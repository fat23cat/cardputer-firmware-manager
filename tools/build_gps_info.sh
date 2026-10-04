#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
source_commit=f16b636ec657b8d1c3fd264544c376a7e6c2a5ad
pio="${PIO:-pio}"
work_dir=""

if [[ -n "${GPS_INFO_SOURCE_DIR:-}" ]]; then
  source_dir="$(cd "$GPS_INFO_SOURCE_DIR" && pwd)"
else
  work_dir="$(mktemp -d)"
  trap 'rm -rf "$work_dir"' EXIT
  source_dir="$work_dir/Cardputer-Adv-GPS-Info"
  git clone --quiet https://github.com/DevinWatson/Cardputer-Adv-GPS-Info.git "$source_dir"
  git -C "$source_dir" checkout --quiet --detach "$source_commit"
fi

actual_commit="$(git -C "$source_dir" rev-parse HEAD)"
if [[ "$actual_commit" != "$source_commit" ]]; then
  printf 'Unexpected GPS Info source revision: %s\n' "$actual_commit" >&2
  exit 1
fi
if ! git -C "$source_dir" diff --quiet HEAD -- src platformio.ini; then
  printf 'GPS Info source or build configuration has local changes\n' >&2
  exit 1
fi

"$pio" run -d "$source_dir" -e m5cardputer-adv
image="$source_dir/.pio/build/m5cardputer-adv/firmware.bin"
PYTHONPATH="$project_dir" python3 - "$image" <<'PY'
from pathlib import Path
import sys

from firmware_manager.core import validate_image

validate_image(
    "gpsinfo", Path(sys.argv[1]), 0x680000, "arduino-lib-builder",
    "Cardputer ADV GPS Info"
)
PY

mkdir -p "$project_dir/dist"
cp "$image" "$project_dir/dist/GPSInfo.bin"
shasum -a 256 "$project_dir/dist/GPSInfo.bin"
