#!/usr/bin/env bash
# Build Bruce with the Compact UI (github.com/fat23cat/firmware, branch main)
# from a local checkout and copy the raw application image to dist/BruceCompact.bin.
#
# The source is the sibling ../firmware checkout, or BRUCE_SOURCE_DIR. The build uses whatever is
# checked out there; commit or stash your work first if you want a reproducible image.
#
# FastLED 3.10.5 (allowed by Bruce's `fastled/FastLED @^3.10.3`) does not compile with Bruce's
# `-DFP=1` board flag. Until that is fixed upstream, this script pins FastLED 3.10.3 in
# platformio.ini for the duration of the build and always restores the file afterwards.
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
source_dir="${BRUCE_SOURCE_DIR:-$project_dir/../firmware}"
source_dir="$(cd "$source_dir" && pwd)"
env_name="m5stack-cardputer"
image="$source_dir/.pio/build/$env_name/firmware.bin"
output="$project_dir/dist/BruceCompact.bin"
pio="${PIO:-pio}"

if ! git -C "$source_dir" diff --quiet -- platformio.ini; then
  printf 'platformio.ini in %s has local changes; commit or stash them first\n' "$source_dir" >&2
  exit 1
fi

pinned_dep=$'\tfastled/FastLED @3.10.3'
floating_dep=$'\tfastled/FastLED @^3.10.3'
if grep -qxF "$floating_dep" "$source_dir/platformio.ini"; then
  trap 'git -C "$source_dir" checkout -- platformio.ini' EXIT
  python3 - "$source_dir/platformio.ini" "$floating_dep" "$pinned_dep" <<'PY'
import sys
path, old, new = sys.argv[1:]
text = open(path).read()
lines = text.split("\n")
lines = [new if line == old else line for line in lines]
open(path, "w").write("\n".join(lines))
PY
fi

printf 'Building Bruce Compact from %s (%s%s)\n' "$source_dir" \
  "$(git -C "$source_dir" rev-parse --short HEAD)" \
  "$(git -C "$source_dir" diff --quiet HEAD -- src include boards lib && echo '' || echo ', uncommitted changes')"

# The final elf2image step occasionally fails right after linking; a second run only redoes that step.
(cd "$source_dir" && "$pio" run -e "$env_name") || (cd "$source_dir" && "$pio" run -e "$env_name")

mkdir -p "$project_dir/dist"
cp "$image" "$output"
printf 'Wrote %s (%s bytes)\n' "$output" "$(wc -c < "$output" | tr -d ' ')"
