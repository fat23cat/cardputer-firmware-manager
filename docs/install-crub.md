# Install or recover CRUB

This is the one-time provisioning procedure for an M5Stack Cardputer ADV with
an 8 MiB flash chip. It installs CRUB and Cardputer Hub in their own
application partitions, and Codex Microputer ADV or Bruce in the shared `extra`
slot. A device that already runs the earlier dedicated Codex layout follows
[Migrate from the dedicated Codex layout](#migrate-from-the-dedicated-codex-layout)
instead of the first-install steps.

Changing the bootloader or partition table can temporarily make the installed
firmware unbootable. ESP32-S3 ROM download mode remains available because this
procedure does not write eFuses, enable Secure Boot, or enable Flash Encryption.

## Back up the complete device

Enter ROM download mode: hold `G0`, press and release Reset, then release
`G0`. The screen stays dark and the ROM's USB-Serial/JTAG port appears, for
example `/dev/cu.usbmodem101` on macOS. Identify the exact serial port and read
all 8 MiB:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 \
  --no-stub read_flash 0x0 0x800000 cardputer-adv-backup.bin
```

Reading is slow over USB-Serial/JTAG: a Cardputer ADV read all 8 MiB at about
92 kbit/s, roughly 12 minutes, even with esptool's flasher stub. esptool writes
the file only when the read finishes, so keep its progress output visible
rather than treating a quiet terminal as a hang.

Keep at least one verified copy outside the device's microSD card.

## Build CRUB with the shared layout

```bash
git clone https://github.com/wisnc/crub.git
cd crub
git checkout 669f70b219d2b2cb6fd18e952284eb25b2652d62
cp /path/to/cardputer-firmware-manager/layouts/cardputer-adv-8mb.csv partitions.csv

# Bootloader: QIO flash mode, pinned platform, separate build directory.
sed -i.dio \
  -e 's/^CONFIG_ESPTOOLPY_FLASHMODE_DIO=y$/# CONFIG_ESPTOOLPY_FLASHMODE_DIO is not set/' \
  -e 's/^# CONFIG_ESPTOOLPY_FLASHMODE_QIO is not set$/CONFIG_ESPTOOLPY_FLASHMODE_QIO=y/' \
  sdkconfig.bootloader
cat > bootloader-qio.ini <<'EOF'
[platformio]
build_dir = .pio/build-qio

[env:bootloader]
platform = https://github.com/pioarduino/platform-espressif32/releases/download/55.03.39/platform-espressif32.zip
board = esp32-s3-devkitc-1
framework = espidf
board_build.partitions = partitions.csv
build_flags = -DESP32S3
EOF
PLATFORMIO_CORE_DIR="$PWD/.platformio-core-55.03.39" \
  uvx --from platformio==6.1.19 pio run -c bootloader-qio.ini -e bootloader

# Launcher and shared partition table.
PLATFORMIO_CORE_DIR="$PWD/.platformio-core" \
  uvx --from platformio==6.2.0 pio run -e m5cardputer
```

The pinned upstream commit is titled `3.0.1`, although its source still reports
`3.0.0` through `CRUB_VERSION`.

**Build the bootloader in QIO flash mode.** Upstream CRUB builds its bootloader
in DIO mode. Arduino applications such as Bruce are built for QIO and then run,
but they cannot mount LittleFS: Bruce formats `spiffs` yet never mounts it,
**Files → LittleFS** returns to the main menu, and every setting resets on the
next boot. On a Cardputer ADV this happened with DIO CRUB bootloaders built on
the `stable` platform (also after a software restart from Bruce itself, with
no CRUB run in between) and on `55.03.39`. It did not happen with Bruce's own
bootloader, even with this exact partition table and Bruce in `extra`, or with
the QIO CRUB bootloader below. Hub and Codex run normally with the QIO
bootloader.
The `sed` command changes only the flash-mode choice; ESP-IDF still writes DIO
into the bootloader header because the ROM loads the bootloader in DIO before
the bootloader switches the flash to QIO.

The bootloader uses the pioarduino `55.03.39` platform (ESP-IDF 5.5.4), which
needs PlatformIO Core 6.1.x. The launcher keeps CRUB's floating `stable`
platform, which now needs PlatformIO Core 6.2.0 or newer; `pio run` from
PlatformIO 6.1.x fails there with `IncompatiblePlatform`. Both builds run
PlatformIO through [`uv`](https://docs.astral.sh/uv/) with private core
directories, so neither a global PlatformIO installation nor its packages for
other projects change. The first build downloads each ESP-IDF toolchain.
Because `stable` floats, a rebuild can use a newer Arduino core than an earlier
launcher build even at the same pinned commit; keep the full backup until the
new launcher has booted.

Keep the bootloader's separate `build_dir`: PlatformIO cleans the whole build
directory when a different project configuration is used, so building both
into `.pio/build` deletes whichever was built first.

Before writing, check that `.pio/build/m5cardputer/partitions.bin` contains the
expected `hub`, `extra`, `apps_nvs`, `hub_config`, and `spiffs` offsets, and
that `.pio/build/m5cardputer/firmware.bin` fits the `test` partition
(`0xc0000` bytes). The QIO bootloader is 22,528 bytes and must end before
`0x8000`.

Write the bootloader, generated shared table, and launcher using the same port
that was used for the backup:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
  --baud 115200 --before default_reset --after hard_reset write_flash -z \
  0x0 .pio/build-qio/bootloader/bootloader.bin \
  0x8000 .pio/build/m5cardputer/partitions.bin \
  0x10000 .pio/build/m5cardputer/firmware.bin
```

Verify the three written ranges with `esptool verify_flash` before continuing.

The automatic reset at the end of an esptool command over USB-Serial/JTAG can
leave the Cardputer with a dark screen instead of starting CRUB. Press and
release Reset once; CRUB should then start. Check the table from CRUB with
`pt info`, and never run `pt write` or `pt reset` there.

The first installation intentionally discards settings. In CRUB, initialize the
three NVS partitions and clear leftovers from any earlier layout in Bruce's
`spiffs` partition:

```text
erase nvs
erase apps_nvs
erase hub_config
erase spiffs
```

Do not repeat those commands during normal application updates.

## Prepare and install applications

Format the microSD card as FAT32, run `usbsd`, and use this repository's
manager to stage local builds or GitHub Releases. Safely eject the volume, exit
`usbsd`, remount the card and reload aliases with `sd`, then run:

```text
sd
uphub
upcodex
```

Each command must report `app: ok` and `flash complete`. Use `upbruce` instead
of `upcodex` to put Bruce in `extra`; both images can stay on the card.

## Migrate from the dedicated Codex layout

The earlier layout had a 2.5 MiB `hub`, a dedicated `codex` partition at
`0x350000`, `apps_nvs` at `0x550000`, `hub_config` at `0x560000`, a 512 KiB
`vfs`, and a 1 MiB `spiffs`. The current layout keeps `hub` at `0xd0000`,
replaces `codex` with the 4.75 MiB `extra` slot, and moves both settings
partitions to the end of flash. Copying them preserves Hub and Codex settings.

1. If `codexfast` is active, restore the CRUB menu with `crubmenu` first. The
   boot command `launch -f codex` has no target after migration.
2. [Back up the complete device](#back-up-the-complete-device).
3. Save both settings partitions from their old offsets with the same port:

   ```bash
   python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
     read_flash 0x550000 0x10000 apps_nvs.bin
   python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
     read_flash 0x560000 0x10000 hub_config.bin
   ```

4. [Build CRUB with the shared layout](#build-crub-with-the-shared-layout) from
   this revision of the repository.
5. Write the QIO bootloader, the new table, the rebuilt launcher, and both
   settings partitions at their new offsets:

   ```bash
   python -m esptool --chip esp32s3 --port /dev/ttyACM0 --no-stub \
     --baud 115200 --before default_reset --after hard_reset write_flash -z \
     0x0 .pio/build-qio/bootloader/bootloader.bin \
     0x8000 .pio/build/m5cardputer/partitions.bin \
     0x10000 .pio/build/m5cardputer/firmware.bin \
     0x790000 apps_nvs.bin \
     0x7a0000 hub_config.bin
   ```

6. Verify the five written ranges with `esptool verify_flash`.
7. Boot CRUB and clear the new `spiffs` range, which holds leftovers from the
   old one. Do not erase `nvs`, `apps_nvs`, or `hub_config`:

   ```text
   erase spiffs
   ```

8. Stage Hub, Codex, and optionally Bruce with this repository's manager, then
   in CRUB run `sd`, `uphub`, and `upcodex` or `upbruce`. Staging also removes
   the retired `codex` and `codexfast` aliases. Launch the slot with `go`.

If anything fails, restore the full backup as described under
[Recovery](#recovery).

## Replace only the bootloader

A device that already runs this layout with upstream CRUB's DIO bootloader
keeps its applications and settings when only the bootloader changes. Build the
QIO bootloader as above, enter ROM download mode, and write `0x0` alone:

```bash
python -m esptool --chip esp32s3 --port /dev/ttyACM0 \
  --before no_reset --after no_reset write_flash \
  0x0 .pio/build-qio/bootloader/bootloader.bin
```

Press Reset: CRUB must start, and **Files → LittleFS** in Bruce must open. The
first mount may find a filesystem the DIO bootloader left behind; Bruce then
keeps settings changed from that point on.

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
