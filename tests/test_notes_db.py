import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import CharacterDB


def _db():
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S", tmdb_id=7)
    db.add_character("s-a", "s", "A", actor_name="Ann Actor", first_season=2, first_episode=3)
    return db


def test_old_database_gets_new_columns_and_table():
    p = Path(tempfile.mkdtemp()) / "old.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE shows (show_id TEXT PRIMARY KEY, show_name TEXT NOT NULL, tmdb_id INTEGER,
            total_characters INTEGER DEFAULT 0, ingested_at TIMESTAMP, status TEXT DEFAULT 'ingesting');
        CREATE TABLE characters (character_id TEXT PRIMARY KEY, show_id TEXT NOT NULL, character_name TEXT NOT NULL,
            actor_name TEXT, role TEXT, first_episode INTEGER, last_episode INTEGER, first_season INTEGER,
            last_season INTEGER, importance_rank INTEGER, relationship_summary TEXT, image_count INTEGER DEFAULT 0,
            confidence_level TEXT);
        CREATE TABLE character_arcs (character_id TEXT NOT NULL, season INTEGER NOT NULL, episode INTEGER NOT NULL,
            status TEXT, current_arc TEXT, last_seen_description TEXT, PRIMARY KEY (character_id, season, episode));
        INSERT INTO character_arcs VALUES ('x', 1, 1, 'alive', 'old', NULL);
    """)
    c.commit(); c.close()
    db = CharacterDB(db_path=p)
    assert db.get_character_arc("x", 1, 1)["source"] == "ai"
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(characters)")}
    assert "actor_tmdb_id" in cols
    db.set_cached_credits(5, [{"title": "T"}])
    assert db.get_cached_credits(5)["credits"] == [{"title": "T"}]


def test_credits_cache_roundtrip_and_missing():
    db = _db()
    assert db.get_cached_credits(99) is None
    db.set_cached_credits(99, [{"title": "X", "year": 2001, "kind": "movie"}], fetched_at="2026-01-01T00:00:00+00:00")
    got = db.get_cached_credits(99)
    assert got["credits"][0]["title"] == "X" and got["fetched_at"].startswith("2026-01-01")
    db.set_cached_credits(99, [])
    assert db.get_cached_credits(99)["credits"] == []


def test_actor_id_kept_by_upsert_and_settable():
    db = _db()
    db.add_character("s-a", "s", "A", actor_name="Ann Actor", actor_tmdb_id=42)
    db.add_character("s-a", "s", "A", actor_name="Ann Actor")            # re-ingest without id
    assert db.get_character("s-a")["actor_tmdb_id"] == 42
    db.add_character("s-b", "s", "B")
    db.set_actor_tmdb_id("s-b", 9)
    assert db.get_character("s-b")["actor_tmdb_id"] == 9


def test_delete_character_arcs_keeps_user_rows():
    db = _db()
    db.add_character_arc("s-a", 1, 1, status="alive", current_arc="ai")
    db.replace_user_arcs("s-a", [{"season": 3, "episode": 1, "status": "alive", "arc_type": "other",
                                  "current_arc": "mine", "last_seen_description": ""}])
    db.delete_character_arcs("s-a")
    assert db.get_character_arc("s-a", 1, 1) is None
    assert db.get_character_arc("s-a", 3, 1)["current_arc"] == "mine"


def test_ai_insert_does_not_overwrite_user_row_but_user_replaces_ai():
    db = _db()
    db.replace_user_arcs("s-a", [{"season": 1, "episode": 1, "status": "alive", "arc_type": "loss",
                                  "current_arc": "mine", "last_seen_description": "x"}])
    db.add_character_arc("s-a", 1, 1, status="dead", current_arc="ai says")
    assert db.get_character_arc("s-a", 1, 1)["current_arc"] == "mine"
    db.add_character_arc("s-a", 1, 2, status="alive", current_arc="ai2")
    db.replace_user_arcs("s-a", [{"season": 1, "episode": 2, "status": "alive", "arc_type": "other",
                                  "current_arc": "user2", "last_seen_description": ""}])
    row = db.get_character_arc_at_or_before("s-a", 1, 2)
    assert row["current_arc"] == "user2" and row["source"] == "user"
    assert db.get_character_arc("s-a", 1, 1) is None      # earlier user entry dropped by replace


def test_replace_user_arcs_is_all_or_nothing():
    db = _db()
    db.replace_user_arcs("s-a", [{"season": 1, "episode": 1, "status": "alive", "arc_type": "other",
                                  "current_arc": "keep", "last_seen_description": ""}])
    bad = [{"season": 1, "episode": 2, "status": "alive", "arc_type": "other",
            "current_arc": "new", "last_seen_description": ""},
           {"season": 1, "episode": 2, "status": "alive", "arc_type": "other",       # duplicate PK
            "current_arc": "dup", "last_seen_description": ""}]
    try:
        db.replace_user_arcs("s-a", bad)
    except Exception:
        pass
    assert [r["current_arc"] for r in db.get_user_arcs("s-a")] == ["keep"]


def test_user_arcs_ordered_and_ai_count():
    db = _db()
    for s, e in [(2, 1), (1, 5), (1, 2)]:
        db.replace_user_arcs("s-a", db.get_user_arcs("s-a") + [
            {"season": s, "episode": e, "status": "alive", "arc_type": "other",
             "current_arc": f"{s}.{e}", "last_seen_description": ""}])
    assert [(r["season"], r["episode"]) for r in db.get_user_arcs("s-a")] == [(1, 2), (1, 5), (2, 1)]
    db.add_character_arc("s-a", 4, 4, status="alive", current_arc="ai")
    assert db.count_ai_arcs("s-a") == 1


def test_derive_first_seen_for_character_never_moves_later():
    db = _db()                                               # first seen S02E03
    db.replace_user_arcs("s-a", [{"season": 3, "episode": 1, "status": "alive", "arc_type": "other",
                                  "current_arc": "late", "last_seen_description": ""}])
    db.derive_first_seen_for_character("s-a")
    c = db.get_character("s-a")
    assert (c["first_season"], c["first_episode"]) == (2, 3)
    db.replace_user_arcs("s-a", [{"season": 1, "episode": 4, "status": "alive", "arc_type": "other",
                                  "current_arc": "early", "last_seen_description": ""}])
    db.derive_first_seen_for_character("s-a")
    c = db.get_character("s-a")
    assert (c["first_season"], c["first_episode"]) == (1, 4)


def test_derive_first_seen_sets_when_unknown_and_ignores_unknown_rows():
    db = _db()
    db.add_character("s-n", "s", "N")
    db.replace_user_arcs("s-n", [{"season": 1, "episode": 1, "status": "unknown", "arc_type": "other",
                                  "current_arc": "", "last_seen_description": "scene"},
                                 {"season": 2, "episode": 2, "status": "alive", "arc_type": "other",
                                  "current_arc": "", "last_seen_description": ""}])
    db.derive_first_seen_for_character("s-n")
    c = db.get_character("s-n")
    assert (c["first_season"], c["first_episode"]) == (2, 2)


def test_overview_has_notes_counts_user_entries():
    db = _db()
    db.add_character("s-b", "s", "B")
    assert not any(r["has_notes"] for r in db.get_show_characters_overview("s"))
    db.replace_user_arcs("s-b", [{"season": 1, "episode": 1, "status": "unknown", "arc_type": "other",
                                  "current_arc": "x", "last_seen_description": ""}])
    notes = {r["character_id"]: r["has_notes"] for r in db.get_show_characters_overview("s")}
    assert notes == {"s-a": False, "s-b": True}
