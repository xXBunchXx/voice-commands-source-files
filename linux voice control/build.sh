#!/usr/bin/env bash
# Build Echo for Linux using PyInstaller.
# Usage:  ./build.sh
#
# Requirements:
#   pip install pyinstaller vosk pyaudio pystray pillow psutil certifi
#   (xdotool, wmctrl, pactl must be installed via your package manager)

set -e
cd "$(dirname "$0")"

VERSION=$(cat version.txt 2>/dev/null || echo "0.0.0")
OUT_DIR="dist/echo-linux-$VERSION"

echo "==> Building Echo v$VERSION for Linux"

pyinstaller \
  --onefile \
  --name echo \
  --add-data "version.txt:." \
  --add-data "icon.png:." \
  --hidden-import vosk \
  --hidden-import pyaudio \
  --hidden-import pystray \
  --hidden-import pystray._xorg \
  --hidden-import PIL \
  --hidden-import psutil \
  --hidden-import certifi \
  main.py

echo "==> Copying assets to $OUT_DIR"
mkdir -p "$OUT_DIR"
cp dist/echo        "$OUT_DIR/echo"
cp version.txt      "$OUT_DIR/version.txt" 2>/dev/null || true
cp icon.png         "$OUT_DIR/icon.png"    2>/dev/null || true

# Include any Vosk model folders next to the binary
for model_dir in vosk-model-*/; do
  if [ -d "$model_dir" ]; then
    echo "    copying model: $model_dir"
    cp -r "$model_dir" "$OUT_DIR/"
  fi
done

chmod +x "$OUT_DIR/echo"

echo ""
echo "==> Done.  Run with:  $OUT_DIR/echo"
echo "    Or for development:  python main.py"
