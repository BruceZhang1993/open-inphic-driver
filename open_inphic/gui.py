"""Qt Quick entry point for the Linux X9 PRO settings app."""

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sys

try:
    from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtQml import QQmlApplicationEngine
    from PyQt6.QtQuick import QQuickWindow
    from PyQt6.QtQuickControls2 import QQuickStyle
    from PyQt6.QtWidgets import QApplication, QFileDialog, QSystemTrayIcon
except ImportError as error:
    raise SystemExit(f"启动图形界面需要 PyQt6 和 Qt Quick：{error}") from error

from . import firmware as firmware_module
from .hid import (
    ADVANCE_FLAGS,
    DeviceBusy,
    DeviceError,
    DeviceState,
    LED_EFFECTS,
    LED_STEPS,
    LIFT_LABELS,
    RATE_COLORS,
    RATES,
    RECEIVERS,
    WIRELESS_DEEP_SLEEP_MINUTES,
    WIRELESS_SLEEP_SECONDS,
    X9_EFFECTS,
    AdvanceSetting,
    Led,
    firmware_version,
    open_mouse,
)
from .tray import Tray


class Controller(QObject):
    deviceChanged = pyqtSignal()
    editorChanged = pyqtSignal()
    busyChanged = pyqtSignal()
    messageChanged = pyqtSignal()
    finished = pyqtSignal(object, bool)
    firmwareFileChanged = pyqtSignal()
    firmwareReadyChanged = pyqtSignal()
    upgradeProgressChanged = pyqtSignal()
    upgradingChanged = pyqtSignal()
    forceUpgradeChanged = pyqtSignal()
    upgradeProgressSignal = pyqtSignal(int)
    upgradeDone = pyqtSignal(bool, str)
    fetchDone = pyqtSignal(bool, str, str)

    def __init__(self, node: Path | None):
        super().__init__()
        self.node = node
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.finished.connect(self._finished)
        self.upgradeProgressSignal.connect(self._set_upgrade_progress)
        self.upgradeDone.connect(self._upgrade_finished)
        self.fetchDone.connect(self._fetch_finished)
        self._state = None
        self._device = self._describe(None)
        self._busy = False
        self._message = ""
        self._firmware_file = ""
        self._firmware_info = ""
        self._firmware_ready = False
        self._upgrade_progress = 0
        self._upgrading = False
        self._force_upgrade = False
        self._firmware_needs_force = False

    @pyqtProperty("QVariantMap", notify=deviceChanged)
    def device(self):
        return self._device

    @pyqtProperty("QVariantMap", notify=editorChanged)
    def editor(self):
        return self._device

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @pyqtProperty(str, notify=messageChanged)
    def message(self):
        return self._message

    @pyqtProperty(str, notify=firmwareFileChanged)
    def firmwareFile(self):
        return self._firmware_file

    @pyqtProperty(str, notify=firmwareFileChanged)
    def firmwareFileInfo(self):
        return self._firmware_info

    @pyqtProperty(bool, notify=firmwareReadyChanged)
    def firmwareReady(self):
        return self._firmware_ready

    @pyqtProperty(int, notify=upgradeProgressChanged)
    def upgradeProgress(self):
        return self._upgrade_progress

    @pyqtProperty(bool, notify=upgradingChanged)
    def upgrading(self):
        return self._upgrading

    @pyqtProperty(bool, notify=forceUpgradeChanged)
    def forceUpgrade(self):
        return self._force_upgrade

    @pyqtProperty(bool, notify=firmwareFileChanged)
    def firmwareNeedsForce(self):
        return self._firmware_needs_force

    @staticmethod
    def _effects(state: DeviceState | None) -> tuple[int, ...]:
        """Effects offered in the UI.

        Only the X9 table is verified, so an unknown or offline model keeps the X9
        list instead of falling back to every known effect number.
        """
        model = state.model if state else None
        return tuple(sorted(LED_EFFECTS)) if model and model != "X9" else X9_EFFECTS

    @staticmethod
    def _describe(state: DeviceState | None):
        settings = state.settings if state else None
        slots = state.slots if state else {}
        slot_numbers = list(slots)
        current_slot = settings.levels[settings.mode] + 1 if settings else 0
        led = settings.led if settings else Led(1, 0, 0, 0, 0)
        advance = settings.advance if settings else AdvanceSetting(2, 1, 2, 1, 1)
        effects = Controller._effects(state)
        receiver_name = RECEIVERS.get(state.receiver_fwid, "—") if state else "—"
        receiver_firmware = (
            firmware_version(state.receiver_fwid, state.receiver_firmware)
            if state else "—"
        )
        return {
            "connected": state is not None,
            "online": settings is not None,
            "battery": (
                f"{state.battery}%" + (" · 充电中" if state.charging else " · 未充电")
                if settings and state.battery <= 100 else "—"
            ),
            "firmware": (
                firmware_version(state.mouse_fwid, state.firmware)
                if settings and state.firmware is not None else "—"
            ),
            "firmwareHex": f"0x{state.firmware:04x}" if settings else "—",
            "receiverFirmware": receiver_firmware,
            "receiverFirmwareHex": (
                f"0x{state.receiver_firmware:04x}" if state else "—"
            ),
            "model": (state.model if state else None) or "—",
            "receiver": receiver_name,
            "receiverSummary": f"{receiver_name} · {receiver_firmware}" if state else "—",
            "mode": str(settings.mode + 1) if settings else "—",
            "modeIndex": settings.mode if settings else -1,
            "modeLabels": [f"配置模式 {index + 1}" for index in range(4)],
            "slotNumbers": slot_numbers,
            "slotValues": list(slots.values()),
            "slotLabels": [f"第 {slot} 档 · {dpi} DPI" for slot, dpi in slots.items()],
            "slotIndex": slot_numbers.index(current_slot) if current_slot in slots else -1,
            "rateLabels": [f"{rate} Hz · {color}" for rate, color in zip(RATES, RATE_COLORS)],
            "rateIndex": settings.wireless_rates[settings.mode] if settings else -1,
            "usbRateIndex": settings.usb_rates[settings.mode] if settings else -1,
            "ledEffects": list(effects),
            "ledEffectLabels": [LED_EFFECTS[mode] for mode in effects],
            "ledEffectIndex": effects.index(led.mode) if led.mode in effects else -1,
            "ledBrightness": min(led.brightness, LED_STEPS - 1),
            "ledSpeed": min(led.speed, LED_STEPS - 1),
            "sleepLabels": [f"{value} 秒" for value in WIRELESS_SLEEP_SECONDS],
            "sleepIndex": advance.wireless_sleep,
            "deepSleepLabels": [f"{value} 分钟" for value in WIRELESS_DEEP_SLEEP_MINUTES],
            "deepSleepIndex": advance.wireless_deep,
            "liftLabels": list(LIFT_LABELS),
            "liftIndex": advance.lift,
            "advanceFlags": [
                {"key": name, "label": label, "value": getattr(advance, name)}
                for name, _, label in ADVANCE_FLAGS
            ] + [
                {"key": "line_correct", "label": "直线修正", "value": advance.line_correct},
            ],
            "flags": {
                **{name: getattr(advance, name) for name, _, _ in ADVANCE_FLAGS},
                "line_correct": advance.line_correct,
            },
        }

    def _start(self, operation=None, *, background=False):
        if self._busy:
            return
        self._busy = True
        self.busyChanged.emit()
        if not background:
            self._message = "正在读取设备…" if operation is None else "正在设置并回读…"
            self.messageChanged.emit()
        future = self.executor.submit(self._work, operation, self._state, background)
        future.add_done_callback(lambda result: self.finished.emit(result, background))

    def _work(self, operation, previous, background):
        with open_mouse(self.node) as mouse:
            if background and previous is not None:
                power = mouse.power_status()
                if not power.online:
                    return DeviceState(
                        previous.receiver_firmware, False, power.battery, power.charging
                    ), "移动或轻按鼠标即可唤醒。"
                if previous.online:
                    return replace(previous, battery=power.battery, charging=power.charging), ""
                return mouse.state(power), ""
            if operation is not None:
                operation(mouse)
            try:
                state = mouse.state()
            except (DeviceError, OSError) as error:
                if operation is None:
                    raise
                return None, f"设置已确认。刷新设备状态失败（{error}）"
        if not state.online:
            return state, "移动或轻按鼠标即可唤醒。"
        return state, "设置已回读确认。" if operation is not None else ""

    @pyqtSlot(object, bool)
    def _finished(self, future: Future, background: bool):
        try:
            state, message = future.result()
        except DeviceBusy as error:
            state, message = self._state, str(error)
        except (DeviceError, OSError) as error:
            state, message = None, str(error)
        except Exception as error:  # noqa: BLE001
            # An unexpected failure would otherwise escape a Qt slot and abort the app.
            state, message = self._state, f"操作失败：{error}"
        connection_changed = (
            (state is None) != (self._state is None)
            or bool(state and state.online) != bool(self._state and self._state.online)
        )
        self._state = state
        description = self._describe(state)
        device_changed = description != self._device
        self._device = description
        self._busy = False
        if not background or connection_changed or state is None:
            self._message = message
            self.messageChanged.emit()
        if device_changed:
            self.deviceChanged.emit()
        if not background or connection_changed:
            self.editorChanged.emit()
        self.busyChanged.emit()

    @property
    def state(self) -> DeviceState | None:
        return self._state

    def poll(self):
        self._start(background=True)

    @pyqtSlot(result=bool)
    def canHide(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            return True
        self._message = "系统托盘暂不可用，窗口保持打开。"
        self.messageChanged.emit()
        return False

    @pyqtSlot()
    def refresh(self):
        self._start(None)

    @pyqtSlot(int)
    def set_slot(self, slot: int):
        self._start(lambda mouse: mouse.set_slot(slot))

    @pyqtSlot(int, int)
    def set_dpi(self, slot: int, dpi: int):
        self._start(lambda mouse: mouse.set_dpi(dpi, slot))

    @pyqtSlot(int)
    def set_rate(self, index: int):
        if 0 <= index < len(RATES):
            self._start(lambda mouse: mouse.set_rate(RATES[index]))

    @pyqtSlot(int)
    def set_usb_rate(self, index: int):
        if 0 <= index < len(RATES):
            self._start(lambda mouse: mouse.set_usb_rate(RATES[index]))

    @pyqtSlot(int)
    def set_mode(self, index: int):
        if 0 <= index <= 3:
            self._start(lambda mouse: mouse.set_mode(index))

    @pyqtSlot(int)
    def set_led_effect(self, index: int):
        effects = self._effects(self._state)
        if 0 <= index < len(effects):
            self._start(lambda mouse: mouse.set_led(mode=effects[index]))

    @pyqtSlot(int, int, int)
    def apply_led(self, effect: int, brightness: int, speed: int):
        """一次提交灯效模式、亮度和速度，界面上的「应用」走这里。

        灯效模式必须和亮度、速度一起写：单独写亮度只更新 b[10]，选中的模式不会生效。
        """
        effects = self._effects(self._state)
        if not 0 <= effect < len(effects):
            return
        # 厂商界面把速度滑块画成协议 Speed 的反码，滑块 0 最慢对应协议 3。
        ui_speed = max(0, min(3, speed))
        self._start(
            lambda mouse: mouse.set_led(
                mode=effects[effect], brightness=brightness, speed=3 - ui_speed
            )
        )

    @pyqtSlot(int, int)
    def set_sleep(self, primary: int, deep: int):
        self._start(lambda mouse: mouse.set_advance(wireless_sleep=primary, wireless_deep=deep))

    @pyqtSlot(int)
    def set_lift(self, index: int):
        self._start(lambda mouse: mouse.set_advance(lift=index))

    @pyqtSlot(str, bool)
    def set_flag(self, name: str, value: bool):
        if name in {key for key, _, _ in ADVANCE_FLAGS} | {"line_correct"}:
            self._start(lambda mouse: mouse.set_advance(**{name: value}))

    def _set_firmware_file(self, path: Path | str):
        self._firmware_file = str(path)
        self._firmware_needs_force = False
        try:
            image = firmware_module.parse_firmware(path)
        except (DeviceError, OSError) as error:
            self._firmware_info = f"{Path(path).name}：{error}"
            self._firmware_ready = False
        else:
            current = None
            if self._state is not None and self._state.firmware is not None:
                # Do not let a stale receiver version block recovery when the
                # mouse is already sitting in its bootloader.
                if not firmware_module.bootloader_nodes():
                    current = self._state.firmware
            note = ""
            if current is not None and image.version == current:
                self._firmware_needs_force = True
                note = " · 与当前版本相同，无需升级"
            elif current is not None and image.version < current:
                self._firmware_needs_force = True
                note = " · 低于当前版本，需要强制"
            self._firmware_info = (
                f"{image.model} · {image.version_text} · "
                f"{len(image.data)} 字节{note}"
            )
            self._firmware_ready = True
        self.firmwareFileChanged.emit()
        self.firmwareReadyChanged.emit()

    @pyqtSlot(bool)
    def setForceUpgrade(self, value: bool):
        value = bool(value)
        if value != self._force_upgrade:
            self._force_upgrade = value
            self.forceUpgradeChanged.emit()

    @pyqtSlot()
    def choose_firmware(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            None,
            "选择鼠标固件",
            str(Path.home()),
            "固件文件 (*.bin);;所有文件 (*)",
        )
        if path:
            self._set_firmware_file(path)

    @pyqtSlot()
    def download_firmware(self):
        if self._busy:
            return
        expected = None
        if self._state is not None:
            expected = self._state.mouse_fwid or None
            if expected is None and self._state.pairing is not None:
                expected = self._state.pairing.firmware_id or None
        self._busy = True
        self._upgrading = True
        self._upgrade_progress = 0
        self._message = "正在从英菲克官网下载并提取固件…"
        self.busyChanged.emit()
        self.upgradingChanged.emit()
        self.upgradeProgressChanged.emit()
        self.messageChanged.emit()
        future = self.executor.submit(self._fetch_work, expected)
        future.add_done_callback(self._fetch_done)

    @pyqtSlot()
    def upgrade_firmware(self):
        if self._busy or not self._firmware_ready:
            return
        path = self._firmware_file
        try:
            firmware_module.parse_firmware(path)
        except (DeviceError, OSError) as error:
            self._message = str(error)
            self.messageChanged.emit()
            return
        if self._firmware_needs_force and not self._force_upgrade:
            self._message = (
                "当前固件与所选版本相同或更高；"
                "请先勾选「强制重刷相同版本/降级」。"
            )
            self.messageChanged.emit()
            return
        self._busy = True
        self._upgrading = True
        self._upgrade_progress = 0
        self._message = "正在升级固件，请勿断开 USB…"
        self.busyChanged.emit()
        self.upgradingChanged.emit()
        self.upgradeProgressChanged.emit()
        self.messageChanged.emit()
        future = self.executor.submit(
            self._upgrade_work, path, self._force_upgrade
        )
        future.add_done_callback(self._upgrade_done)

    def _fetch_work(self, expected):
        def progress(value: int, _message: str):
            self.upgradeProgressSignal.emit(value)

        try:
            path = firmware_module.fetch_firmware(
                expected, progress=progress
            )
        except (DeviceError, OSError) as error:
            return False, str(error), ""
        except Exception as error:  # noqa: BLE001
            return False, f"获取固件失败：{error}", ""
        return True, f"固件已获取：{path}", str(path)

    def _fetch_done(self, future: Future):
        try:
            ok, message, path = future.result()
        except Exception as error:  # noqa: BLE001
            ok, message, path = False, f"获取固件失败：{error}", ""
        self.fetchDone.emit(ok, message, path)

    @pyqtSlot(bool, str, str)
    def _fetch_finished(self, ok: bool, message: str, path: str):
        self._busy = False
        self._upgrading = False
        if ok and path:
            self._set_firmware_file(path)
        self._message = message
        self.busyChanged.emit()
        self.upgradingChanged.emit()
        self.upgradeProgressChanged.emit()
        self.messageChanged.emit()

    def _upgrade_work(self, path: str, force: bool):
        expected = None
        if self._state is not None:
            expected = self._state.mouse_fwid or None
            if expected is None and self._state.pairing is not None:
                expected = self._state.pairing.firmware_id or None

        def progress(value: int, _message: str):
            self.upgradeProgressSignal.emit(value)

        try:
            result = firmware_module.upgrade_mouse(
                path, expected_fwid=expected, progress=progress, force=force
            )
        except (DeviceError, OSError) as error:
            return False, str(error)
        except Exception as error:  # noqa: BLE001
            return False, f"固件升级失败：{error}"
        if result.confirmed:
            return (
                True,
                f"固件升级完成并已回读：{result.app_firmware.version_text}。"
                f"原 Flash 备份：{result.backup}。请点击「刷新」更新界面。",
            )
        if result.app_firmware is not None:
            return (
                False,
                f"设备回读固件 {result.app_firmware.version_text} 与目标 "
                f"{result.image.version_text} 不一致。",
            )
        if result.rebooted:
            return (
                True,
                f"固件 {result.image.version_text} 已写入并校验，设备已重启；"
                f"原 Flash 备份：{result.backup}。请重新插拔鼠标后点击「刷新」。",
            )
        return (
            False,
            "固件已写入并校验，但设备仍停留在 Bootloader。"
            "请拔插 USB 后重试；原 Flash 备份已保存。",
        )

    def _upgrade_done(self, future: Future):
        try:
            ok, message = future.result()
        except Exception as error:  # noqa: BLE001
            ok, message = False, f"固件升级失败：{error}"
        self.upgradeDone.emit(ok, message)

    @pyqtSlot(int)
    def _set_upgrade_progress(self, value: int):
        value = max(0, min(100, int(value)))
        if value != self._upgrade_progress:
            self._upgrade_progress = value
            self.upgradeProgressChanged.emit()

    @pyqtSlot(bool, str)
    def _upgrade_finished(self, ok: bool, message: str):
        self._busy = False
        self._upgrading = False
        if ok:
            self._upgrade_progress = 100
        self._message = message
        self.busyChanged.emit()
        self.upgradingChanged.emit()
        self.upgradeProgressChanged.emit()
        self.messageChanged.emit()


def main() -> int:
    parser = argparse.ArgumentParser(description="INPHIC X9 PRO 图形配置界面")
    parser.add_argument("--device", type=Path, help="指定配置节点，例如 /dev/hidraw4")
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Open INPHIC")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("未找到系统托盘。请启用桌面的通知区域后再启动。", file=sys.stderr)
        return 1
    QQuickStyle.setStyle("Material")

    controller = Controller(args.device)
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"backend": controller})
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "ui" / "Main.qml")))
    try:
        windows = engine.rootObjects()
        if not windows:
            return 1
        window = windows[0]
        if not isinstance(window, QQuickWindow):
            return 1
        tray = Tray(app, window, controller)
        tray.show()
        return app.exec()
    finally:
        controller.executor.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
