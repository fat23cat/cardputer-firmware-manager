#!/usr/bin/env python3
"""Prepare, but never flash, the CRUB table with an isolated Marauder SPIFFS."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firmware_manager.core import FirmwareError, load_catalog  # noqa: E402


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


def table_footer(entries: bytes) -> bytes:
    return b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(entries).digest()


def prepare_table(backup: Path, output: Path) -> bytes:
    catalog = load_catalog(ROOT / "firmware-manager.json")
    partitions = catalog["partition_contract"]
    marauder = next(part for part in partitions if part["name"] == "marauder_fs")
    prior = [part for part in partitions if part["name"] != "marauder_fs"]
    if backup.stat().st_size != FLASH_SIZE:
        raise FirmwareError("full backup must be exactly 8 MiB")
    with backup.open("rb") as source:
        source.seek(TABLE_OFFSET)
        installed = source.read(TABLE_SIZE)
        source.seek(marauder["offset"])
        reserved = source.read(marauder["size"])
    if any(byte != 0xFF for byte in reserved):
        raise FirmwareError("future marauder_fs range is not blank in the backup")
    old_entries = b"".join(encode_entry(part) for part in prior)
    expected_old = (
        old_entries + table_footer(old_entries)
    ).ljust(TABLE_SIZE, b"\xff")
    if installed != expected_old:
        raise FirmwareError("backup partition table differs from the expected CRUB layout")
    new_entries = b"".join(encode_entry(part) for part in partitions)
    result = (
        new_entries + table_footer(new_entries)
    ).ljust(TABLE_SIZE, b"\xff")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="exact 8 MiB full-device backup")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist" / "marauder-partitions.bin"
    )
    args = parser.parse_args()
    try:
        data = prepare_table(args.backup, args.output)
    except (FirmwareError, OSError) as error:
        parser.exit(2, f"error: {error}\n")
    print(f"prepared {args.output}: SHA-256 {hashlib.sha256(data).hexdigest()}")
    print("The device flash has not been changed.")


if __name__ == "__main__":
    main()
