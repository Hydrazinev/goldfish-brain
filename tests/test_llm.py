import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import llm


def _resp(text="hi", stop="end_turn", kind="text"):
    block = SimpleNamespace(type=kind, text=text)
    return SimpleNamespace(content=[block], stop_reason=stop)


class FakeClient:
    def __init__(self, response=None, exc=None):
        self.calls = []
        self._response, self._exc = response or _resp(), exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return self._response


def test_default_model_is_the_cheap_haiku():
    assert llm.MODEL == "claude-haiku-5-5"


def test_request_is_cheap_and_valid_for_haiku():
    req = llm.build_request("Say hi", max_tokens=200)
    assert req["model"] == "claude-haiku-5-5"
    assert req["max_tokens"] == 200
    assert req["output_config"] == {"effort": "low"}
    assert req["thinking"] == {"type": "disabled"}
    # Haiku 5.5 rejects sampling params and assistant prefill with a 400
    assert "temperature" not in req and "top_p" not in req and "top_k" not in req
    assert req["messages"][-1]["role"] == "user"
    assert req["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Say hi"}]}]


def test_image_block_comes_before_text():
    req = llm.build_request("What's on screen?", max_tokens=100, image_b64="QUJD")
    content = req["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg", "data": "QUJD"}}
    assert content[1]["type"] == "text"


def test_model_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("CINEID_MODEL", "claude-sonnet-5-5")
    assert llm.build_request("x", max_tokens=5)["model"] == "claude-sonnet-5-5"


def test_ask_returns_text_and_uses_request():
    c = FakeClient(_resp("  Pausing here— who is that?  "))
    assert llm.ask(c, "p", max_tokens=150, image_b64="QUJD") == "Pausing here— who is that?"
    assert c.calls[0]["model"] == "claude-haiku-5-5"


def test_ask_skips_non_text_blocks():
    c = FakeClient(SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="..."),
                 SimpleNamespace(type="text", text="answer")], stop_reason="end_turn"))
    assert llm.ask(c, "p", max_tokens=50) == "answer"


def test_refusal_raises_plain_error():
    c = FakeClient(_resp("", stop="refusal"))
    with pytest.raises(llm.LLMError) as e:
        llm.ask(c, "p", max_tokens=50)
    assert "declined" in str(e.value)


def test_empty_answer_raises():
    with pytest.raises(llm.LLMError):
        llm.ask(FakeClient(_resp("   ")), "p", max_tokens=50)


def test_bad_api_key_becomes_friendly_error():
    import anthropic, httpx
    exc = anthropic.AuthenticationError(
        "bad", response=httpx.Response(401, request=httpx.Request("POST", "http://x")),
        body=None)
    with pytest.raises(llm.LLMError) as e:
        llm.ask(FakeClient(exc=exc), "p", max_tokens=50)
    assert "API key was rejected" in str(e.value)


# ── JSON extraction ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Here you go:\n{"a": 1}\nHope that helps!',
    '  \n{"a": 1}  ',
])
def test_extract_json_tolerates_fences_and_chatter(text):
    assert llm.extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", '{"a": ', "{'a': 1}"])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(llm.LLMError):
        llm.extract_json(text)


def test_ask_json_parses_object():
    c = FakeClient(_resp('{"status": "alive"}'))
    assert llm.ask_json(c, "p", max_tokens=100) == {"status": "alive"}


def test_ask_json_reports_truncation_instead_of_bad_json():
    """A long episode list cut off at max_tokens must say so, not 'invalid JSON'."""
    c = FakeClient(_resp('{"episodes": {"S01E01": {"status": "al', stop="max_tokens"))
    with pytest.raises(llm.LLMError) as e:
        llm.ask_json(c, "p", max_tokens=100)
    assert "cut off" in str(e.value)


# ── forced JSON (Haiku answered in prose for shows it doesn't know) ─────────

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}},
          "required": ["a"], "additionalProperties": False}


def test_json_schema_adds_structured_output_format():
    req = llm.build_request("x", max_tokens=100, json_schema=SCHEMA)
    assert req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert "format" not in llm.build_request("x", max_tokens=100)["output_config"]


def test_ask_json_passes_schema_to_the_api():
    c = FakeClient(_resp('{"a": "v"}'))
    assert llm.ask_json(c, "p", max_tokens=100, schema=SCHEMA) == {"a": "v"}
    assert c.calls[0]["output_config"]["format"]["schema"] == SCHEMA


def test_ask_json_retries_once_when_the_reply_is_prose():
    """Haiku answers 'I'm not confident…' in prose for shows it doesn't know."""
    class Seq(FakeClient):
        def __init__(self):
            super().__init__()
            self.replies = [_resp("I can help, but I'm not confident about this show."),
                            _resp('{"a": "ok"}')]

        def _create(self, **kw):
            self.calls.append(kw)
            return self.replies.pop(0)
    c = Seq()
    assert llm.ask_json(c, "Describe X", max_tokens=100) == {"a": "ok"}
    assert len(c.calls) == 2
    retry_text = c.calls[1]["messages"][0]["content"][-1]["text"]
    assert "ONLY the JSON" in retry_text and "Describe X" in retry_text


def test_ask_json_gives_up_after_one_retry():
    c = FakeClient(_resp("still prose, sorry"))
    with pytest.raises(llm.LLMError):
        llm.ask_json(c, "p", max_tokens=100)
    assert len(c.calls) == 2


# ── other models need other thinking settings (Sonnet 5.5 rejects thinking "disabled") ──

def test_haiku_thinking_is_disabled():
    assert llm.build_request("x", max_tokens=5)["thinking"] == {"type": "disabled"}


def test_sonnet_5_5_uses_between_tools_not_disabled(monkeypatch):
    """`thinking: {type: disabled}` returns a 400 on Claude Sonnet 5.5."""
    monkeypatch.setenv("CINEID_MODEL", "claude-sonnet-5-5")
    assert llm.build_request("x", max_tokens=5)["thinking"] == {"type": "between_tools"}


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-2"])
def test_models_that_cannot_disable_thinking_omit_the_setting(monkeypatch, model):
    monkeypatch.setenv("CINEID_MODEL", model)
    assert "thinking" not in llm.build_request("x", max_tokens=5)


# ── per-call model + image for ask_json (the "Ask about this scene" feature) ─

def test_build_request_model_argument_beats_default(monkeypatch):
    monkeypatch.delenv("CINEID_MODEL", raising=False)
    req = llm.build_request("x", max_tokens=5, model="claude-sonnet-5-5")
    assert req["model"] == "claude-sonnet-5-5"
    assert req["thinking"] == {"type": "between_tools"}


def test_build_request_default_unchanged_without_model(monkeypatch):
    monkeypatch.delenv("CINEID_MODEL", raising=False)
    assert llm.build_request("x", max_tokens=5)["model"] == "claude-haiku-5-5"


def test_ask_json_passes_model_image_and_media_type():
    c = FakeClient(_resp('{"answer": "a", "refused": false}'))
    out = llm.ask_json(c, "q", max_tokens=300, model="claude-sonnet-5-5",
                       image_b64="QUJD", media_type="image/png",
                       schema={"type": "object"})
    assert out == {"answer": "a", "refused": False}
    call = c.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


def test_ask_accepts_model():
    c = FakeClient(_resp("hello"))
    llm.ask(c, "q", max_tokens=50, model="claude-sonnet-5-5")
    assert c.calls[0]["model"] == "claude-sonnet-5-5"
