import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import populate_metadata as pm


def test_episode_list_becomes_the_dict_keyed_by_code():
    """Structured output needs a fixed-shape array; the rest of populate expects {code: meta}."""
    data = {"relationship_summary": "Wife of X", "first_appearance": "S01E02",
            "episodes": [
                {"code": "S01E02", "status": "alive", "arc_type": "discovery",
                 "current_arc": "Arrives.", "last_seen_description": "Waves."},
                {"code": "s01e03", "status": "unknown", "arc_type": "other",
                 "current_arc": "", "last_seen_description": ""},
                {"code": "garbage", "status": "alive", "arc_type": "other",
                 "current_arc": "x", "last_seen_description": "y"}]}
    out = pm._normalise_episodes(data)
    assert list(out["episodes"]) == ["S01E02", "S01E03"]
    assert out["episodes"]["S01E02"]["current_arc"] == "Arrives."
    assert out["relationship_summary"] == "Wife of X" and out["first_appearance"] == "S01E02"


def test_old_dict_shape_still_accepted():
    data = {"episodes": {"S01E01": {"status": "alive"}}}
    assert pm._normalise_episodes(data)["episodes"] == {"S01E01": {"status": "alive"}}


def test_missing_episodes_key_is_empty_not_a_crash():
    assert pm._normalise_episodes({"relationship_summary": "x"})["episodes"] == {}


def test_prompts_tell_the_model_not_to_guess_and_ground_it_in_the_show_summary():
    p = pm._arc_prompt("FROM", "Tabitha", "Actor", "main", [(1, 1), (1, 2)],
                       show_overview="A family is trapped in a town.")
    assert "A family is trapped in a town." in p
    assert "do not invent" in p.lower() and "S01E01, S01E02" in p


# ── ground the notes in TMDB's episode summaries ────────────────────────────

GUIDE = [
    {"season": 1, "episode": 1, "name": "Long Day's Journey", "overview": "A family's road trip ends in a strange town."},
    {"season": 1, "episode": 2, "name": "Choosing Day", "overview": "x" * 500},
]


def test_guide_text_lists_each_episode_with_title_and_short_summary():
    text = pm._format_guide(GUIDE)
    assert 'S01E01 "Long Day\'s Journey": A family\'s road trip ends in a strange town.' in text
    line2 = [l for l in text.splitlines() if l.startswith("S01E02")][0]
    assert len(line2) < 300          # long summaries are cut


def test_guide_text_is_capped_for_very_long_shows():
    big = [{"season": 1, "episode": i, "name": f"E{i}", "overview": "y" * 200} for i in range(1, 400)]
    assert len(pm._format_guide(big)) <= 20000


def test_prompt_includes_the_episode_guide_and_tells_the_model_to_use_it():
    p = pm._arc_prompt("FROM", "Julie", "Actor", "main", [(1, 1), (1, 2)], "", guide=GUIDE)
    assert "A family's road trip ends in a strange town." in p
    assert "episode guide" in p.lower() and "do not invent" in p.lower()


def test_prompt_without_a_guide_still_works():
    assert "Episodes: S01E01" in pm._arc_prompt("FROM", "Julie", "Actor", "main", [(1, 1)], "")


def test_fetching_the_guide_collects_titles_and_summaries(monkeypatch):
    def fake_get(path, key, **params):
        if path == "/tv/9":
            return {"seasons": [{"season_number": 0}, {"season_number": 1}]}
        assert path == "/tv/9/season/1"
        return {"episodes": [{"episode_number": 1, "name": "Pilot", "overview": "It begins."},
                             {"episode_number": 2, "name": "Two", "overview": None}]}
    monkeypatch.setattr(pm, "_tmdb_get", fake_get)
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    guide = pm._get_episode_guide(9, "k")
    assert guide == [{"season": 1, "episode": 1, "name": "Pilot", "overview": "It begins."},
                     {"season": 1, "episode": 2, "name": "Two", "overview": ""}]
