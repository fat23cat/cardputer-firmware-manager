# Cardputer Firmware Manager

Host-side firmware bundle manager for an 8 MiB M5Stack Cardputer ADV running
[CRUB](https://github.com/wisnc/crub). It owns the shared partition contract
for one shared `extra` application slot that holds
[Cardputer Hub](https://github.com/fat23cat/cardputer-hub),
[Codex Microputer ADV](https://github.com/fat23cat/codex-microputer-adv),
[Bruce](https://github.com/BruceDevices/firmware) (the pinned release or a local
[Compact UI build](#bruce-with-compact-ui)),
[ESP32 Marauder](https://github.com/fat23cat/ESP32Marauder/tree/468de37d988d79b9a3a0f84c3cc61710adb62186),
[Cardputer ADV GPS Info](https://github.com/DevinWatson/Cardputer-Adv-GPS-Info),
[MeshCore](https://github.com/MultiMote/meshcore-cardputer-adv) (the pinned BLE release),
[Meshtastic](https://github.com/meshtastic/firmware) (an isolated local build), or another
application. It prepares safe app-only updates on a FAT32 microSD card. A clean
installation contains only CRUB; `extra` starts empty. Hub is installed and replaced just
like every other application.

The manager does **not** write the Cardputer's internal flash. It validates and
stages images on the SD card; CRUB performs the final `flash` command on the
device. Routine application updates preserve `apps_nvs`, `hub_config`, and all
other data partitions.

## Requirements

- M5Stack Cardputer ADV with 8 MiB flash and the shared layout from this repo;
- CRUB 3.1.0 (revision `7819bb27c2a3e29529255fc848562f0b7d071c36`)
  with a QIO CRUB bootloader as described in
  [Build CRUB with the shared layout](docs/install-crub.md#build-crub-with-the-shared-layout);
- FAT32 microSD card mounted through CRUB's `usbsd` command;
- Python 3.9 or newer;
- GitHub access when downloading release assets.

No third-party Python packages are required.

To install the CLI in a Python environment, run `python3 -m pip install .`
from this checkout. The installed `cardputer-firmware` command includes the
catalog and partition layout, so `cardputer-firmware list` and
`cardputer-firmware doctor` work outside the repository. Local builds still
need the application checkouts and their build tools. Set `--workspace` to
their parent directory, for example:

```bash
cardputer-firmware local --app hub --workspace /path/to/personal \
  --sd /Volumes/CARDPUTER
```

When run from this checkout, the default workspace remains its parent
directory. For an installed CLI, it defaults to the current directory. A
custom `--catalog /path/to/catalog.json` resolves its relative `layout` path
against the directory containing that catalog.

Devices with an earlier dedicated `hub` or `codex` partition need a one-time
[clean installation over USB](docs/install-crub.md#clean-installation).
Replacing only the partition table is insufficient: the `extra` address changed.
Routine `local` and `release` commands continue to stage SD files only.

## Partition layout

| Partition | Offset | Size | Contents |
|---|---|---|---|
| `test` | `0x10000` | 768 KiB | CRUB |
| `extra` (`ota_0`) | `0xd0000` | 6.5 MiB | Hub, Codex, Bruce, Bruce Compact, Marauder, GPS Info, MeshCore, or Meshtastic, one at a time; initially empty |
| `mesh_fs` | `0x750000` | 256 KiB | Meshtastic LittleFS |
| `apps_nvs` | `0x790000` | 64 KiB | Codex settings |
| `hub_config` | `0x7a0000` | 64 KiB | Hub settings |
| `spiffs` | `0x7b0000` | 128 KiB | Bruce LittleFS |
| `marauder_fs` | `0x7d0000` | 128 KiB | Marauder SPIFFS settings |

There is no dedicated Hub application partition. All settings partitions keep
their addresses from the previous Hub + 4.5 MiB `extra` layout. A clean install
clears internal settings; later app-only switches preserve them. The SD images
are independent of the application slot's address and need no rebuild for this
layout change.

## Short development commands

Run `make` for help. These commands use Python 3 and Make; npm is not required.

```bash
make build                        # Hub and Codex, no SD required
make build APP=brucecompact        # local Bruce Compact UI
make flash APP=hub                 # build, validate and stage on mounted SD
make flash APP="hub codex" SD="/Volumes/My Card"
make stage APP=hub                 # stage existing build
make release APP=bruce             # download and stage official Bruce
make release APP=meshcore          # stage pinned MultiMote MeshCore BLE release
make doctor                       # catalog and shared layout
make doctor-sd                    # also validate mounted SD
make check
```

`SD` defaults to `/Volumes/CARDPUTER`; `WORKSPACE` defaults to the parent of
this checkout. Build commands and image validation come from the catalog.
The `build` CLI also works directly: `python3 -m firmware_manager build --app hub`.
`flash`, `stage`, and `release` run doctor before and after SD staging and stop
on any failure. `make -n flash APP=hub` previews the commands.

`flash` prepares the SD card; finish installation on the Cardputer after safe
ejection: leave `usbsd`, run `sd`, then the printed `uphub`, `upcodex`, or
`upbrucec` command, followed by `go`. These targets do not write the device's
internal flash over USB. Hub and Codex are the default local selections;
`APP=brucecompact` selects the locally modified Bruce, while `APP=bruce` selects
its official release. No `npm install` step is needed.

## Quick start

Clone this repository beside the two locally built application repositories:

```text
personal/
├── cardputer-firmware-manager/
├── cardputer-hub/
└── codex-microputer-adv/
```

Inspect the catalog and partition contract:

```bash
python3 -m firmware_manager list
python3 -m firmware_manager doctor
```

On the Cardputer, boot CRUB and enter `usbsd`. Then stage both already-built
local images on macOS:

```bash
python3 -m firmware_manager local \
  --app all \
  --sd /Volumes/CARDPUTER
```

`local --app all` selects Hub and Codex. GPS Info, Marauder, and Meshtastic
require explicit selection; Marauder and Meshtastic also require their isolated
builds. Bruce has no local build and is staged with `release`. The local Bruce
Compact UI build is also selected explicitly; see
[Bruce with Compact UI](#bruce-with-compact-ui).

Add `--build` to build each selected repository first:

```bash
python3 -m firmware_manager local --app all \
  --build --sd /Volumes/CARDPUTER
```

Each repository owns its build wrapper and pinned ESP-IDF installation. The
manager removes inherited ESP-IDF variables before every build, so Hub 5.5.5
and Codex 5.5.3 can be built together without one toolchain leaking into the
other. Complete each repository's one-time setup before using `--build`.

Stage only one local application:

```bash
python3 -m firmware_manager local --app hub --sd /Volumes/CARDPUTER
python3 -m firmware_manager local --app codex --sd /Volumes/CARDPUTER
```

With the current shared layout installed, build and stage the isolated
Marauder image with `local --app marauder --build`. Follow
[Install Marauder](docs/install-marauder.md) for the build tools and device
layout. The official Marauder release image must not be used with
this layout because it formats Bruce's default `spiffs` partition.

## GitHub Releases

Download the latest published release for one application:

```bash
python3 -m firmware_manager release --app hub --sd /Volumes/CARDPUTER
python3 -m firmware_manager release --app bruce --sd /Volumes/CARDPUTER
python3 -m firmware_manager release --app meshcore --sd /Volumes/CARDPUTER
```

Pin an exact release tag:

```bash
python3 -m firmware_manager release --app hub \
  --tag hub=v0.11.0 --sd /Volumes/CARDPUTER
```

For all published applications, omit `--app` or pass `--app all`. Marauder has
only a local isolated build; its official release is excluded. Independent versions
can be pinned by repeating `--tag`:

```bash
python3 -m firmware_manager release --app all \
  --tag hub=v0.11.0 \
  --tag codex=v0.12.0 \
  --tag bruce=1.16.1 \
  --sd /Volumes/CARDPUTER
```

Bruce publishes a merged full-flash image. The manager verifies the asset's
GitHub SHA-256 digest, then extracts the raw application from the partition
table inside the image and stages only that application as
`firmware/Bruce.bin`. Bruce's ESP project name is the generic
`arduino-lib-builder`, so its identity rests on the release checksum.

Because Bruce is a third-party project, the catalog pins it to a reviewed
release through `release_pin`: without `--tag`, `release` downloads that tag
and rejects the asset unless it matches the pinned SHA-256. `--tag bruce=TAG`
with another tag stages that release after GitHub's digest check only. To move
the default to a new Bruce release, review it and update both `tag` and the
merged asset's `sha256` in `firmware-manager.json`. Hub and Codex are not
pinned and follow their latest release.

MeshCore uses the reviewed MultiMote `2026.7.3` BLE release based on MeshCore
1.16.0. The catalog pins the raw application SHA-256 and excludes merged
images. Settings, identity, contacts, channels, and chat history are stored
on the SD card; it must remain inserted during use. `upmeshcore` writes only
`extra`, and `go` starts it. No partition migration is needed. See
[Install MeshCore](docs/install-meshcore.md) for staging, storage boundaries,
BLE pairing, and recovery. The upstream author has paused development.

The command fails before changing the SD card if a repository has no published
release, no matching application asset, or no verifiable SHA-256 digest.
Set `GH_TOKEN` or `GITHUB_TOKEN` when GitHub's anonymous API limit is
insufficient.

## Finish an update on the Cardputer

After staging:

1. Safely eject the SD volume from the computer.
2. Press any Cardputer key to exit `usbsd`.
3. Run `sd` so CRUB remounts the card and reloads the staged aliases.
4. Run `fw` to read the firmware list on the SD card.
5. Choose exactly one updater: `uphub`, `upcodex`, `upbruce`, `upbrucec`, `upmarauder`, `upgpsinfo`, or `upmesh`. All write `extra`.
6. Wait for `app: ok` and `flash complete` after every update command.
7. Run `go` to launch the application currently in `extra`.

Every `local` or `release` staging run writes `/firmwares.txt` with the managed
images currently present on the card, and adds `fw` as an alias for
`cat /firmwares.txt`. The short entries show the two commands to use in order:

```text
FIRMWARES ON SD

HUB
start: uphub -> go

BRUCE
start: upbruce -> go

BOOT MODES (apply on reset)
gofast: auto-boot extra (USB log)
crubmenu: CRUB menu; boot w/o SD 1st
```

The `up...` command flashes the named image into its application partition;
the second command launches that partition. In particular, `go` launches
whichever application is currently in `extra`. The boot mode entries come
from the catalog's `firmware_list_commands` and summarize
[USB serial diagnostics](#usb-serial-diagnostics). You can edit the text file on
the SD card, but the next staging run regenerates it from the catalog and the
images then present. For a permanent new entry, add the application to
`firmware-manager.json`. The configured list path must be a distinct `.txt`
file at the SD root. Older version 1 catalogs without `firmware_list_path`
and `start` still stage images, without generating this list.

CRUB treats `&&` as an unconditional separator, so update aliases never chain
an automatic launch.

### Existing SD cards from the dedicated Hub layout

The old `uphub` alias targets a partition that no longer exists. Until the next
staging run refreshes the aliases, use these direct CRUB commands:

```text
sd
flash /firmware/cardputer-hub.bin extra
launch -f extra
```

The direct commands use the existing image as-is. Other existing `up...`
aliases that explicitly target `extra` still work; `go` still launches it.
The old `hub` and `hubfast` aliases should no longer be used. A staging run
updates `uphub` and preserves unrelated aliases, unselected firmware images,
and `/.crub/boot`. If that boot
file previously enabled automatic app launch, restore the CRUB menu using
`crubmenu` as described below.

## The shared extra slot

`extra` holds one application at a time, including Hub. Every staged image stays
on the SD card, so switching only rewrites the slot:

```text
uphub       # Hub into extra
go

upcodex     # later: replace Hub with Codex
go          # launch it

upbruce     # later: replace Codex with Bruce
go

upbrucec    # later: replace Bruce with the Bruce Compact UI build
go

upmarauder  # later: replace Bruce with isolated Marauder
go

upgpsinfo   # later: replace Marauder with GPS Info
go

upmesh      # later: replace GPS Info with isolated Meshtastic
go
```

Switching replaces the current application, including Hub. Hub keeps its
settings in `hub_config`. Codex keeps its settings in `apps_nvs` and on the SD
card, so they survive a switch to Bruce and back. Bruce keeps its files on the
SD card and its internal settings in `spiffs`. Bruce can mount that LittleFS
partition only with the QIO CRUB bootloader; with upstream CRUB's DIO
bootloader, **Files → LittleFS** returns to the main menu and Bruce's settings
reset on every boot.

The isolated Marauder build keeps its settings in `marauder_fs`. Its built-in
firmware updater is disabled; this layout has a single OTA slot. Use CRUB's
`upmarauder` command for later Marauder updates. The manager checks for the
`marauder_bond` image marker before staging this build, but local builds are not
authenticated releases; build from the pinned source and patch in this repo.
Marauder's Bluetooth bonds and backlight preference use their own namespaces
inside the shared default NVS partition; the build does not clear that NVS.

The isolated Meshtastic build keeps its node database and configuration in
`mesh_fs`, and its Bluetooth bonds in the `mesh_bond` NVS namespace. Its factory
reset clears only Meshtastic's own NVS namespaces, and its OTA update is
disabled. The official Meshtastic image and web flasher must not be used on this
layout. See [Install Meshtastic](docs/install-meshtastic.md).

CRUB does not report which application is in `extra`, and neither can the
manager, which only sees the SD card. There is deliberately no `hub`, `codex`,
`bruce`, `marauder`, `gpsinfo`, or `meshtastic` launch alias, because it would silently start whichever application
the slot holds.

## Cardputer ADV GPS Info

GPS Info 2.1.0 is built from the pinned upstream revision in
`firmware-manager.json`. It has no published binary release. The build needs
PlatformIO (`pio`), GitHub access for the source and dependencies, and enough
disk space for the Arduino toolchain. In CRUB, run `usbsd`, then on the Mac:

```bash
python3 -m firmware_manager local --app gpsinfo --build --sd /Volumes/CARDPUTER
python3 -m firmware_manager doctor --sd /Volumes/CARDPUTER
```

Safely eject the card, exit `usbsd`, and run `sd`, `fw`, `upgpsinfo`, then `go` on the
Cardputer. `upgpsinfo` writes only the shared `extra` application partition.
The data partitions stay intact; the selected application previously in
`extra` is replaced, while its image remains on the SD card. To switch back,
run its `up...` alias followed by `go`.

The upstream firmware expects a Cap LoRa-1262 GPS module on UART2 (RX pin 15,
TX pin 13, 115200 baud by default). Its on-screen pin and baud configuration
lasts only until restart. The v2.1.0 source reads the IMU calibration offset
from the shared default NVS partition but does not erase or initialize it.
No new partition or layout migration is needed.

An application that is not in the catalog can be flashed into the slot by
hand. CRUB accepts raw and merged images; `-nospiffs` prevents a merged image
from overwriting Bruce's `spiffs` partition:

```text
flash /firmware/Other.bin extra -nospiffs
go
```

The manager does not check such images: confirm the SHA-256 yourself and keep
them within the 6.5 MiB slot.

### USB serial diagnostics

CRUB normally initializes its USB mass-storage device before an application is
launched. If an application's USB Serial/JTAG console does not enumerate after
`go`, run:

```text
gofast
```

Then reset the Cardputer. `gofast` replaces `/.crub/boot` with
`launch -f extra`. The current application starts before CRUB initializes USB
and continues to start automatically on later resets. This mode is optional;
normal provisioning leaves CRUB as the boot menu.

To restore the normal CRUB boot screen, power off the Cardputer, remove the
microSD card, and power it on again. Reinsert the card, run `sd`, then run:

```text
crubmenu
```

Reset once more. `crubmenu` restores the default `boots 1500` and `fetch` boot
commands. Both aliases are installed by the next `local` or `release`
staging run; none of them writes the Cardputer's internal flash.

## Bruce with Compact UI

`brucecompact` is a local build of the
[Bruce fork](https://github.com/fat23cat/firmware/tree/main). Its `main` branch
includes the merged Compact UI changes for the 240x135 screen, switched on in
**Config → Display & UI → Compact UI**. It sits next to the pinned release, so
both images stay on the SD card:

```text
upbruce     # pinned Bruce release into extra
go

upbrucec    # Bruce Compact UI build into extra
go
```

Both are the same application for the device: they run from `extra`, read the
same `/bruce.conf` and files on the SD card, and use the same `spiffs` LittleFS
partition, so settings, WiFi credentials and themes carry over in both
directions. The release does not know the `uiCompact` setting; it ignores it and
drops it the next time it saves settings, after which the Compact UI build
starts with the compact layout off until it is switched on again.

Build and stage it from a `firmware` checkout next to this repository (or set
`BRUCE_SOURCE_DIR`), with [PlatformIO](https://platformio.org/) (`pio`) on the
`PATH`:

```bash
git -C ../firmware switch feat/cardputer-compact-ui
python3 -m firmware_manager local --app brucecompact \
  --build --sd /Volumes/CARDPUTER
```

`tools/build_bruce_compact.sh` builds whatever is checked out and copies the raw
application to `dist/BruceCompact.bin`; without `--build` the manager stages the
existing `dist/BruceCompact.bin`. FastLED 3.10.5, which Bruce's
`fastled/FastLED @^3.10.3` dependency resolves to, does not compile with
Bruce's `-DFP=1` board flag, so the script pins FastLED 3.10.3 in the checkout's
`platformio.ini` for the build only and always restores the file. It refuses to
run when that file has local changes.

Both Bruce images share the ESP project name `arduino-lib-builder`, so the
manager accepts a `brucecompact` image only when it contains the Compact UI
menu marker. A release image staged as `brucecompact` is rejected before the SD
card is changed.

## Safety model

Before writing to the SD card, the manager:

- requires an existing mounted output directory;
- keeps every catalog-provided output path inside that mounted SD root;
- accepts only raw ESP application images whose app descriptor identifies the
  selected managed application, after extracting the application from a
  verified merged release image when the catalog says the release is merged;
- checks that every ESP segment and footer is complete, verifies the segment
  XOR checksum and, when present, the image's embedded SHA-256; truncated or
  corrupt images are rejected by both staging and `doctor --sd`, even if the
  SD metadata records their current digest;
- rejects images larger than their assigned partition;
- verifies every copy and later `doctor --sd` run against SHA-256 metadata;
- verifies GitHub's asset digest or a published checksum asset, and the
  catalog's pinned SHA-256 when it downloads a pinned release;
- merges its managed aliases without deleting unrelated user aliases;
- preserves firmware for applications that were not selected;
- rejects firmware list paths that overlap application images or CRUB files;
- writes `firmware/SHA256SUMS`, `firmware/firmware-manager-lock.json`, and
  `/firmwares.txt`.

Partition editing, the initial CRUB installation, layout migration, full-flash
restoration, and CRUB updates are deliberately outside routine app commands.
See [Install or recover CRUB](docs/install-crub.md).

## Development

```bash
make check
```

The project intentionally uses only the Python standard library.
The wheel smoke test additionally needs the build tools `setuptools>=68`
and `wheel`; install them with `python3 -m pip install 'setuptools>=68' wheel`.
With those tools available, `make check` builds a wheel from a source archive
and runs the installed CLI outside the checkout. Otherwise that test is
skipped. CI installs the build tools and runs it on both supported Python
versions in its matrix.
