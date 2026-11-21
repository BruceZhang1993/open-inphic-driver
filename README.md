# Open INPHIC

[![Tests](https://github.com/BruceZhang1993/open-inphic-driver/actions/workflows/tests.yml/badge.svg)](https://github.com/BruceZhang1993/open-inphic-driver/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Linux settings and firmware upgrade tool for INPHIC 2.4G wireless mice.**

English · [中文文档](README.zh-CN.md) · [Protocol notes](docs/x9-pro-protocol.en.md)

> **Unofficial project.** Open INPHIC is not affiliated with, endorsed by, or
> supported by INPHIC (英菲克) or BYKC. Product names and trademarks belong to
> their respective owners. Flashing firmware is done at your own risk. See
> [NOTICE.md](NOTICE.md) for details.

Open INPHIC talks to the vendor configuration interface of the HS6219 2.4G
receiver. The command line and the Qt 6 graphical interface share the same HID
transport. The tool reads the model, firmware version, battery level and
charging state, displays firmware versions in the vendor's `V0071.0106.EB02`
form, upgrades mouse firmware over a USB cable, and changes DPI slots and
values, wired and 2.4G report rates, configuration modes, lighting effects,
sleep timers and advanced features.

Reading and writing settings were verified on a real X9 PRO (FWID
`0x00710106`). All 37 mouse models known to the vendor driver can be
recognised and read, but only hardware that has actually been tested counts as
supported; today that is the X9 PRO. The firmware write path implements the
vendor's BootV1 protocol and has offline protocol replay tests, but it has not
yet been verified by writing to real hardware on Linux.

## Features

- **Device status** — model, firmware, battery percentage, charging state and
  whether the mouse is currently online on the 2.4G link.
- **DPI** — read the sensor's DPI table, select the active DPI slot, change a
  slot's DPI value, and disable or re-enable slots.
- **Report rate** — 125, 250, 500 or 1000 Hz for the 2.4G link and for the
  wired link, per configuration mode.
- **Configuration modes** — four profiles, each with its own DPI slot and
  report rates; edit a mode without switching to it first.
- **Lighting** — effect, brightness, speed and direction, with the vendor's
  effect numbering.
- **Sleep and advanced settings** — sleep and deep-sleep timers, lift-off
  distance, ultra low delay, ultra low power, ripple control, move wake-up and
  line correction.
- **Firmware upgrade** — download the official Windows installer, extract the
  firmware image for the connected model, verify it, back up the original
  flash, write and read back the new firmware.
- **Read-back verification** — every settings write is confirmed by reading
  the affected field back, with automatic rollback if the device does not
  accept it.
- **GUI and tray** — a Qt Quick interface and a tray menu for quick changes,
  battery indication and background status polling.

## Supported hardware

The tool binds to the vendor configuration interface (interface 2, usage page
`0xff0f`, usage `0x01`) of the **HS6219 2.4G receiver** (`32c2:0027`) and
to the wired mouse (`32c2:0024`) and its BootV1 bootloader (`32c2:001b`).

```text
Receivers:  0x01660003  BYKC 6239 Dongle
            0x01660004  BYKC 6219 Dongle

Mice:       37 models, including A1, A9, A11, DR5/DR6/DR8, F8/F9, IN6/IN7/IN10,
            INX SE, M1/M2/M8/M9, X2, X9, YUN and the 元创 series.
```

Run `open-inphic models` for the full FWID-to-model table. The table comes
from the vendor driver; an unknown FWID is rejected instead of guessed. Only
the X9 PRO has been tested end to end; other models should work for reading
and settings, but have not been verified.

## Requirements

- Linux with `hidraw` support (any current distribution).
- Python 3.10 or newer.
- For the GUI: PyQt6 6.6 or newer (Qt 6) and a desktop with a system tray.
- For firmware extraction: `innoextract` 1.10 or newer. Older versions (1.9
  is still common) cannot read the vendor's Inno Setup 6.3 installer; the tool
  can download an official upstream helper binary when needed.

## Installation

### From source

```bash
git clone https://github.com/BruceZhang1993/open-inphic-driver.git
cd open-inphic-driver
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install '.[gui]'        # add .[gui] only if you want the GUI
```

This installs the `open-inphic` and `open-inphic-gui` entry points. You can
also run the launchers directly from the checkout with `python3 inphic.py`
and `python3 inphic_gui.py`.

Distribution packages are an alternative if your distribution ships a recent
PyQt6:

```bash
# Arch Linux
sudo pacman -S --needed python-pyqt6 qt6-declarative innoextract

# Debian / Ubuntu (package names vary by release)
sudo apt install python3-pyqt6 innoextract
```

Distributions that split Qt into separate QML modules also need the Qt Quick
and Qt Quick Controls modules (for example `qml6-module-qtquick-controls` on
Debian/Ubuntu). The PyQt6 wheel from PyPI already contains them.

### udev rules

The vendor interface is a `hidraw` device. The supplied rule grants the
active local session access through `uaccess`, without a daemon and without
running the tool as root:

```bash
sudo install -m 0644 udev/70-inphic-x9-pro.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Replug the receiver afterwards. The rule covers:

| USB ID | Device | Used for |
| --- | --- | --- |
| `32c2:0027` | HS6219 2.4G receiver, interface 2 | status and all settings |
| `32c2:0024` | Wired mouse, interface 2 | firmware upgrade |
| `32c2:001b` | BootV1 bootloader | firmware upgrade |

Access is granted to the user with the active local session. Over SSH there is
usually no local session, so `uaccess` does not apply; use a local login or a
custom udev rule in that case.

## Command line

```bash
open-inphic status            # battery, DPI, rates, lighting, advanced settings
open-inphic firmware          # mouse and receiver firmware versions
open-inphic models            # supported model and receiver table
```

### Settings

```bash
# DPI: choose the active slot, then change a slot's value
open-inphic set-slot 3
open-inphic set-dpi 2400 --slot 3
open-inphic disable-slot 7

# Report rate: 125 / 250 / 500 / 1000 Hz
open-inphic set-rate 500                 # 2.4G link, current mode
open-inphic set-usb-rate 1000 --mode 2   # wired link, mode 2

# Configuration mode (1-4)
open-inphic set-mode 3
open-inphic set-slot 5 --mode 3

# Lighting (X9 PRO numbering: 1 off, 2 blink, 3 breath, 4 neon, 5 steady)
open-inphic set-led --effect 3 --brightness 2 --speed 1

# Sleep and advanced features
open-inphic set-advance --sleep 2 --deep-sleep 1
open-inphic set-advance --lift 2 --ripple-control on --line-correct off
```

- `set-slot` takes a 1-based enabled slot. `disable-slot` writes the empty
  code `0xff`; re-enable a slot with `set-dpi --slot N`.
- `set-dpi` reads the device's DPI table and validates the requested value.
  Without `--slot` it changes the value of the currently selected slot.
- `set-rate` and `set-usb-rate` accept 125, 250, 500 or 1000 Hz, shown as
  green, blue, yellow and red indicators. `--mode` targets one of the four
  configuration modes without switching to it first.
- `set-led --effect` uses the vendor numbering: 1 off, 2 blink, 3 breath,
  4 neon, 5 steady. `--brightness` and `--speed` take 0-3 (darkest/slowest
  to brightest/fastest). `--direction` is `forward` or `reverse`.
- `set-advance --sleep` takes 0-6 (10, 30, 60, 120, 180, 300, 600 s);
  `--deep-sleep` takes 0-6 (5, 10, 15, 20, 30, 40, 60 min); `--lift` takes
  0-3 (1-4 mm).
- Boolean flags: `--ultra-low-delay`, `--ultra-low-power`,
  `--ripple-control`, `--move-wake-up`, `--line-correct`, each `on` or
  `off`.
- With more than one matching receiver, pass `--device /dev/hidrawN` before
  the subcommand. The GUI accepts the same option. After the mouse wakes or
  the receiver is replugged, click **Refresh** in the GUI.
- `firmware` and `status` display versions in the vendor form. If a wired
  mouse is connected without a receiver, `firmware` reads the mouse's own
  model and version.

### Firmware

```bash
# No local .bin yet: download the official installer and extract the image
open-inphic fetch-firmware
open-inphic upgrade --download --dry-run
open-inphic upgrade --download

# Use an already downloaded installer (no network access)
open-inphic fetch-firmware --installer "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"
open-inphic upgrade --installer "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"

# Use an existing .bin (requires a wired USB connection)
open-inphic upgrade --file 0x00710106.bin --dry-run
open-inphic upgrade --file 0x00710106.bin
```

## GUI

```bash
open-inphic-gui
# or, from the checkout
python3 inphic_gui.py
```

The window shows device information and panels for configuration mode, DPI,
report rate, lighting, sleep, advanced features and firmware upgrade. Writes
are confirmed by reading the affected field back; a failed write attempts to
restore the previous value and tells you whether that restore was confirmed.

Closing the window hides the application to the tray. Use the tray menu to
reopen the window, change common settings, or quit.

## Firmware versions and upgrades

`status` and `firmware` show versions in the vendor's format:
`V0071.0106.EB02` is FWID `0x00710106` with firmware version `0xeb02`. The
first two groups are the high and low 16 bits of the FWID; the last group is
the version. The receiver is displayed the same way, for example
`V0166.0004.0902`.

The vendor does not publish a standalone firmware download page; the `.bin`
images are embedded in the Windows driver installer. The tool downloads the
official installer (about 91 MiB), verifies its SHA-256, unpacks it, scans the
embedded assets and extracts the firmware for the connected model. It can also
use a local installer or an existing `.bin`.

Firmware upgrades only work over a **USB cable**. The 2.4G receiver link
cannot carry firmware. Before writing, the tool reads and saves the existing
flash; after writing it reads the firmware bytes back, verifies them, submits
the checksums and boots the new firmware.

- Official installer URL:
  `https://inphic.oss-cn-shenzhen.aliyuncs.com/download/INPHIC%20YU_SetupV2.2.11.exe`
  with SHA-256
  `e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035`.
  The tool prints and checks it. If the vendor replaces the file at that URL,
  a different hash is reported but extraction continues; the firmware header
  and model checks are still enforced.
- The installer uses Inno Setup 6.3.0. `innoextract` 1.9 cannot parse it, so
  the tool prefers a system `innoextract` 1.10 or newer and otherwise
  downloads the official `innoextract 1.10-dev` Linux helper (about 1 MiB,
  pinned SHA-256) to `~/.cache/open-inphic/tools/`. Disable that download
  with `--no-helper-download`, or point `OPEN_INPHIC_INNOEXTRACT` at an
  existing binary.
- Extracted images are written to
  `~/.cache/open-inphic/firmware/<fwid>-<version>.bin`; installers are
  cached in `~/.cache/open-inphic/installers/`. The X9 image is 40912 bytes,
  `V0071.0106.EB02`.
- The file header's FWID and version are validated and matched against the
  model reported by the receiver or by the wired mouse. A mismatched model is
  refused.
- The current version is compared with the target. The same version is
  reported as "already up to date" and the lower version is refused unless
  `--force` is given (in the GUI, tick **Force reflash / downgrade**).
- The original flash is backed up by default to
  `~/<firmware-name>.flash-<timestamp>.bin` in your home directory; use
  `--backup` to choose a path. The backup is the full raw flash
  (`0xc800` bytes, including the application area and the reserved top
  region) and is written before anything is erased. `upgrade` accepts only
  vendor application images (at most `0xb000` bytes); restoring a full flash
  backup is a manual procedure.
- With more than one wired mouse or bootloader, select the target with
  `--boot-device /dev/hidrawN`.
- If an upgrade fails, the mouse usually stays in the bootloader
  (`32c2:001b`). Re-running the same command detects it automatically. If no
  receiver is available to provide the model, pass `--fwid 0x00710106`.
- **Receiver firmware**: all 40 firmware images in the V2.2.11 installer are
  mouse images (`0x0071xxxx`); there is no receiver image (`0x0166xxxx`)
  and the vendor UI only upgrades mice. The tool therefore only reads and
  displays the receiver version and does not write receiver firmware.
- The BootV1 write path has offline protocol replay tests but has not been
  verified on real hardware on Linux. Treat firmware upgrades as experimental
  and keep the vendor installer and the flash backup.

## Advanced settings

The labels below come from the vendor driver's `zh-CN` resources, with the
English strings in parentheses. The vendor UI does not show these switches for
the X9 PRO: its `0x00710106/config.json` only sets `SilentAltitude: false`
and `UltralowDelay: false`, and the UI only renders keys that are true, so all
six are hidden. This tool exposes them directly through the protocol and
confirms writes by reading them back.

| Option | Vendor title | Vendor description |
| --- | --- | --- |
| Ultra low delay | 超低延迟（Ultra Low Delay） | Reduces mouse latency when drawing lines |
| Ultra low power | 超低功耗（Ultra Low Power） | More power-efficient; **the report rate is fixed at 125 Hz while enabled**, turn it off before changing the rate |
| Ripple control | 波纹控制（Ripple Control） | Reduces wave-like jitter at high speed with algorithmic correction |
| Move wake-up | 移动唤醒（Move WakeUp） | Allows the mouse to be woken from deep sleep by moving it |
| Line correction | 直线修正（Line Correct） | Makes it easier to draw straight lines |
| Lift-off distance | 鼠标抬起高度（Mouse LOD Altitude） | Lift-off height, 1-4 mm |

The two sleep settings use the vendor titles 休眠时间设置(单位:秒) (sleep time
in seconds) and 睡眠时间设置(单位:分钟) (deep sleep time in minutes): the
mouse enters sleep after being still for the first period, and enters deep
sleep after the second.

## System tray

- Closing the window hides the app to the tray. Click the tray icon or choose
  **Open settings** to restore it. **Quit Open INPHIC** ends the app; an
  operation that is already running is allowed to finish first.
- The tray menu changes the configuration mode, DPI slot, 2.4G report rate,
  wired report rate and lighting effect directly.
- Hovering shows battery percentage, charging state, mouse and receiver
  firmware versions, and the last read-back slot, rates and effect.
- The tray icon fills up with the battery level, turns red when low and adds a
  bolt while charging. When the mouse is offline it shows an unknown level and
  refreshes after the mouse wakes. The background poll runs every 8 seconds
  and only reads the battery and online flag when the device is online.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `未找到 2.4G 接收器` (no receiver found) | Replug the receiver; make sure the udev rule is installed; run `udevadm info` on the node. |
| `无法访问 /dev/hidrawN` (permission denied) | Install the udev rule and log in locally; `uaccess` does not apply to most SSH sessions. |
| Multiple receivers found | Pass `--device /dev/hidrawN` (see `open-inphic --help`). |
| Mouse offline / query timeouts | Move or click the mouse to wake it; check that it is in 2.4G mode. |
| Settings write fails | The receiver occasionally drops a frame. The tool retries and rolls back; run the command again and check `status`. |
| Firmware upgrade refused | The model or version does not match, or the version is the same/lower; use `--force` only when intended. |
| Upgrade left the mouse in the bootloader | Reconnect the mouse over USB and re-run `upgrade`; pass `--fwid` if no receiver is present. |
| `innoextract` too old | Install 1.10+ or allow the helper download; or set `OPEN_INPHIC_INNOEXTRACT`. |
| No system tray | Enable the desktop's notification area/tray, or use the CLI. |

## Safety and recovery

- Do not disconnect the mouse, close the application, or interrupt the power
  during a firmware write.
- The original flash is saved before the first erase. Keep that backup and the
  vendor installer until you have verified the new firmware.
- If the mouse no longer enumerates as a mouse, check for the BootV1
  bootloader (`32c2:001b`) and re-run `open-inphic upgrade` with the same
  firmware file.
- The project cannot repair a device that has been damaged by an unrelated
  firmware image or by a power loss during flashing. Recovery then requires
  the vendor's own tools or service.

## Not implemented

Button remapping, macro editing and per-slot DPI colours live in the mouse
flash (`0x52`/`0x51`/`0x50`/`0x53` transfers and the configuration
region encoding). They are deliberately not written until a read-only backup
tool and a verified restore path exist. A complete configuration UI over USB
and Bluetooth mode are also not implemented; firmware upgrades only support a
wired USB mouse, and receiver firmware is read-only.

## Project layout

```text
open_inphic/
  hid.py        HID transport, model table, CLI
  firmware.py   firmware parsing, installer extraction, BootV1 upgrade
  gui.py        PyQt6 controller and GUI entry point
  tray.py       system tray integration
  ui/Main.qml   Qt Quick interface
inphic.py       CLI launcher for a source checkout
inphic_gui.py   GUI launcher for a source checkout
tests/          unit, protocol, firmware and UI tests (fake devices)
udev/           udev rule for the receiver, wired mouse and bootloader
docs/           protocol notes (Chinese and English)
```

## Development and tests

```bash
python3 -m pip install -e '.[gui,dev]'
python3 -m pytest
```

The tests use fake HID devices and do not require hardware. The GUI tests need
PyQt6 and run without a display; set `QT_QPA_PLATFORM=offscreen` when running
them on a headless machine.

## Documentation

- [X9 PRO HID protocol (English)](docs/x9-pro-protocol.en.md)
- [英菲克 X9 PRO 的 Linux HID 协议（中文）](docs/x9-pro-protocol.md)
- [Notices and unofficial-implementation statement](NOTICE.md)

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first;
please do not include vendor binaries, firmware images, personal paths or
device serial numbers in issues and pull requests.

## License

[MIT](LICENSE) © 2026 Open INPHIC contributors.

## Disclaimer

Open INPHIC is an unofficial, independent project. It is not affiliated with,
endorsed by, sponsored by, or supported by INPHIC (英菲克), BYKC, or their
affiliates. All product names and trademarks are the property of their
respective owners. The software is provided "as is", without warranty of any
kind; firmware flashing can damage hardware and is performed entirely at your
own risk. See [NOTICE.md](NOTICE.md) for the full statement.
