"""Structured progress events from the long-running pipeline scripts.

ingest.py / populate_metadata.py call emit(); when the job runner starts them
it sets CINEID_EVENTS=1 and parses these lines instead of scraping log text.
"""
from __future__ import annotations

import json
import os
from typing import Optional

_PREFIX = "@@CINEID "


def emit(event: str, **fields) -> None:
    if os.environ.get("CINEID_EVENTS") != "1":
        return
    print(_PREFIX + json.dumps({"event": event, **fields}, separators=(",", ":")),
          flush=True)


def parse_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line.startswith(_PREFIX):
        return None
    try:
        data = json.loads(line[len(_PREFIX):])
    except ValueError:
        return None
    return data if isinstance(data, dict) and "event" in data else None
