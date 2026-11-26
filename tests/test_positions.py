import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config_store


def _cfg(data=None) -> Path:
    p = Path(tempfile.mkdtemp()) / "config.json"
    if data is not None:
        p.write_text(json.dumps(data))
    return p


def test_set_position_updates_current_and_map():
    p = _cfg({"show_id": "a", "season": 1, "episode": 1, "buddy": {"name": "Alex"}})
    cfg = config_store.set_position(p, "a", 3, 7)
    saved = json.loads(p.read_text())
    assert saved["season"] == 3 and saved["episode"] == 7
    assert saved["positions"]["a"] == {"season": 3, "episode": 7}
    assert saved["buddy"] == {"name": "Alex"} and cfg == saved


def test_select_show_restores_that_shows_last_position():
    p = _cfg({"show_id": "a", "season": 4, "episode": 2,
              "positions": {"a": {"season": 4, "episode": 2},
                            "b": {"season": 2, "episode": 9}}})
    config_store.select_show(p, "b")
    saved = json.loads(p.read_text())
    assert (saved["show_id"], saved["season"], saved["episode"]) == ("b", 2, 9)
    assert saved["positions"]["a"] == {"season": 4, "episode": 2}


def test_select_show_saves_the_position_being_left():
    p = _cfg({"show_id": "a", "season": 4, "episode": 2})
    config_store.select_show(p, "b")
    saved = json.loads(p.read_text())
    assert saved["positions"]["a"] == {"season": 4, "episode": 2}


def test_select_unknown_show_defaults_to_s1e1():
    p = _cfg({"show_id": "a", "season": 4, "episode": 2})
    config_store.select_show(p, "new")
    saved = json.loads(p.read_text())
    assert (saved["show_id"], saved["season"], saved["episode"]) == ("new", 1, 1)


def test_set_position_rejects_non_positive():
    import pytest
    p = _cfg({"show_id": "a", "season": 1, "episode": 1})
    with pytest.raises(ValueError):
        config_store.set_position(p, "a", 0, 3)
    with pytest.raises(ValueError):
        config_store.set_position(p, "a", 2, -1)


def test_position_helpers_work_on_missing_file():
    p = _cfg()
    cfg = config_store.set_position(p, "a", 2, 2)
    assert cfg["show_id"] == "a" and cfg["positions"]["a"]["episode"] == 2
