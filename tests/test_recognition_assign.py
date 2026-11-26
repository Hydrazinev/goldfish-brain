"""One-to-one face -> character assignment in /identify (no model needed)."""
import base64
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import CharacterDB


def test_assign_two_faces_same_best_character_falls_back():
    import server
    out = server._assign_one_to_one([
        [("walt", 0.9), ("jesse", 0.55)],
        [("walt", 0.7), ("jesse", 0.6)],
    ])
    assert out == [("walt", 0.9), ("jesse", 0.6)]


def test_assign_no_hits_and_empty():
    import server
    assert server._assign_one_to_one([]) == []
    assert server._assign_one_to_one([[], [("a", 0.8)]]) == [None, ("a", 0.8)]


def test_assign_never_reuses_a_character():
    import server
    out = server._assign_one_to_one([[("a", 0.9)], [("a", 0.8)]])
    assert out == [("a", 0.9), None]


class TwoFaceRecogniser:
    def detect_faces(self, image):
        return [(None, None, SimpleNamespace(det_score=0.99, k=0)),
                (None, None, SimpleNamespace(det_score=0.99, k=1))]

    def get_embedding(self, image, face):
        return np.array([face.k])


class ScriptedIndex:
    def search(self, emb, top_k=3):
        return {0: [("s-a", 0.9), ("s-b", 0.6)], 1: [("s-a", 0.7), ("s-b", 0.55)]}[int(emb[0])]


def test_identify_returns_both_characters(monkeypatch):
    import server
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S")
    for cid in ("s-a", "s-b"):
        db.add_character(cid, "s", cid, actor_name="X", first_season=1, first_episode=1)
        db.add_character_arc(cid, 1, 1, status="alive", current_arc="x")
    monkeypatch.setattr(server, "db", db, raising=False)
    monkeypatch.setattr(server, "recogniser", TwoFaceRecogniser(), raising=False)
    monkeypatch.setitem(server.indexes, "s", ScriptedIndex())
    ok, buf = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))
    r = TestClient(server.app).post("/identify", json={
        "image": base64.b64encode(buf.tobytes()).decode(), "show_id": "s", "season": 2, "episode": 1})
    assert r.status_code == 200
    ids = [c["character_id"] for c in r.json()["characters"]]
    assert sorted(ids) == ["s-a", "s-b"]
