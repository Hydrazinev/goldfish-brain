"""
Populate character metadata using Claude (Haiku 5.5, the cheapest model).

Single-episode mode (default):
    python src/populate_metadata.py
    → updates relationship_summary on characters table for current config episode

All-episodes mode:
    python src/populate_metadata.py --all-episodes
    → fetches episode list from TMDB, then generates per-episode arc data
      (status, current_arc, last_seen_description) for every character × episode
      and stores in the character_arcs table (~20 API calls total)

Reads ANTHROPIC_API_KEY and TMDB_API_KEY from .env or environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
import llm

sys.path.insert(0, str(Path(__file__).parent))
from database import CharacterDB
from job_events import emit

_ROOT    = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "cineid.db"
_CONFIG  = _ROOT / "data" / "config.json"

# When launched by the app (CINEID_EVENTS=1) the keys it passes in win over .env
load_dotenv(_ROOT / ".env", override=not os.environ.get("CINEID_EVENTS"))


def _load_config() -> dict:
    if _CONFIG.exists():
        try:
            return json.loads(_CONFIG.read_text())
        except Exception:
            pass
    return {"show_id": "breaking-bad", "season": 1, "episode": 1}


# ── TMDB helpers ──────────────────────────────────────────────────────────────

def _tmdb_get(path: str, tmdb_key: str, **params) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in {**params, "api_key": tmdb_key}.items())
    url = f"https://api.themoviedb.org/3{path}?{qs}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _get_episode_guide(tmdb_id: int, tmdb_key: str) -> list[dict]:
    """Every regular episode with TMDB's title and one-paragraph summary:
    [{season, episode, name, overview}]. Real facts to ground the notes in."""
    show = _tmdb_get(f"/tv/{tmdb_id}", tmdb_key)
    guide = []
    for s in show.get("seasons", []):
        sn = s["season_number"]
        if sn == 0:
            continue  # skip specials
        season_data = _tmdb_get(f"/tv/{tmdb_id}/season/{sn}", tmdb_key)
        for ep in season_data.get("episodes", []):
            guide.append({"season": sn, "episode": ep["episode_number"],
                          "name": ep.get("name") or "", "overview": ep.get("overview") or ""})
        time.sleep(0.25)
    return guide


_GUIDE_MAX_CHARS = 20000
_GUIDE_OVERVIEW_CHARS = 220


def _format_guide(guide: list[dict]) -> str:
    """'S01E04 "Title": summary' per line, summaries and total length capped."""
    lines, total = [], 0
    for g in guide:
        summary = (g.get("overview") or "").strip().replace("\n", " ")[:_GUIDE_OVERVIEW_CHARS]
        line = f'S{g["season"]:02d}E{g["episode"]:02d} "{g.get("name", "")}": {summary}'.rstrip(": ")
        if total + len(line) + 1 > _GUIDE_MAX_CHARS:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _get_all_episodes(tmdb_id: int, tmdb_key: str) -> list[tuple[int, int]]:
    """Return (season, episode) pairs for every regular episode of the show."""
    show = _tmdb_get(f"/tv/{tmdb_id}", tmdb_key)
    episodes = []
    for s in show.get("seasons", []):
        sn = s["season_number"]
        if sn == 0:
            continue  # skip specials
        season_data = _tmdb_get(f"/tv/{tmdb_id}/season/{sn}", tmdb_key)
        for ep in season_data.get("episodes", []):
            episodes.append((sn, ep["episode_number"]))
        time.sleep(0.25)
    return episodes


# ── Claude helpers ────────────────────────────────────────────────────────────

def _generate_single(client, show: str, name: str, actor: str,
                     role: str, season: int, episode: int) -> dict:
    prompt = f"""TV show expert. Generate overlay metadata for a face-recognition app.

Show: {show}
Character: {name}  |  Played by: {actor or "unknown"}  |  Role: {role or "unknown"}
Watch position: S{season:02d}E{episode:02d}

Return ONLY a JSON object:
{{
  "status": "alive" | "dead" | "unknown",
  "arc_type": "danger" | "conflict" | "alliance" | "discovery" | "deception" | "loss" | "turning_point" | "other",
  "relationship_summary": "2–4 key relationships; semicolon-separated short phrases",
  "current_arc": "One punchy sentence naming the SPECIFIC plot event or conflict {name} is in RIGHT NOW. Use active voice and name the actual situation (e.g. 'Has just discovered Gus is cartel and is racing to warn Walt'). NEVER use vague phrases like 'navigating challenges', 'dealing with consequences', or 'trying to manage'.",
  "last_seen_description": "One sentence naming a specific action, decision, or line of dialogue. Start with what {name} DID or SAID, not 'is seen' or 'appears to be'."
}}
No spoilers beyond S{season:02d}E{episode:02d}."""

    return llm.ask_json(client, prompt, max_tokens=1500)


_ARC_SCHEMA = {
    "type": "object",
    "properties": {
        "relationship_summary": {"type": "string"},
        "first_appearance": {"type": "string"},
        "episodes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "status": {"type": "string"},
                "arc_type": {"type": "string"},
                "current_arc": {"type": "string"},
                "last_seen_description": {"type": "string"},
            },
            "required": ["code", "status", "arc_type", "current_arc", "last_seen_description"],
            "additionalProperties": False,
        }},
    },
    "required": ["relationship_summary", "first_appearance", "episodes"],
    "additionalProperties": False,
}

_BIO_SCHEMA = {
    "type": "object",
    "properties": {"relationship_summary": {"type": "string"}},
    "required": ["relationship_summary"],
    "additionalProperties": False,
}


def _arc_prompt(show: str, name: str, actor: str, role: str,
                episodes: list[tuple[int, int]], show_overview: str = "",
                guide: "list[dict] | None" = None) -> str:
    ep_list = ", ".join(f"S{s:02d}E{e:02d}" for s, e in episodes)
    about = f"\nAbout the show (from TMDB): {show_overview}\n" if show_overview else ""
    if guide:
        about += (f"\nEpisode guide (real episode titles and summaries from TMDB — base your notes on these; "
                  f"do not invent events they don't support):\n{_format_guide(guide)}\n")
    return f"""TV show expert. For {name} ({actor or "unknown"}) in {show}, write SPECIFIC story data at each episode.{about}
Return a JSON object with:
- "relationship_summary": 2-4 key relationships for {name}; semicolon-separated short phrases (e.g. 'Husband of X; partner of Y'). Only relationships established when the character is introduced — no late-series reveals, no deaths.
- "first_appearance": "SxxEyy" — the FIRST episode {name} appears in ("" if you are not sure).
- "episodes": an array with one entry per episode from first_appearance to the end of the list below, each {{"code": "S01E05", "status": "alive"|"dead"|"unknown", "arc_type": "danger"|"conflict"|"alliance"|"discovery"|"deception"|"loss"|"turning_point"|"other", "current_arc": ..., "last_seen_description": ...}}.
  - current_arc: One punchy sentence naming the SPECIFIC plot event or conflict {name} is involved in RIGHT NOW at that episode. Active voice. e.g. 'Has just poisoned Brock to manipulate Jesse and is scrambling to cover his tracks.' NEVER generic summaries like 'is navigating challenges'.
  - last_seen_description: One sentence naming a specific action {name} took, decision made, or line spoken. Start with what they DID or SAID.

Rules:
- Do not invent events. Use the episode guide above where there is one. If you are not confident about this show or this character, use status "unknown" and "" for current_arc and last_seen_description — an empty answer is better than an invented one.
- Omit all episodes before first_appearance (no "unknown" placeholder entries).
- No spoilers beyond each episode's own point in the timeline.
- Each text field under 120 characters.

Episodes: {ep_list}"""


def _normalise_episodes(data: dict) -> dict:
    """Structured output gives episodes as an array of {code,...}; the rest of
    this script wants {code: meta}. Accepts the old dict shape too."""
    eps = data.get("episodes", {})
    if isinstance(eps, list):
        out = {}
        for item in eps:
            if not isinstance(item, dict):
                continue
            parsed = _parse_ep_code(str(item.get("code", "")))
            if not parsed:
                continue
            meta = {k: v for k, v in item.items() if k != "code"}
            out[f"S{parsed[0]:02d}E{parsed[1]:02d}"] = meta
        eps = out
    elif not isinstance(eps, dict):
        eps = {}
    return {**data, "episodes": eps}


def _generate_all_episodes(client, show: str, name: str, actor: str,
                            role: str, episodes: list[tuple[int, int]],
                            show_overview: str = "", guide: "list[dict] | None" = None) -> dict:
    # 62-episode shows truncated at 8000 tokens and produced all-"unknown" rows;
    # a cut-off answer now raises instead of being half-parsed.
    data = llm.ask_json(client, _arc_prompt(show, name, actor, role, episodes, show_overview, guide),
                        max_tokens=16000, schema=_ARC_SCHEMA)
    return _normalise_episodes(data)


def _parse_ep_code(code: str) -> "tuple[int, int] | None":
    """'S02E08' → (2, 8); None if malformed."""
    code = (code or "").strip().upper()
    if not (code.startswith("S") and "E" in code):
        return None
    try:
        sn, ep = code[1:].split("E")
        return int(sn), int(ep)
    except ValueError:
        return None


def _generate_bio(client, show: str, name: str, actor: str, role: str,
                  show_overview: str = "") -> str:
    about = f"\nAbout the show (from TMDB): {show_overview}\n" if show_overview else ""
    prompt = f"""TV show expert. For {name} ({actor or "unknown"}, {role or "unknown"} role) in {show}:{about}
Return a JSON object {{"relationship_summary": ...}}: 2-4 key relationships; semicolon-separated short phrases (e.g. 'Husband of X; partner of Y'). Only relationships established when the character is introduced — no late-series reveals, no deaths.
Do not guess: if you are not confident about this show or character, use "" instead of inventing relationships."""
    return llm.ask_json(client, prompt, max_tokens=500, schema=_BIO_SCHEMA).get("relationship_summary", "")


def _show_overview(tmdb_id, tmdb_key: str) -> str:
    """TMDB's one-paragraph show summary, to ground the model on shows it barely knows."""
    if not tmdb_id or not tmdb_key:
        return ""
    try:
        return (_tmdb_get(f"/tv/{tmdb_id}", tmdb_key).get("overview") or "")[:600]
    except Exception:
        return ""


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show",         default=None)
    parser.add_argument("--season",       type=int, default=None)
    parser.add_argument("--episode",      type=int, default=None)
    parser.add_argument("--all-episodes", action="store_true",
                        help="Pre-generate arc data for every episode (stores in character_arcs table)")
    parser.add_argument("--bios-only", action="store_true",
                        help="Generate relationship_summary only for characters missing one")
    parser.add_argument("--missing-arcs-only", action="store_true",
                        help="With --all-episodes: only regenerate characters whose arc "
                             "rows are all 'unknown' (failed/truncated earlier runs)")
    args = parser.parse_args()

    cfg     = _load_config()
    show_id = args.show    or cfg["show_id"]
    season  = args.season  or cfg["season"]
    episode = args.episode or cfg["episode"]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set (add it in CineID → Settings, or .env)")
        sys.exit(1)

    client = llm.make_client(api_key)
    try:                                  # fail once, clearly, instead of once per character
        llm.ask(client, "Reply with the single word OK.", max_tokens=16)
    except llm.LLMError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    db     = CharacterDB(db_path=_DB_PATH)

    if not db.show_exists(show_id):
        print(f"Show '{show_id}' not in database.")
        sys.exit(1)

    show_row   = next(s for s in db.get_all_shows() if s["show_id"] == show_id)
    show_name  = show_row["show_name"]
    tmdb_id    = show_row["tmdb_id"]
    characters = db.get_show_characters(show_id)
    overview   = _show_overview(tmdb_id, os.environ.get("TMDB_API_KEY", ""))

    # ── bios-only mode ───────────────────────────────────────────────────────
    if args.bios_only:
        missing = [c for c in characters
                   if not (c.get("relationship_summary") or "").strip()]
        print(f"{len(missing)} of {len(characters)} characters in '{show_name}' missing bios")
        ok = fail = 0
        for char in missing:
            name = char["character_name"]
            print(f"  {name} …", end=" ", flush=True)
            try:
                bio = _generate_bio(client, show_name, name,
                                    char.get("actor_name") or "",
                                    char.get("role") or "", overview)
                if bio:
                    db.update_character_bio(char["character_id"], bio)
                    print("done")
                    ok += 1
                else:
                    print("EMPTY response — skipped")
                    fail += 1
            except Exception as exc:
                print(f"FAILED — {exc}")
                fail += 1
        db.close()
        print(f"\nDone. {ok} bios written, {fail} failed.")
        return

    # ── all-episodes mode ────────────────────────────────────────────────────
    if args.all_episodes:
        tmdb_key = os.environ.get("TMDB_API_KEY")
        if not tmdb_key:
            print("Error: TMDB_API_KEY not set in .env (needed to fetch episode list)")
            sys.exit(1)

        if args.missing_arcs_only:
            def _has_usable_arc(cid: str) -> bool:
                return db._conn.execute(
                    "SELECT 1 FROM character_arcs WHERE character_id=? "
                    "AND (LOWER(COALESCE(status,'unknown'))!='unknown' "
                    "     OR COALESCE(current_arc,'')!='') LIMIT 1",
                    (cid,),
                ).fetchone() is not None
            characters = [c for c in characters
                          if not _has_usable_arc(c["character_id"])]
            print(f"--missing-arcs-only: {len(characters)} characters need regeneration")
            if not characters:
                db.close()
                print("Nothing to do.")
                return

        print(f"Fetching episode guide for '{show_name}' from TMDB …")
        guide = _get_episode_guide(tmdb_id, tmdb_key)
        episodes = [(g["season"], g["episode"]) for g in guide]
        print(f"Found {len(episodes)} episodes across {episodes[-1][0]} seasons")
        print(f"Generating arc data for {len(characters)} characters × {len(episodes)} episodes …")
        print(f"(~{len(characters)} API calls — one per character)\n")

        ok = fail = 0
        for idx, char in enumerate(characters):
            name = char["character_name"]
            emit("notes", current=idx, total=len(characters), name=name)
            print(f"  {name} …", end=" ", flush=True)
            try:
                data = _generate_all_episodes(
                    client, show_name, name,
                    char.get("actor_name") or "",
                    char.get("role") or "",
                    episodes, overview, guide,
                )
                # New shape nests arcs under "episodes" and adds bio +
                # first_appearance; tolerate the old flat shape too.
                bio = (data.get("relationship_summary") or "").strip()
                if bio:
                    db.update_character_bio(char["character_id"], bio)
                first = _parse_ep_code(str(data.get("first_appearance") or ""))
                if first:
                    db.update_character_first_seen(char["character_id"], *first)
                ep_data = data.get("episodes", data)
                # Fresh slate: stale all-"unknown" rows from failed runs would
                # otherwise shadow the regenerated data.
                db.delete_character_arcs(char["character_id"])
                saved = 0
                for ep_key, meta in ep_data.items():
                    if not isinstance(meta, dict):
                        continue
                    parsed = _parse_ep_code(ep_key)
                    if not parsed:
                        continue
                    sn, ep = parsed
                    db.add_character_arc(
                        char["character_id"], sn, ep,
                        status=meta.get("status"),
                        current_arc=meta.get("current_arc") or None,
                        last_seen_description=meta.get("last_seen_description") or None,
                        arc_type=meta.get("arc_type") or None,
                    )
                    saved += 1
                first_str = f"S{first[0]:02d}E{first[1]:02d}" if first else "?"
                print(f"done ({saved} eps, first seen {first_str}{', bio' if bio else ''})")
                ok += 1
            except Exception as exc:
                print(f"FAILED — {exc}")
                fail += 1

        # Spoiler shield depends on first_season/first_episode — derive them
        # from the arc rows (earliest episode where the character is present).
        seen = db.derive_first_seen_from_arcs(show_id)
        print(f"\nfirst_seen derived for {seen} characters in {show_id}")

        # Completeness summary — runs in both CLI and in-app wizard paths
        total = db._conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id=?", (show_id,)).fetchone()[0]
        bios = db._conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id=? "
            "AND COALESCE(relationship_summary,'')!=''", (show_id,)).fetchone()[0]
        fs = db._conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id=? "
            "AND first_season IS NOT NULL", (show_id,)).fetchone()[0]
        usable = db._conn.execute(
            "SELECT COUNT(DISTINCT c.character_id) FROM characters c "
            "JOIN character_arcs ca ON ca.character_id=c.character_id "
            "WHERE c.show_id=? AND (LOWER(COALESCE(ca.status,'unknown'))!='unknown' "
            "OR COALESCE(ca.current_arc,'')!='')", (show_id,)).fetchone()[0]
        print(f"Completeness: bios {bios}/{total} · first_seen {fs}/{total} · usable arcs {usable}/{total}")
        emit("notes_done", ok=ok, failed=fail, usable=usable, total=total)
        if bios < total:
            print(f"  ⚠ repair: populate_metadata.py --show {show_id} --bios-only")
        if usable < total:
            print(f"  ⚠ repair: populate_metadata.py --show {show_id} --all-episodes --missing-arcs-only")

        db.close()
        print(f"Done. {ok} characters updated, {fail} failed.")
        print("Arc data is now stored per-episode — the overlay will use the right context automatically.")
        return

    # ── single-episode mode ──────────────────────────────────────────────────
    print(f"Populating {len(characters)} characters in '{show_name}' — S{season:02d}E{episode:02d}")
    print()

    ok = fail = 0
    for char in characters:
        name = char["character_name"]
        print(f"  {name} …", end=" ", flush=True)
        try:
            meta = _generate_single(
                client, show_name, name,
                char.get("actor_name") or "",
                char.get("role") or "",
                season, episode,
            )
            db._conn.execute(
                """UPDATE characters
                   SET status=?, current_arc=?, relationship_summary=?,
                       last_seen_description=?, arc_type=?
                   WHERE character_id=?""",
                (
                    meta.get("status"),
                    meta.get("current_arc"),
                    meta.get("relationship_summary"),
                    meta.get("last_seen_description"),
                    meta.get("arc_type"),
                    char["character_id"],
                ),
            )
            db._conn.commit()
            print("done")
            ok += 1
        except Exception as exc:
            print(f"FAILED — {exc}")
            fail += 1

    db.close()
    print(f"\nDone. {ok} updated, {fail} failed.")


if __name__ == "__main__":
    main()
