#!/bin/bash
# Goldfish Brain — one-command installer for macOS.
#   git clone https://github.com/Hydrazinev/goldfish-brain.git && cd goldfish-brain && ./install.sh
# Safe to run again: it only does what is missing, and re-builds the app.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Goldfish Brain"
DEST="/Applications/$APP_NAME.app"
say()  { printf '\n\033[1;33m==>\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31mProblem:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "Goldfish Brain only runs on a Mac."
MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
[ "$MAJOR" -ge 14 ] || fail "Needs macOS 14 or newer (you have $(sw_vers -productVersion))."

say "1/5  Checking Xcode"
if ! command -v xcodebuild >/dev/null || ! xcodebuild -version >/dev/null 2>&1; then
  fail "Xcode isn't installed (or its license isn't accepted). Install Xcode from the App Store (it's free), open it once, then run ./install.sh again."
fi

say "2/5  Installing build tools"
if ! command -v xcodegen >/dev/null; then
  command -v brew >/dev/null || fail "Homebrew is needed to install the build tool. Get it from https://brew.sh then run ./install.sh again."
  brew install xcodegen
fi
PY="$(command -v python3 || true)"
[ -n "$PY" ] || fail "Python 3 not found. Run: xcode-select --install   (then ./install.sh again)"

say "3/5  Setting up the engine (a few minutes the first time)"
cd "$ROOT"
[ -x venv/bin/python ] || "$PY" -m venv venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
echo "Downloading the face-recognition model (about 300 MB, first time only)…"
venv/bin/python - <<'PYEOF'
from insightface.app import FaceAnalysis
FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"]).prepare(ctx_id=-1)
print("Model ready.")
PYEOF

say "4/5  Building the app"
cd "$ROOT/mac"
xcodegen >/dev/null
SIGN=()
if [ ! -f Config/Local.xcconfig ]; then
  SIGN=(CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES)   # no Apple developer account needed
fi
OUT="$ROOT/mac/DerivedData-install"
mkdir -p "$OUT"
pkill -x "$APP_NAME" 2>/dev/null && sleep 2 || true
xcodebuild build -scheme CineID -configuration Release -destination 'platform=macOS' \
  -derivedDataPath "$OUT" ${SIGN[@]+"${SIGN[@]}"} > "$OUT/build.log" 2>&1 \
  || { tail -25 "$OUT/build.log" >&2; fail "The build failed. The full log is at mac/DerivedData-install/build.log"; }
BUILT="$OUT/Build/Products/Release/$APP_NAME.app"
[ -d "$BUILT" ] || fail "Build finished but the app wasn't produced."

say "5/5  Installing to Applications"
rm -rf "$DEST"
cp -R "$BUILT" "$DEST"
open "$DEST"

cat <<MSG

All done. Goldfish Brain is open. Keep this folder where it is — the app uses it as its engine.

Next, in the app:
  1. Allow Screen Recording and Chrome access when asked.
  2. Paste your free TMDB key (and an Anthropic key if you want notes + Ask).
  3. Add a show, then open Netflix in Chrome and press  ⌘⇧X.

Next time, just open "Goldfish Brain" from Spotlight (⌘Space).
MSG
