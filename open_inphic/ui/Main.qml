import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

ApplicationWindow {
    id: window

    required property var backend
    property bool allowExit: false
    readonly property bool darkMode: Material.theme === Material.Dark
    readonly property color surface: darkMode ? "#232c37" : "#ffffff"
    readonly property color borderColor: darkMode ? "#384858" : "#dce5ed"
    readonly property color textColor: darkMode ? "#ecf2f8" : "#192734"
    readonly property color mutedColor: darkMode ? "#a8b8c8" : "#4a6073"
    readonly property bool editable: backend.device.online && !backend.busy

    title: "INPHIC X9 PRO"
    width: 640
    height: 860
    minimumWidth: 448
    minimumHeight: 420
    visible: true
    color: darkMode ? "#19212b" : "#eff4f8"

    Material.theme: Material.System
    Material.accent: Material.Teal

    onClosing: close => {
        if (window.allowExit)
            return
        close.accepted = false
        if (backend.canHide())
            window.hide()
    }
    Component.onCompleted: backend.refresh()

    Connections {
        target: window.backend
        function onEditorChanged() {
            const device = window.backend.device
            modeBox.currentIndex = device.modeIndex
            slotBox.currentIndex = device.slotIndex
            rateBox.currentIndex = device.rateIndex
            usbRateBox.currentIndex = device.usbRateIndex
            ledEffectBox.currentIndex = device.ledEffectIndex
            ledBrightnessBox.currentIndex = device.ledBrightness
            ledSpeedBox.currentIndex = 3 - device.ledSpeed
            sleepBox.currentIndex = device.sleepIndex
            deepSleepBox.currentIndex = device.deepSleepIndex
            liftBox.currentIndex = device.liftIndex
            // 开关的 checked 会在用户点击时断开绑定，刷新后按回读值重新对齐。
            flagUltraLowDelay.checked = device.flags.ultra_low_delay
            flagUltraLowPower.checked = device.flags.ultra_low_power
            flagRippleControl.checked = device.flags.ripple_control
            flagMoveWakeUp.checked = device.flags.move_wake_up
            flagLineCorrect.checked = device.flags.line_correct
            dpiField.text = slotBox.currentIndex < 0
                ? "" : String(device.slotValues[slotBox.currentIndex])
        }
    }

    ScrollView {
        id: scroll
        objectName: "scroll"
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            x: 24
            y: 20
            width: scroll.availableWidth - 48
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ColumnLayout {
                    spacing: 4

                    Label {
                        text: window.backend.device.online && window.backend.device.model !== "—"
                            ? "INPHIC " + window.backend.device.model
                            : "INPHIC X9 PRO"
                        font.pixelSize: 27
                        font.weight: Font.DemiBold
                        color: window.textColor
                    }

                    Label {
                        text: window.backend.device.online ? "2.4G · 在线"
                            : window.backend.device.connected ? "2.4G · 鼠标离线"
                            : "2.4G · 等待连接"
                        color: window.mutedColor
                    }
                }

                Item { Layout.fillWidth: true }

                Button {
                    objectName: "refresh"
                    text: "刷新"
                    enabled: !window.backend.busy
                    onClicked: window.backend.refresh()
                }
            }

            Panel {
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    Metric { title: "电量"; value: window.backend.device.battery }
                    Metric { title: "型号"; value: window.backend.device.model }
                    Metric { title: "配置模式"; value: window.backend.device.mode }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    Metric { title: "鼠标固件"; value: window.backend.device.firmware }
                    Metric { title: "接收器"; value: window.backend.device.receiverSummary }
                }
            }

            Panel {
                Label { text: "配置模式"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }
                Label {
                    text: "每个模式各自保存 DPI 档位、回报率和灯效。"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true

                    ComboBox {
                        id: modeBox
                        objectName: "configMode"
                        Layout.fillWidth: true
                        model: window.backend.editor.modeLabels
                        currentIndex: -1
                        enabled: window.editable
                        Accessible.name: "配置模式"
                    }

                    Button {
                        objectName: "applyMode"
                        text: "应用"
                        enabled: window.editable && modeBox.currentIndex >= 0
                        onClicked: window.backend.set_mode(modeBox.currentIndex)
                    }
                }
            }

            Panel {
                Label { text: "灵敏度"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }

                RowLayout {
                    Layout.fillWidth: true

                    ComboBox {
                        id: slotBox
                        objectName: "dpiSlot"
                        Layout.fillWidth: true
                        model: window.backend.editor.slotLabels
                        currentIndex: -1
                        enabled: window.editable
                        Accessible.name: "DPI 档位"
                        onActivated: index => {
                            dpiField.text = String(window.backend.device.slotValues[index])
                        }
                    }

                    Button {
                        objectName: "applySlot"
                        text: "切换档位"
                        enabled: window.editable && slotBox.currentIndex >= 0
                        onClicked: window.backend.set_slot(window.backend.device.slotNumbers[slotBox.currentIndex])
                    }
                }

                RowLayout {
                    Layout.fillWidth: true

                    TextField {
                        id: dpiField
                        objectName: "dpiValue"
                        Layout.fillWidth: true
                        placeholderText: "档位数值（DPI）"
                        Accessible.name: "档位数值（DPI）"
                        validator: IntValidator { bottom: 1; top: 65535 }
                        inputMethodHints: Qt.ImhDigitsOnly
                        selectByMouse: true
                        enabled: window.editable && slotBox.currentIndex >= 0
                    }

                    Button {
                        objectName: "applyDpi"
                        text: "设置 DPI"
                        enabled: dpiField.enabled && dpiField.acceptableInput
                        onClicked: window.backend.set_dpi(
                            window.backend.device.slotNumbers[slotBox.currentIndex],
                            Number(dpiField.text)
                        )
                    }
                }
            }

            Panel {
                Label { text: "回报率"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }
                Label {
                    text: "有线回报率仅在 USB 直连时生效，无线回报率在 2.4G 模式下生效。"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true

                    Label { text: "2.4G"; color: window.mutedColor; Layout.preferredWidth: 52 }

                    ComboBox {
                        id: rateBox
                        objectName: "pollingRate"
                        Layout.fillWidth: true
                        model: window.backend.editor.rateLabels
                        currentIndex: -1
                        enabled: window.editable
                        Accessible.name: "2.4G 回报率"
                    }

                    Button {
                        objectName: "applyRate"
                        text: "应用"
                        enabled: window.editable && rateBox.currentIndex >= 0
                        onClicked: window.backend.set_rate(rateBox.currentIndex)
                    }
                }

                RowLayout {
                    Layout.fillWidth: true

                    Label { text: "有线"; color: window.mutedColor; Layout.preferredWidth: 52 }

                    ComboBox {
                        id: usbRateBox
                        objectName: "usbPollingRate"
                        Layout.fillWidth: true
                        model: window.backend.editor.rateLabels
                        currentIndex: -1
                        enabled: window.editable
                        Accessible.name: "有线回报率"
                    }

                    Button {
                        objectName: "applyUsbRate"
                        text: "应用"
                        enabled: window.editable && usbRateBox.currentIndex >= 0
                        onClicked: window.backend.set_usb_rate(usbRateBox.currentIndex)
                    }
                }
            }

            Panel {
                Label { text: "灯效"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }
                Label {
                    text: "亮度 0 最暗、3 最亮；速度 0 最慢、3 最快。"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "模式"; color: window.mutedColor; Layout.preferredWidth: 52 }
                            ComboBox {
                                id: ledEffectBox
                                objectName: "ledEffect"
                                Layout.fillWidth: true
                                model: window.backend.editor.ledEffectLabels
                                currentIndex: -1
                                enabled: window.editable
                                Accessible.name: "灯效模式"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "亮度"; color: window.mutedColor; Layout.preferredWidth: 52 }
                            ComboBox {
                                id: ledBrightnessBox
                                objectName: "ledBrightness"
                                Layout.fillWidth: true
                                model: [0, 1, 2, 3]
                                currentIndex: -1
                                enabled: window.editable
                                Accessible.name: "亮度"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "速度"; color: window.mutedColor; Layout.preferredWidth: 52 }
                            ComboBox {
                                id: ledSpeedBox
                                objectName: "ledSpeed"
                                Layout.fillWidth: true
                                model: [0, 1, 2, 3]
                                currentIndex: -1
                                enabled: window.editable
                                Accessible.name: "速度"
                            }
                        }
                    }

                    Button {
                        objectName: "applyLed"
                        text: "应用"
                        Layout.preferredWidth: 76
                        Layout.alignment: Qt.AlignVCenter
                        enabled: window.editable && ledEffectBox.currentIndex >= 0
                        onClicked: window.backend.apply_led(
                            ledEffectBox.currentIndex,
                            ledBrightnessBox.currentIndex,
                            ledSpeedBox.currentIndex
                        )
                    }
                }
            }

            Panel {
                Label { text: "休眠"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }
                Label {
                    text: "鼠标静止超过一级休眠时间后进入休眠，继续静止超过深度休眠时间进入睡眠。"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "一级"; color: window.mutedColor; Layout.preferredWidth: 52 }
                            ComboBox {
                                id: sleepBox
                                objectName: "sleepTime"
                                Layout.fillWidth: true
                                model: window.backend.editor.sleepLabels
                                currentIndex: -1
                                enabled: window.editable
                                Accessible.name: "一级休眠时间"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label { text: "深度"; color: window.mutedColor; Layout.preferredWidth: 52 }
                            ComboBox {
                                id: deepSleepBox
                                objectName: "deepSleepTime"
                                Layout.fillWidth: true
                                model: window.backend.editor.deepSleepLabels
                                currentIndex: -1
                                enabled: window.editable
                                Accessible.name: "深度休眠时间"
                            }
                        }
                    }

                    Button {
                        objectName: "applySleep"
                        text: "应用"
                        Layout.preferredWidth: 76
                        Layout.alignment: Qt.AlignVCenter
                        enabled: window.editable && sleepBox.currentIndex >= 0
                        onClicked: window.backend.set_sleep(sleepBox.currentIndex, deepSleepBox.currentIndex)
                    }
                }
            }

            Panel {
                Label { text: "高级功能"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }
                Label {
                    text: "开关立即生效，写入后会回读确认。"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label { text: "鼠标抬起高度"; color: window.textColor }
                        Label {
                            text: "设置鼠标抬起时的高度"
                            color: window.mutedColor
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    ComboBox {
                        id: liftBox
                        objectName: "liftHeight"
                        Layout.preferredWidth: 130
                        Layout.alignment: Qt.AlignVCenter
                        model: window.backend.editor.liftLabels
                        currentIndex: -1
                        enabled: window.editable
                        Accessible.name: "鼠标抬起高度"
                        onActivated: window.backend.set_lift(index)
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label { text: "超低延迟"; color: window.textColor }
                        Label {
                            text: "开启超低延迟之后鼠标画线更及时"
                            color: window.mutedColor
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Switch {
                        id: flagUltraLowDelay
                        objectName: "flag_ultra_low_delay"
                        Layout.alignment: Qt.AlignVCenter
                        checked: window.backend.device.flags.ultra_low_delay
                        enabled: window.editable
                        onToggled: {
                            if (checked !== window.backend.device.flags.ultra_low_delay)
                                window.backend.set_flag("ultra_low_delay", checked)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label { text: "超低功耗"; color: window.textColor }
                        Label {
                            text: "开启超低功耗之后鼠标更省电。开启后回报率固定为 125 Hz，需要修改回报率请先关闭。"
                            color: window.mutedColor
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Switch {
                        id: flagUltraLowPower
                        objectName: "flag_ultra_low_power"
                        Layout.alignment: Qt.AlignVCenter
                        checked: window.backend.device.flags.ultra_low_power
                        enabled: window.editable
                        onToggled: {
                            if (checked !== window.backend.device.flags.ultra_low_power)
                                window.backend.set_flag("ultra_low_power", checked)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label { text: "波纹控制"; color: window.textColor }
                        Label {
                            text: "开启波纹控制之后鼠标会在高速情况下进行算法修正消除波浪形的抖动"
                            color: window.mutedColor
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Switch {
                        id: flagRippleControl
                        objectName: "flag_ripple_control"
                        Layout.alignment: Qt.AlignVCenter
                        checked: window.backend.device.flags.ripple_control
                        enabled: window.editable
                        onToggled: {
                            if (checked !== window.backend.device.flags.ripple_control)
                                window.backend.set_flag("ripple_control", checked)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label { text: "移动唤醒"; color: window.textColor }
                        Label {
                            text: "开启移动唤醒之后鼠标进入深度休眠可以通过移动唤醒鼠标"
                            color: window.mutedColor
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Switch {
                        id: flagMoveWakeUp
                        objectName: "flag_move_wake_up"
                        Layout.alignment: Qt.AlignVCenter
                        checked: window.backend.device.flags.move_wake_up
                        enabled: window.editable
                        onToggled: {
                            if (checked !== window.backend.device.flags.move_wake_up)
                                window.backend.set_flag("move_wake_up", checked)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Label { text: "直线修正"; color: window.textColor }
                        Label {
                            text: "开启直线修正之后鼠标更容易画出直线"
                            color: window.mutedColor
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }

                    Switch {
                        id: flagLineCorrect
                        objectName: "flag_line_correct"
                        Layout.alignment: Qt.AlignVCenter
                        checked: window.backend.device.flags.line_correct
                        enabled: window.editable
                        onToggled: {
                            if (checked !== window.backend.device.flags.line_correct)
                                window.backend.set_flag("line_correct", checked)
                        }
                    }
                }
            }

            Panel {
                Label { text: "固件升级"; font.pixelSize: 16; font.weight: Font.DemiBold; color: window.textColor }
                Label {
                    text: "固件升级需要把鼠标切换到 USB 有线模式并用数据线连接。升级开始前会自动备份原 Flash，过程中请勿断开 USB。"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Label {
                    Layout.fillWidth: true
                    text: "当前鼠标固件 " + window.backend.device.firmware
                        + " · 接收器固件 " + window.backend.device.receiverFirmware
                    color: window.textColor
                    wrapMode: Text.WordWrap
                }

                Label {
                    Layout.fillWidth: true
                    text: window.backend.firmwareFileInfo.length > 0
                        ? window.backend.firmwareFileInfo : "未选择固件文件"
                    color: window.mutedColor
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }

                CheckBox {
                    objectName: "forceUpgrade"
                    text: "强制重刷相同版本 / 降级"
                    visible: window.backend.firmwareReady
                    checked: window.backend.forceUpgrade
                    enabled: !window.backend.busy
                    onToggled: window.backend.setForceUpgrade(checked)
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Button {
                        objectName: "chooseFirmware"
                        text: "选择固件…"
                        enabled: !window.backend.busy
                        onClicked: window.backend.choose_firmware()
                    }

                    Button {
                        objectName: "downloadFirmware"
                        text: "在线获取固件"
                        enabled: !window.backend.busy
                        onClicked: window.backend.download_firmware()
                    }

                    Button {
                        objectName: "applyFirmware"
                        text: "开始升级"
                        enabled: !window.backend.busy && window.backend.firmwareReady
                            && (!window.backend.firmwareNeedsForce
                                || window.backend.forceUpgrade)
                        onClicked: upgradeDialog.open()
                    }
                }

                ProgressBar {
                    objectName: "upgradeProgress"
                    Layout.fillWidth: true
                    visible: window.backend.upgrading
                    from: 0
                    to: 100
                    value: window.backend.upgradeProgress
                }
            }

            Label {
                objectName: "operationStatus"
                Layout.fillWidth: true
                Layout.bottomMargin: 18
                text: window.backend.message
                wrapMode: Text.WordWrap
                color: window.mutedColor
            }
        }
    }

    Dialog {
        id: upgradeDialog
        objectName: "upgradeDialog"
        title: "确认升级鼠标固件"
        modal: true
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: Math.min(window.width - 48, 480)

        contentItem: ColumnLayout {
            width: Math.min(window.width - 96, 432)
            spacing: 10

            Label { text: "即将写入以下固件："; color: window.textColor }
            Label {
                text: window.backend.firmwareFileInfo
                color: window.mutedColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                text: "请确认鼠标已通过 USB 数据线连接。升级过程中不要断开 USB 或退出应用。升级前会自动把原 Flash 备份到用户主目录。"
                color: window.mutedColor
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        onAccepted: window.backend.upgrade_firmware()
    }

    component Panel: Rectangle {
        default property alias items: body.data
        Layout.fillWidth: true
        implicitHeight: body.implicitHeight + 28
        radius: 14
        color: window.surface
        border.color: window.borderColor

        ColumnLayout {
            id: body
            width: parent.width - 32
            x: 16
            y: 14
            spacing: 4
        }
    }

    component Metric: ColumnLayout {
        required property string title
        required property string value
        Layout.fillWidth: true
        spacing: 5

        Label { text: parent.title; color: window.mutedColor }
        Label {
            text: parent.value
            color: window.textColor
            font.pixelSize: 15
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
