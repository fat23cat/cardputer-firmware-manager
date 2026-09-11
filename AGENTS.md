# Agent instructions

This repository owns the host-side firmware catalog, shared CRUB partition
layout, SD staging behavior, and installation documentation for an 8 MiB
M5Stack Cardputer ADV.

Keep routine application operations SD-only. Never add an internal-flash erase,
partition write, or bootloader update to `local` or `release`. New behavior must
start with a failing observable test and finish with `make check` passing.

When an application partition, filename, build command, release asset, or
persistence boundary changes, update `firmware-manager.json`, the layout when
applicable, and user documentation together. Preserve unrelated CRUB aliases
and unselected application images.
