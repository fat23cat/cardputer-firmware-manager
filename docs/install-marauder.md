# Install the isolated ESP32 Marauder build

This procedure adds Marauder as another image for the existing `extra` slot on
an 8 MiB Cardputer ADV. Only one `extra` app runs at a time; its image and the
other app images remain on the SD card. Bruce's LittleFS partition stays at
`0x7b0000`. Marauder uses its own 128 KiB SPIFFS partition at `0x7d0000`.

The [official Cardputer ADV image](https://github.com/justcallmekoko/ESP32Marauder/releases/tag/v1.17.0)
mounts the default `spiffs` partition with format-on-failure. On this CRUB
layout, that is Bruce's LittleFS partition, so the official image may erase
Bruce settings. The isolated build pins the
[Cardputer CRUB fork](https://github.com/fat23cat/ESP32Marauder/tree/codex/cardputer-crub-extra),
based on upstream Marauder 1.17.0. It mounts the new `marauder_fs` label and disables Marauder's own firmware
updater. Its OTA updater would otherwise select Hub as the next app partition.
The build also gives Marauder's Bluetooth bonds and backlight preference
separate namespaces in the existing default NVS partition. It checks stored
Bluetooth record sizes and never erases the shared NVS while initializing
NimBLE. It also avoids calling BLE stop methods after a spam iteration has
already deinitialized NimBLE; that caused a Cardputer ADV reboot when leaving
`BLE Spam All`.

## Build the application

Install [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/)
and Arduino ESP32 core 2.0.11. Marauder's upstream Cardputer ADV build uses
that core and the `MARAUDER_CARDPUTER_ADV` target. On a clean Arduino CLI setup:

```bash
arduino-cli core update-index --additional-urls \
  https://github.com/espressif/arduino-esp32/releases/download/2.0.11/package_esp32_dev_index.json
arduino-cli core install esp32:esp32@2.0.11 --additional-urls \
  https://github.com/espressif/arduino-esp32/releases/download/2.0.11/package_esp32_dev_index.json
./tools/build_marauder.sh
```

The script checks fork commit `940ebfd380a464dd09184b2d561c11e59898922c`,
fetches the library versions used by upstream CI, applies the fork's
`patches/nimble-crub.patch` to the pinned NimBLE library, and builds
`dist/Marauder.bin` and `dist/Marauder.elf`. The fork includes the BLE shutdown
guard and handles one BLE spam payload per main loop to improve keyboard
response. The script checks the
application size, ESP project name,
and embedded `marauder_fs` and `marauder_bond` labels. This step touches only the
host computer. `local --app all` continues to select Hub and Codex; select
Marauder explicitly.

## Add the settings partition once

Do this only on a Cardputer already running the shared CRUB layout in this
repository. The following step changes the internal partition table, so make
a fresh [complete 8 MiB backup](install-crub.md#back-up-the-complete-device)
first. Keep the backup outside the SD card. The preparation tool checks that
the current table is exactly an earlier CRUB table from this repository and
that every new range contains only `0xff` bytes:

```bash
python3 tools/prepare_partition_table.py /path/to/cardputer-adv-backup.bin
```

It writes `dist/crub-partitions.bin` and does not contact the Cardputer. The
input backup is preserved; `--output` cannot refer to it through the same
path, a symbolic link, or a hard link. The current table also shrinks `extra`
to 4.5 MiB and adds Meshtastic's `mesh_fs`;
see [Install Meshtastic](install-meshtastic.md) for the details and the
expected SHA-256.
Review its SHA-256 and the backup path. Enter ROM download mode by holding
`G0` while pressing Reset, then release `G0`. Use the serial port from the
backup command. Write **only** the prepared table and verify it:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
  --baud 115200 --before default_reset --after hard_reset write_flash \
  0x8000 dist/crub-partitions.bin
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
  verify_flash 0x8000 dist/crub-partitions.bin
```

Press Reset if the display stays dark. CRUB `pt info` must show `marauder_fs`
at `0x7d0000` with size `0x20000` and `mesh_fs` at `0x750000` with size
`0x40000`, while `hub`, `extra`, `apps_nvs`, `hub_config`, `spiffs`, and
`coredump` keep their previous offsets. `extra` shrinks to `0x480000`. Do not
erase any existing partition. The new partition starts blank and Marauder
formats only `marauder_fs` on its first launch.

## Stage and start Marauder

Enter CRUB `usbsd`, then stage the built image on the mounted FAT32 volume:

```bash
python3 -m firmware_manager local --app marauder --sd /Volumes/CARDPUTER
python3 -m firmware_manager doctor --sd /Volumes/CARDPUTER
```

Safely eject the SD volume, leave `usbsd`, and run:

```text
sd
upmarauder
go
```

`upmarauder` writes only `extra`. The image files for Hub, Codex, and Bruce
remain on SD. To switch back, run `upbruce` or `upcodex`, then `go`. After the
first Marauder boot, save a setting, switch to Bruce and confirm its settings
still work, then switch back to Marauder and confirm its setting persists.
To leave an active scan or BLE spam screen on Cardputer ADV, hold `Shift+9`
until the menu returns (the `(` key in Marauder). BLE spam can delay keyboard
polling, so a brief tap may be missed. `Fn+9` does not produce `(` in this
keyboard driver.

Marauder's **Update Firmware** menu is hidden in this build and its update
command refuses to write. For later versions, rebuild with the reviewed
patch and use `upmarauder`. If the table update fails or CRUB does not boot,
restore the complete backup using [Recovery](install-crub.md#recovery).
