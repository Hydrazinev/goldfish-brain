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


class OneFaceRecogniser:
    def detect_faces(self, image):
        return [(None, None, SimpleNamespace(det_score=0.99, k=0))]

    def get_embedding(self, image, face):
        return np.array([0])


class FixedIndex:
    def __init__(self, matches):
        self.matches = matches

    def search(self, emb, top_k=3):
        return self.matches


def _identify(monkeypatch, matches, first_ep=1, possible_min=None, first_season=1):
    import server
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S")
    db.add_character("s-a", "s", "Fatima", actor_name="X", first_season=first_season, first_episode=first_ep)
    db.add_character_arc("s-a", first_season, first_ep, status="alive", current_arc="secret story")
    monkeypatch.setattr(server, "db", db, raising=False)
    monkeypatch.setattr(server, "recogniser", OneFaceRecogniser(), raising=False)
    monkeypatch.setitem(server.indexes, "s", FixedIndex(matches))
    if possible_min is not None:
        monkeypatch.setattr(server, "_POSSIBLE_MIN", possible_min)
    ok, buf = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))
    r = TestClient(server.app).post("/identify", json={
        "image": base64.b64encode(buf.tobytes()).decode(), "show_id": "s", "season": 2, "episode": 1})
    assert r.status_code == 200
    return r.json()


def test_near_miss_face_is_shown_as_hedged_possible_match(monkeypatch):
    out = _identify(monkeypatch, [("s-a", 0.45), ("s-b", 0.10)])
    (c,) = out["characters"]
    assert c["possible"] is True and c["limited"] is True and c["name"] == "Fatima"
    assert c["current_arc"] is None and c["relationship_summary"] is None      # no story for a weak match


def test_face_below_possible_floor_is_still_not_sure(monkeypatch):
    out = _identify(monkeypatch, [("s-a", 0.35), ("s-b", 0.10)])
    assert out["characters"] == [] and out.get("low_confidence") is True


def test_possible_tier_respects_the_spoiler_shield(monkeypatch):
    out = _identify(monkeypatch, [("s-a", 0.45)], first_season=3)      # Fatima first appears in S3: unseen at S2E1
    assert out["characters"] == []


def test_possible_tier_can_be_switched_off(monkeypatch):
    out = _identify(monkeypatch, [("s-a", 0.45)], possible_min=0.0)
    assert out["characters"] == []


def test_confident_match_is_not_marked_possible(monkeypatch):
    out = _identify(monkeypatch, [("s-a", 0.7)])
    assert not out["characters"][0].get("possible")


def test_loose_hand_drawn_box_still_matches_the_tight_detector_box():
    import eval_metrics
    label = [351, 240, 733, 601]          # a whole head, drawn by hand
    pred = [443, 271, 646, 542]           # the detector's tight face box
    assert eval_metrics.iou(pred, label) < 0.5
    assert eval_metrics.match_boxes([{"bbox": pred}], [{"bbox": label}]) == {0: 0}


def test_tiny_fragment_inside_a_big_label_does_not_match():
    import eval_metrics
    assert eval_metrics.match_boxes([{"bbox": [10, 10, 20, 20]}], [{"bbox": [0, 0, 400, 400]}]) == {}
