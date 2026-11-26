"""Regression tests for the defects found in the full-project bug hunt.

Each test names the user-visible failure it guards against.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import CharacterDB


def _tmp_dir() -> Path:
    return Path(tempfile.mkdtemp())


# ── spoiler shield (server) ─────────────────────────────────────────────────

def _server():
    import server
    return server


def test_shield_hides_character_with_unknown_first_season_and_no_arc():
    """The Sandman: 15/22 characters have NULL first_season and no arc rows.
    They used to pass the shield unconditionally -> spoilers for unseen cast."""
    s = _server()
    char = {"first_season": None, "first_episode": None}
    assert s._passes_spoiler_shield(char, 1, 3, arc=None) is False


def test_shield_allows_unknown_first_season_when_arc_proves_presence():
    s = _server()
    char = {"first_season": None, "first_episode": None}
    arc = {"season": 1, "episode": 2, "status": "alive", "current_arc": "hiding"}
    assert s._passes_spoiler_shield(char, 1, 3, arc=arc) is True


def test_shield_known_first_appearance_still_works():
    s = _server()
    char = {"first_season": 2, "first_episode": 5}
    assert s._passes_spoiler_shield(char, 2, 4) is False
    assert s._passes_spoiler_shield(char, 2, 5) is True
    assert s._passes_spoiler_shield(char, 3, 1) is True
    assert s._passes_spoiler_shield(char, 1, 13) is False


# ── identify result assembly (server) ───────────────────────────────────────

def test_dedupe_keeps_best_match_per_character_and_ranks_by_confidence():
    """Two faces matching the same character produced duplicate popup rows,
    and the popup headlined whichever face the detector found first."""
    s = _server()
    rows = [
        {"character_id": "a", "confidence": 0.55},
        {"character_id": "b", "confidence": 0.91},
        {"character_id": "a", "confidence": 0.72},
    ]
    out = s._dedupe_and_rank(rows)
    assert [r["character_id"] for r in out] == ["b", "a"]
    assert out[1]["confidence"] == 0.72


# ── config + .env handling ──────────────────────────────────────────────────

def test_update_config_is_atomic_and_preserves_other_keys():
    import config_store
    p = _tmp_dir() / "config.json"
    p.write_text(json.dumps({"show_id": "x", "season": 2, "episode": 3}))
    config_store.update_config(p, {"episode": 4})
    cfg = json.loads(p.read_text())
    assert cfg == {"show_id": "x", "season": 2, "episode": 4}
    assert not list(p.parent.glob("*.tmp"))


def test_update_config_refuses_to_clobber_unreadable_file():
    """ensure_buddy_defaults used to overwrite a momentarily unreadable
    config.json with just {"buddy": ...}, wiping show/season/episode."""
    import config_store
    p = _tmp_dir() / "config.json"
    p.write_text('{"show_id": "x", "seas')   # truncated mid-write
    with pytest.raises(config_store.ConfigUnreadable):
        config_store.update_config(p, {"episode": 4})
    assert p.read_text() == '{"show_id": "x", "seas'


def test_ensure_buddy_defaults_does_not_wipe_corrupt_config():
    from buddy import ensure_buddy_defaults
    p = _tmp_dir() / "config.json"
    p.write_text('{"show_id": "x", "seas')
    ensure_buddy_defaults(p)
    assert p.read_text() == '{"show_id": "x", "seas'


def test_ensure_buddy_defaults_keeps_position():
    from buddy import ensure_buddy_defaults
    p = _tmp_dir() / "config.json"
    p.write_text(json.dumps({"show_id": "x", "season": 2, "episode": 3}))
    ensure_buddy_defaults(p)
    cfg = json.loads(p.read_text())
    assert cfg["show_id"] == "x" and cfg["season"] == 2 and "buddy" in cfg


@pytest.mark.parametrize("line,expected", [
    ("OPENAI_API_KEY=sk-abc", "sk-abc"),
    ('OPENAI_API_KEY="sk-abc"', "sk-abc"),
    ("OPENAI_API_KEY='sk-abc'", "sk-abc"),
    ("export OPENAI_API_KEY=sk-abc", "sk-abc"),
    ("OPENAI_API_KEY = sk-abc  # work key", "sk-abc"),
])
def test_env_key_parsing(line, expected):
    """A quoted key used to be sent to OpenAI with the quotes included."""
    import config_store
    p = _tmp_dir() / ".env"
    p.write_text("# comment\nOTHER=1\n" + line + "\n")
    assert config_store.read_env_key(p, "OPENAI_API_KEY") == expected


def test_env_key_missing_returns_empty():
    import config_store
    assert config_store.read_env_key(_tmp_dir() / "nope.env", "X") == ""


# ── watch buddy ─────────────────────────────────────────────────────────────

def test_trigger_mode_defaults_to_audio_like_the_documented_default():
    """On first launch the listener thread read config before the defaults
    were written and silently ran in timer mode."""
    from buddy import _resolve_trigger_mode
    assert _resolve_trigger_mode({}) == "audio"
    assert _resolve_trigger_mode({"buddy": {}}) == "audio"
    assert _resolve_trigger_mode({"buddy": {"trigger_mode": "timer"}}) == "timer"
    assert _resolve_trigger_mode({"buddy": {"trigger_mode": "bogus"}}) == "audio"


def test_prompt_opener_follows_personality():
    """Every persona was forced to open with the 'mac' catchphrases."""
    from buddy import _build_prompt
    hype = _build_prompt("Alex", "hype", "Show", 1, 1, [])
    mac = _build_prompt("Alex", "mac", "Show", 1, 1, [])
    assert "Pausing here" not in hype
    assert "Pausing here" in mac


def test_say_text_cannot_be_parsed_as_a_flag():
    from buddy import _safe_say_text
    assert not _safe_say_text("-v Fred hello").startswith("-")
    assert _safe_say_text("  hello ") == "hello"


# ── database ────────────────────────────────────────────────────────────────

def test_readding_character_preserves_derived_fields():
    """Re-running ingest used INSERT OR REPLACE and wiped first_season,
    status, arc_type and the arc summary that populate_metadata derived."""
    db = CharacterDB(db_path=_tmp_dir() / "t.db")
    db.add_show("s", "S")
    db.add_character("s-a", "s", "A", actor_name="Actor", importance_rank=1)
    db.update_character_first_seen("s-a", 2, 3)
    db._conn.execute("UPDATE characters SET arc_type='danger', status='alive' "
                     "WHERE character_id='s-a'")
    db._conn.commit()
    db.add_character("s-a", "s", "A", actor_name="Actor v2", importance_rank=2)
    c = db.get_character("s-a")
    assert (c["first_season"], c["first_episode"]) == (2, 3)
    assert c["arc_type"] == "danger" and c["status"] == "alive"
    assert c["actor_name"] == "Actor v2" and c["importance_rank"] == 2


def test_get_ready_shows_excludes_unfinished_ingests():
    """The menu listed shows still ingesting/failed; switching to one then
    produced confusing errors."""
    db = CharacterDB(db_path=_tmp_dir() / "t.db")
    db.add_show("ok", "Ok Show")
    db.update_show_status("ok", "ready")
    db.add_show("half", "Half Show")
    db.add_show("bad", "Bad Show")
    db.update_show_status("bad", "failed")
    assert [s["show_id"] for s in db.get_ready_shows()] == ["ok"]


def test_add_show_reingest_resets_status_so_failures_are_not_stuck():
    db = CharacterDB(db_path=_tmp_dir() / "t.db")
    db.add_show("s", "S")
    db.update_show_status("s", "failed")
    db.add_show("s", "S")
    assert db.get_all_shows()[0]["status"] == "ingesting"


# ── ingest ──────────────────────────────────────────────────────────────────

def test_unique_character_ids_when_names_collide():
    """Two cast members both credited 'Self'/'Young Walter' shared one id,
    so one overwrote the other in the DB and the index held duplicate ids."""
    from ingest import _unique_character_id
    seen: set = set()
    a = _unique_character_id("show", "Young Walter", seen)
    b = _unique_character_id("show", "Young Walter", seen)
    c = _unique_character_id("show", "Young Walter", seen)
    assert len({a, b, c}) == 3 and a == "show-young-walter"


# ── stale ingests / slugs ───────────────────────────────────────────────────

def test_fail_stale_ingests_marks_ingesting_failed():
    """App killed mid-setup left the show 'ingesting' forever."""
    db = CharacterDB(db_path=_tmp_dir() / "t.db")
    db.add_show("half", "Half")
    db.add_show("ok", "Ok")
    db.update_show_status("ok", "ready")
    assert db.fail_stale_ingests() == 1
    status = {s["show_id"]: s["status"] for s in db.get_all_shows()}
    assert status == {"half": "failed", "ok": "ready"}


def test_set_show_poster_roundtrip():
    db = CharacterDB(db_path=_tmp_dir() / "t.db")
    db.add_show("s", "S")
    db.set_show_poster("s", "/abc.jpg")
    assert db.get_all_shows()[0]["poster_path"] == "/abc.jpg"


@pytest.mark.parametrize("name", ["Dark/Matter", "Élite", "../../etc", "Ünïcode ⚡ Show"])
def test_slug_is_safe_for_accents_and_slashes(name):
    """Show names become directory names under data/shows/."""
    import re
    from ingest import _slugify
    slug = _slugify(name)
    assert re.fullmatch(r"[a-z0-9-]+", slug), slug
    assert ".." not in slug and "/" not in slug


def test_show_id_falls_back_to_tmdb_id_when_name_has_no_ascii():
    """A non-Latin title slugged to '' and every such show shared one id."""
    from ingest import _show_id
    assert _show_id("進撃の巨人", 1429) == "show-1429"
    assert _show_id("  ", 7) == "show-7"
    assert _show_id("Breaking Bad", 1396) == "breaking-bad"


# ── watch buddy privacy ─────────────────────────────────────────────────────

def _win(owner, number, w, h, layer=0):
    return {"kCGWindowOwnerName": owner, "kCGWindowNumber": number, "kCGWindowLayer": layer,
            "kCGWindowBounds": {"Width": w, "Height": h}}


def test_pick_window_id_prefers_largest_layer0_chrome_window():
    from buddy import _pick_window_id
    wins = [_win("Google Chrome", 1, 400, 300), _win("Google Chrome", 2, 1500, 900),
            _win("Google Chrome", 3, 2000, 40, layer=25),     # menu/overlay layer
            _win("Safari", 4, 3000, 2000)]
    assert _pick_window_id(wins) == 2


def test_pick_window_id_none_when_no_chrome():
    from buddy import _pick_window_id
    assert _pick_window_id([_win("Safari", 4, 800, 600)]) is None
    assert _pick_window_id([]) is None


def _buddy_env(tmp_cfg: dict, monkeypatch):
    import buddy
    d = _tmp_dir()
    cfg = d / "data" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"show_id": "s", "season": 1, "episode": 1, **tmp_cfg}))
    db = CharacterDB(db_path=d / "data" / "t.db")
    db.add_show("s", "Show S")
    db.close()
    calls = {"capture": [], "ask": 0}
    monkeypatch.setattr(buddy, "_capture_b64",
                        lambda window_only=False: calls["capture"].append(window_only) or "QUJD")
    import llm
    monkeypatch.setattr(llm, "make_client", lambda key: object())
    monkeypatch.setattr(llm, "ask", lambda *a, **k: calls.__setitem__("ask", calls["ask"] + 1) or "Quick thought— hmm?")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    return buddy, cfg, d / "data" / "t.db", calls


def test_buddy_message_skipped_without_disclosure_ack(monkeypatch):
    """A screenshot must not leave the machine until the user has been told."""
    buddy, cfg, dbp, calls = _buddy_env({"buddy": {"enabled": True}}, monkeypatch)
    assert buddy._generate_buddy_message(cfg, dbp) is None
    assert calls["capture"] == [] and calls["ask"] == 0


def test_buddy_message_with_ack_shares_only_window_by_default(monkeypatch):
    buddy, cfg, dbp, calls = _buddy_env({"buddy": {"enabled": True, "disclosure_ack": True}}, monkeypatch)
    assert buddy._generate_buddy_message(cfg, dbp) == "Quick thought— hmm?"
    assert calls["capture"] == [True] and calls["ask"] == 1


def test_buddy_message_respects_full_screen_choice(monkeypatch):
    buddy, cfg, dbp, calls = _buddy_env(
        {"buddy": {"enabled": True, "disclosure_ack": True, "share_only_window": False}}, monkeypatch)
    buddy._generate_buddy_message(cfg, dbp)
    assert calls["capture"] == [False]


# ── shows without character notes still identify (name + actor only) ────────

def test_visibility_levels():
    s = _server()
    unknown = {"first_season": None, "first_episode": None}
    arc = {"season": 1, "episode": 2, "status": "alive", "current_arc": "hiding"}
    assert s._visibility(unknown, 1, 3, None) == "limited"
    assert s._visibility(unknown, 1, 3, arc) == "full"
    assert s._visibility({"first_season": 2, "first_episode": 5}, 2, 4, None) == "hidden"
    assert s._visibility({"first_season": 2, "first_episode": 5}, 2, 5, None) == "full"


def test_limited_result_keeps_identity_but_drops_story_details():
    """No notes yet (no Anthropic key, or notes failed): the viewer still learns
    who is on screen, but nothing that could spoil the story is shown."""
    s = _server()
    char = {"character_id": "x-a", "character_name": "A", "actor_name": "Actor", "role": "main",
            "first_season": None, "first_episode": None, "relationship_summary": "Brother of B",
            "importance_rank": 1, "status": "dead", "current_arc": "dies", "last_seen_description": "x"}
    out = s._limited_result(char, 0.71)
    assert out["name"] == "A" and out["actor"] == "Actor" and out["confidence"] == 0.71
    assert out["limited"] is True
    for k in ("status", "arc_type", "current_arc", "last_seen_description",
              "relationship_summary", "first_seen_season", "last_seen_season"):
        assert out[k] is None, k
