"""Check an API key with one cheap request. Messages are plain English and
never include the key."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

_NET = "Couldn't reach {svc} — check your internet connection."


def _call(url: str, headers: dict, opener: Callable, svc: str,
          rejected: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener(req, timeout=8) as resp:
            if getattr(resp, "status", 200) == 200:
                return True, "Looks good."
            return False, f"{svc} gave an unexpected answer. Try again."
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, rejected
        if exc.code == 429:
            return False, f"{svc} says too many requests — wait a minute and try again."
        return False, f"{svc} had a problem ({exc.code}). Try again in a moment."
    except urllib.error.URLError:
        return False, _NET.format(svc=svc)
    except Exception:
        return False, f"Couldn't check the key with {svc}. Try again."


def validate_tmdb(key: str, opener: Callable = urllib.request.urlopen) -> tuple[bool, str]:
    url = ("https://api.themoviedb.org/3/configuration?"
           + urllib.parse.urlencode({"api_key": key}))
    return _call(url, {"User-Agent": "GoldfishBrain/1.0"}, opener, "TMDB",
                 "TMDB rejected that key — check you copied the API Key (v3 auth), "
                 "not the Read Access Token.")


def validate_anthropic(key: str, opener: Callable = urllib.request.urlopen) -> tuple[bool, str]:
    """Lists models (free, no tokens used) to prove the key works."""
    return _call("https://api.anthropic.com/v1/models",
                 {"x-api-key": key, "anthropic-version": "2023-06-01",
                  "User-Agent": "GoldfishBrain/1.0"},
                 opener, "Anthropic", "Anthropic rejected that key — copy it again "
                 "from console.anthropic.com → API keys.")
