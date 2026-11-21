# Contributing to Open INPHIC

Thanks for helping! This document is available in English and Chinese below.

## Before you start

Open INPHIC is an **unofficial** project. It talks to hardware through an
undocumented vendor protocol and can write device firmware. Please:

- Never include vendor installers, firmware images, extracted binaries, device
  serial numbers, personal paths, or secrets in issues, commits, or pull
  requests.
- Test hardware-affecting changes on your own device first, and describe what
  you tested in the pull request.
- Do not weaken the safety checks around firmware writing (model match,
  version check, flash backup, read-back verification).

## Reporting bugs

Open an issue with:

- distribution and kernel version (`uname -a`);
- the mouse model and FWID (`open-inphic models`, `open-inphic firmware`);
- the exact command and its output, with personal paths redacted;
- whether the mouse is on 2.4G or wired USB.

For firmware upgrades, also mention whether the device ended up in the
bootloader and which firmware version you flashed.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[gui,dev]'
python3 -m pytest
```

The test suite uses fake HID devices and needs no hardware. GUI tests require
PyQt6 and can run headless with `QT_QPA_PLATFORM=offscreen`.

Please keep the existing style: standard library where possible, type hints on
public functions, and tests for behaviour changes. There is no enforced
formatter; match the surrounding code.

## Pull requests

- Keep changes focused and explain the *why* in the description.
- Add or update tests for protocol, firmware, CLI and UI changes.
- Update both `README.md` and `README.zh-CN.md` when user-facing behaviour
  changes, and both protocol documents when protocol facts change.
- New model IDs must come with evidence (a device report or vendor resource)
  and a note about whether the model was tested on real hardware.
- By contributing you agree that your contribution is licensed under the
  project's MIT licence.

## Protocol and firmware findings

Protocol notes live in `docs/x9-pro-protocol.md` (Chinese) and
`docs/x9-pro-protocol.en.md` (English). When documenting a new finding,
include the observed bytes, the command/offset involved, and how it was
verified. Prefer reproducible steps over vendor binary dumps.

---

# 参与 Open INPHIC

感谢参与！以下是中文说明。

## 开始之前

Open INPHIC 是**非官方**项目，通过未公开的厂商协议操作硬件，并且可以写入设备
固件。请遵守：

- 不要在 issue、提交或 PR 中包含厂商安装包、固件镜像、提取出的二进制、设备
  序列号、个人路径或密钥。
- 涉及硬件的改动请先在自己的设备上验证，并在 PR 中说明测试内容。
- 不要削弱固件写入的安全检查（型号匹配、版本检查、Flash 备份、回读校验）。

## 报告问题

请在 issue 中提供：

- 发行版与内核版本（`uname -a`）；
- 鼠标型号与 FWID（`open-inphic models`、`open-inphic firmware`）；
- 完整命令与输出（请隐去个人路径）；
- 鼠标处于 2.4G 还是 USB 有线模式。

如果涉及固件升级，还请说明设备是否停留在 Bootloader，以及刷写的固件版本。

## 开发环境

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[gui,dev]'
python3 -m pytest
```

测试使用模拟 HID 设备，不需要硬件。界面测试需要 PyQt6，可以无显示运行
（设置 `QT_QPA_PLATFORM=offscreen`）。

请保持现有风格：优先使用标准库，公开函数加类型标注，行为变化要补测试。项目没有
强制格式化工具，与周围代码保持一致即可。

## Pull Request

- 改动尽量聚焦，并在描述中说明原因。
- 协议、固件、命令行和界面改动请补充或更新测试。
- 用户可见行为变化时同时更新 `README.md` 与 `README.zh-CN.md`；协议事实
  变化时同时更新两份协议文档。
- 新增型号 ID 必须附带证据（设备回包或厂商资源），并说明是否在实机上验证。
- 提交即表示同意以项目的 MIT 许可证授权你的贡献。

## 协议与固件结论

协议文档位于 `docs/x9-pro-protocol.md`（中文）和
`docs/x9-pro-protocol.en.md`（英文）。记录新结论时请附上实际观察到的字节、
涉及的命令或偏移，以及验证方式。优先提供可复现步骤，而不是厂商二进制转储。
