# CineID as a native Mac app — "the dossier"

Status: draft for approval (2026-11-25). Supersedes the *UI shell* parts of
`2026-11-24-first-run-and-show-setup-design.md` (its engine, API, keys and
show-setup decisions stand and are reused).

## Intent and why

CineID is the owner's own tool (never shared, signed for others, or sold). Today it
is Python started from Terminal, with a web page in a window. It works, but it does
not feel like an app: permissions are granted to Terminal, the popup is a hand-drawn
AppKit view, and it is not creative. The owner has built Venus (a SwiftUI iPad app
with an art-driven, object-based look) and wants CineID to feel like that: a real
Mac app, friendly, with a distinctive look — and the popup that appears while watching
Netflix must be good, because that is 95 % of the use.

## Goals

1. A real `CineID.app`: double-click / Dock / Login Item. No Terminal. Screen Recording
   and Automation permissions belong to "CineID".
2. **The popup.** Press ⌘⇧X while watching Netflix **in Chrome, including full screen**
   and a card appears over the video without switching Spaces, without leaving
   full screen, and without pausing or stealing focus from the video.
3. The look chosen by the owner (mockup option A): **Dossier / Polaroid**. Shows are
   case files, characters are cards, the viewing position is a red stamp
   ("SEEN S02E08"), colours come from the actor's photo (as Venus does from album art).
4. A "that's wrong" correction loop that turns the owner's feedback into labeled real
   frames, better matching, and (later) tuned thresholds.
5. Everything already built keeps working: the Python engine, keys in the Keychain,
   shows, notes (Claude Haiku 5.5), per-show positions.

## Non-goals

- Sharing, App Store, notarization, auto-update, other people's Macs.
- Browsers/apps other than **Google Chrome** (the owner watches Netflix in Chrome).
  Brave/Safari/the Netflix iPad app are out of scope; Safari and native apps likely
  produce black frames (copy protection) and cannot be supported.
- Porting the face engine to Swift (kept as an optional last stage).
- A literal skeuomorphic filing-cabinet metaphor everywhere; the dossier look is a
  style, not a rule that blocks usability.

## Anti-goals (a failure even if it "works")

- The popup grabs keyboard focus, pauses the video, exits full screen, or jumps Spaces.
- Any story detail shown for a character the viewer has not met (the spoiler promise).
- A screen that needs Terminal, `.env` editing, or shows raw errors.
- Rebuilding the app silently loses the macOS permissions the owner granted.

## Constraints

- macOS 26.6 (Darwin 25.6), Apple Silicon, Xcode 27.2, XcodeGen installed.
- Follow Venus's conventions: XcodeGen `project.yml`, git-ignored `Config/Local.xcconfig`
  for the owner's team id / bundle id / repo path, TDD for pure logic, `-demo…` launch
  arguments to preview UI without Netflix, `graphify update .` after code changes.
- Sign with the owner's Personal Team certificate (not ad-hoc) so Screen Recording /
  Automation grants survive rebuilds. *(Believed correct; verify in Stage 1.)*
- Engine unchanged and still the source of truth: Python 3.9 venv, FastAPI on
  127.0.0.1:8000, `/identify`, `/api/*`, keys in the Keychain (service `cineid`).
- No paid-developer-program features.
- Chrome needs *View → Developer → Allow JavaScript from Apple Events* for the
  most reliable episode detection; the app must detect when it's off and fall back
  to on-screen text (OCR) or the manual position.
- Netflix video is only capturable in Chrome with hardware acceleration **off**; the
  app must detect a black frame and walk the owner through the fix.
  *Verified on the owner's Mac (2026-11-25, from Chrome's own settings): hardware
  acceleration is already off and "Allow JavaScript from Apple Events" is already on,
  so on this machine both first-run checks start green and the guide is only a safety
  net (e.g. after a Chrome update resets a setting).*

## Approaches considered

| | What | Trade-off |
|---|---|---|
| A | Keep Python + web UI, polish | Cheapest, but never feels native; popup limits stay. |
| **B (chosen)** | Native SwiftUI app; Python engine kept as a supervised helper | Best friendliness/effort; engine accuracy untouched; needs the repo's Python env on this Mac (fine for one user). |
| C | Everything in Swift, engine ported to ONNX/CoreML | One self-contained app, but re-proving accuracy and rewriting the photo scraper; the benefit only matters for sharing. |

Chosen: **B**, because the owner uses it alone. The Swift↔engine boundary is the
existing local HTTP API, so C stays possible later without touching the UI.

## Design

### Architecture
- `mac/` in this repo: XcodeGen project `CineID` (+ `CineIDTests`). Swift owns: menu bar,
  global hotkey, screen capture, Chrome integration, popup, windows (first run, library,
  add-a-show, settings), Keychain, engine supervision.
- Python owns: recognition, ingest, notes, show data, key validation, feedback storage.
- **Engine supervision:** on launch the app starts `venv/bin/python src/server.py`
  (path from `Local.xcconfig`), polls `/health`, shows "Warming up…" until ready,
  restarts it if it dies, and stops it on quit. If port 8000 is already taken by an
  older CineID, say so plainly instead of failing silently.
- `desktop_app.py` (PyObjC) is retired after Stage 1 is verified; the web UI stays until
  Stage 2 replaces it.

### The popup (the heart of it)
- A borderless **non-activating NSPanel** hosting SwiftUI: window level above
  full-screen content, collection behaviour *can join all Spaces + full-screen auxiliary
  + stationary*, never hides on deactivate. Appears top-right of the screen showing
  Chrome, ~300 pt wide.
- **Front:** photo (circle/polaroid), name, "played by", red stamp with the current
  S/E, match meter, small pill for status. **Back (click flips):** Right now, Last scene,
  Knows, First seen, and 👍 / 👎.
- **States:** "Identifying…" appears instantly; success; several faces → a fan/stack of
  cards (best match on top); *limited* (no notes yet: name/actor only + hint);
  *hidden by spoiler shield*; not recognised; each error in plain language
  (reuse `messages.friendly_error` text, served by the engine or mirrored in Swift).
- **Dismissal:** ✕, click-away, auto-dismiss (default 20 s, configurable), and Esc
  once the card has been clicked (a non-activating panel can take keys without
  activating Chrome, but while it is key, Space no longer pauses Netflix — so it
  only becomes key on click). *(Verify real focus behaviour in Stage 1.)*
- Palette from the actor photo (port of Venus's `ArtworkPalette` idea); paper stays
  cream so text is always readable.

### Netflix in Chrome
- **Capture:** ScreenCaptureKit, the Chrome window or whole screen (setting), downscaled
  to ≤1920 px wide, JPEG q85, sent to `/identify`. Black frame (mean < 8) →
  guided fix card: "Chrome is hiding the video from screenshots → Settings → System →
  turn off graphics acceleration → relaunch Chrome" with a button that opens it.
- **Episode:** first `window.netflix` data via AppleScript→Chrome JS (needs the Developer
  setting), else Vision OCR of the on-screen "S2:E8" text, else the stored position.
  Never launch Chrome if it is closed. Do not move the mouse by default (the old mouse-wiggle
  is dropped unless the episode can't be read any other way — decided in Stage 1).
- **Pause/resume** (Watch Buddy only): only resume if the app paused it.

### Screens (SwiftUI, dossier style on a dark desk)
- **First run — "Open your first case file":** three checks with live ✓ — Screen
  Recording, Chrome automation (+ the Developer setting), Chrome graphics acceleration —
  then keys (TMDB, Anthropic optional) then add a show, then a live "Try it".
- **Library — the drawer:** a folder per show with poster, character count and the
  stamp; pull one out to set Season/Episode (stamp/bookmark control) and make it current.
- **Add a show — new case file:** typewriter search line, poster results, progress as
  steps ("Finding the cast · Learning faces 12 of 30 · Writing notes"), cards dropping
  into the folder; cancel; outcome summary in words. Same engine job API as today.
- **Settings:** keys (Keychain, validated), permissions, popup (auto-dismiss, capture
  window vs screen), Watch Buddy (privacy tick required), correction data.
- Menu bar: current show + stamp, Open CineID, Add a Show, Switch show, Settings, Quit.

### The correction loop ("that's wrong")
- 👎 on the card back → "Who is it?" (the show's characters, or "Not in this show").
- App sends frame + face box + answer to a new engine endpoint `POST /api/feedback`;
  engine stores them locally (`data/feedback/<show_id>/…`, never uploaded anywhere).
- Effects v1: (1) these become the **labeled real-frame eval set** we lack today;
  (2) a corrected single clear face (det_score ≥ 0.7) is added to that character's
  gallery, capped (e.g. ≤ 10 per character) and reversible; (3) a Settings view shows
  wrong-match counts per show. **Automatic threshold tuning is deliberately later.**
- 👍 is stored as a positive example (also eval data).

### Watch Buddy (Stage 4)
Native audio-level trigger and a "sticky note" bubble in the dossier style; the Claude
call stays in the engine (`llm.py`, Haiku 5.5). Paused until the privacy box is ticked.
Pause/resume rules as above.

### Data and privacy
Everything stays on this Mac. Keys in Keychain (the Swift app reads the same items
Python writes). Screenshots go only to the local engine, and to Anthropic only for
Watch Buddy after the owner agreed. `data/` and `Config/Local.xcconfig` are git-ignored.

### Stages (each usable and verified before the next)
1. **Shell + popup:** engine supervision, menu bar, hotkey, capture, Chrome integration,
   dossier popup (front/back, states), full-screen behaviour, existing web UI still
   used for library/add-show (opened in a WKWebView window).
2. **SwiftUI screens:** first run, library, add-a-show, settings; web UI retired.
3. **Correction loop** end to end + first real-frame accuracy numbers.
4. **Watch Buddy** native.
5. *(Optional)* engine port.

### Testing
- XCTest, TDD: Chrome-script output parsing, episode/OCR text parsing, popup state
  machine (stack/flip/dismiss/auto-dismiss), engine supervisor state machine, API client
  decoding, Keychain wrapper (injected backend), palette math.
- Python (pytest): new `/api/feedback` and gallery-merge logic; engine API unchanged.
- UI: `-demo` launch arguments (`-demoPopup front|back|stack|limited|hidden|error`) to
  preview every state without Netflix; screenshot checks in Xcode; a manual checklist
  for what cannot be automated (full-screen Chrome, focus, Spaces, permissions) with an
  explicit "verified / not verified" list in each stage's handoff.

## Calls I made (the owner can overrule)
- Esc works only after a click (protects Space = pause); auto-dismiss 20 s.
- Mouse wiggle dropped by default.
- Chrome only; Brave/Safari/native Netflix app not supported.
- Feedback v1 = collect + capped gallery add; no auto-tuning.
- Keep the web UI until Stage 2 so Stage 1 is usable on its own.

## Left to the builder
Exact colours, fonts, animation timings, hotkey registration API, the precise panel
flags that work on macOS 26, how the engine path is configured, stack/fan visual
details, and the Swift project layout inside `mac/`.
