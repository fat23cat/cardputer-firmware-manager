# Install the isolated Meshtastic build

This procedure adds [Meshtastic](https://meshtastic.org) as another image for
the `extra` slot on an 8 MiB Cardputer ADV with the Cap LoRa-1262 module. Only
one `extra` app runs at a time; its image and the other app images remain on
the SD card. Meshtastic keeps its node database, configuration, and messages
in its own 256 KiB LittleFS partition, `mesh_fs`, at `0x750000`.

Do not install the official `m5stack-cardputer-adv` image on this layout, and
do not use the Meshtastic web flasher. The official image mounts the default
`spiffs` partition and formats it if the mount fails. On this layout that is
Bruce's settings partition. Its factory reset also erases the entire default
NVS partition, including other applications' settings. The web flasher
rewrites the whole flash, including CRUB and the partition table.

The isolated build pins upstream Meshtastic 2.7.26
(`54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb`) and applies two patches from this
repository:

- `patches/meshtastic-crub.patch` mounts LittleFS only on `mesh_fs`. On a
  factory reset with Bluetooth bonds, it clears only Meshtastic's NVS
  namespaces (`meshtastic`, `MeshtasticOTA`, `MeshtasticHTTPS`, and
  `mesh_bond`). It also disables the OTA loader switch: applications are
  installed through CRUB into the single `extra` slot.
- `patches/nimble-meshtastic-crub.patch` changes NimBLE-Arduino 1.4.3 to
  store Bluetooth bonds in the `mesh_bond` namespace, not the shared
  `nimble_bond`. It checks stored record sizes and never erases the shared
  NVS during Bluetooth initialization.

## Build the application

The build needs PlatformIO Core 6.2.0 or newer (`pio`), GitHub access, and
about 2 GB of disk space for the toolchain and libraries:

```bash
./tools/build_meshtastic.sh
```

The script clones the pinned tag, checks its commit, applies both patches, and
builds `dist/Meshtastic.bin` and `dist/Meshtastic.elf`. It checks the
application size, the ESP project name, and the embedded `mesh_fs` and
`mesh_bond` labels. This step touches only the host computer.
`local --app all` continues to select only Hub and Codex; select Meshtastic
explicitly.

## Required layout

Use the current CRUB-only layout: `extra` at `0xd0000`, size `0x680000`,
with `mesh_fs` at `0x750000` (256 KiB) and `marauder_fs` at `0x7d0000`
(128 KiB). It includes both settings partitions from the start. If the device
still has a dedicated `hub` partition, follow the
[clean installation procedure](install-crub.md#clean-installation) once.
Do not replace only the table: the application address has changed.

## Stage and start Meshtastic

Attach the antenna to the Cap LoRa-1262 module before the first launch.
Transmitting without an antenna can damage the radio. In CRUB, enter `usbsd`,
then stage the image on the mounted FAT32 volume:

```bash
python3 -m firmware_manager local --app meshtastic --sd /Volumes/CARDPUTER
python3 -m firmware_manager doctor --sd /Volumes/CARDPUTER
```

Safely eject the SD volume, leave `usbsd`, and run:

```text
sd
upmesh
go
```

`upmesh` writes only `extra`. The Hub, Codex, Bruce, and other images remain on
the SD card. If Meshtastic is launched with a layout missing `mesh_fs`, it cannot
mount `mesh_fs`: it shows **Critical fault #13** (flash corruption,
unrecoverable) and reboots whenever it saves a setting, for example the LoRa
region. Other partitions are not touched. Install the current CRUB layout as
above, then reinstall the application into `extra`. To switch back, run
`upbruce`, `upcodex`, or another `up...` alias, then `go`.

## First setup

Meshtastic does not transmit until a LoRa region is set. Choose the region of
the country where the device is used, for example `RU` in Russia or `EU_868`
in the European Union. Set it in the on-device menu or from the Meshtastic app
over Bluetooth; the pairing PIN appears on the Cardputer screen.

After the first boot, change a setting, switch to Bruce and confirm its
settings are intact, then switch back with `upmesh` and `go` and confirm that
Meshtastic kept its setting and node list.

## Updates and storage

The OTA update request is refused in this build. For a later Meshtastic
version, review the release, update the pinned tag and commit in
`tools/build_meshtastic.sh` and `firmware-manager.json`, rebuild, and install
with `upmesh`. Switching `extra` to another application keeps `mesh_fs` and
Meshtastic's NVS namespaces. A Meshtastic factory reset clears only those.

`mesh_fs` holds up to 200 nodes (the limit for 8 MiB ESP32-S3 devices), the
last 20 messages, and configuration. Wi-Fi and PHY calibration data use the
ESP-IDF namespaces in the default NVS, as with every other Wi-Fi application.

If provisioning fails or CRUB does not boot, restore the complete backup
using [Recovery](install-crub.md#recovery).
