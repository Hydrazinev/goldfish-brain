import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import actor_credits as ac
from database import CharacterDB


def movie(id, title, date, votes, genres=(18,), character="Someone"):
    return {"id": id, "media_type": "movie", "title": title, "release_date": date,
            "vote_count": votes, "genre_ids": list(genres), "character": character}


def tv(id, name, date, votes, genres=(18,), character="Someone"):
    return {"id": id, "media_type": "tv", "name": name, "first_air_date": date,
            "vote_count": votes, "genre_ids": list(genres), "character": character}


def getter(cast, calls=None):
    def get(path, params):
        if calls is not None:
            calls.append(path)
        return {"cast": cast, "crew": [movie(1, "Directed Only", "2000-01-01", 99999)]}
    return get


def test_sorted_by_votes_capped_and_shaped():
    cast = [movie(i, f"M{i}", f"20{i:02d}-05-05", i * 10) for i in range(1, 8)] + \
           [tv(100, "Big Show", "2015-02-08", 5000)]
    out = ac.top_credits(getter(cast), "k", 5, None)
    assert [c["title"] for c in out] == ["Big Show", "M7", "M6", "M5"]
    assert out[0]["year"] == 2015 and out[0]["kind"] == "series"
    assert out[1]["kind"] == "movie" and out[1]["year"] == 2007


def test_limit_param():
    cast = [movie(i, f"M{i}", "2001-01-01", i) for i in range(1, 8)]
    assert len(ac.top_credits(getter(cast), "k", 5, None, limit=2)) == 2


def test_drops_the_show_itself_talk_news_reality_and_self_roles():
    cast = [tv(1396, "Breaking Bad", "2008-01-20", 9000),
            tv(2, "Late Show", "2000-01-01", 800, genres=(10767,)),
            tv(3, "The News", "2000-01-01", 800, genres=(10763,)),
            tv(4, "Real Life", "2000-01-01", 800, genres=(10764,)),
            movie(5, "Doc", "2010-01-01", 800, character="Self"),
            movie(6, "Doc2", "2010-01-01", 800, character="Himself (archive)"),
            movie(7, "Doc3", "2010-01-01", 800, character="Herself"),
            movie(8, "Keeper", "2012-01-01", 10),
            movie(1396, "A Movie With The Same Id", "2012-01-01", 5)]     # a movie id is not the show
    out = ac.top_credits(getter(cast), "k", 5, 1396)
    assert [c["title"] for c in out] == ["Keeper", "A Movie With The Same Id"]


def test_dedupes_by_title_keeping_the_most_voted():
    cast = [movie(1, "Twin", "2001-01-01", 10), movie(2, "twin", "2005-01-01", 50),
            movie(3, "Other", "2002-01-01", 20)]
    out = ac.top_credits(getter(cast), "k", 5, None)
    assert [(c["title"], c["year"]) for c in out] == [("twin", 2005), ("Other", 2002)]


def test_missing_dates_and_titles_are_tolerated():
    cast = [movie(1, "Undated", "", 5), {"id": 9, "media_type": "movie", "vote_count": 3},
            {"id": 8, "media_type": "person"}]
    out = ac.top_credits(getter(cast), "k", 5, None)
    assert out == [{"id": 1, "title": "Undated", "year": None, "kind": "movie"}]


def test_formats_titles_for_also_in():
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S", tmdb_id=77)
    db.add_character("s-a", "s", "A", actor_tmdb_id=5)
    assert ac.cached_also_in(db, db.get_character("s-a"), 77) == []          # not cached yet
    db.set_cached_credits(5, [{"id": 77, "title": "S", "year": 2008, "kind": "series"},
                              {"id": 1, "title": "Saul", "year": 2015, "kind": "series"},
                              {"id": 2, "title": "Undated", "year": None, "kind": "movie"}])
    assert ac.cached_also_in(db, db.get_character("s-a"), 77) == ["Saul (2015)", "Undated"]
    db.add_character("s-b", "s", "B")                                           # no actor id
    assert ac.cached_also_in(db, db.get_character("s-b"), 77) == []


def _db_with_cast():
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S", tmdb_id=77)
    db.update_show_status("s", "ready")
    db.add_show("nope", "No tmdb")
    db.update_show_status("nope", "ready")
    db.add_character("s-a", "s", "A", actor_name="Bryan Cranston")
    db.add_character("s-b", "s", "B", actor_name="Ann  Other")
    db.add_character("s-c", "s", "C", actor_name="Has Id", actor_tmdb_id=3)
    return db


def test_backfill_matches_by_name_case_insensitively_and_keeps_existing():
    db = _db_with_cast()
    calls = []

    def get(path, params):
        calls.append(path)
        return {"cast": [{"id": 11, "name": "bryan cranston"}, {"id": 12, "name": "Has Id"},
                         {"id": 13, "name": "Somebody Else"}]}
    n = ac.backfill_actor_ids(db, get, "k")
    assert n == 1 and calls == ["tv/77/aggregate_credits"]
    assert db.get_character("s-a")["actor_tmdb_id"] == 11
    assert db.get_character("s-b")["actor_tmdb_id"] is None
    assert db.get_character("s-c")["actor_tmdb_id"] == 3


def test_backfill_survives_a_tmdb_error():
    db = _db_with_cast()

    def boom(path, params):
        raise OSError("down")
    assert ac.backfill_actor_ids(db, boom, "k") == 0


def _filled_db():
    db = CharacterDB(db_path=Path(tempfile.mkdtemp()) / "t.db")
    db.add_show("s", "S", tmdb_id=77)
    db.update_show_status("s", "ready")
    db.add_character("s-a", "s", "A", actor_tmdb_id=1)
    db.add_character("s-b", "s", "B", actor_tmdb_id=2)
    db.add_character("s-twin", "s", "Twin", actor_tmdb_id=2)      # same actor twice: one fetch
    db.add_character("s-none", "s", "None")
    return db


def test_fill_credits_fetches_missing_once_per_actor_and_caches():
    db, calls = _filled_db(), []

    def get(path, params):
        calls.append(path)
        return {"cast": [movie(9, "Film", "2001-01-01", 10)]}
    n = ac.fill_credits(db, get, "k", sleep=lambda s: None)
    assert n == 2 and sorted(calls) == ["person/1/combined_credits", "person/2/combined_credits"]
    assert db.get_cached_credits(1)["credits"][0]["title"] == "Film"
    calls.clear()
    assert ac.fill_credits(db, get, "k", sleep=lambda s: None) == 0 and calls == []     # fresh


def test_fill_credits_refreshes_expired_and_tolerates_errors():
    db = _filled_db()
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    db.set_cached_credits(1, [{"id": 1, "title": "Old", "year": 1999, "kind": "movie"}], fetched_at=old)
    db.set_cached_credits(2, [], fetched_at=datetime.now(timezone.utc).isoformat())

    def get(path, params):
        if "/1/" in path:
            raise OSError("boom")
        raise AssertionError("fresh actor must not be fetched")
    assert ac.fill_credits(db, get, "k", sleep=lambda s: None) == 0
    assert db.get_cached_credits(1)["credits"][0]["title"] == "Old"      # kept after the failure

    ok = lambda path, params: {"cast": [movie(9, "New", "2020-01-01", 1)]}
    assert ac.fill_credits(db, ok, "k", sleep=lambda s: None) == 1
    assert db.get_cached_credits(1)["credits"][0]["title"] == "New"


def test_failed_actor_does_not_stop_the_others():
    db = _filled_db()

    def get(path, params):
        if "/1/" in path:
            raise OSError("boom")
        return {"cast": [movie(9, "Film", "2001-01-01", 10)]}
    assert ac.fill_credits(db, get, "k", sleep=lambda s: None) == 1
    assert db.get_cached_credits(1) is None and db.get_cached_credits(2) is not None
