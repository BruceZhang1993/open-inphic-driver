# Notices

## Unofficial implementation

Open INPHIC is an independent, community project. It is **not** affiliated
with, endorsed by, sponsored by, approved by, or supported by INPHIC
(英菲克), BYKC, or any of their subsidiaries, affiliates, or partners.

All product names, model names, trademarks, logos, and trade names mentioned
in this repository — including but not limited to INPHIC, 英菲克, X9 PRO,
HS6219, and BYKC — are the property of their respective owners. They are used
only to identify the hardware this software interoperates with.

The protocol descriptions in this repository were obtained by independently
observing USB/HID traffic and by analysing the vendor's publicly distributed
Windows application for interoperability purposes. They are not official
documentation, and they may be incomplete or wrong.

## No vendor binaries

No vendor application, installer, firmware image, or other binary is
redistributed in this repository, its source archives, or its Python wheels.
The optional `fetch-firmware` command downloads the official Windows
installer directly from the vendor's download server at the user's request,
verifies its SHA-256 checksum, extracts the firmware image for the user's own
device, and stores it in the local cache. The user is responsible for
complying with the vendor's licence terms when using that installer.

## Firmware flashing risk

Writing firmware can permanently damage (brick) a device, void its warranty,
or leave it unusable. Firmware upgrades in this project are performed at your
own risk. Keep the original flash backup and the vendor installer; do not
disconnect the mouse during an upgrade.

## No warranty

This software is provided "as is", without warranty of any kind, express or
implied. The authors and contributors are not liable for any damage to
hardware, data, or warranty caused by its use. See [LICENSE](LICENSE).

## Rights holders

If you own rights in any material referenced here and believe this repository
goes beyond interoperability analysis, please open an issue so the situation
can be addressed.
