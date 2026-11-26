import sys, tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import CharacterDB


def _db():
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S")
    db.add_character("s-a", "s", "A", first_season=1, first_episode=1)
    for ep in (1, 2, 3):
        db.add_character_arc("s-a", 1, ep, status="alive", current_arc=f"arc-{ep}")
    db.add_character_arc("s-a", 2, 1, status="dead", current_arc="dies in S02E01")
    return db


def _server():
    import server
    return server


@pytest.mark.parametrize("progress", [None, 0.0, 0.5, 0.89])
def test_early_in_episode_shows_state_at_the_start_of_it(progress):
    """At 1:20 into S01E03 the card must not contain what happens at 22:00."""
    arc, as_of = _server()._pick_arc(_db(), "s-a", 1, 3, progress)
    assert arc["current_arc"] == "arc-2" and as_of == "start"


@pytest.mark.parametrize("progress", [0.9, 0.97, 1.0])
def test_late_in_episode_shows_this_episodes_notes(progress):
    arc, as_of = _server()._pick_arc(_db(), "s-a", 1, 3, progress)
    assert arc["current_arc"] == "arc-3" and as_of == "end"


def test_first_episode_of_a_season_starts_from_the_previous_season():
    arc, as_of = _server()._pick_arc(_db(), "s-a", 2, 1, 0.2)
    assert arc["current_arc"] == "arc-3" and as_of == "start"      # not the S02E01 death


def test_very_first_episode_has_no_earlier_state():
    arc, as_of = _server()._pick_arc(_db(), "s-a", 1, 1, 0.2)
    assert arc is None and as_of == "start"


def test_chrome_script_searches_all_windows_and_never_launches():
    import chrome_scripts as cs
    s = cs.find_netflix_tab_script(cs.js_action("1+1"))
    assert "repeat with w in windows" in s and "netflix.com/watch" in s
    assert "front window" not in s and "launch" not in s and "activate" not in s
    assert 'if application "Google Chrome" is running' in s


def test_js_is_escaped_for_applescript():
    import chrome_scripts as cs
    assert cs.js_action('a"b\\c') == 'return execute t javascript "a\\"b\\\\c"'


@pytest.mark.parametrize("raw,expected", [("0.4321", 0.4321), (" 0.5\n", 0.5), ("1", 1.0),
                                          ("", None), ("NaN", None), ("abc", None),
                                          ("1.7", None), ("-0.1", None)])
def test_parse_progress(raw, expected):
    import chrome_scripts as cs
    assert cs.parse_progress(raw) == expected


def test_buddy_context_never_uses_the_current_episodes_arc():
    from buddy import _query_episode_characters
    db = _db()
    rows = _query_episode_characters(db._path, "s", 1, 3)
    assert rows and rows[0]["current_arc"] == "arc-2"
