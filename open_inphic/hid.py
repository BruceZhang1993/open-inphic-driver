"""Linux hidraw controls for INPHIC mice."""

import argparse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

HID_SYSFS = Path("/sys/class/hidraw")
BLUETOOTH_SYSFS = Path("/sys/class/bluetooth")
HID_ID = "HID_ID=0003:000032C2:00000027"
VENDOR_COLLECTION = bytes.fromhex(
    "06 0f ff 09 01 a1 01 85 0e 09 02 15 00 26 ff 00 "
    "75 08 95 1f 81 02 09 02 15 00 26 ff 00 75 08 95 0f 91 02 c0"
)
CONNECTION_LABELS = {"2.4g": "2.4G", "usb": "USB 有线", "bluetooth": "蓝牙"}
BLUETOOTH_READ_ONLY = "蓝牙鼠标已连接。请切换到 2.4G 或 USB 有线模式修改设置。"
# Microsoft mouse base identifier; the low 24 bits carry the vendor's model FWID.
MOUSE_BASE_FWID = 0x00400000
EMPTY_DPI = 0xFF

# models accepted by the Windows driver, keyed by the FWID reported by 0x61/0x2d
MODELS: dict[int, str] = {
    0x00710102: "A1",
    0x00710107: "A9",
    0x00710252: "A9 二代",
    0x00710469: "A11 V2",
    0x00710170: "DR01",
    0x00710116: "DR5",
    0x00710171: "DR6",
    0x00710354: "DR6 PRO",
    0x00710185: "DR8",
    0x00710223: "F8",
    0x00710101: "F9",
    0x00710343: "F9歼20主题版",
    0x00710305: "IN6 V3",
    0x00710407: "IN6 V4",
    0x00710289: "IN6 PRO",
    0x00710288: "IN7",
    0x00710372: "IN7 PRO",
    0x00710004: "IN10",
    0x00710100: "INX SE",
    0x00710207: "M1",
    0x00710208: "M1",
    0x00710183: "M1 二代",
    0x00710184: "M1 PRO",
    0x00710433: "M1 PRO V3",
    0x00710413: "M1 PRO V4",
    0x00710414: "M1 PRO V4",
    0x00710221: "M2",
    0x00710182: "M8",
    0x00710222: "M9",
    0x00710189: "X2",
    0x00710106: "X9",
    0x00710390: "YUN陨",
    0x00710036: "元创.疾蜂",
    0x00710068: "元创.星遂",
    0x00710139: "元创.赛博",
    0x00710150: "元创.可鲁",
    0x00710462: "元创.可鲁 V3",
}
# receiver model FWIDs seen from 0x21
RECEIVERS: dict[int, str] = {
    0x01660003: "BYKC 6239 Dongle",
    0x01660004: "BYKC 6219 Dongle",
}

RATES = (125, 250, 500, 1000)
RATE_COLORS = ("绿色", "蓝色", "黄色", "红色")

LED_EFFECTS: dict[int, str] = {
    0: "空闲模式",
    1: "关闭灯光",
    2: "闪烁模式",
    3: "呼吸模式",
    4: "霓虹模式",
    5: "常亮模式",
    6: "七彩流光",
    7: "七彩跑马",
    8: "繁星点点",
}
# effects listed by the vendor's X9 profile table
X9_EFFECTS = (1, 2, 3, 4, 5)
LED_DIRECTIONS = ("反向", "正向")
# Windows maps b[10] brightness and speed onto a four-step slider
LED_STEPS = 4

# 0x60 b[12]/b[13] low three bits for the primary sleep, bits 4-6 for the deep sleep
WIRELESS_SLEEP_SECONDS = (10, 30, 60, 120, 180, 300, 600)
WIRELESS_DEEP_SLEEP_MINUTES = (5, 10, 15, 20, 30, 40, 60)
LIFT_LABELS = ("1 MM", "2 MM", "3 MM", "4 MM")

# 0x60 b[14]/b[15] flags
ADVANCE_FLAGS = (
    ("ultra_low_delay", 0x10, "超低延迟"),
    ("ultra_low_power", 0x20, "超低功耗"),
    ("ripple_control", 0x40, "波纹控制"),
    ("move_wake_up", 0x80, "移动唤醒"),
)


class DeviceError(Exception):
    """A user-visible device, protocol, or verification failure."""


class DeviceBusy(DeviceError):
    """Another local process owns the receiver for a transaction."""


@dataclass(frozen=True)
class PowerStatus:
    online: bool
    battery: int
    charging: bool


@dataclass(frozen=True)
class Led:
    mode: int
    direction: int
    brightness: int
    speed: int
    color: int

    @property
    def effect(self) -> str:
        return LED_EFFECTS.get(self.mode, f"模式 {self.mode}")


@dataclass(frozen=True)
class AdvanceSetting:
    wireless_sleep: int
    wireless_deep: int
    bluetooth_sleep: int
    bluetooth_deep: int
    lift: int
    ultra_low_delay: bool = False
    ultra_low_power: bool = False
    ripple_control: bool = False
    move_wake_up: bool = False
    line_correct: bool = False


@dataclass(frozen=True)
class Settings:
    mode: int
    levels: tuple[int, ...]
    usb_rates: tuple[int, ...]
    wireless_rates: tuple[int, ...]
    led: Led = field(default_factory=lambda: Led(1, 0, 0, 0, 0))
    advance: AdvanceSetting = field(
        default_factory=lambda: AdvanceSetting(2, 1, 2, 1, 1)
    )


@dataclass(frozen=True)
class Pairing:
    device_id: int
    firmware_id: int
    version: int


@dataclass(frozen=True)
class DeviceState:
    receiver_firmware: int
    online: bool
    battery: int | None
    charging: bool | None
    firmware: int | None = None
    settings: Settings | None = None
    dpi_codes: bytes = b""
    dpi_table: dict[int, int] | None = None
    pairing: Pairing | None = None
    receiver_fwid: int = 0
    mouse_fwid: int = 0
    connection: str = "2.4g"
    node: Path | None = None
    device_name: str | None = None

    @property
    def slots(self) -> dict[int, int]:
        if self.dpi_table is None:
            return {}
        return {
            slot: self.dpi_table[code]
            for slot, code in enumerate(self.dpi_codes, 1)
            if code in self.dpi_table and code != EMPTY_DPI
        }

    @property
    def model(self) -> str | None:
        """Model name known once the mouse answered, otherwise from the receiver."""
        if self.mouse_fwid:
            return model_name(self.mouse_fwid)
        if self.pairing is not None and self.pairing.firmware_id:
            return model_name(self.pairing.firmware_id)
        return self.device_name


def model_name(fwid: int) -> str | None:
    return MODELS.get(fwid) or MODELS.get(fwid & ~MOUSE_BASE_FWID)


def firmware_version(fwid: int, version: int) -> str:
    """Vendor display form: V+FWID high/low+FirmwareVersion, all hex.

    The Windows settings drawer formats a FWID and firmware version as e.g.
    V0071.0106.EB02 for the X9 PRO.  Keeping the same form makes the values
    comparable with the vendor application.
    """
    return f"V{fwid >> 16:04X}.{fwid & 0xFFFF:04X}.{version:04X}"


def is_receiver_node(node: Path) -> bool:
    if node.parent != Path("/dev") or not re.fullmatch(r"hidraw\d+", node.name):
        return False
    device = HID_SYSFS / node.name / "device"
    try:
        return (
            HID_ID in (device / "uevent").read_text().splitlines()
            and (device.resolve(strict=True).parent / "bInterfaceNumber").read_text().strip() == "02"
            and (device / "report_descriptor").read_bytes().endswith(VENDOR_COLLECTION)
        )
    except FileNotFoundError:
        return False


def receiver_nodes() -> list[Path]:
    return [
        node for entry in sorted(HID_SYSFS.glob("hidraw*"))
        if is_receiver_node(node := Path("/dev") / entry.name)
    ]


def bluetooth_mouse_name(node: Path, *, explicit: bool = False) -> str | None:
    if node.parent != Path("/dev") or not re.fullmatch(r"hidraw\d+", node.name):
        return None
    device = HID_SYSFS / node.name / "device"
    try:
        properties = dict(line.split("=", 1) for line in (device / "uevent").read_text().splitlines()
                          if "=" in line)
        hid_id = properties.get("HID_ID", "")
        name = properties.get("HID_NAME", "").strip()
        if not hid_id.startswith("0005:"):
            return None
        recognized = hid_id.startswith("0005:000032C2:") or name.casefold() == "inphicmouse" or re.search(
            r"(?:^|[^a-z0-9])inphic(?:$|[^a-z0-9])|英菲克|^x9[ -]*pro\b", name, re.IGNORECASE
        )
        if not (explicit or recognized):
            return None
        descriptor = (device / "report_descriptor").read_bytes()
    except OSError:
        return None
    if bytes.fromhex("05 01 09 02 a1 01") in descriptor or any(device.glob("input/input*/mouse*")):
        return name or "蓝牙鼠标"
    return None


@contextmanager
def open_mouse(node: Path | None = None, connection: str = "auto"):
    from .firmware import direct_mouse_nodes, is_direct_mouse_node, open_device

    bluetooth_name = None
    if node is None:
        usb_nodes = direct_mouse_nodes() if connection in ("auto", "usb") else []
        bluetooth_nodes = {}
        if not usb_nodes and connection in ("auto", "bluetooth"):
            for entry in sorted(HID_SYSFS.glob("hidraw*")):
                candidate = Path("/dev") / entry.name
                if name := bluetooth_mouse_name(candidate):
                    bluetooth_nodes[candidate] = name
        if usb_nodes:
            nodes, connection = usb_nodes, "usb"
        elif bluetooth_nodes:
            nodes, connection = list(bluetooth_nodes), "bluetooth"
        elif connection in ("auto", "2.4g"):
            nodes, connection = receiver_nodes(), "2.4g"
        else:
            nodes = []
        if not nodes:
            label = CONNECTION_LABELS[connection]
            raise DeviceError(f"未找到 {label} 设备。")
        if len(nodes) != 1:
            raise DeviceError(f"找到多个 {CONNECTION_LABELS[connection]} 设备，请用 --device 指定节点。")
        node = nodes[0]
        bluetooth_name = bluetooth_nodes.get(node)
    elif connection == "auto":
        if is_receiver_node(node):
            connection = "2.4g"
        elif is_direct_mouse_node(node):
            connection = "usb"
        else:
            connection = "bluetooth"

    if connection == "bluetooth":
        name = bluetooth_name or bluetooth_mouse_name(node, explicit=True)
        if not name:
            raise DeviceError(f"{node} 未匹配蓝牙鼠标接口。")
        yield BluetoothMouse(node, name)
        return
    validate, mouse_class = (
        (is_direct_mouse_node, DirectMouse) if connection == "usb" else (is_receiver_node, Mouse)
    )
    if not validate(node):
        raise DeviceError(f"{node} 未匹配 {CONNECTION_LABELS[connection]} 配置接口。")
    with open_device(node) as handle:
        yield mouse_class(handle, node)


def decode_dpi_table(kind: int, report: bytes) -> dict[int, int]:
    if kind == 1:
        values = {}
        for start in range(2, 32, 3):
            value = int.from_bytes(report[start:start + 2], "little")
            if value == 0:
                break
            values[report[start + 2]] = value
        return values
    if kind == 2:
        step = int.from_bytes(report[2:4], "little")
        count = int.from_bytes(report[4:6], "little")
        start = int.from_bytes(report[6:8], "little")
        code, code_step = report[8:10]
        if not 0 < count <= 256 or not step or not code_step:
            raise DeviceError("DPI 表参数超出有效范围。")
        table = {(code + code_step * i) & 0xFF: start + step * i for i in range(count)}
        if len(table) != count:
            raise DeviceError("DPI 表包含重复的设备代码。")
        return table
    raise DeviceError(f"暂不支持 DPI 表类型 {kind}。")


def settings_from_report(report: bytes) -> Settings:
    modes = report[4:8]
    return Settings(
        report[2] & 3,
        tuple(value & 7 for value in modes),
        tuple(value >> 6 for value in modes),
        tuple((value >> 4) & 3 for value in modes),
        led_from_report(report),
        advance_from_report(report),
    )


def led_from_report(report: bytes) -> Led:
    return Led(
        report[8] & 0x1F,
        (report[8] >> 5) & 1,
        report[10] & 7,
        (report[10] >> 4) & 7,
        report[11] & 7,
    )


def advance_from_report(report: bytes) -> AdvanceSetting:
    flags = report[14]
    return AdvanceSetting(
        report[12] & 7,
        (report[12] >> 4) & 7,
        report[13] & 7,
        (report[13] >> 4) & 7,
        flags & 3,
        *(bool(flags & mask) for _, mask, _ in ADVANCE_FLAGS),
        bool(report[15] & 1),
    )


def pairing_from_report(report: bytes) -> Pairing:
    high = int.from_bytes(report[8:10], "little")
    low = int.from_bytes(report[10:12], "little")
    return Pairing(
        int.from_bytes(report[2:6], "little"),
        (high << 16) | low,
        int.from_bytes(report[12:14], "big"),
    )


def led_request(led: Led) -> bytes:
    report = bytearray(16)
    report[0] = 0x0E
    report[1] = 0x60
    # The receiver always reports 0x40 back for this nibble, so set it unconditionally.
    report[8] = (led.mode & 0x1F) | ((led.direction & 1) << 5) | 0xC0
    report[10] = 0x88 | (led.brightness & 7) | ((led.speed & 7) << 4)
    report[11] = 0x08 | (led.color & 7)
    return bytes(report)


def advance_request(advance: AdvanceSetting) -> bytes:
    report = bytearray(16)
    report[0] = 0x0E
    report[1] = 0x60
    report[2] = 0x40
    report[12] = (advance.wireless_sleep & 7) | ((advance.wireless_deep & 7) << 4)
    report[13] = (advance.bluetooth_sleep & 7) | ((advance.bluetooth_deep & 7) << 4)
    report[14] = advance.lift & 3
    for name, mask, _ in ADVANCE_FLAGS:
        if getattr(advance, name):
            report[14] |= mask
    if advance.line_correct:
        report[15] |= 1
    return bytes(report)


def levels_request(levels: tuple[int, ...]) -> bytes:
    return (bytes((0x0E, 0x60, 0x00, 0x0F)) + bytes(level & 7 for level in levels)).ljust(16, b"\0")


def rates_request(settings: Settings) -> bytes:
    packed = bytes(
        ((usb & 3) << 6) | ((wireless & 3) << 4)
        for usb, wireless in zip(settings.usb_rates, settings.wireless_rates)
    )
    return (bytes((0x0E, 0x60, 0x00, 0xF0)) + packed).ljust(16, b"\0")


def codes_request(codes: bytes) -> bytes:
    return (bytes((0x0E, 0x62, 0x80)) + bytes(codes)).ljust(16, b"\0")


def mode_request(mode: int) -> bytes:
    return bytes((0x0E, 0x60, 0x20 | (mode & 3), 0x00)).ljust(16, b"\0")


class Mouse:
    connection = "2.4g"
    write_size = 16
    opcodes: dict[int, int] = {}

    def __init__(self, handle, node: Path | None = None):
        self.handle = handle
        self.node = node

    def _drain(self) -> None:
        """Discard reports left over by an earlier transaction.

        A write acknowledgement and a state read both start with 0e 60 and carry no
        sequence number, so a stale acknowledgement would otherwise be mistaken for
        the response to the next request.
        """
        while select.select([self.handle], [], [], 0)[0]:
            if not os.read(self.handle.fileno(), 64):
                return

    def _query(self, opcode: int, payload: bytes = b"", attempts: int = 3) -> bytes:
        report = (bytes((0x0E, self.opcodes.get(opcode, opcode))) + payload).ljust(self.write_size, b"\0")
        timeout = 0.1 if opcode < 0x60 else 0.3
        for _ in range(attempts):
            self._drain()
            if self.handle.write(report) != self.write_size:
                raise DeviceError("HID 报告写入不完整。")
            deadline = time.monotonic() + timeout
            while (remaining := deadline - time.monotonic()) > 0:
                if not select.select([self.handle], [], [], remaining)[0]:
                    break
                response = os.read(self.handle.fileno(), 32)
                if not response:
                    raise DeviceError("设备已断开。")
                if len(response) == 32 and response[:2] == report[:2]:
                    return response
        raise DeviceError(f"命令 0x{opcode:02x} 超时，请确认鼠标已唤醒。")

    def _send(self, report: bytes) -> None:
        """Write a request and consume its acknowledgement."""
        self._query(report[1], report[2:], attempts=1)

    def _settings(self) -> Settings:
        return settings_from_report(self._query(0x60))

    def _field(self, name: str):
        """Read one settings field for verification.

        Fields the request did not touch are left out of the comparison: the
        receiver applies a burst of writes with visible lag, so unrelated fields
        can still read back the previous value right after a write.
        """
        return getattr(self._settings(), name)

    def power_status(self) -> PowerStatus:
        status = self._query(0x20)
        return PowerStatus(bool(status[9] & 0x80), status[8] & 0x7F, bool(status[8] & 0x80))

    def pairing(self) -> Pairing:
        return pairing_from_report(self._query(0x2D))

    def keyboard_pairing(self) -> Pairing:
        return pairing_from_report(self._query(0x2E))

    def firmware_state(self, power: PowerStatus | None = None) -> DeviceState:
        receiver = self._query(0x21)
        power = power if power is not None else self.power_status()
        state = DeviceState(
            int.from_bytes(receiver[18:20], "big"),
            power.online,
            power.battery,
            power.charging,
            pairing=self.pairing(),
            receiver_fwid=int.from_bytes(receiver[14:18], "big"),
            node=self.node,
        )
        if not power.online:
            return state

        identity = self._query(0x61)
        return replace(state, firmware=int.from_bytes(identity[18:20], "big"),
                       mouse_fwid=int.from_bytes(identity[14:18], "big"))

    def state(self, power: PowerStatus | None = None) -> DeviceState:
        state = self.firmware_state(power)
        if not state.online:
            return state
        if model_name(state.mouse_fwid) is None:
            raise DeviceError(f"当前鼠标 FWID 为 0x{state.mouse_fwid:08x}，不在支持的型号表内。")
        settings = self._settings()
        dpi = self._query(0x62)
        kind = dpi[11]
        if kind not in (1, 2):
            raise DeviceError(f"暂不支持 DPI 表类型 {kind}。")
        table = decode_dpi_table(kind, self._query(0x63))
        return replace(
            state,
            settings=settings,
            dpi_codes=dpi[3:11],
            dpi_table=table,
        )

    @staticmethod
    def _online_settings(state: DeviceState) -> Settings:
        if state.settings is None:
            raise DeviceError("鼠标离线，请移动或轻按鼠标唤醒。")
        return state.settings

    def _verify(self, matches: Callable[[], bool]) -> None:
        for _ in range(3):
            if matches():
                return
        raise DeviceError("回读值与目标设置不一致。")

    def _change(self, apply, restore, matches, original_matches, attempts: int = 3) -> None:
        """Apply a write, confirm it by read back, and roll back if it never lands.

        The receiver occasionally drops a write frame without acknowledging it and
        without changing state, so a single attempt is not enough. Windows retries
        each command up to three times for the same reason. A retry re-sends the
        whole write, which is safe because every write here is idempotent.
        """
        error: Exception | None = None
        for _ in range(attempts):
            try:
                apply()
                self._verify(matches)
                return
            except (DeviceError, OSError, KeyboardInterrupt) as failure:
                error = failure
                if isinstance(failure, KeyboardInterrupt):
                    break
        assert error is not None
        try:
            # An acknowledgement can be lost after the device applied a write.
            # Restore once, and confirm the original state even if that acknowledgement is lost.
            try:
                restore()
            except (DeviceError, OSError):
                pass
            self._verify(original_matches)
        except (DeviceError, OSError) as recovery_error:
            raise DeviceError(
                f"写入失败（{error}），无法确认原值已恢复（{recovery_error}）。"
            ) from error
        if isinstance(error, KeyboardInterrupt):
            raise error
        raise DeviceError(f"写入失败，已确认原值（{error}）。") from error

    def _write_levels(self, levels: tuple[int, ...]) -> None:
        self._send(levels_request(levels))

    def _write_rates(self, settings: Settings) -> None:
        self._send(rates_request(settings))

    def _write_codes(self, codes: bytes) -> None:
        self._send(codes_request(codes))

    def _write_led(self, led: Led) -> None:
        self._send(led_request(led))

    def _write_advance(self, advance: AdvanceSetting) -> None:
        self._send(advance_request(advance))

    def _write_dpi_codes(self, codes: bytes, levels: tuple[int, ...]) -> None:
        self._write_codes(codes)
        self._write_levels(levels)

    def set_mode(self, mode: int) -> None:
        if not 0 <= mode <= 3:
            raise DeviceError("配置模式范围是 1 到 4。")
        current = self._online_settings(self.state())
        if current.mode == mode:
            return
        target = replace(current, mode=mode)
        self._change(
            lambda: self._send(mode_request(mode)),
            lambda: self._send(mode_request(current.mode)),
            lambda: self._field("mode") == target.mode,
            lambda: self._field("mode") == current.mode,
        )

    def set_slot(self, slot: int, mode: int | None = None) -> None:
        state = self.state()
        original = self._online_settings(state)
        mode = original.mode if mode is None else mode
        if slot not in state.slots:
            raise DeviceError(f"DPI 档位 {slot} 未启用。")
        if original.levels[mode] == slot - 1:
            return
        levels = list(original.levels)
        levels[mode] = slot - 1
        target = replace(original, levels=tuple(levels))
        self._change(
            lambda: self._write_levels(target.levels),
            lambda: self._write_levels(original.levels),
            lambda: self._field("levels") == target.levels,
            lambda: self._field("levels") == original.levels,
        )

    def set_dpi(self, dpi: int, slot: int | None = None) -> None:
        state = self.state()
        original = self._online_settings(state)
        slot = original.levels[original.mode] + 1 if slot is None else slot
        if not 1 <= slot <= len(state.dpi_codes):
            raise DeviceError(f"DPI 档位范围是 1 到 {len(state.dpi_codes)}。")
        table = state.dpi_table or {}
        code = next((code for code, value in table.items() if value == dpi and code != EMPTY_DPI), None)
        if code is None:
            raise DeviceError(f"设备的 DPI 表没有 {dpi} DPI。")
        if state.dpi_codes[slot - 1] == code:
            return
        codes = bytearray(state.dpi_codes)
        codes[slot - 1] = code
        target = bytes(codes)

        def matches(expected_codes: bytes) -> bool:
            return self._query(0x62)[3:11] == expected_codes

        self._change(
            lambda: self._write_dpi_codes(target, original.levels),
            lambda: self._write_dpi_codes(state.dpi_codes, original.levels),
            lambda: matches(target),
            lambda: matches(state.dpi_codes),
        )

    def disable_slot(self, slot: int) -> None:
        state = self.state()
        original = self._online_settings(state)
        if not 1 <= slot <= len(state.dpi_codes):
            raise DeviceError(f"DPI 档位范围是 1 到 {len(state.dpi_codes)}。")
        if state.dpi_codes[slot - 1] == EMPTY_DPI:
            return
        codes = bytearray(state.dpi_codes)
        codes[slot - 1] = EMPTY_DPI
        target = bytes(codes)

        def matches(expected_codes: bytes) -> bool:
            return self._query(0x62)[3:11] == expected_codes

        self._change(
            lambda: self._write_codes(target),
            lambda: self._write_codes(state.dpi_codes),
            lambda: matches(target),
            lambda: matches(state.dpi_codes),
        )

    def _set_rate(self, hz: int, wireless: bool, mode: int | None) -> None:
        if hz not in RATES:
            raise DeviceError("回报率仅支持 125、250、500、1000 Hz。")
        original = self._online_settings(self.state())
        mode = original.mode if mode is None else mode
        rates = list(original.wireless_rates if wireless else original.usb_rates)
        if rates[mode] == RATES.index(hz):
            return
        rates[mode] = RATES.index(hz)
        target = (
            replace(original, wireless_rates=tuple(rates))
            if wireless
            else replace(original, usb_rates=tuple(rates))
        )
        field = "wireless_rates" if wireless else "usb_rates"
        self._change(
            lambda: self._write_rates(target),
            lambda: self._write_rates(original),
            lambda: self._field(field) == getattr(target, field),
            lambda: self._field(field) == getattr(original, field),
        )

    def set_rate(self, hz: int, mode: int | None = None) -> None:
        self._set_rate(hz, True, mode)

    def set_usb_rate(self, hz: int, mode: int | None = None) -> None:
        self._set_rate(hz, False, mode)

    def set_led(self, mode: int | None = None, brightness: int | None = None,
                speed: int | None = None, direction: int | None = None) -> None:
        original = self._online_settings(self.state())
        changes = {
            name: value
            for name, value in (
                ("mode", mode), ("brightness", brightness),
                ("speed", speed), ("direction", direction),
            )
            if value is not None
        }
        if "mode" in changes and not 0 <= changes["mode"] <= 31:
            raise DeviceError("灯效编号范围是 0 到 31。")
        if "brightness" in changes and not 0 <= changes["brightness"] <= 7:
            raise DeviceError("亮度范围是 0 到 7。")
        if "speed" in changes and not 0 <= changes["speed"] <= 7:
            raise DeviceError("速度范围是 0 到 7。")
        if "direction" in changes and changes["direction"] not in (0, 1):
            raise DeviceError("方向只能是 0 或 1。")
        target = replace(original.led, **changes)
        if target == original.led:
            return
        self._change(
            lambda: self._write_led(target),
            lambda: self._write_led(original.led),
            lambda: self._field("led") == target,
            lambda: self._field("led") == original.led,
        )

    def set_advance(self, **changes) -> None:
        original = self._online_settings(self.state())
        known = {
            "wireless_sleep", "wireless_deep", "bluetooth_sleep", "bluetooth_deep",
            "lift", "line_correct",
            *(name for name, _, _ in ADVANCE_FLAGS),
        }
        unknown = set(changes) - known
        if unknown:
            raise DeviceError(f"未知的高级设置项：{', '.join(sorted(unknown))}。")
        for key in ("wireless_sleep", "wireless_deep", "bluetooth_sleep", "bluetooth_deep"):
            if key in changes and not 0 <= changes[key] <= 7:
                raise DeviceError("休眠代码范围是 0 到 7。")
        if "lift" in changes and not 0 <= changes["lift"] <= 3:
            raise DeviceError("鼠标抬起高度范围是 0 到 3。")
        target = replace(original.advance, **changes)
        if target == original.advance:
            return
        self._change(
            lambda: self._write_advance(target),
            lambda: self._write_advance(original.advance),
            lambda: self._field("advance") == target,
            lambda: self._field("advance") == original.advance,
        )


class DirectMouse(Mouse):
    connection = "usb"
    write_size = 32
    opcodes = {0x60: 0x20, 0x61: 0x21, 0x62: 0x22, 0x63: 0x23}

    def firmware_state(self, power: PowerStatus | None = None) -> DeviceState:
        identity = self._query(0x61)
        return DeviceState(
            0, True, None, None,
            firmware=int.from_bytes(identity[18:20], "big"),
            mouse_fwid=int.from_bytes(identity[14:18], "big"),
            connection=self.connection,
            node=self.node,
        )


class BluetoothMouse:
    connection = "bluetooth"

    def __init__(self, node: Path, name: str):
        self.node = node
        self.name = name

    def firmware_state(self) -> DeviceState:
        return self.state()

    def state(self, power: PowerStatus | None = None) -> DeviceState:
        battery = None
        device = HID_SYSFS / self.node.name / "device"
        for capacity in sorted(device.glob("power_supply/*/capacity")):
            try:
                value = int(capacity.read_text().strip())
            except (OSError, ValueError):
                continue
            if 0 <= value <= 100:
                battery = value
                break
        if battery is None:
            battery = bluez_battery(device)
        return DeviceState(0, True, battery, None, connection=self.connection,
                           node=self.node, device_name=self.name)


def bluez_battery(device: Path) -> int | None:
    try:
        properties = (device / "uevent").read_text()
    except OSError:
        return None
    address = re.search(r"^HID_UNIQ=((?:[0-9a-f]{2}:){5}[0-9a-f]{2})$",
                        properties, re.IGNORECASE | re.MULTILINE)
    if address is None:
        return None
    device_name = "dev_" + address[1].upper().replace(":", "_")
    for adapter in sorted(BLUETOOTH_SYSFS.glob("hci*")):
        if not re.fullmatch(r"hci\d+", adapter.name):
            continue
        try:
            result = subprocess.run(
                ["busctl", "--system", "get-property", "org.bluez",
                 f"/org/bluez/{adapter.name}/{device_name}", "org.bluez.Battery1", "Percentage"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        match = re.fullmatch(r"y (\d{1,3})", result.stdout.strip())
        if result.returncode == 0 and match and (value := int(match[1])) <= 100:
            return value
    return None


def sleep_seconds(code: int) -> str:
    if code < len(WIRELESS_SLEEP_SECONDS):
        return f"{WIRELESS_SLEEP_SECONDS[code]} 秒"
    return f"代码 {code}"


def deep_sleep_minutes(code: int) -> str:
    if code < len(WIRELESS_DEEP_SLEEP_MINUTES):
        return f"{WIRELESS_DEEP_SLEEP_MINUTES[code]} 分钟"
    return f"代码 {code}"


def lift_label(code: int) -> str:
    return LIFT_LABELS[code] if code < len(LIFT_LABELS) else f"代码 {code}"


def flag_labels(advance: AdvanceSetting) -> str:
    names = [label for name, _, label in ADVANCE_FLAGS if getattr(advance, name)]
    if advance.line_correct:
        names.append("直线修正")
    return "、".join(names) if names else "无"


def print_firmware(state: DeviceState) -> None:
    if state.connection == "bluetooth":
        raise DeviceError("蓝牙直连无法读取固件版本，请切换到 2.4G 或 USB 有线模式。")
    if state.connection == "2.4g":
        name = RECEIVERS.get(state.receiver_fwid, f"0x{state.receiver_fwid:08x}")
        print(
            f"接收器 {name}（FWID 0x{state.receiver_fwid:08x}），"
            f"固件 {firmware_version(state.receiver_fwid, state.receiver_firmware)}"
        )
    if state.firmware is None or state.mouse_fwid == 0:
        print("鼠标离线。移动或轻按鼠标后重试。")
        return
    model = state.model or f"0x{state.mouse_fwid:08x}"
    label = "USB 直连鼠标" if state.connection == "usb" else "鼠标"
    print(
        f"{label} {model}（FWID 0x{state.mouse_fwid:08x}），"
        f"固件 {firmware_version(state.mouse_fwid, state.firmware)}"
    )


def print_state(state: DeviceState) -> None:
    if state.connection == "2.4g":
        name = RECEIVERS.get(state.receiver_fwid, "未知型号")
        print(
            f"2.4G 接收器 {name}，固件 "
            f"{firmware_version(state.receiver_fwid, state.receiver_firmware)}"
        )
    else:
        print(f"连接方式：{CONNECTION_LABELS[state.connection]}")
    pairing = state.pairing
    if pairing is not None and pairing.firmware_id:
        model = model_name(pairing.firmware_id) or f"0x{pairing.firmware_id:08x}"
        print(f"接收器记录的鼠标 {model}，配对记录 0x{pairing.version:04x}")
    if not state.online:
        print("鼠标离线。移动或轻按鼠标后重试。")
        return
    model = state.model or "未知型号"
    if state.connection == "bluetooth":
        print(f"{model} · 已连接")
        if state.battery is not None:
            print(f"电量 {state.battery}%")
        print(BLUETOOTH_READ_ONLY)
        return
    print(
        f"{model}（FWID 0x{state.mouse_fwid:08x}，"
        f"固件 {firmware_version(state.mouse_fwid, state.firmware)}）"
    )
    if state.battery is not None:
        print(f"电量 {state.battery}%，{'正在充电' if state.charging else '未充电'}")
    mode = state.settings.mode
    selected = state.settings.levels[mode] + 1
    print(f"配置模式 {mode + 1}")
    print(f"  USB 回报率 {RATES[state.settings.usb_rates[mode]]} Hz")
    print(f"  2.4G 回报率 {RATES[state.settings.wireless_rates[mode]]} Hz")
    for slot, dpi in state.slots.items():
        print(f"DPI 档位 {slot}：{dpi} DPI{'（当前）' if slot == selected else ''}")
    disabled = [index for index, code in enumerate(state.dpi_codes, 1) if code == EMPTY_DPI]
    if disabled:
        print(f"未启用的档位：{'、'.join(str(slot) for slot in disabled)}")
    led = state.settings.led
    print(f"灯效：{led.effect}，亮度 {led.brightness}，速度 {led.speed}，方向 {LED_DIRECTIONS[led.direction]}")
    advance = state.settings.advance
    print(f"休眠：一级 {sleep_seconds(advance.wireless_sleep)}，深度 {deep_sleep_minutes(advance.wireless_deep)}")
    print(f"蓝牙休眠代码：一级 {advance.bluetooth_sleep}，深度 {advance.bluetooth_deep}")
    print(f"鼠标抬起高度：{lift_label(advance.lift)}")
    print(f"高级功能：{flag_labels(advance)}")


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", type=Path, help="指定配置节点，例如 /dev/hidraw4")
    parser.add_argument("--connection", choices=("auto", *CONNECTION_LABELS), default="auto",
                        help="指定连接方式，默认按 USB 有线、蓝牙、2.4G 的顺序查找")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="英菲克鼠标设置工具")
    add_connection_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="读取设备、电量、DPI、灯效与高级设置")
    commands.add_parser("firmware", help="显示鼠标与接收器的固件版本")
    commands.add_parser("models", help="列出驱动支持的鼠标与接收器型号")

    fetch = commands.add_parser(
        "fetch-firmware",
        help="从英菲克官方安装包提取当前鼠标的固件 .bin",
    )
    fetch.add_argument("--fwid", type=lambda value: int(value, 0),
                       help="指定鼠标 FWID；默认从接收器或 USB 直连鼠标读取")
    fetch.add_argument("--installer", type=Path,
                       help="使用本地厂商安装包，不联网下载")
    fetch.add_argument("--app-exe", type=Path,
                       help="直接使用已解包的 INPHIC 英云.exe")
    fetch.add_argument("--output", type=Path, help="输出 .bin 路径")
    fetch.add_argument("--url", help="自定义安装包 URL，默认使用官方地址")
    fetch.add_argument("--no-helper-download", action="store_true",
                       help="禁止自动下载 innoextract 辅助工具")

    upgrade = commands.add_parser(
        "upgrade", help="通过 USB 有线连接升级鼠标固件"
    )
    upgrade.add_argument("--file", type=Path,
                         help="本地固件 .bin；与 --download/--installer 三选一")
    upgrade.add_argument("--download", action="store_true",
                         help="从英菲克官网下载安装包并提取当前鼠标固件")
    upgrade.add_argument("--installer", type=Path,
                         help="从本地厂商安装包提取当前鼠标固件")
    upgrade.add_argument("--url", help="自定义安装包 URL（与 --download 搭配）")
    upgrade.add_argument("--no-helper-download", action="store_true",
                         help="禁止自动下载 innoextract 辅助工具")
    upgrade.add_argument("--boot-device", type=Path,
                         help="指定 USB 直连鼠标或 Bootloader 节点，默认自动查找")
    upgrade.add_argument("--yes", action="store_true", help="跳过交互确认")
    upgrade.add_argument("--force", action="store_true",
                         help="允许重刷相同版本或降级")
    upgrade.add_argument("--dry-run", action="store_true",
                         help="只校验固件并显示写入计划，不写入 Flash")
    upgrade.add_argument("--backup", type=Path,
                         help="原 Flash 备份路径，默认保存在用户主目录")
    upgrade.add_argument("--fwid", type=lambda value: int(value, 0),
                         help="设备已在 Bootloader 时指定鼠标 FWID，例如 0x00710106")

    mode = commands.add_parser("set-mode", help="切换配置模式")
    mode.add_argument("mode", type=int, choices=range(1, 5), metavar="{1,2,3,4}")

    slot = commands.add_parser("set-slot", help="切换 DPI 档位")
    slot.add_argument("slot", type=int)
    slot.add_argument("--mode", type=int, choices=range(1, 5), metavar="{1,2,3,4}")

    dpi = commands.add_parser("set-dpi", help="设置 DPI 档位的数值")
    dpi.add_argument("dpi", type=int)
    dpi.add_argument("--slot", type=int, help="指定档位，默认使用当前档位")

    disable = commands.add_parser("disable-slot", help="停用 DPI 档位")
    disable.add_argument("slot", type=int)

    rate = commands.add_parser("set-rate", help="设置当前模式的 2.4G 回报率")
    rate.add_argument("hz", type=int, choices=RATES)
    rate.add_argument("--mode", type=int, choices=range(1, 5), metavar="{1,2,3,4}")

    usb_rate = commands.add_parser("set-usb-rate", help="设置当前模式的有线回报率")
    usb_rate.add_argument("hz", type=int, choices=RATES)
    usb_rate.add_argument("--mode", type=int, choices=range(1, 5), metavar="{1,2,3,4}")

    led = commands.add_parser("set-led", help="设置灯效模式、亮度与速度")
    led.add_argument("--effect", type=int, choices=sorted(LED_EFFECTS), help="灯效编号")
    led.add_argument("--brightness", type=int, choices=range(LED_STEPS), metavar="{0,1,2,3}")
    led.add_argument("--speed", type=int, choices=range(LED_STEPS), metavar="{0,1,2,3}")
    led.add_argument("--direction", choices=("forward", "reverse"))

    advance = commands.add_parser("set-advance", help="设置休眠与高级功能")
    advance.add_argument("--sleep", type=int, choices=range(len(WIRELESS_SLEEP_SECONDS)),
                         dest="wireless_sleep", help="一级休眠代码")
    advance.add_argument("--deep-sleep", type=int, choices=range(len(WIRELESS_DEEP_SLEEP_MINUTES)),
                         dest="wireless_deep", help="深度休眠代码")
    advance.add_argument("--bluetooth-sleep", type=int, choices=range(8), help="蓝牙一级休眠代码")
    advance.add_argument("--bluetooth-deep-sleep", type=int, choices=range(8),
                         dest="bluetooth_deep", help="蓝牙深度休眠代码")
    advance.add_argument("--lift", type=int, choices=range(len(LIFT_LABELS)))
    advance.add_argument("--line-correct", choices=("on", "off"))
    for name, _, label in ADVANCE_FLAGS:
        advance.add_argument(f"--{name.replace('_', '-')}", choices=("on", "off"), help=label)
    return parser


def apply_command(mouse: Mouse, args) -> None:
    on_off = {"on": True, "off": False}
    mode = getattr(args, "mode", None)
    if args.command == "set-mode":
        mouse.set_mode(args.mode - 1)
    elif args.command == "set-slot":
        mouse.set_slot(args.slot, None if mode is None else mode - 1)
    elif args.command == "set-dpi":
        mouse.set_dpi(args.dpi, args.slot)
    elif args.command == "disable-slot":
        mouse.disable_slot(args.slot)
    elif args.command == "set-rate":
        mouse.set_rate(args.hz, None if mode is None else mode - 1)
    elif args.command == "set-usb-rate":
        mouse.set_usb_rate(args.hz, None if mode is None else mode - 1)
    elif args.command == "set-led":
        direction = getattr(args, "direction", None)
        mouse.set_led(
            mode=args.effect,
            brightness=args.brightness,
            speed=args.speed,
            direction=None if direction is None else LED_DIRECTIONS.index(
                "正向" if direction == "forward" else "反向"
            ),
        )
    elif args.command == "set-advance":
        changes = {}
        for name in ("wireless_sleep", "wireless_deep", "bluetooth_sleep", "bluetooth_deep", "lift"):
            value = getattr(args, name, None)
            if value is not None:
                changes[name] = value
        flag_names = [name for name, _, _ in ADVANCE_FLAGS] + ["line_correct"]
        for name in flag_names:
            value = getattr(args, name, None)
            if value is not None:
                changes[name] = on_off[value]
        if not changes:
            raise DeviceError("请至少指定一项要修改的高级设置。")
        mouse.set_advance(**changes)


def _receiver_fwid(node: Path | None) -> int | None:
    """The paired mouse FWID known through the receiver, if it is reachable."""

    try:
        with open_mouse(node, "2.4g") as mouse:
            state = mouse.state()
    except (DeviceError, OSError):
        return None
    if state.mouse_fwid:
        return state.mouse_fwid
    if state.pairing is not None and state.pairing.firmware_id:
        return state.pairing.firmware_id
    return None


def _current_firmware(node: Path | None):
    """The target mouse's running firmware, preferring the USB mouse itself."""

    from . import firmware

    nodes = firmware.direct_mouse_nodes()
    if len(nodes) == 1:
        try:
            return firmware.direct_mouse_info(nodes[0])
        except (DeviceError, OSError):
            pass
    # A device in BootV1 is not running its app; the receiver's last known
    # version must not suppress a recovery flash.
    if firmware.bootloader_nodes():
        return None
    try:
        with open_mouse(node, "2.4g") as mouse:
            state = mouse.state()
    except (DeviceError, OSError):
        return None
    if state.firmware is not None and state.mouse_fwid:
        return firmware.FirmwareInfo(state.mouse_fwid, state.firmware)
    return None


def _print_upgrade_plan(plan) -> None:
    image = plan.image
    prepared = plan.prepared
    print(f"固件文件：{image.path}")
    print(
        f"型号 {image.model}（FWID 0x{image.fwid:08x}），"
        f"固件 {image.version_text}，{len(image.data)} 字节"
    )
    print(
        f"Bootloader 系列 0x{plan.boot_fwid:08x}，"
        f"应用区 {plan.profile.app_size} 字节，Flash {plan.profile.flash_size} 字节"
    )
    print(
        f"写入长度 {len(prepared.data)} 字节，"
        f"appCS=0x{prepared.app_checksum:04x}，flashCS=0x{prepared.flash_checksum:04x}"
    )
    print("dry-run：未写入 Flash，也未进入 Bootloader；必要时只读接收器型号用于校验。")


def _resolve_fwid(node: Path | None, explicit: int | None, connection: str = "auto") -> int | None:
    from . import firmware

    if explicit is not None:
        return explicit
    if connection == "bluetooth":
        return None
    if connection == "usb":
        with open_mouse(node, connection) as mouse:
            return mouse.firmware_state().mouse_fwid
    receiver = _receiver_fwid(node)
    if receiver is not None or connection == "2.4g":
        return receiver
    nodes = firmware.direct_mouse_nodes()
    if len(nodes) == 1:
        return firmware.direct_mouse_info(nodes[0]).fwid
    return None


def _progress_printer(step: int = 5):
    last = [-step]

    def progress(value: int, message: str) -> None:
        if value >= last[0] + step or value == 100:
            print(f"{value:3d}% {message}", flush=True)
            last[0] = value

    return progress


def fetch_command(args) -> int:
    from . import firmware

    try:
        expected = _resolve_fwid(args.device, args.fwid, args.connection)
        if expected is None:
            print(
                "无法确定鼠标型号：请连接接收器或 USB 直连鼠标，"
                "或用 --fwid 指定。",
                file=sys.stderr,
            )
            return 1
        path = firmware.fetch_firmware(
            expected,
            installer=args.installer,
            app_exe=args.app_exe,
            output=args.output,
            url=args.url,
            progress=_progress_printer(),
            allow_helper_download=not args.no_helper_download,
        )
        image = firmware.parse_firmware(path)
    except (DeviceError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"固件已保存：{path}")
    print(f"{image.model} 固件 {image.version_text}，{len(image.data)} 字节")
    print(f"SHA-256：{hashlib.sha256(image.data).hexdigest()}")
    return 0


def upgrade_command(args) -> int:
    from . import firmware

    if args.connection not in ("auto", "usb"):
        print("固件升级需要 USB 有线连接。", file=sys.stderr)
        return 1
    if args.file is not None and (args.download or args.installer is not None):
        print("--file 与 --download/--installer 不能同时使用。", file=sys.stderr)
        return 1
    if args.download and args.installer is not None:
        print("--download 与 --installer 不能同时使用。", file=sys.stderr)
        return 1
    if args.file is None and not args.download and args.installer is None:
        print("请用 --file 指定本地固件，或加 --download 在线获取。", file=sys.stderr)
        return 1

    receiver_fwid = _receiver_fwid(args.device)
    if args.fwid is not None:
        if receiver_fwid is not None and receiver_fwid != args.fwid:
            print(
                f"--fwid 0x{args.fwid:08x} 与接收器记录的鼠标 "
                f"0x{receiver_fwid:08x} 不一致。",
                file=sys.stderr,
            )
            return 1
        expected = args.fwid
    else:
        expected = receiver_fwid
        if expected is None:
            nodes = firmware.direct_mouse_nodes()
            if len(nodes) == 1:
                try:
                    expected = firmware.direct_mouse_info(nodes[0]).fwid
                except (DeviceError, OSError) as error:
                    print(error, file=sys.stderr)
                    if not args.dry_run or args.file is None:
                        return 1

    if args.download or args.installer is not None:
        try:
            args.file = firmware.fetch_firmware(
                expected,
                installer=args.installer,
                url=args.url,
                progress=_progress_printer(),
                allow_helper_download=not args.no_helper_download,
            )
        except (DeviceError, OSError) as error:
            print(error, file=sys.stderr)
            return 1
        if expected is None:
            try:
                expected = firmware.parse_firmware(args.file).fwid
            except (DeviceError, OSError) as error:
                print(error, file=sys.stderr)
                return 1

    try:
        image = firmware.parse_firmware(args.file)
    except (DeviceError, OSError) as error:
        print(error, file=sys.stderr)
        return 1

    if expected is not None and not firmware.image_matches(image, expected):
        expected_model = model_name(expected) or f"0x{expected:08x}"
        print(
            f"固件文件是 {image.model}（FWID 0x{image.fwid:08x}），"
            f"当前鼠标是 {expected_model}（FWID 0x{expected:08x}），型号不匹配。",
            file=sys.stderr,
        )
        return 1

    current = _current_firmware(args.device)
    same_version = current is not None and current.version == image.version
    older_version = current is not None and image.version < current.version

    if args.dry_run:
        try:
            plan = firmware.upgrade_mouse(
                args.file, expected_fwid=expected, dry_run=True
            )
        except (DeviceError, OSError) as error:
            print(error, file=sys.stderr)
            return 1
        _print_upgrade_plan(plan)
        if same_version:
            print("注意：当前鼠标已经是这个版本；实际升级会跳过，除非加 --force。")
        elif older_version:
            print("注意：目标版本低于当前版本；实际升级需要 --force。")
        return 0

    if same_version and not args.force:
        print(f"当前鼠标固件已是 {image.version_text}，无需升级。")
        return 0
    if older_version and not args.force:
        print(
            f"目标固件 {image.version_text} 低于当前 {current.version_text}；"
            "如需降级请加 --force。",
            file=sys.stderr,
        )
        return 1
    if (same_version or older_version) and args.force:
        print(f"已启用 --force，继续写入 {image.version_text}。")

    print(f"固件文件：{args.file}")
    print(
        f"即将把 {image.model} 固件 {image.version_text} "
        f"（{len(image.data)} 字节）写入鼠标。"
    )
    print("鼠标必须通过 USB 数据线连接；升级过程中请勿断开 USB 或退出程序。")
    print(
        "原 Flash 会自动备份到一个文件"
        + (f"：{args.backup}" if args.backup is not None else "（用户主目录）。")
    )
    if not args.yes:
        if not sys.stdin.isatty():
            print("非交互环境下请加 --yes 确认升级。", file=sys.stderr)
            return 1
        answer = input("确认继续请输入 yes：").strip().lower()
        if answer not in ("yes", "y"):
            print("已取消。", file=sys.stderr)
            return 1

    progress = _progress_printer()

    try:
        result = firmware.upgrade_mouse(
            args.file,
            expected_fwid=expected,
            device=args.boot_device,
            backup_path=args.backup,
            progress=progress,
            force=args.force,
        )
    except (DeviceError, OSError) as error:
        print(error, file=sys.stderr)
        return 1

    print(f"原 Flash 备份：{result.backup}")
    if result.app_firmware is not None:
        if result.app_firmware.version == result.image.version:
            print(
                f"升级完成并已回读：{result.app_firmware.model} "
                f"固件 {result.app_firmware.version_text}"
            )
            return 0
        print(
            f"警告：设备回读固件 {result.app_firmware.version_text} "
            f"与目标 {result.image.version_text} 不一致。",
            file=sys.stderr,
        )
        return 1
    if result.rebooted:
        print(
            "固件已写入并校验，设备已重启；未能在重启后回读新版本，"
            "请重新插拔鼠标后运行 firmware 命令查看。"
        )
        return 0
    print(
        "固件已写入并校验，但设备仍停留在 Bootloader。"
        "请拔插 USB 后重新运行 upgrade；原 Flash 备份已保存。",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "models":
        for fwid, name in sorted(MODELS.items()):
            print(f"鼠标 0x{fwid:08x} {name}")
        for fwid, name in sorted(RECEIVERS.items()):
            print(f"接收器 0x{fwid:08x} {name}")
        return 0
    if args.command == "fetch-firmware":
        try:
            return fetch_command(args)
        except KeyboardInterrupt:
            print("操作已中断。", file=sys.stderr)
            return 130
    if args.command == "upgrade":
        try:
            return upgrade_command(args)
        except KeyboardInterrupt:
            print(
                "升级已中断。设备可能仍停留在 Bootloader，可重新运行 upgrade。",
                file=sys.stderr,
            )
            return 130
    try:
        with open_mouse(args.device, args.connection) as mouse:
            if args.command == "firmware":
                print_firmware(mouse.firmware_state())
                return 0
            if args.command == "status":
                print_state(mouse.state())
                return 0
            if isinstance(mouse, BluetoothMouse):
                raise DeviceError(BLUETOOTH_READ_ONLY)
            apply_command(mouse, args)
        print("设置已回读确认。")
        return 0
    except (DeviceError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("操作已中断。", file=sys.stderr)
        return 130



if __name__ == "__main__":
    raise SystemExit(main())

