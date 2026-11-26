# Watch Buddy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Watch Buddy feature that monitors audio silence, takes a screenshot, queries character arc data, and delivers a GPT-generated friend-like reaction + question via a floating chat bubble and macOS TTS voice.

**Architecture:** A `BuddyListener` daemon thread monitors system audio RMS; when silence exceeds 1.5s and the cooldown has elapsed, it screenshots the screen, queries SQLite for the current episode's top-5 characters, calls `gpt-4o` with a persona prompt + screenshot, then posts the response to the main thread as a `BuddyBubble` overlay and speaks it via `say`. Persona (name, voice, personality, cooldown) is stored in `data/config.json` under a `"buddy"` key and editable via a new `BuddySettingsWindow` reachable from the menu bar.

**Tech Stack:** `sounddevice` (audio monitoring), OpenAI `gpt-4o` vision API, macOS `say` (TTS), PyObjC `NSWindow`/`NSPanel`, SQLite (existing `CharacterDB`), `screencapture` (existing `_capture()`), `pytest` (tests)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/buddy.py` | CREATE | `BuddyListener` thread, silence detection, prompt building, OpenAI call |
| `src/desktop_app.py` | MODIFY | Add `BuddyBubble` class, `BuddySettingsWindow` class, wire both into `AppDelegate` |
| `requirements.txt` | MODIFY | Add `sounddevice`, `pytest` |
| `tests/test_buddy.py` | CREATE | Unit tests for pure-Python logic in `buddy.py` |

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add sounddevice and pytest to requirements.txt**

Open `requirements.txt` and append two lines so it reads:
```
fastapi
uvicorn[standard]
openai
insightface
onnxruntime
faiss-cpu
numpy
opencv-python
mss
python-dotenv
icrawler
pynput<1.8
pyobjc-core<12
pyobjc-framework-Quartz<12
pyobjc-framework-ApplicationServices<12
pyobjc-framework-Cocoa<12
pyobjc-framework-CoreText<12
sounddevice
pytest
```

- [ ] **Step 2: Install new packages**

```bash
venv/bin/pip install sounddevice pytest
```

Expected: both install successfully. `sounddevice` may pull in `cffi` and `portaudio` bindings.

- [ ] **Step 3: Verify sounddevice imports**

```bash
venv/bin/python -c "import sounddevice; print(sounddevice.__version__)"
```

Expected: version string printed, no error.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add sounddevice and pytest dependencies"
```

---

## Task 2: Create `src/buddy.py` — pure logic (no PyObjC)

**Files:**
- Create: `src/buddy.py`
- Create: `tests/test_buddy.py`

This task implements and tests the parts of `buddy.py` that have no PyObjC dependency: RMS calculation, cooldown check, prompt building, and the character DB query. The `BuddyListener` thread and OpenAI call are added in Task 3.

- [ ] **Step 1: Create the tests directory and write failing tests**

```bash
mkdir -p tests
```

Create `tests/test_buddy.py`:

```python
import sys, json, sqlite3, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np


def test_rms_silent():
    from buddy import _rms
    silent = np.zeros(1024, dtype=np.float32)
    assert _rms(silent) < 0.001


def test_rms_loud():
    from buddy import _rms
    loud = np.ones(1024, dtype=np.float32)
    assert _rms(loud) > 0.5


def test_cooldown_allows_first_trigger():
    from buddy import _cooldown_elapsed
    assert _cooldown_elapsed(last_trigger=0.0, cooldown_seconds=180) is True


def test_cooldown_blocks_recent_trigger():
    from buddy import _cooldown_elapsed
    assert _cooldown_elapsed(last_trigger=time.time(), cooldown_seconds=180) is False


def test_cooldown_allows_after_elapsed():
    from buddy import _cooldown_elapsed
    assert _cooldown_elapsed(last_trigger=time.time() - 200, cooldown_seconds=180) is True


def test_build_prompt_includes_character():
    from buddy import _build_prompt
    chars = [{"character_name": "Walter White", "arc_type": "danger",
              "current_arc": "He is cooking meth"}]
    prompt = _build_prompt(
        buddy_name="Alex",
        personality="hype",
        show_title="Breaking Bad",
        season=3,
        episode=7,
        characters=chars,
    )
    assert "Walter White" in prompt
    assert "danger" in prompt.lower()
    assert "Season 3, Episode 7" in prompt
    assert "Alex" in prompt
    assert "spoil" in prompt.lower()


def test_build_prompt_personality_hype():
    from buddy import _build_prompt
    prompt = _build_prompt("Alex", "hype", "Breaking Bad", 1, 1, [])
    assert "enthusiastic" in prompt.lower() or "casual" in prompt.lower() or "slang" in prompt.lower()


def test_build_prompt_personality_film_nerd():
    from buddy import _build_prompt
    prompt = _build_prompt("Alex", "film_nerd", "Breaking Bad", 1, 1, [])
    assert "analytical" in prompt.lower() or "theme" in prompt.lower()


def test_build_prompt_personality_chill():
    from buddy import _build_prompt
    prompt = _build_prompt("Alex", "chill", "Breaking Bad", 1, 1, [])
    assert "laid-back" in prompt.lower() or "dry humor" in prompt.lower()


def test_query_episode_characters_spoiler_shield():
    from buddy import _query_episode_characters
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE shows (show_id TEXT PRIMARY KEY, show_name TEXT,
                    tmdb_id INTEGER, total_characters INTEGER, ingested_at TEXT, status TEXT)""")
    conn.execute("""CREATE TABLE characters (character_id TEXT PRIMARY KEY, show_id TEXT,
                    character_name TEXT, actor_name TEXT, role TEXT, first_episode INTEGER,
                    last_episode INTEGER, first_season INTEGER, last_season INTEGER,
                    importance_rank INTEGER, relationship_summary TEXT, image_count INTEGER,
                    confidence_level TEXT, status TEXT, current_arc TEXT,
                    last_seen_description TEXT, arc_type TEXT)""")
    conn.execute("""CREATE TABLE character_arcs (character_id TEXT, season INTEGER,
                    episode INTEGER, status TEXT, current_arc TEXT,
                    last_seen_description TEXT, arc_type TEXT,
                    PRIMARY KEY (character_id, season, episode))""")
    conn.execute("INSERT INTO shows VALUES ('bb','Breaking Bad',1396,2,NULL,'ready')")
    # character introduced S1E1 — should appear
    conn.execute("""INSERT INTO characters VALUES
        ('bb-walter','bb','Walter White','Bryan Cranston','main',1,62,1,5,1,
         'Chemistry teacher','0','high','alive','Cooking meth','Last seen',NULL)""")
    conn.execute("INSERT INTO character_arcs VALUES ('bb-walter',3,7,'alive','He cooks','Last lab',NULL)")
    # character introduced S3E8 — should be hidden (future)
    conn.execute("""INSERT INTO characters VALUES
        ('bb-future','bb','Future Guy','Actor','supporting',8,8,3,3,2,
         'Future guy','0','low','alive','Future arc','Future scene',NULL)""")
    conn.execute("INSERT INTO character_arcs VALUES ('bb-future',3,7,'alive','Future arc','Future',NULL)")
    conn.commit()
    conn.close()

    results = _query_episode_characters(db_path, "bb", season=3, episode=7)
    names = [r["character_name"] for r in results]
    assert "Walter White" in names
    assert "Future Guy" not in names
    Path(db_path).unlink(missing_ok=True)
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
venv/bin/pytest tests/test_buddy.py -v
```

Expected: `ModuleNotFoundError: No module named 'buddy'` — that's the right failure.

- [ ] **Step 3: Create `src/buddy.py` with the pure logic functions**

```python
from __future__ import annotations

import base64
import json
import math
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

_SILENCE_THRESHOLD = 0.01   # RMS below this = silence
_SILENCE_DURATION  = 1.5    # seconds of sustained silence to trigger

_PERSONALITY_DESCRIPTIONS = {
    "hype": (
        "enthusiastic and excitable, uses casual slang, reacts loudly to drama"
    ),
    "film_nerd": (
        "thoughtful and analytical, notices themes and character motivations, warm but smart"
    ),
    "chill": (
        "laid-back and funny, low-key reactions, dry humor"
    ),
}

_DEFAULT_BUDDY = {
    "name": "Alex",
    "personality": "hype",
    "voice": "Samantha",
    "cooldown_minutes": 3,
    "enabled": True,
}


def _rms(audio: np.ndarray) -> float:
    """Return RMS amplitude of a float32 audio chunk."""
    return float(math.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def _cooldown_elapsed(last_trigger: float, cooldown_seconds: float) -> bool:
    """Return True if enough time has passed since last_trigger."""
    return (time.time() - last_trigger) >= cooldown_seconds


def _build_prompt(
    buddy_name: str,
    personality: str,
    show_title: str,
    season: int,
    episode: int,
    characters: list[dict],
) -> str:
    desc = _PERSONALITY_DESCRIPTIONS.get(personality, _PERSONALITY_DESCRIPTIONS["hype"])
    char_lines = "\n".join(
        f"- {c['character_name']} ({c.get('arc_type') or 'unknown'}): {c.get('current_arc') or 'no arc data'}"
        for c in characters
    )
    return (
        f"You are {buddy_name}, a {desc} friend watching {show_title} with the user.\n"
        f"They are currently on Season {season}, Episode {episode}.\n"
        f"IMPORTANT: Do NOT reference or hint at anything that happens after this episode. No spoilers.\n\n"
        f"Here is what's happening with the main characters right now:\n"
        f"{char_lines if char_lines else '(no character data available)'}\n\n"
        f"[Screenshot of the current scene attached]\n\n"
        f"React to what you see on screen and ask ONE short question like a friend would. "
        f"Keep your response under 40 words. Be natural, not formal."
    )


def _query_episode_characters(
    db_path: str | Path,
    show_id: str,
    season: int,
    episode: int,
    limit: int = 5,
) -> list[dict]:
    """Return top-N characters visible up to the current episode, with arc data."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                c.character_name,
                COALESCE(ca.arc_type, c.arc_type)    AS arc_type,
                COALESCE(ca.current_arc, c.current_arc) AS current_arc
            FROM characters c
            LEFT JOIN character_arcs ca
                ON ca.character_id = c.character_id
                AND ca.season  = ?
                AND ca.episode = ?
            WHERE c.show_id = ?
              AND (
                c.first_season < ?
                OR (c.first_season = ? AND c.first_episode <= ?)
              )
            ORDER BY c.importance_rank ASC NULLS LAST
            LIMIT ?
            """,
            (season, episode, show_id, season, season, episode, limit),
        ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests — expect most to pass, verify**

```bash
venv/bin/pytest tests/test_buddy.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/buddy.py tests/test_buddy.py
git commit -m "feat: add buddy pure-logic functions with tests"
```

---

## Task 3: Add `BuddyListener` thread and OpenAI call to `src/buddy.py`

**Files:**
- Modify: `src/buddy.py`

- [ ] **Step 1: Append BuddyListener class and _generate_buddy_message to `src/buddy.py`**

Add this to the bottom of `src/buddy.py`:

```python
# Lazy imports to avoid pulling PyObjC/AppHelper at test time
def _post_to_main(callback, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(callback, *args)


def _capture_b64() -> str:
    """Screenshot → base64 JPEG string (reuses desktop_app logic)."""
    import cv2
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(["screencapture", "-x", "-t", "jpg", tmp],
                       check=True, timeout=5)
        img = cv2.imread(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if img is None:
        raise RuntimeError("screencapture returned no image")
    import numpy as np
    _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return base64.b64encode(jpeg.tobytes()).decode()


def _get_show_title(db_path: str | Path, show_id: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT show_name FROM shows WHERE show_id = ?", (show_id,)
        ).fetchone()
    return row[0] if row else show_id


def _generate_buddy_message(config_path: Path, db_path: Path) -> Optional[str]:
    """Take screenshot, query DB, call OpenAI, return response text or None."""
    try:
        cfg = json.loads(config_path.read_text())
    except Exception:
        return None

    buddy = cfg.get("buddy", _DEFAULT_BUDDY)
    if not buddy.get("enabled", True):
        return None

    show_id = cfg.get("show_id", "")
    season  = cfg.get("season", 1)
    episode = cfg.get("episode", 1)

    try:
        b64 = _capture_b64()
    except Exception as exc:
        print(f"[Buddy] screenshot failed: {exc}")
        return None

    characters = _query_episode_characters(db_path, show_id, season, episode)
    show_title = _get_show_title(db_path, show_id)

    prompt = _build_prompt(
        buddy_name=buddy.get("name", "Alex"),
        personality=buddy.get("personality", "hype"),
        show_title=show_title,
        season=season,
        episode=episode,
        characters=characters,
    )

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # Try reading from .env
        env_path = config_path.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        print("[Buddy] no OPENAI_API_KEY found")
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": "low",
                    }},
                ],
            }],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[Buddy] OpenAI call failed: {exc}")
        return None


class BuddyListener:
    """Background thread that monitors audio and fires the watch buddy."""

    def __init__(self, config_path: Path, db_path: Path, on_message):
        self._config_path  = config_path
        self._db_path      = db_path
        self._on_message   = on_message   # callable(text, voice)
        self._last_trigger = 0.0
        self._silence_buf  = 0.0          # seconds of consecutive silence
        self._lock         = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="BuddyListener")
        self._thread.start()

    def _run(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            print("[Buddy] sounddevice not installed — buddy disabled")
            return

        import numpy as np

        def _callback(indata: np.ndarray, frames: int, t, status):
            level = _rms(indata[:, 0])
            with self._lock:
                if level < _SILENCE_THRESHOLD:
                    self._silence_buf += frames / 44100.0
                else:
                    self._silence_buf = 0.0

                if self._silence_buf >= _SILENCE_DURATION:
                    cfg = {}
                    try:
                        cfg = json.loads(self._config_path.read_text())
                    except Exception:
                        pass
                    buddy = cfg.get("buddy", _DEFAULT_BUDDY)
                    if not buddy.get("enabled", True):
                        return
                    cooldown = buddy.get("cooldown_minutes", 3) * 60
                    if not _cooldown_elapsed(self._last_trigger, cooldown):
                        return
                    self._last_trigger = time.time()
                    self._silence_buf  = 0.0
                    threading.Thread(target=self._trigger, daemon=True).start()

        try:
            with sd.InputStream(
                samplerate=44100,
                channels=1,
                dtype="float32",
                blocksize=2048,
                callback=_callback,
            ):
                while True:
                    time.sleep(1)
        except Exception as exc:
            print(f"[Buddy] audio stream error: {exc}")

    def _trigger(self) -> None:
        cfg = {}
        try:
            cfg = json.loads(self._config_path.read_text())
        except Exception:
            pass
        buddy = cfg.get("buddy", _DEFAULT_BUDDY)
        voice = buddy.get("voice", "Samantha")

        text = _generate_buddy_message(self._config_path, self._db_path)
        if text:
            _post_to_main(self._on_message, text, voice)
```

- [ ] **Step 2: Verify syntax**

```bash
venv/bin/python -c "import ast; ast.parse(open('src/buddy.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run existing tests to confirm they still pass**

```bash
venv/bin/pytest tests/test_buddy.py -v
```

Expected: all 10 tests pass (new code is in separate class, doesn't break pure functions).

- [ ] **Step 4: Commit**

```bash
git add src/buddy.py
git commit -m "feat: add BuddyListener thread and OpenAI vision call"
```

---

## Task 4: Add `BuddyBubble` class to `desktop_app.py`

**Files:**
- Modify: `src/desktop_app.py` (add class after line 1050, before `class SetupWindow`)

- [ ] **Step 1: Insert BuddyBubble class into desktop_app.py**

Open `src/desktop_app.py`. Find the line `class SetupWindow(NSObject):` (currently line 1051). Insert the following block immediately **before** that line:

```python
# ── watch buddy bubble ────────────────────────────────────────────────────────

class BuddyBubble:
    """Floating iMessage-style chat bubble shown when the buddy speaks."""

    _WIN_W = 360
    _MARGIN = 20
    _AUTO_DISMISS = 8.0   # seconds

    def show(self, text: str, buddy_name: str = "Alex") -> None:
        """Create and display the bubble. Call from main thread only."""
        self._dismiss_existing()

        font_body  = NSFont.systemFontOfSize_(14)
        font_name  = NSFont.boldSystemFontOfSize_(12)
        pad        = 16
        inner_w    = self._WIN_W - pad * 2

        # Estimate text height
        chars_per_line = max(1, int(inner_w / (14 * 0.58)))
        words = text.split()
        lines, cur = [], ""
        for w in words:
            cand = (cur + " " + w).strip()
            if len(cand) <= chars_per_line:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        text_h = max(1, len(lines)) * int(14 * 1.5) + 4

        win_h = pad + 20 + 6 + text_h + pad   # name row + gap + body + bottom pad

        sf   = NSScreen.mainScreen().frame()
        x    = sf.size.width - self._WIN_W - self._MARGIN
        y    = self._MARGIN

        self._win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, self._WIN_W, win_h),
            0,   # NSWindowStyleMaskBorderless
            NSBackingStoreBuffered,
            False,
        )
        self._win.setLevel_(3)   # NSFloatingWindowLevel
        self._win.setOpaque_(False)
        self._win.setBackgroundColor_(NSColor.clearColor())
        self._win.setIgnoresMouseEvents_(False)

        cv = self._win.contentView()

        # Background rounded rect
        bg = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self._WIN_W, win_h))
        bg.setWantsLayer_(True)
        bg.layer().setBackgroundColor_(_rgba(0.12, 0.12, 0.14, 0.96).CGColor)
        bg.layer().setCornerRadius_(16)
        cv.addSubview_(bg)

        # Buddy name label
        name_lbl = NSTextField.alloc().initWithFrame_(
            NSMakeRect(pad, win_h - pad - 20, inner_w, 18))
        name_lbl.setStringValue_(f"💬 {buddy_name}")
        name_lbl.setFont_(font_name)
        name_lbl.setTextColor_(_rgba(0.55, 0.85, 1.0))
        name_lbl.setEditable_(False)
        name_lbl.setBordered_(False)
        name_lbl.setBackgroundColor_(NSColor.clearColor())
        cv.addSubview_(name_lbl)

        # Message text
        msg_lbl = NSTextField.alloc().initWithFrame_(
            NSMakeRect(pad, pad, inner_w, text_h))
        msg_lbl.setStringValue_(text)
        msg_lbl.setFont_(font_body)
        msg_lbl.setTextColor_(NSColor.whiteColor())
        msg_lbl.setEditable_(False)
        msg_lbl.setBordered_(False)
        msg_lbl.setBackgroundColor_(NSColor.clearColor())
        msg_lbl.setLineBreakMode_(0)   # NSLineBreakByWordWrapping
        msg_lbl.setMaximumNumberOfLines_(0)
        cv.addSubview_(msg_lbl)

        self._win.makeKeyAndOrderFront_(None)

        # Auto-dismiss
        self._dismiss_timer = threading.Timer(self._AUTO_DISMISS, lambda: AppHelper.callAfter(self._dismiss_existing))
        self._dismiss_timer.daemon = True
        self._dismiss_timer.start()

    def _dismiss_existing(self) -> None:
        if hasattr(self, "_dismiss_timer"):
            self._dismiss_timer.cancel()
            del self._dismiss_timer
        if hasattr(self, "_win"):
            try:
                self._win.orderOut_(None)
            except Exception:
                pass
            del self._win
```

- [ ] **Step 2: Verify syntax**

```bash
venv/bin/python -c "import ast; ast.parse(open('src/desktop_app.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/desktop_app.py
git commit -m "feat: add BuddyBubble overlay class"
```

---

## Task 5: Add `BuddySettingsWindow` to `desktop_app.py`

**Files:**
- Modify: `src/desktop_app.py` (add class after BuddyBubble, before SetupWindow)

- [ ] **Step 1: Insert BuddySettingsWindow class**

In `src/desktop_app.py`, find the line `class SetupWindow(NSObject):`. Insert the following block immediately before it (after the BuddyBubble class you added in Task 4):

```python
# ── buddy settings window ─────────────────────────────────────────────────────

class BuddySettingsWindow:
    """NSPanel for configuring the Watch Buddy persona."""

    _PERSONALITIES = ["Hype Friend", "Film Nerd", "Chill Vibes"]
    _PERSONALITY_KEYS = ["hype", "film_nerd", "chill"]
    _COOLDOWNS = ["2 minutes", "3 minutes", "5 minutes"]
    _COOLDOWN_VALUES = [2, 3, 5]

    def __init__(self):
        self._win = None

    def show(self) -> None:
        if self._win is not None:
            self._win.makeKeyAndOrderFront_(None)
            return
        self._build()
        self._win.makeKeyAndOrderFront_(None)

    def _build(self) -> None:
        W, H = 340, 300
        sf = NSScreen.mainScreen().visibleFrame()
        x  = (sf.size.width  - W) / 2
        y  = (sf.size.height - H) / 2

        self._win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, W, H),
            15,   # titled | closable | miniaturizable | resizable
            NSBackingStoreBuffered,
            False,
        )
        self._win.setTitle_("Watch Buddy Settings")
        self._win.setLevel_(3)

        cfg   = _load_config()
        buddy = cfg.get("buddy", {
            "name": "Alex", "personality": "hype",
            "voice": "Samantha", "cooldown_minutes": 3, "enabled": True,
        })

        cv  = self._win.contentView()
        pad = 20
        row = H - 50

        def _label(text, y):
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(pad, y, 120, 20))
            lbl.setStringValue_(text)
            lbl.setEditable_(False)
            lbl.setBordered_(False)
            lbl.setBackgroundColor_(NSColor.clearColor())
            cv.addSubview_(lbl)

        # Enabled checkbox
        self._enabled_btn = NSButton.alloc().initWithFrame_(NSMakeRect(pad, row, W - pad*2, 24))
        self._enabled_btn.setButtonType_(3)   # NSSwitchButton
        self._enabled_btn.setTitle_("Enable Watch Buddy")
        self._enabled_btn.setState_(1 if buddy.get("enabled", True) else 0)
        cv.addSubview_(self._enabled_btn)
        row -= 44

        # Buddy name
        _label("Buddy Name:", row)
        self._name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 24))
        self._name_field.setStringValue_(buddy.get("name", "Alex"))
        cv.addSubview_(self._name_field)
        row -= 40

        # Personality
        _label("Personality:", row)
        self._personality_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        for p in self._PERSONALITIES:
            self._personality_btn.addItemWithTitle_(p)
        cur_p = buddy.get("personality", "hype")
        if cur_p in self._PERSONALITY_KEYS:
            self._personality_btn.selectItemAtIndex_(self._PERSONALITY_KEYS.index(cur_p))
        cv.addSubview_(self._personality_btn)
        row -= 40

        # Voice
        _label("Voice:", row)
        self._voice_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        voices = self._available_voices()
        for v in voices:
            self._voice_btn.addItemWithTitle_(v)
        cur_v = buddy.get("voice", "Samantha")
        if cur_v in voices:
            self._voice_btn.selectItemAtIndex_(voices.index(cur_v))
        cv.addSubview_(self._voice_btn)
        self._voices = voices
        row -= 40

        # Cooldown
        _label("Cooldown:", row)
        self._cooldown_btn = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(140, row, W - 140 - pad, 26))
        for c in self._COOLDOWNS:
            self._cooldown_btn.addItemWithTitle_(c)
        cur_c = buddy.get("cooldown_minutes", 3)
        if cur_c in self._COOLDOWN_VALUES:
            self._cooldown_btn.selectItemAtIndex_(self._COOLDOWN_VALUES.index(cur_c))
        cv.addSubview_(self._cooldown_btn)
        row -= 50

        # Save button
        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 100 - pad, row, 100, 30))
        save_btn.setTitle_("Save")
        save_btn.setTarget_(self)
        save_btn.setAction_("onSave:")
        cv.addSubview_(save_btn)

    def _available_voices(self) -> list:
        import subprocess
        try:
            out = subprocess.check_output(["say", "-v", "?"],
                                          text=True, timeout=3)
            voices = []
            for line in out.splitlines():
                name = line.split()[0] if line.strip() else ""
                if name:
                    voices.append(name)
            return voices or ["Samantha"]
        except Exception:
            return ["Samantha", "Tom", "Karen"]

    def onSave_(self, _sender):
        cfg   = _load_config()
        p_idx = self._personality_btn.indexOfSelectedItem()
        c_idx = self._cooldown_btn.indexOfSelectedItem()
        v_idx = self._voice_btn.indexOfSelectedItem()
        cfg["buddy"] = {
            "name":             self._name_field.stringValue(),
            "personality":      self._PERSONALITY_KEYS[p_idx],
            "voice":            self._voices[v_idx],
            "cooldown_minutes": self._COOLDOWN_VALUES[c_idx],
            "enabled":          bool(self._enabled_btn.state()),
        }
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        self._win.orderOut_(None)
        self._win = None
```

- [ ] **Step 2: Verify syntax**

```bash
venv/bin/python -c "import ast; ast.parse(open('src/desktop_app.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/desktop_app.py
git commit -m "feat: add BuddySettingsWindow panel"
```

---

## Task 6: Wire BuddyListener and BuddyBubble into AppDelegate

**Files:**
- Modify: `src/desktop_app.py`

The `AppDelegate` class is at the bottom of `desktop_app.py`. You need to:
1. Add "Watch Buddy Settings…" to the menu
2. Instantiate `BuddyBubble` and `BuddySettingsWindow` in `applicationDidFinishLaunching_`
3. Start `BuddyListener` in `applicationDidFinishLaunching_`
4. Add `_on_buddy_message` callback
5. Add `onBuddySettings_` action

- [ ] **Step 1: Add menu item for Watch Buddy Settings in `_populate_menu`**

In `_populate_menu`, find the line that adds the separator before Quit:
```python
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit CineID", "terminate:", "")
```

Insert the buddy settings item **before** that separator:

```python
        buddy_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Watch Buddy Settings…", "onBuddySettings:", "")
        buddy_item.setTarget_(self)
        menu.addItem_(buddy_item)
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit CineID", "terminate:", "")
```

- [ ] **Step 2: Add instances and start BuddyListener in `applicationDidFinishLaunching_`**

Find these lines in `applicationDidFinishLaunching_`:
```python
        self._overlay   = Overlay()
        self._busy      = threading.Lock()
        self._setup_win = None
```

Replace with:
```python
        self._overlay       = Overlay()
        self._busy          = threading.Lock()
        self._setup_win     = None
        self._buddy_bubble  = BuddyBubble()
        self._buddy_settings_win = BuddySettingsWindow()

        # Start watch buddy audio listener
        import sys as _sys
        _sys.path.insert(0, str(_ROOT / "src"))
        from buddy import BuddyListener
        self._buddy_listener = BuddyListener(
            config_path=_CONFIG_PATH,
            db_path=_ROOT / "data" / "cineid.db",
            on_message=self._on_buddy_message,
        )
        self._buddy_listener.start()
```

- [ ] **Step 3: Add `_on_buddy_message` and `onBuddySettings_` methods to AppDelegate**

Find the `_on_hotkey` method (currently around line 1626). Add the two new methods immediately before it:

```python
    def _on_buddy_message(self, text: str, voice: str) -> None:
        """Called on the main thread when the buddy has a message ready."""
        cfg        = _load_config()
        buddy      = cfg.get("buddy", {})
        buddy_name = buddy.get("name", "Alex")
        self._buddy_bubble.show(text, buddy_name)
        subprocess.Popen(["say", "-v", voice, text])

    def onBuddySettings_(self, _sender):
        self._buddy_settings_win.show()

```

Note: `subprocess` is already imported at the top of `desktop_app.py` (used in `_show_netflix_controls`). Verify with:

```bash
grep -n "^import subprocess\|^from subprocess\| subprocess" src/desktop_app.py | head -5
```

If `subprocess` is only imported inside functions, add `import subprocess` to the top-level imports block at the top of the file.

- [ ] **Step 4: Verify syntax**

```bash
venv/bin/python -c "import ast; ast.parse(open('src/desktop_app.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run tests**

```bash
venv/bin/pytest tests/test_buddy.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/desktop_app.py
git commit -m "feat: wire BuddyListener and BuddyBubble into AppDelegate"
```

---

## Task 7: Add default buddy config and BlackHole setup note

**Files:**
- Modify: `src/buddy.py` (add a helper used at first run)
- Modify: `src/desktop_app.py` (call helper on launch)

- [ ] **Step 1: Add `_ensure_buddy_defaults` to `src/buddy.py`**

Add this function to `src/buddy.py` after the `_DEFAULT_BUDDY` constant:

```python
def ensure_buddy_defaults(config_path: Path) -> None:
    """Write default buddy config into config.json if the key is missing."""
    try:
        cfg = json.loads(config_path.read_text())
    except Exception:
        cfg = {}
    if "buddy" not in cfg:
        cfg["buddy"] = dict(_DEFAULT_BUDDY)
        config_path.write_text(json.dumps(cfg, indent=2))
```

- [ ] **Step 2: Add a test for ensure_buddy_defaults**

Add to `tests/test_buddy.py`:

```python
def test_ensure_buddy_defaults_writes_defaults():
    from buddy import ensure_buddy_defaults
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"show_id": "bb", "season": 1, "episode": 1}, f)
        cfg_path = Path(f.name)
    try:
        ensure_buddy_defaults(cfg_path)
        result = json.loads(cfg_path.read_text())
        assert "buddy" in result
        assert result["buddy"]["name"] == "Alex"
        assert result["buddy"]["enabled"] is True
    finally:
        cfg_path.unlink(missing_ok=True)


def test_ensure_buddy_defaults_does_not_overwrite():
    from buddy import ensure_buddy_defaults
    existing = {"show_id": "bb", "season": 1, "episode": 1,
                "buddy": {"name": "Sam", "enabled": False}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(existing, f)
        cfg_path = Path(f.name)
    try:
        ensure_buddy_defaults(cfg_path)
        result = json.loads(cfg_path.read_text())
        assert result["buddy"]["name"] == "Sam"   # unchanged
    finally:
        cfg_path.unlink(missing_ok=True)
```

- [ ] **Step 3: Run tests**

```bash
venv/bin/pytest tests/test_buddy.py -v
```

Expected: 12 tests pass.

- [ ] **Step 4: Call `ensure_buddy_defaults` on app launch**

In `applicationDidFinishLaunching_`, after the line `self._buddy_listener.start()`, add:

```python
        from buddy import ensure_buddy_defaults
        ensure_buddy_defaults(_CONFIG_PATH)
```

- [ ] **Step 5: Verify syntax**

```bash
venv/bin/python -c "import ast; ast.parse(open('src/desktop_app.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/buddy.py tests/test_buddy.py src/desktop_app.py
git commit -m "feat: ensure buddy defaults written to config on first launch"
```

---

## Task 8: Manual smoke test

- [ ] **Step 1: Start the server**

```bash
venv/bin/python src/server.py
```

Leave running in a separate terminal.

- [ ] **Step 2: Start the desktop app**

```bash
venv/bin/python src/desktop_app.py
```

- [ ] **Step 3: Verify menu bar has "Watch Buddy Settings…"**

Click the CID icon in the menu bar. Confirm "Watch Buddy Settings…" appears.

- [ ] **Step 4: Open buddy settings and save**

Click "Watch Buddy Settings…". Panel should open with name/personality/voice/cooldown fields. Change the name to "Jordan", click Save. Verify `data/config.json` now contains `"buddy": {"name": "Jordan", ...}`.

```bash
python3 -c "import json; c=json.load(open('data/config.json')); print(c.get('buddy'))"
```

Expected: dict with `"name": "Jordan"`.

- [ ] **Step 5: Trigger buddy manually for a quick test**

In a Python REPL (not the running app), call the generation function directly:

```bash
venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, 'src')
from pathlib import Path
from buddy import _generate_buddy_message
msg = _generate_buddy_message(Path("data/config.json"), Path("data/cineid.db"))
print("Response:", msg)
EOF
```

Expected: a short reaction + question string (≤40 words). If OPENAI_API_KEY is set in `.env` it will call the API; otherwise it prints a warning.

- [ ] **Step 6: Test TTS**

```bash
say -v Samantha "Yo, Walter just looked super suspicious. Do you think he's about to cook again?"
```

Expected: macOS speaks the sentence.

- [ ] **Step 7: Note BlackHole requirement for user**

The buddy listens to the **default input device**. To monitor TV show audio rather than the mic, the user must install [BlackHole](https://existential.audio/blackhole/) and set it as the system audio output. Document this in `ONBOARDING.md` under a new "Watch Buddy Audio Setup" section:

Open `ONBOARDING.md` and add:

```markdown
## Watch Buddy Audio Setup

The Watch Buddy feature monitors your system audio for silence between scenes.
By default it reads from your microphone. To make it respond to the show audio instead:

1. Install **BlackHole 2ch** (free): https://existential.audio/blackhole/
2. In **System Settings → Sound → Output**, select BlackHole 2ch
3. Create a **Multi-Output Device** in Audio MIDI Setup that includes both BlackHole and your speakers/headphones so you still hear audio
4. Set that Multi-Output Device as your system output

Once configured, CineID will automatically detect silence in the show audio.
```

- [ ] **Step 8: Commit**

```bash
git add ONBOARDING.md
git commit -m "docs: add Watch Buddy audio setup instructions (BlackHole)"
```
