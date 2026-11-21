"""Firmware image handling and BootV1 upgrades for INPHIC mice.

The write sequence mirrors the vendor's Windows driver (BootV1Driver and
BootV1Device).  It is intentionally conservative: the existing flash is read
and saved before anything is erased, the written firmware bytes are read back,
and the upgrade is only committed after that verification succeeds.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import re
import select
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from .hid import (
    HID_SYSFS,
    DeviceBusy,
    DeviceError,
    firmware_version,
    model_name,
)

# USB identities used by the vendor driver.  The receiver's configuration
# interface is 32c2:0027 (see hid.py); these are the wired mouse and its
# bootloader.
DIRECT_MOUSE_HID_ID = "HID_ID=0003:000032C2:00000024"
BOOTLOADER_HID_ID = "HID_ID=0003:000032C2:0000001B"
DIRECT_MOUSE_REPORT_ID = bytes.fromhex("85 0e")

# BootV1 reports are unnumbered 32-byte reports.  The vendor buffer is 33
# bytes because Windows includes the zero report ID; on Linux hidraw the zero
# report ID is not part of an unnumbered report.
BOOT_REPORT_SIZE = 32
BOOT_FIFO_DATA = 28
BOOT_READ_DATA = 28
BOOT_BEGIN = bytes.fromhex("0e 13 5a a5")
BOOT_APP = bytes.fromhex("0e 1e 5a a5")
BOOT_GET_INFO = bytes.fromhex("0e 20")
DIRECT_GET_INFO = bytes.fromhex("0e 21")
DIRECT_BOOT = bytes.fromhex("0e 1e 55 aa 00 ff")

# BootV1Device profiles selected from the bootloader FWID family.
MOUSE_BOOT_FAMILY = 0x0071FF00
RECEIVER_BOOT_FAMILY = 0x0166FF00
MOUSE_APP_SIZE = 0xB000
MOUSE_FLASH_SIZE = 0xC800
RECEIVER_PRESERVE = (0xB700, 0xB800)

FIRMWARE_FWID_OFFSET = 0x10
FIRMWARE_VERSION_OFFSET = 0x14
FIRMWARE_HEADER_SIZE = 0x18

# The vendor does not publish a standalone firmware.bin: the images live in
# resources/app/device/<fwid>/data/firmware.bin inside the Windows driver.
# This is the official installer link from https://www.inphic.cn/download.
OFFICIAL_INSTALLER_URL = (
    "https://inphic.oss-cn-shenzhen.aliyuncs.com/download/"
    "INPHIC%20YU_SetupV2.2.11.exe"
)
OFFICIAL_INSTALLER_SHA256 = (
    "e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035"
)
OFFICIAL_DOWNLOAD_REFERER = "https://www.inphic.cn/download"
DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# innoextract 1.9 (still shipped by several distributions) cannot read the
# Inno Setup 6.3.0 installer used by the vendor.  The upstream project provides
# official 1.10-dev Linux binaries; use them only when the system tool is too
# old, and verify the pinned tarball checksum before executing anything.
INNOEXTRACT_SNAPSHOT = "innoextract-1.10-dev-2025-01-07"
INNOEXTRACT_TARBALL_URL = (
    "https://constexpr.org/innoextract/files/snapshots/"
    f"{INNOEXTRACT_SNAPSHOT}/{INNOEXTRACT_SNAPSHOT}-linux.tar.xz"
)
INNOEXTRACT_TARBALL_SHA256 = (
    "e4eb7e15a3b417549fd521fe011ae4e542fd8c10b62c5ddafc3b9ac599a54f50"
)
INNOEXTRACT_ARCHES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "i386": "i686",
    "i686": "i686",
    "armv6l": "armv6j-hardfloat",
    "armv7l": "armv6j-hardfloat",
}


@dataclass(frozen=True)
class FirmwareInfo:
    """FWID and firmware version as reported by a device or image header."""

    fwid: int
    version: int

    @property
    def model(self) -> str:
        return model_name(self.fwid) or f"0x{self.fwid:08x}"

    @property
    def version_text(self) -> str:
        return firmware_version(self.fwid, self.version)


@dataclass(frozen=True)
class FirmwareImage:
    path: Path
    data: bytes
    fwid: int
    version: int

    @property
    def model(self) -> str:
        return model_name(self.fwid) or f"0x{self.fwid:08x}"

    @property
    def version_text(self) -> str:
        return firmware_version(self.fwid, self.version)


@dataclass(frozen=True)
class FirmwareAsset:
    """A firmware image found inside the vendor's Windows driver."""

    fwid: int
    version: int
    data: bytes

    @property
    def model(self) -> str:
        return model_name(self.fwid) or f"0x{self.fwid:08x}"

    @property
    def version_text(self) -> str:
        return firmware_version(self.fwid, self.version)


@dataclass(frozen=True)
class BootProfile:
    """Sizes and quirks used by one BootV1 flash family."""

    app_size: int
    flash_size: int
    fifo_size: int
    send_end: bool
    verify_all: bool
    preserve: tuple[int, int] | None = None


@dataclass(frozen=True)
class PreparedImage:
    """The exact flash image that is written, with its two checksums."""

    data: bytes
    app_size: int
    flash_size: int
    app_checksum: int
    flash_checksum: int
    # BootV1Device only reads back the original firmware bytes, not the 0xff
    # padding or the reserved top of the flash.  Keep that boundary explicit.
    verify_size: int


@dataclass(frozen=True)
class UpgradePlan:
    """What a dry run would write; no device was touched."""

    image: FirmwareImage
    boot_fwid: int
    profile: BootProfile
    prepared: PreparedImage


@dataclass(frozen=True)
class UpgradeResult:
    image: FirmwareImage
    boot: FirmwareInfo
    profile: BootProfile
    prepared: PreparedImage
    backup: Path
    app_ack: bool
    rebooted: bool
    app_firmware: FirmwareInfo | None = None

    @property
    def confirmed(self) -> bool:
        return (
            self.app_firmware is not None
            and self.app_firmware.version == self.image.version
        )


Progress = Callable[[int, str], None]


def _emit(progress: Progress | None, value: int, message: str) -> None:
    if progress is not None:
        progress(max(0, min(100, int(value))), message)


def firmware_checksum(data: bytes) -> int:
    """The vendor's BootV1 checksum: 0xfa21 plus little-endian 16-bit words."""

    if len(data) % 2:
        raise DeviceError("固件数据长度必须为偶数。")
    checksum = 0xFA21
    for index in range(0, len(data), 2):
        checksum += data[index] | (data[index + 1] << 8)
    return checksum & 0xFFFF


def parse_firmware(path: Path | str) -> FirmwareImage:
    """Read and validate an official mouse firmware image."""

    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise DeviceError(f"无法读取固件文件 {path}：{error}") from error
    if len(data) < FIRMWARE_HEADER_SIZE:
        raise DeviceError("固件文件太小，缺少型号与版本头。")
    fwid = int.from_bytes(data[FIRMWARE_FWID_OFFSET:FIRMWARE_FWID_OFFSET + 4], "little")
    version = int.from_bytes(
        data[FIRMWARE_VERSION_OFFSET:FIRMWARE_VERSION_OFFSET + 2], "big"
    )
    if fwid == 0 or version in (0, 0xFFFF):
        raise DeviceError("固件文件头中的型号或版本号无效。")
    if fwid >> 16 != 0x0071:
        raise DeviceError(
            f"固件文件型号为 0x{fwid:08x}，不是受支持的鼠标固件（0x0071xxxx）。"
        )
    if len(data) > MOUSE_APP_SIZE:
        raise DeviceError(
            f"固件文件 {len(data)} 字节，超过设备应用区大小 {MOUSE_APP_SIZE} 字节。"
        )
    if model_name(fwid) is None:
        raise DeviceError(f"固件文件头中的型号 0x{fwid:08x} 不在支持的型号表内。")
    return FirmwareImage(path, data, fwid, version)


def image_matches(image: FirmwareImage, expected_fwid: int | None) -> bool:
    """Accept the exact model or another firmware variant of the same model.

    The vendor ships, for example, M1 firmware with FWID 0x00710208 for both
    the 0x00710207 and 0x00710208 model entries, so an exact FWID comparison
    would reject a legitimate image.
    """

    if expected_fwid is None:
        return True
    if image.fwid == expected_fwid:
        return True
    return model_name(image.fwid) is not None and model_name(image.fwid) == model_name(
        expected_fwid
    )


def boot_profile(boot_fwid: int) -> BootProfile:
    family = boot_fwid & 0xFFFFFF00
    if family == MOUSE_BOOT_FAMILY:
        return BootProfile(
            MOUSE_APP_SIZE, MOUSE_FLASH_SIZE, 0x100, send_end=True, verify_all=True
        )
    if family == RECEIVER_BOOT_FAMILY:
        return BootProfile(
            MOUSE_APP_SIZE,
            MOUSE_FLASH_SIZE,
            0x100,
            send_end=True,
            verify_all=True,
            preserve=RECEIVER_PRESERVE,
        )
    if family in (0x00920000, 0x00960000):
        return BootProfile(0x7000, 0x7000, 0x80, send_end=False, verify_all=True)
    raise DeviceError(f"暂不支持 BootID 0x{boot_fwid:08x} 的 Bootloader。")


def prepare_image(
    image: FirmwareImage,
    profile: BootProfile,
    existing_flash: bytes | None = None,
) -> PreparedImage:
    """Pad the application image the way BootV1Device.UpgradeFirmware does."""

    if len(image.data) > profile.app_size:
        raise DeviceError(
            f"固件文件 {len(image.data)} 字节，超过应用区 {profile.app_size} 字节。"
        )
    if not profile.send_end:
        data = bytes(image.data)
        return PreparedImage(
            data, len(data), len(data), firmware_checksum(data), 0, len(data)
        )

    app = bytes(image.data) + b"\xff" * (profile.app_size - len(image.data))
    app_checksum = firmware_checksum(app)
    flash = app + b"\xff" * (profile.flash_size - profile.app_size)
    if profile.preserve is not None:
        start, end = profile.preserve
        if existing_flash is None or len(existing_flash) < end:
            raise DeviceError("缺少原 Flash 备份，无法保留设备参数区。")
        flash = flash[:start] + existing_flash[start:end] + flash[end:]
    return PreparedImage(
        flash,
        profile.app_size,
        profile.flash_size,
        app_checksum,
        firmware_checksum(flash),
        len(image.data),
    )


def _valid_node(node: Path) -> bool:
    return (
        node.parent == Path("/dev")
        and re.fullmatch(r"hidraw\d+", node.name) is not None
        and node.exists()
    )


def _uevent(node: Path) -> list[str]:
    try:
        return (HID_SYSFS / node.name / "device" / "uevent").read_text().splitlines()
    except OSError:
        return []


def _report_descriptor(node: Path) -> bytes:
    try:
        return (HID_SYSFS / node.name / "device" / "report_descriptor").read_bytes()
    except OSError:
        return b""


def is_bootloader_node(node: Path) -> bool:
    node = Path(node)
    if not _valid_node(node) or BOOTLOADER_HID_ID not in _uevent(node):
        return False
    descriptor = _report_descriptor(node)
    # An unreadable descriptor is tolerated for a node whose USB ID matches;
    # the read-only info command below still validates the bootloader family.
    return (
        not descriptor
        or (
            b"\x06\x0e\xff" in descriptor
            and b"\x09\x01" in descriptor
        )
    )


def is_direct_mouse_node(node: Path) -> bool:
    node = Path(node)
    if not _valid_node(node) or DIRECT_MOUSE_HID_ID not in _uevent(node):
        return False
    descriptor = _report_descriptor(node)
    return (
        b"\x06\x0f\xff" in descriptor
        and b"\x09\x01" in descriptor
        and DIRECT_MOUSE_REPORT_ID in descriptor
    )


def bootloader_nodes() -> list[Path]:
    return [
        node for entry in sorted(HID_SYSFS.glob("hidraw*"))
        if is_bootloader_node(node := Path("/dev") / entry.name)
    ]


def direct_mouse_nodes() -> list[Path]:
    return [
        node for entry in sorted(HID_SYSFS.glob("hidraw*"))
        if is_direct_mouse_node(node := Path("/dev") / entry.name)
    ]


@contextmanager
def open_device(node: Path):
    try:
        handle = open(node, "r+b", buffering=0)
    except PermissionError as error:
        raise DeviceError(
            f"无法访问 {node}，请安装项目的 udev 规则并重新插拔设备。"
        ) from error
    except OSError as error:
        raise DeviceError(f"无法打开 {node}：{error}") from error
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeviceBusy("另一个固件升级或设置操作正在使用该设备。") from error
        yield handle


class _ReportDevice:
    """Small hidraw report transport with the vendor's retry behaviour."""

    def __init__(self, handle, write_size: int = BOOT_REPORT_SIZE,
                 read_size: int = BOOT_REPORT_SIZE):
        self.handle = handle
        self.write_size = write_size
        self.read_size = read_size

    def _drain(self) -> None:
        while select.select([self.handle], [], [], 0)[0]:
            if not os.read(self.handle.fileno(), 64):
                return

    def _write(self, payload: bytes) -> None:
        if len(payload) > self.write_size:
            raise DeviceError("HID 报告超出设备输出报告长度。")
        data = bytes(payload).ljust(self.write_size, b"\0")
        try:
            written = self.handle.write(data)
        except OSError as error:
            raise DeviceError(f"HID 报告写入失败：{error}") from error
        if written != len(data):
            raise DeviceError("HID 报告写入不完整。")

    def _read(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            if not select.select([self.handle], [], [], remaining)[0]:
                break
            response = os.read(self.handle.fileno(), 64)
            if not response:
                raise DeviceError("设备已断开。")
            if response[0] == 0 and len(response) > self.read_size:
                response = response[1:]
            if len(response) >= self.read_size:
                return response[:self.read_size]
        return None

    def transaction(self, payload: bytes, timeout: float = 0.5, attempts: int = 3,
                    label: str = "Bootloader 命令") -> bytes:
        payload = bytes(payload)
        for _ in range(attempts):
            try:
                self._drain()
                self._write(payload)
                response = self._read(timeout)
            except OSError as error:
                raise DeviceError(f"{label}失败：{error}") from error
            if response is not None and response[:2] == payload[:2]:
                return response
        raise DeviceError(f"{label}超时或应答不匹配。请确认鼠标仍处于 Bootloader 模式。")


class Bootloader:
    def __init__(self, node: Path, handle):
        self.node = Path(node)
        self.io = _ReportDevice(handle)

    def info(self) -> FirmwareInfo:
        response = self.io.transaction(BOOT_GET_INFO, 0.5, 3, "读取 Bootloader 信息")
        if len(response) < 8:
            raise DeviceError("Bootloader 信息应答长度不足。")
        return FirmwareInfo(
            int.from_bytes(response[2:6], "little"),
            int.from_bytes(response[6:8], "big"),
        )

    def read_flash(self, addr: int, length: int) -> bytes:
        if not 0 <= addr <= 0xFFFF or not 1 <= length <= BOOT_READ_DATA:
            raise DeviceError("Flash 读取参数超出范围。")
        request = bytes((0x0E, 0x12, addr & 0xFF, (addr >> 8) & 0xFF, length))
        response = self.io.transaction(request, 0.5, 3, "读取 Flash")
        if len(response) < 4 + length:
            raise DeviceError("Flash 读取应答长度不足。")
        return response[4:4 + length]

    def read_flash_all(self, profile: BootProfile,
                       progress: Progress | None = None) -> bytes:
        data = bytearray()
        total = profile.flash_size
        for offset in range(0, total, BOOT_READ_DATA):
            length = min(BOOT_READ_DATA, total - offset)
            data += self.read_flash(offset, length)
            _emit(progress, 5 + 15 * (offset + length) / total,
                  f"读取原 Flash {offset + length}/{total} 字节")
        return bytes(data)

    def write_fifo(self, addr: int, data: bytes) -> None:
        if not 0 <= addr <= 0xFF or not 1 <= len(data) <= BOOT_FIFO_DATA:
            raise DeviceError("FIFO 写入参数超出范围。")
        request = bytes((0x0E, 0x11, addr, len(data))) + data
        response = self.io.transaction(request, 0.5, 3, "写入 FIFO")
        if response[2] != addr:
            raise DeviceError("FIFO 写入应答地址不匹配。")

    def write_flash(self, addr: int, length: int, checksum: int) -> None:
        # BootV1 passes the length as uint8; 256 is encoded as 0.
        if not 0 <= addr <= 0xFFFF or not 1 <= length <= 0x100:
            raise DeviceError("Flash 写入参数超出范围。")
        request = bytes((
            0x0E, 0x10,
            addr & 0xFF, (addr >> 8) & 0xFF,
            length & 0xFF, 0xA5,
            checksum & 0xFF, (checksum >> 8) & 0xFF,
        ))
        response = self.io.transaction(request, 1.0, 3, "写入 Flash")
        if len(response) < 6 or response[2:5] != request[2:5] or response[5] != 1:
            raise DeviceError("Bootloader 拒绝写入 Flash。")

    def write_chunk(self, addr: int, data: bytes, profile: BootProfile) -> None:
        if len(data) > profile.fifo_size:
            raise DeviceError("FIFO 块超过设备 FIFO 大小。")
        for index in range(0, len(data), BOOT_FIFO_DATA):
            self.write_fifo(index, data[index:index + BOOT_FIFO_DATA])
        self.write_flash(addr, len(data), firmware_checksum(data))

    def begin_upgrade(self) -> None:
        response = self.io.transaction(BOOT_BEGIN, 1.0, 3, "开始升级")
        if len(response) < 6 or response[5] != 1:
            raise DeviceError("Bootloader 拒绝开始升级。")

    def end_upgrade(self, app_size: int, flash_size: int,
                    app_checksum: int, flash_checksum: int) -> None:
        request = bytes((0x0E, 0x14)) + b"".join(
            value.to_bytes(2, "little")
            for value in (app_size, flash_size, app_checksum, flash_checksum)
        )
        response = self.io.transaction(request, 3.0, 3, "提交升级参数")
        if len(response) < 10 or response[:10] != request[:10]:
            raise DeviceError("Bootloader 的升级参数应答不匹配。")

    def app(self) -> bool:
        try:
            response = self.io.transaction(BOOT_APP, 1.0, 1, "启动新固件")
        except DeviceError:
            # The device commonly disconnects before acknowledging APP.
            return False
        return len(response) >= 3 and response[2] == 1

    def write_image(self, prepared: PreparedImage, profile: BootProfile,
                    progress: Progress | None = None) -> None:
        data = prepared.data
        total = len(data)
        for offset in range(0, total, profile.fifo_size):
            chunk = data[offset:offset + profile.fifo_size]
            self.write_chunk(offset, chunk, profile)
            _emit(progress, 22 + 46 * (offset + len(chunk)) / total,
                  f"写入 Flash {offset + len(chunk)}/{total} 字节")
        verify_total = prepared.verify_size if profile.verify_all else 0
        for offset in range(0, verify_total, BOOT_READ_DATA):
            length = min(BOOT_READ_DATA, verify_total - offset)
            if self.read_flash(offset, length) != data[offset:offset + length]:
                raise DeviceError(
                    f"回读校验失败（地址 0x{offset:04x}）。设备仍停留在 Bootloader，"
                    "可重新运行升级；原 Flash 备份已保存。"
                )
            _emit(progress, 68 + 26 * (offset + length) / verify_total,
                  f"回读校验 {offset + length}/{verify_total} 字节")


def direct_mouse_info(node: Path) -> FirmwareInfo:
    """Read the wired mouse identity without changing any state."""

    with open_device(node) as handle:
        io = _ReportDevice(handle)
        response = io.transaction(DIRECT_GET_INFO, 0.5, 3, "读取鼠标型号")
        if len(response) < 20:
            raise DeviceError("鼠标型号应答长度不足。")
        return FirmwareInfo(
            int.from_bytes(response[14:18], "big"),
            int.from_bytes(response[18:20], "big"),
        )


def enter_bootloader(node: Path, progress: Progress | None = None) -> bool:
    """Ask the wired mouse to reboot into BootV1."""

    with open_device(node) as handle:
        io = _ReportDevice(handle)
        try:
            response = io.transaction(DIRECT_BOOT, 0.5, 3, "进入 Bootloader")
        except DeviceError:
            # Re-enumeration can remove the device before it answers.
            _emit(progress, 3, "已发送进入 Bootloader 的命令，等待 USB 重新枚举…")
            return False
        if len(response) < 3 or response[2] != 1:
            raise DeviceError("鼠标拒绝了进入 Bootloader 的命令。")
        return True


def wait_for_bootloader(timeout: float = 15.0,
                        progress: Progress | None = None) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        nodes = bootloader_nodes()
        if len(nodes) == 1:
            return nodes[0]
        if len(nodes) > 1:
            raise DeviceError("找到多个 Bootloader 设备，请用 --boot-device 指定节点。")
        _emit(progress, 5, "等待 Bootloader 设备出现…")
        time.sleep(0.15)
    raise DeviceError(
        "等待 Bootloader 超时。请确认鼠标已通过 USB 数据线连接，而不是仅接在 2.4G 接收器上。"
    )


def wait_for_application(boot_node: Path, timeout: float = 10.0,
                         progress: Progress | None = None) -> FirmwareInfo | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_bootloader_node(boot_node) and not bootloader_nodes():
            try:
                nodes = direct_mouse_nodes()
                if len(nodes) == 1:
                    return direct_mouse_info(nodes[0])
            except DeviceError:
                pass
        _emit(progress, 98, "等待鼠标重新枚举…")
        time.sleep(0.25)
    return None


def default_backup_path(image: FirmwareImage) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / f"{image.path.stem}.flash-{stamp}.bin"


def _backup_path(image: FirmwareImage, requested: Path | None) -> Path:
    path = Path(requested) if requested is not None else default_backup_path(image)
    if path.exists():
        raise DeviceError(f"备份文件已存在：{path}")
    if not path.parent.is_dir():
        raise DeviceError(f"备份目录不存在：{path.parent}")
    if not os.access(path.parent, os.W_OK):
        raise DeviceError(f"备份目录不可写：{path.parent}")
    return path


def _check_image_for_boot(image: FirmwareImage, boot_fwid: int) -> BootProfile:
    profile = boot_profile(boot_fwid)
    family = boot_fwid & 0xFFFFFF00
    if image.fwid >> 16 != 0x0071 or family != MOUSE_BOOT_FAMILY:
        raise DeviceError(
            f"固件型号 0x{image.fwid:08x} 与 Bootloader 0x{boot_fwid:08x} 不匹配。"
        )
    return profile


def upgrade_mouse(
    path: Path | str,
    *,
    expected_fwid: int | None = None,
    device: Path | None = None,
    backup_path: Path | None = None,
    progress: Progress | None = None,
    boot_timeout: float = 15.0,
    dry_run: bool = False,
    force: bool = False,
) -> UpgradePlan | UpgradeResult:
    """Upgrade a wired mouse from an official raw firmware image.

    The mouse must be connected by USB.  A device that is already in the
    BootV1 bootloader is upgraded directly, which also makes re-running a
    failed attempt possible.  No write happens before the old flash has been
    read and saved.
    """

    image = parse_firmware(path)
    if expected_fwid is not None and not image_matches(image, expected_fwid):
        current = model_name(expected_fwid) or f"0x{expected_fwid:08x}"
        raise DeviceError(
            f"固件文件是 {image.model}（FWID 0x{image.fwid:08x}），"
            f"当前鼠标是 {current}（FWID 0x{expected_fwid:08x}），型号不匹配。"
        )
    if dry_run:
        profile = boot_profile(MOUSE_BOOT_FAMILY)
        existing = b"\xff" * profile.flash_size if profile.preserve else None
        return UpgradePlan(
            image, MOUSE_BOOT_FAMILY, profile,
            prepare_image(image, profile, existing),
        )

    # Reserve the backup path before rebooting the mouse: a bad path must not
    # leave a device stranded in the bootloader.
    backup = _backup_path(image, backup_path)

    direct = False
    if device is not None:
        device = Path(device)
        if is_bootloader_node(device):
            target = device
        elif is_direct_mouse_node(device):
            target = device
            direct = True
        else:
            raise DeviceError(
                f"{device} 不是 USB 直连鼠标或 Bootloader。固件升级不能使用接收器节点；"
                "请把鼠标切到 USB 有线模式。"
            )
    else:
        boots = bootloader_nodes()
        if boots:
            if len(boots) != 1:
                raise DeviceError("找到多个 Bootloader 设备，请用 --boot-device 指定节点。")
            target = boots[0]
        else:
            mouses = direct_mouse_nodes()
            if not mouses:
                raise DeviceError(
                    "未找到 USB 直连鼠标。固件升级需要把鼠标切换到 USB 有线模式，"
                    "并用数据线连接电脑；接收器上的 2.4G 连接不能升级。"
                )
            if len(mouses) != 1:
                raise DeviceError("找到多个 USB 直连鼠标，请用 --boot-device 指定节点。")
            target = mouses[0]
            direct = True

    _emit(progress, 1, f"读取固件 {image.path.name}")
    if direct:
        info = direct_mouse_info(target)
        if expected_fwid is None:
            expected_fwid = info.fwid
        elif (
            expected_fwid != info.fwid
            and model_name(expected_fwid) != model_name(info.fwid)
        ):
            raise DeviceError("接收器记录的鼠标型号与 USB 直连鼠标不一致。")
        if not image_matches(image, info.fwid):
            raise DeviceError(
                f"固件文件是 {image.model}（FWID 0x{image.fwid:08x}），"
                f"USB 直连鼠标是 {info.model}（FWID 0x{info.fwid:08x}），型号不匹配。"
            )
        _emit(progress, 2, f"当前鼠标 {info.model}，固件 {info.version_text}")
        if not force and info.version not in (0, 0xFFFF):
            if info.version == image.version:
                raise DeviceError(
                    f"当前鼠标固件已是 {image.version_text}，无需升级。"
                    "如需强制重刷，请启用强制选项（CLI: --force）。"
                )
            if image.version < info.version:
                raise DeviceError(
                    f"目标固件 {image.version_text} 低于当前 {info.version_text}；"
                    "如需降级，请启用强制选项（CLI: --force）。"
                )
        enter_bootloader(target, progress)
        boot_node = wait_for_bootloader(boot_timeout, progress)
    else:
        boot_node = target
        if expected_fwid is None:
            raise DeviceError(
                "无法确认当前鼠标型号：设备已在 Bootloader，但接收器没有可用的型号记录。"
                f"请同时连接接收器，或用 --fwid 0x{image.fwid:08x} 指定型号后重试。"
            )

    with open_device(boot_node) as handle:
        bootdev = Bootloader(boot_node, handle)
        boot_info = bootdev.info()
        profile = _check_image_for_boot(image, boot_info.fwid)
        _emit(progress, 4, f"Bootloader 0x{boot_info.fwid:08x}，固件 {boot_info.version_text}")
        existing = bootdev.read_flash_all(profile, progress)
        try:
            with backup.open("xb") as file:
                file.write(existing)
        except FileExistsError as error:
            raise DeviceError(f"备份文件已存在：{backup}") from error
        except OSError as error:
            raise DeviceError(f"无法保存原 Flash 备份到 {backup}：{error}") from error
        if backup.stat().st_size != len(existing):
            raise DeviceError(f"原 Flash 备份写入不完整：{backup}")
        _emit(progress, 21, f"原 Flash 已备份到 {backup}")
        prepared = prepare_image(image, profile, existing)
        bootdev.begin_upgrade()
        _emit(progress, 22, "开始写入固件…")
        bootdev.write_image(prepared, profile, progress)
        if profile.send_end:
            bootdev.end_upgrade(
                prepared.app_size,
                prepared.flash_size,
                prepared.app_checksum,
                prepared.flash_checksum,
            )
        _emit(progress, 96, "写入与校验完成，启动新固件…")
        app_ack = bootdev.app()
        app_info = wait_for_application(boot_node, 10.0, progress)
        rebooted = not is_bootloader_node(boot_node)
        _emit(progress, 100, "固件升级完成")
        return UpgradeResult(
            image,
            boot_info,
            profile,
            prepared,
            backup,
            app_ack,
            rebooted,
            app_info,
        )


# ---------------------------------------------------------------------------
# Firmware acquisition: download the official Windows installer and extract
# the embedded raw firmware images.  The vendor's site does not publish a
# standalone firmware.bin, so this is the only online path.

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_root(cache_dir: Path | None = None) -> Path:
    if cache_dir is not None:
        root = Path(cache_dir)
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        root = (Path(base) if base else Path.home() / ".cache") / "open-inphic"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _scaled(progress: Progress | None, start: float, end: float) -> Progress | None:
    if progress is None:
        return None

    def report(value: int, message: str) -> None:
        mapped = int(start + (end - start) * max(0, min(100, value)) / 100)
        progress(mapped, message)

    return report


def _is_pe(path: Path) -> bool:
    try:
        with Path(path).open("rb") as file:
            return file.read(2) == b"MZ"
    except OSError:
        return False


def _download_file(
    url: str,
    dest: Path,
    *,
    headers: dict[str, str] | None = None,
    progress: Progress | None = None,
    timeout: float = 60.0,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        _emit(progress, 100, f"使用已缓存的 {dest.name}")
        return dest
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": DOWNLOAD_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            with part.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    file.write(chunk)
                    written += len(chunk)
                    if total:
                        _emit(
                            progress,
                            100 * written / total,
                            f"下载 {written // (1024 * 1024)}/"
                            f"{max(1, total // (1024 * 1024))} MB",
                        )
                    else:
                        _emit(progress, 0, f"已下载 {written // (1024 * 1024)} MB")
        if part.stat().st_size == 0:
            raise DeviceError("下载的文件为空。")
        os.replace(part, dest)
    except urllib.error.HTTPError as error:
        part.unlink(missing_ok=True)
        raise DeviceError(
            f"下载失败：HTTP {error.code}（{url}）。请确认网络可用或手动指定 --installer。"
        ) from error
    except urllib.error.URLError as error:
        part.unlink(missing_ok=True)
        raise DeviceError(f"下载失败：{error.reason}（{url}）") from error
    except OSError as error:
        part.unlink(missing_ok=True)
        raise DeviceError(f"下载文件失败：{error}") from error
    _emit(progress, 100, f"已下载 {dest.name}")
    return dest


def download_installer(
    url: str = OFFICIAL_INSTALLER_URL,
    *,
    cache_dir: Path | None = None,
    progress: Progress | None = None,
) -> Path:
    """Download the official Windows driver installer (about 91 MiB)."""

    root = _cache_root(cache_dir)
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
    name = name or "INPHIC YU_Setup.exe"
    dest = root / "installers" / name
    if dest.is_file() and not _is_pe(dest):
        dest.unlink()
    headers = {"User-Agent": DOWNLOAD_USER_AGENT}
    if url == OFFICIAL_INSTALLER_URL:
        headers["Referer"] = OFFICIAL_DOWNLOAD_REFERER
    path = _download_file(url, dest, headers=headers, progress=progress)
    if not _is_pe(path):
        raise DeviceError(f"下载的文件不是 Windows 安装包：{path}")
    if url == OFFICIAL_INSTALLER_URL:
        digest = _sha256_file(path)
        if digest == OFFICIAL_INSTALLER_SHA256:
            _emit(progress, 100, "官方安装包 SHA-256 校验通过")
        else:
            _emit(
                progress,
                100,
                f"安装包 SHA-256 与已知版本不同（{digest[:16]}…），可能已更新；继续提取",
            )
    return path


def _innoextract_supports_modern(tool: Path | str) -> bool:
    try:
        result = subprocess.run(
            [str(tool), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    text = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        return False
    return bool(
        re.search(r"innoextract 1\.(1[0-9]|[2-9][0-9])", text)
        or "6.3" in text
        or "6.4" in text
    )


def ensure_innoextract(
    *,
    cache_dir: Path | None = None,
    progress: Progress | None = None,
    allow_download: bool = True,
) -> Path:
    """Return an innoextract that can read Inno Setup 6.3.x installers."""

    override = os.environ.get("OPEN_INPHIC_INNOEXTRACT")
    if override:
        tool = Path(override)
        if not tool.is_file() or not os.access(tool, os.X_OK):
            raise DeviceError(f"OPEN_INPHIC_INNOEXTRACT 不可执行：{tool}")
        return tool
    system = shutil.which("innoextract")
    if system and _innoextract_supports_modern(system):
        _emit(progress, 100, f"使用系统 innoextract：{system}")
        return Path(system)

    root = _cache_root(cache_dir)
    tool = root / "tools" / INNOEXTRACT_SNAPSHOT / "innoextract"
    if (
        tool.is_file()
        and os.access(tool, os.X_OK)
        and _innoextract_supports_modern(tool)
    ):
        _emit(progress, 100, "使用已缓存的 innoextract 1.10")
        return tool
    if not allow_download:
        raise DeviceError(
            "系统 innoextract 版本过旧，无法解包 Inno Setup 6.3 安装包。"
            "请安装 innoextract 1.10 或更新版本，或允许工具下载官方辅助二进制。"
        )
    arch = INNOEXTRACT_ARCHES.get(platform.machine().lower())
    if arch is None:
        raise DeviceError(
            f"当前架构 {platform.machine()} 没有可用的 innoextract 辅助二进制；"
            "请安装 innoextract 1.10 或更新版本。"
        )
    tarball = root / "tools" / f"{INNOEXTRACT_SNAPSHOT}.tar.xz"
    if tarball.is_file() and _sha256_file(tarball) != INNOEXTRACT_TARBALL_SHA256:
        tarball.unlink()
    if not tarball.is_file():
        _emit(progress, 0, "下载官方 innoextract 辅助工具…")
        _download_file(
            INNOEXTRACT_TARBALL_URL,
            tarball,
            progress=_scaled(progress, 0, 60),
        )
    if _sha256_file(tarball) != INNOEXTRACT_TARBALL_SHA256:
        tarball.unlink(missing_ok=True)
        raise DeviceError("innoextract 辅助工具 SHA-256 校验失败，已删除下载文件。")
    _emit(progress, 65, "解压 innoextract 辅助工具…")
    member_suffix = f"/bin/{arch}/innoextract"
    with tarfile.open(tarball, "r:xz") as archive:
        member = next(
            (m for m in archive.getmembers() if m.name.endswith(member_suffix)),
            None,
        )
        if member is None or not member.isfile():
            raise DeviceError(f"innoextract 压缩包中没有 {arch} 二进制。")
        source = archive.extractfile(member)
        if source is None:
            raise DeviceError("无法读取 innoextract 辅助二进制。")
        tool.parent.mkdir(parents=True, exist_ok=True)
        temp = tool.with_name(tool.name + ".part")
        with temp.open("wb") as file:
            shutil.copyfileobj(source, file)
    temp.chmod(0o755)
    os.replace(temp, tool)
    if not _innoextract_supports_modern(tool):
        raise DeviceError(
            "下载的 innoextract 辅助工具无法运行；请安装系统版本 1.10 或更新版本。"
        )
    _emit(progress, 100, "innoextract 辅助工具已就绪（SHA-256 校验通过）")
    return tool


def extract_app_exe(
    installer: Path,
    dest: Path,
    *,
    cache_dir: Path | None = None,
    progress: Progress | None = None,
    allow_helper_download: bool = True,
    innoextract: Path | None = None,
) -> Path:
    """Unpack the Windows installer and return its main Go executable."""

    installer = Path(installer)
    if not installer.is_file():
        raise DeviceError(f"找不到厂商安装包：{installer}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    tool = (
        Path(innoextract)
        if innoextract is not None
        else ensure_innoextract(
            cache_dir=cache_dir,
            progress=_scaled(progress, 0, 20),
            allow_download=allow_helper_download,
        )
    )
    _emit(progress, 25, "正在解包厂商安装包…")
    try:
        result = subprocess.run(
            [str(tool), "-d", str(dest), str(installer)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise DeviceError(f"运行 innoextract 失败：{error}") from error
    _emit(progress, 50, "查找 INPHIC 英云.exe…")
    app = dest / "app" / "INPHIC 英云.exe"
    if not app.is_file():
        candidates = sorted(
            (path for path in dest.rglob("*.exe") if path.is_file()),
            key=lambda path: path.stat().st_size,
            reverse=True,
        )
        app = candidates[0] if candidates else None
    if app is None or not app.is_file() or not _is_pe(app):
        details = (result.stdout + result.stderr).strip().splitlines()[-8:]
        raise DeviceError(
            "innoextract 没有解出主程序。请安装 innoextract 1.10 或更新版本。\n"
            + "\n".join(details)
        )
    _emit(progress, 70, f"已解出 {app.name}")
    return app


def _gunzip_at(data: bytes, offset: int, limit: int) -> bytes | None:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    position = offset
    while position < len(data) and len(output) <= limit:
        chunk = data[position:position + 65536]
        try:
            output += decompressor.decompress(chunk, limit - len(output))
        except zlib.error:
            return None
        position += len(chunk)
        if decompressor.eof:
            return bytes(output)
        if not chunk:
            break
    return None


def scan_firmware_images(
    app_exe: Path,
    progress: Progress | None = None,
) -> list[FirmwareAsset]:
    """Scan the Go executable for gzip-compressed vendor firmware assets."""

    app_exe = Path(app_exe)
    try:
        data = app_exe.read_bytes()
    except OSError as error:
        raise DeviceError(f"无法读取 {app_exe}：{error}") from error
    _emit(progress, 0, f"扫描 {app_exe.name} 中的固件…")
    magic = b"\x1f\x8b\x08"
    position = 0
    candidates = 0
    assets: dict[tuple[int, int, str], FirmwareAsset] = {}
    length = len(data)
    while True:
        offset = data.find(magic, position)
        if offset < 0:
            break
        position = offset + 1
        candidates += 1
        if candidates % 32 == 0:
            _emit(
                progress,
                100 * offset / max(1, length),
                f"扫描 {offset // (1024 * 1024)}/{length // (1024 * 1024)} MB",
            )
        raw = _gunzip_at(data, offset, MOUSE_APP_SIZE + 1)
        if raw is None or len(raw) < 0x1000 or len(raw) > MOUSE_APP_SIZE:
            continue
        if raw[0x18:0x1c] != b"BYKC":
            continue
        fwid = int.from_bytes(raw[0x10:0x14], "little")
        version = int.from_bytes(raw[0x14:0x16], "big")
        stack_pointer = int.from_bytes(raw[0:4], "little")
        reset_vector = int.from_bytes(raw[4:8], "little")
        if (fwid >> 16) not in (0x0071, 0x0166):
            continue
        if version in (0, 0xFFFF):
            continue
        if not 0x20000000 <= stack_pointer <= 0x20080000:
            continue
        if not 0 < reset_vector < len(raw):
            continue
        digest = hashlib.sha256(raw).hexdigest()
        assets[(fwid, version, digest)] = FirmwareAsset(fwid, version, raw)
    _emit(progress, 100, f"找到 {len(assets)} 个固件镜像")
    return sorted(assets.values(), key=lambda asset: (asset.fwid, asset.version))


def select_firmware(
    assets: list[FirmwareAsset],
    target_fwid: int | None = None,
) -> FirmwareAsset:
    if not assets:
        raise DeviceError("安装包中没有找到固件镜像。")
    if target_fwid is None:
        if len(assets) == 1:
            return assets[0]
        raise DeviceError(
            "无法确定当前鼠标型号：请连接鼠标，或用 --fwid 指定 FWID。"
        )
    exact = [asset for asset in assets if asset.fwid == target_fwid]
    if exact:
        return max(exact, key=lambda asset: asset.version)
    target_model = model_name(target_fwid)
    if target_model is not None:
        same_model = [
            asset for asset in assets if model_name(asset.fwid) == target_model
        ]
        if same_model:
            return max(same_model, key=lambda asset: asset.version)
    available = sorted(
        {f"0x{asset.fwid:08x} {asset.model} {asset.version_text}" for asset in assets}
    )
    raise DeviceError(
        f"安装包中没有 FWID 0x{target_fwid:08x} 的固件。\n可用型号：\n"
        + "\n".join(available[:20])
    )


def fetch_firmware(
    target_fwid: int | None = None,
    *,
    installer: Path | None = None,
    app_exe: Path | None = None,
    output: Path | None = None,
    url: str | None = None,
    cache_dir: Path | None = None,
    progress: Progress | None = None,
    allow_helper_download: bool = True,
) -> Path:
    """Get a model's raw firmware .bin from the official installer or a local copy."""

    root = _cache_root(cache_dir)
    if target_fwid is None:
        nodes = direct_mouse_nodes()
        if len(nodes) == 1:
            target_fwid = direct_mouse_info(nodes[0]).fwid
    _emit(progress, 0, "准备获取鼠标固件…")

    if app_exe is not None:
        app_path = Path(app_exe)
        if not app_path.is_file():
            raise DeviceError(f"找不到已解包的主程序：{app_path}")
        assets = scan_firmware_images(app_path, _scaled(progress, 0, 90))
    elif installer is not None:
        installer_path = Path(installer)
        if not installer_path.is_file():
            raise DeviceError(f"找不到厂商安装包：{installer_path}")
        with tempfile.TemporaryDirectory(
            prefix="open-inphic-extract-", dir=root
        ) as directory:
            app_path = extract_app_exe(
                installer_path,
                Path(directory),
                cache_dir=root,
                progress=_scaled(progress, 0, 30),
                allow_helper_download=allow_helper_download,
            )
            assets = scan_firmware_images(app_path, _scaled(progress, 30, 90))
    else:
        installer_path = download_installer(
            url or OFFICIAL_INSTALLER_URL,
            cache_dir=root,
            progress=_scaled(progress, 0, 55),
        )
        with tempfile.TemporaryDirectory(
            prefix="open-inphic-extract-", dir=root
        ) as directory:
            app_path = extract_app_exe(
                installer_path,
                Path(directory),
                cache_dir=root,
                progress=_scaled(progress, 55, 70),
                allow_helper_download=allow_helper_download,
            )
            assets = scan_firmware_images(app_path, _scaled(progress, 70, 90))

    asset = select_firmware(assets, target_fwid)
    output_path = (
        Path(output)
        if output is not None
        else root / "firmware" / f"{asset.fwid:08x}-{asset.version:04x}.bin"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file() and output_path.read_bytes() == asset.data:
        _emit(progress, 100, f"使用已提取的固件：{output_path}")
        return output_path
    part = output_path.with_name(output_path.name + ".part")
    try:
        part.write_bytes(asset.data)
        os.replace(part, output_path)
    except OSError as error:
        part.unlink(missing_ok=True)
        raise DeviceError(f"无法写入固件文件 {output_path}：{error}") from error
    parsed = parse_firmware(output_path)
    if parsed.fwid != asset.fwid or parsed.version != asset.version:
        raise DeviceError("提取出的固件文件头与扫描结果不一致。")
    _emit(progress, 100, f"固件已保存：{output_path}")
    return output_path

