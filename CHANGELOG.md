# Changelog

## Unreleased

- Update the isolated Cardputer ADV Marauder build to upstream 1.18.0, pinning
  fork commit `468de37d988d79b9a3a0f84c3cc61710adb62186` in the catalog and build
  recipe. Fetch that commit directly so later branch updates cannot change the
  selected source. Preserve CRUB storage isolation, NimBLE safeguards, keyboard
  handling, and updater guards.
- Reject incomplete ESP segments and footers, incorrect XOR checksums, and
  incorrect embedded SHA-256 digests before SD staging and during `doctor`.
- Prevent partition-table preparation from overwriting its full-device input
  backup, including through symbolic and hard links.
- Bundle the canonical catalog and partition layout in installed wheels,
  resolve custom layouts relative to their catalog, and test the installed
  CLI from a wheel built from a source archive in CI.
- List the `hubfast`, `gofast`, and `crubmenu` boot mode aliases in
  `/firmwares.txt`, so `fw` shows them on the device. The entries come from
  the catalog's `firmware_list_commands`, which must name managed aliases.
- Add a local isolated Meshtastic 2.7.26 build for Cardputer ADV with the Cap
  LoRa-1262 in the shared `extra` slot, staged as `firmware/Meshtastic.bin` and
  flashed with `upmesh`. Shrink `extra` to 4.5 MiB and give Meshtastic a
  dedicated 256 KiB `mesh_fs` LittleFS partition at `0x750000`. Patch it so
  factory reset clears only its own NVS namespaces, Bluetooth bonds use
  `mesh_bond`, and OTA cannot switch to another app slot. Reject the official
  image before SD staging.
- Replace `tools/prepare_marauder_table.py` with
  `tools/prepare_partition_table.py`, which migrates either earlier CRUB table
  to the current one and checks that the application in `extra` still fits.
- Add `brucecompact`, an opt-in local build of Bruce with the Compact UI,
  staged next to the pinned Bruce release as `firmware/BruceCompact.bin` and
  flashed into `extra` with `upbrucec`. It shares Bruce's SD settings and
  `spiffs` partition, requires the Compact UI marker so a release image cannot
  be staged in its place, and is built by `tools/build_bruce_compact.sh`, which
  pins FastLED 3.10.3 for the build only.
- Print each application's catalog update alias after staging instead of
  assuming `up<app id>`.
- Add a local isolated ESP32 Marauder 1.17.0 build for Cardputer ADV in the
  shared `extra` slot. Give it a dedicated 128 KiB `marauder_fs` partition,
  isolate its Bluetooth and backlight NVS namespaces, reject malformed stored
  Bluetooth records, avoid stopping stale NimBLE objects after BLE spam exits,
  disable its updater on the CRUB layout, and provide a guarded one-time table
  preparation tool that verifies a full backup and the unused flash range.
- Pin the Cardputer CRUB fork of Marauder for firmware-specific fixes and
  process one BLE spam payload per UI loop so the keyboard is checked between
  steps. Keep NimBLE's isolation patch with the fork.
- Reject unisolated Marauder images before SD staging and preserve the default
  `local --app all` and published `release --app all` selections.
- Update the pinned CRUB launcher to 3.1.0 and document an application-only
  USB update that preserves the QIO bootloader, shared layout, and other apps.
  The upstream release binary exceeds the 768 KiB launcher partition, so the
  documented update checks a rebuild against that partition before writing.
- Shrink `hub` to 2 MiB and replace the dedicated `codex` partition with a
  4.75 MiB shared `extra` slot for Codex, Bruce, or another application. Move
  `apps_nvs` and `hub_config` to `0x790000` and `0x7a0000`, drop `vfs`, shrink
  `spiffs` to 128 KiB, and document the one-time USB migration.
- Add Bruce as a release-only application staged into `extra` with `upbruce`,
  pinned by default to the reviewed 1.16.1 release and its SHA-256.
- Extract the raw application from verified merged release images.
- Replace the `codex` and `codexfast` aliases with `go` and `gofast`, which
  launch the `extra` slot, and remove retired aliases from the SD card while
  they are unmodified.
- Stage only applications with a local build for `local --app all`.
- Tell the user to flash only one application into a shared partition.
- Build CRUB with an isolated PlatformIO 6.2.0, which its floating platform
  now requires, and document slow USB backups, the dark screen after an
  esptool reset, and clearing `spiffs` on a first installation.
- Build the CRUB bootloader in QIO flash mode on the pinned pioarduino
  `55.03.39` platform, because Bruce cannot mount LittleFS and loses its
  settings under upstream CRUB's DIO bootloader, and document replacing only
  the bootloader on an existing installation.
- Follow Bruce's move from `pr3y/Bruce` to `BruceDevices/firmware`.
- Validate every critical CRUB, application, and persistence partition against
  the catalog contract before accepting the shared layout.
- Require a mounted SD root, contain catalog paths inside it, verify copies
  before replacement, and make `doctor --sd` check recorded SHA-256 metadata,
  including images recorded previously but now missing from the card.
- Reject firmware whose ESP project identity does not match the selected app.
- Isolate local application builds so repositories with different pinned
  ESP-IDF versions can be built together with `local --build --app all`.
- Remount the SD card with CRUB's `sd` command before using staged aliases.

## 0.1.0

- Added a manifest-driven Cardputer Hub and Codex Microputer ADV catalog.
- Added selective local-build and GitHub Release staging for CRUB microSD cards.
- Added raw ESP image, partition-size, and release checksum validation.
- Added alias merging, SHA256SUMS, and staged-version lock metadata.
- Moved ownership of the shared 8 MiB CRUB partition layout and recovery guide
  out of the individual application repositories.
