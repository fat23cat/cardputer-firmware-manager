# Install MultiMote MeshCore BLE

This installs the ready-built [MultiMote Cardputer ADV firmware](https://github.com/MultiMote/meshcore-cardputer-adv)
for the M5Stack Cardputer ADV + Cap LoRa-1262 into CRUB's shared `extra` slot.
It has a keyboard/display UI, GPS, message history, and Companion BLE for the
MeshCore mobile app, including iOS. No firmware build or partition migration
is required on the current manager layout.

## Reviewed release

The default is [2026.7.3](https://github.com/MultiMote/meshcore-cardputer-adv/releases/tag/2026.7.3),
published July 31, 2026, based on MeshCore 1.16.0. Upstream development is paused.
The manager downloads only this raw BLE application:

```text
cardputer_adv_companion_radio_ble-v1.16.0-2026.7.3.bin
SHA-256: 8bcf7ffd0ba1fa5224a1c00478dc1aea26b3ab0e768a86f4e8bce0edb204a894
Size: 1,421,008 bytes
```

The catalog pins both the tag and digest. The generic ESP project name is
`arduino-lib-builder`, so the release digest establishes its identity; the
`Insert SD Card` marker also rejects applications without the expected SD
startup path. Merged images and USB-only images are excluded from asset
selection. An explicit `--tag meshcore=TAG` overrides the default version;
review that version's storage behavior before using it. Moving the default
requires reviewing the release and updating both the tag and SHA-256.

## Storage and requirements

- Use the current 8 MiB CRUB layout: `test` at `0x10000`, `extra` at `0xd0000`,
  size `0x680000`, with the reviewed QIO bootloader. Follow
  [Install or recover CRUB](install-crub.md) for an earlier layout.
- Insert a writable FAT32 microSD card and keep it inserted while MeshCore runs.
  Without a usable card the firmware stops at `Insert SD Card`.
- Attach the antenna to the Cap LoRa-1262 before starting the radio.
- Back up the SD card. If existing internal contents matter, keep a verified
  [full-device backup](install-crub.md#back-up-the-complete-device) too.

The release's `USE_SD_CARD` configuration uses SD instead of mounting internal
SPIFFS. Standard MeshCore identity, preferences, contacts, and channels use
files at the SD root; UI settings and history use `/meshcore_custom`. Preserve
both when backing up. These are runtime data, separate from the staged
`/firmware/MeshCore.bin` application image. The card can also hold CRUB and
other application files; normal manager staging does not modify MeshCore data.

No `meshcore_fs` partition is needed, and Meshtastic's `mesh_fs` is not reused.
This is the stock BLE build, not a build with isolated Bluetooth NVS namespaces.
The installation procedure writes only `extra` and does not erase shared NVS
or other data partitions; this is not a guarantee about all firmware runtime
or factory-reset operations. Avoid a whole-device erase or factory reset when
preserving other applications' state.

## Stage and install

In CRUB, enter `usbsd`. On the computer, using the actual mounted card path:

```bash
python3 -m firmware_manager doctor --sd /Volumes/CARDPUTER
python3 -m firmware_manager release --app meshcore --sd /Volumes/CARDPUTER
python3 -m firmware_manager doctor --sd /Volumes/CARDPUTER
```

Or use `make release APP=meshcore SD=/Volumes/CARDPUTER`.

Safely eject the volume, leave `usbsd`, then run on the Cardputer:

```text
sd
fw
upmeshcore
```

Wait for `app: ok` and `flash complete`. Only then run:

```text
go
```

`upmeshcore` expands to `flash /firmware/MeshCore.bin extra -nospiffs`; `go`
expands to `launch -f extra`. The previous application in `extra` is replaced,
while its SD image and internal data partitions remain. The manager prepares
SD files only; CRUB performs the final flash on the device.

Do not flash a merged release at `0x0` through a USB/web flasher on this
layout: that replaces CRUB and the partition table. Do not write the raw
application at its standalone address `0x10000`, which is occupied by CRUB.

## First use and switching back

Set the radio parameters to match your local MeshCore network. Connect through
the MeshCore app's BLE device selection; check the on-device BLE settings/PIN
if pairing is requested. Light sleep disconnects Bluetooth, so disable it when
you need an ongoing phone connection. Change a setting and restart to confirm
SD persistence before relying on the installation.

To return to another staged application, reset into CRUB and run its updater
(for example `upbruce`) followed by `go`. MeshCore's SD data is retained.
If an SD boot script starts the application automatically, boot once without
the card, reinsert it, run `sd`, then `crubmenu` to restore the CRUB menu.
