#!/usr/bin/env bash
# Build a test ZIP of the integration for manual installation in a HA test instance.
#
# The ZIP contains the folder "anker_solix_official/" and is meant to be
# extracted into /config/custom_components/. The manifest version inside the
# ZIP is marked as a dev build (e.g. 0.1.0-dev-1a2b3c4), so the installed build
# can be identified in Home Assistant. The repository itself is not modified.
#
# Usage: scripts/build_test_zip.sh [output-dir]   (default: dist/)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENT="anker_solix_official"
SRC="$REPO_ROOT/custom_components/$COMPONENT"
OUT_DIR="${1:-$REPO_ROOT/dist}"

BASE_VERSION="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$SRC/manifest.json")"
SHA="$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD)"
SUFFIX="dev-$SHA"
if [ -n "$(git -C "$REPO_ROOT" status --porcelain -- "$SRC")" ]; then
  SUFFIX="$SUFFIX-dirty"
fi
VERSION="$BASE_VERSION-$SUFFIX"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$COMPONENT"
(cd "$SRC" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' -cf - .) \
  | tar -xf - -C "$TMP/$COMPONENT"

python3 - "$TMP/$COMPONENT/manifest.json" "$VERSION" <<'PY'
import re, sys
path, version = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text, n = re.subn(r'("version"\s*:\s*)"[^"]*"', lambda m: f'{m.group(1)}"{version}"', text, count=1)
if n != 1:
    sys.exit("version field not found in manifest.json")
open(path, "w", encoding="utf-8").write(text)
PY

mkdir -p "$OUT_DIR"
ZIP="$OUT_DIR/${COMPONENT}-${VERSION}.zip"
rm -f "$ZIP"
(cd "$TMP" && zip -qr "$ZIP" "$COMPONENT")
echo "$ZIP"
