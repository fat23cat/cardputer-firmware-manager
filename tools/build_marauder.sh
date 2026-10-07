#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
source_commit=468de37d988d79b9a3a0f84c3cc61710adb62186
arduino_cli="${ARDUINO_CLI:-arduino-cli}"
work_dir="$(mktemp -d)"
if [[ "${MARAUDER_KEEP_BUILD:-0}" != 1 ]]; then
  trap 'rm -rf "$work_dir"' EXIT
else
  printf 'Marauder build workspace: %s\n' "$work_dir"
fi

source_dir="$work_dir/ESP32Marauder"
if [[ -n "${MARAUDER_SOURCE_DIR:-}" ]]; then
  cp -R "$MARAUDER_SOURCE_DIR" "$source_dir"
else
  # Fetch the reviewed commit even after the repository's branches advance.
  git init --quiet "$source_dir"
  git -C "$source_dir" remote add origin https://github.com/fat23cat/ESP32Marauder.git
  git -C "$source_dir" fetch --quiet --depth 1 origin "$source_commit"
  git -C "$source_dir" checkout --quiet --detach FETCH_HEAD
fi
actual_commit="$(git -C "$source_dir" rev-parse HEAD)"
if [[ "$actual_commit" != "$source_commit" ]]; then
  printf 'Unexpected Marauder source revision: %s\n' "$actual_commit" >&2
  exit 1
fi
git -C "$source_dir" reset --hard "$source_commit" >/dev/null

libraries="$work_dir/libraries"
if [[ -n "${MARAUDER_LIBRARIES_DIR:-}" ]]; then
  cp -R "$MARAUDER_LIBRARIES_DIR" "$libraries"
else
  mkdir -p "$libraries"
  clone_library() {
    git clone --quiet --depth 1 --branch "$3" \
      "https://github.com/$2.git" "$libraries/$1"
  }
  clone_library CustomESP32Ping marian-craciunescu/ESP32Ping 1.6
  clone_library CustomAsyncTCP ESP32Async/AsyncTCP v3.4.8
  clone_library CustomMicroNMEA stevemarple/MicroNMEA v2.0.6
  clone_library CustomESPAsyncWebServer ESP32Async/ESPAsyncWebServer v3.8.1
  clone_library CustomTFT_eSPI Bodmer/TFT_eSPI V2.5.34
  clone_library CustomXPT2046_Touchscreen PaulStoffregen/XPT2046_Touchscreen v1.4
  clone_library Customlv_arduino lvgl/lv_arduino 3.0.0
  clone_library CustomJPEGDecoder Bodmer/JPEGDecoder 1.8.0
  clone_library CustomNimBLE-Arduino h2zero/NimBLE-Arduino 1.3.8
  clone_library CustomAdafruit_NeoPixel adafruit/Adafruit_NeoPixel 1.12.0
  clone_library CustomArduinoJson bblanchon/ArduinoJson v6.18.2
  clone_library CustomLinkedList ivanseidel/LinkedList v1.3.3
  clone_library CustomEspSoftwareSerial plerup/espsoftwareserial 8.1.0
  clone_library CustomAdafruit_BusIO adafruit/Adafruit_BusIO 1.15.0
  clone_library CustomAdafruit_MAX1704X adafruit/Adafruit_MAX1704X 1.0.2
  cp -R "$source_dir/libraries/Adafruit_TCA8418" "$libraries/CustomAdafruit_TCA8418"
fi

nimble_dir="$libraries/CustomNimBLE-Arduino"
nimble_commit=4a7529eef96bf0f2ff5cb9362304db228e6b6538
if [[ "$(git -C "$nimble_dir" rev-parse HEAD)" != "$nimble_commit" ]]; then
  echo "Unexpected NimBLE source revision" >&2
  exit 1
fi
git -C "$nimble_dir" reset --hard "$nimble_commit" >/dev/null
git -C "$nimble_dir" apply --check "$source_dir/patches/nimble-crub.patch"
git -C "$nimble_dir" apply "$source_dir/patches/nimble-crub.patch"

python3 - "$source_dir" "$libraries/CustomTFT_eSPI" <<'PY'
from pathlib import Path
import shutil
import sys

source, tft = map(Path, sys.argv[1:])
for setup in source.glob("User*.h"):
    shutil.copy2(setup, tft / setup.name)
selector = tft / "User_Setup_Select.h"
text = selector.read_text()
old = "//#include <User_Setup_marauder_m5cardputer_adv.h>"
selected = old[2:]
if text.count(old) == 1:
    selector.write_text(text.replace(old, selected))
elif text.count(selected) != 1:
    raise SystemExit("Marauder TFT setup selector changed")
PY

"$arduino_cli" compile \
  --fqbn 'esp32:esp32:esp32s3:PartitionScheme=min_spiffs,FlashSize=8M,PSRAM=disabled' \
  --libraries "$libraries" \
  --build-property 'compiler.cpp.extra_flags=-DMARAUDER_CARDPUTER_ADV -DCRUB_SHARED_EXTRA' \
  --build-property 'compiler.c.elf.extra_flags=-Wl,-zmuldefs' \
  --warnings none \
  --output-dir "$work_dir/build" \
  "$source_dir/esp32_marauder"

image="$work_dir/build/esp32_marauder.ino.bin"
PYTHONPATH="$project_dir" python3 - "$image" <<'PY'
from pathlib import Path
import sys
from firmware_manager.core import validate_image

image = Path(sys.argv[1])
validate_image("marauder", image, 0x680000, "arduino-lib-builder", "marauder_fs")
if b"marauder_bond" not in image.read_bytes():
    raise SystemExit("isolated Marauder Bluetooth namespace missing from image")
PY
mkdir -p "$project_dir/dist"
cp "$image" "$project_dir/dist/Marauder.bin"
cp "$work_dir/build/esp32_marauder.ino.elf" "$project_dir/dist/Marauder.elf"
shasum -a 256 "$project_dir/dist/Marauder.bin"
