"""Static files for the web UI, always revalidated.

Without a Cache-Control header the app's web view applied heuristic caching and kept showing an
old app.js after the UI was rebuilt. `no-cache` means "check with the server every time"; the ETag
keeps that cheap (unchanged files answer 304)."""
from __future__ import annotations

from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
