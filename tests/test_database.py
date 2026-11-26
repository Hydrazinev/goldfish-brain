import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import CharacterDB


def _make_db():
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db = CharacterDB(db_path=tmp)
    db.add_show("bb", "Breaking Bad", tmdb_id=1396)
    db.add_character(
        character_id="bb-walter", show_id="bb",
        character_name="Walter White", actor_name="Bryan Cranston",
        role="main", first_season=1, first_episode=1,
    )
    return db


def test_arc_at_or_before_prefers_exact_episode():
    db = _make_db()
    db.add_character_arc("bb-walter", 1, 2, status="alive", current_arc="early")
    db.add_character_arc("bb-walter", 1, 5, status="alive", current_arc="exact")
    arc = db.get_character_arc_at_or_before("bb-walter", 1, 5)
    assert arc["current_arc"] == "exact"


def test_arc_at_or_before_falls_back_to_latest_past_row():
    db = _make_db()
    db.add_character_arc("bb-walter", 1, 2, status="alive", current_arc="early")
    db.add_character_arc("bb-walter", 1, 4, status="alive", current_arc="later")
    arc = db.get_character_arc_at_or_before("bb-walter", 1, 5)
    assert arc["current_arc"] == "later"
    assert arc["season"] == 1 and arc["episode"] == 4


def test_arc_at_or_before_never_returns_future_row():
    db = _make_db()
    db.add_character_arc("bb-walter", 5, 16, status="dead",
                         current_arc="SPOILER finale arc")
    arc = db.get_character_arc_at_or_before("bb-walter", 1, 5)
    assert arc is None


def test_derive_first_seen_from_arcs():
    db = _make_db()
    # walter present from S1E1
    db.add_character_arc("bb-walter", 1, 1, status="alive", current_arc="pilot")
    db.add_character_arc("bb-walter", 1, 2, status="alive", current_arc="x")
    # second character: unknown until S2E3
    db.add_character(character_id="bb-late", show_id="bb",
                     character_name="Late Guy")
    db.add_character_arc("bb-late", 1, 1, status="unknown", current_arc=None)
    db.add_character_arc("bb-late", 2, 3, status="alive", current_arc="enters")
    updated = db.derive_first_seen_from_arcs("bb")
    assert updated == 2
    walter = db.get_character("bb-walter")
    late = db.get_character("bb-late")
    assert (walter["first_season"], walter["first_episode"]) == (1, 1)
    assert (late["first_season"], late["first_episode"]) == (2, 3)


def test_update_character_bio():
    db = _make_db()
    db.update_character_bio("bb-walter", "Husband of Skyler; partner of Jesse")
    assert db.get_character("bb-walter")["relationship_summary"].startswith("Husband")


def test_arc_at_or_before_crosses_season_boundary():
    db = _make_db()
    db.add_character_arc("bb-walter", 1, 7, status="alive", current_arc="s1 finale")
    arc = db.get_character_arc_at_or_before("bb-walter", 2, 1)
    assert arc["current_arc"] == "s1 finale"


# ── thread safety (the engine segfaulted inside SQLite) ─────────────────────

def test_each_thread_gets_its_own_connection():
    """One sqlite3 connection shared by FastAPI's worker threads corrupted memory and
    crashed the engine (SIGSEGV in libsqlite3). Every thread must use its own."""
    import threading
    db = _make_db()
    mine = db._conn
    assert db._conn is mine                       # stable within a thread
    seen = []
    t = threading.Thread(target=lambda: seen.append(db._conn))
    t.start(); t.join()
    assert seen[0] is not mine


def test_many_threads_can_read_and_write_at_once():
    import threading
    db = _make_db()
    errors = []

    def work(n):
        try:
            for i in range(60):
                db.get_ready_shows(); db.get_character("bb-walter")
                db.add_character_arc("bb-walter", 1, (n * 60 + i) % 500 + 1, status="alive", current_arc="x")
        except Exception as exc:                  # pragma: no cover - only on failure
            errors.append(repr(exc))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(12)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert errors == []


def test_show_overview_lists_characters_with_notes_flag():
    """The library's 'N characters' list: every character, and whether story notes exist."""
    db = _make_db()                                    # bb-walter (rank unset)
    db.add_character("bb-jesse", "bb", "Jesse Pinkman", actor_name="Aaron Paul", role="main",
                     importance_rank=2)
    db.add_character("bb-gus", "bb", "Gus Fring", actor_name="Giancarlo Esposito", importance_rank=3)
    db.add_character_arc("bb-jesse", 1, 1, status="alive", current_arc="Cooks his first batch.")
    db.add_character_arc("bb-gus", 1, 1, status="unknown", current_arc="")     # empty = no usable notes
    rows = db.get_show_characters_overview("bb")
    by_id = {r["character_id"]: r for r in rows}
    assert by_id["bb-jesse"]["has_notes"] is True
    assert by_id["bb-gus"]["has_notes"] is False and by_id["bb-walter"]["has_notes"] is False
    assert [r["character_id"] for r in rows][:2] == ["bb-jesse", "bb-gus"]       # importance order
    assert set(rows[0]) >= {"character_id", "character_name", "actor_name", "role", "has_notes"}
