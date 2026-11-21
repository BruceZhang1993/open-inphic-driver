"""GUI wiring checks: controls exist, dispatch, gate on connection state, and theme."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtQuickControls2 import QQuickStyle
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from open_inphic import firmware
from open_inphic.gui import Controller
from open_inphic.hid import RATES

from test_features import FakeMouse

BUTTONS = ["refresh", "applyMode", "applySlot", "applyDpi", "applyRate",
           "applyUsbRate", "applyLed", "applySleep"]
FIRMWARE_BUTTONS = ["chooseFirmware", "downloadFirmware", "applyFirmware"]
COMBOS = ["configMode", "dpiSlot", "pollingRate", "usbPollingRate", "ledEffect",
          "ledBrightness", "ledSpeed", "sleepTime", "deepSleepTime", "liftHeight"]
FIELDS = ["dpiValue", "operationStatus"]
SWITCHES = ["flag_ultra_low_delay", "flag_ultra_low_power", "flag_ripple_control",
            "flag_move_wake_up", "flag_line_correct"]
PROGRESS = ["upgradeProgress"]
CHECKS = ["forceUpgrade"]


class UiTest(unittest.TestCase):
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

        patch("open_inphic.gui.open_mouse", fake_connection).start()
        self.addCleanup(patch.stopall)
        availability = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True)
        availability.start()
        self.controller = Controller(None)
        self.addCleanup(self.controller.executor.shutdown)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda warnings: self.warnings.extend(
            str(warning.toString()) for warning in warnings))
        self.engine.setInitialProperties({"backend": self.controller})
        qml = Path(__file__).resolve().parents[1] / "open_inphic" / "ui" / "Main.qml"
        self.engine.load(QUrl.fromLocalFile(str(qml)))
        self.window, = self.engine.rootObjects()
        self.settle()

    def settle(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.controller.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertFalse(self.controller.busy)

    def open_window(self, width=None):
        """Load a separate window, optionally created at the given width.

        Resizing one window repeatedly is unreliable without a compositor:
        nested Qt Quick layouts can keep the geometry of the previous width.
        Creating each window at its final size exercises the real layout.
        """
        engine = QQmlApplicationEngine()
        properties = {"backend": self.controller}
        if width is not None:
            properties["width"] = width
        engine.setInitialProperties(properties)
        qml = Path(__file__).resolve().parents[1] / "open_inphic" / "ui" / "Main.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        self.assertFalse(self.warnings, self.warnings)
        window, = engine.rootObjects()
        self.addCleanup(window.setVisible, False)
        self.addCleanup(engine.deleteLater)
        deadline = time.monotonic() + 3.0
        while self.controller.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        return window

    def item(self, name):
        item = self.window.findChild(QObject, name)
        self.assertIsNotNone(item, f"缺少控件 {name}")
        return item

    def click(self, name):
        button = self.item(name)
        self.assertTrue(button.property("enabled"), f"{name} 应可用")
        button.metaObject().invokeMethod(button, "clicked")
        self.settle()

    def select(self, name, index):
        combo = self.item(name)
        combo.setProperty("currentIndex", index)
        self.app.processEvents()

    def test_qml_loads_without_warnings(self):
        self.assertEqual(self.warnings, [])

    def test_every_control_exists(self):
        for name in (
            BUTTONS + FIRMWARE_BUTTONS + COMBOS + FIELDS + SWITCHES + PROGRESS
            + CHECKS
        ):
            self.item(name)
        self.item("scroll")
        self.item("upgradeDialog")

    def test_no_horizontal_overflow_at_minimum_width(self):
        names = (
            BUTTONS + FIRMWARE_BUTTONS + COMBOS + FIELDS + SWITCHES + PROGRESS
            + CHECKS + ["scroll"]
        )
        for width in (640, 520, 448):
            window = self.open_window(width)
            self.assertEqual(window.width(), width)
            for name in names:
                item = window.findChild(QObject, name)
                self.assertIsNotNone(item, f"缺少控件 {name}")
                pos = item.mapToItem(window.contentItem(), QPointF(0, 0))
                right = pos.x() + item.property("width")
                self.assertLessEqual(right, width + 0.5, f"{name} 在 {width}px 下超出窗口")

    def test_led_panel_controls_share_one_action_column(self):
        widths = {name: self.item(name).property("width")
                  for name in ("ledEffect", "ledBrightness", "ledSpeed")}
        self.assertEqual(len(set(widths.values())), 1, widths)
        sleeps = {self.item(name).property("width")
                  for name in ("sleepTime", "deepSleepTime")}
        self.assertEqual(len(sleeps), 1)
        self.assertGreater(self.item("applyLed").property("width"), 0)

    def test_apply_buttons_dispatch_to_the_controller(self):
        self.select("configMode", 2)
        self.click("applyMode")
        self.assertEqual(self.controller.device["mode"], "3")

        self.select("dpiSlot", 4)
        self.click("applySlot")
        self.assertEqual(self.mouse.calls[-1][0], "slot")

        self.item("dpiValue").setProperty("text", "3200")
        self.click("applyDpi")
        self.assertEqual(self.mouse.calls[-1][0], "dpi")

        self.select("pollingRate", 1)
        self.click("applyRate")
        self.assertEqual(self.mouse.calls[-1], ("rate", RATES[1], None))

        self.select("usbPollingRate", 2)
        self.click("applyUsbRate")
        self.assertEqual(self.mouse.calls[-1], ("usb_rate", RATES[2], None))

        self.select("ledEffect", 2)
        self.select("ledBrightness", 1)
        self.select("ledSpeed", 0)
        self.click("applyLed")
        self.assertEqual(self.mouse.settings.led.mode, 3)
        self.assertEqual(self.controller.device["ledEffectLabels"][2], "呼吸模式")

        self.select("sleepTime", 0)
        self.select("deepSleepTime", 6)
        self.click("applySleep")
        self.assertEqual(self.mouse.settings.advance.wireless_sleep, 0)
        self.assertEqual(self.mouse.settings.advance.wireless_deep, 6)

    def test_lift_combo_applies_on_selection(self):
        combo = self.item("liftHeight")
        combo.setProperty("currentIndex", 3)
        QMetaObject.invokeMethod(combo, "activated", Q_ARG("int", 3))
        self.settle()
        self.assertEqual(self.mouse.settings.advance.lift, 3)

    def test_switches_react_to_real_clicks(self):
        self.window.resize(640, 900)
        self.settle(1.0)
        scroll = self.item("scroll")
        flickable = scroll.property("contentItem")
        switch = self.item("flag_ripple_control")
        before = switch.mapToItem(self.window.contentItem(), QPointF(0, 0)).y()
        flickable.setProperty("contentY", max(0, before - 300))
        self.settle(1.0)

        for expected in (True, False):
            pos = switch.mapToItem(self.window.contentItem(), QPointF(10, 10))
            QTest.mouseClick(self.window, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier,
                             QPoint(int(pos.x()), int(pos.y())))
            self.settle()
            self.assertEqual(switch.property("checked"), expected)
            self.assertEqual(self.mouse.settings.advance.ripple_control, expected)

    def test_editor_follows_device_after_refresh(self):
        self.controller.apply_led(2, 2, 1)
        self.settle()
        self.assertEqual(self.item("ledEffect").property("currentIndex"), 2)
        self.assertEqual(self.item("ledBrightness").property("currentIndex"), 2)
        # 速度滑块是协议 Speed 的反码：设备存 2，界面显示 3-2=1。
        self.assertEqual(self.item("ledSpeed").property("currentIndex"), 1)

        self.controller.set_sleep(1, 2)
        self.settle()
        self.assertEqual(self.item("sleepTime").property("currentIndex"), 1)
        self.assertEqual(self.item("deepSleepTime").property("currentIndex"), 2)

    def test_invalid_firmware_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.bin"
            path.write_bytes(b"\0" * 8)
            with patch(
                "open_inphic.gui.QFileDialog.getOpenFileName",
                return_value=(str(path), ""),
            ):
                self.controller.choose_firmware()
            self.app.processEvents()
        self.assertFalse(self.controller.firmwareReady)
        self.assertIn("太小", self.controller.firmwareFileInfo)
        self.assertFalse(self.item("applyFirmware").property("enabled"))

    def test_firmware_download_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            data = bytearray(64)
            data[16:20] = (0x00710106).to_bytes(4, "little")
            data[20:22] = (0xEC02).to_bytes(2, "big")
            path.write_bytes(bytes(data))
            seen = {}

            def fake_fetch(target_fwid=None, progress=None):
                seen["target"] = target_fwid
                if progress:
                    progress(42, "half")
                return path

            with patch("open_inphic.firmware.fetch_firmware", side_effect=fake_fetch):
                self.controller.download_firmware()
                self.settle()

            self.assertEqual(seen["target"], 0x00710106)
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.controller.upgrading)
        self.assertTrue(self.controller.firmwareReady)
        self.assertIn("X9", self.controller.firmwareFileInfo)
        self.assertIn("固件已获取", self.controller.message)

    def test_firmware_same_version_requires_force(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            data = bytearray(64)
            data[16:20] = (0x00710106).to_bytes(4, "little")
            data[20:22] = (0xEB02).to_bytes(2, "big")
            path.write_bytes(bytes(data))
            with patch(
                "open_inphic.gui.QFileDialog.getOpenFileName",
                return_value=(str(path), ""),
            ):
                self.controller.choose_firmware()
            self.app.processEvents()

            self.assertTrue(self.controller.firmwareNeedsForce)
            self.assertIn("无需升级", self.controller.firmwareFileInfo)
            self.assertFalse(self.item("applyFirmware").property("enabled"))

            with patch(
                "open_inphic.firmware.upgrade_mouse",
                side_effect=AssertionError("must not run without force"),
            ):
                self.controller.upgrade_firmware()
            self.assertFalse(self.controller.busy)
            self.assertIn("强制", self.controller.message)

            self.controller.setForceUpgrade(True)
            self.app.processEvents()
            self.assertTrue(self.controller.forceUpgrade)
            self.assertTrue(self.item("applyFirmware").property("enabled"))

            image = firmware.parse_firmware(path)
            profile = firmware.boot_profile(firmware.MOUSE_BOOT_FAMILY)
            prepared = firmware.prepare_image(image, profile)
            result = firmware.UpgradeResult(
                image=image,
                boot=firmware.FirmwareInfo(0x0071FF00, 0x0100),
                profile=profile,
                prepared=prepared,
                backup=path.with_suffix(".backup"),
                app_ack=True,
                rebooted=True,
                app_firmware=firmware.FirmwareInfo(0x00710106, 0xEB02),
            )
            seen = {}

            def fake_upgrade(_path, expected_fwid=None, progress=None, force=False):
                seen["force"] = force
                if progress:
                    progress(50, "half")
                return result

            with patch("open_inphic.firmware.upgrade_mouse", side_effect=fake_upgrade):
                self.controller.upgrade_firmware()
                self.settle()

            self.assertTrue(seen["force"])
        self.assertIn("升级完成", self.controller.message)

    def test_firmware_selection_and_upgrade_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x9.bin"
            data = bytearray(64)
            data[16:20] = (0x00710106).to_bytes(4, "little")
            data[20:22] = (0xEC02).to_bytes(2, "big")
            path.write_bytes(bytes(data))

            with patch(
                "open_inphic.gui.QFileDialog.getOpenFileName",
                return_value=(str(path), ""),
            ):
                self.controller.choose_firmware()
            self.app.processEvents()
            self.assertTrue(self.controller.firmwareReady)
            self.assertIn("X9", self.controller.firmwareFileInfo)
            self.assertTrue(self.item("applyFirmware").property("enabled"))

            image = firmware.parse_firmware(path)
            profile = firmware.boot_profile(firmware.MOUSE_BOOT_FAMILY)
            prepared = firmware.prepare_image(image, profile)
            result = firmware.UpgradeResult(
                image=image,
                boot=firmware.FirmwareInfo(0x0071FF00, 0x0100),
                profile=profile,
                prepared=prepared,
                backup=path.with_suffix(".backup"),
                app_ack=True,
                rebooted=True,
                app_firmware=firmware.FirmwareInfo(0x00710106, 0xEC02),
            )
            seen = {}

            def fake_upgrade(_path, expected_fwid=None, progress=None, force=False):
                seen["expected"] = expected_fwid
                seen["force"] = force
                if progress:
                    progress(42, "half")
                return result

            with patch("open_inphic.firmware.upgrade_mouse", side_effect=fake_upgrade):
                self.controller.upgrade_firmware()
                self.settle()

            self.assertEqual(seen["expected"], 0x00710106)
            self.assertFalse(seen["force"])
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.controller.upgrading)
        self.assertEqual(self.controller.upgradeProgress, 100)
        self.assertIn("升级完成", self.controller.message)

    def test_offline_keeps_the_x9_effect_list(self):
        self.mouse.current = replace(self.mouse.current, online=False, settings=None)
        self.controller.poll()
        self.settle()
        self.assertEqual(self.controller.device["ledEffects"], [1, 2, 3, 4, 5])
        self.assertEqual(self.controller.device["ledEffectLabels"][0], "关闭灯光")

    def test_controls_disable_when_mouse_is_offline(self):
        self.mouse.current = replace(self.mouse.current, online=False, settings=None)
        self.controller.poll()
        self.settle()
        for name in BUTTONS[1:] + COMBOS + SWITCHES + FIELDS[:1]:
            self.assertFalse(self.item(name).property("enabled"), name)
        self.assertTrue(self.item("refresh").property("enabled"))
        self.assertTrue(self.item("chooseFirmware").property("enabled"))
        self.assertTrue(self.item("downloadFirmware").property("enabled"))
        self.assertFalse(self.item("applyFirmware").property("enabled"))
        self.assertFalse(self.item("upgradeProgress").property("visible"))
        self.assertEqual(self.controller.device["mode"], "—")


if __name__ == "__main__":
    unittest.main()
