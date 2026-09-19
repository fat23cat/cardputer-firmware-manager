# Cardputer Firmware Manager

Host-side firmware bundle manager for an 8 MiB M5Stack Cardputer ADV running
[CRUB](https://github.com/wisnc/crub). It owns the shared partition contract
between [Cardputer Hub](https://github.com/fat23cat/cardputer-hub) and
[Codex Microputer ADV](https://github.com/fat23cat/codex-microputer-adv), and
prepares safe app-only updates on a FAT32 microSD card.

The manager does **not** write the Cardputer's internal flash. It validates and
stages images on the SD card; CRUB performs the final `flash` command on the
device. Routine application updates preserve `apps_nvs`, `hub_config`, and all
other data partitions.

## Requirements

- M5Stack Cardputer ADV with 8 MiB flash and the shared layout from this repo;
- CRUB revision `669f70b219d2b2cb6fd18e952284eb25b2652d62`;
- FAT32 microSD card mounted through CRUB's `usbsd` command;
- Python 3.9 or newer;
- GitHub access when downloading release assets.

No third-party Python packages are required.

## Quick start

Clone this repository beside the two application repositories:

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
  --sd /Volumes/CARDPUTER
```

The command fails before changing the SD card if a repository has no published
release, no matching raw application asset, or no verifiable SHA-256 digest.
Set `GH_TOKEN` or `GITHUB_TOKEN` when GitHub's anonymous API limit is
insufficient.

## Finish an update on the Cardputer

After staging:

1. Safely eject the SD volume from the computer.
2. Press any Cardputer key to exit `usbsd`.
3. Run `sd` so CRUB remounts the card and reloads the staged aliases.
4. Run `uphub`, `upcodex`, or both.
5. Wait for `app: ok` and `flash complete` after every command.
6. Launch the updated app with `hub` or `codex`.

CRUB treats `&&` as an unconditional separator, so update aliases never chain
an automatic launch.

### Hub and Codex USB serial diagnostics

CRUB normally initializes its USB mass-storage device before an application is
launched. If Cardputer Hub's or Codex's USB Serial/JTAG console does not
enumerate after the `hub` or `codex` alias, run:

```text
hubfast
```

or

```text
codexfast
```

Then reset the Cardputer. `hubfast` replaces `/.crub/boot` with `launch -f`;
`codexfast` replaces it with `launch -f codex` (Codex is not CRUB's default
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
  selected managed application;
- rejects images larger than their assigned partition;
- verifies every copy and later `doctor --sd` run against SHA-256 metadata;
- verifies GitHub's asset digest or a published checksum asset;
- merges its seven aliases without deleting unrelated user aliases;
- preserves firmware for applications that were not selected;
- writes `firmware/SHA256SUMS` and `firmware/firmware-manager-lock.json`.

Partition editing, the initial CRUB installation, full-flash restoration, and
CRUB updates are deliberately outside routine app commands. See
[Install or recover CRUB](docs/install-crub.md).

## Development

```bash
make check
```

The project intentionally uses only the Python standard library.
