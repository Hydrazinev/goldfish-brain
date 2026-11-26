import base64
import sys
import tempfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import CharacterDB


class FakeRecogniser:
    def detect_faces(self, image):
        return [(None, None, SimpleNamespace(det_score=0.99))]

    def get_embedding(self, image, face):
        return np.zeros(4)


class FakeIndex:
    def __init__(self, cid):
        self.cid = cid

    def search(self, emb, top_k=3):
        return [(self.cid, 0.9)]


def _png() -> str:
    ok, buf = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))
    return base64.b64encode(buf.tobytes()).decode()


@pytest.fixture
def served(monkeypatch):
    import server
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S", tmdb_id=77)
    db.update_show_status("s", "ready")
    monkeypatch.setattr(server, "db", db, raising=False)
    monkeypatch.setattr(server, "recogniser", FakeRecogniser(), raising=False)

    def no_network(*a, **k):
        raise AssertionError("identify must not touch the network")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(server.api_routes, "default_tmdb_get", no_network)

    def call(cid, season=2, episode=1):
        monkeypatch.setitem(server.indexes, "s", FakeIndex(cid))
        r = TestClient(server.app).post("/identify", json={
            "image": _png(), "show_id": "s", "season": season, "episode": episode})
        assert r.status_code == 200
        return r.json()["characters"]
    return SimpleNamespace(db=db, call=call)


def test_full_card_gets_also_in_from_cache(served):
    served.db.add_character("s-a", "s", "A", actor_name="X", actor_tmdb_id=5, first_season=1, first_episode=1)
    served.db.add_character_arc("s-a", 1, 1, status="alive", current_arc="x")
    served.db.set_cached_credits(5, [{"id": 77, "title": "S", "year": 2008, "kind": "series"},
                                     {"id": 1, "title": "Saul", "year": 2015, "kind": "series"},
                                     {"id": 2, "title": "Film", "year": 2006, "kind": "movie"}])
    [card] = served.call("s-a")
    assert card["also_in"] == ["Saul (2015)", "Film (2006)"]


def test_uncached_actor_gets_empty_list_and_no_network(served):
    served.db.add_character("s-a", "s", "A", actor_name="X", actor_tmdb_id=5, first_season=1, first_episode=1)
    served.db.add_character_arc("s-a", 1, 1, status="alive", current_arc="x")
    [card] = served.call("s-a")
    assert card["also_in"] == []


def test_limited_card_also_gets_also_in(served):
    served.db.add_character("s-l", "s", "L", actor_name="X", actor_tmdb_id=6)      # no arcs, no first seen
    served.db.set_cached_credits(6, [{"id": 2, "title": "Film", "year": 2006, "kind": "movie"}])
    [card] = served.call("s-l")
    assert card["limited"] is True and card["also_in"] == ["Film (2006)"]
    assert card["current_arc"] is None


def test_user_note_end_to_end_shows_only_from_its_episode_on(served):
    import server
    served.db.add_character("s-n", "s", "N", actor_name="X")                # no notes at all
    [card] = served.call("s-n")
    assert card["limited"] is True and card["current_arc"] is None
    r = TestClient(server.app).put("/api/characters/s-n/notes", json={"entries": [
        {"season": 2, "episode": 3, "status": "alive", "arc_type": "danger",
         "current_arc": "Mine: hides the money", "last_seen_description": "Garage"}]})
    assert r.status_code == 200

    def at(season, episode, progress=None):
        server.indexes["s"] = FakeIndex("s-n")
        body = {"image": _png(), "show_id": "s", "season": season, "episode": episode, "progress": progress}
        return TestClient(server.app).post("/identify", json=body).json()

    assert at(2, 2, 0.99)["characters"] == []                               # not introduced yet: hidden
    start = at(2, 3, 0.1)["characters"][0]
    assert start["current_arc"] is None and start["as_of"] == "start"       # note describes the END of S02E03
    end = at(2, 3, 0.95)["characters"][0]
    assert end["current_arc"] == "Mine: hides the money" and end["arc_type"] == "danger"
    assert at(2, 4, 0.1)["characters"][0]["current_arc"] == "Mine: hides the money"
