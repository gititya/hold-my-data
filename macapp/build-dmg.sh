#!/bin/bash
# Wraps dist/Hold My Data.app in dist/HoldMyData-<version>.dmg (app + Applications shortcut).
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)
APP="$ROOT/dist/Hold My Data.app"
VERSION=$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)
DMG="$ROOT/dist/HoldMyData-$VERSION.dmg"
[ -d "$APP" ] || { echo "build the app first: macapp/build-app.sh" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "Hold My Data" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "built $DMG"
