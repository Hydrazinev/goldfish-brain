"""Safe access to data/config.json and .env.

config.json is written by the hotkey thread, the buddy thread and the menu
(main thread). Plain read_text()/write_text() let a reader see a half-written
file and let a "defaults" writer replace an unreadable file with a stub, which
silently wiped the user's show/season/episode. All writes here are atomic
(temp file + rename) and serialised by a process-wide lock.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

_LOCK = threading.RLock()


class ConfigUnreadable(RuntimeError):
    """config.json exists but cannot be parsed; refusing to overwrite it."""


def read_config(path: Path, default: Optional[dict] = None) -> dict:
    """Return the parsed config, or `default` ({}) if missing/unreadable."""
    try:
        with _LOCK:
            return json.loads(Path(path).read_text())
    except Exception:
        return dict(default) if default is not None else {}


def _write_atomic(path: Path, cfg: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_config(path: Path, changes: dict | Callable[[dict], None]) -> dict:
    """Read-modify-write. `changes` is a dict merged at top level, or a
    function that mutates the loaded dict. Raises ConfigUnreadable instead of
    overwriting a file that exists but cannot be parsed."""
    path = Path(path)
    with _LOCK:
        cfg: dict = {}
        if path.exists():
            try:
                cfg = json.loads(path.read_text())
            except Exception as exc:
                raise ConfigUnreadable(f"{path} is not valid JSON: {exc}") from exc
            if not isinstance(cfg, dict):
                raise ConfigUnreadable(f"{path} does not contain a JSON object")
        if callable(changes):
            changes(cfg)
        else:
            cfg.update(changes)
        _write_atomic(path, cfg)
        return cfg


def read_env_key(env_path: Path, key: str) -> str:
    """Value of `key` from a .env file ('' if absent). Handles `export `,
    spaces around '=', single/double quotes and trailing ` # comments`."""
    try:
        text = Path(env_path).read_text()
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, val = line.partition("=")
        if not sep or name.strip() != key:
            continue
        val = val.strip()
        if val[:1] in ("'", '"'):
            end = val.find(val[0], 1)
            return val[1:end] if end != -1 else val[1:]
        return val.split(" #", 1)[0].strip()
    return ""


def positions(cfg: dict) -> dict:
    """show_id -> {season, episode} remembered per show."""
    p = cfg.get("positions")
    return p if isinstance(p, dict) else {}


def set_position(path: Path, show_id: str, season: int, episode: int) -> dict:
    """Set the viewing position for `show_id` (and make it the current one)."""
    if int(season) < 1 or int(episode) < 1:
        raise ValueError("season and episode must be 1 or higher")

    def _apply(cfg: dict) -> None:
        cfg["show_id"] = show_id
        cfg["season"], cfg["episode"] = int(season), int(episode)
        pos = dict(positions(cfg))
        pos[show_id] = {"season": int(season), "episode": int(episode)}
        cfg["positions"] = pos

    return update_config(path, _apply)


def select_show(path: Path, show_id: str) -> dict:
    """Make `show_id` current, remembering where we were in the previous show
    and restoring this show's last position (S01E01 if it has none)."""
    def _apply(cfg: dict) -> None:
        pos = dict(positions(cfg))
        prev = cfg.get("show_id")
        if prev and isinstance(cfg.get("season"), int) and isinstance(cfg.get("episode"), int):
            pos[prev] = {"season": cfg["season"], "episode": cfg["episode"]}
        target = pos.get(show_id) or {"season": 1, "episode": 1}
        cfg["show_id"] = show_id
        cfg["season"], cfg["episode"] = target["season"], target["episode"]
        cfg["positions"] = pos

    return update_config(path, _apply)
