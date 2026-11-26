#!/bin/bash
# Build the Goldfish Brain Mac app and open it. Run from anywhere: ./scripts/run-mac.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAC="$ROOT/mac"
DERIVED="$MAC/DerivedData"

fail() { echo "error: $*" >&2; exit 1; }

command -v xcodegen >/dev/null || fail "xcodegen is not installed. Run: brew install xcodegen"
command -v xcodebuild >/dev/null || fail "Xcode is not installed (xcodebuild not found). Install Xcode from the App Store."
[ -x "$ROOT/venv/bin/python" ] || fail "The Python engine is not set up. Run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"

SIGN_ARGS=()
if [ -f "$MAC/Config/Local.xcconfig" ]; then
  echo "Using your signing settings from mac/Config/Local.xcconfig."
else
  SIGN_ARGS=(CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES)
  echo "WARNING: mac/Config/Local.xcconfig not found, so the app is signed ad hoc."
  echo "         macOS will forget the Screen Recording / Automation permissions after every rebuild."
  echo "         To keep them: cp mac/Config/Local.xcconfig.example mac/Config/Local.xcconfig and set DEVELOPMENT_TEAM."
fi

mkdir -p "$DERIVED"
cd "$MAC"
xcodegen >/dev/null
xcodebuild build -scheme CineID -destination 'platform=macOS' -derivedDataPath "$DERIVED" \
  ${SIGN_ARGS[@]+"${SIGN_ARGS[@]}"} > "$DERIVED/build.log" 2>&1 \
  || { tail -20 "$DERIVED/build.log" >&2; fail "build failed (full log: mac/DerivedData/build.log)"; }

APP="$DERIVED/Build/Products/Debug/Goldfish Brain.app"
[ -d "$APP" ] || fail "build finished but $APP is missing"

# A previous copy would hold port 8000 and the hotkey.
pkill -x "Goldfish Brain" 2>/dev/null && sleep 1 || true
open "$APP" ${1:+--args "$@"}
echo "Goldfish Brain is starting. Look for the film icon in the menu bar (the engine takes up to ~90 s to warm up)."
