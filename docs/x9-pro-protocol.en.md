# INPHIC X9 PRO HID Protocol (Linux)

English · [中文版](x9-pro-protocol.md)

> This document is an unofficial reverse-engineering record and is not
> affiliated with INPHIC. The protocol was derived from the vendor's publicly
> distributed Windows driver and from observing Linux hardware, for
> interoperability purposes only; product names and trademarks belong to their
> respective owners. See [NOTICE.md](../NOTICE.md).

The analysis target is the Windows installer `INPHIC YU_SetupV2.2.11.exe`. The local USB descriptor matches the **HS6219 2.4G receiver** branch in the installer. Vendor commands are sent from interface 2 using Report ID `0x0e`; the host writes 16 bytes and the device replies with 32 bytes, with the length including the Report ID in both cases. On Linux, the X9 PRO's online model, firmware, and DPI queries have been verified, as have switching, reading back, and restoring the DPI level and the 2.4G report rate.

## 1. Device identification

| Object | Evidence gathered | Linux-side determination |
| --- | --- | --- |
| Local 2.4G receiver | USB `32c2:0027`, `bcdDevice=2.00`, product string `HS6219 2.4G Wireless Receiver` | Locate the receiver by VID/PID, then verify the configuration interface |
| Windows configuration interface | `DongleDriver.Info` matches `32c2:0027`, Usage Page `0xff0f`, Usage `0x01` | Match the same top-level HID collection and report length |
| Receiver model | The local `0x21` reply confirms FWID `0x01660004` and running firmware `0x0902`. The built-in model table maps it to `BYKC 6219 Dongle` with resource version `0x0402` | Confirmed through a Linux `hidraw` query |
| X9 PRO mouse | The online `0x61` reply confirms FWID `0x00710106` and running firmware `0xeb02`, corresponding to `X9` in the installer. The receiver's `0x2d` returns the same FWID and pairing-record version `0x0402` | Use `0x61` to confirm the online model and running firmware. The pairing record can be used to display the model while offline |
| USB direct-connect mouse | Windows' generic `MouseDriver.Info` matches `32c2:0024`, `0xff0f:0x01` | `NewMouseDriver` uses 32-byte input/output and Report ID `0x0e`. See section 8.1 for settings. Firmware upgrade uses `0x1e` to enter the bootloader |
| Bluetooth | Hardware name `inphicMouse`, BlueZ PnP ID `32c2:0026`, Linux HID ID `0005:00000000:00000000` | Read connection and BlueZ battery status. The observed GATT and HID reports expose no vendor configuration interface |

The X9 PRO's older `INPHIC SZ_SetupV1.0.13.exe` also matches the generic
`MouseDriver` against `32c2:0024` and `0xff0f:0x01`. Neither driver
manager showed a separate Bluetooth configuration entry point.
[Older INPHIC driver downloads](https://www.inphic.cn/download?pg=3)

On 2026-11-23 the paired X9 PRO exposed standard GATT services `1800`,
`1801`, `180a`, `180f` and `1812`. The 161-byte report map read from
`2a4b` matched the Linux HID descriptor. The report references identify
input reports `01` (mouse), `02` (consumer control), and `03` (keyboard),
as well as output report `03` (keyboard LEDs). The device exposed neither
a vendor usage page nor report `0x0e` or a feature report. Its BlueZ
`org.bluez.Battery1` property reported 100%.

The INPHIC download page lists the X9 PRO within the supported range of this wireless mouse driver. The product page claims a maximum of 12,000 DPI, while the `X9` resource in this installer lists 12,800 DPI. Which hardware or firmware version each source applies to still needs to be checked on real hardware. On the Linux side, read `0x61`, `0x62`, and `0x63` where available first, then choose the value to display and the writable levels. [INPHIC driver downloads](https://www.inphic.cn/download) · [X9 PRO official product page](https://www.inphic.com/zh-hans/products/inphic-x9-pro-vertical-ergonomic-precision-dual-mouse)

## 2. Linux HID transport

| USB interface | Top-level collection | USB endpoint | Example node on this machine |
| --- | --- | --- | --- |
| 0 | Boot keyboard | `0x81` Interrupt IN | `/dev/hidraw2` |
| 1 | Boot mouse | `0x82` Interrupt IN | `/dev/hidraw3` |
| 2 | Keyboard, Consumer Control, vendor-defined `0xff0f:0x01` | `0x83` Interrupt IN, maximum packet 32 bytes | `/dev/hidraw4` |

The report descriptor of interface 2 is 86 bytes long. The raw fragment for the vendor collection is as follows.

```text
06 0f ff 09 01 a1 01 85 0e 09 02 15 00 26 ff 00
75 08 95 1f 81 02 09 02 15 00 26 ff 00 75 08 95
0f 91 02 c0
```

- The numbered Output Report is `0e` followed by 15 bytes. Set `tx[0]=0x0e` and `tx[1]=command`, fill in the fields according to the tables below, and zero the rest. The Windows Go code builds a 16-byte output buffer, and the bundled HID library ultimately goes through `WriteFile`.
- The numbered Input Report is `0e` followed by 31 bytes. Interface 2 also carries input reports with `0x01` and `0x05`. The read loop checks the Report ID and the actual length first.
- Linux can use `hidraw`'s `write()` and `read()` directly, leaving the existing `hid-generic` handling of the standard mouse and keyboard collections in place. Interface 2 has no Interrupt OUT, so `write()` follows the kernel HIDRAW API and goes through `SET_REPORT` on the control endpoint. The expected USB setup tuple for this interface is `bmRequestType=0x21, bRequest=0x09, wValue=0x020e, wIndex=0x0002, wLength=16`, pending confirmation by USB capture. [Linux kernel HIDRAW documentation](https://docs.kernel.org/hid/hidraw.html)
- For ordinary commands, Windows reads the reply after a single write, usually matching on the first two bytes, with up to 3 attempts. A receiver query waits 100 ms for a single read; queries forwarded to the mouse typically use 300 ms. No checksum or sequence number appears in the construction of ordinary commands. The background read loop also dispatches `0e 20` and `0e 60` as status updates. On Linux, the same read loop should handle both receiving packets and matching requests, sending serially per device. For `0x20` and `0x60`, a prefix match only proves that a status of the corresponding type arrived; configuration writes must verify the read-back fields.

## 3. Read-only commands

Below, `b[n]` denotes a byte in the packet, counted from 0. Request prefixes are right-padded with `00` to 16 bytes. In ordinary replies, `b[0:2]` echoes the Report ID and command of the request. Slice end indices are exclusive.

| Request prefix | What is read | Reply fields |
| --- | --- | --- |
| `0e 21` | Receiver model and running firmware version | `b[14:18]` is the big-endian FWID, `b[18:20]` the big-endian version |
| `0e 20` | Online, battery, and charging status maintained by the receiver | Mouse online is `b[9]` bit 7, charging is `b[8]` bit 7, and the battery level is `b[8] & 0x7f`. The keyboard counterparts are `b[11]` and `b[10]` |
| `0e 2d` | Mouse identity recorded by the receiver | `b[2:6]` is the little-endian DeviceID. Read `b[8:10]` and `b[10:12]` as little-endian 16-bit words and concatenate them in order as the high and low 16 bits of the FWID. `b[12:14]` is the big-endian version |
| `0e 2e` | Keyboard identity recorded by the receiver | Same fields as `0x2d`; the X9 PRO configuration does not need to query the keyboard identity |
| `0e 61` | Model and running firmware version of the paired mouse | `b[14:18]` is the big-endian FWID, `b[18:20]` the big-endian version. The bytes of the X9 candidate FWID are `00 71 01 06` |
| `0e 60 00 00` | Current mouse settings | Configuration mode, the DPI level and report rate of each mode, lighting effect, sleep, and so on; see the table below |
| `0e 62 00` | Sensor and the list of current DPI value codes | `b[2]` is the SensorID, `b[3:11]` holds 8 DPI value codes, `b[11]` is the DPIType |
| `0e 63` | DPI table supported by the sensor | Decoded according to the DPIType returned by the earlier `0x62` |

Windows folds the FWID and DeviceID from `0x2d` into the receiver state, and fills in the mouse model information separately through `0x61`. The two replies encode the FWID differently.

### `0x60` reply: current mouse settings

| Byte | Decoded as in Windows |
| --- | --- |
| `b[2]` | The low 2 bits are the configuration mode, range 0–3 |
| `b[4:8]` | One byte per configuration mode, 4 modes in total. The low 3 bits are the DPI level index, bits 6–7 the USB report rate, bits 4–5 the 2.4G report rate |
| `b[8]` | The low 5 bits are the lighting effect number, bit 5 is the direction. When bit 6 of the packet is 0, Windows sets bit 6 of its internal `BLMode` |
| `b[10]` | The low 3 bits are brightness, bits 4–6 are speed |
| `b[11]` | The low 3 bits are the color index |
| `b[12]` | The low 3 bits are the 2.4G primary sleep code, bits 4–6 the 2.4G deep sleep code |
| `b[13]` | The low 3 bits are the Bluetooth primary sleep code, bits 4–6 the Bluetooth deep sleep code |
| `b[14]` | The low 2 bits are `SilentAltitude`; bits 4, 5, 6, and 7 are low delay, low power, ripple control, and move wake-up, respectively |
| `b[15]` | bit 0 is `LineCorrect` |

The Windows front end displays report rate codes `0/1/2/3` as `125/250/500/1000 Hz` in order. The `bInterval=1` of the USB endpoint is an enumeration parameter. Confirming the mouse's actual report rate requires measuring the interval between movement input packets.

### `0x63` DPI table decoding

- When `DPIType=1`, entries start at `b[2]`, 3 bytes each, up to 10 entries. The DPI of entry `i` is the little-endian `uint16(b[2+3*i:4+3*i])`, and its value code is `b[4+3*i]`. Decoding stops when the DPI value is 0. Under this layout, 2400 DPI with code `0x30` encodes as `60 09 30`; this is example data.
- When `DPIType=2`, `b[2:4]`, `b[4:6]`, and `b[6:8]` are the little-endian 16-bit DPI step, the number of levels, and the starting DPI, respectively. Level `i` is counted from 0; its DPI is `starting DPI + i*DPI step` and its value code is `(b[8] + i*b[9]) & 0xff`.
- Windows calls `0x63` when `DPIType` is 1 or 2. On this machine, the X9 PRO `0x62` reply gives `SensorID=0x10`, `DPIType=2`, and current value codes `0e 1e 2e 3e 5e fe ff ff`. The `0x63` reply on real hardware has `b[2:10]` = `32 00 ff 00 64 00 00 01`, which gives a starting DPI of 100, a step of 50 DPI, 255 levels in total, and codes that start at 0 and increase by 1 per level. For codes `0x00–0xfe`, `DPI = 100 + 50 × value code`. The six enabled levels on this machine are 800, 1600, 2400, 3200, 4800, and 12800 DPI in order; the last two, `0xff`, fall outside the valid code range of this table. `b[12:]` of `0x62` has not yet been included in the verified fields.

### X9 default configuration in the installer

| Level | DPI | Value code | Default color |
| ---: | ---: | ---: | --- |
| 1 | 800 | `0x10` | Red |
| 2 | 1600 | `0x20` | Green |
| 3 | 2400 | `0x30` | Blue, selected by default |
| 4 | 3200 | `0x40` | Magenta |
| 5 | 4800 | `0x60` | Yellow |
| 6 | 12800 | `0x80` | Cyan |

`config.json` labels the sensor as S205 and sets `SilentAltitude` and `UltralowDelay` under `Advance` to `false`, while the remaining `UltraLowPower`, `RippleControl`, `LineCorrect`, and `MoveWakeUp` keys are simply missing. The UI renders a control only when the corresponding `Advance` key is true, so the X9 PRO vendor UI does not show these six advanced toggles and keeps only the two report rates, the two sleep stages, and the key/macro pages; the firmware itself still accepts the `0e 60 40 00` write and reads it back (see the real-hardware addendum in section 4). In the default `profile.json`, all four configuration modes use report rate code 3 for both USB and 2.4G. Lighting effect numbers `1–5` correspond to off, blink, breathe, neon, and steady on. The values above come from the software resources.

## 4. Setting commands

In the table, `i` is the configuration mode index `0–3`. Each row is built from an all-zero 16-byte buffer. Windows uses the same parser to handle replies to the `0x60` requests below, and after a write `0e 60 00 00` can be sent to read back.

| Operation | Request layout | Evidence |
| --- | --- | --- |
| Switch configuration mode | `0e 60 [0x20 \| (mode & 3)] 00` | `DongleMouseDevice.SetMode` |
| Update the DPI value list | `0e 62 80`, with `b[3:11]` holding 8 value codes, padded with `ff` when fewer than 8 entries | `SetDPI` first calls `CMD_GetMouseDPIInfo(ctrl=0x80)` and continues downstream only on success |
| Set the current DPI level of each mode | `0e 60 00 0f`, `b[4+i]=level[i] & 7` | `CMD_SetMouseDPILevels` |
| Set the report rate | `0e 60 00 f0`, `b[4+i]=((usb[i] & 3)<<6) \| ((wl[i] & 3)<<4)` | `CMD_SetMouseReportRate`; read back all 4 entries of both links first, then change the target configuration entry |
| Set the lighting effect | `0e 60`, `b[8]=(BLMode & 31) \| ((BLDirection & 1)<<5) \| (BLMode & 0x40 ? 0x80 : 0xc0)`, `b[10]=0x88 \| (Brightness & 7) \| ((Speed & 7)<<4)`, `b[11]=0x08 \| (Color & 7)` | `CMD_SetMouseLEDEffect` |
| Set sleep and advanced features | `0e 60 40 00`, filling `b[12:16]` according to the table above and keeping the rest zero | `CMD_SetMouseAdvanceSetting` |
| Reload the Flash configuration | `0e 60 80 00` | Sent by `SetConfigData` and `SetKeyDefine` after writing Flash; the actual persistence behavior is still to be verified |

### Real-hardware addendum: lighting effect and advanced settings (verified 2026-11-21)

The conclusions below come from writes and read-backs on real hardware under Linux; the bit layouts agree with the disassembly of `CMD_SetMouseLEDEffect` and `CMD_SetMouseAdvanceSetting`.

| Item | Conclusion |
| --- | --- |
| Top 2 bits of `b[8]` in a lighting request | They must always be written as `0xc0`. The driver branches on `BLMode & 0x40` and writes either `0x80` or `0xc0`, but the top 2 bits of the receiver's reply are always `01`, so sending only `0x80` inevitably makes the read-back disagree with the machine's internal value. On real hardware, `b8=c5` corresponds to number 5 (steady on). |
| Brightness and speed are inverted | The protocol `Speed` and the vendor UI slider value are bitwise complements of each other: UI `0` (slowest) corresponds to protocol `Speed=3`, and UI `3` (fastest) to `Speed=0`. Both have the range `0–3`, but `b[10]` uses only the low 3 bits and bits 4–6, hence `Brightness = b[10] & 7` and `Speed = (b[10] >> 4) & 7`. |
| X9 does not expose direction or color | In the `LETable` of `0x00710106/config.json`, only "常亮模式" (Steady On) carries `MoreColor: 4`, and the `Config` bitmask does not provide a direction (`4`). In testing, the direction bit 5 of `b8` and the color index in `b[11]` can be written and read back, but the vendor UI does not expose them to X9 users; the color of each level is actually stored in the Flash configuration area (see section 5). |
| Brightness and speed are still accepted while the light is off | When lighting effect number `1` (off) is set, brightness or speed values sent anyway are retained on read-back, so the fields are not lost just because the light is off; it is therefore safe to set the values first and switch modes afterwards. |
| Field confirmation for sleep and advanced settings | `0x12` corresponds to primary `2` (60 s) and deep `1` (10 min), identical to `WLPrimarySleep=2` and `WLDeepSleep=1` in the vendor `profile.json`. `b[14]=0x01` corresponds to `SilentAltitude=1` (that is, `2 MM` in the UI). `UltraLowDelay`, `UltraLowPower`, `RippleControl`, and `MoveWakeUp` in `AdvanceSetting` map to bits 4, 5, 6, and 7 of `b[14]` respectively, and `LineCorrect` maps to bit 0 of `b[15]`. |
| Configuration mode request | The vendor `SetMode` reuses the `CMD_GetMouseDeviceInfo(0x20 \| mode, 0)` channel to send this, rather than building a separate packet; on Linux we write `0e 60 20\|mode 00` accordingly, and only the low 2 bits of read-back `b[2]` change. |
| Sleep timing takes effect immediately | After the primary sleep was changed to 10 seconds, the mouse went to sleep right away and `0x60` queries began to time out until the mouse was woken. Acceptance scripts must therefore follow the rhythm of "write → read back → restore immediately", otherwise the next query fails. |
| Read-back verification can only compare the target fields | The receiver applies consecutive writes with a visible delay: right after `00 0f` (DPI level) is written, `b[8]`/`b[10]`/`b[12]` in the same reply may still hold values from the previous transaction. If verification demands the entire settings block to be identical, even a normal write is judged a failure and triggers a rollback. In testing, 200 random writes gave zero failures under per-field verification, while whole-block equality verification reliably produced one false failure in 150 runs. |

### Transport-layer caveat: stale replies must be discarded

Every `0x60` request (reading state, `00 0f` writing the level, `00 f0` writing the report rate, `40 00` writing advanced settings, `20|mode` switching the mode) returns one reply frame beginning with `0e 60`, and the packets carry no sequence number or transaction ID. If a write operation writes without reading, that frame stays in the hidraw buffer, and the next `0x60` read request takes the stale reply for its own response.

Observed behavior: after writing the lighting effect and reading back, the value read was still the pre-write one, so the write was misjudged as failed and a rollback was triggered, even though the device had already accepted the write. The fix is to drain the buffer with a non-blocking `select` before each transaction and to require write operations to consume their own corresponding reply as well. After applying this correction on Linux, writes and read-backs are consistently in agreement.

### Raw packets read back on real hardware

```text
Baseline                         0e 60 00 83 c1 f2 f2 f2 41 00 13 07 12 12 01 00
Effect 5, brightness 2, speed 2  0e 60 00 83 c1 f2 f2 f2 45 00 22 07 12 12 01 00
Primary sleep set to 10 s        0e 60 00 83 c1 f2 f2 f2 41 00 13 07 10 12 01 00
All advanced toggles on          0e 60 00 83 c1 f2 f2 f2 41 00 13 07 12 12 f1 01
Configuration mode switched to 3 0e 60 02 83 c1 f2 f2 f2 41 00 13 07 12 12 01 00
Mode 2 USB rate set to 500 Hz    0e 60 00 83 c1 b2 f2 f2 41 00 13 07 12 12 01 00
Mode 4 2.4G rate set to 250 Hz   0e 60 00 83 c1 f2 f2 d2 41 00 13 07 12 12 01 00
Enable levels 7 and 8 (0x62)     0e 62 10 0e 1e 2e 3e 5e fe 70 80 02 20 03 40 06
After full restore               0e 60 00 83 c1 f2 f2 f2 41 00 13 07 12 12 01 00
```

For the first round of real-hardware verification, a reversible change to the 2.4G report rate of a single mode works well; write the original value back once confirmed. A DPI list change involves two requests; if the second fails, read back both sets of state to identify a partial success.

The three conclusions in this section (discard stale replies, verify per target field, sleep timing takes effect immediately) are preconditions for stable writes on Linux; missing any one of them shows up as the false failure of "the write was ignored by the device".

## 5. Buttons, macros, and Flash

The default key configuration in the installer gives the indices and values below. Scroll up and scroll down each have their own separate mapping entry.

| Index | Function | Default value |
| ---: | --- | --- |
| 0 | Left button | `0x4181` |
| 1 | Right button | `0x4182` |
| 2 | Middle button | `0x4184` |
| 3 | Back | `0x4188` |
| 4 | Forward | `0x4190` |
| 7 | DPI cycle | `0x4802` |
| 8 | Scroll up | `0x4201` |
| 9 | Scroll down | `0x42ff` |

Windows' `SetKeyDefine` writes the encoded keys and macros starting at mouse Flash address `0x0000`. The configuration area is at `0x1000`; `GetConfigData` reads 256 bytes, and `SetConfigData` reads, updates, and then writes. The constructor gives `layerSize=1024`, `bankSize=256`, `fifoSize=256`, at most 11 bytes of FIFO data per packet, and at most 27 bytes per Flash read packet.

### Flash contents

- The **key area** has 4 layers of 1024 bytes each, filled with `ff` when built. The base address of layer `layer` is `layer*0x400`. Ordinary key action codes are little-endian 16-bit values written at offset `2*Index` within the layer, with indices ranging from 0 to 11. For the default left button `0x4181`, the first two bytes of the layer header are `81 41`. A configured layer has its layer index written at offset `0x1e` and `0x5a XOR first 31 bytes of the layer header` at `0x1f`.
- **Macro records** share the layer with the key table and are allocated from offset `0x20`. In the key table, bit `0x8000` of the 16-bit action code marks a macro pointer; the first byte of a macro record is `0x5a`, and operations are written as little-endian 16-bit values starting at record offset `0x10`. The repeat mode also modifies the macro pointer and the tail of the record. The complete macro opcode set and the repeat behavior have not yet been confirmed against real-hardware samples.
- In the **configuration area**, offsets `0x02` and `0x03` hold the value codes for the temporary DPI increase and decrease. DPI level colors are written as three-byte RGB values at `0x14+8*i`, and the steady-on color at `0x54+8*i`, with `i` ranging from 0 to 7 in both cases. After encoding, `0xfe` holds the result of XORing the first 254 bytes with initial value `0x5a`, and `0xff` is written with `1`. The layout comes from `encodeConfigData`; the remaining bytes are preserved by reading before writing.

### Flash transport

| Command | Request prefix or fields | Windows verification |
| --- | --- | --- |
| `0x52` Flash read | `0e 52 addr_lo addr_hi len` | Echoes the first 5 bytes; `b[5:]` is the data, and the caller takes `len` bytes |
| `0x51` Write FIFO | `0e 51 addr_lo addr_hi data...` | Compares the first `4+len(data)` bytes |
| `0x50` Commit Flash write | `0e 50 addr_lo addr_hi len a5 checksum_lo checksum_hi` | Echoes the first 5 bytes; `b[5]==1` means success |
| `0x53` Erase bank | `0e 53 bank (bank XOR ff) 55 aa` | Echoes the first 3 bytes; `b[4]==1` means success |

The commit checksum is `(0xfa21 + sum(little-endian 16-bit words in the data block)) & 0xffff`. The high-level write flow compares the existing data and performs an erase when overwriting a bank whose contents have changed. Writes to keys and macros will only be enabled after a read-only backup has been taken from real hardware and the layout and recovery strategy have been verified. Windows additionally has mouse reset `0x5f` and receiver-to-Boot `0x1e`; see section 8 for the BootV1 upgrade flow.

## 6. Confirmation steps on real hardware

The receiver channel was verified from a Linux terminal using `/dev/hidraw4`. The `b[14:20]` of the `0x21` reply is `01 66 00 04 09 02`. The `b[8:14]` of `0x2d` is `71 00 06 01 04 02`, which decodes to paired mouse FWID `0x00710106` and pairing-record version `0x0402`. While offline, `b[8:10]` of `0x20` is `64 0c`, the online bit is 0, the battery field reads 100, and `0x61` times out after three attempts.

After waiting for the standard mouse interface of the same receiver to receive input, the online bit became 1 and all mouse queries received 32-byte replies.

| Reply | Real-hardware fields | Decoding |
| --- | --- | --- |
| `0x20` | `b[8:10] = 64 8c` | Online, battery 100, charging bit 0 |
| `0x61` | `b[14:20] = 00 71 01 06 eb 02` | Mouse FWID `0x00710106`, running firmware `0xeb02` |
| `0x60` | `b[2] = 00`, `b[4:8] = e2 f2 f2 f2` | Configuration mode 0, DPI level index 2 for all four modes. The USB report rate setting is 1000 Hz for all four modes, and the 2.4G settings are 500, 1000, 1000, and 1000 Hz in order |
| `0x62` | `b[2:12] = 10 0e 1e 2e 3e 5e fe ff ff 02` | Sensor `0x10`, 8 current DPI value codes, table type 2 |

Windows' `Manager.ReadData.func1` reads the mouse online state at `0x0073b437`, and only after the bit is set does it create a `DongleMouseDevice` for the newly online mouse. On real hardware, confirm first that the standard mouse interface receives input, then query the configuration channel immediately to determine whether ordinary movement and configuration forwarding work at the same time.

### Settings-write acceptance

The following operations were carried out in a normal Linux user session using the [udev rules](../udev/70-inphic-x9-pro.rules). After each write, the change was read back, then the original value was restored and the DPI level and USB/2.4G report rates of all four modes were re-checked. The baseline re-read before writing was mode 0, 2400 DPI, 2.4G 125 Hz.

| Acceptance item | Temporary change | Read-back after restore |
| --- | --- | --- |
| Current DPI level | `0x60 00 0f` changes index 2 to 0, i.e. 2400 → 800 DPI | Index 2, 2400 DPI |
| Custom DPI value | `0x62 80` changes the level-3 code `0x2e` to `0x2f`, then `0x60 00 0f` applies the level, i.e. 2400 → 2450 DPI | Code `0x2e`, 2400 DPI |
| Current 2.4G report rate | `0x60 00 f0` changes code 0 to 1, i.e. 125 → 250 Hz | Code 0, 125 Hz |

In the final read-back, all four DPI level indices are 2, all USB report rate codes are 3, and the 2.4G report rate codes are `0,3,3,3`. The eight DPI value codes read through `0x62` are identical to the baseline.

After the receiver was re-plugged, Linux received removal and addition events for the configuration interface, udev automatically re-granted access to the normal user, and `0x60`, `0x62`, and `0x63` could all be read. While the mouse sat idle, the online bit of `0x20` changed from 1 to 0. After waking it, the online bit returned to 1, and 2400 DPI and 2.4G 125 Hz were successfully read back.

### Probing on real hardware

1. Find USB interface 2 of the `32c2:0027` receiver and its corresponding hidraw node. Node numbers change with enumeration. Use existing device access permissions, or grant access to the local user through a udev rule that matches only this receiver's configuration interface.
2. Switch the mouse to 2.4G mode and wake it. Following the Windows receiver initialization order, send `0x21`, `0x20`, `0x2d`, `0x2e`, then the mouse model query `0x61`. Verify the mouse online bit, the pairing record, and the running firmware version. If `0x2d` already returns a non-zero FWID while `0x61` times out, record the paired FWID as corroborating evidence for the model.
3. Read `0x60` and `0x62`. Read `0x63` as well when DPIType is 1 or 2. Switch one level with the mouse DPI button and re-read `0x60` to verify that the level index changes. Validate the setting commands with a reversible report-rate change, write the original value back, and re-read.
4. When the USB layer needs to be cross-checked, capture with `usbmon` on Linux or record the same operation with Windows USBPcap, matching the control OUT and the Interrupt IN of interface 2 respectively. Keep the actual packets emitted by the paired mouse for offline testing.

The following example queries the receiver and the paired device. The first device argument is the configuration node. If the standard mouse node of the same receiver, such as `/dev/hidraw3`, is passed as well, the script waits for mouse input and then queries the online state, model, current configuration, and DPI. The script checks the sysfs identity and the report descriptor before accessing the node.

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
    raise SystemExit("device identity does not match 32c2:0027")
if not descriptor.endswith(vendor_collection):
    raise SystemExit("vendor HID collection does not match")

def query(handle, opcode, timeout):
    request = bytes((0x0e, opcode)) + bytes(14)
    for _ in range(3):
        if handle.write(request) != len(request):
            raise SystemExit("short write while sending the report")
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
            raise SystemExit("standard mouse node does not belong to the same receiver")
        with open(mouse_node, "rb", buffering=0) as mouse_handle:
            print("please move the mouse within 10 seconds", flush=True)
            if not select.select([mouse_handle], [], [], 10)[0] or not mouse_handle.read(8):
                raise SystemExit("the receiver's standard mouse interface received no input")
            print("the receiver's standard mouse interface received input", flush=True)
        opcodes = (0x20, 0x61, 0x60, 0x62)

    for opcode in opcodes:
        response = query(handle, opcode, 0.3 if opcode >= 0x60 else 0.1)
        if response is None:
            print(f"{opcode:02x}: query timed out (3 attempts)")
            continue
        print(f"{opcode:02x}: {response.hex(' ')}")
        if opcode == 0x20:
            print(f"mouse online={bool(response[9] & 0x80)} battery={response[8] & 0x7f}")
            continue
        if opcode == 0x62 and response[11] in (1, 2):
            dpi_table = query(handle, 0x63, 0.3)
            print(f"63: {dpi_table.hex(' ')}" if dpi_table else "63: query timed out (3 attempts)")
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
        print(f"FWID=0x{fwid:08x} version=0x{version:04x}")
PY
```

## 7. Locating the evidence and reproducing it

| Object | SHA-256 |
| --- | --- |
| Input installer | `e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035` |
| Extracted `app/INPHIC 英云.exe` | `5973d676a8b02b9d2aefde30c9c14b7082df3d3ec5caeaea433810362918897b` |
| `report_descriptor` of receiver interface 2 | `208043d95fd5458b1997d7b1c36de917f66031320add7a020618861ebb597393` |

The installer is Inno Setup 6.3.0. It was unpacked offline with the upstream `innoextract` development build at commit `6e9e34ed0876014fdb46e684103ef8c3605e382e`; the manifest lists only the main executable and the icons. The main executable is a Windows x86 PE built with Go 1.24.2, module `smurfs`, embedding the Electron UI and a locally modified copy of `github.com/karalabe/hid`. The build information records source commit `27231a6a7e5b2b6c2b472e21591384437cb5afbe`. [innoextract upstream](https://github.com/dscharrer/innoextract/tree/6e9e34ed0876014fdb46e684103ef8c3605e382e)

```bash
innoextract -d /tmp/inphic-yu "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"
sample='/tmp/inphic-yu/app/INPHIC 英云.exe'
go version -m "$sample"
go tool objdump -s '^smurfs/driver\.' "$sample"
objdump --dwarf=info "$sample"
nm -n "$sample" | rg 'DongleDriver|resourcesAppDevice(0x00710106|ModellistJson)'
```

The `main._resourcesAppDevice…` data symbol reported by `nm` is a Windows 386 Go slice header containing a 32-bit little-endian pointer, a length, and a capacity. Using the PE section table, convert the slice header address and its data pointer into file offsets, take the data by length, and gunzip it to obtain `resources/app/device/modellist.json` and the configuration, default profile, and key maps under `resources/app/device/0x00710106/`. The model-table data symbol is at `0x05b2e830`, the X9 configuration at `0x05b2daf0`, and the default profile at `0x05b2db20`, all as PE preferred virtual addresses in this sample.

| Address | Symbol | Purpose |
| --- | --- | --- |
| `0x00735f70`, `0x007391de` | `DongleDriver.Info`, inline construction in `NewManager` | VID/PID, Usage, report length, and ID |
| `0x00736ab0`, `0x00736c90`, `0x00736e30`, `0x00737270` | `CMD_GetModelInfo`, `CMD_GetDeviceInfo`, `CMD_GetMouseID`, `CMD_GetMouseModelInfo` | Receiver and paired-device identity and status |
| `0x00736130`, `0x00736210`, `0x007365e0`, `0x007367b0` | `parseDeviceInfo`, `parseMouseDeviceInfo`, `parseMouseDPIInfo`, `parseMouseDPIs` | Status and DPI reply offsets |
| `0x00737450` to `0x00738560` | `DongleDriver.CMD_GetMouseDeviceInfo` to `CMD_WriteMouseFIFO` | Setting packet construction and reply matching |
| `0x00734700`, `0x00734890`, `0x00735a60`, `0x00735c60` | `DongleMouseDevice.ReadFlash`, `WriteFlash`, `SetConfigData`, `SetKeyDefine` | Chunking, checksums, addressing, and configuration application |
| `0x007336e0`, `0x00733e50`, `0x00734040` | `DongleMouseDevice.encodeFlashData`, `encodeMacroOP`, `encodeConfigData` | Record layout and application-layer checksums |
| `0x007a0b10` | `_hid_write` | Windows output report transport |


## 8. Firmware upgrade (BootV1)

This section is based on the disassembly of `BootV1Driver`, `BootV1Device`, `main.upgradeFirmware`, and `MouseDriver.CMD_Boot` in the vendor Windows driver, together with offline protocol replay by `open_inphic/firmware.py` on the Linux side. No write has yet been executed on real hardware from Linux; keep a backup and confirm the firmware model before upgrading a real device.

### 8.1 Device identity and report lengths

| Object | USB identity | HID usage | Report format |
| --- | --- | --- | --- |
| USB direct-connect mouse configuration interface | `32c2:0024` | Usage Page `0xff0f`, Usage `0x01` | Report ID `0x0e`, 32-byte input, 32-byte output |
| BootV1 Bootloader | `32c2:001b` | Usage Page `0xff0e`, Usage `0x01` | Unnumbered reports, 32-byte input, 32-byte output |
| 2.4G receiver | `32c2:0027` | Usage Page `0xff0f`, Usage `0x01` | Report ID `0x0e`, 32-byte input, 16-byte output; cannot be used for a firmware upgrade |

The `BootV1Driver` constructor parameters on the Windows side are `inputLength=32`, `outputLength=33`, `reportID=0`, `writeFIFOMax=28`, and `readFlashMax=28`. The output length is 33 because the Windows HID write buffer requires the Report ID in the first byte; the Bootloader actually uses unnumbered 32-byte reports, so the Linux `hidraw` write starts at the command byte `0x0e` and totals 32 bytes, with no leading zero.

The `MouseDriver` for a directly connected mouse uses `inputLength=32`, `outputLength=32`, and `reportID=0x0e`; the first byte `0x0e` is the report ID and also the start of the packet.

Wired settings use `MouseDriver.CMD_GetDeviceInfo` (`0x00740720`),
`CMD_GetDPIInfo` (`0x00740900`), and `CMD_GetDPIs` (`0x00740bc0`) from the
same installer. USB opcodes `0x20`–`0x23` correspond to the receiver's forwarded
`0x60`–`0x63`. Mode, DPI, report rates, lighting and advanced settings use
the fields in section 4 with requests padded to 32 bytes. On USB, `0x20`
returns settings. The project's battery and charging-state decoding uses
the receiver's separate `0x20` status reply. Wired model, firmware and
settings reads were verified on the X9 PRO on 2026-11-23. Settings writes
have offline replay coverage.

### 8.2 Firmware file header

Each model in the vendor installer has an image such as `resources/app/device/0x00710106/data/firmware.bin`. The decompressed X9 image is 40912 bytes (`0x9fd0`) long and begins with the Cortex-M vector table and a vendor header:

| Offset | Length | Content |
| ---: | ---: | --- |
| `0x00` | 4 | Initial stack pointer; `00 30 00 20` (`0x20003000`) for X9 |
| `0x04` | 4 | Reset vector; `91 00 00 00` for X9 |
| `0x10` | 4 | Little-endian FWID; `06 01 71 00` (`0x00710106`) for X9 |
| `0x14` | 2 | Big-endian firmware version; `eb 02` (`0xeb02`) for X9 |
| `0x18` | 8 | ASCII build string; `BYKC-V10` for X9 |

All vendor mouse images carry this header. Note that the FWID in the firmware header does not necessarily equal the model ID: for example, the M1 firmware header for `0x00710207` says `0x00710208`, yet both are called `M1` in the model table. Compatibility checks should therefore compare the model name, or accept firmware variants of the same model, rather than relying solely on exact 32-bit FWID equality.

### 8.3 Entering the Bootloader

The mouse must be connected through a USB data cable; the 2.4G receiver channel cannot carry Bootloader packets. Send the following to a directly connected mouse:

```text
0e 1e 55 aa 00 ff            (zero-padded to 32 bytes)
```

A successful reply is `0e 1e 01 ...`. The device then drops its current USB identity and re-enumerates as `32c2:001b`. The vendor `main.upgradeFirmware` sends this command first, then waits up to 30 times at 100 ms intervals, and then looks up the `BootV1` device through the Manager; the Linux side relaxes the wait to 15 seconds.

The model and running firmware of a directly connected mouse can be read with `0e 21`: `b[14:18]` of the reply is the big-endian FWID and `b[18:20]` the big-endian version. This command is read-only; the tool uses it to validate the firmware file's model before entering the Bootloader, and also to read back the new version after an upgrade.

### 8.4 BootV1 commands

The commands in the table below are in on-wire format with the Report ID removed; requests shorter than 32 bytes are zero-padded. Replies start at `0x0e` and are always 32 bytes; the vendor code only compares the first two bytes of a reply (some commands additionally check the status byte), while the Linux side also checks the address echo, the data length, and the status.

| Request prefix | Operation | Reply fields and decision |
| --- | --- | --- |
| `0e 20` | Read the Bootloader model and version | `b[2:6]` little-endian FWID; `b[6:8]` big-endian version |
| `0e 13 5a a5` | Begin the upgrade | Echoes `0e 13`; `b[5]==1` |
| `0e 11 addr len data...` | Write FIFO | Echoes `0e 11 addr`; `len` is at most 28 per packet |
| `0e 10 addr_lo addr_hi len a5 cs_lo cs_hi` | Commit the FIFO to Flash | Echoes `0e 10` plus the address and length; `b[5]==1`; `len` is a `uint8`, so 256 bytes is encoded as `0` |
| `0e 12 addr_lo addr_hi len` | Read Flash | Echoes `0e 12`; `b[4:4+len]` is the data, at most 28 bytes per call |
| `0e 14 app_size(2) flash_size(2) app_cs(2) flash_cs(2)` | Commit the upgrade parameters | Echoes the first 10 bytes; no separate status byte |
| `0e 1e 5a a5` | Start the new firmware | Echoes `0e 1e`; `b[2]==1`; the device may disconnect before replying |

All multi-byte lengths, addresses, and checksums are little-endian on the wire. The checksum of `0e 10` is the 16-bit checksum of that FIFO data block; in `0e 14`, `app_cs` covers the application area and `flash_cs` covers the entire Flash.

The checksum algorithm is the same as for the mouse Flash configuration area:

```text
checksum = (0xfa21 + sum(16-bit words read as little-endian)) & 0xffff
```

### 8.5 BootV1Device write parameters and layout

`BootV1Device.UpgradeFirmware` selects the write parameters from the top 24 bits of the FWID in the Bootloader reply:

| BootID family | appSize | flashSize | FIFO size | Sends 0x13/0x14 | Reserved area |
| --- | ---: | ---: | ---: | --- | --- |
| `0x0071ff00` | `0xb000` | `0xc800` | 256 | Yes | None |
| `0x0166ff00` | `0xb000` | `0xc800` | 256 | Yes | Original Flash `0xb700`–`0xb800` |
| `0x00920000`, `0x00960000` | `0x7000` | `0x7000` | 128 | No | None |

According to the branches in `BootV1Device`, `0x0071` models such as the X9 use the `0x0071ff00` family; the FWID of the Bootloader on real hardware has not yet been read and verified. Taking the 40912-byte X9 image as an example:

1. Pad with `0xff` up to `appSize` (`0xb000`) and compute `appCS` over the application area.
2. Pad further with `0xff` up to `flashSize` (`0xc800`) and compute `flashCS` over the whole Flash.
3. Starting at address 0, split the image into chunks of the FIFO size, 256 bytes; write each chunk into the FIFO with `0x11` packets of at most 28 bytes, then commit it with `0x10`.
4. After writing, the vendor only reads back the length of the original firmware file (40912 bytes) for comparison, and does not verify the `0xff` padding or the reserved area at the top of Flash.
5. Send `0x14` to commit `appSize=0xb000`, `flashSize=0xc800`, `appCS`, and `flashCS`, then send `0x1e 5a a5` to start.

Before computing `flashCS` and writing, the `0x0166ff00` family splices the 256 bytes at original Flash `0xb700`–`0xb800` back into the same position in the image, so that device parameters are not overwritten. The Linux write implementation for the X9 follows the `0x0071ff00` family and does not perform this preservation step.

### 8.6 Upgrade flow and recovery

The Linux implementation (`open_inphic/firmware.py`) runs in the following order. Before upgrading it reads the currently running version through the receiver status or the directly connected mouse's `0x21`; if that equals the image version it skips by default, and a lower target requires `--force`, to avoid pointless erase cycles.

1. Read the model/version of the directly connected mouse via `0x21` and validate the model against the firmware file header; the firmware file must also match the receiver's pairing record.
2. Send `0x1e 55 aa 00 ff` and wait for a Bootloader node at `32c2:001b`.
3. Read the Bootloader's `0x20` information and select the write parameters by BootID.
4. Before sending `0x13`, read the original Flash in full and save it to a backup file; a failed read or a failed backup write aborts the process without erasing or writing the device.
5. Send `0x13`, write the FIFO and Flash in chunks, then read back the original firmware bytes and compare them chunk by chunk.
6. After verification passes, send `0x14` and `0x1e 5a a5` and wait for the device to re-enumerate; if a new directly connected mouse appears, read `0x21` again to confirm the version.
7. A failure at any step means `0x1e` is not sent, leaving the device in the Bootloader. Re-running the upgrade command detects the Bootloader automatically; if no receiver is available to provide the model record, specify it with `--fwid 0x00710106`.

This write path has not yet been executed on real hardware from Linux; offline replay covers command encoding, FIFO chunking, 256-byte length encoding, checksums, backup, read-back verification, and the End/App ordering. Before upgrading a real device, confirm that the firmware comes from the matching model and keep the vendor installer and the original Flash backup.

### 8.7 Disassembly locations

| Address | Symbol | Purpose |
| --- | --- | --- |
| `0x0072ff30` | `BootV1Driver.Info` | Bootloader VID/PID `32c2:001b`, Usage Page `0xff0e`, Usage `0x0001` |
| `0x007390a0` | `NewManager` | Inline construction of BootV1Driver: report lengths 32/33, Report ID 0, FIFO and Flash-read limit 28 |
| `0x0072efb0` | `BootV1Device.UpgradeFirmware` | BootID parameters, padding, writing, read-back, End/App |
| `0x00730c00` | `BootV1Driver.CMD_BeginUpgrade` | `0e 13 5a a5` |
| `0x00730560` | `BootV1Driver.CMD_WriteFIFO` | `0e 11 addr len data` |
| `0x00730770` | `BootV1Driver.CMD_WriteFlash` | `0e 10 ...` and the checksum |
| `0x007309d0` | `BootV1Driver.CMD_ReadFlash` | `0e 12 ...` |
| `0x00730df0` | `BootV1Driver.CMD_EndUpgrade` | `0e 14 ...` |
| `0x00731070` | `BootV1Driver.CMD_App` | `0e 1e 5a a5` |
| `0x00742080` | `MouseDriver.CMD_Boot` | `0e 1e 55 aa 00 ff` for a direct-connect mouse to enter the Bootloader |
| `0x00740540` | `MouseDriver.CMD_GetModelInfo` | Direct-connect mouse `0e 21` model/version |
| `0x0079d420` | `main.upgradeFirmware` | Choose the firmware file, send Boot, wait for BootV1, upgrade again |
| `0x05b2db00` | `main._resourcesAppDevice0x00710106DataFirmwareBin` | Slice header of the X9 firmware resource; 40912 bytes after gunzip, header FWID `0x00710106`, version `0xeb02` |

### 8.8 Obtaining the firmware file online

The vendor download page [inphic.cn/download](https://www.inphic.cn/download) only offers the Windows driver installer; there is no standalone `firmware.bin`. The direct download URL for V2.2.11 is:

```text
https://inphic.oss-cn-shenzhen.aliyuncs.com/download/INPHIC%20YU_SetupV2.2.11.exe
SHA-256 e501510d2c2ea66645fa19c4fae51ef89fc3779c107a5e7cbdea1825481c1035
```

`app/INPHIC 英云.exe` inside the installer is a Go program with the firmware embedded as gzip bindata resources. On Linux, first extract the main executable with `innoextract`, then scan for gzip streams and identify images by their header: offset `0x10` is the little-endian FWID, `0x14` the big-endian version, and `0x18` the `BYKC` build string, with additional checks on the vector table's initial stack pointer and reset vector. V2.2.11 contains 40 unique firmware images; the X9 extraction is 40912 bytes, FWID `0x00710106`, version `0xeb02`, SHA-256 `3d47e616bc27c9bee7368776db35b76ea7f2b1a68cd6ec5c24b2ab41b8a8d510`, consistent with the header in section 8.2.

The system `innoextract` 1.9 only declares support up to Inno Setup 6.0.5 and, in testing, cannot parse the vendor's 6.3.0 installer. Upstream provides a 1.10-dev Linux binary:

```text
https://constexpr.org/innoextract/files/snapshots/innoextract-1.10-dev-2025-01-07/innoextract-1.10-dev-2025-01-07-linux.tar.xz
SHA-256 e4eb7e15a3b417549fd521fe011ae4e542fd8c10b62c5ddafc3b9ac599a54f50
```

The tool prefers the system `innoextract` 1.10 or newer; when the version is too old it downloads the tar.xz above, verifies the SHA-256, and extracts only `bin/<arch>/innoextract` for the current architecture into `~/.cache/open-inphic/tools/`. When automatic download is unavailable, use `--no-helper-download` or point `OPEN_INPHIC_INNOEXTRACT` at an existing tool.

The Linux implementation lives in `open_inphic/firmware.py`: `download_installer`, `ensure_innoextract`, `extract_app_exe`, `scan_firmware_images`, `select_firmware`, and `fetch_firmware`. The corresponding commands are:

```bash
python3 inphic.py fetch-firmware
python3 inphic.py fetch-firmware --installer "$HOME/Downloads/INPHIC YU_SetupV2.2.11.exe"
python3 inphic.py upgrade --download --dry-run
python3 inphic.py upgrade --download
```

Extracted `.bin` files are saved by default to `~/.cache/open-inphic/firmware/<fwid>-<version>.bin`, and installers are cached in `~/.cache/open-inphic/installers/`. Downloading and extraction only read files; actually writing the mouse still requires the earlier USB cable connection, model validation, original Flash backup, and the BootV1 flow.

All 40 unique images scanned in V2.2.11 are mouse `0x0071xxxx` images; there are no `0x0166xxxx` receiver images. `DongleDevice.UpgradeFirmware` in the driver only calls `CMD_Boot` to put the receiver into the Bootloader; the vendor UI has no receiver upgrade entry point and the installer contains no receiver firmware resources. This tool therefore only reads the receiver version and does not provide receiver writes. If INPHIC later provides a receiver `.bin` or a factory tool, the `0x166ff00` branch can be reused to add it.
