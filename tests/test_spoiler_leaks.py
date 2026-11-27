"""Regression tests for the three spoiler leaks found in the Nov 2026 audit:
library cast list / note editor, ask prompt bios, current-episode debuts."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import ask
import relationships
from test_ask import ACK, GUIDE, Env


def pos(season, episode, show="bb"):
    return {"show_id": show, "positions": {show: {"season": season, "episode": episode}},
            "buddy": {"disclosure_ack": True}}


def cast_env(config=None):
    """bb with Jesse (S1E1) whose bio names Jane, introduced S2E8, plus a current-episode debut."""
    e = Env(config=config if config is not None else ACK)
    e.db.add_character("jesse", "bb", "Jesse Pinkman", "Aaron Paul", first_season=1, first_episode=1,
                       importance_rank=2,
                       relationship_summary="Partner of Walter White; Girlfriend of Jane Margolis")
    e.db.add_character("jane", "bb", "Jane Margolis", "Krysten Ritter", first_season=2, first_episode=8,
                       importance_rank=6, relationship_summary="Lover of Jesse Pinkman")
    e.db.add_character("badger", "bb", "Badger Mayhew", "Matt Jones", first_season=1, first_episode=2,
                       importance_rank=7, relationship_summary="Friend of Jesse Pinkman")
    return e


# ── LEAK 2: redact_bio ──────────────────────────────────────────────────────

def test_redact_bio_drops_clauses_naming_hidden_characters():
    bio = "Partner of Walter White; Girlfriend of Jane Margolis"
    assert ask.redact_bio(bio, ["Jane Margolis"]) == "Partner of Walter White"
    assert ask.redact_bio(bio, []) == bio                      # Jane visible: passes through


def test_redact_bio_matches_unique_first_or_last_name_tokens_only():
    assert ask.redact_bio("Dating Jane", ["Jane Margolis"]) == ""
    assert ask.redact_bio("Mentor of margolis.", ["Jane Margolis"]) == ""
    assert ask.redact_bio("Janet is a friend", ["Jane Margolis"]) == "Janet is a friend"   # word boundary
    assert ask.redact_bio("Friend of Ted", ["Ted Lee"]) == ""                               # 3-letter first name now counts
    assert ask.redact_bio("Friend of Al", ["Al Lee"]) == "Friend of Al"                     # 2 letters: too short
    # "White" is shared by two hidden characters, so the bare token proves nothing
    assert ask.redact_bio("Wife of Walter White", ["Skyler White", "Marie White"]) \
        == "Wife of Walter White"
    # ...and a token that belongs to a visible character is never enough
    assert ask.redact_bio("Wife of Walter White", ["Skyler White"], visible_names=["Walter White"]) \
        == "Wife of Walter White"


@pytest.mark.parametrize("word", ["later", "eventually", "Ex-wife", "ex husband", "former partner",
                                  "formerly a teacher", "turns on him", "becomes a kingpin",
                                  "ultimately dies", "finally free"])
def test_redact_bio_drops_arc_hinting_clauses(word):
    assert ask.redact_bio(f"Friend of Walt; {word} of someone", []) == "Friend of Walt"


def test_redact_bio_keeps_ordinary_words_that_contain_the_keywords():
    assert ask.redact_bio("Exterminator; Latera", []) == "Exterminator; Latera"


def test_redact_bio_empty_when_everything_is_dropped():
    assert ask.redact_bio("Ex-girlfriend of Jane Margolis", ["Jane Margolis"]) == ""
    assert ask.redact_bio("", ["Jane Margolis"]) == ""


def test_prompt_redacts_bio_naming_a_character_not_yet_introduced():
    e = cast_env()
    early = ask.build_prompt("q", ask.gather_context(e.db, "bb", 2, 2, 0.3, GUIDE))
    assert "Jesse Pinkman" in early and "Partner of Walter White" in early
    assert "Jane" not in early and "Margolis" not in early
    late = ask.build_prompt("q", ask.gather_context(e.db, "bb", 2, 9, 0.3, GUIDE))
    assert "Girlfriend of Jane Margolis" in late


def test_prompt_forbids_saying_a_relationship_ended_or_changed():
    prompt = ask.build_prompt("q", ask.gather_context(cast_env().db, "bb", 1, 2, 0.3, GUIDE))
    assert "relationship ended or changed" in prompt


def test_hidden_current_episode_debut_also_redacted_from_other_bios():
    e = cast_env()
    e.db.add_character("jesse2", "bb", "Jesse Two", "X", first_season=1, first_episode=1,
                       importance_rank=3, relationship_summary="Partner of Badger Mayhew; friend of Walt")
    early = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, 0.05, GUIDE))
    assert "Badger" not in early and "friend of Walt" in early


# ── LEAK 3: current-episode debuts ──────────────────────────────────────────

def test_ask_character_first_seen_in_current_episode_appears_only_after_reveal_point():
    e = cast_env()
    for progress in (None, 0.05, 0.89):
        p = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, progress, GUIDE))
        assert "Badger" not in p, progress
    assert "Badger Mayhew" in ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, 0.95, GUIDE))
    assert "Badger Mayhew" in ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 3, None, GUIDE))


def test_map_build_graph_hides_current_episode_debuts_unless_progress_is_late():
    e = cast_env()
    chars = e.db.get_show_characters("bb")
    ids = lambda g: {n["id"] for n in g["nodes"]}
    assert "badger" not in ids(relationships.build_graph(chars, 1, 2))
    assert "badger" not in ids(relationships.build_graph(chars, 1, 2, progress=0.05))
    assert "badger" in ids(relationships.build_graph(chars, 1, 2, progress=0.95))
    assert "badger" in ids(relationships.build_graph(chars, 1, 3))
    assert "walt" in ids(relationships.build_graph(chars, 1, 2))     # earlier debuts unaffected


def test_map_endpoint_always_uses_the_strict_rule():
    e = cast_env()
    ids = {n["id"] for n in e.client.get("/api/shows/bb/relationships?season=1&episode=2").json()["nodes"]}
    assert "badger" not in ids and "walt" in ids and "jesse" in ids


def test_identify_style_rule_is_unchanged_in_spoiler_module():
    import spoiler
    c = {"first_season": 1, "first_episode": 2}
    assert spoiler.visibility(c, 1, 2, None) == "full"       # a face on screen still triggers /identify


# ── position clamps ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("season,episode", [(0, 1), (1, 0), (100, 1), (1, 100), (-3, 2)])
def test_relationships_rejects_absurd_positions(season, episode):
    r = cast_env().client.get(f"/api/shows/bb/relationships?season={season}&episode={episode}")
    assert r.status_code == 400
    assert r.json()["error"] and "99" in r.json()["error"]


def test_relationships_accepts_boundary_positions():
    c = cast_env().client
    assert c.get("/api/shows/bb/relationships?season=99&episode=99").status_code == 200
    assert c.get("/api/shows/bb/relationships?season=1&episode=1").status_code == 200


@pytest.mark.parametrize("season,episode", [(100, 1), (1, 100), (0, 1)])
def test_ask_rejects_absurd_positions(season, episode):
    e = cast_env()
    r = e.ask(season=season, episode=episode)
    assert r.status_code == 400 and r.json()["error"]
    assert e.claude.calls == []


# ── LEAK 1: library cast list and note editor ───────────────────────────────

def test_characters_endpoint_hides_later_characters_by_default():
    e = cast_env(pos(1, 2))
    body = e.client.get("/api/shows/bb/characters").json()
    names = {c["name"] for c in body["characters"]}
    assert names == {"Walter White", "Jesse Pinkman", "Badger Mayhew"}
    assert body["hidden_count"] == 2                      # Gale and Jane
    assert body["total"] == 3
    raw = str(body)
    assert "Gale" not in raw and "Jane" not in raw and "Costabile" not in raw and "Ritter" not in raw


def test_characters_endpoint_all_flag_returns_everyone():
    e = cast_env(pos(1, 2))
    body = e.client.get("/api/shows/bb/characters?all=1").json()
    assert {c["name"] for c in body["characters"]} >= {"Gale Boetticher", "Jane Margolis"}
    assert body["total"] == 5 and body["includes_spoilers"] is True


def test_characters_endpoint_follows_the_saved_position_and_defaults_to_1_1():
    e = cast_env(pos(3, 5))
    body = e.client.get("/api/shows/bb/characters").json()
    assert {"Gale Boetticher", "Jane Margolis"} <= {c["name"] for c in body["characters"]}
    assert body["hidden_count"] == 0
    e2 = cast_env({})                                         # no saved position -> S1E1
    body = e2.client.get("/api/shows/bb/characters").json()
    assert {c["name"] for c in body["characters"]} == {"Walter White", "Jesse Pinkman"}
    assert body["hidden_count"] == 3


def test_characters_without_first_appearance_or_notes_count_as_hidden():
    e = cast_env(pos(1, 2))
    e.db.add_character("mystery", "bb", "Mystery Person", "Someone", importance_rank=9)
    body = e.client.get("/api/shows/bb/characters").json()
    assert "Mystery" not in str(body) and body["hidden_count"] == 3


def test_notes_endpoint_403_for_character_not_met_yet():
    e = cast_env(pos(1, 2))
    r = e.client.get("/api/characters/jane/notes")
    assert r.status_code == 403
    j = r.json()
    assert "not met yet" in j["error"] and j["message"] == j["error"]
    assert "Lover of Jesse" not in r.text and "Krysten" not in r.text


def test_notes_endpoint_allows_met_characters_and_the_all_opt_in():
    e = cast_env(pos(1, 2))
    assert e.client.get("/api/characters/jesse/notes").status_code == 200
    r = e.client.get("/api/characters/jane/notes?all=1")
    assert r.status_code == 200 and r.json()["relationship_summary"] == "Lover of Jesse Pinkman"


def test_notes_put_is_unchanged_for_hidden_characters():
    e = cast_env(pos(1, 2))
    r = e.client.put("/api/characters/jane/notes", json={"entries": []})
    assert r.status_code == 200


def test_redact_text_drops_sentences_naming_unmet_characters():
    import ask
    text = "Gus runs the laundry. He is working with Mike Ehrmantraut. Walt is wary of him."
    out = ask.redact_text(text, hidden_names=["Mike Ehrmantraut"], visible_names=["Gus Fring", "Walter White"])
    assert "Mike" not in out and "Ehrmantraut" not in out
    assert "Gus runs the laundry." in out and "Walt is wary of him." in out


def test_redact_text_keeps_everything_when_nobody_is_hidden():
    import ask
    assert ask.redact_text("A. B. C.", hidden_names=[], visible_names=["X"]) == "A. B. C."
