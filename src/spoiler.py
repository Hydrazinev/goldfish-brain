"""The spoiler-shield rules, importable without the face engine.

These mirror `_passes_spoiler_shield`, `_visibility` and `_pick_arc` in
server.py (tests/test_spoiler.py pins the two copies to the same behaviour).
The ask feature and the relationship map use them so they can never show more
than the identify popup would.
"""
from __future__ import annotations

from typing import Optional

REVEAL_AT = 0.9    # share of an episode that must be played before its own notes show


def arc_shows_presence(arc: Optional[dict]) -> bool:
    if not arc:
        return False
    status = (arc.get("status") or "unknown").strip().lower()
    return status != "unknown" or bool((arc.get("current_arc") or "").strip())


def passes_shield(char: dict, season: int, episode: int,
                  arc: Optional[dict] = None) -> bool:
    first_season, first_episode = char.get("first_season"), char.get("first_episode")
    if first_season is None:
        return arc_shows_presence(arc)
    if first_season < season:
        return True
    return first_season == season and (first_episode is None or first_episode <= episode)


def visibility(char: dict, season: int, episode: int, arc: Optional[dict]) -> str:
    """'full' | 'limited' | 'hidden' (see server._visibility)."""
    if char.get("first_season") is None:
        return "full" if arc_shows_presence(arc) else "limited"
    return "full" if passes_shield(char, season, episode, arc=arc) else "hidden"


def pick_arc(db, character_id: str, season: int, episode: int, progress):
    """(arc_row, "start"|"end"): state at the end of the previous episode until
    REVEAL_AT of this one has played."""
    if progress is not None and progress >= REVEAL_AT:
        return db.get_character_arc_at_or_before(character_id, season, episode), "end"
    return db.get_character_arc_at_or_before(character_id, season, episode - 1), "start"


def debuts_in(char: dict, season: int, episode: int) -> bool:
    """True when the character's first appearance is exactly this episode."""
    return char.get("first_season") == season and char.get("first_episode") == episode


def is_met(char: dict, season: int, episode: int, arc: Optional[dict],
           progress: Optional[float]) -> bool:
    """Stricter than `visibility == 'full'` for text features (ask, relationship map):
    a character who first appears in THIS episode counts as met only once
    REVEAL_AT of it has played. /identify keeps the plain rule: a face on
    screen is itself the introduction."""
    if visibility(char, season, episode, arc) != "full":
        return False
    if debuts_in(char, season, episode):
        return progress is not None and progress >= REVEAL_AT
    return True
