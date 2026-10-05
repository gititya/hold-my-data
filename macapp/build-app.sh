#!/bin/bash
# Builds dist/Hold My Data.app: the SwiftUI shell, the engine wheel, and a bundled uv.
# Apple Silicon only. Ad-hoc signed (no Apple Developer account), so a downloaded copy needs
# System Settings > Privacy & Security > Open Anyway on first launch.
set -euo pipefail
cd "$(dirname "$0")"
MAC=$PWD
ROOT=$(cd .. && pwd)
APP="$ROOT/dist/Hold My Data.app"
VERSION=$(grep -m1 '^version' "$ROOT/pyproject.toml" | cut -d'"' -f2)

echo "1/4 engine wheel"
rm -rf "$ROOT/build/engine" && mkdir -p "$ROOT/build/engine"
(cd "$ROOT" && uv build --wheel --out-dir build/engine)

echo "2/4 app"
swift build -c release --arch arm64
BIN=$(swift build -c release --arch arm64 --show-bin-path)/HoldMyData

echo "3/4 bundle"
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/engine"
cp "$BIN" "$APP/Contents/MacOS/HoldMyData"
cp "$(command -v uv)" "$APP/Contents/Resources/uv"
cp "$ROOT"/build/engine/*.whl "$APP/Contents/Resources/engine/"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
sed "s/__VERSION__/$VERSION/g" Info.plist.in > "$APP/Contents/Info.plist"

echo "4/4 sign (ad-hoc)"
codesign --force --sign - "$APP/Contents/Resources/uv"
codesign --force --sign - "$APP"
codesign --verify --strict "$APP"
echo "built $APP"
