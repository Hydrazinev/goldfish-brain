"""An actor's other well-known work ("Also in" on the card back).

TMDB access is injected (`tmdb_get(path, params)`), so everything here is testable offline.
Credits are fetched in the background and cached in `actor_credits`; the /identify path only
ever reads the cache (no network while the viewer waits).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

_SKIP_GENRES = {10767, 10763, 10764}          # talk, news, reality (awards shows fall under these)
_SELF_ROLES = ("self", "himself", "herself")
_FETCH_LIMIT = 8                              # cached per actor; the card shows up to 4 after dropping its own show
_SLEEP = 0.3


def _is_self(character: str) -> bool:
    c = (character or "").strip().lower()
    return any(c == s or c.startswith(s + " ") or c.startswith(s + "(") or c.startswith(s + "/")
               for s in _SELF_ROLES)


def top_credits(tmdb_get: Callable[[str, dict], dict], key: str, person_id: int,
                exclude_tmdb_id: Optional[int], limit: int = 4) -> list[dict]:
    """Best-known acting credits: [{"id","title","year","kind"}], most voted first."""
    data = tmdb_get(f"person/{person_id}/combined_credits", {"api_key": key})
    best: dict[str, tuple[int, dict]] = {}
    for c in data.get("cast") or []:
        kind = {"movie": "movie", "tv": "series"}.get(c.get("media_type"))
        if kind is None:
            continue
        if kind == "series" and exclude_tmdb_id is not None and c.get("id") == exclude_tmdb_id:
            continue
        if _SKIP_GENRES & set(c.get("genre_ids") or []) or _is_self(c.get("character") or ""):
            continue
        title = (c.get("title") or c.get("name") or "").strip()
        if not title:
            continue
        date = c.get("release_date") or c.get("first_air_date") or ""
        year = int(date[:4]) if date[:4].isdigit() else None
        votes = c.get("vote_count") or 0
        slot = title.casefold()
        if slot not in best or votes > best[slot][0]:
            best[slot] = (votes, {"id": c.get("id"), "title": title, "year": year, "kind": kind})
    ranked = sorted(best.values(), key=lambda t: t[0], reverse=True)
    return [item for _, item in ranked[:limit]]


def _norm(name: str) -> str:
    return " ".join((name or "").casefold().split())


def backfill_actor_ids(db, tmdb_get, key: str) -> int:
    """Fill characters.actor_tmdb_id for shows ingested before it was stored (match by actor name)."""
    filled = 0
    for show in db.get_ready_shows():
        if not show.get("tmdb_id"):
            continue
        chars = [c for c in db.get_show_characters(show["show_id"])
                 if c.get("actor_tmdb_id") is None and c.get("actor_name")]
        if not chars:
            continue
        try:
            cast = tmdb_get(f"tv/{show['tmdb_id']}/aggregate_credits", {"api_key": key}).get("cast") or []
        except Exception:
            continue
        ids = {_norm(m.get("name")): m.get("id") for m in cast if m.get("id") and m.get("name")}
        for c in chars:
            pid = ids.get(_norm(c["actor_name"]))
            if pid:
                db.set_actor_tmdb_id(c["character_id"], pid)
                filled += 1
    return filled


def _is_fresh(cached: Optional[dict], max_age_days: int) -> bool:
    if not cached or not cached.get("fetched_at"):
        return False
    try:
        at = datetime.fromisoformat(cached["fetched_at"])
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - at < timedelta(days=max_age_days)


def fill_credits(db, tmdb_get, key: str, max_age_days: int = 30,
                 sleep: Callable[[float], None] = time.sleep) -> int:
    """Fetch + cache credits for every known actor whose cache is missing or expired.
    One failing actor never stops the rest. Returns how many were fetched."""
    ids: list[int] = []
    for show in db.get_ready_shows():
        for c in db.get_show_characters(show["show_id"]):
            pid = c.get("actor_tmdb_id")
            if pid and pid not in ids:
                ids.append(pid)
    fetched = 0
    for pid in ids:
        if _is_fresh(db.get_cached_credits(pid), max_age_days):
            continue
        try:
            credits = top_credits(tmdb_get, key, pid, None, limit=_FETCH_LIMIT)
        except Exception:
            continue
        db.set_cached_credits(pid, credits)
        fetched += 1
        sleep(_SLEEP)
    return fetched


def cached_also_in(db, char: dict, show_tmdb_id: Optional[int], limit: int = 4) -> list[str]:
    """["Better Call Saul (2015)", ...] from the cache only; [] when not cached yet."""
    pid = char.get("actor_tmdb_id")
    cached = db.get_cached_credits(pid) if pid else None
    if not cached:
        return []
    out = []
    for c in cached["credits"]:
        if show_tmdb_id is not None and c.get("kind") == "series" and c.get("id") == show_tmdb_id:
            continue
        out.append(f"{c['title']} ({c['year']})" if c.get("year") else c["title"])
        if len(out) == limit:
            break
    return out
