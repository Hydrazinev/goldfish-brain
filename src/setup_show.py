#!/usr/bin/env python3
"""setup_show.py — one-command setup for a new show.

Usage
-----
  # Interactive (search by name):
  venv/bin/python src/setup_show.py

  # Non-interactive (name provided):
  venv/bin/python src/setup_show.py "Better Call Saul"

  # Skip the populate step (just ingest):
  venv/bin/python src/setup_show.py "Breaking Bad" --no-populate

What it does
------------
1. Reads TMDB_API_KEY from .env
2. Searches TMDB for the show name and lets you pick the right one
3. Runs  src/ingest.py   --show  "<name>"  --tmdb-id <id>
4. Runs  src/populate_metadata.py  --all-episodes
5. Updates data/config.json with season=1 episode=1 for the new show

The two long-running steps print progress live so you can see what's happening.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_ENV  = _ROOT / ".env"
_CFG  = _ROOT / "data" / "config.json"


# ── helpers ──────────────────────────────────────────────────────────────────

def _read_tmdb_key() -> str:
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            if line.startswith("TMDB_API_KEY="):
                key = line.split("=", 1)[1].strip()
                if key:
                    return key
    key = os.environ.get("TMDB_API_KEY", "")
    if key:
        return key
    sys.exit(
        "[setup] ✗  TMDB_API_KEY not found.\n"
        "       Add it to your .env file:  TMDB_API_KEY=<your_key>"
    )


def _tmdb_search(name: str, api_key: str) -> list[dict]:
    """Return up to 5 TV show results from TMDB search."""
    q = urllib.parse.urlencode({"api_key": api_key, "query": name, "language": "en-US"})
    url = f"https://api.themoviedb.org/3/search/tv?{q}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CineID/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        return data.get("results", [])[:5]
    except Exception as exc:
        sys.exit(f"[setup] ✗  TMDB search failed: {exc}")


def _pick_show(results: list[dict], query: str) -> dict:
    """Let the user pick from search results, or auto-pick if only one."""
    if not results:
        sys.exit(f"[setup] ✗  No TMDB results for {query!r}. Try a different spelling.")

    if len(results) == 1:
        r = results[0]
        print(f"[setup] Found: {r['name']}  (TMDB #{r['id']},  {r.get('first_air_date','?')[:4]})")
        return r

    print("\n[setup] Multiple results — pick one:\n")
    for i, r in enumerate(results, 1):
        year  = r.get("first_air_date", "????")[:4]
        votes = r.get("vote_count", 0)
        print(f"  {i}.  {r['name']}  ({year})  — {votes} ratings  [TMDB #{r['id']}]")
    print()

    while True:
        try:
            choice = int(input("Enter number: ").strip())
            if 1 <= choice <= len(results):
                return results[choice - 1]
        except (ValueError, KeyboardInterrupt):
            pass
        print(f"Please enter a number between 1 and {len(results)}.")


def _run(cmd: list[str], label: str) -> None:
    """Run a subprocess, streaming its output.  Exit on failure."""
    print(f"\n{'─'*60}")
    print(f"[setup] ▶  {label}")
    print(f"{'─'*60}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"\n[setup] ✗  {label} failed (exit {result.returncode}).")
    print(f"[setup] ✓  {label} done.\n")


def _slug(name: str) -> str:
    """Convert a show name to a show_id slug (same convention as ingest.py)."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _report_completeness(show_id: str) -> None:
    """Print a data-completeness summary for a show; warn loudly on gaps."""
    import sqlite3
    db_path = _ROOT / "data" / "cineid.db"
    with sqlite3.connect(str(db_path)) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id=?", (show_id,)).fetchone()[0]
        bios = conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id=? "
            "AND COALESCE(relationship_summary,'') != ''", (show_id,)).fetchone()[0]
        first_seen = conn.execute(
            "SELECT COUNT(*) FROM characters WHERE show_id=? "
            "AND first_season IS NOT NULL", (show_id,)).fetchone()[0]
        arcs, eps = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT season||'-'||episode) "
            "FROM character_arcs ca JOIN characters c ON ca.character_id=c.character_id "
            "WHERE c.show_id=?", (show_id,)).fetchone()
        usable = conn.execute(
            "SELECT COUNT(DISTINCT c.character_id) FROM characters c "
            "JOIN character_arcs ca ON ca.character_id=c.character_id "
            "WHERE c.show_id=? AND (LOWER(COALESCE(ca.status,'unknown'))!='unknown' "
            "OR COALESCE(ca.current_arc,'')!='')", (show_id,)).fetchone()[0]

    print(f"\n[setup] Data completeness for {show_id!r}:")
    print(f"[setup]   characters : {total}")
    print(f"[setup]   bios       : {bios}/{total}")
    print(f"[setup]   first_seen : {first_seen}/{total}")
    print(f"[setup]   arc rows   : {arcs} across {eps} episodes")
    print(f"[setup]   usable arcs: {usable}/{total} characters")
    warnings = []
    if total == 0:
        warnings.append("no characters ingested")
    if 0 < usable < total:
        warnings.append(f"{total - usable} characters have only 'unknown' arc rows "
                        f"(failed generation) — run: populate_metadata.py "
                        f"--show {show_id} --all-episodes --missing-arcs-only")
    if bios < total:
        warnings.append(f"{total - bios} characters missing bios "
                        f"(BIOGRAPHY section will be empty) — run: "
                        f"populate_metadata.py --show {show_id} --bios-only")
    if first_seen < total:
        warnings.append(f"{total - first_seen} characters missing first_seen "
                        f"(spoiler shield cannot protect them)")
    if arcs == 0:
        warnings.append("no per-episode arc data — overlay will show no story context")
    for w in warnings:
        print(f"[setup] ⚠  {w}")
    if not warnings:
        print("[setup] ✓  all data complete")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="One-command setup for a new CineID show.")
    parser.add_argument("show_name", nargs="?", help="Show name to search for (optional; prompts if omitted)")
    parser.add_argument("--no-populate", action="store_true", help="Skip the populate_metadata step")
    args = parser.parse_args()

    api_key = _read_tmdb_key()

    # Get show name
    name = args.show_name
    if not name:
        try:
            name = input("[setup] Enter show name: ").strip()
        except KeyboardInterrupt:
            sys.exit("\nCancelled.")
    if not name:
        sys.exit("[setup] ✗  Show name cannot be empty.")

    # Search TMDB
    print(f"[setup] Searching TMDB for {name!r} …")
    results = _tmdb_search(name, api_key)
    show    = _pick_show(results, name)

    tmdb_id   = show["id"]
    show_name = show["name"]
    show_id   = _slug(show_name)
    year      = show.get("first_air_date", "?")[:4]

    print(f"\n[setup] Selected: {show_name!r}  (TMDB #{tmdb_id},  {year})")
    print(f"[setup] show_id will be: {show_id!r}")

    # Confirm
    try:
        yn = input("[setup] Proceed? [Y/n]: ").strip().lower()
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")
    if yn not in ("", "y", "yes"):
        sys.exit("Cancelled.")

    python = str(_ROOT / "venv" / "bin" / "python")

    # Step 1 — ingest
    _run(
        [python, str(_ROOT / "src" / "ingest.py"),
         "--show", show_name, "--tmdb-id", str(tmdb_id)],
        f"Ingest cast for {show_name!r}",
    )

    # Step 2 — populate metadata (pass --show explicitly: config.json still
    # points at the PREVIOUS show until step 3, so omitting it would generate
    # arcs for the wrong show)
    if not args.no_populate:
        _run(
            [python, str(_ROOT / "src" / "populate_metadata.py"),
             "--show", show_id, "--all-episodes"],
            f"Populate per-episode arcs for {show_name!r}",
        )
    else:
        print("[setup] Skipping populate_metadata (--no-populate).")

    # Step 2.5 — completeness report: loud warnings instead of silent gaps
    _report_completeness(show_id)

    # Step 3 — update config.json
    import config_store
    try:
        config_store.update_config(
            _CFG, lambda cfg: cfg.update({"show_id": show_id, "season": 1, "episode": 1}))
    except config_store.ConfigUnreadable:
        print(f"[setup] !  {_CFG} is unreadable, so it was left untouched. "
              f"Fix or delete it, then select {show_id!r} in the app.")
        return
    print(f"[setup] ✓  config.json updated  →  show_id={show_id!r}  S01E01")

    print(f"\n{'═'*60}")
    print(f"[setup] ✅  {show_name!r} is ready!")
    print(f"[setup]    Launch CineID and start watching.")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
