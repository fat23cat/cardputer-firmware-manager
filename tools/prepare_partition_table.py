#!/usr/bin/env python3
"""Prepare, but never flash, the current CRUB partition table from a full backup."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from firmware_manager.core import (  # noqa: E402
    FirmwareError,
    _esp_image_length,
    load_catalog,
)


ENTRY = struct.Struct("<HBBII16sI")
TABLE_OFFSET = 0x8000
TABLE_SIZE = 0xC00
FLASH_SIZE = 0x800000
EXTRA_SIZE_BEFORE_MESHTASTIC = 0x4C0000
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


def earlier_layouts(partitions: Sequence[dict]) -> List[Tuple[str, List[dict]]]:
    """Return the CRUB tables this tool may replace, newest first."""
    before_meshtastic = [
        dict(partition, size=EXTRA_SIZE_BEFORE_MESHTASTIC)
        if partition["name"] == "extra" else dict(partition)
        for partition in partitions
        if partition["name"] != "mesh_fs"
    ]
    before_marauder = [
        partition for partition in before_meshtastic
        if partition["name"] != "marauder_fs"
    ]
    return [
        ("before Meshtastic", before_meshtastic),
        ("before Marauder", before_marauder),
    ]


def prepare_table(backup: Path, output: Path) -> bytes:
    catalog = load_catalog(ROOT / "firmware-manager.json")
    partitions = catalog["partition_contract"]
    if backup.stat().st_size != FLASH_SIZE:
        raise FirmwareError("full backup must be exactly 8 MiB")
    flash = backup.read_bytes()
    installed = flash[TABLE_OFFSET : TABLE_OFFSET + TABLE_SIZE]
    result = encode_table(partitions)
    if installed == result:
        raise FirmwareError("backup already uses the current partition table")
    previous = next(
        (layout for _, layout in earlier_layouts(partitions)
         if encode_table(layout) == installed),
        None,
    )
    if previous is None:
        raise FirmwareError("backup partition table differs from the expected CRUB layouts")

    old: Dict[str, dict] = {partition["name"]: partition for partition in previous}
    extra = next(partition for partition in partitions if partition["name"] == "extra")
    old_extra = old["extra"]
    length = _esp_image_length(
        flash[old_extra["offset"] : old_extra["offset"] + old_extra["size"]]
    )
    if length is not None and length > extra["size"]:
        raise FirmwareError(
            f"application in extra is {length} bytes and does not fit "
            f"the new {extra['size']}-byte slot"
        )
    for partition in partitions:
        if partition["name"] in old:
            continue
        start = partition["offset"]
        region = flash[start : start + partition["size"]]
        if region.count(0xFF) != len(region):
            raise FirmwareError(
                f"future {partition['name']} range is not blank in the backup"
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="exact 8 MiB full-device backup")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist" / "crub-partitions.bin"
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
