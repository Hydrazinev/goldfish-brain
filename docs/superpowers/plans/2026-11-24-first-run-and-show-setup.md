# CineID First Run, Show Setup & Everyday UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, this session). Steps use checkbox (`- [ ]`) syntax. **No git commits** unless the user asks — the user has not asked; leave changes in the working tree.

**Goal:** A non-technical person can launch CineID, paste two keys, search a show, click a poster and watch a friendly progress view — and everyday use (hotkey, popup, errors, position) is calm and clear.

**Architecture:** Python engine and native PyObjC popup stay. A local web UI (`src/ui/`, plain HTML/CSS/JS, no build step) is served by the existing FastAPI app on 127.0.0.1:8000 under `/ui/`, talking to JSON endpoints under `/api/*` defined in a new testable router. A native `WKWebView` window hosts it (fallback: default browser). Show setup runs the existing `ingest.py` / `populate_metadata.py` as subprocesses that print structured `@@CINEID {json}` event lines, parsed by an in-process job runner.

**Tech Stack:** Python 3.9, FastAPI, PyObjC (`pyobjc-framework-WebKit<12` new), SQLite, vanilla JS, pytest, Playwright (for verifying the web UI).

**Spec:** `docs/superpowers/specs/2026-11-24-first-run-and-show-setup-design.md`

## Global Constraints

- Python 3.9 venv at `venv/`; always `venv/bin/python`. Keep `pynput<1.8`, `pyobjc*<12`.
- Server binds `127.0.0.1` only. Keys never logged, never in git, never returned by any API (only `{"set": bool}`).
- macOS rules (CLAUDE.md): no menu delegates, no `AXIsProcessTrusted`, UI updates from threads via `AppHelper.callAfter`.
- UI copy is plain language: no tracebacks, no raw log unless the user opens "Show details".
- A show is listed/usable only when `shows.status = 'ready'`.
- All `config.json` writes go through `config_store` (atomic; refuses to overwrite unreadable files).
- Face ID needs no key; OpenAI is optional (character notes + Watch Buddy only).
- GUI behaviour that cannot be exercised here (WKWebView window, popup, permissions prompts) must be reported as **unverified** in the final handoff.

## Review Focus

- Search with empty / whitespace / 300-char / punctuation-heavy query, or with no TMDB key set → clear JSON message, never a 500.
- Pasted key with surrounding whitespace/newline or wrapped in quotes → trimmed before validation and storage.
- Starting a second setup while one runs → HTTP 409 with a readable message; same show already ready → allowed only as an explicit "update", never silently wiping it.
- Show names with accents or slashes ("Élite", "Dark/Matter") → safe slug, no path traversal in `data/shows/`.
- App killed or crashed mid-setup → on next start the show is `failed`, not stuck `ingesting` / listed.
- OpenAI key missing, invalid or rate-limited during notes → face ID still ready; summary says exactly how many characters lack notes.
- `config.json` missing or corrupt → `/api/state` still answers and the UI shows first-run, not an error page.

---

### Task 1: WKWebView spike (go / fall back)

**Files:**
- Modify: `requirements.txt` (add `pyobjc-framework-WebKit<12`)
- Create: `scripts/spike_webview.py`

**Interfaces:** Produces: a yes/no on whether a `WKWebView` window loads and runs JS alongside this app's thread model; decides the default `ui_mode` ("window" or "browser") used in Task 7.

- [ ] **Step 1:** `venv/bin/pip install "pyobjc-framework-WebKit<12"`; confirm `venv/bin/python -c "import WebKit"` succeeds.
- [ ] **Step 2:** Write `scripts/spike_webview.py`: start `NSApplication` (accessory policy), open an `NSWindow` with a `WKWebView` loading `data:text/html,<title>ok</title>`, start a `pynput.keyboard.GlobalHotKeys` listener thread (mirrors the app), after 2 s evaluate `document.title`, print `SPIKE_RESULT <title>`, exit 0.
- [ ] **Step 3:** Run `timeout 20 venv/bin/python scripts/spike_webview.py`. Expected: `SPIKE_RESULT ok`. Record outcome. If it crashes or prints nothing: default `ui_mode` = `"browser"` and note in handoff.

### Task 2: Key store and key validation

**Files:**
- Create: `src/keystore.py`, `src/key_check.py`, `tests/test_keys.py`

**Interfaces:**
- Produces (`keystore`): `KEYS = ("TMDB_API_KEY", "OPENAI_API_KEY")`; `set_key(name: str, value: str, runner=subprocess.run) -> None`; `get_key(name: str, env_path: Path, runner=subprocess.run) -> str` (Keychain service `cineid` → `os.environ` → `.env` via `config_store.read_env_key`); `delete_key(name, runner=...) -> None`; `export_to_environ(env_path: Path, runner=...) -> None` (sets `os.environ` for both keys when found); `clean_key(raw: str) -> str` (strip whitespace/newlines and one pair of surrounding quotes).
- Produces (`key_check`): `validate_tmdb(key: str, opener=urllib.request.urlopen) -> tuple[bool, str]` (GET `https://api.themoviedb.org/3/configuration?api_key=…`; 401 → `(False, "TMDB rejected that key — check you copied the API Key (v3 auth), not the Read Access Token.")`; network error → `(False, "Couldn't reach TMDB — check your internet connection.")`); `validate_openai(key, opener=...) -> tuple[bool, str]` (GET `https://api.openai.com/v1/models` with Bearer; 401 → `"OpenAI rejected that key."`).

- [ ] **Step 1:** Tests in `tests/test_keys.py`: `test_clean_key_strips_whitespace_and_quotes` (`' "sk-abc"\n'` → `sk-abc`); `test_set_then_get_uses_keychain_runner` with a fake runner recording `security add-generic-password -U -s cineid -a TMDB_API_KEY -w <v>` and returning the value for `find-generic-password -w`; `test_get_key_falls_back_to_env_file` (runner returns rc 44); `test_validate_tmdb_401_message`, `test_validate_tmdb_network_error_message`, `test_validate_openai_ok` with fake openers raising `urllib.error.HTTPError(…, 401, …)` / `URLError` / returning 200.
- [ ] **Step 2:** Run `venv/bin/pytest tests/test_keys.py -q` → FAIL (modules missing).
- [ ] **Step 3:** Implement both modules. Keychain via `security` CLI only; never put the key in logs or exception text.
- [ ] **Step 4:** Run tests → PASS.

### Task 3: Structured setup events and the job runner

**Files:**
- Create: `src/job_events.py`, `src/show_jobs.py`, `tests/test_show_jobs.py`
- Modify: `src/ingest.py` (emit events; optional `poster` handled by runner, not ingest), `src/populate_metadata.py` (emit events), `src/database.py` (add `poster_path` column migration, `set_show_poster(show_id, poster_path)`, `fail_stale_ingests() -> int`), `src/server.py` (call `fail_stale_ingests()` in lifespan)

**Interfaces:**
- Produces (`job_events`): `emit(event: str, **fields) -> None` prints `@@CINEID ` + compact JSON `{"event": event, **fields}` to stdout only when env `CINEID_EVENTS=1` (no-op otherwise); `parse_line(line: str) -> dict | None`.
- Events: ingest emits `cast` (`total`), `faces` (`current`, `total`, `name`, `ok: bool`), `faces_done` (`ingested`, `skipped`); populate (`--all-episodes`) emits `notes` (`current`, `total`, `name`, `ok`), `notes_done` (`ok`, `failed`, `usable`, `total`).
- Produces (`show_jobs`): `class SetupJob` snapshot via `ShowSetupRunner.status() -> dict | None` with keys `show_id, show_name, state ("running"|"done"|"failed"|"cancelled"), step ("cast"|"faces"|"notes"|"finishing"), current, total, percent (0–100 int), message (plain English), summary (str|None), details (list[str], last 400 lines)`.
  `class ShowSetupRunner(db_path, project_root, env_provider: Callable[[], dict], spawn=subprocess.Popen, on_ready: Callable[[str], None])` with `start(show_name: str, tmdb_id: int, poster_path: str | None, with_notes: bool) -> str` (returns show_id; raises `JobBusy` if one is running), `cancel() -> None`, `status()`.
  Percent mapping: with notes → faces 0–60, notes 60–100; without → faces 0–100. Messages: `"Finding the cast…"`, `"Learning faces — 12 of 20"`, `"Writing character notes — 5 of 20"`. Summary: `"{n} of {t} characters ready. {k} had no usable photos."` and, if notes enabled, `"{u} of {t} have character notes."`.
  Show id: `ingest._slugify(show_name)`. Subprocess env = `os.environ` + `env_provider()` + `CINEID_EVENTS=1` + `TQDM_DISABLE=1`. Cancel → terminate process group, mark show `failed` via DB, state `cancelled`. Ingest exit ≠ 0 → state `failed`, message `"Couldn't finish setting up {name}. Show details has the technical reason."` Populate failure after a good ingest → state `done` with the notes shortfall in the summary.

- [ ] **Step 1:** Tests (`tests/test_show_jobs.py`) using a fake `spawn` that yields scripted stdout lines: `test_progress_maps_to_percent_with_notes` (faces 10/20 → 30%, notes 10/20 → 80%); `test_without_notes_faces_map_to_full_range`; `test_second_start_raises_job_busy`; `test_ingest_failure_sets_failed_and_message`; `test_populate_failure_keeps_show_ready_and_reports_shortfall`; `test_cancel_marks_show_failed_not_ingesting`; `test_non_event_lines_go_to_details_only`. In `tests/test_bugfixes.py` add `test_fail_stale_ingests_marks_ingesting_failed` and `test_slug_is_safe_for_accents_and_slashes` (`"Dark/Matter"` and `"Élite"` produce `[a-z0-9-]+`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `job_events`, runner (reader thread parses lines; `on_ready(show_id)` called after a successful ingest so the server loads the index), DB methods, event emission in the two scripts, and the lifespan call. If `_slugify` is not already safe for the review-focus names, fix it.
- [ ] **Step 4:** Run `venv/bin/pytest tests -q` → all PASS.

### Task 4: Per-show position in config

**Files:**
- Modify: `src/config_store.py`, `src/desktop_app.py` (`_save_position`, `onSwitchShow_`), `tests/test_bugfixes.py`

**Interfaces:**
- Produces: `config_store.set_position(path: Path, show_id: str, season: int, episode: int) -> dict` (writes top-level `show_id/season/episode` AND `positions[show_id] = {season, episode}`); `config_store.select_show(path: Path, show_id: str) -> dict` (sets `show_id`, restores `positions[show_id]` or S1E1); `config_store.positions(cfg: dict) -> dict`.

- [ ] **Step 1:** Tests: `test_select_show_restores_that_shows_last_position`, `test_set_position_updates_current_and_map`, `test_select_unknown_show_defaults_to_s1e1`.
- [ ] **Step 2:** FAIL → **Step 3:** implement; make `_save_position(cfg)` in `desktop_app.py` call `set_position`; make `onSwitchShow_` call `select_show` (remove the hard reset to S1E1). → **Step 4:** PASS; `venv/bin/python -c "import ast; ast.parse(open('src/desktop_app.py').read())"`.

### Task 5: API router and server wiring

**Files:**
- Create: `src/api_routes.py`, `tests/test_api.py`
- Modify: `src/server.py` (include router, mount `/ui/` static from `src/ui/`, call `keystore.export_to_environ`, pass deps), `src/database.py` if needed

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: `build_router(deps: ApiDeps) -> fastapi.APIRouter` where `ApiDeps` is a dataclass with `config_path, env_path, get_db: Callable[[], CharacterDB], keystore, key_check, runner: ShowSetupRunner, tmdb_get: Callable[[str, dict], dict]` (injectable for tests), `permissions: Callable[[], dict]`.
- Endpoints (all JSON; errors are `{"ok": false, "message": "<plain English>"}` with a proper status, never a stack trace):
  - `GET /api/state` → `{setup: {tmdb_key: bool, openai_key: bool}, permissions: {screen_recording: bool|None}, current: {show_id, show_name, season, episode}, shows: [{show_id, show_name, total_characters, poster_url|None, season, episode, current: bool}], job: <status>|None, needs_first_run: bool}` (`needs_first_run` = no TMDB key or no ready show).
  - `POST /api/keys` `{name, value}` → validates (blank → 400 `"Paste your key first."`), stores on success; `{ok, message}`. `DELETE /api/keys/{name}`.
  - `GET /api/shows/search?q=` → `{results: [{tmdb_id, name, year, poster_url, overview}]}` (≤8; blank q → `{results: []}`; no key → 400 `"Add your TMDB key first."`).
  - `POST /api/shows/setup` `{tmdb_id, name, poster_path, with_notes, update?: bool}` → `{ok, show_id}`; 409 if job running (`"Another show is still being set up."`) or show already ready and `update` not true (`"{name} is already set up. Use Update to refresh it."`); `with_notes` forced false (with note in message) when no OpenAI key.
  - `GET /api/shows/job`, `POST /api/shows/job/cancel`.
  - `POST /api/shows/select` `{show_id}`; `POST /api/position` `{show_id, season ≥1, episode ≥1}` (400 on non-positive).
  - `GET/POST /api/buddy` (read/merge `buddy` block via `config_store`; fields: `name, personality, voice, cooldown_minutes, enabled, trigger_mode, share_only_window, disclosure_ack`).
  - `GET /api/permissions` → `{screen_recording: bool|None}`.

- [ ] **Step 1:** Tests with a fake `ApiDeps` and `TestClient`: state with empty config, with corrupt config (`needs_first_run` true, 200); key save with `"  sk-x\n"` trimmed and invalid key rejected without storing; search blank/no-key/long query; setup 409 cases; position validation; poster URL built as `https://image.tmdb.org/t/p/w342{poster_path}`; `GET /api/state` never contains a key value.
- [ ] **Step 2:** FAIL → **Step 3:** implement router + wire into `server.py` (`app.include_router`, `StaticFiles(directory=src/ui, html=True)` at `/ui`; `GET /` redirects to `/ui/`). Real `permissions()` runs `screencapture -x -t jpg <tmp>` and returns `rc == 0 and file non-empty`, `None` on exception. → **Step 4:** `venv/bin/pytest tests -q` PASS.

### Task 6: Web UI

**Files:**
- Create: `src/ui/index.html`, `src/ui/app.js`, `src/ui/style.css`

**Interfaces:** Consumes Task 5 endpoints. Hash routes: `#/welcome` (3 steps: Welcome → Permissions → Keys), `#/add`, `#/library`, `#/settings`. Default route: `#/welcome` if `needs_first_run`, else `#/library`.

Behaviour (the spec's screens): welcome sentence "Press ⌘⇧X while watching"; permission row with live ✓ / "Open System Settings" (`x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture`) and re-check on window focus; key fields with inline ✓/✗ messages from `/api/keys`, OpenAI marked optional with the "turns on character notes and Watch Buddy" line; add-show search-as-you-type (250 ms debounce) with poster cards, confirm panel, a "Write character notes (uses your OpenAI key)" checkbox (disabled with reason if no key); progress view polling `/api/shows/job` every 1 s with bar, step message, "Show details" disclosure, Cancel, and a final summary + "Try it now" panel; library cards with poster, count, S/E steppers (call `/api/position`), "Use this show"; settings: buddy fields incl. `share_only_window` and the one-time disclosure checkbox. Calm dark theme following `prefers-color-scheme`, system font stack, 16px gutters, works at 420 px width.

- [ ] **Step 1:** Start the server with a temp data dir and a fake TMDB (env `CINEID_TMDB_BASE` override used only by tests) OR the real keys; open `http://127.0.0.1:8000/ui/` with Playwright.
- [ ] **Step 2:** Verify with Playwright: first-run flow reachable with no keys; invalid key message appears; search shows poster cards; progress view renders from a scripted job; library steppers persist position (check via `GET /api/state`); no console errors; screenshots at 1000 px and 420 px widths saved to the scratchpad and inspected.
- [ ] **Step 3:** Fix anything found; re-run Step 2 until clean.

### Task 7: Native shell — window host, loading state, friendly errors, warm-up, Esc

**Files:**
- Create: `src/ui_window.py`, `src/messages.py`, `tests/test_messages.py`
- Modify: `src/desktop_app.py` (menu: *Open CineID*, current show line, *Add a Show…* opens `#/add`, *Watch Buddy Settings…* opens `#/settings`; hotkey shows "Identifying…" immediately; use `messages.friendly_error`; warm-up message; `_OverlayWindow(NSWindow)` with `canBecomeKeyWindow → True`; show first-run UI automatically when `needs_first_run`)

**Interfaces:**
- Produces (`messages`): `friendly_error(exc: BaseException, *, server_age_s: float) -> str` — maps `RuntimeError("drm_blocked")` to the existing DRM text; `urllib.error.URLError`/connection refused with `server_age_s < 90` → `"CineID is warming up — try again in a few seconds."`, otherwise `"CineID's engine isn't responding. Quit and reopen CineID."`; `HTTPError 404` → `"{show} hasn't been set up yet — open CineID → Add a Show."` (caller passes show name via `show: str | None = None` kwarg); `HTTPError 5xx` → `"Something went wrong on our side. Try again."`; `subprocess.TimeoutExpired` → `"Taking too long — try again."`; anything else → `"Something went wrong. Try again."` (never the raw repr).
- Produces (`ui_window`): `open_ui(route: str = "") -> None` — `ui_mode` from env `CINEID_UI` (`window`|`browser`), default from Task 1; window mode creates/reuses one `NSWindow`+`WKWebView` at `http://127.0.0.1:8000/ui/#/<route>`; any exception falls back to `webbrowser.open`.

- [ ] **Step 1:** `tests/test_messages.py`: one test per mapping above (including warm-up vs. not-responding by `server_age_s`, and that no returned string contains `Traceback` or `Error:`).
- [ ] **Step 2:** FAIL → **Step 3:** implement `messages`, `ui_window`, and the `desktop_app.py` edits (record `_APP_STARTED = time.time()`; hotkey handler calls `AppHelper.callAfter(self._overlay.show_message, "Identifying…")` before the work starts). → **Step 4:** tests PASS; syntax-check `desktop_app.py`; run `venv/bin/python src/desktop_app.py` for 8 s under `timeout` and confirm it starts without a traceback, `curl 127.0.0.1:8000/health` answers, and `curl -I 127.0.0.1:8000/ui/` returns 200.

### Task 8: Watch Buddy privacy — window-only capture and disclosure

**Files:**
- Modify: `src/buddy.py`, `tests/test_bugfixes.py`

**Interfaces:**
- Produces: `buddy._pick_window_id(windows: list[dict], owner: str = "Google Chrome") -> int | None` (largest on-screen layer-0 window of that owner, from `Quartz.CGWindowListCopyWindowInfo` dicts with keys `kCGWindowOwnerName`, `kCGWindowNumber`, `kCGWindowBounds{Width,Height}`, `kCGWindowLayer`); `_capture_b64(window_only: bool = False)` uses `screencapture -l<id>` when a window id is found, else full screen; `_generate_buddy_message` reads `buddy.share_only_window` (default `True`) and returns `None` unless `buddy.disclosure_ack` is true (print a one-line reason).

- [ ] **Step 1:** Tests: `test_pick_window_id_prefers_largest_layer0_chrome_window`, `test_pick_window_id_none_when_no_chrome`, `test_buddy_message_skipped_without_disclosure_ack` (monkeypatch capture + OpenAI to assert neither is called).
- [ ] **Step 2:** FAIL → **Step 3:** implement; add `share_only_window: True, disclosure_ack: False` to `_DEFAULT_BUDDY`. → **Step 4:** PASS.

### Task 9: Hygiene, launcher and docs

**Files:**
- Modify: `.gitignore`, `CLAUDE.md`, `start.sh`
- Create: `.env.example`, `CineID.app/Contents/MacOS/CineID` (executable wrapper), `CineID.app/Contents/Info.plist` (`LSUIElement` true), `docs/HOW_TO_TRY.md`

- [ ] **Step 1:** `git rm --cached data/cineid.db data/config.json`; add `data/cineid.db`, `data/config.json`, `data/*.db-*` to `.gitignore`; confirm both files still exist on disk.
- [ ] **Step 2:** `.env.example` lists the two keys, noting the app's Keychain entry is preferred. Wrapper script `cd`s to the repo root and `exec ./start.sh`; mark it **untested for macOS permission ownership** in `docs/HOW_TO_TRY.md`.
- [ ] **Step 3:** Update CLAUDE.md (architecture list, new modules, key storage, UI mode, how to run tests) and write `docs/HOW_TO_TRY.md` with the exact verified steps and an explicit "not verified here" list.

### Task 10: Final verification

- [ ] **Step 1:** `venv/bin/pytest tests -q` → all PASS (record the count).
- [ ] **Step 2:** Start the real server; confirm `/health`, `/api/state`, `/ui/` respond; replay the `/identify` frame check (Saul Goodman ≈ 0.68, ≈180 ms).
- [ ] **Step 3:** Run one real end-to-end show setup through `POST /api/shows/setup` with a small show only if the user's keys validate, capped by env `CINEID_MAX_CAST` (add to `ingest.py` as a developer knob, default unset) to ≤5 characters and `with_notes` false; watch `GET /api/shows/job` reach `done`; confirm the show appears in `/api/state` and its index loads; then delete that test show's rows/files. If keys don't validate, say so and skip.
- [ ] **Step 4:** `graphify update .`; final report lists verified vs unverified items.

## Self-review notes

- Spec coverage: shell/window (T1, T7), first run (T5, T6), add show + structured events (T3, T5, T6), library + per-show position (T4, T5, T6), popup loading/errors/Esc/warm-up (T7), buddy privacy (T8), keys + hygiene + launcher (T2, T9), testing (T2–T10). Spec items deliberately not built: auto-update, signing, non-Chrome browsers.
- Names are consistent across tasks (`ShowSetupRunner`, `set_position`, `select_show`, `friendly_error`, `ApiDeps`).
