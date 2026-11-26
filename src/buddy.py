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

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
import config_store

_SILENCE_THRESHOLD = 0.01
_SILENCE_DURATION  = 1.5

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
    "mac": (
        "sharp, witty, and concise like Apple — uses crisp Mac/tech vocabulary, "
        "says things like 'Pausing here—' or 'Quick thought—', never rambles"
    ),
}

_MIN_RETRIGGER_GAP = 10   # seconds — prevents same pause firing twice while API call runs
_DEFAULT_TIMER_COOLDOWN_MIN = 3  # timer mode floor — cooldown 0 must not mean a Claude call per tick
_MAX_AUDIO_FAILURES = 3   # consecutive stream failures before falling back to timer mode

_DEFAULT_BUDDY = {
    "name": "Alex",
    "personality": "mac",
    "voice": "Samantha",
    "cooldown_minutes": 0,
    "enabled": True,
    "trigger_mode": "audio",   # "audio" = silence detection, "timer" = fixed interval (no mic)
    "share_only_window": True, # screenshot just the browser window, not the whole desktop
    "disclosure_ack": False,   # user has read that screenshots go to Anthropic (Claude)
}


def ensure_buddy_defaults(config_path: Path) -> None:
    """Write default buddy config into config.json if the key is missing.

    Never overwrites a config.json it cannot parse (that used to replace the
    user's show/season/episode with just the buddy block)."""
    def _add(cfg: dict) -> None:
        cfg.setdefault("buddy", dict(_DEFAULT_BUDDY))
    try:
        config_store.update_config(config_path, _add)
    except config_store.ConfigUnreadable as exc:
        print(f"[Buddy] config.json unreadable, leaving it untouched: {exc}")


def _resolve_trigger_mode(cfg: dict) -> str:
    """'audio' or 'timer'. Defaults to the documented default ('audio') when
    the buddy block or key is missing, and for unknown values."""
    mode = (cfg.get("buddy") or {}).get("trigger_mode", _DEFAULT_BUDDY["trigger_mode"])
    return mode if mode in ("audio", "timer") else _DEFAULT_BUDDY["trigger_mode"]


def _safe_say_text(text: str) -> str:
    """Text for macOS `say`: strip, and never start with '-' (it would be
    parsed as an option)."""
    t = text.strip()
    return t.lstrip("-").lstrip() or t


# How each persona opens. The old prompt forced the 'mac' catchphrases on
# every persona.
_PERSONALITY_OPENERS = {
    "hype": "Open with an excited reaction to what's on screen, then your question.",
    "film_nerd": "Open with an observation about a theme or motivation, then your question.",
    "chill": "Open with a dry, low-key remark, then your question.",
    "mac": "Start with 'Pausing here—' or 'Quick thought—', then your question.",
}


def _rms(audio: np.ndarray) -> float:
    return float(math.sqrt(np.mean(audio.astype(np.float32) ** 2)))


def _cooldown_elapsed(last_trigger: float, cooldown_seconds: float) -> bool:
    return (time.time() - last_trigger) >= cooldown_seconds


def _effective_cooldown(cooldown_minutes: float, mode: str) -> float:
    """Seconds between triggers. cooldown_minutes <= 0 means "as often as
    allowed": audio mode floors at _MIN_RETRIGGER_GAP, timer mode at
    _DEFAULT_TIMER_COOLDOWN_MIN minutes (each tick costs one small Claude call)."""
    if cooldown_minutes > 0:
        return cooldown_minutes * 60
    if mode == "audio":
        return _MIN_RETRIGGER_GAP
    return _DEFAULT_TIMER_COOLDOWN_MIN * 60


def _build_prompt(
    buddy_name: str,
    personality: str,
    show_title: str,
    season: int,
    episode: int,
    characters: list[dict],
) -> str:
    desc = _PERSONALITY_DESCRIPTIONS.get(personality, _PERSONALITY_DESCRIPTIONS["mac"])
    opener = _PERSONALITY_OPENERS.get(personality, _PERSONALITY_OPENERS["mac"])
    char_lines = "\n".join(
        f"- {c['character_name']} ({c.get('arc_type') or 'unknown'}): {c.get('current_arc') or 'no arc data'}"
        for c in characters
    )
    return (
        f"You are {buddy_name}, a {desc} friend watching {show_title} with the user.\n"
        f"They are currently on Season {season}, Episode {episode}.\n"
        f"IMPORTANT: Do NOT reference or hint at anything that happens after this episode. No spoilers.\n\n"
        f"What's happening in the story right now:\n"
        f"{char_lines if char_lines else '(no character data available)'}\n\n"
        f"[Screenshot of the current scene attached]\n\n"
        f"Use the screenshot to identify who is actually on screen. "
        f"Then use their story context above to ask a question that's specific to their current situation — "
        f"not a generic 'what do you think will happen' but something tied to the exact arc listed. "
        f"{opener} "
        f"Under 35 words. Sound like a friend who's been watching closely, not a recap bot."
    )


def _query_episode_characters(
    db_path: str | Path,
    show_id: str,
    season: int,
    episode: int,
    limit: int = 5,
) -> list[dict]:
    # Arc data comes only from per-episode rows at or before the current
    # episode. The character-level current_arc/arc_type columns describe the
    # character's latest known state in the whole show — using them as a
    # fallback leaks spoilers into the prompt.
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                c.character_name,
                (SELECT ca.arc_type FROM character_arcs ca
                  WHERE ca.character_id = c.character_id
                    AND (ca.season < :season
                         OR (ca.season = :season AND ca.episode < :episode))
                  ORDER BY ca.season DESC, ca.episode DESC
                  LIMIT 1) AS arc_type,
                (SELECT ca.current_arc FROM character_arcs ca
                  WHERE ca.character_id = c.character_id
                    AND (ca.season < :season
                         OR (ca.season = :season AND ca.episode < :episode))
                  ORDER BY ca.season DESC, ca.episode DESC
                  LIMIT 1) AS current_arc
            FROM characters c
            WHERE c.show_id = :show_id
              AND (
                c.first_season < :season
                OR (c.first_season = :season AND c.first_episode <= :episode)
              )
            ORDER BY c.importance_rank ASC NULLS LAST
            LIMIT :limit
            """,
            {"season": season, "episode": episode,
             "show_id": show_id, "limit": limit},
        ).fetchall()
    return [dict(r) for r in rows]


def _post_to_main(callback, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(callback, *args)


def _pick_window_id(windows: list, owner: str = "Google Chrome") -> Optional[int]:
    """CGWindowNumber of the largest normal (layer 0) window owned by `owner`."""
    best, best_area = None, 0
    for w in windows:
        if w.get("kCGWindowOwnerName") != owner or w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        area = b.get("Width", 0) * b.get("Height", 0)
        if area > best_area:
            best, best_area = w.get("kCGWindowNumber"), area
    return best


def _chrome_window_id() -> Optional[int]:
    try:
        import Quartz
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        return _pick_window_id(list(wins or []))
    except Exception:
        return None


def _capture_b64(window_only: bool = False) -> str:
    """Screenshot as base64 JPEG. window_only=True grabs just the browser
    window (falls back to the whole screen if it can't be found)."""
    import cv2
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp = f.name
    try:
        cmd = ["screencapture", "-x", "-t", "jpg"]
        win_id = _chrome_window_id() if window_only else None
        if win_id:
            cmd += [f"-l{win_id}"]
        subprocess.run(cmd + [tmp], check=True, timeout=5)
        img = cv2.imread(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if img is None:
        raise RuntimeError("screencapture returned no image")
    _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return base64.b64encode(jpeg.tobytes()).decode()


def _get_show_title(db_path: str | Path, show_id: str) -> str:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT show_name FROM shows WHERE show_id = ?", (show_id,)
        ).fetchone()
    return row[0] if row else show_id


def _generate_buddy_message(config_path: Path, db_path: Path) -> Optional[str]:
    try:
        cfg = json.loads(config_path.read_text())
    except Exception:
        return None

    buddy = cfg.get("buddy", _DEFAULT_BUDDY)
    if not buddy.get("enabled", True):
        return None

    if not buddy.get("disclosure_ack", False):
        print("[Buddy] paused — tick the privacy box in Goldfish Brain → Settings → Watch Buddy "
              "(each reaction uploads a screenshot to Anthropic)")
        return None

    show_id = cfg.get("show_id", "")
    season  = cfg.get("season", 1)
    episode = cfg.get("episode", 1)

    try:
        b64 = _capture_b64(window_only=bool(buddy.get("share_only_window", True)))
    except Exception as exc:
        print(f"[Buddy] screenshot failed: {exc}")
        return None

    characters = _query_episode_characters(db_path, show_id, season, episode)
    show_title = _get_show_title(db_path, show_id)

    prompt = _build_prompt(
        buddy_name=buddy.get("name", "Alex"),
        personality=buddy.get("personality", _DEFAULT_BUDDY["personality"]),
        show_title=show_title,
        season=season,
        episode=episode,
        characters=characters,
    )

    api_key = (os.environ.get("ANTHROPIC_API_KEY", "")
               or config_store.read_env_key(config_path.parent.parent / ".env",
                                            "ANTHROPIC_API_KEY"))
    if not api_key:
        print("[Buddy] no ANTHROPIC_API_KEY found — add it in Goldfish Brain → Settings")
        return None

    try:
        import llm
        client = llm.make_client(api_key)
        return llm.ask(client, prompt, max_tokens=200, image_b64=b64)
    except Exception as exc:
        print(f"[Buddy] Claude call failed: {exc}")
        return None


class BuddyListener:
    def __init__(self, config_path: Path, db_path: Path, on_message):
        self._config_path  = config_path
        self._db_path      = db_path
        self._on_message   = on_message
        self._last_trigger = 0.0
        self._silence_buf  = 0.0
        self._silence_pending = False
        self._armed        = True   # one trigger per silence period; re-arms on sound
        self._lock         = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="BuddyListener")
        self._thread.start()

    def _run(self) -> None:
        mode = _resolve_trigger_mode(config_store.read_config(self._config_path))

        if mode == "audio":
            self._run_audio()
        else:
            self._run_timer()

    def _run_timer(self) -> None:
        print("[Buddy] timer mode — no microphone used")
        while True:
            time.sleep(30)   # check every 30 seconds
            try:
                cfg = json.loads(self._config_path.read_text())
            except Exception:
                continue
            buddy = cfg.get("buddy", _DEFAULT_BUDDY)
            if not buddy.get("enabled", True):
                continue
            cooldown = _effective_cooldown(
                buddy.get("cooldown_minutes", _DEFAULT_BUDDY["cooldown_minutes"]), "timer"
            )
            if _cooldown_elapsed(self._last_trigger, cooldown):
                self._last_trigger = time.time()
                threading.Thread(target=self._trigger, daemon=True).start()

    def _run_audio(self) -> None:
        try:
            import sounddevice as sd
        except ImportError:
            print("[Buddy] sounddevice not installed — falling back to timer mode")
            self._run_timer()
            return

        def _callback(indata, frames: int, t, status):
            # Runs on the audio driver thread — no file I/O or config reads
            # here; just flag the silence and let the outer loop do slow work.
            self._process_block(_rms(indata[:, 0]), frames)

        failures = 0
        backoff  = 5
        while True:
            try:
                with sd.InputStream(
                    samplerate=44100,
                    channels=1,
                    dtype="float32",
                    blocksize=2048,
                    callback=_callback,
                ) as stream:
                    failures = 0
                    backoff  = 5
                    while stream.active:
                        time.sleep(1)
                        self._maybe_trigger_from_pending()
                    raise RuntimeError("input stream went inactive")
            except Exception as exc:
                failures += 1
                print(f"[Buddy] audio stream error "
                      f"({failures}/{_MAX_AUDIO_FAILURES}): {exc}")
                if failures >= _MAX_AUDIO_FAILURES:
                    print("[Buddy] audio unavailable — falling back to timer mode")
                    self._run_timer()
                    return
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _process_block(self, level: float, frames: int,
                       samplerate: float = 44100.0) -> None:
        """Track silence from one audio block. Arms exactly one pending
        trigger per silence period — sound must resume before the next one,
        so an idle mic can't fire repeatedly."""
        with self._lock:
            if level < _SILENCE_THRESHOLD:
                self._silence_buf += frames / samplerate
                if self._silence_buf >= _SILENCE_DURATION and self._armed:
                    self._armed = False
                    self._silence_pending = True
            else:
                self._silence_buf = 0.0
                self._armed = True

    def _maybe_trigger_from_pending(self) -> None:
        """Consume a pending silence flag and trigger if config allows."""
        with self._lock:
            pending = self._silence_pending
            self._silence_pending = False
        if not pending:
            return
        try:
            cfg = json.loads(self._config_path.read_text())
        except Exception:
            return
        buddy = cfg.get("buddy", _DEFAULT_BUDDY)
        if not buddy.get("enabled", True):
            return
        gap = _effective_cooldown(
            buddy.get("cooldown_minutes", _DEFAULT_BUDDY["cooldown_minutes"]),
            "audio",
        )
        if not _cooldown_elapsed(self._last_trigger, gap):
            return
        self._last_trigger = time.time()
        threading.Thread(target=self._trigger, daemon=True).start()

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
