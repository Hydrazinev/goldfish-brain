"""Local-only identification log (data/accuracy_log.jsonl) and its summary.

One JSON line per detected face: timestamp, show, season/episode, top-1 score,
margin to the runner-up, face height in px, matched yes/no, character_id when
matched, and whether the viewer was told "not sure". No image, no names.
Logging never raises: a failed write must not fail an identification.
"""
from __future__ import annotations

import json
import statistics
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedback as _feedback

LOG_NAME = "accuracy_log.jsonl"
_LOCK = threading.Lock()


def face_height(bbox) -> Optional[float]:
    try:
        return round(float(bbox[3]) - float(bbox[1]), 1)
    except (TypeError, ValueError, IndexError):
        return None


def log_faces(data_dir, rows: list[dict], *, show_id: str, season: int, episode: int) -> None:
    """rows: dicts with score, margin, face_h, character_id (None = unmatched), not_sure."""
    try:
        if not rows:
            return
        ts = datetime.now(timezone.utc).isoformat()
        lines = []
        for r in rows:
            cid = r.get("character_id")
            lines.append(json.dumps({
                "ts": ts, "show_id": show_id, "season": season, "episode": episode,
                "score": r.get("score"), "margin": r.get("margin"), "face_h": r.get("face_h"),
                "matched": cid is not None, "character_id": cid,
                "not_sure": bool(r.get("not_sure")),
            }) + "\n")
        path = Path(data_dir) / LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(path, "a", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:       # never fail the request over a log
        pass


def summary(data_dir) -> dict:
    n = matched = not_sure = 0
    scores: list[float] = []
    margins: list[float] = []
    try:
        lines = (Path(data_dir) / LOG_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        n += 1
        matched += bool(row.get("matched"))
        not_sure += bool(row.get("not_sure"))
        if isinstance(row.get("score"), (int, float)):
            scores.append(row["score"])
        if isinstance(row.get("margin"), (int, float)):
            margins.append(row["margin"])
    fb = _feedback.summary(data_dir)
    out = {
        "identifications": n, "matched": matched, "not_sure": not_sure,
        "median_score": round(statistics.median(scores), 3) if scores else None,
        "median_margin": round(statistics.median(margins), 3) if margins else None,
        "feedback": {k: fb[k] for k in ("wrong", "missed", "ok")},
        "reported_wrong_rate": None,
        "reported_wrong_rate_note": (
            "Lower bound: only errors you reported with 'Wrong?' count, and unreported "
            "wrong names are invisible. wrong reports / matched identifications."),
    }
    if matched:
        out["reported_wrong_rate"] = round(min(fb["wrong"] / matched, 1.0), 4)
    return out
