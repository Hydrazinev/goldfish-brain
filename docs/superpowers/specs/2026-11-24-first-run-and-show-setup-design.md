# CineID — friendly first run, show setup, and everyday UX

Status: draft for approval (2026-11-24)

## Intent and why

CineID is a personal tool: its one user is its author. It will never be
distributed, signed, sold or hosted. But it should feel like a finished
product, in the spirit of Wispr Flow and Cluely: install-free feeling, calm,
no configuration files, nothing that looks like a developer tool. The
sharpest gap today is **adding a show**: it needs a hand-edited `.env`, a
Terminal, a raw log window and knowledge of what a TMDB key is. If adding a
show were something a non-technical friend could do unaided, the rest of the
app would be fine by comparison.

## Goals

1. Launch by double-click (or `./start.sh`); no terminal edits, ever.
2. API keys are entered in the app, checked on the spot, and stored safely.
3. Adding a show = type a name, click a poster, wait for a friendly progress
   view. Works in the background; shows a clear "ready" or "needs attention".
4. Every failure is plain language with a next step. No tracebacks or raw
   logs unless the user asks ("Show details").
5. Hotkey feedback within ~100 ms ("Identifying…"); never a silent wait.
6. Viewing position (show / season / episode) is editable in the UI instead
   of by editing `config.json`.
7. The app never changes the viewer's system without need (mouse, Chrome
   launch, playback) and never sends more of the screen than needed.

## Non-goals

- Code signing, notarization, App Store, auto-update, installers.
- A hosted backend or accounts. All data and keys stay on this Mac.
- A Swift rewrite (kept as a later option; the engine API is reusable).
- Non-Mac platforms; non-Chrome browsers (a later, separate piece of work).

## Anti-goals (failure even if it "works")

- A UI that exposes pipeline internals (stdout, tqdm bars, stack traces).
- A recognised character revealed before the viewer has met them.
- A setup that silently leaves a show half-built and listed as usable.
- Keys written anywhere world-readable or committed to git.

## Constraints

- Existing engine stays: InsightFace buffalo_l, FAISS, SQLite, FastAPI on
  127.0.0.1:8000, native PyObjC popup, Python 3.9 venv, `pynput<1.8`,
  `pyobjc*<12`.
- macOS stability rules in CLAUDE.md apply (no menu delegates; no
  `AXIsProcessTrusted`; UI updates via `AppHelper.callAfter`).
- Face ID itself needs no key. OpenAI is only for character notes
  (`populate_metadata`, gpt-4o-mini) and Watch Buddy (gpt-4o).

## Approaches considered

| | What | Trade-off |
|---|---|---|
| A | Polish the native AppKit UI | Reuses everything but every pixel is hand-placed; rich screens (poster grid, live progress) are slow to build. |
| **B (chosen)** | Keep Python engine + native popup; build wizard / library / settings as a small local web UI shown in a native `WKWebView` window, served by the existing FastAPI | Big UX gain for modest rewrite; one new dependency (`pyobjc-framework-WebKit<12`). |
| C | Native Swift shell + Python helper | Best end state, but a second project, and its main payoff (signing, distribution) does not apply to a personal tool. |

Chosen: **B**, because the user is not distributing, so C's payoff is absent,
and B keeps a clean engine/UI split so C stays possible later.

## Design

### 1. Shell
- Menu bar item stays (static menu, no delegates). Items: *Open CineID*,
  current show + episode, *Add a Show…*, *Quit*.
- *Open CineID* shows one `NSWindow` hosting a `WKWebView` that loads
  `http://127.0.0.1:8000/ui/`. Pages are static HTML/CSS/JS under
  `src/ui/`, served by FastAPI; they call JSON endpoints under `/api/*`.
- **Milestone 0 is a spike**: confirm a `WKWebView` window opens and stays
  stable alongside the pynput hotkey thread on this macOS version. If it
  crashes, fall back to opening the default browser at the same URL (the UI
  is unchanged either way).

### 2. First run (shown only until keys/permissions are settled)
1. *Welcome* — one sentence of what the app does; "Press ⌘⇧X while watching."
2. *Permissions* — Screen Recording (required) and Microphone (optional,
   Watch Buddy audio mode). Each row shows a live ✓ / "Open System Settings"
   button and re-checks when the app regains focus.
3. *Keys* — TMDB key (required to add shows; link + two-line "how to get a
   free key"). OpenAI key (optional: "needed for character notes and Watch
   Buddy"). Each is validated on paste (TMDB `/configuration`, OpenAI model
   list) with ✓ or a specific message ("That key was rejected"). Skippable
   OpenAI → those features show a calm "add an OpenAI key to turn this on".
4. *Add your first show* → flow below.

### 3. Add a show
- Search box with results as you type (debounced): poster, title, year, from
  TMDB. Click a poster to confirm ("Breaking Bad (2008) · 5 seasons").
- Setup runs as a **background job** with three human steps and one overall
  bar: *Finding the cast*, *Learning faces* ("12 of 20"), *Writing character
  notes* (optional step; skipped with a note if no OpenAI key).
- The job reports **structured events** (`step`, `current`, `total`,
  `message`) from the pipeline directly — replacing the current approach of
  filtering subprocess stdout line by line. The window can be closed; the
  menu bar item shows "Setting up Breaking Bad… 60%"; a notification fires on
  completion.
- Outcome summary in words: "20 of 24 characters ready. 4 had no usable
  photos." with *Show details* (the old log) and *Retry missing*.
- *Cancel* stops cleanly and marks the show failed (never left "ingesting").
- A show appears in the library only when `status = ready`.

### 4. Library and position
- Cards per show (poster, "N characters", current S/E). Click to make it
  current. Season/episode **steppers** set the viewing position; "Auto-detect
  from Netflix" toggle stays on by default.
- Switching shows keeps each show's own last position (today it resets to
  S01E01 and loses it — my call).

### 5. Everyday experience (native popup + hotkey)
- On ⌘⇧X the popup appears immediately in an "Identifying…" state, then
  fills in (or the message appears). If the server is still warming up:
  "Warming up — try again in a few seconds" (not "Restart the app").
- Error messages mapped to plain text + one next step (no `Error: <repr>`).
- Esc must dismiss the popup (verify; use an `NSWindow` subclass that can
  become key if currently inert).
- Popup stays as today (design in CLAUDE.md), including the new "Recognised
  someone you haven't met yet — hidden to avoid spoilers" state.

### 6. Watch Buddy privacy
- First enable shows a one-time plain explanation: "Buddy sends a screenshot
  to OpenAI to react to the scene." Setting **Share only the show window**
  (default on) captures just the browser window instead of the whole desktop.

### 7. Keys and data hygiene
- Keys live in the macOS Keychain (`security` CLI, service `cineid`), with
  `.env` still honoured as a fallback for CLI scripts. Provide `.env.example`.
- Stop tracking `data/cineid.db` and `data/config.json` in git; create them
  on first run.
- Launch: keep `./start.sh`; add a tiny `CineID.app` wrapper that runs it so
  macOS permissions attach to a named app rather than Terminal (my call; the
  CLAUDE.md note "run from Terminal.app" exists because of permission
  ownership).

### 8. Testing
- Unit/API tests (pytest, FastAPI `TestClient`): key validation endpoints
  (network mocked), show search, job runner event stream and cancel, show
  status transitions, position persistence per show.
- GUI behaviour (window, popup states, permissions rows, Esc) cannot be
  unit-tested with PyObjC; each milestone ends with a manual checklist run in
  the real app, and I will say plainly which items I could not verify.

## Milestones (in order)
0. WKWebView spike (go / fall back to browser).
1. Key store + `/api/*` for keys, health, permissions status + first-run UI.
2. Structured show-setup job + search + Add-a-show UI.
3. Library, per-show position, steppers.
4. Popup states (loading, friendly errors, Esc), warm-up handling.
5. Buddy privacy option, git/data hygiene, `.env.example`, launcher app.

## Left to the builder
Visual styling and copy details; exact endpoint shapes; how the job runner
wraps `ingest_show` / `populate_metadata` (in-process callbacks vs.
subprocess JSON lines); window-capture implementation for the buddy option.

## Addendum (2026-11-24): Claude replaces OpenAI
After approval the user chose to use their Anthropic key instead of OpenAI, with the cheapest
model for everything. Everywhere this document says "OpenAI key" read **Anthropic (Claude) key**;
Watch Buddy and character notes both call **Claude Haiku 5.5** (`claude-haiku-5-5`) through
`src/llm.py`. Also decided during the build: a show with no character notes still identifies
faces but shows name + actor only ("limited" results) instead of hiding everyone.
