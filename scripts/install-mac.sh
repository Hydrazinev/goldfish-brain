#!/bin/zsh
# Build a signed Release Goldfish Brain.app and install it in /Applications.
# Run once (and again after code changes). Then just open Goldfish Brain like any app.
set -e
ROOT="${0:A:h:h}"
cd "$ROOT/mac"
[ -f Config/Local.xcconfig ] || { echo "Missing mac/Config/Local.xcconfig (team + bundle id). See Local.xcconfig.example."; exit 1; }
command -v xcodegen >/dev/null || { echo "xcodegen not installed (brew install xcodegen)"; exit 1; }
pkill -TERM -f "/Applications/Goldfish Brain.app/Contents/MacOS/Goldfish Brain" 2>/dev/null && sleep 2 || true
xcodegen >/dev/null
OUT="$ROOT/mac/DerivedData-install"
xcodebuild build -scheme CineID -configuration Release -destination 'platform=macOS' \
  -derivedDataPath "$OUT" -allowProvisioningUpdates 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)"
APP="$OUT/Build/Products/Release/Goldfish Brain.app"
[ -d "$APP" ] || { echo "Build did not produce Goldfish Brain.app"; exit 1; }
rm -rf "/Applications/Goldfish Brain.app"
cp -R "$APP" "/Applications/Goldfish Brain.app"
echo "Installed /Applications/Goldfish Brain.app"
codesign -dv "/Applications/Goldfish Brain.app" 2>&1 | grep -E "TeamIdentifier|Identifier="
echo "Open it from Launchpad / Spotlight (⌘Space → Goldfish Brain). It lives in the menu bar."
