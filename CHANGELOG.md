# Changelog

## Unreleased

- Shrink `hub` to 2 MiB and replace the dedicated `codex` partition with a
  4.75 MiB shared `extra` slot for Codex, Bruce, or another application. Move
  `apps_nvs` and `hub_config` to `0x790000` and `0x7a0000`, drop `vfs`, shrink
  `spiffs` to 128 KiB, and document the one-time USB migration.
- Add Bruce as a release-only application staged into `extra` with `upbruce`,
  pinned by default to the reviewed 1.16.1 release and its SHA-256.
- Extract the raw application from verified merged release images.
- Replace the `codex` and `codexfast` aliases with `extra` and `extrafast`, and
  remove the retired aliases from the SD card while they are unmodified.
- Stage only applications with a local build for `local --app all`.
- Tell the user to flash only one application into a shared partition.
- Build CRUB with an isolated PlatformIO 6.2.0, which its floating platform
  now requires, and document slow USB backups, the dark screen after an
  esptool reset, and clearing `spiffs` on a first installation.
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
