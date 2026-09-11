# Install or recover CRUB

This is the one-time provisioning procedure for an M5Stack Cardputer ADV with
an 8 MiB flash chip. It installs CRUB, Cardputer Hub, and Codex Microputer ADV
in independent application partitions.

Changing the bootloader or partition table can temporarily make the installed
firmware unbootable. ESP32-S3 ROM download mode remains available because this
procedure does not write eFuses, enable Secure Boot, or enable Flash Encryption.

## Back up the complete device

Enter ROM download mode, identify the exact serial port, and read all 8 MiB:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 \
  --no-stub read_flash 0x0 0x800000 cardputer-adv-backup.bin
```

Keep at least one verified copy outside the device's microSD card.

## Build CRUB with the shared layout

```bash
git clone https://github.com/wisnc/crub.git
cd crub
git checkout 669f70b219d2b2cb6fd18e952284eb25b2652d62
cp /path/to/cardputer-firmware-manager/layouts/cardputer-adv-8mb.csv partitions.csv
pio run -e bootloader
pio run -e m5cardputer
```

The pinned upstream commit is titled `3.0.1`, although its source still reports
`3.0.0` through `CRUB_VERSION`.

Write the bootloader, generated shared table, and launcher using the same port
that was used for the backup:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
  --baud 115200 --before default_reset --after hard_reset write_flash -z \
  0x0 .pio/build/bootloader/bootloader.bin \
  0x8000 .pio/build/m5cardputer/partitions.bin \
  0x10000 .pio/build/m5cardputer/firmware.bin
```

Verify the three written ranges with `esptool verify_flash` before continuing.

The first migration intentionally discards settings. In CRUB, initialize the
three NVS partitions:

```text
erase nvs
erase apps_nvs
erase hub_config
```

Do not repeat those commands during normal application updates.

## Prepare and install applications

Format the microSD card as FAT32, run `usbsd`, and use this repository's
manager to stage local builds or GitHub Releases. Safely eject the volume, exit
`usbsd`, then run:

```text
uphub
upcodex
```

Each command must report `app: ok` and `flash complete`.

## Updating CRUB

Never flash CRUB's stock `partitions.bin` after adopting this layout. Rebuild
every reviewed CRUB revision with `layouts/cardputer-adv-8mb.csv`, verify that
the launcher image fits the `test` partition (`0xc0000` bytes), and retain a
full backup. A routine `local` or `release` command cannot update CRUB.

## Recovery

If no firmware boots, hold `G0`, press and release Reset, then release `G0`.
Restore the exact full backup through the ROM download port:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
  write_flash 0x0 cardputer-adv-backup.bin
```
