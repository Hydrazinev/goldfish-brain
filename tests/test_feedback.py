import json
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import api_routes


class _Keys:
    KEYS = ()

    def get_key(self, *a, **k):
        return ""


def _env(capture=False):
    d = Path(tempfile.mkdtemp())
    if capture:
        (d / "last_capture.jpg").write_bytes(b"\xff\xd8JPEGDATA")
    deps = api_routes.ApiDeps(config_path=d / "config.json", env_path=d / ".env",
                              get_db=lambda: None, keystore=_Keys(), key_check=None,
                              runner=None, tmdb_get=lambda p, q: {},
                              permissions=lambda: {}, data_dir=d)
    app = FastAPI()
    api_routes.install(app, deps)
    return d, TestClient(app)


def _post(c, **over):
    body = {"show_id": "bb", "kind": "wrong", "season": 2, "episode": 3, "character_id": "walter"}
    body.update(over)
    return c.post("/api/feedback", json=body)


def _lines(d):
    p = d / "feedback" / "feedback.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_feedback_appends_a_json_line_with_timestamp():
    d, c = _env()
    r = _post(c, note="That was the barista")
    assert r.status_code == 200 and r.json() == {"ok": True}
    _post(c, kind="ok", character_id=None)
    rows = _lines(d)
    assert len(rows) == 2
    assert rows[0]["show_id"] == "bb" and rows[0]["kind"] == "wrong"
    assert rows[0]["character_id"] == "walter" and rows[0]["note"] == "That was the barista"
    assert rows[0]["season"] == 2 and rows[0]["episode"] == 3 and rows[0]["ts"]


def test_wrong_and_missed_copy_the_last_capture_ok_does_not():
    d, c = _env(capture=True)
    _post(c, kind="wrong")
    _post(c, kind="missed")
    _post(c, kind="ok")
    rows = _lines(d)
    assert rows[0]["image"] and rows[1]["image"] and rows[2].get("image") is None
    assert (d / "feedback" / rows[0]["image"]).read_bytes() == b"\xff\xd8JPEGDATA"
    assert len(list((d / "feedback").glob("*.jpg"))) == 2
    assert rows[0]["image"] != rows[1]["image"]


def test_missing_capture_is_fine():
    d, c = _env(capture=False)
    assert _post(c).status_code == 200
    assert _lines(d)[0].get("image") is None


@pytest.mark.parametrize("over", [{"kind": "bogus"}, {"note": "x" * 301}, {"show_id": ""}])
def test_invalid_feedback_rejected(over):
    d, c = _env()
    r = _post(c, **over)
    assert r.status_code in (400, 422)
    assert not (d / "feedback" / "feedback.jsonl").exists()


def test_summary_counts_by_kind_and_handles_empty_and_bad_lines():
    d, c = _env()
    assert c.get("/api/feedback/summary").json() == {"total": 0, "wrong": 0, "missed": 0, "ok": 0}
    _post(c, kind="wrong"); _post(c, kind="wrong"); _post(c, kind="ok")
    with open(d / "feedback" / "feedback.jsonl", "a") as f:
        f.write("not json\n")
    assert c.get("/api/feedback/summary").json() == {"total": 3, "wrong": 2, "missed": 0, "ok": 1}


def test_gitignore_covers_feedback():
    text = (Path(__file__).parent.parent / ".gitignore").read_text()
    assert "data/feedback/" in text
