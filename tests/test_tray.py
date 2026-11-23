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
from open_inphic.hid import DeviceState, PowerStatus, RATES, Settings
from open_inphic.tray import Tray, battery_icon


class FakeMouse:
    def __init__(self):
        self.current = DeviceState(
            0x0902, True, 100, False, 0xEB02,
            Settings(0, (2,) * 4, (3,) * 4, (0, 3, 3, 3)),
            bytes.fromhex("0e 1e 2e 3e 5e fe ff ff"),
            {code: 100 + 50 * code for code in range(255)},
            mouse_fwid=0x00710106,
            receiver_fwid=0x01660004,
        )
        self.full_reads = 0
        self.power_reads = 0

    def state(self, power=None):
        self.full_reads += 1
        return self.current

    def power_status(self):
        self.power_reads += 1
        return PowerStatus(self.current.online, self.current.battery, self.current.charging)

    def set_slot(self, slot):
        settings = replace(self.current.settings, levels=(slot - 1, 2, 2, 2))
        self.current = replace(self.current, settings=settings)

    def set_rate(self, rate):
        settings = replace(self.current.settings, wireless_rates=(RATES.index(rate), 3, 3, 3))
        self.current = replace(self.current, settings=settings)


class TrayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        QQuickStyle.setStyle("Material")

    def setUp(self):
        self.mouse = FakeMouse()

        @contextmanager
        def fake_connection(_node, _connection):
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

    def test_close_reopen_and_exit_from_tray(self):
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.property("allowExit"))
        self.tray.open_window()
        self.settle()
        self.assertTrue(self.window.isVisible())

        self.tray.slot_actions[0].trigger()
        self.settle()
        self.assertTrue(self.tray.slot_actions[0].isChecked())
        self.tray.rate_actions[1].trigger()
        self.settle()
        self.assertTrue(self.tray.rate_actions[1].isChecked())

        self.tray.menu.actions()[-1].trigger()
        self.assertTrue(self.window.property("allowExit"))
        self.window.close()
        self.assertFalse(self.window.isVisible())

    def test_idle_power_poll_keeps_editor_and_recovers_after_sleep(self):
        field = self.window.findChild(QObject, "dpiValue")
        self.assertEqual(field.property("text"), "2400")
        field.setProperty("text", "2450")
        self.mouse.current = replace(self.mouse.current, battery=17, charging=True)
        self.controller.poll()
        self.settle()
        self.assertEqual(self.mouse.full_reads, 1)
        self.assertEqual(self.mouse.power_reads, 1)
        self.assertEqual(field.property("text"), "2450")
        self.assertIn("17% · 充电中", self.tray.icon.toolTip())
        self.assertIn("2400 DPI", self.tray.icon.toolTip())
        self.assertIn("V0071.0106.EB02", self.tray.icon.toolTip())
        self.assertIn("V0166.0004.0902", self.tray.icon.toolTip())
        self.assertNotEqual(battery_icon(17, False).pixmap(32).toImage(),
                            battery_icon(17, True).pixmap(32).toImage())

        awake = self.mouse.current
        self.mouse.current = replace(awake, online=False, settings=None)
        self.controller.poll()
        self.settle()
        self.assertIn("鼠标离线", self.tray.icon.toolTip())
        self.assertFalse(self.tray.slots_menu.isEnabled())
        self.assertEqual(self.mouse.full_reads, 1)

        self.mouse.current = awake
        self.controller.poll()
        self.settle()
        self.assertEqual(self.mouse.full_reads, 2)
        self.assertTrue(self.tray.slots_menu.isEnabled())


if __name__ == "__main__":
    unittest.main()
