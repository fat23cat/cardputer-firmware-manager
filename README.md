# Cardputer Firmware Manager

Host-side firmware bundle manager for an 8 MiB M5Stack Cardputer ADV running
[CRUB](https://github.com/wisnc/crub). It owns the shared partition contract
between [Cardputer Hub](https://github.com/fat23cat/cardputer-hub) and a shared
`extra` application slot that holds
[Codex Microputer ADV](https://github.com/fat23cat/codex-microputer-adv),
[Bruce](https://github.com/BruceDevices/firmware) (the pinned release or a local
[Compact UI build](#bruce-with-compact-ui)),
[ESP32 Marauder](https://github.com/fat23cat/ESP32Marauder/tree/codex/cardputer-crub-extra), or another
application, and
prepares safe app-only updates on a FAT32 microSD card.

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

Devices provisioned with the earlier layout, which had a 2.5 MiB `hub` and a
dedicated `codex` partition, must be migrated once over USB. See
[Migrate from the dedicated Codex layout](docs/install-crub.md#migrate-from-the-dedicated-codex-layout).

## Partition layout

| Partition | Offset | Size | Contents |
|---|---|---|---|
| `test` | `0x10000` | 768 KiB | CRUB |
| `hub` | `0xd0000` | 2 MiB | Cardputer Hub |
| `extra` | `0x2d0000` | 4.75 MiB | Codex, Bruce, Bruce Compact, or Marauder, one at a time |
| `apps_nvs` | `0x790000` | 64 KiB | Codex settings |
| `hub_config` | `0x7a0000` | 64 KiB | Hub settings |
| `spiffs` | `0x7b0000` | 128 KiB | Bruce LittleFS |
| `marauder_fs` | `0x7d0000` | 128 KiB | Marauder SPIFFS settings |

The existing application offsets and Bruce's `spiffs` partition are unchanged.
The former reserved range at `0x7d0000-0x7effff` now holds Marauder settings.
An existing device needs the one-time table update in
[Install Marauder](docs/install-marauder.md) before launching this build.

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

`local --app all` selects Hub and Codex. Marauder requires its isolated build
and is selected explicitly; Bruce has no local build and is staged with
`release`. The local Bruce Compact UI build is also selected explicitly; see
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

After preparing the Marauder partition table, build and stage the isolated
Marauder image with `local --app marauder --build`. Follow
[Install Marauder](docs/install-marauder.md) for the build tools and one-time
device migration. The official Marauder release image must not be used with
this layout because it formats Bruce's default `spiffs` partition.

## GitHub Releases

Download the latest published release for one application:

```bash
python3 -m firmware_manager release --app hub --sd /Volumes/CARDPUTER
python3 -m firmware_manager release --app bruce --sd /Volumes/CARDPUTER
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
5. Run `uphub` to update Hub, and `upcodex`, `upbruce`, `upbrucec`, or `upmarauder` to fill `extra`.
6. Wait for `app: ok` and `flash complete` after every update command.
7. Launch Hub with `hub`, or the application in the shared slot with `go`.

Every `local` or `release` staging run writes `/firmwares.txt` with the managed
images currently present on the card, and adds `fw` as an alias for
`cat /firmwares.txt`. The short entries show the two commands to use in order:

```text
FIRMWARES ON SD

HUB
start: uphub -> hub

BRUCE
start: upbruce -> go
```

The `up...` command flashes the named image into its application partition;
the second command launches that partition. In particular, `go` launches
whichever application is currently in `extra`. You can edit the text file on
the SD card, but the next staging run regenerates it from the catalog and the
images then present. For a permanent new entry, add the application to
`firmware-manager.json`. The configured list path must be a distinct `.txt`
file at the SD root. Older version 1 catalogs without `firmware_list_path`
and `start` still stage images, without generating this list.

CRUB treats `&&` as an unconditional separator, so update aliases never chain
an automatic launch.

## The shared extra slot

`extra` holds one application at a time. Every staged image stays on the SD
card, so switching only rewrites the slot:

```text
upcodex     # Codex into extra
go          # launch it

upbruce     # later: replace Codex with Bruce
go

upbrucec    # later: replace Bruce with the Bruce Compact UI build
go

upmarauder  # later: replace Bruce with isolated Marauder
go
```

Hub is never affected. Codex keeps its settings in `apps_nvs` and on the SD
card, so they survive a switch to Bruce and back. Bruce keeps its files on the
SD card and its internal settings in `spiffs`. Bruce can mount that LittleFS
partition only with the QIO CRUB bootloader; with upstream CRUB's DIO
bootloader, **Files → LittleFS** returns to the main menu and Bruce's settings
reset on every boot.

The isolated Marauder build keeps its settings in `marauder_fs`. Its built-in
firmware updater is disabled because the next OTA slot is Hub. Use CRUB's
`upmarauder` command for later Marauder updates. The manager checks for the
`marauder_bond` image marker before staging this build, but local builds are not
authenticated releases; build from the pinned source and patch in this repo.
Marauder's Bluetooth bonds and backlight preference use their own namespaces
inside the shared default NVS partition; the build does not clear that NVS.

CRUB does not report which application is in `extra`, and neither can the
manager, which only sees the SD card. There is deliberately no `codex` or
`bruce` or `marauder` launch alias, because it would silently start whichever application
the slot holds.

An application that is not in the catalog can be flashed into the slot by
hand. CRUB accepts raw and merged images; `-nospiffs` prevents a merged image
from overwriting Bruce's `spiffs` partition:

```text
flash /firmware/Other.bin extra -nospiffs
go
```

The manager does not check such images: confirm the SHA-256 yourself and keep
them within the 4.75 MiB slot.

### USB serial diagnostics

CRUB normally initializes its USB mass-storage device before an application is
launched. If an application's USB Serial/JTAG console does not enumerate after
the `hub` or `go` alias, run:

```text
hubfast
```

or

```text
gofast
```

Then reset the Cardputer. `hubfast` replaces `/.crub/boot` with `launch -f`;
`gofast` replaces it with `launch -f extra` (`extra` is not CRUB's default
boot partition, so it must be named explicitly). Either way, the target app
starts before CRUB initializes USB and continues to start automatically on
later resets.

To restore the normal CRUB boot screen, power off the Cardputer, remove the
microSD card, and power it on again. Reinsert the card, run `sd`, then run:

```text
crubmenu
```

Reset once more. `crubmenu` restores the default `boots 1500` and `fetch` boot
commands. All three aliases are installed by the next `local` or `release`
staging run; none of them writes the Cardputer's internal flash.

## Bruce with Compact UI

`brucecompact` is a local build of Bruce from the
[`feat/cardputer-compact-ui`](https://github.com/fat23cat/firmware/tree/feat/cardputer-compact-ui)
branch, which adds a compact layout for the 240x135 screen, switched on in
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
- rejects images larger than their assigned partition;
- verifies every copy and later `doctor --sd` run against SHA-256 metadata;
- verifies GitHub's asset digest or a published checksum asset, and the
  catalog's pinned SHA-256 when it downloads a pinned release;
- merges its managed aliases without deleting unrelated user aliases, and removes
  the retired `codex`, `codexfast`, `extra`, and `extrafast` aliases only while
  they still hold their original managed commands;
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
