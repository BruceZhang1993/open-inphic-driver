from collections import deque
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from open_inphic import firmware, hid
from open_inphic.hid import (
    ADVANCE_FLAGS,
    EMPTY_DPI,
    LIFT_LABELS,
    MODELS,
    RECEIVERS,
    WIRELESS_DEEP_SLEEP_MINUTES,
    WIRELESS_SLEEP_SECONDS,
    AdvanceSetting,
    DeviceError,
    Led,
    advance_from_report,
    advance_request,
    codes_request,
    decode_dpi_table,
    led_request,
    levels_request,
    mode_request,
    model_name,
    pairing_from_report,
    rates_request,
    settings_from_report,
    Settings,
)


def settings_report(led=bytes([0x41, 0x00, 0x13, 0x07]), advance=bytes([0x12, 0x12, 0x01, 0x00])):
    return bytes([0x0E, 0x60, 0x00, 0x83, 0xC1, 0xF2, 0xF2, 0xF2]) + led + advance + bytes(16)


class ConnectionTest(unittest.TestCase):
    def make_node(self, root, name, hid_id, hid_name, descriptor):
        device = root / name / "device"
        device.mkdir(parents=True)
        (device / "uevent").write_text(f"HID_ID={hid_id}\nHID_NAME={hid_name}\n")
        (device / "report_descriptor").write_bytes(bytes.fromhex(descriptor))
        return device

    def test_wired_status_and_usb_rate_use_direct_report_protocol(self):
        model = ("0e 21" + " 00" * 12 + " 00 71 01 06 eb 02")
        settings = settings_report().hex(" ").replace("0e 60", "0e 20", 1)
        dpi = "0e 22 10 0e 1e 2e 3e 5e fe ff ff 02"
        table = "0e 23 32 00 ff 00 64 00 00 01"
        state_packets = [("0e 21", model), ("0e 20", settings),
                         ("0e 22", dpi), ("0e 23", table)]
        after = settings.replace("c1 f2 f2 f2", "81 f2 f2 f2", 1)
        operations = deque([*state_packets, *state_packets,
                            ("0e 20 00 f0 80 f0 f0 f0", after), ("0e 20", after)])
        pending = deque()
        handle = Mock()

        def write(report):
            request, reply = operations.popleft()
            self.assertEqual(report, bytes.fromhex(request).ljust(32, b"\0"))
            pending.append(bytes.fromhex(reply).ljust(32, b"\0"))
            return len(report)

        handle.write.side_effect = write
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_node(root, "hidraw6", "0003:000032C2:00000024", "Wired mouse",
                           "06 0f ff 09 01 a1 01 85 0e c0")
            output = StringIO()
            with (patch.object(hid, "HID_SYSFS", root), patch.object(firmware, "HID_SYSFS", root),
                  patch.object(firmware, "_valid_node", side_effect=lambda node: node.name == "hidraw6"),
                  patch.object(firmware, "open_device", return_value=nullcontext(handle)),
                  patch.object(hid.select, "select", side_effect=lambda readers, *_: (
                      readers if pending else [], [], [])),
                  patch.object(hid.os, "read", side_effect=lambda *_: pending.popleft()),
                  redirect_stdout(output)):
                self.assertEqual(hid.main(["status"]), 0)
                self.assertEqual(hid.main(["--device", "/dev/hidraw6", "set-usb-rate", "500"]), 0)
        self.assertFalse(operations)
        self.assertIn("USB 有线", output.getvalue())
        self.assertIn("V0071.0106.EB02", output.getvalue())
        self.assertIn("2400 DPI", output.getvalue())
        self.assertIn("设置已回读确认", output.getvalue())

    def test_bluetooth_status_is_read_only_and_reports_kernel_battery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            descriptor = "05 01 09 02 a1 01 c0"
            device = self.make_node(root, "hidraw5", "0005:00000000:00000000",
                                    "INPHIC_BT", descriptor)
            self.make_node(root, "hidraw6", "0005:00000000:00000000", "Another mouse", descriptor)
            self.make_node(root, "hidraw7", "0005:000032C2:00000024", "INPHIC keyboard",
                           "05 01 09 06 a1 01 c0")
            capacity = device / "power_supply" / "hid-battery" / "capacity"
            capacity.parent.mkdir(parents=True)
            capacity.write_text("73\n")
            output, errors = StringIO(), StringIO()
            with (patch.object(hid, "HID_SYSFS", root), patch.object(firmware, "HID_SYSFS", root),
                  patch.object(firmware, "open_device") as open_device,
                  redirect_stdout(output), redirect_stderr(errors)):
                self.assertEqual(hid.main(["status"]), 0)
                self.assertEqual(hid.main(["--connection", "bluetooth", "set-dpi", "800"]), 1)
                self.assertEqual(hid.main(["--connection", "bluetooth", "firmware"]), 1)
                self.assertEqual(hid.main(["--connection", "bluetooth", "upgrade", "--file", "x9.bin"]), 1)
                self.assertEqual(hid.main(["--device", "/dev/hidraw6", "status"]), 0)
                open_device.assert_not_called()
        self.assertIn("INPHIC_BT · 已连接", output.getvalue())
        self.assertIn("电量 73%", output.getvalue())
        self.assertIn("Another mouse · 已连接", output.getvalue())
        self.assertIn(hid.BLUETOOTH_READ_ONLY, errors.getvalue())

    def test_bluetooth_status_detects_inphicmouse_and_reads_bluez_battery(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            device = self.make_node(root, "hidraw6", "0005:00000000:00000000", "inphicMouse",
                                    "05 01 09 02 a1 01 c0")
            with (device / "uevent").open("a") as properties:
                properties.write("HID_UNIQ=02:01:02:03:04:05\n")
            adapters = root / "bluetooth"
            (adapters / "hci0").mkdir(parents=True)
            (adapters / "hci0:3585").mkdir()
            output = StringIO()
            reply = Mock(returncode=0, stdout="y 100\n")
            with (patch.object(hid, "HID_SYSFS", root), patch.object(hid, "BLUETOOTH_SYSFS", adapters),
                  patch.object(firmware, "HID_SYSFS", root),
                  patch.object(hid.subprocess, "run", return_value=reply) as busctl,
                  redirect_stdout(output)):
                self.assertEqual(hid.main(["status"]), 0)
                busctl.assert_called_once()
                self.assertEqual(busctl.call_args.args[0][-3:], [
                    "/org/bluez/hci0/dev_02_01_02_03_04_05", "org.bluez.Battery1", "Percentage",
                ])
            self.assertIn("inphicMouse · 已连接", output.getvalue())
            self.assertIn("电量 100%", output.getvalue())


class DecodeTest(unittest.TestCase):
    def test_settings_from_report_matches_live_capture(self):
        settings = settings_from_report(settings_report())
        self.assertEqual(settings.mode, 0)
        self.assertEqual(settings.levels, (1, 2, 2, 2))
        self.assertEqual(settings.usb_rates, (3, 3, 3, 3))
        self.assertEqual(settings.wireless_rates, (0, 3, 3, 3))
        self.assertEqual(settings.led, Led(1, 0, 3, 1, 7))

    def test_advance_from_report_matches_live_capture(self):
        advance = advance_from_report(settings_report())
        self.assertEqual(advance.wireless_sleep, 2)
        self.assertEqual(advance.wireless_deep, 1)
        self.assertEqual(advance.bluetooth_sleep, 2)
        self.assertEqual(advance.bluetooth_deep, 1)
        self.assertEqual(advance.lift, 1)
        self.assertFalse(advance.ultra_low_delay)
        self.assertFalse(advance.line_correct)

    def test_advance_flags_decode_individually(self):
        for offset, (name, mask, _) in enumerate(ADVANCE_FLAGS):
            advance = advance_from_report(settings_report(advance=bytes([0x12, 0x12, 0x01, 0x00])))
            self.assertFalse(getattr(advance, name))
            report = bytearray(settings_report(advance=bytes([0x12, 0x12, mask, 0x00])))
            self.assertTrue(getattr(advance_from_report(bytes(report)), name))
        advance = advance_from_report(settings_report(advance=bytes([0x12, 0x12, 0x01, 0x01])))
        self.assertTrue(advance.line_correct)

    def test_led_decode_masks_high_bits(self):
        led = Led(0x25, 1, 3, 2, 5)
        decoded = settings_from_report(settings_report(led=bytes([0xE5, 0x00, 0xB3, 0x8D]))).led
        self.assertEqual(decoded, Led(5, 1, 3, 3, 5))

    def test_pairing_from_report(self):
        report = bytes.fromhex("0e 2d ed 1b cb 8d eb 06 71 00 06 01 04 02") + bytes(18)
        pairing = pairing_from_report(report)
        self.assertEqual(pairing.device_id, 0x8DCB1BED)
        self.assertEqual(pairing.firmware_id, 0x00710106)
        self.assertEqual(pairing.version, 0x0402)


class EncodeTest(unittest.TestCase):
    def test_led_request_matches_capture(self):
        self.assertEqual(
            led_request(Led(5, 0, 2, 2, 3)).hex(" "),
            "0e 60 00 00 00 00 00 00 c5 00 aa 0b 00 00 00 00",
        )

    def test_led_request_always_sets_high_nibble(self):
        # The receiver reports 0x40 back regardless, so the request must carry 0xc0.
        self.assertEqual(led_request(Led(1, 0, 3, 1, 7))[8], 0xC1)

    def test_advance_request_matches_capture(self):
        advance = AdvanceSetting(0, 1, 2, 1, 1)
        self.assertEqual(
            advance_request(advance).hex(" "),
            "0e 60 40 00 00 00 00 00 00 00 00 00 10 12 01 00",
        )

    def test_advance_request_sets_flags_and_line_correct(self):
        advance = AdvanceSetting(0, 0, 0, 0, 0, True, True, True, True, True)
        report = advance_request(advance)
        self.assertEqual(report[14], 0xF0)
        self.assertEqual(report[15], 0x01)

    def test_levels_and_rates_requests(self):
        self.assertEqual(levels_request((1, 2, 2, 2)).hex(" "),
                         "0e 60 00 0f 01 02 02 02 00 00 00 00 00 00 00 00")
        settings = Settings(0, (1, 2, 2, 2), (3, 3, 3, 3), (0, 3, 3, 3))
        self.assertEqual(rates_request(settings).hex(" "),
                         "0e 60 00 f0 c0 f0 f0 f0 00 00 00 00 00 00 00 00")

    def test_mode_and_codes_requests(self):
        self.assertEqual(mode_request(2).hex(" "), "0e 60 22 00 00 00 00 00 00 00 00 00 00 00 00 00")
        self.assertEqual(codes_request(bytes([0x0E, 0x1E, 0x2E, 0x3E, 0x5E, 0xFE, 0x70, 0x80])).hex(" "),
                         "0e 62 80 0e 1e 2e 3e 5e fe 70 80 00 00 00 00 00")

    def test_requests_round_trip_through_settings(self):
        settings = Settings(3, (5, 2, 2, 2), (1, 3, 3, 3), (2, 3, 3, 3), Led(2, 1, 1, 0, 4),
                            AdvanceSetting(3, 5, 1, 2, 3, True, False, True, False, True))
        report = bytearray(settings_report())
        report[2] = 0x20 | settings.mode
        report[4:8] = rates_request(settings)[4:8]
        report[8:12] = led_request(settings.led)[8:12]
        report[12:16] = advance_request(settings.advance)[12:16]
        decoded = settings_from_report(bytes(report))
        self.assertEqual(decoded.mode, settings.mode)
        self.assertEqual(decoded.usb_rates, settings.usb_rates)
        self.assertEqual(decoded.wireless_rates, settings.wireless_rates)
        self.assertEqual(decoded.led, settings.led)
        self.assertEqual(decoded.advance, settings.advance)


class TablesTest(unittest.TestCase):
    def test_models_cover_x9(self):
        self.assertEqual(model_name(0x00710106), "X9")
        self.assertIn(0x01660004, RECEIVERS)
        self.assertIsNone(model_name(0xDEADBEEF))

    def test_sleep_tables_match_vendor_ui(self):
        self.assertEqual(len(WIRELESS_SLEEP_SECONDS), 7)
        self.assertEqual(len(WIRELESS_DEEP_SLEEP_MINUTES), 7)
        self.assertEqual(WIRELESS_SLEEP_SECONDS[2], 60)
        self.assertEqual(WIRELESS_DEEP_SLEEP_MINUTES[1], 10)
        self.assertEqual(LIFT_LABELS[1], "2 MM")

    def test_dpi_table_types(self):
        kind2 = bytes.fromhex("0e 63 32 00 ff 00 64 00 00 01") + bytes(22)
        table = decode_dpi_table(2, kind2)
        self.assertEqual(table[0x00], 100)
        self.assertEqual(table[0x2E], 2400)
        kind1 = bytes.fromhex("0e 63 60 09 30 20 03 00 00")
        self.assertEqual(decode_dpi_table(1, kind1 + bytes(24)), {0x30: 2400, 0x00: 800})
        with self.assertRaises(DeviceError):
            decode_dpi_table(3, kind1)


class ScopedVerificationTest(unittest.TestCase):
    """只有请求改动过的字段参与回读校验。

    接收器处理连发写入时有可见延迟，未触及的字段可能仍读到旧值。若校验要求整个
    Settings 全等，正常的写入也会被误判失败并触发回滚。
    """

    class FakeHandle:
        def __init__(self):
            self.reports = []

        def write(self, data):
            self.reports.append(bytes(data))
            return len(data)

    def mouse(self, settings):
        from open_inphic.hid import Mouse

        mouse = Mouse(self.FakeHandle())
        mouse._settings = lambda: settings
        return mouse

    def test_level_change_tolerates_other_fields_lagging(self):
        original = Settings(0, (2, 2, 2, 2), (3, 3, 3, 3), (0, 3, 3, 3))
        target = replace(original, levels=(0, 2, 2, 2))
        lagging = replace(target, led=Led(5, 0, 0, 0, 7),
                          advance=AdvanceSetting(0, 0, 0, 0, 0))
        mouse = self.mouse(lagging)
        self.assertEqual(mouse._field("levels"), target.levels)
        self.assertNotEqual(mouse._field("led"), original.led)

    def test_scoped_fields_read_current_report(self):
        settings = Settings(0, (1, 2, 2, 2), (3, 3, 3, 3), (0, 3, 3, 3))
        mouse = self.mouse(settings)
        self.assertEqual(mouse._field("levels"), (1, 2, 2, 2))
        self.assertEqual(mouse._field("wireless_rates"), (0, 3, 3, 3))
        self.assertEqual(mouse._field("led"), settings.led)


if __name__ == "__main__":
    unittest.main()
