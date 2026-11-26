#!/usr/bin/env python3
"""Verify all improvements made so far. Costs nothing (no OpenAI calls).

Run:  venv/bin/python test_improvements.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

PASS = "\033[32m✓ PASS\033[0m"
FAIL = "\033[31m✗ FAIL\033[0m"
SKIP = "\033[33m– SKIP\033[0m"

results: list[tuple[str, bool | None]] = []


def check(label: str, ok: bool | None, detail: str = "") -> None:
    mark = SKIP if ok is None else (PASS if ok else FAIL)
    print(f"  {mark}  {label}" + (f"  ({detail})" if detail else ""))
    results.append((label, ok))


# ── 1. Unit tests (buddy cooldown fix, spoiler-shield queries, db helpers) ──
print("\n[1] Unit test suite")
r = subprocess.run(
    [str(ROOT / "venv" / "bin" / "pytest"), "tests/", "-q"],
    capture_output=True, text=True, cwd=ROOT,
)
last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[-80:]
check("pytest tests/", r.returncode == 0, last)

# ── 2. Database completeness (pipeline fix + total_characters fix) ──────────
print("\n[2] Database completeness — bios, first_seen, total_characters")
from database import CharacterDB  # noqa: E402

db = CharacterDB(ROOT / "data" / "cineid.db")
shows = db.get_all_shows()
check("at least one show ingested", len(shows) > 0, f"{len(shows)} shows")
for s in shows:
    chars = db.get_show_characters(s["show_id"])
    n = len(chars)
    bios = sum(1 for c in chars if (c.get("relationship_summary") or "").strip())
    fseen = sum(1 for c in chars if c.get("first_season") is not None)
    check(f"{s['show_id']}: total_characters matches", s["total_characters"] == n,
          f"shows table says {s['total_characters']}, actual {n}")
    check(f"{s['show_id']}: all bios present", bios == n, f"{bios}/{n}")
    check(f"{s['show_id']}: all first_seen set", fseen == n, f"{fseen}/{n}")

# ── 3. Spoiler shield — arc lookups never return future data ────────────────
print("\n[3] Spoiler shield — get_character_arc_at_or_before")
leaks = 0
checked = 0
for s in shows:
    for c in db.get_show_characters(s["show_id"])[:5]:  # top 5 per show
        arc = db.get_character_arc_at_or_before(c["character_id"], 1, 2)
        checked += 1
        if arc and (arc["season"], arc["episode"]) > (1, 2):
            leaks += 1
            print(f"      LEAK: {c['character_id']} returned S{arc['season']}E{arc['episode']} at S1E2")
check("no future arc rows returned at S01E02", leaks == 0,
      f"{checked} characters probed across {len(shows)} shows")

# late-arrival character must return None before their first appearance
late = [c for s in shows for c in db.get_show_characters(s["show_id"])
        if (c.get("first_season") or 1) >= 2]
if late:
    c = late[0]
    arc = db.get_character_arc_at_or_before(c["character_id"], 1, 1)
    check(f"late arrival hidden at S01E01 ({c['character_name']}, "
          f"first seen S{c['first_season']:02d})", arc is None or
          (arc["season"], arc["episode"]) <= (1, 1))
else:
    check("late-arrival character probe", None, "no character first appears after season 1")

# ── 4. Watch Buddy cost floors ──────────────────────────────────────────────
print("\n[4] Watch Buddy cooldown floors (cost-drain fix)")
from buddy import _effective_cooldown  # noqa: E402

check("cooldown 0 + timer mode floors at 3 min",
      _effective_cooldown(0, "timer") >= 180,
      f"{_effective_cooldown(0, 'timer')}s")
check("cooldown 0 + audio mode floors at 10s",
      _effective_cooldown(0, "audio") >= 10,
      f"{_effective_cooldown(0, 'audio')}s")
check("explicit cooldown respected",
      _effective_cooldown(5, "timer") == 300,
      f"{_effective_cooldown(5, 'timer')}s")

# ── 5. Live server check (optional — only if server is running) ─────────────
print("\n[5] Live server (start it first to include this check)")
try:
    with urllib.request.urlopen("http://localhost:8000/health", timeout=2) as resp:
        health = json.loads(resp.read())
    check("/health responds", True, str(health)[:60])
except Exception:
    check("/health responds", None, "server not running — venv/bin/python src/server.py")

# ── 6. Git protection ───────────────────────────────────────────────────────
print("\n[6] Git hygiene")
tracked = subprocess.run(["git", "ls-files", ".env"],
                         capture_output=True, text=True, cwd=ROOT).stdout.strip()
check(".env is NOT committed", tracked == "")
n_commits = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True, cwd=ROOT).stdout.strip()
check("repo has history", int(n_commits or 0) >= 1, f"{n_commits} commits")

# ── summary ─────────────────────────────────────────────────────────────────
db.close()
failed = [label for label, ok in results if ok is False]
skipped = sum(1 for _, ok in results if ok is None)
passed = sum(1 for _, ok in results if ok)
print(f"\n{'═' * 60}")
print(f"  {passed} passed · {len(failed)} failed · {skipped} skipped")
if failed:
    print("  Failed:")
    for f in failed:
        print(f"    ✗ {f}")
    sys.exit(1)
print("  All automated checks pass ✅")
print(f"{'═' * 60}")
