# Open INPHIC

[![Tests](https://github.com/BruceZhang1993/open-inphic-driver/actions/workflows/tests.yml/badge.svg)](https://github.com/BruceZhang1993/open-inphic-driver/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**英菲克 2.4G 无线鼠标的 Linux 配置与固件升级工具。**

[English](README.md) · 中文 · [协议文档](docs/x9-pro-protocol.md)

> **非官方项目。** Open INPHIC 与英菲克（INPHIC）、BYKC 没有任何隶属、授权、
> 赞助或支持关系。产品名称与商标归各自所有者所有。刷写固件有风险，请自行承担。
> 详见 [NOTICE.md](NOTICE.md)。

Open INPHIC 通过 HS6219 接收器的厂商配置接口与鼠标通信，命令行和 Qt 6
图形界面共用同一套 HID 传输。工具可以读取型号、固件、电量和充电状态，按厂商
格式显示固件版本（如 `V0071.0106.EB02`），通过 USB 有线连接升级鼠标固件，
并设置 DPI 档位与数值、有线/无线回报率、配置模式、灯效、休眠时间和高级功能。

读取与设置功能已在 X9 PRO（FWID `0x00710106`）实机验证。厂商驱动支持的 37 个
鼠标型号都能识别并读取状态，但只有实机验证过的型号才算已测试，目前是 X9 PRO。
固件写入流程按厂商 BootV1 协议实现，只有离线协议回放测试，尚未在 Linux 上
完成实机写入验证。

## 功能

- **设备状态**：型号、固件、电量、充电状态，以及鼠标当前是否在 2.4G 链路在线。
- **DPI**：读取传感器 DPI 表，切换当前档位，修改档位数值，停用或重新启用档位。
- **回报率**：2.4G 与有线链路均支持 125、250、500、1000 Hz，按配置模式分别保存。
- **配置模式**：四个模式，每个模式各自保存 DPI 档位和回报率；可以直接修改
  指定模式，不需要先切换过去。
- **灯效**：灯效编号、亮度、速度与方向，沿用厂商的编号方式。
- **休眠与高级功能**：一级休眠、深度休眠、鼠标抬起高度、超低延迟、超低功耗、
  波纹控制、移动唤醒和直线修正。
- **固件升级**：下载官方 Windows 安装包，提取当前型号的固件镜像，校验后备份
  原 Flash，再写入并回读校验。
- **回读确认**：每次设置写入后都回读相关字段；设备没有接受时自动尝试恢复原值。
- **图形界面与托盘**：Qt Quick 界面和托盘菜单，可快速修改常用设置、查看电量并
  在后台轮询状态。

## 支持的硬件

工具绑定 **HS6219 2.4G 接收器**（`32c2:0027`）的厂商配置接口（接口 2，
Usage Page `0xff0f`，Usage `0x01`），同时支持 USB 直连鼠标（`32c2:0024`）
及其 BootV1 Bootloader（`32c2:001b`）。

```text
接收器：0x01660003  BYKC 6239 Dongle
        0x01660004  BYKC 6219 Dongle

鼠标：  共 37 个型号，包括 A1、A9、A11、DR5/DR6/DR8、F8/F9、IN6/IN7/IN10、
        INX SE、M1/M2/M8/M9、X2、X9、YUN 和元创系列。
```

运行 `open-inphic models` 可以查看完整的 FWID 与型号对照。型号表来自厂商驱动；
未知 FWID 会直接拒绝，不做猜测。只有 X9 PRO 做过完整实测；其他型号的读取和
设置理论上可用，但尚未验证。

## 依赖

- 支持 `hidraw` 的 Linux 发行版。
- Python 3.10 或更新版本。
- 图形界面需要 PyQt6 6.6 或更新版本（Qt 6），以及带系统托盘的桌面环境。
- 固件提取需要 `innoextract` 1.10 或更新版本。1.9 等旧版本无法解析厂商的
  Inno Setup 6.3 安装包；需要时工具会自动下载官方上游的辅助二进制。

## 安装

### 从源码安装

```bash
git clone https://github.com/BruceZhang1993/open-inphic-driver.git
cd open-inphic-driver
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install '.[gui]'        # 只要命令行可以去掉 .[gui]
```

安装后入口为 `open-inphic` 和 `open-inphic-gui`。也可以直接在项目目录运行
`python3 inphic.py` 和 `python3 inphic_gui.py`。

如果发行版自带较新的 PyQt6，也可以使用系统包：

```bash
# Arch Linux
sudo pacman -S --needed python-pyqt6 qt6-declarative innoextract

# Debian / Ubuntu（包名随版本变化）
sudo apt install python3-pyqt6 innoextract
```

把 Qt 拆分成独立 QML 模块的发行版还需要安装 Qt Quick 和 Qt Quick Controls
模块（例如 Debian/Ubuntu 的 `qml6-module-qtquick-controls`）。PyPI 上的
PyQt6 wheel 已经包含这些模块。

### udev 规则

厂商接口是 `hidraw` 设备。项目提供的规则通过 `uaccess` 把访问权限授予活动的
本地登录用户，不需要常驻服务，也不需要用 root 运行工具：

```bash
sudo install -m 0644 udev/70-inphic-x9-pro.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

之后重新插拔接收器。规则覆盖：

| USB ID | 设备 | 用途 |
| --- | --- | --- |
| `32c2:0027` | HS6219 2.4G 接收器，接口 2 | 状态与全部设置 |
| `32c2:0024` | USB 直连鼠标，接口 2 | 固件升级 |
| `32c2:001b` | BootV1 Bootloader | 固件升级 |

权限授予有活动本地会话的用户。SSH 登录通常没有本地会话，`uaccess` 不生效；
这种情况下请使用本地登录，或自行编写 udev 规则。

## 命令行

```bash
open-inphic status            # 电量、DPI、回报率、灯效和高级设置
open-inphic firmware          # 鼠标与接收器固件版本
open-inphic models            # 受支持的型号与接收器
```

### 设置

```bash
# DPI：切换档位并修改数值
open-inphic set-slot 3
open-inphic set-dpi 2400 --slot 3
open-inphic disable-slot 7

# 回报率：125 / 250 / 500 / 1000 Hz
open-inphic set-rate 500                 # 2.4G，当前模式
open-inphic set-usb-rate 1000 --mode 2   # 有线，模式 2

# 配置模式（1-4）
open-inphic set-mode 3
open-inphic set-slot 5 --mode 3

# 灯效（X9 PRO 编号：1 关闭、2 闪烁、3 呼吸、4 霓虹、5 常亮）
open-inphic set-led --effect 3 --brightness 2 --speed 1

# 休眠与高级功能
open-inphic set-advance --sleep 2 --deep-sleep 1
open-inphic set-advance --lift 2 --ripple-control on --line-correct off
```

- `set-slot` 接受从 1 开始的已启用档位。`disable-slot` 把档位代码置为
  `0xff`；重新启用用 `set-dpi --slot N`。
- `set-dpi` 读取实机 DPI 表并校验数值。省略 `--slot` 时修改当前档位的数值。
- `set-rate` 与 `set-usb-rate` 分别设置 2.4G 与有线链路的回报率，接受
  125、250、500 或 1000 Hz，对应绿、蓝、黄、红指示灯。`--mode` 用 1-4
  指定配置模式，不需要先切换过去。
- `set-led --effect` 使用厂商编号：1 关闭、2 闪烁、3 呼吸、4 霓虹、5 常亮。
  `--brightness` 和 `--speed` 取 0-3（0 最暗/最慢）。`--direction` 取
  `forward` 或 `reverse`。
- `set-advance --sleep` 取 0-6（10、30、60、120、180、300、600 秒）；
  `--deep-sleep` 取 0-6（5、10、15、20、30、40、60 分钟）；`--lift` 取
  0-3（1-4 mm）。
- 高级功能开关：`--ultra-low-delay`、`--ultra-low-power`、
  `--ripple-control`、`--move-wake-up`、`--line-correct`，取值
  `on` 或 `off`。
- 接入多个匹配接收器时，可在子命令前加 `--device /dev/hidrawN`。图形界面
  也接受该参数。鼠标唤醒或接收器重插后，可点击界面上的「刷新」重新读取。
- `firmware` 与 `status` 都按厂商格式显示版本；接入 USB 直连鼠标且没有
  接收器时，`firmware` 会读取鼠标自己的型号与版本。

### 固件

```bash
# 还没有本地 .bin：在线下载官方安装包并提取
open-inphic fetch-firmware
open-inphic upgrade --download --dry-run
open-inphic upgrade --download

# 使用已下载的安装包（不联网）
open-inphic fetch-firmware --installer "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"
open-inphic upgrade --installer "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"

# 使用已有的 .bin（需要 USB 有线连接）
open-inphic upgrade --file 0x00710106.bin --dry-run
open-inphic upgrade --file 0x00710106.bin
```

## 图形界面

```bash
open-inphic-gui
# 或在项目目录
python3 inphic_gui.py
```

窗口包含设备信息，以及配置模式、DPI、回报率、灯效、休眠、高级功能和固件升级
面板。写入会回读确认；失败时工具会尝试恢复原值，并报告是否确认恢复。

关闭窗口会把应用隐藏到托盘。可以通过托盘菜单重新打开窗口、修改常用设置或退出。

## 固件版本与升级

`status` 和 `firmware` 按厂商界面的格式显示版本：`V0071.0106.EB02` 对应
FWID `0x00710106` 与固件版本 `0xeb02`，前两段为 FWID 的高低 16 位，最后
一段为版本号。接收器同样显示为 `V0166.0004.0902`。

厂商没有单独的固件下载页，`.bin` 文件嵌在 Windows 驱动安装包里。工具可以直接
从英菲克官网下载安装包（约 91 MiB），校验 SHA-256，解包后扫描并提取当前鼠标
型号的固件；也可以使用本地安装包或已有的 `.bin`。固件升级只支持 **USB 有线
连接** 的鼠标，2.4G 接收器通道不能传输固件。升级前会读取并保存原 Flash，写入
后回读校验，再提交校验和并启动新固件。

- 官方安装包地址为
  `https://inphic.oss-cn-shenzhen.aliyuncs.com/download/INPHIC%20YU_SetupV2.2.11.exe`，
  V2.2.11 的 SHA-256 为
  `e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035`。
  工具会打印并校验；如果厂商在原地址更新了安装包，会提示哈希不同但继续提取，
  最终仍由固件文件头和型号校验把关。
- 安装包是 Inno Setup 6.3.0。系统 `innoextract` 1.9 无法解析，工具优先使用
  系统 1.10 或更新版本；没有时自动下载官方 `innoextract 1.10-dev` Linux
  辅助二进制（约 1 MiB，固定 SHA-256 校验）到 `~/.cache/open-inphic/tools/`。
  不希望自动下载可加 `--no-helper-download`，或用
  `OPEN_INPHIC_INNOEXTRACT=/path/to/innoextract` 指定。
- 提取出的 `.bin` 默认保存在
  `~/.cache/open-inphic/firmware/<fwid>-<version>.bin`；安装包缓存在
  `~/.cache/open-inphic/installers/`。X9 的提取结果是 40912 字节、
  `V0071.0106.EB02`。
- 工具会校验文件头的 FWID 与版本，并用接收器记录的鼠标型号或 USB 直连鼠标的
  `0x21` 型号应答做匹配；固件文件型号不匹配时拒绝写入。
- 当前鼠标版本与目标相同时默认直接报告「无需升级」并退出，不进入 Bootloader；
  目标版本更低也默认拒绝。确需重刷或降级时，命令行加 `--force`，图形界面勾选
  「强制重刷相同版本/降级」。
- 原 Flash 默认备份到 `~/<固件名>.flash-<时间戳>.bin`，可用 `--backup`
  指定。备份是整片 Flash 的原始镜像（`0xc800` 字节，含应用区和顶部保留区），
  备份完成前不会擦写 Flash；`upgrade` 只接受厂商应用镜像（不超过 `0xb000`
  字节），完整 Flash 备份需要人工恢复。
- 同时存在多个 USB 直连鼠标或 Bootloader 时，用
  `--boot-device /dev/hidrawN` 指定目标。
- 升级失败时鼠标通常停留在 Bootloader（USB `32c2:001b`）。重新运行同一命令会
  自动识别 Bootloader；如果此时没有接收器，可在命令行用 `--fwid 0x00710106`
  指定型号。设备拔插后也可以重试。
- **接收器固件**：V2.2.11 安装包里的 40 个固件镜像全部是鼠标
  `0x0071xxxx`，没有接收器 `0x0166xxxx` 镜像；Windows 界面也只提供鼠标
  升级。工具因此只读取并显示接收器版本，不提供写入。
- 该写入流程按厂商 BootV1 协议实现，已通过协议的离线回放测试，但尚未在 Linux
  上完成实机写入验证。升级有风险，请先确认能从厂商安装包中取得对应型号的原厂
  固件，并在升级前保留备份。

## 高级功能

下表文案取自厂商驱动 `zh-CN` 语言资源，括号内是同一项的英文文案。X9 PRO 的
厂商界面并不显示这些开关：`0x00710106/config.json` 的 `Advance` 只写了
`SilentAltitude: false` 和 `UltralowDelay: false`，其余键缺失，而界面按
「键为真才显示」处理，所以六项全部隐藏。本工具按协议直接开放，写入后会回读确认。

| 开关 | 厂商标题 | 厂商说明 |
| --- | --- | --- |
| 超低延迟 | 超低延迟（Ultra Low Delay） | 开启超低延迟之后鼠标画线更及时 |
| 超低功耗 | 超低功耗（Ultra Low Power） | 更省电；**开启后回报率固定为 125 Hz**，要改回报率需先关闭 |
| 波纹控制 | 波纹控制（Ripple Control） | 开启后在高速度下做算法修正，消除波浪形抖动 |
| 移动唤醒 | 移动唤醒（Move WakeUp） | 深度休眠时可以通过移动鼠标唤醒 |
| 直线修正 | 直线修正（Line Correct） | 开启后更容易画出直线 |
| 鼠标抬起高度 | 鼠标抬起高度（Mouse LOD Altitude） | 设置鼠标抬起时的高度，取 1-4 mm |

休眠的两项也有厂商文案：标题分别是「休眠时间设置(单位:秒)」和「睡眠时间设置
(单位:分钟)」，说明是「鼠标静止大于设置时间后进入休眠状态」与「鼠标停止后
大于设置时间进入睡眠状态」。

## 系统托盘

- 关闭窗口会把应用隐藏到托盘。点击托盘图标或选择「打开设置」可恢复窗口。从托盘
  菜单选择「退出 Open INPHIC」可结束应用。正在进行的读写会先完成。
- 托盘菜单可以直接切换配置模式、DPI 档位、2.4G 回报率、有线回报率和灯效。
- 悬停可查看电量百分比、充电状态、鼠标与接收器固件版本、最近回读的档位、回报率
  和灯效。
- 托盘图标用填充量表示电量，低电量用红色，充电时叠加闪电。鼠标离线时显示未知
  电量，唤醒后会自动重新读取。后台每 8 秒查询接收器状态，正常在线时只读电量和
  在线位。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| 「未找到 2.4G 接收器」 | 重插接收器；确认 udev 规则已安装；用 `udevadm info` 查看节点。 |
| 「无法访问 /dev/hidrawN」 | 安装 udev 规则并使用本地登录；大多数 SSH 会话不适用 `uaccess`。 |
| 找到多个接收器 | 用 `--device /dev/hidrawN` 指定节点（见 `open-inphic --help`）。 |
| 鼠标离线 / 查询超时 | 移动或轻按鼠标唤醒；确认鼠标处于 2.4G 模式。 |
| 设置写入失败 | 接收器偶尔会丢帧。工具会重试并回滚；重新执行命令并查看 `status`。 |
| 固件升级被拒绝 | 型号或版本不匹配，或目标版本相同/更低；确认后才可以加 `--force`。 |
| 升级后停留在 Bootloader | 用 USB 重新连接后重新运行 `upgrade`；没有接收器时加 `--fwid`。 |
| `innoextract` 版本过旧 | 安装 1.10 或更新版本，或允许下载辅助工具，或设置 `OPEN_INPHIC_INNOEXTRACT`。 |
| 没有系统托盘 | 打开桌面的通知区域/托盘，或改用命令行。 |

## 安全与恢复

- 固件写入过程中不要断开鼠标、关闭应用或中断供电。
- 首次擦写前会保存原 Flash。请保留该备份和厂商安装包，直到确认新固件正常。
- 如果鼠标不再以鼠标设备枚举，检查是否出现 BootV1 Bootloader
  （`32c2:001b`），然后用同一个固件文件重新运行 `open-inphic upgrade`。
- 对于因使用了不匹配的固件镜像或刷写时断电而损坏的设备，本项目无法修复，
  需要厂商工具或售后服务。

## 尚未实现

按键重映射、宏编辑和每档 DPI 颜色属于鼠标 Flash 内容（`0x52`/`0x51`/
`0x50`/`0x53` 传输与配置区编码）。在完成只读备份工具和经过验证的恢复流程
之前，这些功能不会开放写入。USB 直连的完整配置界面和蓝牙模式也尚未实现；固件
升级目前只支持 USB 有线鼠标，接收器固件只显示版本，不提供写入。

## 项目结构

```text
open_inphic/
  hid.py        HID 传输、型号表、命令行
  firmware.py   固件解析、安装包提取、BootV1 升级
  gui.py        PyQt6 控制器与图形界面入口
  tray.py       系统托盘
  ui/Main.qml   Qt Quick 界面
inphic.py       从源码目录运行命令行
inphic_gui.py   从源码目录运行图形界面
tests/          单元、协议、固件与界面测试（使用模拟设备）
udev/           接收器、直连鼠标与 Bootloader 的 udev 规则
docs/           协议文档（中文与英文）
```

## 开发与测试

```bash
python3 -m pip install -e '.[gui,dev]'
python3 -m pytest
```

测试使用模拟 HID 设备，不需要真实硬件。界面测试需要 PyQt6，并且可以无显示运行；
在无头机器上设置 `QT_QPA_PLATFORM=offscreen`。

## 文档

- [X9 PRO HID 协议（英文）](docs/x9-pro-protocol.en.md)
- [英菲克 X9 PRO 的 Linux HID 协议（中文）](docs/x9-pro-protocol.md)
- [声明与非官方实现说明](NOTICE.md)

## 贡献

欢迎提交改进。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；在 issue 和 PR 中
请勿包含厂商二进制、固件镜像、个人路径或设备序列号。

## 许可证

[MIT](LICENSE) © 2026 Open INPHIC contributors。

## 免责声明

Open INPHIC 是非官方独立项目，与英菲克（INPHIC）、BYKC 及其关联公司没有隶属、
授权、赞助或支持关系。所有产品名称与商标归各自所有者所有。软件按「现状」提供，
不附带任何担保；刷写固件可能损坏硬件，风险完全由使用者承担。完整声明见
[NOTICE.md](NOTICE.md)。
