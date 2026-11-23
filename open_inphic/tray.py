"""Qt system tray for the X9 PRO."""

from PyQt6.QtCore import QObject, QRectF, QTimer, Qt
from PyQt6.QtGui import QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


def battery_icon(percent: int | None, charging: bool) -> QIcon:
    icon = QIcon()
    for size in (16, 22, 32, 48):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / 32, size / 32)
        painter.setPen(QPen(QColor("#eff6fc"), 1.6))
        painter.setBrush(QColor("#172734"))
        painter.drawRoundedRect(QRectF(2.5, 8, 24, 16), 3, 3)
        painter.drawRoundedRect(QRectF(26.5, 13, 3, 6), 1, 1)

        if percent is None:
            painter.drawLine(11, 16, 18, 16)
        elif percent > 0:
            fill = "#ef6975" if percent <= 20 else "#76d8a5"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(QRectF(5, 10.5, 19 * percent / 100, 11), 1.5, 1.5)

        if charging:
            bolt = QPainterPath()
            bolt.moveTo(17, 8)
            bolt.lineTo(10.5, 17.5)
            bolt.lineTo(14, 17.5)
            bolt.lineTo(12.5, 24)
            bolt.lineTo(20, 14.5)
            bolt.lineTo(16, 14.5)
            bolt.closeSubpath()
            painter.setPen(QPen(QColor("#172734"), 1.1))
            painter.setBrush(QColor("#ffd45d"))
            painter.drawPath(bolt)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class Tray(QObject):
    def __init__(self, app, window, controller):
        super().__init__(app)
        self.app = app
        self.window = window
        self.controller = controller
        self._icon_state = None
        self._effect_models = ()
        self.effect_actions = []
        self._exiting = False

        self.icon = QSystemTrayIcon(self)
        self.menu = QMenu()
        self.status = self.menu.addAction("INPHIC X9 PRO")
        self.status.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("打开设置", self.open_window)
        self.refresh_action = self.menu.addAction("刷新设备", controller.refresh)
        self.slots_menu = self.menu.addMenu("DPI 档位")
        self.rates_menu = self.menu.addMenu("2.4G 回报率")
        self.usb_rates_menu = self.menu.addMenu("有线回报率")
        self.modes_menu = self.menu.addMenu("配置模式")
        self.effects_menu = self.menu.addMenu("灯效")
        self.menu.addSeparator()
        self.menu.addAction("退出 Open INPHIC", self.exit)

        self.slots = QActionGroup(self.slots_menu)
        self.slot_actions = []
        for slot in range(1, 9):
            action = self.slots_menu.addAction("")
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, slot=slot: controller.set_slot(slot))
            self.slots.addAction(action)
            self.slot_actions.append(action)

        self.rates = QActionGroup(self.rates_menu)
        self.rate_actions = []
        for index, label in enumerate(controller.device["rateLabels"]):
            action = self.rates_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, index=index: controller.set_rate(index))
            self.rates.addAction(action)
            self.rate_actions.append(action)

        self.usb_rates = QActionGroup(self.usb_rates_menu)
        self.usb_rate_actions = []
        for index, label in enumerate(controller.device["rateLabels"]):
            action = self.usb_rates_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, index=index: controller.set_usb_rate(index))
            self.usb_rates.addAction(action)
            self.usb_rate_actions.append(action)

        self.modes = QActionGroup(self.modes_menu)
        self.mode_actions = []
        for index, label in enumerate(controller.device["modeLabels"]):
            action = self.modes_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked, index=index: controller.set_mode(index))
            self.modes.addAction(action)
            self.mode_actions.append(action)

        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self.activate)
        controller.deviceChanged.connect(self.sync)
        controller.busyChanged.connect(self.sync)
        controller.messageChanged.connect(self.sync)
        self.timer = QTimer(self)
        self.timer.setInterval(8000)
        self.timer.timeout.connect(controller.poll)
        self.sync()

    def show(self, window_visible: bool = True):
        """Start the tray; the settings window only opens when asked for."""
        if window_visible:
            self.window.show()
        else:
            self.window.hide()
        self.icon.show()
        self.timer.start()

    def activate(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()

    def open_window(self):
        if self._exiting:
            return
        was_hidden = not self.window.isVisible()
        self.window.showNormal()
        self.window.raise_()
        self.window.requestActivate()
        if was_hidden:
            self.controller.refresh()

    def exit(self):
        self._exiting = True
        self.timer.stop()
        self.sync()

    def sync(self):
        if self._exiting and not self.controller.busy:
            self.window.setProperty("allowExit", True)
            self.icon.hide()
            self.app.quit()
            return

        device = self.controller.device
        state = self.controller.state
        editable = device["configurable"] and not self.controller.busy and not self._exiting
        percent = (state.battery if device["online"] and state.battery is not None
                   and 0 <= state.battery <= 100 else None)
        charging = bool(device["online"] and state.charging)
        icon_state = (percent, charging)
        if self._icon_state != icon_state:
            self.icon.setIcon(battery_icon(*icon_state))
            self._icon_state = icon_state

        if device["online"]:
            status = device["battery"] if percent is not None else f"{device['connection']} 在线"
            title = device["model"] if device["model"] != "—" else "X9 PRO"
            lines = [f"INPHIC {title} · {device['connection']} 在线"]
            if percent is not None:
                lines.append(f"电池 {status}")
            if device["firmware"] != "—":
                lines.append(f"鼠标固件 {device['firmware']}")
            if device["receiverFirmware"] != "—":
                lines.append(f"接收器固件 {device['receiverFirmware']}")
            if device["configurable"]:
                lines.append(f"配置模式 {device['mode']}")
                if device["ledEffectIndex"] >= 0:
                    lines.append(f"灯效 {device['ledEffectLabels'][device['ledEffectIndex']]}")
                if device["slotIndex"] >= 0:
                    lines.append(f"DPI {device['slotLabels'][device['slotIndex']]}")
                if state.connection == "usb":
                    lines.append(f"有线回报率 {device['rateLabels'][device['usbRateIndex']]}")
                else:
                    lines.append(f"2.4G 回报率 {device['rateLabels'][device['rateIndex']]}")
        else:
            status = "鼠标离线" if device["connected"] else "等待连接"
            title = device["model"] if device["model"] != "—" else "X9 PRO"
            lines = [f"INPHIC {title} · {status}"]
            if device["receiverFirmware"] != "—":
                lines.append(f"接收器固件 {device['receiverFirmware']}")

        if self.controller.message:
            lines.append(self.controller.message)
        title = device["model"] if device["model"] != "—" else "X9 PRO"
        self.status.setText(f"{title} · {status}")
        self.icon.setToolTip("\n".join(lines))
        self.refresh_action.setEnabled(not self.controller.busy and not self._exiting)

        slot_labels = dict(zip(device["slotNumbers"], device["slotLabels"]))
        current_slot = device["slotNumbers"][device["slotIndex"]] if device["slotIndex"] >= 0 else None
        self.slots_menu.setEnabled(editable and bool(slot_labels))
        for slot, action in enumerate(self.slot_actions, 1):
            label = slot_labels.get(slot)
            action.setVisible(label is not None)
            if label:
                action.setText(label)
            action.setChecked(slot == current_slot)

        self.rates_menu.setEnabled(editable)
        for index, action in enumerate(self.rate_actions):
            action.setChecked(index == device["rateIndex"])

        self.usb_rates_menu.setEnabled(editable)
        for index, action in enumerate(self.usb_rate_actions):
            action.setChecked(index == device["usbRateIndex"])

        self.modes_menu.setEnabled(editable)
        for index, action in enumerate(self.mode_actions):
            action.setChecked(index == device["modeIndex"])

        self.effects_menu.setEnabled(editable)
        effects = tuple(device["ledEffects"])
        if effects != self._effect_models:
            self.effects_menu.clear()
            self.effect_actions = []
            for index, label in enumerate(device["ledEffectLabels"]):
                action = self.effects_menu.addAction(label)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda _checked, index=index: self.controller.set_led_effect(index)
                )
                self.effect_actions.append(action)
            self._effect_models = effects
        current = device["ledEffectIndex"]
        for index, action in enumerate(self.effect_actions):
            action.setChecked(index == current)
