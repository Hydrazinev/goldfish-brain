"""API key storage: macOS Keychain first, then environment, then .env.

Keys are stored with the `security` CLI under service "cineid". They are never
logged and never returned by any API endpoint.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config_store

KEYS = ("TMDB_API_KEY", "ANTHROPIC_API_KEY")
_SERVICE = "cineid"


def clean_key(raw: str) -> str:
    """Trim whitespace/newlines and one pair of surrounding quotes."""
    v = (raw or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


def _check(name: str) -> None:
    if name not in KEYS:
        raise ValueError(f"unknown key name: {name}")


def set_key(name: str, value: str, runner=subprocess.run) -> None:
    _check(name)
    value = clean_key(value)
    if not value:
        raise ValueError("empty key")
    runner(["security", "add-generic-password", "-U", "-s", _SERVICE,
            "-a", name, "-w", value],
           capture_output=True, text=True, timeout=10)


def delete_key(name: str, runner=subprocess.run) -> None:
    _check(name)
    runner(["security", "delete-generic-password", "-s", _SERVICE, "-a", name],
           capture_output=True, text=True, timeout=10)


def _from_keychain(name: str, runner) -> str:
    try:
        r = runner(["security", "find-generic-password", "-s", _SERVICE,
                    "-a", name, "-w"],
                   capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return clean_key(r.stdout) if r.returncode == 0 else ""


def get_key(name: str, env_path: Path, runner=subprocess.run) -> str:
    _check(name)
    return (_from_keychain(name, runner)
            or clean_key(os.environ.get(name, ""))
            or clean_key(config_store.read_env_key(env_path, name)))


def export_to_environ(env_path: Path, runner=subprocess.run) -> None:
    """Make stored keys visible to code (and subprocesses) that read os.environ."""
    for name in KEYS:
        value = get_key(name, env_path, runner=runner)
        if value:
            os.environ[name] = value
