"""Which season holds episode N with a given title? Asks TMDB, season by season.

Pure: the HTTP call is injected (`tmdb_get(path, params) -> dict`, same shape as
api_routes.default_tmdb_get) so tests need no network.
"""
import urllib.error
from typing import Callable, Optional


def find_season(tmdb_get: Callable[[str, dict], dict], tmdb_id: int, episode: int,
                title: str, start_season: int = 1, max_season: int = 30) -> Optional[int]:
    """The season whose episode number is `episode` and whose name matches `title`.

    Tries `start_season` first (the common case costs one request), then 1..max_season.
    Stops after 3 consecutive 404s (past the last season). Any other error gives None.
    """
    wanted = (title or "").strip().lower()
    order = [start_season] + list(range(1, max_season + 1))
    tried: set = set()
    misses = 0
    for season in order:
        if season in tried or season < 1:
            continue
        tried.add(season)
        try:
            data = tmdb_get(f"tv/{tmdb_id}/season/{season}", {})
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return None
            misses += 1
            if misses >= 3:
                return None
            continue
        except Exception:
            return None
        misses = 0
        for ep in (data or {}).get("episodes", []):
            if ep.get("episode_number") != episode:
                continue
            name = str(ep.get("name") or "").lower()
            if not wanted or wanted in name or name in wanted:
                return season
    return None
