import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import api_routes
from database import CharacterDB
from show_jobs import JobBusy


class FakeKeys:
    def __init__(self, **initial):
        self.store = dict(initial)

    def get_key(self, name, env_path, runner=None):
        return self.store.get(name, "")

    def set_key(self, name, value, runner=None):
        self.store[name] = value.strip().strip('"')

    def delete_key(self, name, runner=None):
        self.store.pop(name, None)

    KEYS = ("TMDB_API_KEY", "ANTHROPIC_API_KEY")

    @staticmethod
    def clean_key(v):
        return v.strip().strip('"').strip()


class FakeCheck:
    def __init__(self, ok=True, msg="Looks good."):
        self.ok, self.msg, self.seen = ok, msg, []

    def validate_tmdb(self, key):
        self.seen.append(("tmdb", key))
        return self.ok, self.msg

    def validate_anthropic(self, key):
        self.seen.append(("anthropic", key))
        return self.ok, self.msg


class FakeRunner:
    def __init__(self):
        self.started = []
        self.busy = False
        self.cancelled = False
        self._status = None

    def start(self, show_name, tmdb_id, poster_path, with_notes):
        if self.busy:
            raise JobBusy("Another show is still being set up.")
        self.started.append((show_name, tmdb_id, poster_path, with_notes))
        self._status = {"show_id": "x", "state": "running", "percent": 0}
        return "breaking-bad"

    def status(self):
        return self._status

    def cancel(self):
        self.cancelled = True


class Env:
    def __init__(self, keys=None, config=None, check=None, tmdb=None):
        d = Path(tempfile.mkdtemp())
        self.config_path = d / "config.json"
        if config is not None:
            self.config_path.write_text(config if isinstance(config, str)
                                        else json.dumps(config))
        self.db = CharacterDB(db_path=d / "t.db")
        self.keys = FakeKeys(**(keys or {}))
        self.check = check or FakeCheck()
        self.runner = FakeRunner()
        self.tmdb_calls = []
        self._tmdb = tmdb or (lambda path, params: {"results": []})
        deps = api_routes.ApiDeps(
            config_path=self.config_path, env_path=d / ".env",
            get_db=lambda: self.db, keystore=self.keys, key_check=self.check,
            runner=self.runner, tmdb_get=self._tmdb_get,
            permissions=lambda: {"screen_recording": True})
        app = FastAPI()
        api_routes.install(app, deps)
        self.client = TestClient(app)

    def _tmdb_get(self, path, params):
        self.tmdb_calls.append((path, params))
        return self._tmdb(path, params)

    def ready_show(self, show_id="breaking-bad", name="Breaking Bad", n=20):
        self.db.add_show(show_id, name, tmdb_id=1396)
        self.db.update_show_status(show_id, "ready")
        self.db.refresh_show_character_count(show_id)
        self.db.set_show_poster(show_id, "/poster.jpg")


# ── state ───────────────────────────────────────────────────────────────────

def test_state_with_missing_config_asks_for_first_run():
    e = Env()
    r = e.client.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_first_run"] is True
    assert body["setup"] == {"tmdb_key": False, "anthropic_key": False}
    assert body["shows"] == []


def test_state_with_corrupt_config_still_answers():
    """config.json cut off mid-write must not turn the UI into an error page."""
    e = Env(config='{"show_id": "x", "seas')
    r = e.client.get("/api/state")
    assert r.status_code == 200 and r.json()["needs_first_run"] is True


def test_state_lists_ready_shows_with_positions_and_poster():
    e = Env(keys={"TMDB_API_KEY": "k"},
            config={"show_id": "breaking-bad", "season": 2, "episode": 8})
    e.ready_show()
    e.db.add_show("half", "Half Done")                     # ingesting: hidden
    body = e.client.get("/api/state").json()
    assert body["needs_first_run"] is False
    assert [s["show_id"] for s in body["shows"]] == ["breaking-bad"]
    s = body["shows"][0]
    assert s["season"] == 2 and s["episode"] == 8 and s["current"] is True
    assert s["poster_url"] == "https://image.tmdb.org/t/p/w342/poster.jpg"
    assert body["current"]["show_name"] == "Breaking Bad"


def test_state_never_contains_key_values():
    e = Env(keys={"TMDB_API_KEY": "SECRET-TMDB", "ANTHROPIC_API_KEY": "SECRET-ANT"})
    text = e.client.get("/api/state").text
    assert "SECRET" not in text
    assert json.loads(text)["setup"] == {"tmdb_key": True, "anthropic_key": True}


# ── keys ────────────────────────────────────────────────────────────────────

def test_save_key_trims_validates_and_stores():
    e = Env()
    r = e.client.post("/api/keys", json={"name": "TMDB_API_KEY", "value": '  "abc"\n'})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert e.check.seen == [("tmdb", "abc")]
    assert e.keys.store["TMDB_API_KEY"] == "abc"


def test_rejected_key_is_not_stored():
    e = Env(check=FakeCheck(ok=False, msg="TMDB rejected that key"))
    r = e.client.post("/api/keys", json={"name": "TMDB_API_KEY", "value": "bad"})
    assert r.status_code == 400 and r.json() == {"ok": False, "message": "TMDB rejected that key"}
    assert "TMDB_API_KEY" not in e.keys.store


def test_blank_key_and_unknown_name_rejected():
    e = Env()
    r = e.client.post("/api/keys", json={"name": "TMDB_API_KEY", "value": "   "})
    assert r.status_code == 400 and r.json()["message"] == "Paste your key first."
    r = e.client.post("/api/keys", json={"name": "AWS", "value": "x"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_delete_key():
    e = Env(keys={"ANTHROPIC_API_KEY": "k"})
    assert e.client.delete("/api/keys/ANTHROPIC_API_KEY").json()["ok"] is True
    assert "ANTHROPIC_API_KEY" not in e.keys.store


# ── search ──────────────────────────────────────────────────────────────────

def test_search_without_tmdb_key_is_clear_400():
    e = Env()
    r = e.client.get("/api/shows/search", params={"q": "dark"})
    assert r.status_code == 400 and r.json()["message"] == "Add your TMDB key first."


@pytest.mark.parametrize("q", ["", "   ", "\n\t"])
def test_search_blank_returns_empty_without_calling_tmdb(q):
    e = Env(keys={"TMDB_API_KEY": "k"})
    r = e.client.get("/api/shows/search", params={"q": q})
    assert r.status_code == 200 and r.json() == {"results": []}
    assert e.tmdb_calls == []


def test_search_long_and_punctuated_queries_are_safe():
    e = Env(keys={"TMDB_API_KEY": "k"})
    q = "x" * 300 + " &=?#/ ../ é"
    r = e.client.get("/api/shows/search", params={"q": q})
    assert r.status_code == 200
    assert len(e.tmdb_calls[0][1]["query"]) <= 100


def test_search_maps_results_and_flags_installed():
    payload = {"results": [
        {"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20",
         "poster_path": "/bb.jpg", "overview": "x" * 400},
        {"id": 5, "name": "No Poster", "first_air_date": "", "poster_path": None,
         "overview": ""},
    ] + [{"id": 100 + i, "name": f"S{i}", "first_air_date": "2000-01-01",
          "poster_path": None, "overview": ""} for i in range(12)]}
    e = Env(keys={"TMDB_API_KEY": "k"}, tmdb=lambda p, q: payload)
    e.ready_show()
    res = e.client.get("/api/shows/search", params={"q": "breaking"}).json()["results"]
    assert len(res) == 8
    assert res[0] == {"tmdb_id": 1396, "name": "Breaking Bad", "year": "2008",
                      "poster_path": "/bb.jpg",
                      "poster_url": "https://image.tmdb.org/t/p/w342/bb.jpg",
                      "overview": "x" * 180, "installed": True}
    assert res[1]["year"] == "" and res[1]["poster_url"] is None
    assert res[1]["installed"] is False


def test_search_network_failure_is_friendly():
    def boom(path, params):
        raise OSError("down")
    e = Env(keys={"TMDB_API_KEY": "k"}, tmdb=boom)
    r = e.client.get("/api/shows/search", params={"q": "dark"})
    assert r.status_code == 502 and "Couldn't reach TMDB" in r.json()["message"]


# ── setup job ───────────────────────────────────────────────────────────────

BODY = {"tmdb_id": 1396, "name": "Breaking Bad", "poster_path": "/bb.jpg",
        "with_notes": True}


def test_setup_starts_job_with_notes_when_anthropic_key_present():
    e = Env(keys={"TMDB_API_KEY": "k", "ANTHROPIC_API_KEY": "o"})
    r = e.client.post("/api/shows/setup", json=BODY)
    assert r.status_code == 200 and r.json()["show_id"] == "breaking-bad"
    assert e.runner.started == [("Breaking Bad", 1396, "/bb.jpg", True)]


def test_setup_without_anthropic_key_disables_notes_and_says_so():
    e = Env(keys={"TMDB_API_KEY": "k"})
    r = e.client.post("/api/shows/setup", json=BODY)
    assert r.status_code == 200 and r.json()["notes_enabled"] is False
    assert e.runner.started[0][3] is False


def test_setup_requires_tmdb_key():
    e = Env()
    r = e.client.post("/api/shows/setup", json=BODY)
    assert r.status_code == 400 and r.json()["message"] == "Add your TMDB key first."


def test_setup_409_when_job_running():
    e = Env(keys={"TMDB_API_KEY": "k"})
    e.runner.busy = True
    r = e.client.post("/api/shows/setup", json=BODY)
    assert r.status_code == 409 and r.json()["message"] == "Another show is still being set up."


def test_setup_409_when_already_ready_unless_update():
    e = Env(keys={"TMDB_API_KEY": "k"})
    e.ready_show()
    r = e.client.post("/api/shows/setup", json=BODY)
    assert r.status_code == 409
    assert r.json()["message"] == "Breaking Bad is already set up. Use Update to refresh it."
    assert e.runner.started == []
    r = e.client.post("/api/shows/setup", json={**BODY, "update": True})
    assert r.status_code == 200 and len(e.runner.started) == 1


def test_setup_blank_name_rejected():
    e = Env(keys={"TMDB_API_KEY": "k"})
    r = e.client.post("/api/shows/setup", json={**BODY, "name": "  "})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_malformed_body_gets_plain_message_not_422_detail():
    e = Env(keys={"TMDB_API_KEY": "k"})
    r = e.client.post("/api/shows/setup", json={"nonsense": 1})
    assert r.status_code == 422 and r.json() == {"ok": False,
                                                 "message": "That request wasn't understood."}


def test_job_status_and_cancel():
    e = Env(keys={"TMDB_API_KEY": "k"})
    assert e.client.get("/api/shows/job").json() == {"job": None}
    e.client.post("/api/shows/setup", json=BODY)
    assert e.client.get("/api/shows/job").json()["job"]["state"] == "running"
    assert e.client.post("/api/shows/job/cancel").json()["ok"] is True
    assert e.runner.cancelled is True


# ── select / position ───────────────────────────────────────────────────────

def test_select_show_restores_its_position():
    e = Env(config={"show_id": "a", "season": 4, "episode": 2,
                    "positions": {"b": {"season": 2, "episode": 9}}})
    e.ready_show("a", "A")
    e.ready_show("b", "B")
    assert e.client.post("/api/shows/select", json={"show_id": "b"}).json()["ok"] is True
    cfg = json.loads(e.config_path.read_text())
    assert (cfg["show_id"], cfg["season"], cfg["episode"]) == ("b", 2, 9)


def test_select_unknown_or_unready_show_404():
    e = Env()
    e.db.add_show("half", "Half")
    assert e.client.post("/api/shows/select", json={"show_id": "half"}).status_code == 404
    assert e.client.post("/api/shows/select", json={"show_id": "nope"}).status_code == 404


@pytest.mark.parametrize("season,episode", [(0, 1), (1, 0), (-1, 5)])
def test_position_rejects_non_positive(season, episode):
    e = Env()
    e.ready_show()
    r = e.client.post("/api/position", json={"show_id": "breaking-bad",
                                             "season": season, "episode": episode})
    assert r.status_code == 400
    assert r.json()["message"] == "Season and episode must be 1 or higher."


def test_position_saved():
    e = Env(config={"show_id": "breaking-bad", "season": 1, "episode": 1})
    e.ready_show()
    r = e.client.post("/api/position", json={"show_id": "breaking-bad",
                                             "season": 3, "episode": 7})
    assert r.json()["ok"] is True
    cfg = json.loads(e.config_path.read_text())
    assert cfg["season"] == 3 and cfg["positions"]["breaking-bad"]["episode"] == 7


def test_position_refuses_to_overwrite_unreadable_config():
    e = Env(config='{"show_id": "x", "seas')
    e.ready_show()
    r = e.client.post("/api/position", json={"show_id": "breaking-bad",
                                             "season": 3, "episode": 7})
    assert r.status_code == 409
    assert e.config_path.read_text() == '{"show_id": "x", "seas'


# ── buddy / permissions ─────────────────────────────────────────────────────

def test_buddy_defaults_and_update():
    e = Env(config={"show_id": "a", "season": 1, "episode": 1})
    got = e.client.get("/api/buddy").json()["buddy"]
    assert got["trigger_mode"] == "audio" and got["share_only_window"] is True
    assert got["disclosure_ack"] is False
    r = e.client.post("/api/buddy", json={"name": "Sam", "personality": "chill",
                                          "disclosure_ack": True, "cooldown_minutes": 2})
    assert r.json()["ok"] is True
    cfg = json.loads(e.config_path.read_text())
    assert cfg["buddy"]["name"] == "Sam" and cfg["buddy"]["disclosure_ack"] is True
    assert cfg["show_id"] == "a"


@pytest.mark.parametrize("bad", [{"personality": "villain"}, {"trigger_mode": "psychic"},
                                 {"cooldown_minutes": -3}, {"name": ""}])
def test_buddy_rejects_bad_values(bad):
    e = Env()
    assert e.client.post("/api/buddy", json=bad).status_code == 400


def test_permissions_endpoint():
    e = Env()
    assert e.client.get("/api/permissions").json() == {"screen_recording": True}


# ── poster backfill ─────────────────────────────────────────────────────────

def test_backfill_posters_fills_only_missing_and_survives_errors():
    e = Env()
    e.db.add_show("a", "A", tmdb_id=1)
    e.db.update_show_status("a", "ready")
    e.db.add_show("b", "B", tmdb_id=2)
    e.db.update_show_status("b", "ready")
    e.db.add_show("c", "C", tmdb_id=3)
    e.db.update_show_status("c", "ready")
    e.db.set_show_poster("c", "/keep.jpg")

    def fake(path, params):
        if path == "tv/2":
            raise OSError("offline")
        return {"poster_path": "/new.jpg"}

    n = api_routes.backfill_posters(e.db, fake, "k")
    posters = {s["show_id"]: s["poster_path"] for s in e.db.get_all_shows()}
    assert posters == {"a": "/new.jpg", "b": None, "c": "/keep.jpg"}
    assert n == 1


# ── open System Settings / key pages (whitelist only) ───────────────────────

def test_open_allows_only_whitelisted_targets():
    e = Env()
    opened = []
    e.open_target = opened.append
    # rebuild client with the recording opener
    from fastapi import FastAPI
    app = FastAPI()
    api_routes.install(app, api_routes.ApiDeps(
        config_path=e.config_path, env_path=e.config_path.parent / ".env",
        get_db=lambda: e.db, keystore=e.keys, key_check=e.check, runner=e.runner,
        tmdb_get=lambda p, q: {}, permissions=lambda: {}, open_target=opened.append))
    c = TestClient(app)
    assert c.post("/api/open", json={"target": "screen_recording"}).json()["ok"] is True
    assert opened[-1].startswith("x-apple.systempreferences:")
    assert "ScreenCapture" in opened[-1]
    assert c.post("/api/open", json={"target": "tmdb_key_page"}).json()["ok"] is True
    assert opened[-1] == "https://www.themoviedb.org/settings/api"
    assert c.post("/api/open", json={"target": "anthropic_key_page"}).json()["ok"] is True
    assert opened[-1] == "https://console.anthropic.com/settings/keys"
    n = len(opened)
    r = c.post("/api/open", json={"target": "https://evil.example"})
    assert r.status_code == 400 and len(opened) == n
    r = c.post("/api/open", json={"target": "file:///etc/passwd"})
    assert r.status_code == 400 and len(opened) == n


# ── character photo for the Mac app's popup card ────────────────────────────

def _env_with_photos(files: dict):
    """Env whose data_dir has data/shows/s/raw/s-a/<files>; character s-a in show s."""
    e = Env()
    d = e.config_path.parent
    e.db.add_show("s", "S")
    e.db.add_character("s-a", "s", "A", actor_name="Actor")
    raw = d / "shows" / "s" / "raw" / "s-a"
    raw.mkdir(parents=True)
    for name, data in files.items():
        (raw / name).write_bytes(data)
    app = FastAPI()
    api_routes.install(app, api_routes.ApiDeps(
        config_path=e.config_path, env_path=d / ".env", get_db=lambda: e.db,
        keystore=e.keys, key_check=e.check, runner=e.runner,
        tmdb_get=lambda p, q: {}, permissions=lambda: {}, data_dir=d))
    return TestClient(app)


def test_photo_served_for_known_character():
    c = _env_with_photos({"tmdb_profile.jpg": b"\xff\xd8JPEGDATA"})
    r = c.get("/api/characters/s-a/photo")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8JPEGDATA"


def test_photo_prefers_tmdb_profile_then_any_image():
    c = _env_with_photos({"zzz.jpg": b"other", "tmdb_profile.jpg": b"profile"})
    assert c.get("/api/characters/s-a/photo").content == b"profile"
    c2 = _env_with_photos({"only.png": b"pngdata"})
    r = c2.get("/api/characters/s-a/photo")
    assert r.content == b"pngdata" and r.headers["content-type"] == "image/png"


def test_photo_404_when_no_files_or_unknown_character():
    c = _env_with_photos({})
    r = c.get("/api/characters/s-a/photo")
    assert r.status_code == 404 and r.json() == {"ok": False, "message": "No photo."}
    assert c.get("/api/characters/nobody/photo").status_code == 404


@pytest.mark.parametrize("bad", ["..%2f..%2fetc%2fpasswd", "..", "a%2fb", "S-A", "s-a%00"])
def test_photo_rejects_path_traversal_and_odd_ids(bad):
    c = _env_with_photos({"tmdb_profile.jpg": b"x"})
    assert c.get(f"/api/characters/{bad}/photo").status_code in (404, 422)


# ── episode season lookup ───────────────────────────────────────────────────

def _season_tmdb(path, params):
    import urllib.error
    n = int(path.rsplit("/", 1)[1])
    if n != 2:
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    return {"episodes": [{"episode_number": 4, "name": "A Rock and a Far Away"}]}


def test_episode_season_found():
    e = Env(keys={"TMDB_API_KEY": "k"}, tmdb=_season_tmdb)
    e.ready_show()
    r = e.client.get("/api/episodes/season",
                     params={"show_id": "breaking-bad", "episode": 4, "title": "A Rock and a Far Away"})
    assert r.status_code == 200 and r.json() == {"season": 2}


def test_episode_season_without_key_or_unknown_show_is_null():
    e = Env(tmdb=_season_tmdb)
    e.ready_show()
    q = {"show_id": "breaking-bad", "episode": 4, "title": "x"}
    assert e.client.get("/api/episodes/season", params=q).json() == {"season": None}
    e2 = Env(keys={"TMDB_API_KEY": "k"}, tmdb=_season_tmdb)
    q["show_id"] = "nope"
    assert e2.client.get("/api/episodes/season", params=q).json() == {"season": None}


def test_episode_season_tmdb_failure_is_null_not_500():
    def boom(path, params):
        raise RuntimeError("down")
    e = Env(keys={"TMDB_API_KEY": "k"}, tmdb=boom)
    e.ready_show()
    r = e.client.get("/api/episodes/season", params={"show_id": "breaking-bad", "episode": 4})
    assert r.status_code == 200 and r.json() == {"season": None}


def test_episode_season_long_title_is_truncated():
    seen = []
    def get(path, params):
        seen.append(path)
        return {"episodes": [{"episode_number": 4, "name": "x" * 120}]}
    e = Env(keys={"TMDB_API_KEY": "k"}, tmdb=get)
    e.ready_show()
    r = e.client.get("/api/episodes/season",
                     params={"show_id": "breaking-bad", "episode": 4, "title": "x" * 500})
    assert r.json() == {"season": 1}


# ── /api/state must be fast: no screenshot probe on the hotkey path ─────────

def test_state_does_not_probe_permissions_unless_asked():
    """The Mac app calls /api/state on every ⌘⇧X; the permission probe takes a real
    screenshot (~1 s), so it must not run by default."""
    calls = []
    e = Env()
    app = FastAPI()
    api_routes.install(app, api_routes.ApiDeps(
        config_path=e.config_path, env_path=e.config_path.parent / ".env", get_db=lambda: e.db,
        keystore=e.keys, key_check=e.check, runner=e.runner, tmdb_get=lambda p, q: {},
        permissions=lambda: calls.append(1) or {"screen_recording": True}))
    c = TestClient(app)
    body = c.get("/api/state").json()
    assert calls == [] and body["permissions"] == {"screen_recording": None}
    assert c.get("/api/state?permissions=1").json()["permissions"] == {"screen_recording": True}
    assert calls == [1]


def test_permission_probe_is_cached_briefly():
    clock = [100.0]
    n = []
    probe = api_routes.cached(lambda: n.append(1) or {"screen_recording": True}, ttl=10, clock=lambda: clock[0])
    assert probe() == probe() == {"screen_recording": True} and len(n) == 1
    clock[0] += 11
    probe()
    assert len(n) == 2


# ── the library's character list ────────────────────────────────────────────

def test_show_characters_endpoint_lists_cast_with_photo_urls_and_notes_flag():
    e = Env()
    e.ready_show("s", "S")
    e.db.add_character("s-a", "s", "Alice", actor_name="Actor A", role="main", importance_rank=1)
    e.db.add_character("s-b", "s", "Bob", actor_name="Actor B", importance_rank=2)
    e.db.add_character_arc("s-a", 1, 1, status="alive", current_arc="Does a thing.")
    body = e.client.get("/api/shows/s/characters?all=1").json()
    assert body["with_notes"] == 1 and body["total"] == 2
    a, b = body["characters"]
    assert (a["name"], a["actor"], a["has_notes"]) == ("Alice", "Actor A", True)
    assert a["photo_url"] == "/api/characters/s-a/photo" and b["has_notes"] is False


def test_show_characters_404_for_unknown_or_unfinished_show():
    e = Env()
    e.db.add_show("half", "Half")                       # still ingesting
    assert e.client.get("/api/shows/half/characters").status_code == 404
    assert e.client.get("/api/shows/nope/characters").status_code == 404
