# 英菲克 X9 PRO 的 Linux HID 协议

[English](x9-pro-protocol.en.md) · 中文

> 本文档是非官方逆向分析记录，与英菲克（INPHIC）官方无关。协议内容由厂商公开
> 发布的 Windows 驱动和 Linux 实机观察整理而来，仅用于互操作性研究；厂商名称与
> 商标归其所有者所有。详见 [NOTICE.md](../NOTICE.md)。

分析对象为 Windows 安装包 `INPHIC YU_SetupV2.2.11.exe`。本机 USB 描述符与安装包里的 **HS6219 2.4G 接收器**分支一致。厂商命令从接口 2 发出，使用 Report ID `0x0e`，主机写 16 字节，设备回 32 字节，长度均包含 Report ID。已在 Linux 上验证 X9 PRO 的在线型号、固件、DPI 查询，以及 DPI 档位和 2.4G 回报率的切换、回读与恢复。

## 1. 设备识别

| 对象 | 已取得的证据 | Linux 端判断 |
| --- | --- | --- |
| 本机 2.4G 接收器 | USB `32c2:0027`，`bcdDevice=2.00`，产品字符串 `HS6219 2.4G Wireless Receiver` | 用 VID/PID 找到接收器，再验证配置接口 |
| Windows 配置接口 | `DongleDriver.Info` 匹配 `32c2:0027`、Usage Page `0xff0f`、Usage `0x01` | 匹配同一顶层 HID 集合和报告长度 |
| 接收器型号 | 本机 `0x21` 回包确认 FWID 为 `0x01660004`，运行固件为 `0x0902`。内置型号表对应 `BYKC 6219 Dongle`，资源版本为 `0x0402` | 已通过 Linux `hidraw` 查询确认 |
| X9 PRO 鼠标 | 在线 `0x61` 回包确认 FWID `0x00710106`、运行固件 `0xeb02`，对应安装包内的 `X9`。接收器 `0x2d` 返回同一 FWID 和配对记录版本 `0x0402` | 以 `0x61` 确认在线型号和运行固件。配对记录可用于离线时显示型号 |
| USB 直连鼠标 | Windows 通用 `MouseDriver.Info` 匹配 `32c2:0024`、`0xff0f:0x01` | `NewMouseDriver` 使用 32 字节输入/输出、Report ID `0x0e`。日常配置见第 8.1 节，固件升级用 `0x1e` 进入 Bootloader |
| 蓝牙 | 实机名称 `inphicMouse`，BlueZ PnP ID `32c2:0026`，Linux HID ID `0005:00000000:00000000` | 读取连接与 BlueZ 电量。实测 GATT 和 HID 报告未暴露厂商配置通道 |

英菲克下载页将 X9 PRO 列入这套无线鼠标驱动的支持范围。产品页标称最高 12,000 DPI，本安装包的 `X9` 资源列出 12,800 DPI。两项资料的适用硬件或固件版本待实机核对。Linux 端先读取 `0x61`、`0x62` 和可用的 `0x63`，再选择显示值和可写档位。 [英菲克驱动下载](https://www.inphic.cn/download) · [X9 PRO 官方产品页](https://www.inphic.com/zh-hans/products/inphic-x9-pro-vertical-ergonomic-precision-dual-mouse)

另核对了 X9 PRO 的旧版 `INPHIC SZ_SetupV1.0.13.exe`。两版驱动的
`NewManager` 和 `MouseDriver.Info` 都使用 `32c2:0024`、
`0xff0f:0x01` 匹配通用 HID 鼠标，未找到独立的蓝牙配置入口。
[英菲克旧版驱动下载页](https://www.inphic.cn/download?pg=3)

2026-11-23 的蓝牙实机枚举确认，设备只公开标准 GATT 服务 `1800`、`1801`、
`180a`、`180f` 和 `1812`。从 `2a4b` 读取的 161 字节报告映射与内核
HID 描述符一致。报告引用为输入 `01`（鼠标）、`02`（媒体控制）、
`03`（键盘）和输出 `03`（键盘指示灯）。本次没有看到厂商 Usage Page、
报告 `0x0e` 或 Feature Report。电量由 BlueZ 的 `org.bluez.Battery1` 提供，
实机读取为 100%。

## 2. Linux HID 传输

| USB 接口 | 顶层集合 | USB 端点 | 本机节点示例 |
| --- | --- | --- | --- |
| 0 | 启动键盘 | `0x81` Interrupt IN | `/dev/hidraw2` |
| 1 | 启动鼠标 | `0x82` Interrupt IN | `/dev/hidraw3` |
| 2 | 键盘、Consumer Control、厂商自定义 `0xff0f:0x01` | `0x83` Interrupt IN，最大包 32 字节 | `/dev/hidraw4` |

接口 2 的报告描述符长 86 字节。厂商集合的原始片段如下。

```text
06 0f ff 09 01 a1 01 85 0e 09 02 15 00 26 ff 00
75 08 95 1f 81 02 09 02 15 00 26 ff 00 75 08 95
0f 91 02 c0
```

- 带编号的 Output Report 是 `0e` 加 15 字节。设置 `tx[0]=0x0e`、`tx[1]=命令`，按下面各表填写字段，其余置零。Windows 的 Go 代码构建 16 字节输出缓冲，内置 HID 库最终走 `WriteFile`。
- 带编号的 Input Report 是 `0e` 加 31 字节。接口 2 也有 `0x01` 和 `0x05` 的输入报告。读循环先检查 Report ID 和实际长度。
- Linux 可直接使用 `hidraw` 的 `write()` 和 `read()`，保留现有 `hid-generic` 对标准鼠标和键盘集合的处理。接口 2 没有 Interrupt OUT，因此 `write()` 按内核 HIDRAW API 走控制端点的 `SET_REPORT`。本接口的预期 USB setup tuple 为 `bmRequestType=0x21, bRequest=0x09, wValue=0x020e, wIndex=0x0002, wLength=16`，待 USB 抓包确认。 [Linux 内核 HIDRAW 文档](https://docs.kernel.org/hid/hidraw.html)
- Windows 的普通命令在一次写入后读回包，通常按前两个字节匹配，并最多尝试 3 次。接收器查询单次读等待 100 ms，转发到鼠标的查询通常为 300 ms。普通命令构造里未见校验和或序列号。后台读循环还将 `0e 20` 和 `0e 60` 分发为状态更新。Linux 端应由同一读循环处理收包和请求匹配，按设备串行发送。对 `0x20`、`0x60`，前缀匹配只能证明收到对应类型的状态，配置写入要核对回读字段。

## 3. 只读命令

下文用 `b[n]` 表示报文中从 0 开始的字节。请求前缀均右补 `00` 到 16 字节。普通回包的 `b[0:2]` 回显请求的 Report ID 和命令。切片右端不包含在内。

| 请求前缀 | 读取内容 | 回包字段 |
| --- | --- | --- |
| `0e 21` | 接收器型号和运行固件版本 | `b[14:18]` 为大端 FWID，`b[18:20]` 为大端版本 |
| `0e 20` | 接收器维护的在线、电量和充电状态 | 鼠标在线为 `b[9]` bit 7，充电为 `b[8]` bit 7，电量为 `b[8] & 0x7f`。键盘对应 `b[11]` 和 `b[10]` |
| `0e 2d` | 接收器记录的鼠标身份 | `b[2:6]` 为小端 DeviceID。将 `b[8:10]`、`b[10:12]` 分别读作小端 16 位字，依次拼为 FWID 的高 16 位和低 16 位。`b[12:14]` 为大端版本 |
| `0e 2e` | 接收器记录的键盘身份 | 字段同 `0x2d`，X9 PRO 配置无需查询键盘身份 |
| `0e 61` | 配对鼠标的型号和运行固件版本 | `b[14:18]` 为大端 FWID，`b[18:20]` 为大端版本。X9 候选 FWID 的字节为 `00 71 01 06` |
| `0e 60 00 00` | 当前鼠标设置 | 配置模式、各模式的 DPI 档和回报率、灯效、休眠等，见下表 |
| `0e 62 00` | 传感器与当前 DPI 值列表 | `b[2]` 为 SensorID，`b[3:11]` 为 8 个 DPI 值代码，`b[11]` 为 DPIType |
| `0e 63` | 传感器支持的 DPI 表 | 按此前 `0x62` 返回的 DPIType 解码 |

Windows 将 `0x2d` 的 FWID 和 DeviceID 纳入接收器状态，并通过 `0x61` 单独填写鼠标的型号信息。两种回包的 FWID 编码不同。

### `0x60` 当前鼠标设置回包

| 字节 | 按 Windows 解码 |
| --- | --- |
| `b[2]` | 低 2 位为配置模式，值域 0–3 |
| `b[4:8]` | 4 个配置模式各占 1 字节。低 3 位为 DPI 档位索引，bits 6–7 为 USB 回报率，bits 4–5 为 2.4G 回报率 |
| `b[8]` | 低 5 位为灯效编号，bit 5 为方向。Windows 在报文 bit 6 为 0 时，对内部 `BLMode` 置 bit 6 |
| `b[10]` | 低 3 位为亮度，bits 4–6 为速度 |
| `b[11]` | 低 3 位为颜色索引 |
| `b[12]` | 低 3 位为 2.4G 一级休眠代码，bits 4–6 为 2.4G 深度休眠代码 |
| `b[13]` | 低 3 位为蓝牙一级休眠代码，bits 4–6 为蓝牙深度休眠代码 |
| `b[14]` | 低 2 位为 `SilentAltitude`，bits 4、5、6、7 依次为低延迟、低功耗、波纹控制、移动唤醒 |
| `b[15]` | bit 0 为 `LineCorrect` |

Windows 前端将回报率代码 `0/1/2/3` 依次显示为 `125/250/500/1000 Hz`。USB 端点的 `bInterval=1` 是枚举参数。确认鼠标实际回报率需测量移动输入包间隔。

### `0x63` DPI 表解码

- `DPIType=1` 时，从 `b[2]` 起按 3 字节一项，最多 10 项。第 `i` 项的 DPI 为小端 `uint16(b[2+3*i:4+3*i])`，值代码为 `b[4+3*i]`。DPI 值为 0 时停止。按此布局，2400 DPI 和代码 `0x30` 编码为 `60 09 30`，这是示例数据。
- `DPIType=2` 时，`b[2:4]`、`b[4:6]`、`b[6:8]` 分别为小端 16 位 DPI 步长、档数和起始 DPI。第 `i` 档从 0 开始，DPI 为 `起始 DPI + i*DPI 步长`，值代码为 `(b[8] + i*b[9]) & 0xff`。
- Windows 在 `DPIType` 为 1 或 2 时调用 `0x63`。本机 X9 PRO 的 `0x62` 回包给出 `SensorID=0x10`、`DPIType=2`，当前值代码为 `0e 1e 2e 3e 5e fe ff ff`。`0x63` 实机回包的 `b[2:10]` 为 `32 00 ff 00 64 00 00 01`，给出起始 100 DPI、步长 50 DPI、共 255 档，以及从 0 开始、每档加 1 的代码。对代码 `0x00–0xfe`，`DPI = 100 + 50 × 值代码`。本机六个已启用档位依次为 800、1600、2400、3200、4800、12800 DPI，后两项 `0xff` 超出此表的有效代码范围。`0x62` 的 `b[12:]` 尚未纳入已验证的字段。

### 安装包内 X9 默认配置

| 档位 | DPI | 值代码 | 默认颜色 |
| ---: | ---: | ---: | --- |
| 1 | 800 | `0x10` | 红 |
| 2 | 1600 | `0x20` | 绿 |
| 3 | 2400 | `0x30` | 蓝，默认选中 |
| 4 | 3200 | `0x40` | 品红 |
| 5 | 4800 | `0x60` | 黄 |
| 6 | 12800 | `0x80` | 青 |

`config.json` 将传感器标为 S205，并把 `Advance` 里的 `SilentAltitude` 与 `UltralowDelay` 设为 `false`，其余 `UltraLowPower`、`RippleControl`、`LineCorrect`、`MoveWakeUp` 键直接缺失。界面按「`Advance` 对应键为真才渲染」处理，因此 X9 PRO 的厂商界面不显示这六项高级开关，只保留两条回报率、两级休眠和按键宏页面；固件本身仍接受 `0e 60 40 00` 的写入并回读（见第 4 节实机补充）。默认 `profile.json` 的 USB 和 2.4G 四个配置模式均使用回报率代码 3。灯效编号 `1–5` 对应关闭、闪烁、呼吸、霓虹、常亮。上述值来自软件资源。

## 4. 设置命令

表内 `i` 为配置模式索引 `0–3`。每行从全零的 16 字节缓冲区构建。Windows 给下列 `0x60` 请求使用同一解析器处理应答，写入后可发 `0e 60 00 00` 回读。

| 操作 | 请求布局 | 证据 |
| --- | --- | --- |
| 切换配置模式 | `0e 60 [0x20 \| (mode & 3)] 00` | `DongleMouseDevice.SetMode` |
| 更新 DPI 值列表 | `0e 62 80`，`b[3:11]` 为 8 个值代码，不足 8 项时以 `ff` 补齐 | `SetDPI` 先调 `CMD_GetMouseDPIInfo(ctrl=0x80)`，成功后继续下行 |
| 设置各模式当前 DPI 档 | `0e 60 00 0f`，`b[4+i]=level[i] & 7` | `CMD_SetMouseDPILevels` |
| 设置回报率 | `0e 60 00 f0`，`b[4+i]=((usb[i] & 3)<<6) \| ((wl[i] & 3)<<4)` | `CMD_SetMouseReportRate`，应先回读两个链路各 4 项，再改变目标配置项 |
| 设置灯效 | `0e 60`，`b[8]=(BLMode & 31) \| ((BLDirection & 1)<<5) \| (BLMode & 0x40 ? 0x80 : 0xc0)`，`b[10]=0x88 \| (Brightness & 7) \| ((Speed & 7)<<4)`，`b[11]=0x08 \| (Color & 7)` | `CMD_SetMouseLEDEffect` |
| 设置休眠和高级功能 | `0e 60 40 00`，按上表填 `b[12:16]`，其余保留零 | `CMD_SetMouseAdvanceSetting` |
| 重新载入 Flash 配置 | `0e 60 80 00` | `SetConfigData` 和 `SetKeyDefine` 在写 Flash 后发送，实际持久化行为待验证 |

### 实机补充：灯效与高级设置（2026-11-21 验证）

以下结论来自 Linux 实机写入与回读，位布局与 `CMD_SetMouseLEDEffect`、`CMD_SetMouseAdvanceSetting` 的反汇编一致。

| 项 | 结论 |
| --- | --- |
| 灯效请求的 `b[8]` 高 2 位 | 必须固定写 `0xc0`。驱动按 `BLMode & 0x40` 分支写 `0x80` 或 `0xc0`，但接收器回包的高 2 位恒为 `01`，所以只发 `0x80` 时回读必然与本机内部值不一致。实机 `b8=c5` 对应编号 5（常亮）。 |
| 亮度与速度的互反 | 协议 `Speed` 与厂商界面的滑块值互为反码：界面 `0`（最慢）对应协议 `Speed=3`，界面 `3`（最快）对应 `Speed=0`。两者值域都是 `0–3`，但 `b[10]` 仅用低 3 位、bits 4–6，因此 `Brightness = b[10] & 7`、`Speed = (b[10] >> 4) & 7`。 |
| X9 不显示方向与颜色 | `0x00710106/config.json` 的 `LETable` 里只有「常亮模式」带 `MoreColor: 4`，且 `Config` 位掩码没有给出方向（`4`）。实测 `b8` 的 bit 5 方向位与 `b[11]` 颜色索引可以写入并回读，但厂商界面不向 X9 用户开放；每档颜色实际保存在 Flash 配置区（见第 5 节）。 |
| 关闭灯光仍接受亮度/速度 | 灯效编号 `1`（关闭）时下发亮度或速度，回读会保留新值，不会因为灯是灭的而丢字段，因此可以安全地先设数值再切模式。 |
| 休眠与高级设置的字段确认 | `0x12` 对应一级 `2`（60 秒）、深度 `1`（10 分钟），与厂商 `profile.json` 的 `WLPrimarySleep=2`、`WLDeepSleep=1` 相同。`b[14]=0x01` 对应 `SilentAltitude=1`（即界面上的 `2 MM`）。`AdvanceSetting` 里的 `UltraLowDelay`、`UltraLowPower`、`RippleControl`、`MoveWakeUp` 分别映射 `b[14]` 的 bit 4、5、6、7，`LineCorrect` 映射 `b[15]` bit 0。 |
| 配置模式请求 | 厂商 `SetMode` 复用 `CMD_GetMouseDeviceInfo(0x20 \| mode, 0)` 这条通道发送，而不是另行构建报文；Linux 端照此写 `0e 60 20\|mode 00`，回读 `b[2]` 只变化低 2 位。 |
| 休眠时间会立刻生效 | 把一级休眠改成 10 秒后，鼠标随即进入休眠，`0x60` 查询开始超时，直到鼠标被唤醒。因此验收脚本必须按「写入 → 回读 → 立刻恢复」的节奏执行，否则下一次查询会失败。 |
| 回读校验只能比目标字段 | 接收器对连发写入的落实有可见延迟：刚写完 `00 0f`（DPI 档位）时，同一回包里 `b[8]`/`b[10]`/`b[12]` 仍可能是上一事务的旧值。若校验要求整份设置全等，正常写入也会被判失败并触发回滚。实测 200 次随机写入按字段校验零失败，按整份全等校验在 150 次里稳定出现 1 次误判。 |

### 传输层注意事项：必须丢弃残留应答

`0x60` 的每一次请求（读状态、`00 0f` 写档位、`00 f0` 写回报率、`40 00` 写高级设置、`20|mode` 切模式）都会回一帧 `0e 60` 开头的应答，且报文没有序列号或事务 ID。如果写操作只写不读，这一帧会留在 hidraw 缓冲区里，随后 `0x60` 的读请求会把旧应答当成自己的回包。

实测表现：先写灯效再回读，读到的仍是写入前的值，于是误判写入失败并触发回滚；而实际上设备早已接受写入。解决办法是在每次事务前用非阻塞 `select` 把缓冲区读空，并要求写操作自己也消费掉对应应答。Linux 端按此修正后，写入回读稳定一致。

### 实测回读的原始报文

```text
基线                        0e 60 00 83 c1 f2 f2 f2 41 00 13 07 12 12 01 00
灯效 5/亮度 2/速度 2        0e 60 00 83 c1 f2 f2 f2 45 00 22 07 12 12 01 00
一级休眠改成 10 秒           0e 60 00 83 c1 f2 f2 f2 41 00 13 07 10 12 01 00
高级开关全开                0e 60 00 83 c1 f2 f2 f2 41 00 13 07 12 12 f1 01
配置模式切到 3               0e 60 02 83 c1 f2 f2 f2 41 00 13 07 12 12 01 00
模式 2 有线回报率改 500Hz    0e 60 00 83 c1 b2 f2 f2 41 00 13 07 12 12 01 00
模式 4 2.4G 回报率改 250Hz   0e 60 00 83 c1 f2 f2 d2 41 00 13 07 12 12 01 00
启用第 7、8 档 (0x62)       0e 62 10 0e 1e 2e 3e 5e fe 70 80 02 20 03 40 06
全部恢复后                  0e 60 00 83 c1 f2 f2 f2 41 00 13 07 12 12 01 00
```

第一轮实机验证可用单个模式的 2.4G 回报率做可逆变更，确认后写回原值。DPI 列表变更包含两次请求，若第二次失败，回读两组状态以识别部分成功。

本节三条结论（丢弃残留应答、按目标字段校验、休眠时间立刻生效）是 Linux 端能稳定写入的前提，缺一条都会表现为"写入被设备忽略"的假故障。

## 5. 按键、宏与 Flash

安装包默认按键配置给出的索引和值如下。滚轮上、下各有一个独立的映射项。

| 索引 | 功能 | 默认值 |
| ---: | --- | --- |
| 0 | 左键 | `0x4181` |
| 1 | 右键 | `0x4182` |
| 2 | 中键 | `0x4184` |
| 3 | 后退 | `0x4188` |
| 4 | 前进 | `0x4190` |
| 7 | DPI 循环 | `0x4802` |
| 8 | 滚轮上 | `0x4201` |
| 9 | 滚轮下 | `0x42ff` |

Windows 的 `SetKeyDefine` 从鼠标 Flash 地址 `0x0000` 写入编码后的按键和宏。配置区位于 `0x1000`，`GetConfigData` 读取 256 字节，`SetConfigData` 先读后更新再写。构造函数给出 `layerSize=1024`、`bankSize=256`、`fifoSize=256`、单包 FIFO 数据最多 11 字节、单包 Flash 读取最多 27 字节。

### Flash 内容

- **按键区**有 4 层，每层 1024 字节，构建时以 `ff` 填充。第 `layer` 层的基地址为 `layer*0x400`。普通键的动作码为小端 16 位，写在层内偏移 `2*Index`，索引范围 0–11。以默认左键 `0x4181` 为例，层头的两个字节为 `81 41`。已配置层在偏移 `0x1e` 写层索引，在 `0x1f` 写 `0x5a XOR 层头前 31 字节`。
- **宏记录**与键表同层，从偏移 `0x20` 起分配。按键表中的 16 位动作码使用 `0x8000` 位标记宏指针，宏记录首字节是 `0x5a`，操作从记录偏移 `0x10` 起按小端 16 位写入。重复模式也会修改宏指针和记录尾部。宏的完整操作码和重复行为尚未通过实机样本确认。
- **配置区**内偏移 `0x02`、`0x03` 保存临时 DPI 增、减的值代码。DPI 档位颜色在 `0x14+8*i` 写入三字节 RGB，常亮颜色在 `0x54+8*i` 写入三字节 RGB，两者的 `i` 范围均为 0–7。编码结束后，`0xfe` 保存以 `0x5a` 为初值异或前 254 字节的结果，`0xff` 写 `1`。布局来自 `encodeConfigData`，其余字节通过先读后写保留。

### Flash 传输

| 命令 | 请求前缀或字段 | Windows 验证 |
| --- | --- | --- |
| `0x52` Flash 读 | `0e 52 addr_lo addr_hi len` | 回显前 5 字节，`b[5:]` 是数据，调用方按 `len` 取值 |
| `0x51` 写 FIFO | `0e 51 addr_lo addr_hi data...` | 比较前 `4+len(data)` 字节 |
| `0x50` 提交 Flash 写入 | `0e 50 addr_lo addr_hi len a5 checksum_lo checksum_hi` | 回显前 5 字节，`b[5]==1` 表示成功 |
| `0x53` 擦除 bank | `0e 53 bank (bank XOR ff) 55 aa` | 回显前 3 字节，`b[4]==1` 表示成功 |

提交校验和为 `(0xfa21 + sum(数据块中的小端 16 位字)) & 0xffff`。高层写流程会比较已有数据，并在覆写有变化的 bank 时执行擦除。按键和宏在取得实机只读备份、验证布局和恢复策略之后再开放写入。Windows 另有鼠标重置 `0x5f` 和接收器转 Boot `0x1e`；BootV1 升级流程见第 8 节。

## 6. 实机确认步骤

用户在 Linux 终端使用 `/dev/hidraw4` 验证了接收器通道。`0x21` 回包的 `b[14:20]` 为 `01 66 00 04 09 02`。`0x2d` 的 `b[8:14]` 为 `71 00 06 01 04 02`，解得配对鼠标 FWID `0x00710106`、配对记录版本 `0x0402`。离线时 `0x20` 的 `b[8:10]` 为 `64 0c`，在线位为 0，电量字段为 100，`0x61` 在三次尝试后超时。

等待同一接收器的标准鼠标接口收到输入后，在线位变为 1，鼠标查询均收到 32 字节回包。

| 回包 | 实机字段 | 解码 |
| --- | --- | --- |
| `0x20` | `b[8:10] = 64 8c` | 在线、电量 100、充电位为 0 |
| `0x61` | `b[14:20] = 00 71 01 06 eb 02` | 鼠标 FWID `0x00710106`，运行固件 `0xeb02` |
| `0x60` | `b[2] = 00`，`b[4:8] = e2 f2 f2 f2` | 配置模式 0，四个模式的 DPI 档位索引均为 2。四个模式的 USB 回报率设置均为 1000 Hz，2.4G 设置依次为 500、1000、1000、1000 Hz |
| `0x62` | `b[2:12] = 10 0e 1e 2e 3e 5e fe ff ff 02` | 传感器 `0x10`，8 个当前 DPI 值代码，表类型 2 |

Windows 的 `Manager.ReadData.func1` 在 `0x0073b437` 读取鼠标在线状态，置位后才为新上线的鼠标创建 `DongleMouseDevice`。实机可先确认标准鼠标接口收到输入，再立即查询配置通道，以判断普通移动和配置转发是否同时工作。

### 设置写入验收

已在 Linux 普通用户会话上使用 [udev 规则](../udev/70-inphic-x9-pro.rules) 完成下列操作。每项写入后先回读改动，再恢复原值并复核四个模式的 DPI 档位及 USB、2.4G 回报率。写入前重新读取的基线是模式 0、2400 DPI、2.4G 125 Hz。

| 验收项 | 临时改动 | 恢复后回读 |
| --- | --- | --- |
| 当前 DPI 档位 | `0x60 00 0f` 将索引 2 改为 0，对应 2400 → 800 DPI | 索引 2，2400 DPI |
| 自定义 DPI 值 | `0x62 80` 将第 3 档代码 `0x2e` 改为 `0x2f`，再用 `0x60 00 0f` 应用档位，对应 2400 → 2450 DPI | 代码 `0x2e`，2400 DPI |
| 当前 2.4G 回报率 | `0x60 00 f0` 将代码 0 改为 1，对应 125 → 250 Hz | 代码 0，125 Hz |

最终回读的四个 DPI 档位索引均为 2，USB 回报率代码均为 3，2.4G 回报率代码为 `0,3,3,3`。`0x62` 读到的八个 DPI 值代码与基线相同。

用户重插接收器后，Linux 收到配置接口的移除和新增事件，udev 自动重新授予普通用户权限，`0x60`、`0x62` 和 `0x63` 均能读取。鼠标静置期间，`0x20` 的在线位从 1 变为 0。唤醒后在线位恢复为 1，成功回读 2400 DPI 和 2.4G 125 Hz。

### 实机探测

1. 找到 `32c2:0027` 接收器的 USB 接口 2 和对应的 hidraw 节点。节点编号随枚举变化。使用已有设备访问权限，或通过只匹配该接收器配置接口的 udev 规则给本地用户授权。
2. 将鼠标切到 2.4G 模式并唤醒。按 Windows 接收器初始化顺序发 `0x21`、`0x20`、`0x2d`、`0x2e`，再发鼠标型号查询 `0x61`。核实鼠标在线位、配对记录和运行固件版本。若 `0x2d` 已返回非零 FWID、`0x61` 超时，记录配对 FWID 作为型号判断的旁证。
3. 读 `0x60` 和 `0x62`。DPIType 为 1 或 2 时再读 `0x63`。用鼠标 DPI 键切一档并复读 `0x60`，核实档位索引变化。用可恢复的回报率变更验证设置命令，写回原值并复读。
4. 需要核对 USB 层时，在 Linux 抓 `usbmon` 或通过 Windows USBPcap 记录同一操作，分别匹配控制 OUT 和接口 2 的 Interrupt IN。保留配对鼠标输出的实际报文供离线测试。

以下示例查询接收器和配对设备。第一个设备参数是配置节点。若再传入同一接收器的标准鼠标节点，例如 `/dev/hidraw3`，脚本会等待鼠标输入再查询在线状态、型号、当前配置和 DPI。脚本核对 sysfs 身份和报告描述符后才访问节点。

```bash
python3 - /dev/hidraw4 <<'PY'
import os
from pathlib import Path
import select
import sys
import time

node = Path(sys.argv[1])
device = Path("/sys/class/hidraw") / node.name / "device"
info = (device / "uevent").read_text().splitlines()
descriptor = (device / "report_descriptor").read_bytes()
vendor_collection = bytes.fromhex(
    "06 0f ff 09 01 a1 01 85 0e 09 02 15 00 26 ff 00 "
    "75 08 95 1f 81 02 09 02 15 00 26 ff 00 75 08 95 0f 91 02 c0"
)
if node.parent != Path("/dev") or "HID_ID=0003:000032C2:00000027" not in info:
    raise SystemExit("设备标识未匹配 32c2:0027")
if not descriptor.endswith(vendor_collection):
    raise SystemExit("厂商 HID 集合未匹配")

def query(handle, opcode, timeout):
    request = bytes((0x0e, opcode)) + bytes(14)
    for _ in range(3):
        if handle.write(request) != len(request):
            raise SystemExit("发送报告时发生短写")
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            if not select.select([handle], [], [], remaining)[0]:
                break
            response = os.read(handle.fileno(), 32)
            if len(response) == 32 and response[:2] == request[:2]:
                return response
    return None

with open(node, "r+b", buffering=0) as handle:
    opcodes = (0x21, 0x20, 0x2d, 0x2e, 0x61)
    if len(sys.argv) == 3:
        mouse_node = Path(sys.argv[2])
        mouse = Path("/sys/class/hidraw") / mouse_node.name / "device"
        mouse_descriptor = (mouse / "report_descriptor").read_bytes()
        if (
            mouse_node.parent != Path("/dev")
            or mouse.resolve().parent.parent != device.resolve().parent.parent
            or not mouse_descriptor.startswith(bytes.fromhex("05 01 09 02 a1 01"))
        ):
            raise SystemExit("标准鼠标节点未匹配同一接收器")
        with open(mouse_node, "rb", buffering=0) as mouse_handle:
            print("请在 10 秒内移动鼠标", flush=True)
            if not select.select([mouse_handle], [], [], 10)[0] or not mouse_handle.read(8):
                raise SystemExit("接收器的标准鼠标接口没有收到输入")
            print("接收器的标准鼠标接口已收到输入", flush=True)
        opcodes = (0x20, 0x61, 0x60, 0x62)

    for opcode in opcodes:
        response = query(handle, opcode, 0.3 if opcode >= 0x60 else 0.1)
        if response is None:
            print(f"{opcode:02x}: 查询超时（3 次）")
            continue
        print(f"{opcode:02x}: {response.hex(' ')}")
        if opcode == 0x20:
            print(f"鼠标在线={bool(response[9] & 0x80)} 电量={response[8] & 0x7f}")
            continue
        if opcode == 0x62 and response[11] in (1, 2):
            dpi_table = query(handle, 0x63, 0.3)
            print(f"63: {dpi_table.hex(' ')}" if dpi_table else "63: 查询超时（3 次）")
        if opcode in (0x60, 0x62):
            continue
        if opcode in (0x2d, 0x2e):
            high = int.from_bytes(response[8:10], "little")
            low = int.from_bytes(response[10:12], "little")
            fwid = (high << 16) | low
            version = int.from_bytes(response[12:14], "big")
        else:
            fwid = int.from_bytes(response[14:18], "big")
            version = int.from_bytes(response[18:20], "big")
        print(f"FWID=0x{fwid:08x} 版本=0x{version:04x}")
PY
```

## 7. 证据定位与复现

| 对象 | SHA-256 |
| --- | --- |
| 输入安装包 | `e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035` |
| 解出的 `app/INPHIC 英云.exe` | `5973d676a8b02b9d2aefde30c9c14b7082df3d3ec5caeaea433810362918897b` |
| 接收器接口 2 的 `report_descriptor` | `208043d95fd5458b1997d7b1c36de917f66031320add7a020618861ebb597393` |

安装包为 Inno Setup 6.3.0。使用上游 `innoextract` 开发版提交 `6e9e34ed0876014fdb46e684103ef8c3605e382e` 离线解包，清单仅列出主程序和图标。主程序为 Go 1.24.2 的 Windows x86 PE，模块 `smurfs`，嵌入 Electron 界面和 `github.com/karalabe/hid` 的本地修改版。构建信息中的源码提交为 `27231a6a7e5b2b6c2b472e21591384437cb5afbe`。 [innoextract 上游](https://github.com/dscharrer/innoextract/tree/6e9e34ed0876014fdb46e684103ef8c3605e382e)

```bash
innoextract -d /tmp/inphic-yu "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"
sample='/tmp/inphic-yu/app/INPHIC 英云.exe'
go version -m "$sample"
go tool objdump -s '^smurfs/driver\.' "$sample"
objdump --dwarf=info "$sample"
nm -n "$sample" | rg 'DongleDriver|resourcesAppDevice(0x00710106|ModellistJson)'
```

`nm` 给出的 `main._resourcesAppDevice…` 数据符号是 Windows 386 Go 的切片头，包含 32 位小端指针、长度和容量。按 PE 节表将切片头地址及其数据指针换算为文件偏移，按长度取数据后解 gzip，得到 `resources/app/device/modellist.json` 和 `resources/app/device/0x00710106/` 下的配置、默认 profile 与键位。型号表数据符号地址为 `0x05b2e830`，X9 配置为 `0x05b2daf0`，默认 profile 为 `0x05b2db20`，均为该样本的 PE 首选虚拟地址。

| 地址 | 符号 | 用途 |
| --- | --- | --- |
| `0x00735f70`、`0x007391de` | `DongleDriver.Info`、`NewManager` 内联构造 | VID/PID、Usage、报告长度和 ID |
| `0x00736ab0`、`0x00736c90`、`0x00736e30`、`0x00737270` | `CMD_GetModelInfo`、`CMD_GetDeviceInfo`、`CMD_GetMouseID`、`CMD_GetMouseModelInfo` | 接收器、配对设备身份及状态 |
| `0x00736130`、`0x00736210`、`0x007365e0`、`0x007367b0` | `parseDeviceInfo`、`parseMouseDeviceInfo`、`parseMouseDPIInfo`、`parseMouseDPIs` | 状态和 DPI 回包偏移 |
| `0x00737450` 至 `0x00738560` | `DongleDriver.CMD_GetMouseDeviceInfo` 至 `CMD_WriteMouseFIFO` | 设置报文构建和回包匹配 |
| `0x00734700`、`0x00734890`、`0x00735a60`、`0x00735c60` | `DongleMouseDevice.ReadFlash`、`WriteFlash`、`SetConfigData`、`SetKeyDefine` | 分块、校验、地址和配置应用 |
| `0x007336e0`、`0x00733e50`、`0x00734040` | `DongleMouseDevice.encodeFlashData`、`encodeMacroOP`、`encodeConfigData` | 记录布局和应用层校验 |
| `0x007a0b10` | `_hid_write` | Windows 输出报告传输 |


## 8. 固件升级（BootV1）

本节来自厂商 Windows 驱动中 `BootV1Driver`、`BootV1Device`、`main.upgradeFirmware` 与 `MouseDriver.CMD_Boot` 的反汇编，以及 Linux 端 `open_inphic/firmware.py` 的离线协议回放。Linux 端尚未在实机上执行写入，实机升级前应保留备份并确认固件型号。

### 8.1 设备身份与报告长度

| 对象 | USB 标识 | HID 用法 | 报告格式 |
| --- | --- | --- | --- |
| USB 直连鼠标配置接口 | `32c2:0024` | Usage Page `0xff0f`、Usage `0x01` | Report ID `0x0e`，输入 32 字节、输出 32 字节 |
| BootV1 Bootloader | `32c2:001b` | Usage Page `0xff0e`、Usage `0x01` | 无编号报告，输入 32 字节、输出 32 字节 |
| 2.4G 接收器 | `32c2:0027` | Usage Page `0xff0f`、Usage `0x01` | Report ID `0x0e`，输入 32 字节、输出 16 字节；不能用于固件升级 |

Windows 端的 `BootV1Driver` 构造参数是 `inputLength=32`、`outputLength=33`、`reportID=0`、`writeFIFOMax=28`、`readFlashMax=28`。输出长度 33 是因为 Windows 的 HID 写入缓冲要求首字节放 Report ID；Bootloader 实际使用无编号 32 字节报告，所以 Linux 的 `hidraw` 写入从命令字节 `0x0e` 开始，共 32 字节，不带前导零。

直连鼠标的 `MouseDriver` 使用 `inputLength=32`、`outputLength=32`、`reportID=0x0e`；首字节 `0x0e` 是报告 ID，也是报文开头。

直连日常配置来自同一安装包的 `MouseDriver.CMD_GetDeviceInfo`（`0x00740720`）、
`CMD_GetDPIInfo`（`0x00740900`）和 `CMD_GetDPIs`（`0x00740bc0`）。
USB 的 `0x20`–`0x23` 分别对应接收器转发的 `0x60`–`0x63`。
配置模式、DPI、回报率、灯效和高级设置复用第 4 节的有效字段，USB 请求补齐到
32 字节。USB 的 `0x20` 回包是配置数据，项目中的电量和充电状态解析使用的是
接收器单独的 `0x20` 回包。2026-11-23 在 X9 PRO 上验证了 USB 有线型号、
固件和日常设置的读取。写入设置已通过离线回放测试。

### 8.2 固件文件头

厂商安装包内每个型号都有 `resources/app/device/0x00710106/data/firmware.bin` 一类的镜像。解压后的 X9 镜像长 40912 字节（`0x9fd0`），开头是 Cortex-M 向量表和厂商头：

| 偏移 | 长度 | 内容 |
| ---: | ---: | --- |
| `0x00` | 4 | 初始栈指针，X9 为 `00 30 00 20`（`0x20003000`） |
| `0x04` | 4 | 复位向量，X9 为 `91 00 00 00` |
| `0x10` | 4 | 小端 FWID，X9 为 `06 01 71 00`（`0x00710106`） |
| `0x14` | 2 | 大端固件版本，X9 为 `eb 02`（`0xeb02`） |
| `0x18` | 8 | ASCII 构建串，X9 为 `BYKC-V10` |

厂商所有鼠标镜像都带这个头。需要注意固件头的 FWID 不一定等于型号 ID：例如 `0x00710207` 的 M1 固件头写的是 `0x00710208`，两者在型号表里都叫 `M1`。因此兼容性校验应比较型号名，或接受同一型号的固件变体，不能只做 32 位 FWID 全等比较。

### 8.3 进入 Bootloader

鼠标必须通过 USB 数据线连接，2.4G 接收器通道不能传递 Bootloader 报文。对直连鼠标发送：

```text
0e 1e 55 aa 00 ff            （补零到 32 字节）
```

成功回包为 `0e 1e 01 ...`。设备随后断开当前 USB 身份，重新枚举为 `32c2:001b`。厂商 `main.upgradeFirmware` 先发送这条命令，再以 100 ms 间隔最多等待 30 次，然后通过 Manager 查找 `BootV1` 设备；Linux 端把等待时间放宽到 15 秒。

直连鼠标的型号和运行固件可用 `0e 21` 读取：回包 `b[14:18]` 为大端 FWID，`b[18:20]` 为大端版本。这条命令是只读的，工具在进入 Bootloader 前用它校验固件文件型号，也用于升级后回读新版本。

### 8.4 BootV1 命令

下表命令均为去掉 Report ID 后的线上格式，请求不足 32 字节时补零。回包从 `0x0e` 开始，固定 32 字节；厂商代码对回包只比较前两个字节（部分命令再检查状态字节），Linux 端额外检查了地址回显、数据长度和状态。

| 请求前缀 | 操作 | 回包字段与判定 |
| --- | --- | --- |
| `0e 20` | 读取 Bootloader 型号与版本 | `b[2:6]` 小端 FWID；`b[6:8]` 大端版本 |
| `0e 13 5a a5` | 开始升级 | 回显 `0e 13`；`b[5]==1` |
| `0e 11 addr len data...` | 写 FIFO | 回显 `0e 11 addr`；单包 `len` 最大 28 |
| `0e 10 addr_lo addr_hi len a5 cs_lo cs_hi` | 提交 FIFO 到 Flash | 回显 `0e 10` 加地址和长度；`b[5]==1`；`len` 为 `uint8`，256 字节编码为 `0` |
| `0e 12 addr_lo addr_hi len` | 读 Flash | 回显 `0e 12`；`b[4:4+len]` 为数据，单次最多 28 字节 |
| `0e 14 app_size(2) flash_size(2) app_cs(2) flash_cs(2)` | 提交升级参数 | 回显前 10 字节；无单独状态字节 |
| `0e 1e 5a a5` | 启动新固件 | 回显 `0e 1e`；`b[2]==1`；设备可能在回包前断开 |

所有多字节长度、地址和校验和在线上均为小端。`0e 10` 的校验和是该次 FIFO 数据块的 16 位校验；`0e 14` 的 `app_cs` 覆盖应用区，`flash_cs` 覆盖整片 Flash。

校验和算法与鼠标 Flash 配置区相同：

```text
checksum = (0xfa21 + sum(按小端读取的 16 位字)) & 0xffff
```

### 8.5 BootV1Device 的写入参数与布局

`BootV1Device.UpgradeFirmware` 按 Bootloader 回包 FWID 的高 24 位选择写入参数：

| BootID 家族 | appSize | flashSize | FIFO 大小 | 发送 0x13/0x14 | 保留区 |
| --- | ---: | ---: | ---: | --- | --- |
| `0x0071ff00` | `0xb000` | `0xc800` | 256 | 是 | 无 |
| `0x0166ff00` | `0xb000` | `0xc800` | 256 | 是 | 原 Flash `0xb700`–`0xb800` |
| `0x00920000`、`0x00960000` | `0x7000` | `0x7000` | 128 | 否 | 无 |

按 `BootV1Device` 的分支，X9 这类 `0x0071` 型号使用 `0x0071ff00` 家族；实机 Bootloader 的 FWID 尚未读取验证。以 40912 字节的 X9 镜像为例：

1. 不足 `appSize` 的部分补 `0xff` 到 `0xb000`，对应用区计算 `appCS`。
2. 再补 `0xff` 到 `flashSize`（`0xc800`），对整片计算 `flashCS`。
3. 从地址 0 开始按 FIFO 大小 256 字节分块，每块先以最多 28 字节的 `0x11` 包写入 FIFO，再用 `0x10` 提交。
4. 写入完成后，厂商只回读原始固件文件长度（40912 字节）做比较，不校验 0xff 填充和 Flash 顶部保留区。
5. 发送 `0x14` 提交 `appSize=0xb000`、`flashSize=0xc800`、`appCS`、`flashCS`，再发送 `0x1e 5a a5` 启动。

`0x0166ff00` 家族在计算 `flashCS` 和写入前，会把原 Flash `0xb700`–`0xb800` 的 256 字节插回镜像同一位置，避免覆盖设备参数。X9 的 Linux 写入实现沿用 `0x0071ff00` 家族，不执行这一步保留。

### 8.6 升级流程与恢复

Linux 实现（`open_inphic/firmware.py`）按以下顺序执行。升级前会通过接收器状态或直连鼠标 `0x21` 读取当前运行版本；若与镜像版本相同，默认跳过，目标更低则需 `--force`，避免无意义擦写。

1. 读取直连鼠标的 `0x21` 型号/版本，和固件文件头做型号校验；固件文件与接收器配对记录也必须一致。
2. 发送 `0x1e 55 aa 00 ff`，等待 `32c2:001b` 的 Bootloader 节点。
3. 读取 Bootloader 的 `0x20` 信息，按 BootID 选择写入参数。
4. 在发送 `0x13` 之前，完整读取原 Flash 并保存到备份文件；读取失败或备份写入失败都会中止，不擦写设备。
5. 发送 `0x13`，分块写 FIFO 和 Flash，回读原始固件字节并逐块比较。
6. 校验通过后发送 `0x14` 和 `0x1e 5a a5`，等待设备重新枚举；若出现新的直连鼠标，再读 `0x21` 确认版本。
7. 任何一步失败都不发送 `0x1e`，设备停留在 Bootloader。重新运行升级命令会自动识别 Bootloader；若没有接收器可提供型号记录，用 `--fwid 0x00710106` 指定。

这条写入路径尚未在 Linux 实机上执行过，离线回放覆盖了命令编码、FIFO 分块、256 字节长度编码、校验和、备份、回读校验和 End/App 顺序。实机升级前请确认固件来自对应型号，并保留厂商安装包和原 Flash 备份。

### 8.7 反汇编定位

| 地址 | 符号 | 用途 |
| --- | --- | --- |
| `0x0072ff30` | `BootV1Driver.Info` | Bootloader VID/PID `32c2:001b`、Usage Page `0xff0e`、Usage `0x0001` |
| `0x007390a0` | `NewManager` | 内联构造 BootV1Driver：报告长度 32/33、Report ID 0、FIFO 和读 Flash 上限 28 |
| `0x0072efb0` | `BootV1Device.UpgradeFirmware` | BootID 参数、填充、写入、回读、End/App |
| `0x00730c00` | `BootV1Driver.CMD_BeginUpgrade` | `0e 13 5a a5` |
| `0x00730560` | `BootV1Driver.CMD_WriteFIFO` | `0e 11 addr len data` |
| `0x00730770` | `BootV1Driver.CMD_WriteFlash` | `0e 10 ...` 和校验和 |
| `0x007309d0` | `BootV1Driver.CMD_ReadFlash` | `0e 12 ...` |
| `0x00730df0` | `BootV1Driver.CMD_EndUpgrade` | `0e 14 ...` |
| `0x00731070` | `BootV1Driver.CMD_App` | `0e 1e 5a a5` |
| `0x00742080` | `MouseDriver.CMD_Boot` | 直连鼠标进入 Bootloader 的 `0e 1e 55 aa 00 ff` |
| `0x00740540` | `MouseDriver.CMD_GetModelInfo` | 直连鼠标 `0e 21` 型号/版本 |
| `0x0079d420` | `main.upgradeFirmware` | 选择固件文件、发送 Boot、等待 BootV1、再次升级 |
| `0x05b2db00` | `main._resourcesAppDevice0x00710106DataFirmwareBin` | X9 固件资源的 slice 头；gzip 解压后 40912 字节，头部 FWID `0x00710106`、版本 `0xeb02` |

### 8.8 固件文件的在线获取

厂商下载页 [inphic.cn/download](https://www.inphic.cn/download) 只提供 Windows 驱动安装包，没有单独的 `firmware.bin`。V2.2.11 的直接下载地址是：

```text
https://inphic.oss-cn-shenzhen.aliyuncs.com/download/INPHIC%20YU_SetupV2.2.11.exe
SHA-256 e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035
```

安装包内 `app/INPHIC 英云.exe` 是 Go 程序，固件以 gzip bindata 资源嵌在里面。Linux 端先用 `innoextract` 解出主程序，再扫描 gzip 流，按文件头识别镜像：偏移 `0x10` 为小端 FWID，`0x14` 为大端版本，`0x18` 为 `BYKC` 构建串，并检查向量表的初始栈指针和复位向量。V2.2.11 中共有 40 个唯一固件镜像；X9 提取结果为 40912 字节、FWID `0x00710106`、版本 `0xeb02`、SHA-256 `3d47e616bc27c9bee7368776db35b76ea7f2b1a68cd6ec5c24b2ab41b8a8d510`，与第 8.2 节的头部一致。

系统 `innoextract` 1.9 只声明支持到 Inno Setup 6.0.5，实测无法解析厂商的 6.3.0 安装包。官方上游提供 1.10-dev 的 Linux 二进制：

```text
https://constexpr.org/innoextract/files/snapshots/innoextract-1.10-dev-2025-01-07/innoextract-1.10-dev-2025-01-07-linux.tar.xz
SHA-256 e4eb7e15a3b417549fd521fe011ae4e542fd8c10b62c5ddafc3b9ac599a54f50
```

工具优先使用系统 `innoextract` 1.10 或更新版本；版本过旧时下载上面的 tar.xz，校验 SHA-256，只提取当前架构的 `bin/<arch>/innoextract` 到 `~/.cache/open-inphic/tools/`。不支持自动下载时可用 `--no-helper-download` 或 `OPEN_INPHIC_INNOEXTRACT` 指定已有工具。

Linux 实现位于 `open_inphic/firmware.py`：`download_installer`、`ensure_innoextract`、`extract_app_exe`、`scan_firmware_images`、`select_firmware` 和 `fetch_firmware`。对应命令是：

```bash
python3 inphic.py fetch-firmware
python3 inphic.py fetch-firmware --installer "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"
python3 inphic.py upgrade --download --dry-run
python3 inphic.py upgrade --download
```

提取出的 `.bin` 默认保存到 `~/.cache/open-inphic/firmware/<fwid>-<version>.bin`，安装包缓存到 `~/.cache/open-inphic/installers/`。下载和提取只读取文件；真正写鼠标仍需前面的 USB 有线连接、型号校验、原 Flash 备份和 BootV1 流程。

V2.2.11 扫描到的 40 个唯一镜像全部是鼠标 `0x0071xxxx`，没有 `0x0166xxxx` 接收器镜像。驱动中的 `DongleDevice.UpgradeFirmware` 只调用 `CMD_Boot` 让接收器进入 Bootloader，厂商 UI 没有接收器升级入口，安装包也没有接收器固件资源；因此本工具目前只读取接收器版本，不提供接收器写入。若英菲克后续提供接收器 `.bin` 或工厂工具，可复用 `0x166ff00` 分支补上。

