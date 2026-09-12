# Changelog

## Unreleased

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
