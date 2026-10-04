#!/usr/bin/env python3
"""Prepare the CRUB-only layout for a clean installation; never contact the device."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firmware_manager.core import (  # noqa: E402
    FirmwareError,
    load_catalog,
)


ENTRY = struct.Struct("<HBBII16sI")
TABLE_OFFSET = 0x8000
TABLE_SIZE = 0xC00
FLASH_SIZE = 0x800000
SUBTYPES = {
    "nvs": (1, 0x02),
    "ota": (1, 0x00),
    "test": (0, 0x20),
    "ota_0": (0, 0x10),
    "ota_1": (0, 0x11),
    "spiffs": (1, 0x82),
    "coredump": (1, 0x03),
}


def encode_entry(partition: dict) -> bytes:
    kind, subtype = SUBTYPES[partition["subtype"]]
    return ENTRY.pack(
        0x50AA,
        kind,
        subtype,
        partition["offset"],
        partition["size"],
        partition["name"].encode("ascii")[:16].ljust(16, b"\0"),
        0,
    )


def encode_table(partitions: Sequence[dict]) -> bytes:
    entries = b"".join(encode_entry(partition) for partition in partitions)
    footer = b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(entries).digest()
    return (entries + footer).ljust(TABLE_SIZE, b"\xff")


def prepare_table(backup: Path, output: Path) -> bytes:
    if backup.resolve() == output.resolve() or (
        output.exists() and backup.samefile(output)
    ):
        raise FirmwareError("output must not overwrite the full backup")
    catalog = load_catalog(ROOT / "firmware-manager.json")
    partitions = catalog["partition_contract"]
    if backup.stat().st_size != FLASH_SIZE:
        raise FirmwareError("full backup must be exactly 8 MiB")
    flash = backup.read_bytes()
    installed = flash[TABLE_OFFSET : TABLE_OFFSET + TABLE_SIZE]
    result = encode_table(partitions)
    if installed == result:
        raise FirmwareError("backup already uses the current partition table")
    raise FirmwareError(
        "changing to the single extra slot requires a full erase and reprovision; "
        "a partition-table-only migration would leave applications at wrong addresses. "
        "Use --blank only with the clean-install procedure in docs/install-crub.md"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("backup", nargs="?", type=Path,
                        help="check a previous backup; table-only migration is refused")
    source.add_argument("--blank", action="store_true",
                        help="generate the table for a full erase and CRUB-only install")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist" / "crub-partitions.bin"
    )
    args = parser.parse_args()
    try:
        if args.blank:
            catalog = load_catalog(ROOT / "firmware-manager.json")
            data = encode_table(catalog["partition_contract"])
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(data)
        else:
            data = prepare_table(args.backup, args.output)
    except (FirmwareError, OSError) as error:
        parser.exit(2, f"error: {error}\n")
    print(f"prepared {args.output}: SHA-256 {hashlib.sha256(data).hexdigest()}")
    print("The device flash has not been changed.")


if __name__ == "__main__":
    main()
