import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import key_check
import keystore


class FakeSecurity:
    """Stands in for the `security` CLI, recording calls."""

    def __init__(self):
        self.store: dict = {}
        self.calls: list = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        sub, name = cmd[1], cmd[cmd.index("-a") + 1]
        if sub == "add-generic-password":
            self.store[name] = cmd[cmd.index("-w") + 1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if sub == "find-generic-password":
            if name in self.store:
                return subprocess.CompletedProcess(cmd, 0, self.store[name] + "\n", "")
            return subprocess.CompletedProcess(cmd, 44, "", "not found")
        if sub == "delete-generic-password":
            self.store.pop(name, None)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)


def _env(text: str = "") -> Path:
    p = Path(tempfile.mkdtemp()) / ".env"
    p.write_text(text)
    return p


def test_clean_key_strips_whitespace_and_quotes():
    assert keystore.clean_key(' "sk-abc"\n') == "sk-abc"
    assert keystore.clean_key("  tmdb123  ") == "tmdb123"


def test_set_then_get_uses_keychain():
    fake = FakeSecurity()
    keystore.set_key("TMDB_API_KEY", "  abc123\n", runner=fake)
    assert fake.store["TMDB_API_KEY"] == "abc123"
    assert keystore.get_key("TMDB_API_KEY", _env(), runner=fake) == "abc123"
    add = next(c for c in fake.calls if c[1] == "add-generic-password")
    assert "-U" in add and add[add.index("-s") + 1] == "cineid"


def test_get_key_falls_back_to_env_file(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake = FakeSecurity()
    assert keystore.get_key("ANTHROPIC_API_KEY", _env('ANTHROPIC_API_KEY="sk-ant-file"\n'),
                            runner=fake) == "sk-ant-file"


def test_get_key_missing_everywhere_is_empty(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    assert keystore.get_key("TMDB_API_KEY", _env(), runner=FakeSecurity()) == ""


def test_unknown_key_name_rejected():
    import pytest
    with pytest.raises(ValueError):
        keystore.set_key("AWS_SECRET", "x", runner=FakeSecurity())


def test_delete_key_removes_it():
    fake = FakeSecurity()
    keystore.set_key("TMDB_API_KEY", "abc", runner=fake)
    keystore.delete_key("TMDB_API_KEY", runner=fake)
    assert "TMDB_API_KEY" not in fake.store


def test_export_to_environ_sets_found_keys(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake = FakeSecurity()
    keystore.set_key("TMDB_API_KEY", "tm", runner=fake)
    keystore.export_to_environ(_env(), runner=fake)
    import os
    assert os.environ["TMDB_API_KEY"] == "tm"
    assert "ANTHROPIC_API_KEY" not in os.environ


# ── validation ──────────────────────────────────────────────────────────────

class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def _raiser(exc):
    def opener(*a, **k):
        raise exc
    return opener


def _http(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def test_validate_tmdb_ok():
    ok, msg = key_check.validate_tmdb("k", opener=lambda *a, **k: _Resp())
    assert ok is True


def test_validate_tmdb_401_message():
    ok, msg = key_check.validate_tmdb("k", opener=_raiser(_http(401)))
    assert ok is False and "rejected" in msg and "v3" in msg


def test_validate_tmdb_network_error_message():
    ok, msg = key_check.validate_tmdb("k", opener=_raiser(urllib.error.URLError("down")))
    assert ok is False and "internet" in msg


def test_validate_anthropic_ok_and_401():
    seen = {}

    def opener(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    assert key_check.validate_anthropic("sk-ant-x", opener=opener)[0] is True
    assert seen["url"] == "https://api.anthropic.com/v1/models"
    assert seen["headers"]["x-api-key"] == "sk-ant-x"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    ok, msg = key_check.validate_anthropic("k", opener=_raiser(_http(401)))
    assert ok is False and "Anthropic rejected that key" in msg


def test_key_never_appears_in_messages():
    secret = "sk-SECRET-123"
    for fn in (key_check.validate_tmdb, key_check.validate_anthropic):
        for exc in (_http(401), _http(500), urllib.error.URLError("boom")):
            _, msg = fn(secret, opener=_raiser(exc))
            assert secret not in msg
