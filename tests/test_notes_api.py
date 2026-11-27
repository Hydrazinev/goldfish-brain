import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from test_api import Env


def entry(season=1, episode=1, **kw):
    return {"season": season, "episode": episode, "status": "alive", "arc_type": "other",
            "current_arc": "A note", "last_seen_description": "A scene", **kw}


def setup():
    e = Env()
    e.ready_show("s", "S")
    e.db.add_character("s-a", "s", "Saul", actor_name="Bob Odenkirk", actor_tmdb_id=5, relationship_summary="Old bio")
    e.db.add_character_arc("s-a", 1, 1, status="alive", current_arc="ai1")
    e.db.add_character_arc("s-a", 1, 2, status="alive", current_arc="ai2")
    e.db.set_cached_credits(5, [{"id": 1, "title": "Better Call Saul", "year": 2015, "kind": "series"}])
    return e


def put(e, body, cid="s-a"):
    return e.client.put(f"/api/characters/{cid}/notes", json=body)


def test_get_returns_user_entries_only_plus_ai_count_and_also_in():
    e = setup()
    e.db.replace_user_arcs("s-a", [{"season": 2, "episode": 3, "status": "dead", "arc_type": "loss",
                                    "current_arc": "mine", "last_seen_description": "s"}])
    j = e.client.get("/api/characters/s-a/notes?all=1").json()
    assert j == {"character_id": "s-a", "name": "Saul", "actor": "Bob Odenkirk",
                 "relationship_summary": "Old bio",
                 "entries": [{"season": 2, "episode": 3, "status": "dead", "arc_type": "loss",
                              "current_arc": "mine", "last_seen_description": "s"}],
                 "also_in": ["Better Call Saul (2015)"], "ai_rows": 2}


def test_get_unknown_character_404():
    r = setup().client.get("/api/characters/nope/notes?all=1")
    assert r.status_code == 404 and r.json()["ok"] is False and r.json()["message"]


def test_put_saves_replaces_and_updates_bio():
    e = setup()
    r = put(e, {"relationship_summary": "Jimmy; Kim", "entries": [entry(1, 1), entry(2, 5, arc_type="danger")]})
    assert r.status_code == 200 and r.json()["ok"] is True
    j = e.client.get("/api/characters/s-a/notes?all=1").json()
    assert [(x["season"], x["episode"]) for x in j["entries"]] == [(1, 1), (2, 5)]
    assert j["relationship_summary"] == "Jimmy; Kim" and j["ai_rows"] == 1      # S1E1 AI row replaced
    put(e, {"entries": [entry(3, 3)]})                                           # replaces; bio untouched
    j = e.client.get("/api/characters/s-a/notes?all=1").json()
    assert [(x["season"], x["episode"]) for x in j["entries"]] == [(3, 3)]
    assert j["relationship_summary"] == "Jimmy; Kim"


def test_put_defaults_and_trimming():
    e = setup()
    put(e, {"entries": [{"season": 1, "episode": 1, "current_arc": "  hi  "}]})
    x = e.client.get("/api/characters/s-a/notes?all=1").json()["entries"][0]
    assert x["arc_type"] == "other" and x["status"] == "unknown" and x["current_arc"] == "hi"


def test_user_note_visible_through_arc_lookup_and_has_notes():
    e = setup()
    e.db.add_character("s-b", "s", "B")
    assert not {c["character_id"]: c["has_notes"]
                for c in e.client.get("/api/shows/s/characters?all=1").json()["characters"]}["s-b"]
    put(e, {"entries": [entry(1, 2, current_arc="user wins")]}, cid="s-b")
    chars = {c["character_id"]: c["has_notes"] for c in e.client.get("/api/shows/s/characters?all=1").json()["characters"]}
    assert chars["s-b"] is True
    assert e.db.get_character_arc_at_or_before("s-b", 1, 9)["current_arc"] == "user wins"
    put(e, {"entries": [entry(1, 2, current_arc="user wins")]})
    assert e.db.get_character_arc_at_or_before("s-a", 1, 2)["current_arc"] == "user wins"


@pytest.mark.parametrize("bad", [
    {"season": 0}, {"season": 51}, {"episode": 0}, {"episode": 501},
    {"status": "zombie"}, {"arc_type": "silly"},
    {"current_arc": "x" * 301}, {"last_seen_description": "x" * 301},
])
def test_put_rejects_bad_entries_with_plain_message_and_saves_nothing(bad):
    e = setup()
    r = put(e, {"relationship_summary": "changed", "entries": [entry(2, 2), {**entry(1, 1), **bad}]})
    assert r.status_code == 400 and r.json()["ok"] is False and r.json()["message"]
    assert "Traceback" not in r.json()["message"]
    j = e.client.get("/api/characters/s-a/notes?all=1").json()
    assert j["entries"] == [] and j["relationship_summary"] == "Old bio"       # atomic: nothing applied


def test_put_rejects_long_bio_too_many_entries_and_duplicates():
    e = setup()
    assert put(e, {"relationship_summary": "x" * 601, "entries": []}).status_code == 400
    assert put(e, {"entries": [entry(1 + i // 50, i % 50 + 1) for i in range(61)]}).status_code == 400
    assert put(e, {"entries": [entry(1, 1), entry(1, 1)]}).status_code == 400
    assert put(e, {"entries": [entry(1, i + 1) for i in range(60)]}).status_code == 200


def test_put_unknown_character_404_and_garbage_body():
    e = setup()
    assert put(e, {"entries": []}, cid="nope").status_code == 404
    assert e.client.put("/api/characters/s-a/notes", json={"entries": "x"}).status_code == 422


def test_put_database_failure_rolls_back_everything(monkeypatch):
    e = setup()
    put(e, {"entries": [entry(1, 1, current_arc="keep")]})
    real = e.db.replace_user_arcs

    def boom(cid, entries, bio=None):
        real(cid, entries + entries, bio)          # duplicate rows: fails inside the transaction
        raise RuntimeError("disk full")
    monkeypatch.setattr(e.db, "replace_user_arcs", boom)
    r = put(e, {"relationship_summary": "new bio", "entries": [entry(4, 4), entry(5, 5)]})
    assert r.status_code == 500 and r.json()["ok"] is False and "disk full" not in r.json()["message"]
    assert e.db.get_character("s-a")["relationship_summary"] == "Old bio"


def test_saving_makes_character_introduced_from_earliest_entry_only():
    e = setup()
    e.db.add_character("s-new", "s", "New")                                      # first seen unknown
    put(e, {"entries": [entry(2, 4), entry(3, 1)]}, cid="s-new")
    c = e.db.get_character("s-new")
    assert (c["first_season"], c["first_episode"]) == (2, 4)
    e.db.update_character_first_seen("s-a", 1, 1)
    put(e, {"entries": [entry(3, 1)]})
    assert e.db.get_character("s-a")["first_season"] == 1                         # not moved later


def test_put_marks_a_changed_bio_as_user_edited():
    e = setup()
    assert e.db.is_bio_user_edited("s-a") is False
    put(e, {"entries": [entry(3, 3)]})                    # no bio sent: not marked
    assert e.db.is_bio_user_edited("s-a") is False
    put(e, {"relationship_summary": "My words", "entries": []})
    assert e.db.is_bio_user_edited("s-a") is True


def test_get_notes_redacts_hidden_character_names_unless_all():
    e = Env()
    e.ready_show("s", "S")
    e.db.add_character("s-a", "s", "Walter White", first_season=1, first_episode=1,
                       relationship_summary="Teacher turned partner of Gus Fring; father of Flynn")
    e.db.add_character("s-g", "s", "Gus Fring", first_season=3, first_episode=1)
    e.db.replace_user_arcs("s-a", [{"season": 1, "episode": 1, "status": "alive", "arc_type": "other",
                                    "current_arc": "Meets Gus Fring at the lab. Cooks.",
                                    "last_seen_description": "Home."}])
    j = e.client.get("/api/characters/s-a/notes").json()
    assert "Gus" not in j["relationship_summary"] and "Gus" not in str(j["entries"])
    assert "Cooks." in j["entries"][0]["current_arc"]
    full = e.client.get("/api/characters/s-a/notes?all=1").json()
    assert "Gus Fring" in full["relationship_summary"] and "Gus Fring" in full["entries"][0]["current_arc"]
