"""JSON API behind the local web UI (/api/*).

Everything the UI needs — first-run state, keys, show search, the "add a show"
job, per-show position, Watch Buddy settings — lives here so it can be tested
without the face-recognition engine. Errors are always
{"ok": false, "message": "<plain English>"}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
import buddy as _buddy
import actor_credits
import ask as _ask
import config_store
import accuracy as _accuracy
import feedback as _feedback
import llm as _llm
import relationships as _relationships
import spoiler as _spoiler
from episode_lookup import find_season
from ingest import _show_id
from show_jobs import JobBusy

_POSTER = "https://image.tmdb.org/t/p/w342"
_TMDB_BASE = os.environ.get("CINEID_TMDB_BASE", "https://api.themoviedb.org/3")


@dataclass
class ApiDeps:
    config_path: Path
    env_path: Path
    get_db: Callable
    keystore: object
    key_check: object
    runner: object
    tmdb_get: Callable[[str, dict], dict]
    permissions: Callable[[], dict]
    open_target: Optional[Callable[[str], None]] = None   # defaults to macOS `open`
    data_dir: Optional[Path] = None                        # for character photos
    make_llm: Optional[Callable[[str], object]] = None     # api_key -> Claude client (tests inject)
    episode_guide: Optional[Callable[[int, str], list]] = None   # (tmdb_id, key) -> episodes


# ── default real-world helpers ──────────────────────────────────────────────

def default_tmdb_get(path: str, params: dict) -> dict:
    url = f"{_TMDB_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "GoldfishBrain/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def cached(fn: Callable[[], dict], ttl: float = 10.0, clock=None) -> Callable[[], dict]:
    """Remember fn()'s result for `ttl` seconds."""
    import time as _time
    clock = clock or _time.monotonic
    box: dict = {}

    def wrapper() -> dict:
        now = clock()
        if "at" not in box or now - box["at"] >= ttl:
            box["value"], box["at"] = fn(), now
        return box["value"]
    return wrapper


def default_permissions() -> dict:
    """Screen Recording is granted iff `screencapture` can actually grab the
    screen (without permission it fails). None = couldn't tell."""
    try:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "probe.jpg"
            r = subprocess.run(["screencapture", "-x", "-t", "jpg", str(out)],
                               capture_output=True, timeout=5)
            return {"screen_recording": r.returncode == 0 and out.exists()
                    and out.stat().st_size > 0}
    except Exception:
        return {"screen_recording": None}


_OPEN_TARGETS = {
    "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "tmdb_key_page": "https://www.themoviedb.org/settings/api",
    "anthropic_key_page": "https://console.anthropic.com/settings/keys",
}


def default_open(url: str) -> None:
    subprocess.run(["open", url], capture_output=True, timeout=5)


# ── request bodies ──────────────────────────────────────────────────────────

class KeyBody(BaseModel):
    name: str
    value: str


class SetupBody(BaseModel):
    tmdb_id: int
    name: str
    poster_path: Optional[str] = None
    with_notes: bool = True
    update: bool = False


class OpenBody(BaseModel):
    target: str


class SelectBody(BaseModel):
    show_id: str


class PositionBody(BaseModel):
    show_id: str
    season: int
    episode: int


class AskBody(BaseModel):
    question: str = ""
    image_b64: Optional[str] = None
    show_id: str
    season: int
    episode: int
    progress: Optional[float] = None
    mode: str = "ask"          # "ask" | "react" (the Watch Buddy's on-demand reaction)


class FeedbackBody(BaseModel):
    show_id: str
    kind: str
    season: int = 1
    episode: int = 1
    character_id: Optional[str] = None
    note: Optional[str] = None


class BuddyBody(BaseModel):
    name: Optional[str] = None
    personality: Optional[str] = None
    voice: Optional[str] = None
    cooldown_minutes: Optional[float] = None
    enabled: Optional[bool] = None
    trigger_mode: Optional[str] = None
    share_only_window: Optional[bool] = None
    disclosure_ack: Optional[bool] = None


class NoteEntry(BaseModel):
    season: int
    episode: int
    status: Optional[str] = None
    arc_type: Optional[str] = None
    current_arc: Optional[str] = None
    last_seen_description: Optional[str] = None


class NotesBody(BaseModel):
    relationship_summary: Optional[str] = None
    entries: list[NoteEntry]


_STATUSES = ("alive", "dead", "unknown")
_ARC_TYPES = ("danger", "conflict", "alliance", "discovery", "deception", "loss",
              "turning_point", "other")
_MAX_ENTRIES, _MAX_TEXT, _MAX_BIO = 60, 300, 600


def validate_notes(body: NotesBody) -> tuple[Optional[list[dict]], Optional[str]]:
    """(clean_entries, None) or (None, plain-language problem)."""
    if len(body.entries) > _MAX_ENTRIES:
        return None, f"That's a lot of notes. Keep it to {_MAX_ENTRIES} per character."
    if body.relationship_summary is not None and len(body.relationship_summary.strip()) > _MAX_BIO:
        return None, f"The relationships text is too long (max {_MAX_BIO} characters)."
    clean, seen = [], set()
    for i, e in enumerate(body.entries, start=1):
        where = f"Note {i}: "
        if not 1 <= e.season <= 50:
            return None, where + "season must be between 1 and 50."
        if not 1 <= e.episode <= 500:
            return None, where + "episode must be between 1 and 500."
        if (e.season, e.episode) in seen:
            return None, where + f"S{e.season:02d}E{e.episode:02d} already has a note. Use one note per episode."
        seen.add((e.season, e.episode))
        status = (e.status or "unknown").strip().lower()
        if status not in _STATUSES:
            return None, where + "status must be alive, dead or unknown."
        arc_type = (e.arc_type or "other").strip().lower()
        if arc_type not in _ARC_TYPES:
            return None, where + "pick one of the listed arc types."
        arc = (e.current_arc or "").strip()
        scene = (e.last_seen_description or "").strip()
        if len(arc) > _MAX_TEXT or len(scene) > _MAX_TEXT:
            return None, where + f"keep each text under {_MAX_TEXT} characters."
        clean.append({"season": e.season, "episode": e.episode, "status": status,
                      "arc_type": arc_type, "current_arc": arc, "last_seen_description": scene})
    return clean, None


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "message": message}, status_code=status)


def _api_err(message: str, status: int = 400) -> JSONResponse:
    """Error shape of /api/ask, /api/feedback: {"error": ...} (+ the usual ok/message)."""
    return JSONResponse({"error": message, "ok": False, "message": message}, status_code=status)


def _poster_url(path: Optional[str]) -> Optional[str]:
    return f"{_POSTER}{path}" if path else None


# ── router ──────────────────────────────────────────────────────────────────

def build_router(deps: ApiDeps) -> APIRouter:
    r = APIRouter(prefix="/api")

    def key(name: str) -> str:
        return deps.keystore.get_key(name, deps.env_path)

    @r.get("/state")
    def state(permissions: bool = False):
        cfg = config_store.read_config(deps.config_path)
        db = deps.get_db()
        ready = db.get_ready_shows()
        pos = config_store.positions(cfg)
        cur_id = cfg.get("show_id")
        shows = []
        current = {"show_id": None, "show_name": None, "season": 1, "episode": 1}
        for s in ready:
            p = pos.get(s["show_id"], {})
            is_cur = s["show_id"] == cur_id
            season = cfg.get("season") if is_cur and isinstance(cfg.get("season"), int) \
                else p.get("season", 1)
            episode = cfg.get("episode") if is_cur and isinstance(cfg.get("episode"), int) \
                else p.get("episode", 1)
            item = {"show_id": s["show_id"], "show_name": s["show_name"],
                    "total_characters": s.get("total_characters") or 0,
                    "poster_url": _poster_url(s.get("poster_path")),
                    "season": season, "episode": episode, "current": is_cur}
            shows.append(item)
            if is_cur:
                current = {k: item[k] for k in ("show_id", "show_name", "season", "episode")}
        tmdb, claude = bool(key("TMDB_API_KEY")), bool(key("ANTHROPIC_API_KEY"))
        return {
            "setup": {"tmdb_key": tmdb, "anthropic_key": claude},
            # The probe takes a real screenshot (~1 s): only when asked, never on the hotkey path.
            "permissions": deps.permissions() if permissions else {"screen_recording": None},
            "current": current,
            "shows": shows,
            "job": deps.runner.status(),
            "needs_first_run": (not tmdb) or (not ready),
            # Ask/React upload a screenshot: only the Chrome window unless the owner turned this off.
            "share_only_window": (cfg.get("buddy") or {}).get("share_only_window", True) is not False,
        }

    # keys -------------------------------------------------------------------

    @r.post("/keys")
    def save_key(body: KeyBody):
        if body.name not in deps.keystore.KEYS:
            return _err("That isn't a key Goldfish Brain uses.")
        value = deps.keystore.clean_key(body.value)
        if not value:
            return _err("Paste your key first.")
        check = (deps.key_check.validate_tmdb if body.name == "TMDB_API_KEY"
                 else deps.key_check.validate_anthropic)
        ok, message = check(value)
        if not ok:
            return _err(message)
        deps.keystore.set_key(body.name, value)
        return {"ok": True, "message": message}

    @r.delete("/keys/{name}")
    def delete_key(name: str):
        if name not in deps.keystore.KEYS:
            return _err("That isn't a key Goldfish Brain uses.")
        deps.keystore.delete_key(name)
        return {"ok": True}

    # search / setup ------------------------------------------------------------

    @r.get("/shows/search")
    def search(q: str = ""):
        q = " ".join(q.split())[:100]
        tmdb_key = key("TMDB_API_KEY")
        if not q:
            return {"results": []}
        if not tmdb_key:
            return _err("Add your TMDB key first.")
        try:
            data = deps.tmdb_get("search/tv", {"api_key": tmdb_key, "query": q})
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return _err("TMDB rejected your key — update it in Settings.", 400)
            return _err("TMDB had a problem. Try again in a moment.", 502)
        except Exception:
            return _err("Couldn't reach TMDB — check your internet connection.", 502)
        ready = deps.get_db().get_ready_shows()
        ready_tmdb = {s.get("tmdb_id") for s in ready}
        ready_ids = {s["show_id"] for s in ready}
        out = []
        for item in (data.get("results") or [])[:8]:
            name = item.get("name") or ""
            out.append({
                "tmdb_id": item.get("id"), "name": name,
                "year": (item.get("first_air_date") or "")[:4],
                "poster_path": item.get("poster_path"),
                "poster_url": _poster_url(item.get("poster_path")),
                "overview": (item.get("overview") or "")[:180],
                "installed": item.get("id") in ready_tmdb
                             or _show_id(name, item.get("id")) in ready_ids,
            })
        return {"results": out}

    @r.post("/shows/setup")
    def setup(body: SetupBody):
        name = " ".join(body.name.split())
        if not name:
            return _err("Pick a show first.")
        if not key("TMDB_API_KEY"):
            return _err("Add your TMDB key first.")
        show_id = _show_id(name, body.tmdb_id)
        ready = deps.get_db().get_ready_shows()
        if not body.update and any(
                s["show_id"] == show_id or s.get("tmdb_id") == body.tmdb_id for s in ready):
            return _err(f"{name} is already set up. Use Update to refresh it.", 409)
        notes = bool(body.with_notes and key("ANTHROPIC_API_KEY"))
        try:
            sid = deps.runner.start(name, body.tmdb_id, body.poster_path, notes)
        except JobBusy as exc:
            return _err(str(exc), 409)
        msg = "Setting up…"
        if body.with_notes and not notes:
            msg = "Setting up faces only — add an Anthropic key later to get character notes."
        return {"ok": True, "show_id": sid, "notes_enabled": notes, "message": msg}

    @r.get("/shows/job")
    def job():
        return {"job": deps.runner.status()}

    @r.post("/shows/job/cancel")
    def cancel():
        deps.runner.cancel()
        return {"ok": True}

    # selection / position ------------------------------------------------------

    def _is_ready(show_id: str) -> bool:
        return any(s["show_id"] == show_id for s in deps.get_db().get_ready_shows())

    def _saved_position(show_id: str) -> tuple[int, int]:
        pos = config_store.positions(config_store.read_config(deps.config_path)).get(show_id) or {}
        season, episode = pos.get("season", 1), pos.get("episode", 1)
        if not (isinstance(season, int) and isinstance(episode, int)) or season < 1 or episode < 1:
            return 1, 1
        return season, episode

    def _is_met(db, char: dict, season: int, episode: int) -> bool:
        """Same rule as /identify: 'full' visibility at the viewer's saved position."""
        arc = None
        if char.get("first_season") is None:
            arc, _ = _spoiler.pick_arc(db, char["character_id"], season, episode, None)
        return _spoiler.visibility(char, season, episode, arc) == "full"

    @r.get("/shows/{show_id}/characters")
    def show_characters(show_id: str, all: int = 0):
        """The cast of a finished show, for the library's "N characters" list.
        By default only characters the viewer has met at their saved position;
        `all=1` is the explicit spoiler opt-in."""
        if not _is_ready(show_id):
            return _err("That show isn't set up yet.", 404)
        db = deps.get_db()
        season, episode = _saved_position(show_id)
        met = {c["character_id"] for c in db.get_show_characters(show_id)
               if _is_met(db, c, season, episode)}
        rows = db.get_show_characters_overview(show_id)
        shown = [r for r in rows if all or r["character_id"] in met]
        chars = [{"character_id": r["character_id"], "name": r["character_name"],
                  "actor": r["actor_name"], "role": r["role"], "has_notes": r["has_notes"],
                  "photo_url": f"/api/characters/{r['character_id']}/photo"} for r in shown]
        return {"total": len(chars), "with_notes": sum(1 for c in chars if c["has_notes"]),
                "hidden_count": sum(1 for r in rows if r["character_id"] not in met),
                "includes_spoilers": bool(all), "characters": chars}

    @r.get("/shows/{show_id}/relationships")
    def relationships(show_id: str, season: Optional[int] = None, episode: Optional[int] = None):
        """Characters introduced by the position + who is related to whom."""
        db = deps.get_db()
        if not _is_ready(show_id):
            return _api_err("That show isn't set up yet.", 404)
        for given in (season, episode):
            if given is not None and not 1 <= given <= _ask.MAX_POSITION:
                return _api_err(f"Season and episode must be between 1 and {_ask.MAX_POSITION}.")
        if season is None or episode is None:
            season, episode = _saved_position(show_id)
        return _relationships.build_graph(db.get_show_characters(show_id), season, episode, db=db)

    @r.get("/characters/{character_id}/notes")
    def get_notes(character_id: str, all: int = 0):
        db = deps.get_db()
        char = db.get_character(character_id)
        if not char:
            return _err("That character isn't in your library.", 404)
        if not all:
            season, episode = _saved_position(char["show_id"])
            if not _is_met(db, char, season, episode):
                return _api_err("This character is not met yet at your place in the show, "
                                "so their notes are hidden to avoid spoilers.", 403)
        show = db.get_show(char["show_id"]) or {}
        entries = [{k: row.get(k) for k in ("season", "episode", "status", "arc_type",
                                            "current_arc", "last_seen_description")}
                   for row in db.get_user_arcs(character_id)]
        return {"character_id": character_id, "name": char["character_name"],
                "actor": char.get("actor_name"),
                "relationship_summary": char.get("relationship_summary") or "",
                "entries": entries,
                "also_in": actor_credits.cached_also_in(db, char, show.get("tmdb_id")),
                "ai_rows": db.count_ai_arcs(character_id)}

    @r.put("/characters/{character_id}/notes")
    def put_notes(character_id: str, body: NotesBody):
        db = deps.get_db()
        if not db.get_character(character_id):
            return _err("That character isn't in your library.", 404)
        entries, problem = validate_notes(body)
        if problem:
            return _err(problem)
        bio = body.relationship_summary.strip() if body.relationship_summary is not None else None
        try:
            db.replace_user_arcs(character_id, entries, bio)
            db.derive_first_seen_for_character(character_id)
        except Exception:
            return _err("Couldn't save your notes. Nothing was changed — try again.", 500)
        return {"ok": True, "message": "Saved", "count": len(entries)}

    @r.post("/shows/select")
    def select(body: SelectBody):
        if not _is_ready(body.show_id):
            return _err("That show isn't set up yet.", 404)
        try:
            config_store.select_show(deps.config_path, body.show_id)
        except config_store.ConfigUnreadable:
            return _err("Goldfish Brain's settings file looks damaged, so it was left "
                        "untouched. Fix or delete data/config.json.", 409)
        return {"ok": True}

    @r.post("/position")
    def position(body: PositionBody):
        if body.season < 1 or body.episode < 1:
            return _err("Season and episode must be 1 or higher.")
        if not _is_ready(body.show_id):
            return _err("That show isn't set up yet.", 404)
        try:
            config_store.set_position(deps.config_path, body.show_id,
                                      body.season, body.episode)
        except config_store.ConfigUnreadable:
            return _err("Goldfish Brain's settings file looks damaged, so it was left "
                        "untouched. Fix or delete data/config.json.", 409)
        return {"ok": True}

    @r.get("/episodes/season")
    def episode_season(show_id: str = "", episode: int = 0, title: str = ""):
        """Which season has episode N with this title? {"season": int|null}; never an error."""
        try:
            api_key = key("TMDB_API_KEY")
            show = next((s for s in deps.get_db().get_all_shows() if s["show_id"] == show_id), None)
            if not api_key or not show or not show.get("tmdb_id") or episode < 1:
                return {"season": None}
            get = lambda path, params: deps.tmdb_get(path, {**params, "api_key": api_key})
            return {"season": find_season(get, show["tmdb_id"], episode, title[:120])}
        except Exception:
            return {"season": None}

    _structure = {"cache": {}}

    @r.get("/shows/{show_id}/structure")
    def show_structure(show_id: str):
        """Episodes per season from TMDB, e.g. {"seasons": {"1": 10, "2": 8}}; {} if unknown. Never an error."""
        try:
            show = next((s for s in deps.get_db().get_all_shows() if s["show_id"] == show_id), None)
            api_key = key("TMDB_API_KEY")
            if not show or not show.get("tmdb_id") or not api_key:
                return {"seasons": {}}
            cache = _structure["cache"]
            if show["tmdb_id"] not in cache:
                data = deps.tmdb_get(f"tv/{show['tmdb_id']}", {"api_key": api_key})
                cache[show["tmdb_id"]] = {str(x["season_number"]): int(x.get("episode_count") or 0)
                                          for x in data.get("seasons", [])
                                          if x.get("season_number", 0) >= 1 and (x.get("episode_count") or 0) > 0}
            return {"seasons": cache[show["tmdb_id"]]}
        except Exception:
            return {"seasons": {}}

    # ask about this scene ---------------------------------------------------------

    _guide = {"cache": {}}

    def _cached_guide(tmdb_id: int, tmdb_key: str) -> list:
        cache = _guide["cache"]
        if tmdb_id not in cache:
            fetch = deps.episode_guide
            if fetch is None:
                from populate_metadata import _get_episode_guide as fetch
            cache[tmdb_id] = fetch(tmdb_id, tmdb_key)
        return cache[tmdb_id]

    @r.post("/ask")
    def ask_scene(body: AskBody):
        api_key = key("ANTHROPIC_API_KEY")
        if not api_key:
            return _api_err("Add your Anthropic key in Settings to ask questions.")
        cfg = config_store.read_config(deps.config_path) if body.image_b64 or body.mode == "react" else {}
        if body.image_b64:
            if not (cfg.get("buddy") or {}).get("disclosure_ack"):
                return _api_err("needs_disclosure", 403)    # the screenshot goes to Anthropic
        try:
            client = (deps.make_llm or _llm.make_client)(api_key)
            result = _ask.answer(client, deps.get_db(), question=body.question,
                                 image_b64=body.image_b64, show_id=body.show_id,
                                 season=body.season, episode=body.episode,
                                 progress=body.progress, guide_fn=_cached_guide,
                                 tmdb_key=key("TMDB_API_KEY"), mode=body.mode,
                                 buddy=cfg.get("buddy"))
        except _ask.AskError as exc:
            return _api_err(exc.message, exc.status)
        except _llm.LLMError as exc:
            return _api_err(str(exc), 502)
        except Exception:
            # never echo the exception: it could carry the question or the image
            return _api_err("Something went wrong answering that. Try again.", 500)
        return result

    # accuracy feedback -------------------------------------------------------------

    @r.post("/feedback")
    def post_feedback(body: FeedbackBody):
        if body.kind not in _feedback.KINDS:
            return _api_err("kind must be wrong, missed or ok.")
        if not body.show_id.strip():
            return _api_err("Say which show this is about.")
        if body.note and len(body.note) > _feedback.MAX_NOTE:
            return _api_err(f"Keep the note under {_feedback.MAX_NOTE} characters.")
        if deps.data_dir is None:
            return _api_err("Feedback isn't available right now.", 500)
        try:
            _feedback.record(deps.data_dir, show_id=body.show_id.strip(), kind=body.kind,
                             season=body.season, episode=body.episode,
                             character_id=body.character_id, note=body.note)
        except OSError:
            return _api_err("Couldn't save that feedback.", 500)
        return {"ok": True}

    @r.get("/feedback/summary")
    def feedback_summary():
        return _feedback.summary(deps.data_dir) if deps.data_dir else \
            {"total": 0, "wrong": 0, "missed": 0, "ok": 0}

    @r.get("/accuracy/summary")
    def accuracy_summary():
        return _accuracy.summary(deps.data_dir or Path("/nonexistent-cineid"))

    # buddy / permissions -------------------------------------------------------

    @r.get("/buddy")
    def get_buddy():
        cfg = config_store.read_config(deps.config_path)
        merged = {**_buddy._DEFAULT_BUDDY, **(cfg.get("buddy") or {})}
        return {"buddy": merged,
                "personalities": list(_buddy._PERSONALITY_DESCRIPTIONS)}

    @r.post("/buddy")
    def set_buddy(body: BuddyBody):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if "personality" in changes and changes["personality"] not in _buddy._PERSONALITY_DESCRIPTIONS:
            return _err("Pick one of the listed personalities.")
        if "trigger_mode" in changes and changes["trigger_mode"] not in ("audio", "timer"):
            return _err("Trigger must be audio or timer.")
        if "cooldown_minutes" in changes and changes["cooldown_minutes"] < 0:
            return _err("Cooldown can't be negative.")
        if "name" in changes:
            changes["name"] = changes["name"].strip()
            if not changes["name"]:
                return _err("Give your buddy a name.")

        def _merge(cfg: dict) -> None:
            cur = {**_buddy._DEFAULT_BUDDY, **(cfg.get("buddy") or {})}
            cur.update(changes)
            cfg["buddy"] = cur

        try:
            config_store.update_config(deps.config_path, _merge)
        except config_store.ConfigUnreadable:
            return _err("Goldfish Brain's settings file looks damaged, so it was left "
                        "untouched. Fix or delete data/config.json.", 409)
        return {"ok": True}

    @r.get("/characters/{character_id}/photo")
    def character_photo(character_id: str):
        """Best reference photo for a character (the Mac app's popup card)."""
        if deps.data_dir is None or not re.fullmatch(r"[a-z0-9-]+", character_id):
            return _err("No photo.", 404)
        char = deps.get_db().get_character(character_id)
        if not char:
            return _err("No photo.", 404)
        base = (deps.data_dir / "shows").resolve()
        raw = (base / char["show_id"] / "raw" / character_id).resolve()
        if base not in raw.parents or not raw.is_dir():
            return _err("No photo.", 404)
        candidates = [raw / n for n in ("tmdb_profile.jpg", "tmdb_0.jpg", "tmdb_1.jpg")]
        for pat in ("*.jpg", "*.jpeg", "*.png"):
            candidates += sorted(raw.glob(pat))
        for p in candidates:
            if p.is_file():
                kind = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                return Response(content=p.read_bytes(), media_type=kind)
        return _err("No photo.", 404)

    @r.post("/open")
    def open_target(body: OpenBody):
        """Open System Settings or a key page. Whitelist only — the web UI can
        not make the app open arbitrary URLs or files."""
        url = _OPEN_TARGETS.get(body.target)
        if url is None:
            return _err("That can't be opened from here.")
        try:
            (deps.open_target or default_open)(url)
        except Exception:
            return _err("Couldn't open that. Open it from System Settings yourself.", 500)
        return {"ok": True}

    @r.get("/permissions")
    def permissions():
        return deps.permissions()

    return r


def install(app: FastAPI, deps: ApiDeps) -> None:
    app.include_router(build_router(deps))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api"):
            return _err("That request wasn't understood.", 422)
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)


def backfill_posters(db, tmdb_get: Callable[[str, dict], dict], api_key: str) -> int:
    """Fill poster_path for ready shows that predate poster storage.
    Best-effort: a failure for one show never stops the others."""
    filled = 0
    for show in db.get_ready_shows():
        if show.get("poster_path") or not show.get("tmdb_id"):
            continue
        try:
            data = tmdb_get(f"tv/{show['tmdb_id']}", {"api_key": api_key})
        except Exception:
            continue
        if data.get("poster_path"):
            db.set_show_poster(show["show_id"], data["poster_path"])
            filled += 1
    return filled
