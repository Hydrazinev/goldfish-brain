import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from episode_lookup import find_season


def make_get(seasons, calls=None, error=None):
    """seasons: {n: [(episode_number, name), ...]}; others 404."""
    def get(path, params):
        if calls is not None:
            calls.append(path)
        if error:
            raise error
        n = int(path.rsplit("/", 1)[1])
        if n not in seasons:
            raise urllib.error.HTTPError("u", 404, "nf", {}, None)
        return {"episodes": [{"episode_number": e, "name": t} for e, t in seasons[n]]}
    return get


def test_found_in_start_season_costs_one_request():
    calls = []
    get = make_get({1: [(4, "Other")], 2: [(4, "A Rock and a Far Away")]}, calls)
    assert find_season(get, 99, 4, "a rock and a far away", start_season=2) == 2
    assert calls == ["tv/99/season/2"]


def test_found_in_another_season():
    get = make_get({1: [(4, "Nope")], 2: [(4, "Other")], 3: [(4, "The Title")]})
    assert find_season(get, 99, 4, "the title") == 3


def test_title_matches_as_substring_either_way_and_empty_title_matches_number():
    get = make_get({1: [(4, "The Title: Part 2")]})
    assert find_season(get, 1, 4, "the title") == 1
    assert find_season(get, 1, 4, "The Title: Part 2 extended") == 1
    assert find_season(get, 1, 4, "") == 1


def test_title_mismatch_returns_none():
    get = make_get({1: [(4, "Alpha")], 2: [(4, "Beta")]})
    assert find_season(get, 1, 4, "Gamma") is None


def test_three_consecutive_404s_stop_the_search():
    calls = []
    get = make_get({1: [(1, "x")]}, calls)
    assert find_season(get, 1, 4, "zzz", start_season=1) == None
    assert calls == ["tv/1/season/1", "tv/1/season/2", "tv/1/season/3", "tv/1/season/4"]


def test_other_errors_return_none():
    assert find_season(make_get({}, error=OSError("down")), 1, 4, "t") is None
    err = urllib.error.HTTPError("u", 500, "boom", {}, None)
    assert find_season(make_get({}, error=err), 1, 4, "t") is None
