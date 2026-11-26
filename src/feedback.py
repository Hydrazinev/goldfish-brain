"""Local-only "that's wrong / you missed someone / that's right" log.

One JSON line per report in data/feedback/feedback.jsonl; for wrong/missed the
last captured frame is copied next to it so recognition accuracy can be
measured later. Nothing leaves the machine.
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

KINDS = ("wrong", "missed", "ok")
MAX_NOTE = 300
_LOCK = threading.Lock()


def record(data_dir: Path, *, show_id: str, kind: str, season: int, episode: int,
           character_id: Optional[str] = None, note: Optional[str] = None) -> dict:
    """Append one report. Returns the stored row. A missing/failed screenshot
    copy never fails the report."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(data_dir) / "feedback"
    folder.mkdir(parents=True, exist_ok=True)
    row = {"ts": now.isoformat(), "show_id": show_id, "kind": kind,
           "season": season, "episode": episode,
           "character_id": character_id or None, "note": (note or "").strip() or None,
           "image": None}
    if kind != "ok":
        src = Path(data_dir) / "last_capture.jpg"
        try:
            if src.is_file():
                shutil.copyfile(src, folder / f"{stamp}.jpg")
                row["image"] = f"{stamp}.jpg"
        except OSError:
            row["image"] = None
    with _LOCK, open(folder / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def summary(data_dir: Path) -> dict:
    counts = {"total": 0, **{k: 0 for k in KINDS}}
    try:
        lines = (Path(data_dir) / "feedback" / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return counts
    for line in lines:
        try:
            kind = json.loads(line).get("kind")
        except (ValueError, AttributeError):
            continue
        if kind in KINDS:
            counts[kind] += 1
            counts["total"] += 1
    return counts
