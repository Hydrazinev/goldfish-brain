"""Plain-language text for things that go wrong at the hotkey.

Nothing here ever includes a raw exception, path or traceback."""
from __future__ import annotations

import socket
import subprocess
import urllib.error
from typing import Optional

_WARMUP_SECONDS = 90   # the engine loads its face model for a few seconds at launch


def friendly_error(exc: BaseException, *, server_age_s: float,
                   show: Optional[str] = None) -> str:
    if isinstance(exc, RuntimeError) and "drm_blocked" in str(exc):
        return ("Netflix DRM is blocking the screenshot.\n"
                "In Chrome: Settings → System → turn off “Use hardware acceleration”.")
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return f"{show or 'This show'} hasn't been set up yet — open Goldfish Brain → Add a Show."
        if exc.code >= 500:
            return "Something went wrong on our side. Try again."
        return "Something went wrong. Try again."
    if isinstance(exc, (subprocess.TimeoutExpired, socket.timeout, TimeoutError)):
        return "Taking too long — try again."
    if isinstance(exc, urllib.error.URLError):
        if server_age_s < _WARMUP_SECONDS:
            return "Goldfish Brain is warming up — try again in a few seconds."
        return "Goldfish Brain's engine isn't responding. Quit and reopen Goldfish Brain."
    return "Something went wrong. Try again."
