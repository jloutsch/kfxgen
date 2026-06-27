#!/bin/bash
# Build the kfxgen Calibre plugin zip from the current source tree.
#
# Usage:
#   ./build_plugin.sh             # builds dist/kfxgen-plugin-<version>.zip
#   ./build_plugin.sh --install   # also installs into the local Calibre
#
# Version is read from plugin/kfxgen/__init__.py (the single source of
# truth shared with the plugin wrapper at plugin/__init__.py).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$ROOT/plugin"
DIST_DIR="$ROOT/dist"

# Parse args
INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    -h|--help)
      sed -n '2,9p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

# Read version from the single source of truth
VERSION="$(python3 -c "
import re, sys
with open('$PLUGIN_SRC/kfxgen/__init__.py') as f:
    src = f.read()
m = re.search(r'version\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)', src)
if not m:
    sys.exit('could not parse version')
print('.'.join(m.groups()))
")"

PLUGIN_NAME="kfxgen-plugin-${VERSION}.zip"
OUT="$DIST_DIR/$PLUGIN_NAME"

echo "Building kfxgen v$VERSION..."

# Build into a temp directory so we can ignore __pycache__ etc.
BUILD_DIR="$(mktemp -d -t kfxgen-build-XXXXXX)"
trap 'rm -rf "$BUILD_DIR"' EXIT

# Copy the entire plugin/ tree (wrapper + kfxgen module + kfxlib_minimal).
# We exclude byte-compiled artifacts and Mac metadata; everything else under
# plugin/ is part of the runtime.
cp -R "$PLUGIN_SRC/." "$BUILD_DIR/"
find "$BUILD_DIR" \
  \( -name '__pycache__' -o -name '*.pyc' -o -name '.DS_Store' \) \
  -exec rm -rf {} + 2>/dev/null || true

# Plugin marker file (required by Calibre's plugin loader)
echo "kfxgen" > "$BUILD_DIR/plugin-import-name-kfxgen.txt"

# Generated about.txt — sourced from the actual license/version.
cat > "$BUILD_DIR/about.txt" <<ABOUT
KFX Output (kfxgen) — Open Source KFX Converter for Calibre

Version: $VERSION
License: GPL v3

Native KFX generation for Kindle devices. No external tools required
for the default path; falls back to Kindle Previewer if installed.

See CHANGELOG.md for release notes.
ABOUT

mkdir -p "$DIST_DIR"
rm -f "$OUT"

# Bundle LICENSE + NOTICE inside the plugin zip so users who install via
# `calibre-customize -a` have the license text and third-party attribution
# alongside the code.
if [ -f "$ROOT/LICENSE" ]; then
  cp "$ROOT/LICENSE" "$BUILD_DIR/LICENSE"
fi
if [ -f "$ROOT/NOTICE" ]; then
  cp "$ROOT/NOTICE" "$BUILD_DIR/NOTICE"
fi

(cd "$BUILD_DIR" && zip -rq "$OUT" .)

echo "✓ Built: $OUT"
ls -lh "$OUT"

if [ "$INSTALL" -eq 1 ]; then
  CALIBRE_CUSTOMIZE="${CALIBRE_CUSTOMIZE:-}"
  if [ -z "$CALIBRE_CUSTOMIZE" ]; then
    if [ -x /Applications/calibre.app/Contents/MacOS/calibre-customize ]; then
      CALIBRE_CUSTOMIZE=/Applications/calibre.app/Contents/MacOS/calibre-customize
    elif command -v calibre-customize >/dev/null 2>&1; then
      CALIBRE_CUSTOMIZE=calibre-customize
    fi
  fi
  if [ -z "$CALIBRE_CUSTOMIZE" ]; then
    echo "calibre-customize not found; set CALIBRE_CUSTOMIZE or install in PATH" >&2
    exit 1
  fi
  echo
  echo "Installing into Calibre..."
  "$CALIBRE_CUSTOMIZE" -a "$OUT"
else
  echo
  echo "To install:"
  echo "  calibre-customize -a \"$OUT\""
  echo "Or rerun with --install."
fi
