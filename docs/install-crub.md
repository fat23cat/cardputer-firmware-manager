# Install or recover CRUB

The current 8 MiB Cardputer ADV layout installs only CRUB. Every application,
including Cardputer Hub, uses one shared `extra` slot at `0xd0000`, size
`0x680000` (6.5 MiB). That slot starts empty. Hardware Reset enters CRUB;
an application runs only after it has been flashed and launched.

Older dedicated Hub or Codex layouts require a clean installation. Replacing
only their partition table leaves application images at incompatible addresses.
Routine `local` and `release` commands remain SD-only.

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

If you want to keep existing device data, retain a verified copy outside the
SD card. A clean installation deliberately discards internal applications
and settings; skip this backup only when those contents are not needed.

## Build CRUB with the shared layout

```bash
git clone https://github.com/wisnc/crub.git
cd crub
git checkout 7819bb27c2a3e29529255fc848562f0b7d071c36
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

The pinned upstream commit is the `3.1.0` release and reports `3.1.0` through
`CRUB_VERSION`.

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
platform, which used PlatformIO Core 6.2.0 in the verified build; `pio run` from
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

Before writing, run `python3 -m firmware_manager doctor` in the manager
checkout. The table must have exactly two app partitions: CRUB's `test` at
`0x10000` (768 KiB), and `extra` (`ota_0`) at `0xd0000` (6.5 MiB). There
must be no `hub` or `codex` app partition. Settings partitions stay at the
addresses shown in the README.

The CRUB application must fit `0xc0000` bytes. The verified 3.1.0 local
build is 753,760 bytes; its upstream release asset is too large for this slot.
The QIO bootloader is 22,528 bytes and must end before `0x8000`. A verified
existing QIO bootloader and raw CRUB application can be reused: both find the
partition table at runtime. Raw application images on SD also need no rebuild
for the changed `extra` offset.

## Clean installation

This step erases **all internal flash**, including application settings. It
leaves the microSD card untouched. Remove the card before provisioning; insert
it again only after CRUB has started. No application is installed by this step.

Generate the table in the firmware-manager checkout:

```bash
python3 tools/prepare_partition_table.py --blank
```

This command only writes `dist/crub-partitions.bin` on the host. Its SHA-256 is
`c5414c7821983a08fc6861fd484b7829a90fd9c63886ecfd317c4ac621a63603`.
The tool refuses a table-only migration from old backups. `--blank` is for
this full erase procedure, not for replacing a live table on its own.

Build a complete 8 MiB image on the host. `--fill-flash-size 8MB` fills all
unused ranges with `0xff`, including the entire `extra` slot and settings:

```bash
python -m esptool --chip esp32s3 merge_bin \
  --output dist/crub-only-8mb.bin --fill-flash-size 8MB \
  0x0 /path/to/crub/.pio/build-qio/bootloader/bootloader.bin \
  0x8000 dist/crub-partitions.bin \
  0x10000 /path/to/crub/.pio/build/m5cardputer/firmware.bin
```

Hold `G0`, press and release Reset, then release `G0`. Substitute the actual
serial port. Write the complete image and verify all 8 MiB before Reset:

```bash
python -m esptool --chip esp32s3 --port /dev/cu.usbmodem101 --no-stub \
  --baud 460800 --before no_reset --after no_reset write_flash -z \
  0x0 dist/crub-only-8mb.bin
python -m esptool --chip esp32s3 --port /dev/cu.usbmodem101 --no-stub \
  --before no_reset --after no_reset verify_flash \
  0x0 dist/crub-only-8mb.bin
```

Writing the full image erases and rewrites the complete internal flash.
A separate `erase_flash` command is unnecessary and is unsupported by the
ESP32-S3 ROM when using `--no-stub`.

Press Reset. CRUB should start. Check `pt info`: `extra` must start at
`0xd0000` and have size `0x680000`. `launch -f extra` should report
`nothing flashed` until an application is installed. Do not use CRUB's
`pt write` or `pt reset`: they replace this shared layout.

## Install applications from SD

Insert the existing FAT32 SD card and run `sd`. Existing raw application
images can be installed as-is. For example, install Hub:

```text
flash /firmware/cardputer-hub.bin extra
launch -f extra
```

To replace Hub with Codex, return to CRUB with Reset and run:

```text
flash /firmware/Codex.bin extra
launch -f extra
```

The current app is replaced; SD image files and the data partitions are kept.
The previous layout's `uphub` alias still names the removed `hub` partition,
so use the direct command until the aliases have been refreshed. Other old
updater aliases that target `extra` keep working.

To refresh SD images and aliases intentionally, use CRUB `usbsd` and a normal
`local` or `release` staging command. Eject the volume, leave `usbsd`, run
`sd`, then choose one `up...` command followed by `go`. Every updater,
including `uphub`, writes only `extra`. Staging preserves unrelated aliases
and unselected images. Obsolete `hub`/`hubfast` aliases on an old card can be
removed manually from `/.crub/aliases`.

An existing `/.crub/boot` file is kept during SD staging. If it enables automatic
app launch, restore the normal menu: boot once without the card, reinsert it,
run `sd`, then `crubmenu` (after alias refresh), and Reset. Without refreshed
aliases, use the direct equivalent:

```text
echo boots 1500 > /.crub/boot
echo fetch >> /.crub/boot
```

These two commands intentionally update the SD boot script. They are separate
from USB provisioning, which never accesses the card.

## Replace only the bootloader

For a device already using the current layout, replacing a DIO bootloader
with the reviewed QIO bootloader does not require erasing flash. Write and
verify only `0x0`, leaving the table, CRUB, `extra`, and settings in place.
The bootloader must end before `0x8000`.

## Updating CRUB

On a device already using this layout, a CRUB update writes only the raw
application at `0x10000`. Verify the reviewed version and its image integrity,
check that it fits `0xc0000`, and verify the written range before Reset.
Keep a backup if the existing internal contents matter. Never flash CRUB's
stock partition table or a merged release image over this shared layout.
A routine `local` or `release` command cannot update CRUB.

## Recovery

Hold `G0`, press and release Reset, then release `G0`. ROM download mode
remains available. Repeat the clean installation, or restore a verified full
8 MiB backup when you want its previous layout and contents:

```bash
python -m esptool --chip esp32s3 --port /dev/cu.usbmodem101 --no-stub \
  write_flash 0x0 cardputer-adv-backup.bin
```
