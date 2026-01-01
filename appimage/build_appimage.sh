#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Stream247"
APPDIR="$ROOT/appimage/AppDir"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT/.venv-appimage"

ensure_venv() {
  if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1091
  . "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip wheel
  python -m pip install pyinstaller PySide6 Pillow
}

build_pyinstaller() {
  rm -rf "$ROOT/dist" "$ROOT/build"
  pyinstaller --noconfirm --onefile --windowed --name "$APP_NAME" "$ROOT/Stream247_GUI.py"
}

make_icon() {
  export ROOT
  python - <<'PY'
import os
from pathlib import Path
from PIL import Image

root = Path(os.environ["ROOT"])
src = root / "icon.ico"
dst = root / "appimage" / "Stream247.png"

if not src.exists():
    raise SystemExit("icon.ico not found")

img = Image.open(src).convert("RGBA")
img = img.resize((256, 256), Image.LANCZOS)
dst.parent.mkdir(parents=True, exist_ok=True)
img.save(dst)
PY
}

stage_appdir() {
  rm -rf "$APPDIR"
  mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

  cp "$ROOT/dist/$APP_NAME" "$APPDIR/usr/bin/$APP_NAME"
  cp "$ROOT/appimage/AppRun" "$APPDIR/AppRun"
  chmod +x "$APPDIR/AppRun"

  cp "$ROOT/appimage/Stream247.desktop" "$APPDIR/Stream247.desktop"
  cp "$ROOT/appimage/Stream247.desktop" "$APPDIR/usr/share/applications/Stream247.desktop"

  cp "$ROOT/appimage/Stream247.png" "$APPDIR/Stream247.png"
  cp "$ROOT/appimage/Stream247.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/Stream247.png"
  # AppImage expects a .DirIcon at the AppDir root for the file manager icon.
  ln -sf "Stream247.png" "$APPDIR/.DirIcon"

  # Bundle libtiff and libxcb-cursor if present to avoid Qt plugin crashes on some distros.
  local libtiff_path=""
  local libxcb_cursor_path=""
  if command -v ldconfig >/dev/null 2>&1; then
    libtiff_path="$(ldconfig -p 2>/dev/null | awk '/libtiff\.so\.5/ {print $NF; exit}')"
    libxcb_cursor_path="$(ldconfig -p 2>/dev/null | awk '/libxcb-cursor\.so\.0/ {print $NF; exit}')"
  fi
  if [ -z "$libtiff_path" ]; then
    for cand in /usr/lib*/**/libtiff.so.5 /lib*/**/libtiff.so.5; do
      if [ -f "$cand" ]; then
        libtiff_path="$cand"
        break
      fi
    done
  fi
  if [ -z "$libxcb_cursor_path" ]; then
    for cand in /usr/lib*/**/libxcb-cursor.so.0 /lib*/**/libxcb-cursor.so.0; do
      if [ -f "$cand" ]; then
        libxcb_cursor_path="$cand"
        break
      fi
    done
  fi
  if [ -n "$libtiff_path" ] && [ -f "$libtiff_path" ]; then
    cp "$libtiff_path" "$APPDIR/usr/lib/"
  else
    echo "WARN: libtiff.so.5 not found; AppImage may miss Qt TIFF plugin dependency." >&2
  fi
  if [ -n "$libxcb_cursor_path" ] && [ -f "$libxcb_cursor_path" ]; then
    cp "$libxcb_cursor_path" "$APPDIR/usr/lib/"
  else
    echo "WARN: libxcb-cursor.so.0 not found; AppImage may miss Qt XCB plugin dependency." >&2
  fi
}

build_appimage() {
  if ! command -v appimagetool >/dev/null 2>&1; then
    echo "appimagetool not found in PATH. Install it from https://github.com/AppImage/AppImageKit/releases" >&2
    exit 1
  fi
  appimagetool "$APPDIR" "$ROOT/${APP_NAME}.AppImage"
}

ensure_venv
build_pyinstaller
make_icon
stage_appdir
build_appimage

echo "Built: $ROOT/${APP_NAME}.AppImage"
