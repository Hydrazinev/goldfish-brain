import sys, json, sqlite3, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np


def test_rms_silent():
    from buddy import _rms
    silent = np.zeros(1024, dtype=np.float32)
    assert _rms(silent) < 0.001


def test_rms_loud():
    from buddy import _rms
    loud = np.ones(1024, dtype=np.float32)
    assert _rms(loud) > 0.5


def test_cooldown_allows_first_trigger():
    from buddy import _cooldown_elapsed
    assert _cooldown_elapsed(last_trigger=0.0, cooldown_seconds=180) is True


def test_cooldown_blocks_recent_trigger():
    from buddy import _cooldown_elapsed
    assert _cooldown_elapsed(last_trigger=time.time(), cooldown_seconds=180) is False


def test_cooldown_allows_after_elapsed():
    from buddy import _cooldown_elapsed
    assert _cooldown_elapsed(last_trigger=time.time() - 200, cooldown_seconds=180) is True


def test_effective_cooldown_positive_minutes():
    from buddy import _effective_cooldown
    assert _effective_cooldown(3, "timer") == 180
    assert _effective_cooldown(5, "audio") == 300


def test_effective_cooldown_zero_timer_mode_floors_at_default():
    from buddy import _effective_cooldown, _DEFAULT_TIMER_COOLDOWN_MIN
    # cooldown 0 in timer mode must never mean one GPT call per 30s tick
    assert _effective_cooldown(0, "timer") == _DEFAULT_TIMER_COOLDOWN_MIN * 60


def test_effective_cooldown_zero_audio_mode_uses_retrigger_gap():
    from buddy import _effective_cooldown, _MIN_RETRIGGER_GAP
    assert _effective_cooldown(0, "audio") == _MIN_RETRIGGER_GAP


def test_build_prompt_includes_character():
    from buddy import _build_prompt
    chars = [{"character_name": "Walter White", "arc_type": "danger",
              "current_arc": "He is cooking meth"}]
    prompt = _build_prompt(
        buddy_name="Alex",
        personality="hype",
        show_title="Breaking Bad",
        season=3,
        episode=7,
        characters=chars,
    )
    assert "Walter White" in prompt
    assert "danger" in prompt.lower()
    assert "Season 3, Episode 7" in prompt
    assert "Alex" in prompt
    assert "spoil" in prompt.lower()


def test_build_prompt_personality_hype():
    from buddy import _build_prompt
    prompt = _build_prompt("Alex", "hype", "Breaking Bad", 1, 1, [])
    assert "enthusiastic" in prompt.lower() or "casual" in prompt.lower() or "slang" in prompt.lower()


def test_build_prompt_personality_film_nerd():
    from buddy import _build_prompt
    prompt = _build_prompt("Alex", "film_nerd", "Breaking Bad", 1, 1, [])
    assert "analytical" in prompt.lower() or "theme" in prompt.lower()


def test_build_prompt_personality_chill():
    from buddy import _build_prompt
    prompt = _build_prompt("Alex", "chill", "Breaking Bad", 1, 1, [])
    assert "laid-back" in prompt.lower() or "dry humor" in prompt.lower()


def test_query_episode_characters_spoiler_shield():
    from buddy import _query_episode_characters
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE shows (show_id TEXT PRIMARY KEY, show_name TEXT,
                    tmdb_id INTEGER, total_characters INTEGER, ingested_at TEXT, status TEXT)""")
    conn.execute("""CREATE TABLE characters (character_id TEXT PRIMARY KEY, show_id TEXT,
                    character_name TEXT, actor_name TEXT, role TEXT, first_episode INTEGER,
                    last_episode INTEGER, first_season INTEGER, last_season INTEGER,
                    importance_rank INTEGER, relationship_summary TEXT, image_count INTEGER,
                    confidence_level TEXT, status TEXT, current_arc TEXT,
                    last_seen_description TEXT, arc_type TEXT)""")
    conn.execute("""CREATE TABLE character_arcs (character_id TEXT, season INTEGER,
                    episode INTEGER, status TEXT, current_arc TEXT,
                    last_seen_description TEXT, arc_type TEXT,
                    PRIMARY KEY (character_id, season, episode))""")
    conn.execute("INSERT INTO shows VALUES ('bb','Breaking Bad',1396,2,NULL,'ready')")
    # character introduced S1E1 — should appear
    conn.execute("""INSERT INTO characters VALUES
        ('bb-walter','bb','Walter White','Bryan Cranston','main',1,62,1,5,1,
         'Chemistry teacher','0','high','alive','Cooking meth','Last seen',NULL)""")
    conn.execute("INSERT INTO character_arcs VALUES ('bb-walter',3,7,'alive','He cooks','Last lab',NULL)")
    # character introduced S3E8 — should be hidden (future)
    conn.execute("""INSERT INTO characters VALUES
        ('bb-future','bb','Future Guy','Actor','supporting',8,8,3,3,2,
         'Future guy','0','low','alive','Future arc','Future scene',NULL)""")
    conn.execute("INSERT INTO character_arcs VALUES ('bb-future',3,7,'alive','Future arc','Future',NULL)")
    conn.commit()
    conn.close()

    results = _query_episode_characters(db_path, "bb", season=3, episode=7)
    names = [r["character_name"] for r in results]
    assert "Walter White" in names
    assert "Future Guy" not in names
    Path(db_path).unlink(missing_ok=True)


def test_query_episode_characters_no_character_level_arc_leak():
    """A character with no arc row for the current episode must not fall back
    to the character-level current_arc (whole-show state = spoilers)."""
    from buddy import _query_episode_characters
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE characters (character_id TEXT PRIMARY KEY, show_id TEXT,
                    character_name TEXT, actor_name TEXT, role TEXT, first_episode INTEGER,
                    last_episode INTEGER, first_season INTEGER, last_season INTEGER,
                    importance_rank INTEGER, relationship_summary TEXT, image_count INTEGER,
                    confidence_level TEXT, status TEXT, current_arc TEXT,
                    last_seen_description TEXT, arc_type TEXT)""")
    conn.execute("""CREATE TABLE character_arcs (character_id TEXT, season INTEGER,
                    episode INTEGER, status TEXT, current_arc TEXT,
                    last_seen_description TEXT, arc_type TEXT,
                    PRIMARY KEY (character_id, season, episode))""")
    conn.execute("""INSERT INTO characters VALUES
        ('bb-walter','bb','Walter White','Bryan Cranston','main',1,62,1,5,1,
         'Chemistry teacher','0','high','dead','SPOILER: he dies in the finale','Last seen',NULL)""")
    # earlier arc row exists (S1E2), but nothing for the current episode S1E5
    conn.execute("INSERT INTO character_arcs VALUES ('bb-walter',1,2,'alive','Just diagnosed','Pilot',NULL)")
    # future arc row must never be picked
    conn.execute("INSERT INTO character_arcs VALUES ('bb-walter',5,16,'dead','SPOILER finale arc','Finale',NULL)")
    conn.commit()
    conn.close()

    results = _query_episode_characters(db_path, "bb", season=1, episode=5)
    walter = next(r for r in results if r["character_name"] == "Walter White")
    assert walter["current_arc"] == "Just diagnosed"  # latest row <= S1E5
    assert "SPOILER" not in (walter["current_arc"] or "")
    Path(db_path).unlink(missing_ok=True)


def _make_listener():
    from buddy import BuddyListener
    return BuddyListener(config_path=Path("/nonexistent/config.json"),
                         db_path=Path("/nonexistent/db"), on_message=None)


def _feed_silence(listener, seconds: float):
    """Feed N seconds of silent audio blocks (2048 frames @ 44100 Hz each)."""
    blocks = int(seconds * 44100 / 2048) + 1
    for _ in range(blocks):
        listener._process_block(level=0.0, frames=2048)


def test_silence_arms_one_pending_trigger():
    listener = _make_listener()
    _feed_silence(listener, 2.0)
    assert listener._silence_pending is True


def test_continuous_silence_fires_only_once():
    """The repeated-speaking bug: an idle mic (show audio not reaching it)
    must arm exactly ONE trigger, not one per cooldown window forever."""
    listener = _make_listener()
    _feed_silence(listener, 2.0)
    listener._silence_pending = False   # outer loop consumed it
    _feed_silence(listener, 60.0)       # a full minute more of silence
    assert listener._silence_pending is False


def test_sound_rearms_silence_trigger():
    listener = _make_listener()
    _feed_silence(listener, 2.0)
    listener._silence_pending = False
    listener._process_block(level=0.5, frames=2048)   # sound resumes
    _feed_silence(listener, 2.0)                       # next pause
    assert listener._silence_pending is True


def test_short_silence_does_not_trigger():
    listener = _make_listener()
    _feed_silence(listener, 0.5)   # under the 1.5s threshold
    assert listener._silence_pending is False


def test_ensure_buddy_defaults_writes_defaults():
    from buddy import ensure_buddy_defaults
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"show_id": "bb", "season": 1, "episode": 1}, f)
        cfg_path = Path(f.name)
    try:
        ensure_buddy_defaults(cfg_path)
        result = json.loads(cfg_path.read_text())
        assert "buddy" in result
        assert result["buddy"]["name"] == "Alex"
        assert result["buddy"]["enabled"] is True
    finally:
        cfg_path.unlink(missing_ok=True)


def test_ensure_buddy_defaults_does_not_overwrite():
    from buddy import ensure_buddy_defaults
    existing = {"show_id": "bb", "season": 1, "episode": 1,
                "buddy": {"name": "Sam", "enabled": False}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(existing, f)
        cfg_path = Path(f.name)
    try:
        ensure_buddy_defaults(cfg_path)
        result = json.loads(cfg_path.read_text())
        assert result["buddy"]["name"] == "Sam"
    finally:
        cfg_path.unlink(missing_ok=True)
