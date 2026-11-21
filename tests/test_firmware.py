from contextlib import contextmanager
from pathlib import Path
import io
from contextlib import redirect_stdout
import socket
import tempfile
import unittest
from unittest.mock import patch

from open_inphic import firmware
from open_inphic.hid import (
    DeviceError,
    DeviceState,
    Settings,
    firmware_version,
    print_firmware,
)


def image_bytes(fwid=0x00710106, version=0xEB02, size=64):
    data = bytearray(size)
    data[16:20] = fwid.to_bytes(4, "little")
    data[20:22] = version.to_bytes(2, "big")
    return bytes(data)


class FakeBootHandle:
    """A real socket-backed fake hidraw handle for select/read/write."""

    def __init__(self, boot):
        self.boot = boot
        self.read_end, self.write_end = socket.socketpair()
        self.writes = []

    def fileno(self):
        return self.read_end.fileno()

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        response = self.boot.respond(data)
        if response is not None:
            self.write_end.sendall(response)
        return len(data)

    def close(self):
        self.read_end.close()
        self.write_end.close()


class FakeBoot:
    def __init__(self, fwid, version, flash=None, app_fwid=0x00710106,
                 app_version=0xEB02):
        self.fwid = fwid
        self.version = version
        self.app_fwid = app_fwid
        self.app_version = app_version
        self.flash = bytearray(
            flash if flash is not None else b"\xff" * 0xC800
        )
        self.fifo = bytearray(0x100)
        self.begin = False
        self.ended = False
        self.app_started = False

    def respond(self, data):
        if len(data) != 32:
            raise AssertionError(f"unexpected report length {len(data)}")
        if data[:2] == b"\x0e\x20":
            response = bytearray(32)
            response[:2] = b"\x0e\x20"
            response[2:6] = self.fwid.to_bytes(4, "little")
            response[6:8] = self.version.to_bytes(2, "big")
            return bytes(response)
        if data[:2] == b"\x0e\x21":
            response = bytearray(32)
            response[:2] = b"\x0e\x21"
            response[14:18] = self.app_fwid.to_bytes(4, "big")
            response[18:20] = self.app_version.to_bytes(2, "big")
            return bytes(response)
        if data[:2] == b"\x0e\x1e":
            if data[2:6] == bytes.fromhex("55 aa 00 ff"):
                response = bytearray(32)
                response[:2] = b"\x0e\x1e"
                response[2] = 1
                return bytes(response)
            self.app_started = True
            response = bytearray(32)
            response[:2] = b"\x0e\x1e"
            response[2] = 1
            return bytes(response)
        if data[:2] == b"\x0e\x13":
            self.begin = True
            response = bytearray(32)
            response[:2] = b"\x0e\x13"
            response[5] = 1
            return bytes(response)
        if data[:2] == b"\x0e\x11":
            addr = data[2]
            length = data[3]
            self.fifo[addr:addr + length] = data[4:4 + length]
            response = bytearray(32)
            response[:3] = b"\x0e\x11\x00"
            response[2] = addr
            return bytes(response)
        if data[:2] == b"\x0e\x10":
            addr = data[2] | (data[3] << 8)
            length = data[4] or 0x100
            checksum = data[6] | (data[7] << 8)
            chunk = bytes(self.fifo[:length])
            if firmware.firmware_checksum(chunk) != checksum:
                response = bytearray(32)
                response[:6] = b"\x0e\x10\x00\x00\x00\x00"
                return bytes(response)
            self.flash[addr:addr + length] = chunk
            response = bytearray(32)
            response[:2] = b"\x0e\x10"
            response[2] = addr & 0xFF
            response[3] = (addr >> 8) & 0xFF
            response[4] = data[4]
            response[5] = 1
            return bytes(response)
        if data[:2] == b"\x0e\x12":
            addr = data[2] | (data[3] << 8)
            length = data[4]
            response = bytearray(32)
            response[:4] = b"\x0e\x12\x00\x00"
            response[4:4 + length] = self.flash[addr:addr + length]
            return bytes(response)
        if data[:2] == b"\x0e\x14":
            self.ended = True
            response = bytearray(32)
            response[:10] = data[:10]
            return bytes(response)
        raise AssertionError(f"unexpected report {data.hex(' ')}")


class ChecksumAndVersionTest(unittest.TestCase):
    def test_vendor_version_format(self):
        self.assertEqual(firmware_version(0x00710106, 0xEB02), "V0071.0106.EB02")
        self.assertEqual(firmware_version(0x01660004, 0x0902), "V0166.0004.0902")

    def test_checksum_matches_bootv1_initial_value(self):
        self.assertEqual(firmware.firmware_checksum(b"\x00\x00"), 0xFA21)
        self.assertEqual(firmware.firmware_checksum(b"\x01\x00"), 0xFA22)
        self.assertEqual(firmware.firmware_checksum(b"\xff\xff"), 0xFA20)
        with self.assertRaises(DeviceError):
            firmware.firmware_checksum(b"\x00")


class ImageTest(unittest.TestCase):
    def test_parse_official_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            path.write_bytes(image_bytes())
            image = firmware.parse_firmware(path)
        self.assertEqual(image.fwid, 0x00710106)
        self.assertEqual(image.version, 0xEB02)
        self.assertEqual(image.model, "X9")
        self.assertEqual(image.version_text, "V0071.0106.EB02")

    def test_rejects_unknown_or_invalid_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.bin"
            path.write_bytes(b"\0" * 10)
            with self.assertRaises(DeviceError):
                firmware.parse_firmware(path)
            path.write_bytes(image_bytes(fwid=0xDEADBEEF))
            with self.assertRaises(DeviceError):
                firmware.parse_firmware(path)
            path.write_bytes(image_bytes(fwid=0x01660004))
            with self.assertRaises(DeviceError):
                firmware.parse_firmware(path)
            path.write_bytes(image_bytes(size=firmware.MOUSE_APP_SIZE + 2))
            with self.assertRaises(DeviceError):
                firmware.parse_firmware(path)

    def test_m1_firmware_variant_matches_same_model(self):
        image = firmware.FirmwareImage(
            Path("m1.bin"), image_bytes(fwid=0x00710208), 0x00710208, 0xE302
        )
        self.assertTrue(firmware.image_matches(image, 0x00710207))
        self.assertFalse(firmware.image_matches(image, 0x00710106))

    def test_prepare_mouse_image_pads_app_and_flash(self):
        data = image_bytes(size=64)
        image = firmware.FirmwareImage(Path("x9.bin"), data, 0x00710106, 0xEB02)
        profile = firmware.boot_profile(firmware.MOUSE_BOOT_FAMILY)
        prepared = firmware.prepare_image(image, profile)
        self.assertEqual(len(prepared.data), firmware.MOUSE_FLASH_SIZE)
        self.assertEqual(prepared.data[:64], data)
        self.assertEqual(prepared.data[64:firmware.MOUSE_APP_SIZE],
                         b"\xff" * (firmware.MOUSE_APP_SIZE - 64))
        self.assertEqual(prepared.data[firmware.MOUSE_APP_SIZE:],
                         b"\xff" * (firmware.MOUSE_FLASH_SIZE - firmware.MOUSE_APP_SIZE))
        self.assertEqual(prepared.app_checksum,
                         firmware.firmware_checksum(prepared.data[:firmware.MOUSE_APP_SIZE]))
        self.assertEqual(prepared.flash_checksum,
                         firmware.firmware_checksum(prepared.data))

    def test_prepare_receiver_image_preserves_parameter_area(self):
        data = image_bytes(size=64)
        image = firmware.FirmwareImage(Path("receiver.bin"), data, 0x00710106, 0xEB02)
        profile = firmware.boot_profile(firmware.RECEIVER_BOOT_FAMILY)
        existing = bytearray(b"\xff" * profile.flash_size)
        existing[0xB700:0xB800] = bytes(range(256))
        prepared = firmware.prepare_image(image, profile, bytes(existing))
        self.assertEqual(prepared.data[0xB700:0xB800], bytes(range(256)))
        with self.assertRaises(DeviceError):
            firmware.prepare_image(image, profile, None)

    def test_dry_run_does_not_touch_the_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            path.write_bytes(image_bytes())
            with patch.object(firmware, "bootloader_nodes",
                              side_effect=AssertionError("must not enumerate")):
                plan = firmware.upgrade_mouse(
                    path, expected_fwid=0x00710106, dry_run=True
                )
        self.assertIsInstance(plan, firmware.UpgradePlan)
        self.assertEqual(plan.prepared.flash_checksum,
                         firmware.firmware_checksum(plan.prepared.data))


class BootloaderProtocolTest(unittest.TestCase):
    def setUp(self):
        self.original = bytes((index * 7) & 0xFF for index in range(0xC800))
        self.boot = FakeBoot(0x0071FF00, 0x0100, self.original)
        self.handle = FakeBootHandle(self.boot)
        self.addCleanup(self.handle.close)

    def bootloader(self):
        return firmware.Bootloader(Path("/dev/hidraw-test"), self.handle)

    def test_info_and_read_flash(self):
        boot = self.bootloader()
        info = boot.info()
        self.assertEqual(info.fwid, 0x0071FF00)
        self.assertEqual(info.version, 0x0100)
        self.assertEqual(boot.read_flash(0x10, 4), self.original[0x10:0x14])
        self.assertEqual(self.handle.writes[0][:2], b"\x0e\x20")
        self.assertEqual(self.handle.writes[1][:2], b"\x0e\x12")

    def test_write_fifo_and_flash_encodings(self):
        boot = self.bootloader()
        chunk = bytes(range(28))
        boot.write_chunk(0x20, chunk, firmware.boot_profile(firmware.MOUSE_BOOT_FAMILY))
        self.assertEqual(self.boot.flash[0x20:0x20 + 28], chunk)
        writes = [write for write in self.handle.writes if write[1] == 0x11]
        self.assertEqual(writes[0][:4], b"\x0e\x11\x00\x1c")
        flash_write = next(write for write in self.handle.writes if write[1] == 0x10)
        self.assertEqual(flash_write[:8], bytes.fromhex(
            "0e 10 20 00 1c a5"
        ) + firmware.firmware_checksum(chunk).to_bytes(2, "little"))

    def test_256_byte_flash_write_uses_zero_length_byte(self):
        boot = self.bootloader()
        chunk = bytes(range(256))
        boot.write_chunk(0, chunk, firmware.boot_profile(firmware.MOUSE_BOOT_FAMILY))
        flash_write = next(write for write in self.handle.writes if write[1] == 0x10)
        self.assertEqual(flash_write[4], 0)
        self.assertEqual(self.boot.flash[:256], chunk)

    def test_begin_end_and_app(self):
        boot = self.bootloader()
        boot.begin_upgrade()
        self.assertTrue(self.boot.begin)
        boot.end_upgrade(0xB000, 0xC800, 0x1234, 0x5678)
        self.assertTrue(self.boot.ended)
        self.assertTrue(boot.app())
        self.assertTrue(self.boot.app_started)

    def test_full_upgrade_flow_with_bootloader_present(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "x9.bin"
            path.write_bytes(image_bytes())
            backup = directory / "backup.bin"
            profile = firmware.boot_profile(firmware.MOUSE_BOOT_FAMILY)

            @contextmanager
            def fake_open(_node):
                yield self.handle

            with patch.multiple(
                firmware,
                bootloader_nodes=lambda: [Path("/dev/hidraw-test")],
                open_device=fake_open,
                wait_for_application=lambda *args, **kwargs: firmware.FirmwareInfo(
                    0x00710106, 0xEB02
                ),
                is_bootloader_node=lambda _node: False,
            ):
                result = firmware.upgrade_mouse(
                    path,
                    expected_fwid=0x00710106,
                    backup_path=backup,
                )
            self.assertTrue(result.confirmed)
            self.assertEqual(result.backup.read_bytes(), self.original)
            self.assertEqual(bytes(self.boot.flash), result.prepared.data)
            self.assertTrue(self.boot.begin)
            self.assertTrue(self.boot.ended)
            self.assertTrue(self.boot.app_started)

    def test_direct_mouse_upgrade_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "x9.bin"
            path.write_bytes(image_bytes(version=0xEC02))
            backup = directory / "backup.bin"

            @contextmanager
            def fake_open(_node):
                yield self.handle

            with patch.multiple(
                firmware,
                bootloader_nodes=lambda: [],
                direct_mouse_nodes=lambda: [Path("/dev/hidraw-test")],
                direct_mouse_info=lambda _node: firmware.FirmwareInfo(
                    0x00710106, 0xEB02
                ),
                enter_bootloader=lambda node, progress=None: True,
                wait_for_bootloader=lambda timeout=15.0, progress=None: Path(
                    "/dev/hidraw-test"
                ),
                open_device=fake_open,
                wait_for_application=lambda *args, **kwargs: firmware.FirmwareInfo(
                    0x00710106, 0xEC02
                ),
                is_bootloader_node=lambda _node: False,
            ):
                result = firmware.upgrade_mouse(
                    path,
                    expected_fwid=0x00710106,
                    backup_path=backup,
                )
            self.assertTrue(result.confirmed)
            self.assertEqual(result.backup.read_bytes(), self.original)
            self.assertEqual(bytes(self.boot.flash), result.prepared.data)

    def test_same_version_is_skipped_unless_forced(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "x9.bin"
            path.write_bytes(image_bytes())
            backup = directory / "backup.bin"

            with patch.multiple(
                firmware,
                bootloader_nodes=lambda: [],
                direct_mouse_nodes=lambda: [Path("/dev/hidraw-test")],
                direct_mouse_info=lambda _node: firmware.FirmwareInfo(
                    0x00710106, 0xEB02
                ),
                enter_bootloader=lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("must not enter bootloader for the same version")
                ),
            ):
                with self.assertRaises(DeviceError) as caught:
                    firmware.upgrade_mouse(
                        path,
                        expected_fwid=0x00710106,
                        backup_path=backup,
                    )
            self.assertIn("无需升级", str(caught.exception))
            self.assertFalse(backup.exists())

            @contextmanager
            def fake_open(_node):
                yield self.handle

            with patch.multiple(
                firmware,
                bootloader_nodes=lambda: [],
                direct_mouse_nodes=lambda: [Path("/dev/hidraw-test")],
                direct_mouse_info=lambda _node: firmware.FirmwareInfo(
                    0x00710106, 0xEB02
                ),
                enter_bootloader=lambda node, progress=None: True,
                wait_for_bootloader=lambda timeout=15.0, progress=None: Path(
                    "/dev/hidraw-test"
                ),
                open_device=fake_open,
                wait_for_application=lambda *args, **kwargs: firmware.FirmwareInfo(
                    0x00710106, 0xEB02
                ),
                is_bootloader_node=lambda _node: False,
            ):
                result = firmware.upgrade_mouse(
                    path,
                    expected_fwid=0x00710106,
                    backup_path=backup,
                    force=True,
                )
            self.assertTrue(result.confirmed)
            self.assertTrue(self.boot.app_started)

    def test_bootloader_without_a_model_is_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            path = directory / "x9.bin"
            path.write_bytes(image_bytes())
            backup = directory / "backup.bin"
            with patch.multiple(
                firmware,
                bootloader_nodes=lambda: [Path("/dev/hidraw-test")],
                open_device=lambda _node: (_ for _ in ()).throw(
                    AssertionError("must not open the bootloader")
                ),
            ):
                with self.assertRaises(DeviceError) as caught:
                    firmware.upgrade_mouse(path, backup_path=backup)
            self.assertIn("无法确认当前鼠标型号", str(caught.exception))
            self.assertFalse(backup.exists())

    def test_enter_bootloader_command(self):
        @contextmanager
        def fake_open(_node):
            yield self.handle

        with patch.object(firmware, "open_device", fake_open):
            self.assertTrue(firmware.enter_bootloader(Path("/dev/hidraw-test")))
        self.assertEqual(
            self.handle.writes[-1][:6],
            bytes.fromhex("0e 1e 55 aa 00 ff"),
        )

    def test_direct_mouse_info(self):
        @contextmanager
        def fake_open(_node):
            yield self.handle

        with patch.object(firmware, "open_device", fake_open):
            info = firmware.direct_mouse_info(Path("/dev/hidraw-test"))
        self.assertEqual(info.fwid, 0x00710106)
        self.assertEqual(info.version, 0xEB02)


class DetectionTest(unittest.TestCase):
    def test_bootloader_and_direct_mouse_nodes(self):
        class FakeSysfs:
            @staticmethod
            def glob(_pattern):
                return [Path("hidraw0"), Path("hidraw1")]

        def uevent(node):
            return [
                firmware.BOOTLOADER_HID_ID if node.name == "hidraw0"
                else firmware.DIRECT_MOUSE_HID_ID
            ]

        def descriptor(node):
            if node.name == "hidraw0":
                return bytes.fromhex("06 0e ff 09 01 a1 01")
            return bytes.fromhex("06 0f ff 09 01 85 0e a1 01")

        with patch.object(firmware, "HID_SYSFS", FakeSysfs()), \
                patch.object(firmware, "_valid_node", return_value=True), \
                patch.object(firmware, "_uevent", side_effect=uevent), \
                patch.object(firmware, "_report_descriptor", side_effect=descriptor):
            self.assertEqual(firmware.bootloader_nodes(), [Path("/dev/hidraw0")])
            self.assertEqual(firmware.direct_mouse_nodes(), [Path("/dev/hidraw1")])
            self.assertTrue(firmware.is_bootloader_node(Path("/dev/hidraw0")))
            self.assertFalse(firmware.is_bootloader_node(Path("/dev/hidraw1")))
            self.assertTrue(firmware.is_direct_mouse_node(Path("/dev/hidraw1")))
            self.assertFalse(firmware.is_direct_mouse_node(Path("/dev/hidraw0")))


class FirmwareAcquisitionTest(unittest.TestCase):
    @staticmethod
    def synthetic_firmware(fwid=0x00710106, version=0xEB02, size=0x2000):
        data = bytearray(size)
        data[0:4] = (0x20003000).to_bytes(4, "little")
        data[4:8] = (0x91).to_bytes(4, "little")
        data[0x10:0x14] = fwid.to_bytes(4, "little")
        data[0x14:0x16] = version.to_bytes(2, "big")
        data[0x18:0x1c] = b"BYKC"
        return bytes(data)

    def test_scan_and_fetch_from_app_exe(self):
        import gzip

        image = self.synthetic_firmware()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            app = directory / "INPHIC 英云.exe"
            app.write_bytes(
                b"MZ" + b"junk" * 100
                + gzip.compress(image)
                + gzip.compress(b"not a firmware" * 1000)
            )
            assets = firmware.scan_firmware_images(app)
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].fwid, 0x00710106)
            self.assertEqual(assets[0].version, 0xEB02)
            output = directory / "x9.bin"
            path = firmware.fetch_firmware(
                0x00710106,
                app_exe=app,
                output=output,
                cache_dir=directory / "cache",
            )
            self.assertEqual(path, output)
            parsed = firmware.parse_firmware(path)
            self.assertEqual(parsed.data, image)

    def test_select_firmware_accepts_same_model_variant(self):
        assets = [
            firmware.FirmwareAsset(0x00710208, 0xE302, b"\0" * 16),
            firmware.FirmwareAsset(0x00710106, 0xEB02, b"\1" * 16),
        ]
        self.assertEqual(
            firmware.select_firmware(assets, 0x00710207).fwid, 0x00710208
        )
        self.assertEqual(
            firmware.select_firmware(assets, 0x00710106).fwid, 0x00710106
        )
        with self.assertRaises(DeviceError):
            firmware.select_firmware(assets, 0xDEADBEEF)

    def test_download_installer_accepts_file_url(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "INPHIC YU_SetupV2.2.11.exe"
            source.write_bytes(b"MZ" + b"\0" * 2048)
            path = firmware.download_installer(
                source.as_uri(), cache_dir=directory / "cache"
            )
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), source.read_bytes())

    def test_fetch_from_installer_uses_extracted_app(self):
        import gzip

        image = self.synthetic_firmware()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            installer = directory / "setup.exe"
            installer.write_bytes(b"MZ" + b"\0" * 2048)
            app = directory / "app.exe"
            app.write_bytes(b"MZ" + gzip.compress(image))
            output = directory / "x9.bin"
            with patch.object(
                firmware, "extract_app_exe", return_value=app
            ):
                path = firmware.fetch_firmware(
                    0x00710106,
                    installer=installer,
                    output=output,
                    cache_dir=directory / "cache",
                )
            self.assertEqual(firmware.parse_firmware(path).data, image)

    def test_modern_system_innoextract_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            tool = directory / "innoextract"
            tool.write_text(
                "#!/bin/sh\n"
                "echo 'innoextract 1.10-dev'\n"
                "echo 'Extracts installers created by Inno Setup 1.2.10 to 6.3.3'\n"
            )
            tool.chmod(0o755)
            with patch.object(firmware.shutil, "which", return_value=str(tool)):
                found = firmware.ensure_innoextract(
                    cache_dir=directory / "cache"
                )
            self.assertEqual(found, tool)


class CliTest(unittest.TestCase):
    def test_parser_accepts_upgrade_and_firmware(self):
        from open_inphic.hid import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "upgrade", "--file", "x9.bin", "--yes", "--force"
        ])
        self.assertEqual(args.command, "upgrade")
        self.assertEqual(args.file, Path("x9.bin"))
        self.assertTrue(args.yes)
        self.assertTrue(args.force)
        args = parser.parse_args(["firmware"])
        self.assertEqual(args.command, "firmware")

    def test_print_firmware_matches_vendor_format(self):
        state = DeviceState(
            0x0902, True, 100, False, 0xEB02,
            Settings(0, (2,) * 4, (3,) * 4, (0, 3, 3, 3)),
            bytes.fromhex("0e 1e 2e 3e 5e fe ff ff"),
            {code: 100 + 50 * code for code in range(255)},
            mouse_fwid=0x00710106,
            receiver_fwid=0x01660004,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            print_firmware(state)
        text = output.getvalue()
        self.assertIn("V0166.0004.0902", text)
        self.assertIn("V0071.0106.EB02", text)

    def test_parser_accepts_fetch_and_download(self):
        from open_inphic.hid import build_parser

        parser = build_parser()
        args = parser.parse_args(["fetch-firmware", "--fwid", "0x00710106"])
        self.assertEqual(args.command, "fetch-firmware")
        self.assertEqual(args.fwid, 0x00710106)
        args = parser.parse_args([
            "upgrade", "--download", "--dry-run", "--fwid", "0x00710106"
        ])
        self.assertTrue(args.download)
        self.assertTrue(args.dry_run)
        self.assertIsNone(args.file)

    def test_fetch_command_saves_firmware(self):
        from open_inphic.hid import fetch_command, build_parser

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            path.write_bytes(image_bytes())
            args = build_parser().parse_args([
                "fetch-firmware", "--fwid", "0x00710106"
            ])
            output = io.StringIO()
            with patch(
                "open_inphic.hid._resolve_fwid", return_value=0x00710106
            ), patch(
                "open_inphic.firmware.fetch_firmware", return_value=path
            ):
                with redirect_stdout(output):
                    code = fetch_command(args)
        self.assertEqual(code, 0)
        self.assertIn("固件已保存", output.getvalue())

    def test_upgrade_download_dry_run_uses_fetched_file(self):
        from open_inphic.hid import upgrade_command, build_parser

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            path.write_bytes(image_bytes())
            args = build_parser().parse_args([
                "upgrade", "--download", "--dry-run", "--fwid", "0x00710106"
            ])
            output = io.StringIO()
            with patch(
                "open_inphic.hid._receiver_fwid", return_value=None
            ), patch(
                "open_inphic.hid._current_firmware", return_value=None
            ), patch(
                "open_inphic.firmware.fetch_firmware", return_value=path
            ):
                with redirect_stdout(output):
                    code = upgrade_command(args)
        self.assertEqual(code, 0)
        self.assertIn("dry-run", output.getvalue())
        self.assertIn("V0071.0106.EB02", output.getvalue())

    def test_same_version_is_skipped_without_force(self):
        from open_inphic.hid import upgrade_command, build_parser

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            path.write_bytes(image_bytes())
            args = build_parser().parse_args([
                "upgrade", "--file", str(path), "--yes"
            ])
            output = io.StringIO()
            with patch(
                "open_inphic.hid._receiver_fwid", return_value=None
            ), patch(
                "open_inphic.hid._current_firmware", return_value=firmware.FirmwareInfo(
                    0x00710106, 0xEB02
                )
            ), patch(
                "open_inphic.firmware.upgrade_mouse",
                side_effect=AssertionError("must not write"),
            ):
                with redirect_stdout(output):
                    code = upgrade_command(args)
        self.assertEqual(code, 0)
        self.assertIn("无需升级", output.getvalue())

    def test_dry_run_command_does_not_need_hardware(self):
        from open_inphic.hid import upgrade_command, build_parser

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            path.write_bytes(image_bytes())
            args = build_parser().parse_args([
                "upgrade", "--file", str(path), "--dry-run"
            ])
            output = io.StringIO()
            with patch(
                "open_inphic.hid._receiver_fwid", return_value=None
            ), patch(
                "open_inphic.hid._current_firmware", return_value=None
            ):
                with redirect_stdout(output):
                    code = upgrade_command(args)
        self.assertEqual(code, 0)
        self.assertIn("dry-run", output.getvalue())
        self.assertIn("V0071.0106.EB02", output.getvalue())


if __name__ == "__main__":
    unittest.main()
