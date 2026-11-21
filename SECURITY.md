# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
private vulnerability reporting (Security → Advisories → Report a
vulnerability) in this repository, or contact the maintainers privately.

Include the affected version, a description, and, if possible, a minimal
reproduction. Do not test against hardware you do not own.

## Scope

Security-relevant areas of this project include:

- the udev rule and permissions model (no daemon, no root execution);
- downloads: the official installer and the `innoextract` helper are fetched
  over HTTPS and verified against pinned SHA-256 hashes;
- firmware writing: model/version checks, mandatory flash backup and read-back
  verification before the new firmware is booted;
- parsing of untrusted input (firmware images, installer contents, HID
  replies).

## Supported versions

The `main` branch receives fixes. Released versions are supported on a
best-effort basis.

---

# 安全策略

## 报告漏洞

发现安全问题时请**不要**直接开公开 issue。请使用 GitHub 的私密漏洞报告
（Security → Advisories → Report a vulnerability），或私下联系维护者。

请说明受影响的版本、问题描述，并尽量提供最小复现步骤。请勿在非本人所有的硬件上
进行测试。

## 范围

与本项目相关的安全内容包括：

- udev 规则与权限模型（无常驻服务、不以 root 运行）；
- 下载：官方安装包与 `innoextract` 辅助工具均通过 HTTPS 获取，并用固定的
  SHA-256 校验；
- 固件写入：型号/版本校验、强制备份原 Flash、回读校验后才启动新固件；
- 不可信输入的解析（固件镜像、安装包内容、HID 回包）。

## 支持的版本

`main` 分支会持续修复；已发布版本按尽力而为的方式支持。
