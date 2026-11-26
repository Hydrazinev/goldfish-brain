from __future__ import annotations

import base64
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure src/ sibling modules are importable however the server is invoked
sys.path.insert(0, str(Path(__file__).parent))
from recogniser import FaceRecogniser, FaceIndex
from database import CharacterDB
import actor_credits
import api_routes
import key_check
import keystore
from show_jobs import ShowSetupRunner
from fastapi.responses import RedirectResponse
from static_files import NoCacheStaticFiles

_ROOT = Path(__file__).parent.parent
_DATA_DIR = _ROOT / "data"
_DB_PATH = _DATA_DIR / "cineid.db"
_ENV_PATH = _ROOT / ".env"
_CONFIG_PATH = _DATA_DIR / "config.json"
_UI_DIR = Path(__file__).parent / "ui"
_MODEL_NAME = "buffalo_l"
_SIMILARITY_THRESHOLD = 0.5
_MIN_DET_SCORE = 0.5      # ignore very low-quality detections (blurry/partial faces)


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------

class IdentifyRequest(BaseModel):
    image: str          # base64-encoded PNG/JPEG
    show_id: str
    season: int
    episode: int
    progress: Optional[float] = None     # 0..1 share of the episode played so far


class LoadShowRequest(BaseModel):
    show_id: str


# ------------------------------------------------------------------
# Application state
# ------------------------------------------------------------------

recogniser: FaceRecogniser
db: CharacterDB
indexes: dict[str, FaceIndex] = {}   # show_id → loaded FaceIndex


# ------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global recogniser, db

    keystore.export_to_environ(_ENV_PATH)    # Keychain keys -> os.environ
    recogniser = FaceRecogniser(model_name=_MODEL_NAME)
    db = CharacterDB(db_path=_DB_PATH)
    stale = db.fail_stale_ingests()      # a crashed setup must not look usable
    if stale:
        print(f"[Goldfish Brain] marked {stale} unfinished show setup(s) as failed")

    for show in db.get_all_shows():
        if show["status"] != "ready":
            continue
        index_path = _DATA_DIR / "shows" / show["show_id"] / "embeddings.faiss"
        if index_path.exists():
            idx = FaceIndex()
            idx.load(index_path)
            indexes[show["show_id"]] = idx

    def _poster_backfill():
        try:
            k = keystore.get_key("TMDB_API_KEY", _ENV_PATH)
            if k:
                api_routes.backfill_posters(db, api_routes.default_tmdb_get, k)
        except Exception:
            pass          # best-effort; also fires harmlessly if the server shut down first

    def _credits_backfill():
        try:
            k = keystore.get_key("TMDB_API_KEY", _ENV_PATH)
            if k:
                actor_credits.backfill_actor_ids(db, api_routes.default_tmdb_get, k)
                actor_credits.fill_credits(db, api_routes.default_tmdb_get, k)
        except Exception:
            pass          # best-effort; "Also in" just stays empty

    import threading as _t
    _t.Thread(target=_credits_backfill, daemon=True, name="CreditsBackfill").start()
    _t.Thread(target=_poster_backfill, daemon=True, name="PosterBackfill").start()

    print("Goldfish Brain server ready")
    yield
    db.close()


# ------------------------------------------------------------------
# App + CORS
# ------------------------------------------------------------------

app = FastAPI(title="Goldfish Brain", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Web UI + /api (first run, keys, add-a-show, library, settings)
# ------------------------------------------------------------------

def _on_show_ready(show_id: str) -> None:
    """A setup job finished ingesting: make the new show identifiable now."""
    index_path = _DATA_DIR / "shows" / show_id / "embeddings.faiss"
    if index_path.exists():
        idx = FaceIndex()
        idx.load(index_path)
        indexes[show_id] = idx


def _key_env() -> dict:
    out = {}
    for name in keystore.KEYS:
        value = keystore.get_key(name, _ENV_PATH)
        if value:
            out[name] = value
    return out


_runner = ShowSetupRunner(db_path=_DB_PATH, project_root=_ROOT,
                          env_provider=_key_env, on_ready=_on_show_ready)

api_routes.install(app, api_routes.ApiDeps(
    config_path=_CONFIG_PATH, env_path=_ENV_PATH,
    get_db=lambda: db, keystore=keystore, key_check=key_check,
    runner=_runner, tmdb_get=api_routes.default_tmdb_get,
    permissions=api_routes.cached(api_routes.default_permissions, ttl=10),
    data_dir=_DATA_DIR,
))

if _UI_DIR.exists():
    app.mount("/ui", NoCacheStaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def _root():
    return RedirectResponse("/ui/")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _decode_image(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("cv2 could not decode image bytes")
    return image


def _arc_shows_presence(arc: Optional[dict]) -> bool:
    """True if a per-episode arc row proves the character has appeared."""
    if not arc:
        return False
    status = (arc.get("status") or "unknown").strip().lower()
    return status != "unknown" or bool((arc.get("current_arc") or "").strip())


def _passes_spoiler_shield(char: dict, season: int, episode: int,
                           arc: Optional[dict] = None) -> bool:
    """Return True if the character has already been introduced by this point.

    Uses first_season / first_episode. When the first appearance is unknown
    (never derived), the character is shown only if a per-episode arc row at
    or before the current episode proves they have appeared. Unknown used to
    mean "show", which leaked characters the viewer had not met yet.
    """
    first_season = char.get("first_season")
    first_episode = char.get("first_episode")
    if first_season is None:
        return _arc_shows_presence(arc)
    if first_season < season:
        return True
    if first_season == season and (first_episode is None or first_episode <= episode):
        return True
    return False


def _visibility(char: dict, season: int, episode: int, arc: Optional[dict]) -> str:
    """'full' | 'limited' | 'hidden'.

    hidden  - first appearance is known and still ahead of the viewer.
    limited - first appearance unknown and no arc data (the show has no
              character notes yet): identify them, but show no story details.
    full    - safe to show everything we have.
    """
    if char.get("first_season") is None:
        return "full" if _arc_shows_presence(arc) else "limited"
    return "full" if _passes_spoiler_shield(char, season, episode, arc=arc) else "hidden"


def _limited_result(char: dict, confidence: float) -> dict:
    """Name / actor / role only: nothing that could spoil the story."""
    out = _build_character_result(char, confidence, None)
    for key in ("status", "arc_type", "first_seen_season", "first_seen_episode",
                "last_seen_season", "last_seen_episode", "relationship_summary",
                "current_arc", "last_seen_description"):
        out[key] = None
    out["limited"] = True
    return out


_REVEAL_AT = 0.9    # share of an episode that must be played before its own notes show


def _pick_arc(db, character_id: str, season: int, episode: int, progress):
    """(arc_row, "start"|"end"). Notes are written per episode, so early in an
    episode they may describe events from later in it. Until `_REVEAL_AT` of the
    episode has played (or if the playback position is unknown) show the state at
    the END of the PREVIOUS episode; afterwards this episode's notes."""
    if progress is not None and progress >= _REVEAL_AT:
        return db.get_character_arc_at_or_before(character_id, season, episode), "end"
    return db.get_character_arc_at_or_before(character_id, season, episode - 1), "start"


def _dedupe_and_rank(results: list[dict]) -> list[dict]:
    """One row per character (best confidence), most confident first, so the
    popup headlines the best match rather than the first face detected."""
    best: dict[str, dict] = {}
    for r in results:
        cur = best.get(r["character_id"])
        if cur is None or r["confidence"] > cur["confidence"]:
            best[r["character_id"]] = r
    return sorted(best.values(), key=lambda r: r["confidence"], reverse=True)


def _assign_one_to_one(candidates: list[list[tuple[str, float]]]
                       ) -> list[Optional[tuple[str, float]]]:
    """candidates[i] = face i's (character_id, score) hits at/above threshold,
    best first. Returns one (character_id, score) or None per face, with each
    character claimed by at most one face. Highest scores are settled first, so
    a weaker face whose best match was taken falls back to its next-best hit
    instead of vanishing (two faces used to collapse onto one character)."""
    pairs = sorted(
        ((score, i, cid) for i, hits in enumerate(candidates) for cid, score in hits),
        key=lambda p: p[0], reverse=True)
    out: list[Optional[tuple[str, float]]] = [None] * len(candidates)
    taken: set[str] = set()
    for score, i, cid in pairs:
        if out[i] is None and cid not in taken:
            out[i] = (cid, score)
            taken.add(cid)
    return out


def _build_character_result(char: dict, confidence: float,
                             arc: Optional[dict] = None) -> dict:
    # Arc fields come only from per-episode rows (get_character_arc_at_or_before,
    # never future episodes). The character-level columns hold whole-show
    # end-state — falling back to them leaks spoilers into the overlay.
    arc = arc or {}
    return {
        "character_id":         char["character_id"],
        "name":                 char["character_name"],
        "actor":                char["actor_name"],
        "role":                 char["role"],
        "confidence":           round(confidence, 4),
        "status":               arc.get("status"),
        "arc_type":             arc.get("arc_type"),
        "first_seen_season":    char["first_season"],
        "first_seen_episode":   char["first_episode"],
        # last-seen = episode of the arc row being shown, not the character's
        # final appearance in the whole show (which spoils their longevity)
        "last_seen_season":     arc.get("season"),
        "last_seen_episode":    arc.get("episode"),
        "relationship_summary": char["relationship_summary"],
        "current_arc":          arc.get("current_arc"),
        "last_seen_description":arc.get("last_seen_description"),
        "importance_rank":      char["importance_rank"],
    }


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "loaded_shows": list(indexes.keys()),
        "model": _MODEL_NAME,
    }


@app.post("/identify")
def identify(req: IdentifyRequest):
    try:
        image = _decode_image(req.image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}")

    if req.show_id not in indexes:
        raise HTTPException(
            status_code=404,
            detail=f"Show '{req.show_id}' is not loaded. Call /load-show first.",
        )

    index = indexes[req.show_id]
    show_row = db.get_show(req.show_id) or {}
    show_tmdb_id = show_row.get("tmdb_id")
    face_detections = recogniser.detect_faces(image)
    total_faces = len(face_detections)

    characters: list[dict] = []
    any_face_attempted = False
    hidden_by_shield = 0

    face_hits: list[list[tuple[str, float]]] = []
    for _crop, _bbox, face in face_detections:
        if getattr(face, "det_score", 1.0) < _MIN_DET_SCORE:
            continue
        embedding = recogniser.get_embedding(image, face)
        if embedding is None:
            continue

        any_face_attempted = True
        matches = index.search(embedding, top_k=5)

        # Keep only confident hits
        face_hits.append([(cid, score) for cid, score in matches if score >= _SIMILARITY_THRESHOLD])

    # One face per character: a second face can no longer be swallowed by the
    # character the first face already claimed.
    for assigned in _assign_one_to_one(face_hits):
        if assigned is None:
            continue
        character_id, confidence = assigned
        char = db.get_character(character_id)
        if char is None:
            continue

        arc, as_of = _pick_arc(db, character_id, req.season, req.episode, req.progress)
        visibility = _visibility(char, req.season, req.episode, arc)
        also_in = actor_credits.cached_also_in(db, char, show_tmdb_id)     # cache only, never the network
        if visibility == "hidden":
            hidden_by_shield += 1
            continue
        if visibility == "limited":
            limited = _limited_result(char, confidence)
            limited["also_in"] = also_in
            characters.append(limited)
            continue

        result = _build_character_result(char, confidence, arc)
        result["as_of"] = as_of
        result["also_in"] = also_in
        characters.append(result)

    characters = _dedupe_and_rank(characters)

    # A recognised character was withheld to avoid spoilers: say so, instead
    # of the misleading "face not recognised".
    if not characters and hidden_by_shield:
        return {
            "characters": [],
            "hidden_by_spoiler_shield": hidden_by_shield,
            "total_faces_detected": total_faces,
            "show_id": req.show_id,
        }

    # All faces were attempted but none crossed the confidence threshold
    if any_face_attempted and not characters:
        return {
            "characters": [],
            "low_confidence": True,
            "total_faces_detected": total_faces,
            "show_id": req.show_id,
        }

    return {
        "characters": characters,
        "total_faces_detected": total_faces,
        "show_id": req.show_id,
    }


@app.post("/load-show")
def load_show(req: LoadShowRequest):
    show_id = req.show_id

    if not db.show_exists(show_id):
        raise HTTPException(status_code=404, detail=f"Show '{show_id}' not in database")

    index_path = _DATA_DIR / "shows" / show_id / "embeddings.faiss"
    if not index_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No FAISS index found for '{show_id}' at {index_path}",
        )

    idx = FaceIndex()
    idx.load(index_path)
    indexes[show_id] = idx

    character_count = len(db.get_show_characters(show_id))
    return {"status": "loaded", "character_count": character_count}


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
