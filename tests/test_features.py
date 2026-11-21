from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtQuick import QQuickWindow
from PyQt6.QtQuickControls2 import QQuickStyle
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from open_inphic.gui import Controller
from open_inphic.hid import (
    AdvanceSetting,
    DeviceState,
    Led,
    PowerStatus,
    RATES,
    Settings,
)
from open_inphic.tray import Tray


class FakeMouse:
    def __init__(self):
        self.current = DeviceState(
            0x0902, True, 100, False, 0xEB02,
            Settings(0, (2,) * 4, (3,) * 4, (0, 3, 3, 3), Led(1, 0, 3, 1, 7),
                     AdvanceSetting(2, 1, 2, 1, 1)),
            bytes.fromhex("0e 1e 2e 3e 5e fe ff ff"),
            {code: 100 + 50 * code for code in range(255)},
            mouse_fwid=0x00710106,
            receiver_fwid=0x01660004,
        )
        self.full_reads = 0
        self.power_reads = 0
        self.calls = []

    def state(self, power=None):
        self.full_reads += 1
        return self.current

    def power_status(self):
        self.power_reads += 1
        return PowerStatus(self.current.online, self.current.battery, self.current.charging)

    @property
    def settings(self):
        return self.current.settings

    def _update(self, **changes):
        self.current = replace(self.current, settings=replace(self.settings, **changes))

    def set_slot(self, slot, mode=None):
        self.calls.append(("slot", slot, mode))
        mode = self.settings.mode if mode is None else mode
        levels = list(self.settings.levels)
        levels[mode] = slot - 1
        self._update(levels=tuple(levels))

    def set_dpi(self, dpi, slot=None):
        self.calls.append(("dpi", dpi, slot))
        table = {code: 100 + 50 * code for code in range(255)}
        code = next(code for code, value in table.items() if value == dpi)
        slot = (self.settings.levels[self.settings.mode] + 1) if slot is None else slot
        codes = bytearray(self.current.dpi_codes)
        codes[slot - 1] = code
        self.current = replace(self.current, dpi_codes=bytes(codes),
                               dpi_table={code: 100 + 50 * code for code in range(255)})

    def set_rate(self, rate, mode=None):
        self.calls.append(("rate", rate, mode))
        mode = self.settings.mode if mode is None else mode
        rates = list(self.settings.wireless_rates)
        rates[mode] = RATES.index(rate)
        self._update(wireless_rates=tuple(rates))

    def set_usb_rate(self, rate, mode=None):
        self.calls.append(("usb_rate", rate, mode))
        mode = self.settings.mode if mode is None else mode
        rates = list(self.settings.usb_rates)
        rates[mode] = RATES.index(rate)
        self._update(usb_rates=tuple(rates))

    def set_mode(self, mode):
        self.calls.append(("mode", mode))
        self._update(mode=mode)

    def set_led(self, mode=None, brightness=None, speed=None, direction=None):
        self.calls.append(("led", mode, brightness, speed, direction))
        changes = {name: value for name, value in
                   (("mode", mode), ("brightness", brightness),
                    ("speed", speed), ("direction", direction)) if value is not None}
        self._update(led=replace(self.settings.led, **changes))

    def set_advance(self, **changes):
        self.calls.append(("advance", tuple(sorted(changes.items()))))
        self._update(advance=replace(self.settings.advance, **changes))


class ControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        QQuickStyle.setStyle("Material")

    def setUp(self):
        self.mouse = FakeMouse()

        @contextmanager
        def fake_connection(_node):
            yield self.mouse

        connection = patch("open_inphic.gui.open_mouse", fake_connection)
        connection.start()
        self.addCleanup(connection.stop)
        availability = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True)
        availability.start()
        self.addCleanup(availability.stop)
        self.controller = Controller(None)
        self.addCleanup(self.controller.executor.shutdown)
        self.engine = QQmlApplicationEngine()
        self.engine.setInitialProperties({"backend": self.controller})
        qml = Path(__file__).resolve().parents[1] / "open_inphic" / "ui" / "Main.qml"
        self.engine.load(QUrl.fromLocalFile(str(qml)))
        self.window, = self.engine.rootObjects()
        self.assertIsInstance(self.window, QQuickWindow)
        self.tray = Tray(self.app, self.window, self.controller)
        self.addCleanup(self.tray.deleteLater)
        self.addCleanup(self.window.hide)
        self.settle()

    def settle(self):
        deadline = time.monotonic() + 2
        while self.controller.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(self.controller.busy)

    def field(self, name):
        return self.window.findChild(QObject, name)

    def test_device_description_exposes_new_fields(self):
        device = self.controller.device
        self.assertEqual(device["model"], "X9")
        self.assertEqual(device["receiver"], "BYKC 6219 Dongle")
        self.assertEqual(device["firmware"], "V0071.0106.EB02")
        self.assertEqual(device["receiverFirmware"], "V0166.0004.0902")
        self.assertEqual(
            device["receiverSummary"], "BYKC 6219 Dongle · V0166.0004.0902"
        )
        self.assertEqual(device["ledEffects"], [1, 2, 3, 4, 5])
        self.assertEqual(device["ledEffectLabels"][0], "关闭灯光")
        self.assertEqual(device["ledEffectIndex"], 0)
        self.assertEqual(device["ledBrightness"], 3)
        self.assertEqual(device["usbRateIndex"], 3)
        self.assertEqual(device["sleepIndex"], 2)
        self.assertEqual(device["deepSleepIndex"], 1)
        self.assertEqual(device["liftIndex"], 1)
        self.assertEqual(len(device["advanceFlags"]), 5)
        self.assertEqual([flag["key"] for flag in device["advanceFlags"]],
                         ["ultra_low_delay", "ultra_low_power", "ripple_control",
                          "move_wake_up", "line_correct"])

    def test_mode_switch_and_per_mode_rate(self):
        self.controller.set_mode(2)
        self.settle()
        self.assertEqual(self.controller.device["mode"], "3")
        self.assertEqual(self.controller.device["modeIndex"], 2)
        self.controller.set_rate(1)
        self.settle()
        self.assertEqual(self.controller.device["rateIndex"], 1)
        self.assertEqual(self.controller.state.settings.wireless_rates, (0, 3, 1, 3))
        self.controller.set_usb_rate(0)
        self.settle()
        self.assertEqual(self.controller.device["usbRateIndex"], 0)
        self.assertEqual(self.controller.state.settings.usb_rates, (3, 3, 0, 3))

    def test_led_apply_writes_effect_and_levels_together(self):
        # 界面索引 2 对应厂商灯效编号 3（呼吸模式）；「应用」必须同时写入模式，
        # 否则用户选了呼吸模式、界面却仍显示关闭灯光。
        self.controller.apply_led(2, 1, 3)
        self.settle()
        led = self.mouse.settings.led
        self.assertEqual(led.mode, 3)
        self.assertEqual(led.brightness, 1)
        self.assertEqual(led.speed, 0)  # 界面速度 3（最快）是协议 0
        self.assertEqual(self.controller.device["ledEffectIndex"], 2)
        self.assertEqual(self.controller.device["ledEffectLabels"][2], "呼吸模式")

        self.controller.apply_led(0, 3, 2)
        self.settle()
        self.assertEqual(self.mouse.settings.led.mode, 1)
        self.assertEqual(self.mouse.settings.led.speed, 1)  # 界面 2 是协议 1
        self.assertEqual(self.controller.device["ledEffectIndex"], 0)

    def test_sleep_lift_and_flags(self):
        self.controller.set_sleep(0, 6)
        self.settle()
        self.assertEqual(self.mouse.settings.advance.wireless_sleep, 0)
        self.assertEqual(self.mouse.settings.advance.wireless_deep, 6)
        self.controller.set_lift(3)
        self.settle()
        self.assertEqual(self.mouse.settings.advance.lift, 3)
        self.controller.set_flag("ripple_control", True)
        self.settle()
        self.assertTrue(self.mouse.settings.advance.ripple_control)
        self.controller.set_flag("not_a_flag", True)
        self.settle()
        self.assertEqual(self.mouse.calls[-1][0], "advance")

    def test_tray_menus_follow_device_state(self):
        self.assertEqual(len(self.tray.mode_actions), 4)
        self.assertTrue(self.tray.mode_actions[0].isChecked())
        self.tray.mode_actions[3].trigger()
        self.settle()
        self.assertTrue(self.tray.mode_actions[3].isChecked())
        self.assertEqual(self.controller.device["mode"], "4")
        self.assertTrue(self.tray.usb_rates_menu.isEnabled())
        self.assertEqual(len(self.tray.effects_menu.actions()), 5)
        self.assertIn("灯效", self.tray.icon.toolTip())

    def test_offline_device_disables_new_controls(self):
        self.mouse.current = replace(self.mouse.current, online=False, settings=None)
        self.controller.poll()
        self.settle()
        self.assertFalse(self.tray.modes_menu.isEnabled())
        self.assertFalse(self.tray.effects_menu.isEnabled())
        self.assertEqual(self.controller.device["mode"], "—")


if __name__ == "__main__":
    unittest.main()
