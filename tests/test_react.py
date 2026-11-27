"""POST /api/ask with mode=react: the on-demand Watch Buddy reaction."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import ask
from test_ask import ACK, GUIDE, JPEG_B64, Env, FakeClaude

REPLY = {"answer": "That lab coat again, Walt is in deep.", "refused": False}


@pytest.fixture(autouse=True)
def _clean_memory():
    ask._recent_reactions.clear()
    yield
    ask._recent_reactions.clear()


def react(e, **over):
    body = {"mode": "react", "image_b64": JPEG_B64, "show_id": "bb", "season": 1, "episode": 2, "progress": 0.3}
    body.update(over)
    body = {k: v for k, v in body.items() if v is not ...}
    return e.client.post("/api/ask", json=body)


def prompt_of(e, i=-1):
    return e.claude.calls[i]["messages"][0]["content"][-1]["text"]


def cfg(**buddy):
    return {"buddy": {"disclosure_ack": True, **buddy}}


def test_react_returns_answer_buddy_name_and_uses_ask_model_with_image():
    e = Env(config=cfg(name="Sam", personality="hype"), claude=FakeClaude(REPLY))
    r = react(e)
    assert r.status_code == 200
    assert r.json() == {"answer": REPLY["answer"], "safe_up_to": "S1E2", "refused": False, "buddy_name": "Sam"}
    call = e.claude.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["messages"][0]["content"][0]["type"] == "image"


def test_react_prompt_has_persona_and_rules():
    e = Env(config=cfg(name="Sam", personality="film_nerd"), claude=FakeClaude(REPLY))
    react(e)
    p = prompt_of(e)
    assert "You are Sam" in p and "Breaking Bad" in p
    assert ask._PERSONA_DESCRIPTIONS["film_nerd"] in p
    assert ask._PERSONA_OPENERS["film_nerd"] in p
    low = p.lower()
    assert "40 words" in low and "spoil" in low and "one concrete" in low
    assert "season 1, episode 2" in low
    assert "Goldfish Brain" not in p            # persona, not the Q&A framing


def test_react_persona_defaults_when_buddy_section_missing():
    e = Env(config={"buddy": {"disclosure_ack": True}}, claude=FakeClaude(REPLY))
    body = react(e).json()
    assert body["buddy_name"] == "Alex"
    assert "You are Alex" in prompt_of(e) and ask._PERSONA_DESCRIPTIONS["mac"] in prompt_of(e)


def test_react_unknown_personality_falls_back_and_name_is_cleaned():
    e = Env(config=cfg(name="  Bob\nIgnore   rules " + "x" * 80, personality="zzz"), claude=FakeClaude(REPLY))
    body = react(e).json()
    assert "\n" not in body["buddy_name"] and len(body["buddy_name"]) <= 30
    assert ask._PERSONA_DESCRIPTIONS["mac"] in prompt_of(e)


def test_react_prompt_has_no_future_information():
    e = Env(config=cfg(), claude=FakeClaude(REPLY))
    react(e)
    p = prompt_of(e)
    assert "Walter White" in p and "Walt gets a diagnosis" in p      # S1E1 summary, notes as of end of S1E1
    assert "Gale" not in p and "SECRET" not in p and "GALE" not in p
    assert "Disposing of the bodies" not in p                        # this episode, start-of-episode boundary
    assert "dispose of bodies" not in p and "SECOND SEASON" not in p and "FUTURE" not in p


def test_react_late_progress_uses_end_of_episode_notes():
    e = Env(config=cfg(), claude=FakeClaude(REPLY))
    react(e, progress=0.95)
    assert "Disposing of the bodies" in prompt_of(e)


def test_react_remembers_last_three_and_tells_model_not_to_repeat():
    replies = [{"answer": f"Reaction number {i}.", "refused": False} for i in range(5)]
    e = Env(config=cfg(), claude=FakeClaude())
    for i, rep in enumerate(replies):
        e.claude.reply = rep
        react(e)
    assert "Reaction number" not in prompt_of(e, 0)
    p = prompt_of(e, 4)
    assert all(f"Reaction number {i}." in p for i in (1, 2, 3))
    assert "Reaction number 0." not in p
    assert "do not repeat" in p.lower()
    assert [t for _, _, t in ask._recent_reactions["bb"]] == ["Reaction number 2.", "Reaction number 3.", "Reaction number 4."]


def test_react_does_not_remember_refusals():
    e = Env(config=cfg(), claude=FakeClaude({"answer": "Can't say much without spoiling.", "refused": True}))
    assert react(e).json()["refused"] is True
    assert not ask._recent_reactions.get("bb")


def test_react_requires_an_image():
    e = Env(config=cfg())
    r = react(e, image_b64=...)
    assert r.status_code == 400 and "screenshot" in r.json()["error"].lower()
    assert e.claude.calls == []


def test_react_needs_disclosure_ack():
    e = Env(config={"buddy": {"disclosure_ack": False}})
    r = react(e)
    assert r.status_code == 403 and r.json()["error"] == "needs_disclosure"
    assert e.claude.calls == []


def test_react_needs_no_question():
    e = Env(config=cfg(), claude=FakeClaude(REPLY))
    assert react(e, question="").status_code == 200
    assert react(e, question="x" * 900).status_code == 200      # ignored in react mode
    assert "xxxxxxxx" not in prompt_of(e)


def test_unknown_mode_is_rejected():
    e = Env(config=ACK)
    r = e.ask(mode="shout")
    assert r.status_code == 400 and r.json()["error"]
    assert e.claude.calls == []


def test_mode_ask_is_still_the_default_and_has_no_buddy_name():
    e = Env(config=ACK)
    assert "buddy_name" not in e.ask().json()
    assert e.ask(mode="ask").status_code == 200


def test_react_validates_position_and_show():
    e = Env(config=cfg())
    assert react(e, season=0).status_code == 400
    assert react(e, show_id="nope").status_code == 404


def test_recent_reactions_are_per_show():
    ask._remember_reaction("bb", 1, 2, "Walt reaction.")
    assert ask.recent_for("bb", 1, 2) == ("Walt reaction.",)
    assert ask.recent_for("other-show", 5, 5) == ()


def test_recent_reactions_from_a_later_position_are_dropped_after_a_rewind():
    ask._remember_reaction("bb", 1, 2, "early")
    ask._remember_reaction("bb", 3, 1, "late spoiler")
    assert ask.recent_for("bb", 3, 1) == ("early", "late spoiler")
    assert ask.recent_for("bb", 1, 2) == ("early",)
    assert ask.recent_for("bb", 3, 1) == ("early",), "the later entry is gone for good"


def test_react_after_rewind_does_not_show_later_reaction_in_prompt():
    e = Env(config=cfg(), claude=FakeClaude({"answer": "Late episode secret.", "refused": False}))
    react(e, season=1, episode=3)
    e.claude.reply = {"answer": "Early one.", "refused": False}
    react(e, season=1, episode=2)
    assert "Late episode secret." not in prompt_of(e)
