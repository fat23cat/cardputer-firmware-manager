# Cardputer Firmware Manager

Host-side firmware bundle manager for an 8 MiB M5Stack Cardputer ADV running
[CRUB](https://github.com/wisnc/crub). It owns the shared partition contract
between [Cardputer Hub](https://github.com/fat23cat/cardputer-hub) and a shared
`extra` application slot that holds
[Codex Microputer ADV](https://github.com/fat23cat/codex-microputer-adv),
[Bruce](https://github.com/BruceDevices/firmware), or another application, and
prepares safe app-only updates on a FAT32 microSD card.

The manager does **not** write the Cardputer's internal flash. It validates and
stages images on the SD card; CRUB performs the final `flash` command on the
device. Routine application updates preserve `apps_nvs`, `hub_config`, and all
other data partitions.

## Requirements

- M5Stack Cardputer ADV with 8 MiB flash and the shared layout from this repo;
- CRUB revision `669f70b219d2b2cb6fd18e952284eb25b2652d62`, with its bootloader
  built in QIO flash mode as described in
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
| `extra` | `0x2d0000` | 4.75 MiB | Codex, Bruce, or another app, one at a time |
| `apps_nvs` | `0x790000` | 64 KiB | Codex settings |
| `hub_config` | `0x7a0000` | 64 KiB | Hub settings |
| `spiffs` | `0x7b0000` | 128 KiB | Bruce LittleFS |

`0x7d0000-0x7effff` is intentionally unallocated so a `vfs` partition can be
added later without moving anything.

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

`local --app all` selects Hub and Codex. Bruce has no local build and is
staged with `release`.

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

For all applications, omit `--app` or pass `--app all`. Independent versions
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
4. Run `uphub` to update Hub, and `upcodex` or `upbruce` to fill `extra`.
5. Wait for `app: ok` and `flash complete` after every command.
6. Launch Hub with `hub`, or the application in the shared slot with `go`.

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
```

Hub is never affected. Codex keeps its settings in `apps_nvs` and on the SD
card, so they survive a switch to Bruce and back. Bruce keeps its files on the
SD card and its internal settings in `spiffs`. Bruce can mount that LittleFS
partition only with the QIO CRUB bootloader; with upstream CRUB's DIO
bootloader, **Files → LittleFS** returns to the main menu and Bruce's settings
reset on every boot.

CRUB does not report which application is in `extra`, and neither can the
manager, which only sees the SD card. There is deliberately no `codex` or
`bruce` launch alias, because it would silently start whichever application
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
- merges its eight aliases without deleting unrelated user aliases, and removes
  the retired `codex`, `codexfast`, `extra`, and `extrafast` aliases only while
  they still hold their original managed commands;
- preserves firmware for applications that were not selected;
- writes `firmware/SHA256SUMS` and `firmware/firmware-manager-lock.json`.

Partition editing, the initial CRUB installation, layout migration, full-flash
restoration, and CRUB updates are deliberately outside routine app commands.
See [Install or recover CRUB](docs/install-crub.md).

## Development

```bash
make check
```

The project intentionally uses only the Python standard library.
