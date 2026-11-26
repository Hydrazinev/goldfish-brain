"""The one place CineID talks to Claude.

Both Watch Buddy (screenshot + story context -> a short reaction) and the
character-notes generator (populate_metadata) go through here, so the model
and its settings are chosen once. Default model is Claude Haiku 5.5, the
cheapest current Claude model; override with CINEID_MODEL if ever needed.

Haiku 5.5 rules this module respects: no sampling params (temperature etc.
400), no assistant prefill (400), effort defaults to "medium" so we set "low",
and thinking is disabled (allowed at effort "high" or below) so the whole
token budget goes to the answer.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

DEFAULT_MODEL = "claude-haiku-5-5"
MODEL = DEFAULT_MODEL


class LLMError(RuntimeError):
    """A failure with a message that is safe to show to the user."""


class LLMFormatError(LLMError):
    """The reply was not the JSON object we asked for (e.g. prose)."""


def make_client(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=600.0)


def _thinking_for(model: str) -> Optional[dict]:
    """Thinking is off for these short jobs, but each model family spells that differently:
    Haiku accepts {disabled}; Claude Sonnet 5.5 rejects it (400) and wants {between_tools};
    Opus / Fable always think, so the setting is left out (effort stays low)."""
    if model.startswith("claude-haiku"):
        return {"type": "disabled"}
    if model.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}
    return None


def build_request(prompt: str, *, max_tokens: int,
                  image_b64: Optional[str] = None,
                  media_type: str = "image/jpeg",
                  json_schema: Optional[dict] = None,
                  model: Optional[str] = None) -> dict:
    content: list = []
    if image_b64:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": image_b64}})
    content.append({"type": "text", "text": prompt})
    output_config: dict = {"effort": "low"}
    if json_schema is not None:
        # Structured outputs: the reply is guaranteed to be JSON matching the
        # schema. Without it Haiku answers in prose when it is unsure.
        output_config["format"] = {"type": "json_schema", "schema": json_schema}
    # An explicit per-call model (e.g. Sonnet for "Ask about this scene") wins;
    # otherwise CINEID_MODEL, otherwise the cheap default.
    model = model or os.environ.get("CINEID_MODEL", DEFAULT_MODEL)
    req = {
        "model": model,
        "max_tokens": max_tokens,
        "output_config": output_config,
        "messages": [{"role": "user", "content": content}],
    }
    thinking = _thinking_for(model)
    if thinking is not None:
        req["thinking"] = thinking
    return req


def _call(client, req: dict):
    try:
        return client.messages.create(**req)
    except Exception as exc:
        try:
            import anthropic
        except ImportError:
            raise
        if isinstance(exc, anthropic.AuthenticationError):
            raise LLMError("Your Anthropic API key was rejected. "
                           "Update it in CineID → Settings.") from exc
        if isinstance(exc, anthropic.PermissionDeniedError):
            raise LLMError("Your Anthropic API key doesn't have access to "
                           "this model.") from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise LLMError("Anthropic says too many requests — try again "
                           "in a minute.") from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise LLMError("Couldn't reach Anthropic — check your internet "
                           "connection.") from exc
        raise


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", None) == "text").strip()


def ask(client, prompt: str, *, max_tokens: int,
        image_b64: Optional[str] = None, model: Optional[str] = None) -> str:
    """One prompt (optionally with a JPEG screenshot) -> the reply text."""
    resp = _call(client, build_request(prompt, max_tokens=max_tokens,
                                       image_b64=image_b64, model=model))
    if resp.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")
    text = _text_of(resp)
    if not text:
        raise LLMError("Claude sent back an empty answer.")
    return text


def extract_json(text: str) -> dict:
    """Parse the JSON object in a model reply, tolerating code fences and a
    sentence of chatter around it."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise LLMFormatError("Claude's answer wasn't in the expected format.")
    try:
        data = json.loads(t[start:end + 1])
    except ValueError as exc:
        raise LLMFormatError("Claude's answer wasn't valid JSON.") from exc
    if not isinstance(data, dict):
        raise LLMFormatError("Claude's answer wasn't in the expected format.")
    return data


_RETRY_SUFFIX = ("\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON "
                 "object — no explanation, no other text. If you are unsure of "
                 "something, use an empty string for it instead of explaining.")


def ask_json(client, prompt: str, *, max_tokens: int,
             schema: Optional[dict] = None, model: Optional[str] = None,
             image_b64: Optional[str] = None,
             media_type: str = "image/jpeg") -> dict:
    """Ask for a JSON object. Retries once if the reply is prose."""
    last: Optional[LLMFormatError] = None
    for attempt in range(2):
        text_prompt = prompt if attempt == 0 else prompt + _RETRY_SUFFIX
        resp = _call(client, build_request(text_prompt, max_tokens=max_tokens,
                                           json_schema=schema, model=model,
                                           image_b64=image_b64,
                                           media_type=media_type))
        if resp.stop_reason == "refusal":
            raise LLMError("Claude declined this request.")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off (too long). "
                           "Try again or reduce the episode range.")
        try:
            return extract_json(_text_of(resp))
        except LLMFormatError as exc:
            last = exc
    raise last  # type: ignore[misc]
