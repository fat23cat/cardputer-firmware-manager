#!/usr/bin/env bash
# Build Meshtastic for the CRUB shared extra slot from the pinned upstream release.
# patches/meshtastic-crub.patch moves its LittleFS to mesh_fs, keeps factory reset out of
# other applications' NVS namespaces and disables the OTA loader switch.
# patches/nimble-meshtastic-crub.patch gives its Bluetooth bonds the mesh_bond namespace.
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
source_tag=v2.7.26.54e0d8d
source_commit=54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb
env_name=m5stack-cardputer-adv
pio="${PIO:-pio}"
work_dir="$(mktemp -d)"
if [[ "${MESHTASTIC_KEEP_BUILD:-0}" != 1 ]]; then
  trap 'rm -rf "$work_dir"' EXIT
else
  printf 'Meshtastic build workspace: %s\n' "$work_dir"
fi

source_dir="$work_dir/meshtastic"
if [[ -n "${MESHTASTIC_SOURCE_DIR:-}" ]]; then
  cp -R "$MESHTASTIC_SOURCE_DIR" "$source_dir"
else
  git clone --quiet --depth 1 --branch "$source_tag" \
    https://github.com/meshtastic/firmware.git "$source_dir"
fi
actual_commit="$(git -C "$source_dir" rev-parse HEAD)"
if [[ "$actual_commit" != "$source_commit" ]]; then
  printf 'Unexpected Meshtastic source revision: %s\n' "$actual_commit" >&2
  exit 1
fi
git -C "$source_dir" reset --hard "$source_commit" >/dev/null
git -C "$source_dir" apply --check "$project_dir/patches/meshtastic-crub.patch"
git -C "$source_dir" apply "$project_dir/patches/meshtastic-crub.patch"

"$pio" pkg install -d "$source_dir" -e "$env_name"
nimble_dir="$source_dir/.pio/libdeps/$env_name/NimBLE-Arduino"
if ! grep -qx 'version=1.4.3' "$nimble_dir/library.properties"; then
  echo "Unexpected NimBLE-Arduino version" >&2
  exit 1
fi
patch -d "$nimble_dir" -p1 --forward --dry-run --quiet \
  < "$project_dir/patches/nimble-meshtastic-crub.patch"
patch -d "$nimble_dir" -p1 --forward --quiet \
  < "$project_dir/patches/nimble-meshtastic-crub.patch"

"$pio" run -d "$source_dir" -e "$env_name"

# Meshtastic names its outputs firmware-<env>-<version>.bin, next to a merged .factory.bin.
build_dir="$source_dir/.pio/build/$env_name"
images=()
for candidate in "$build_dir/firmware-$env_name-"*.bin; do
  [[ "$candidate" == *.factory.bin ]] || images+=("$candidate")
done
if [[ "${#images[@]}" -ne 1 || ! -f "${images[0]}" ]]; then
  echo "Expected one Meshtastic application image in $build_dir" >&2
  exit 1
fi
image="${images[0]}"
PYTHONPATH="$project_dir" python3 - "$image" <<'PY'
from pathlib import Path
import sys
from firmware_manager.core import validate_image

image = Path(sys.argv[1])
validate_image("meshtastic", image, 0x680000, "arduino-lib-builder", "mesh_bond")
if b"mesh_fs" not in image.read_bytes():
    raise SystemExit("isolated Meshtastic filesystem label missing from image")
PY
mkdir -p "$project_dir/dist"
cp "$image" "$project_dir/dist/Meshtastic.bin"
cp "${image%.bin}.elf" "$project_dir/dist/Meshtastic.elf"
shasum -a 256 "$project_dir/dist/Meshtastic.bin"
