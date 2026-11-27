"""Local accuracy log, /api/accuracy/summary and the match-margin rule (mock recogniser)."""
import base64
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import accuracy
import api_routes
import server
from database import CharacterDB


class Recogniser:
    def __init__(self, n):
        self.n = n

    def detect_faces(self, image):
        return [(None, [10.0, 20.0, 110.0, 170.0], SimpleNamespace(det_score=0.99, k=i)) for i in range(self.n)]

    def get_embedding(self, image, face):
        return np.array([face.k])


class Index:
    def __init__(self, table):
        self.table = table

    def search(self, emb, top_k=3):
        return self.table[int(emb[0])]


def _identify(monkeypatch, table, margin=0.0):
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S")
    for cid in ("s-a", "s-b", "s-c"):
        db.add_character(cid, "s", cid, actor_name="X", first_season=1, first_episode=1)
        db.add_character_arc(cid, 1, 1, status="alive", current_arc="x")
    monkeypatch.setattr(server, "db", db, raising=False)
    monkeypatch.setattr(server, "recogniser", Recogniser(len(table)), raising=False)
    monkeypatch.setattr(server, "_MIN_MARGIN", margin)
    monkeypatch.setitem(server.indexes, "s", Index(table))
    ok, buf = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))
    return TestClient(server.app).post("/identify", json={
        "image": base64.b64encode(buf.tobytes()).decode(), "show_id": "s", "season": 2, "episode": 1})


def _log():
    return [json.loads(x) for x in (server._DATA_DIR / accuracy.LOG_NAME).read_text().splitlines()]


def test_one_log_line_per_face_with_no_image_or_names(monkeypatch):
    r = _identify(monkeypatch, [[("s-a", 0.9), ("s-b", 0.5)], [("s-b", 0.3), ("s-c", 0.2)]])
    assert r.status_code == 200
    rows = _log()
    assert len(rows) == 2
    assert set(rows[0]) == {"ts", "show_id", "season", "episode", "score", "margin", "face_h",
                            "matched", "character_id", "not_sure", "p_correct"}
    assert rows[0]["matched"] is True and rows[0]["character_id"] == "s-a"
    assert rows[0]["score"] == 0.9 and rows[0]["margin"] == 0.4 and rows[0]["face_h"] == 150.0
    assert rows[0]["not_sure"] is False
    assert 0.0 <= rows[0]["p_correct"] <= 1.0 and rows[0]["p_correct"] > rows[1]["p_correct"]
    body = r.json()["characters"][0]
    assert body["calibrated_confidence"] == rows[0]["p_correct"] and body["confidence"] == 0.9
    assert rows[1]["matched"] is False and rows[1]["character_id"] is None


def test_unmatched_frame_is_logged_as_not_sure(monkeypatch):
    r = _identify(monkeypatch, [[("s-a", 0.3), ("s-b", 0.2)]])
    assert r.json()["low_confidence"] is True
    assert _log()[0]["not_sure"] is True and _log()[0]["matched"] is False


def test_logging_failure_never_fails_the_request(monkeypatch):
    monkeypatch.setattr(accuracy, "LOG_NAME", "bad\0name")
    r = _identify(monkeypatch, [[("s-a", 0.9), ("s-b", 0.5)]])
    assert r.status_code == 200 and r.json()["characters"][0]["character_id"] == "s-a"


def test_low_margin_is_not_sure_never_a_wrong_name(monkeypatch):
    r = _identify(monkeypatch, [[("s-a", 0.62), ("s-b", 0.58)]], margin=0.1)
    body = r.json()
    assert body["characters"] == [] and body["low_confidence"] is True
    assert _log()[0]["matched"] is False and _log()[0]["not_sure"] is True


def test_clear_margin_still_matches(monkeypatch):
    r = _identify(monkeypatch, [[("s-a", 0.7), ("s-b", 0.5)]], margin=0.1)
    assert [c["character_id"] for c in r.json()["characters"]] == ["s-a"]


def test_margin_off_matches_close_calls(monkeypatch):
    r = _identify(monkeypatch, [[("s-a", 0.62), ("s-b", 0.58)]], margin=0.0)
    assert [c["character_id"] for c in r.json()["characters"]] == ["s-a"]


def test_margin_with_one_to_one_assignment(monkeypatch):
    # face 0 is clear (a); face 1 is ambiguous (a vs b) so it stays unmatched, not b
    r = _identify(monkeypatch, [[("s-a", 0.9), ("s-c", 0.4)], [("s-a", 0.7), ("s-b", 0.66)]], margin=0.1)
    assert [c["character_id"] for c in r.json()["characters"]] == ["s-a"]
    # both faces clear: the stronger claims a, the other falls back to its next-best hit b
    r = _identify(monkeypatch, [[("s-a", 0.9), ("s-c", 0.4)], [("s-a", 0.8), ("s-b", 0.6)]], margin=0.1)
    assert sorted(c["character_id"] for c in r.json()["characters"]) == ["s-a", "s-b"]


def test_margin_env_override_is_read(monkeypatch):
    import importlib
    monkeypatch.setenv("CINEID_MIN_MARGIN", "0.07")
    mod = importlib.reload(server)
    try:
        assert mod._MIN_MARGIN == 0.07
        monkeypatch.setenv("CINEID_MIN_MARGIN", "junk")
        assert importlib.reload(server)._MIN_MARGIN == 0.0
    finally:
        monkeypatch.delenv("CINEID_MIN_MARGIN", raising=False)
        importlib.reload(server)


def _client(d):
    deps = api_routes.ApiDeps(config_path=d / "config.json", env_path=d / ".env",
                              get_db=lambda: None, keystore=None, key_check=None,
                              runner=None, tmdb_get=lambda p, q: {},
                              permissions=lambda: {}, data_dir=d)
    app = FastAPI()
    api_routes.install(app, deps)
    return TestClient(app)


def test_summary_empty():
    d = Path(tempfile.mkdtemp())
    out = _client(d).get("/api/accuracy/summary").json()
    assert out["identifications"] == 0 and out["matched"] == 0
    assert out["median_score"] is None and out["reported_wrong_rate"] is None
    assert out["feedback"] == {"wrong": 0, "missed": 0, "ok": 0}


def test_summary_counts_medians_and_lower_bound_rate():
    d = Path(tempfile.mkdtemp())
    rows = [{"score": 0.9, "margin": 0.4, "face_h": 100, "character_id": "a", "not_sure": False},
            {"score": 0.7, "margin": 0.2, "face_h": 100, "character_id": "b", "not_sure": False},
            {"score": 0.8, "margin": 0.1, "face_h": 100, "character_id": "c", "not_sure": False},
            {"score": 0.3, "margin": 0.05, "face_h": 100, "character_id": None, "not_sure": True}]
    accuracy.log_faces(d, rows, show_id="s", season=1, episode=1)
    (d / "accuracy_log.jsonl").open("a").write("garbage\n")
    import feedback
    feedback.record(d, show_id="s", kind="wrong", season=1, episode=1)
    feedback.record(d, show_id="s", kind="ok", season=1, episode=1)
    out = _client(d).get("/api/accuracy/summary").json()
    assert (out["identifications"], out["matched"], out["not_sure"]) == (4, 3, 1)
    assert out["median_score"] == 0.75 and out["median_margin"] == 0.15
    assert out["feedback"] == {"wrong": 1, "missed": 0, "ok": 1}
    assert out["reported_wrong_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert "lower bound" in out["reported_wrong_rate_note"].lower()


def test_gitignore_covers_accuracy_log():
    assert "data/accuracy_log.jsonl" in (Path(__file__).parent.parent / ".gitignore").read_text()
