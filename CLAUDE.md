# Goldfish Brain (internal name: CineID) — AI Context

> The app is branded **Goldfish Brain** (public GitHub repo `goldfish-brain`, MIT, site in `site/`, one-command `./install.sh`). Internal identifiers deliberately keep the old name so nothing breaks: Xcode target/module `CineID`, bundle id, Keychain service `cineid`, `CINEID_*` env vars, `~/Library/Logs/CineID`, the Application Support folder. The built product is `Goldfish Brain.app`. Never put personal data in the repo (it is public): `.env`, `data/` DB/config/feedback, `mac/Config/Local.xcconfig` are git-ignored.

## What This Project Is

CineID is a macOS app (personal use, owner watches Netflix in Chrome) that identifies TV show characters using face recognition. Press ⌘⇧X while watching: it screenshots the screen, detects faces, looks up characters, and shows a "dossier" paper-card popup with name, actor, story notes and "Also in" credits — with spoiler protection by the viewer's season/episode. The shipping shell is the native Swift app in `mac/`; it supervises the Python engine (`src/server.py`, port 8000). The older PyObjC shell (`src/desktop_app.py`, `./start.sh`) still works but is legacy. Never run both at once (same port + hotkey).

## Architecture

```
src/
  desktop_app.py       — PyObjC macOS app: menu bar icon, setup wizard, overlay popup
  server.py            — FastAPI REST API (port 8000): /identify, /load-show, /health
  recogniser.py        — InsightFace buffalo_l face detection + embedding
  database.py          — SQLite wrapper (data/cineid.db): shows, characters, arcs
  ingest.py            — Downloads actor photos from TMDB, builds FAISS index
  populate_metadata.py — Claude (via llm.py) writes character notes per episode, grounded in TMDB episode summaries; new shows run it on Sonnet 5.5 (CINEID_NOTES_MODEL overrides), Watch Buddy stays on Haiku 5.5
  setup_show.py        — CLI to add a new show (TMDB scrape + ingest + populate)
  buddy.py             — Watch Buddy: BuddyListener audio thread, Claude vision call (via llm.py), TTS
  llm.py               — the one place that calls Claude (claude-haiku-5-5 default, effort low; per-model thinking via _thinking_for; structured outputs via ask_json(schema=))
  actor_credits.py     — TMDB actor filmography cache ("Also in"), cached per actor in the DB
  episode_lookup.py    — find_season(): which season an episode title/number belongs to (TMDB)
  chrome_scripts.py    — AppleScript/JS snippets to read the Netflix tab (episode detection)
  static_files.py      — NoCacheStaticFiles (web UI must never be served from WKWebView cache)
  edit_menu.py         — Edit menu for the legacy PyObjC window
  ask.py               — (new) ⌘⇧E "Ask": spoiler-safe answer about the current scene (Sonnet 5.5); `mode: "react"` = ⌘⇧R Watch Buddy reaction (persona from config.json `buddy`, last 3 kept in memory, tests/test_react.py)
  config_store.py      — atomic config.json writes (refuses unreadable files), .env parsing, per-show positions
  keystore.py          — API keys in macOS Keychain (service "cineid"), env / .env fallback
  key_check.py         — validates TMDB / Anthropic keys with one cheap request
  api_routes.py        — /api/* JSON for the web UI (state, keys, search, add-a-show job, position, buddy)
  show_jobs.py         — background "add a show" runner; parses @@CINEID JSON events from ingest/populate
  job_events.py        — emit()/parse_line() for those structured progress events
  messages.py          — plain-language error text for the hotkey path
  ui_window.py         — native WKWebView window (falls back to the browser) for the web UI
  ui/                  — the web UI (index.html, app.js, style.css), served by FastAPI at /ui/

tests/               — pytest suite (run: venv/bin/pytest tests/ -q, ~280+ tests): test_bugfixes, test_api, test_show_jobs, test_llm, test_keys, test_positions, test_messages, test_buddy, test_database, test_populate, test_progress, test_static, test_episode_lookup, test_actor_credits, test_identify_also_in, test_notes_db, test_notes_api (+ test_ask, test_relationships, test_feedback, test_recognition_* when present)

data/
  cineid.db            — SQLite database
  config.json          — Current show/season/episode + buddy persona settings
  shows/<show_id>/
    embeddings.faiss   — FAISS vector index of all character face embeddings
    raw/<character_id>/— Actor photos downloaded from TMDB (tmdb_profile.jpg, etc.)
```

## Setup (Fresh Clone)

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
# InsightFace downloads buffalo_l model to models/ on first run (~1GB)
```

## Running the App

```bash
# One command — the desktop app embeds the API server (uvicorn daemon thread
# started in main(), desktop_app.py). Run from Terminal.app, not VSCode.
./start.sh        # or: venv/bin/python src/desktop_app.py

# Server standalone (only for API debugging without the app)
venv/bin/python src/server.py

# Populate arc data for a show (run once per show after setup)
venv/bin/python src/populate_metadata.py --show breaking-bad --all-episodes
```

## Changing Current Episode

Edit `data/config.json` manually — no CLI for this:
```bash
echo '{"show_id": "breaking-bad", "season": 4, "episode": 1}' > data/config.json
```

## Debugging Face Recognition

```bash
# Edit IMG_PATH variable at top of test_recognition.py, then:
venv/bin/python test_recognition.py
# data/last_capture.jpg — last screenshot taken by Cmd+Shift+X, useful for diagnosing misses
curl localhost:8000/health  # verify server is running before testing
```

## Key Commands

```bash
# Add a new show
venv/bin/python src/setup_show.py --show "Show Name"

# Regenerate arc data
venv/bin/python src/populate_metadata.py --show <show_id> --all-episodes

# Syntax check app
venv/bin/python -c "import ast; ast.parse(open('src/desktop_app.py').read())"

# Run tests
venv/bin/pytest tests/ -v
```

## Active Shows

Run `sqlite3 data/cineid.db "SELECT show_id, show_name FROM shows;"` to list loaded shows.

## Database Schema

**characters** — `character_id, show_id, character_name, actor_name, role, first_season, first_episode, last_season, last_episode, importance_rank, relationship_summary, status, arc_type, current_arc, last_seen_description`

**character_arcs** — `character_id, season, episode, status, current_arc, last_seen_description, arc_type`
(Per-episode arc data, looked up at identify time based on current season/episode)

**shows** — `show_id, show_name, tmdb_id, total_characters, ingested_at, status`

## Overlay Popup Design

The popup (`Overlay` class in desktop_app.py) shows:
- Character name + actor in header with avatar thumbnail (circular, from TMDB photos)
- Confidence as a rounded capsule bar (track + colored fill) with monospaced-digit %
- BIOGRAPHY section: relationship_summary
- RIGHT NOW section: current_arc with arc_type pill badge (tinted capsule, e.g. DANGER) and 3px colored left accent bar
- LAST SCENE section: last_seen_description with amber episode pill (S02E04)
- Spoiler shield: characters not yet introduced are hidden
- Background: NSVisualEffectView HUD blur + dark tint + 1px hairline border; solid dark fallback when vibrancy unavailable (`_HAS_VIBRANCY`)
- Section headers are letterspaced caps (kern via attributed strings in `_tf`)
- Enter: 24px slide + fade, 180ms, custom ease-out (`_EASE_OUT` from `objc.lookUpClass("CAMediaTimingFunction")`). Dismiss is instant — keyboard/click-initiated actions are never animated.

**Arc type badges:**
- `danger` → red  `conflict` → orange  `alliance` → green
- `discovery` → sky blue  `deception` → purple
- `loss` → indigo  `turning_point` → amber

Old arc data without arc_type falls back to keyword detection (`_detect_arc_type()`).

## Watch Buddy

`BuddyListener` (src/buddy.py) runs as a daemon thread, monitors audio RMS for silence ≥1.5s, then calls Claude Haiku 5.5 vision with a screenshot + character arc context to generate a friend-like reaction. Response is shown via `BuddyBubble` overlay (bottom-right, auto-dismisses in 8s) and spoken via macOS `say`.

- Persona configured via menu bar → "Watch Buddy Settings…" (saved to `config.json`)
- Personalities: `hype` / `film_nerd` / `chill` / `mac` (default)
- Trigger modes (`trigger_mode` in config): `audio` = silence detection, `timer` = fixed interval (no mic)
- `cooldown_minutes: 0` means "as often as allowed" — floored at 10s in audio mode and 3 min in timer mode (`_effective_cooldown` in buddy.py); each trigger costs a Claude Haiku call. Watch Buddy exists only in the legacy Python app; the Mac app has no auto-triggers (the owner prefers on-demand); its Watch Buddy is ⌘⇧R "React" (AskFlow.reactHotKeyPressed → POST /api/ask mode=react, shown in the Ask card)
- **Audio capture:** `sounddevice` reads the default input device (mic by default). To monitor show audio, install [BlackHole 2ch](https://existential.audio/blackhole/) and route system output through it. See docs/HOW_TO_TRY.md for setup steps.
- Spoiler shield: only characters introduced ≤ current episode are included in the prompt

## macOS Constraints (Darwin 25.4.0)

- No menu bar delegates — causes SIGTRAP crash. Use `setTarget_/setAction_` instead.
- No `AXIsProcessTrusted()` calls — crashes. Accessibility check removed.
- `CGColor` is a property in PyObjC, not a method: `color.CGColor` not `color.CGColor()`
- Use `AppHelper.callAfter()` for all UI updates from background threads.
- SF Symbol: `NSImage.imageWithSystemSymbolName_accessibilityDescription_("film.fill", "CineID")`
- `pynput<1.8` required — newer versions break the global hotkey listener on macOS.

## Environment

`.env` file at project root requires:
```
ANTHROPIC_API_KEY=...   # normally entered in the app (Settings → Keys) and kept in the Keychain; .env is a fallback
TMDB_API_KEY=...
```

Python venv at `venv/`. Always use `venv/bin/python`, never system python.

## Config File

`data/config.json` stores current viewing position and Watch Buddy persona:
```json
{
  "show_id": "breaking-bad",
  "season": 3,
  "episode": 7,
  "buddy": {
    "name": "Alex",
    "personality": "mac",
    "voice": "Samantha",
    "cooldown_minutes": 3,
    "enabled": true,
    "trigger_mode": "audio"
  }
}
```
The overlay uses `show_id`/`season`/`episode` to look up per-episode arc data and apply spoiler shield. The `buddy` key is written automatically on first launch.

## Key Implementation Notes

- Face recognition threshold: 0.5 cosine similarity (`_SIMILARITY_THRESHOLD` in server.py)
- Model: InsightFace `buffalo_l`
- Popup dimensions: 440px wide (`_WIN_W`), height varies with content
- Overlay has rounded corners (16px radius) via layer mask; drawRect_ solid bg only used when vibrancy unavailable
- Setup wizard: H=420, log textarea H=162, Cancel button cancels background setup thread
- populate_metadata uses TQDM_DISABLE=1 for clean subprocess output


## Web UI, keys and adding shows (added Nov 2026)

- The user-facing flow is the web UI (menu bar → Open CineID…): first run (permissions + keys), Add a Show,
  Library (per-show season/episode), Settings (keys, Watch Buddy). Details: docs/superpowers/specs/2026-11-24-first-run-and-show-setup-design.md
  and docs/HOW_TO_TRY.md.
- Keys: TMDB + Anthropic. Anthropic is optional (face ID works without it; character notes and Watch Buddy need it).
- Spoiler shield (server `_visibility`): `hidden` (first appearance ahead of viewer), `limited` (no notes → name/actor only),
  `full`. Watch Buddy is paused until `buddy.disclosure_ack` is true (screenshot goes to Anthropic).
- `config.json` is only written through `config_store` (atomic; never overwrites an unreadable file).
- `data/cineid.db` and `data/config.json` are git-ignored (personal data).
- Check the Claude connection: `venv/bin/python scripts/check_claude.py`.

## Native Mac app (mac/)

SwiftUI/AppKit rewrite of the shell; the Python engine (`src/server.py`, port 8000) stays and the app supervises it. Build/run: `./scripts/run-mac.sh` (see `docs/HOW_TO_TRY_MAC.md`); tests: `cd mac && xcodegen && xcodebuild test -scheme CineID -destination 'platform=macOS' CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=YES`.
- XcodeGen project (`mac/project.yml`); never edit the .xcodeproj. Own signing team goes in untracked `mac/Config/Local.xcconfig`.
- `Flow/IdentifyFlow.swift` is the ⌘⇧X pipeline (permission, engine ready, capture, black frame, episode, identify, popup, 30 s engine timeout). `App/AppEnvironment.swift` wires supervisor, hotkey, popup and menu; nothing starts under XCTest.
- Do not run the Python app and the Mac app together (same port and hotkey).
- Window-only capture: Ask/React upload only the front-most Chrome window (`ScreenCaptureKitCapture.captureChromeWindow`, `ChromeWindowPicker`), never the whole display; no Chrome window means a friendly message, not a fallback. `buddy.share_only_window` (exposed as `share_only_window` in `/api/state`, default true) can allow the whole display; ⌘⇧X keeps whole-display capture (it never uploads).
- React memory (`ask._recent_reactions`) is keyed by show_id and stamped with (season, episode); entries from a later position (rewind) or another show never reach a prompt.
- `characters.bio_user_edited` is set when the notes editor saves a changed bio; `populate_metadata` then keeps that bio (and first appearance) when notes are regenerated.
- `data/last_capture.jpg` is written by the engine on every `/identify` (local, git-ignored, best effort) so feedback reports attach the frame that was identified.
- Regular Dock app (Quit ⌘Q fully stops the engine, mic/hotkeys: privacy requirement). Opens the web UI in a WKWebView (cache cleared on open) at http://127.0.0.1:8000/ui/. Install: `./scripts/install-mac.sh` (signed Release into /Applications).
- Popup = non-activating NSPanel "dossier" card (front/back, "1 of N" chip for several faces). Episode comes from Chrome (AppleScript over ALL windows for the netflix.com/watch tab, never `front window`, never launches Chrome), season via `/api/episodes/season`, fallback OCR/stored position. Known gap: with player controls hidden the episode can't be read (mouse nudge not built).
- Logs: ~/Library/Logs/CineID/app.log (+ last_capture.jpg); `-identifyNow` launch arg is a diagnostic.

## Spoilers, notes and credits (Nov 2026)

- Spoiler model (server.py): `_visibility` full/limited/hidden; `_pick_arc` uses the previous episode's end state ("AS OF START OF EPISODE") until progress ≥ 0.9 of the episode; `_limited_result` strips story fields. Any new feature that builds text from character data or episode summaries MUST go through this logic (run the spoiler-auditor agent after changes).
- Notes: AI notes per episode in `character_arcs` (Sonnet 5.5 for new shows via show_jobs `CINEID_NOTES_MODEL`). The owner can write their own: web UI library → character editor (`GET/PUT /api/characters/{id}/notes`); user rows survive regeneration, a user-edited bio does not yet (populate_metadata overwrites it).
- "Also in": `actor_credits.py` caches TMDB credits per actor; `/identify` returns `also_in` (titles only, the current show excluded).
- Relationship map (web UI, per show), Ask (⌘⇧E, typing; `POST /api/ask`, needs `buddy.disclosure_ack` for the screenshot) and "Wrong?" feedback (`POST /api/feedback` → data/feedback/, git-ignored) were added Nov 25–26; see their tests (test_relationships, test_ask, test_feedback).
- Popup micro-UX rules: instant "Looking…" state, same hotkey / Esc / click-away dismiss, auto-fade 12 s (20 s for Ask) paused on hover, "Spoiler-safe up to SxEy" caption.
- Concurrency: `CharacterDB` uses per-thread SQLite connections (a shared connection segfaulted the engine). `/api/state` permission probe is opt-in (`?permissions=1`) and cached 10 s.
- LLM gotchas: Haiku 5.5 → thinking `disabled`; Sonnet 5.5 rejects `disabled` and needs `between_tools`; use structured outputs (`ask_json(schema=)`) because Haiku answers in prose when unsure.
- Accuracy: engine logs data/accuracy_log.jsonl (no images; GET /api/accuracy/summary); margin rule CINEID_MIN_MARGIN defaults 0 (benchmark showed no wrong names); ingest drops outlier reference photos (src/photo_clean.py, audit with src/audit_photos.py); method + numbers in docs/ACCURACY.md; popup label is 'match strength'.
