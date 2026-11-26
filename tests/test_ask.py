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
import ask
import llm
from database import CharacterDB

JPEG_B64 = "/9j/4AAQSkZJRgABAQ=="        # starts with the JPEG magic bytes


class FakeKeys:
    KEYS = ("TMDB_API_KEY", "ANTHROPIC_API_KEY")

    def __init__(self, **k):
        self.store = k

    def get_key(self, name, env_path, runner=None):
        return self.store.get(name, "")


class FakeClaude:
    def __init__(self, reply=None, exc=None):
        self.calls = []
        self.reply = reply if reply is not None else {"answer": "Skyler is Walt's wife.", "refused": False}
        self.exc = exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        block = SimpleNamespace(type="text", text=json.dumps(self.reply))
        return SimpleNamespace(content=[block], stop_reason="end_turn")


GUIDE = [
    {"season": 1, "episode": 1, "name": "Pilot", "overview": "Walt gets a diagnosis."},
    {"season": 1, "episode": 2, "name": "Cat's in the Bag", "overview": "Walt and Jesse dispose of bodies."},
    {"season": 2, "episode": 1, "name": "Seven Thirty-Seven", "overview": "SECOND SEASON OPENER."},
    {"season": 2, "episode": 2, "name": "Grilled", "overview": "FUTURE EPISODE TWO."},
]


class Env:
    def __init__(self, keys=None, config=None, claude=None, guide=None):
        d = Path(tempfile.mkdtemp())
        self.config_path = d / "config.json"
        if config is not None:
            self.config_path.write_text(json.dumps(config))
        self.db = CharacterDB(db_path=d / "t.db")
        self.claude = claude or FakeClaude()
        self.guide_calls = []

        def _guide(tmdb_id, key):
            self.guide_calls.append(tmdb_id)
            if guide == "boom":
                raise RuntimeError("TMDB down")
            return GUIDE if guide is None else guide

        deps = api_routes.ApiDeps(
            config_path=self.config_path, env_path=d / ".env", get_db=lambda: self.db,
            keystore=FakeKeys(**({"ANTHROPIC_API_KEY": "sk-x", "TMDB_API_KEY": "t"} if keys is None else keys)),
            key_check=None, runner=None, tmdb_get=lambda p, q: {},
            permissions=lambda: {}, make_llm=lambda key: self.claude, episode_guide=_guide)
        app = FastAPI()
        api_routes.install(app, deps)
        self.client = TestClient(app)
        self._seed()

    def _seed(self):
        db = self.db
        db.add_show("bb", "Breaking Bad", tmdb_id=1396)
        db.update_show_status("bb", "ready")
        db.add_character("walt", "bb", "Walter White", "Bryan Cranston", first_season=1, first_episode=1,
                         importance_rank=1, relationship_summary="Chemistry teacher turned cook")
        db.add_character("gale", "bb", "Gale Boetticher", "David Costabile", first_season=3, first_episode=5,
                         importance_rank=5, relationship_summary="SECRET LATER LAB ASSISTANT")
        db.add_character_arc("walt", 1, 1, status="alive", current_arc="Diagnosed with cancer", arc_type="turning_point")
        db.add_character_arc("walt", 1, 2, status="alive", current_arc="Disposing of the bodies")
        db.add_character_arc("gale", 3, 5, status="alive", current_arc="GALE FUTURE ARC")

    def ask(self, **over):
        body = {"question": "Who is that?", "image_b64": JPEG_B64, "show_id": "bb",
                "season": 1, "episode": 2, "progress": 0.3}
        body.update(over)
        body = {k: v for k, v in body.items() if v is not ...}
        return self.client.post("/api/ask", json=body)


ACK = {"buddy": {"disclosure_ack": True}}


# ── prompt (pure) ───────────────────────────────────────────────────────────

def test_prompt_has_only_introduced_characters_and_hides_the_rest():
    e = Env(config=ACK)
    ctx = ask.gather_context(e.db, "bb", 1, 2, 0.3, GUIDE)
    prompt = ask.build_prompt("Who is that?", ctx)
    assert "Walter White" in prompt
    assert "Gale" not in prompt and "GALE" not in prompt and "SECRET" not in prompt


def test_prompt_notes_are_as_of_start_of_episode_until_90_percent():
    e = Env(config=ACK)
    early = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, 0.3, GUIDE))
    assert "Diagnosed with cancer" in early            # end of S1E1
    assert "Disposing of the bodies" not in early      # this episode's own notes
    unknown = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, None, GUIDE))
    assert "Disposing of the bodies" not in unknown
    late = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, 0.95, GUIDE))
    assert "Disposing of the bodies" in late


def test_prompt_summaries_are_strictly_before_current_episode():
    e = Env(config=ACK)
    prompt = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, 0.3, GUIDE))
    assert "Walt gets a diagnosis" in prompt                  # S1E1
    assert "dispose of bodies" not in prompt                  # S1E2 is the current episode
    assert "SECOND SEASON" not in prompt and "FUTURE" not in prompt


def test_prompt_contains_the_hard_rules_and_position():
    e = Env(config=ACK)
    prompt = ask.build_prompt("What happened?", ask.gather_context(e.db, "bb", 1, 2, 0.3, GUIDE))
    low = prompt.lower()
    assert "season 1, episode 2" in low
    assert "only" in low and "spoil" in low and "60 words" in low
    assert "What happened?" in prompt


def test_prompt_without_guide_still_works():
    e = Env(config=ACK)
    prompt = ask.build_prompt("q", ask.gather_context(e.db, "bb", 1, 2, 0.3, None))
    assert "Walter White" in prompt


def test_safe_up_to_label():
    assert ask.safe_up_to(3, 7) == "S3E7"


# ── endpoint ────────────────────────────────────────────────────────────────

def test_ask_returns_answer_and_uses_sonnet_with_the_screenshot():
    e = Env(config=ACK)
    r = e.ask()
    assert r.status_code == 200
    assert r.json() == {"answer": "Skyler is Walt's wife.", "safe_up_to": "S1E2", "refused": False}
    call = e.claude.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["messages"][0]["content"][0]["type"] == "image"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert e.guide_calls == [1396]


def test_ask_refusal_is_passed_through():
    e = Env(config=ACK, claude=FakeClaude({"answer": "I can't answer that without spoiling it.", "refused": True}))
    body = e.ask().json()
    assert body["refused"] is True and "spoiling" in body["answer"]


def test_ask_without_image_needs_no_disclosure():
    e = Env(config={})
    r = e.ask(image_b64=...)
    assert r.status_code == 200
    assert e.claude.calls[0]["messages"][0]["content"][0]["type"] == "text"


def test_ask_with_image_needs_disclosure_ack():
    e = Env(config={"buddy": {"disclosure_ack": False}})
    r = e.ask()
    assert r.status_code == 403 and r.json()["error"] == "needs_disclosure"
    assert e.claude.calls == []
    assert Env(config=None).ask().status_code == 403      # no config file at all


def test_ask_without_anthropic_key_is_a_clear_message():
    e = Env(keys={}, config=ACK)
    r = e.ask()
    assert r.status_code == 400
    assert "Anthropic key" in r.json()["error"]


@pytest.mark.parametrize("q", ["", "   ", "x" * 501])
def test_ask_rejects_bad_questions(q):
    e = Env(config=ACK)
    r = e.ask(question=q)
    assert r.status_code == 400 and r.json()["error"]
    assert e.claude.calls == []


def test_ask_rejects_non_image_payload():
    e = Env(config=ACK)
    r = e.ask(image_b64="aGVsbG8=")      # "hello"
    assert r.status_code == 400


def test_ask_unknown_show_is_404():
    r = Env(config=ACK).ask(show_id="nope")
    assert r.status_code == 404 and r.json()["error"]


def test_ask_survives_tmdb_failure():
    e = Env(config=ACK, guide="boom")
    r = e.ask()
    assert r.status_code == 200
    assert "Walt gets a diagnosis" not in e.claude.calls[0]["messages"][0]["content"][-1]["text"]


def test_ask_llm_error_becomes_plain_message():
    import anthropic
    exc = llm.LLMError("Anthropic says too many requests — try again in a minute.")
    e = Env(config=ACK, claude=FakeClaude(exc=exc))
    r = e.ask()
    assert r.status_code == 502
    assert r.json()["error"].startswith("Anthropic says")


def test_ask_unexpected_error_does_not_leak_question_or_trace():
    e = Env(config=ACK, claude=FakeClaude(exc=ValueError("boom with SECRETQ")))
    r = e.ask(question="SECRETQ please")
    assert r.status_code == 500
    assert "SECRETQ" not in r.text and "boom" not in r.text


def test_ask_does_not_log_question_or_image(caplog, capsys):
    e = Env(config=ACK)
    with caplog.at_level("DEBUG"):
        e.ask(question="UNIQUEQUESTION42")
    out = capsys.readouterr()
    assert "UNIQUEQUESTION42" not in caplog.text + out.out + out.err
    assert JPEG_B64 not in caplog.text + out.out + out.err


def test_ask_progress_is_optional_and_clamped():
    e = Env(config=ACK)
    assert e.ask(progress=None).status_code == 200
    assert e.ask(progress=7).status_code == 200
