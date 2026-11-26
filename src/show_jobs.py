"""Runs "add a show" (ingest + optional character notes) as a background job
and exposes a plain-language status for the UI."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))
import job_events
from database import CharacterDB
from ingest import _show_id


class JobBusy(RuntimeError):
    """A show is already being set up."""


# Character notes need real knowledge of the show; Haiku left half of a new show's characters
# empty. Notes run once per show, so the stronger model is worth it (CINEID_NOTES_MODEL overrides).
DEFAULT_NOTES_MODEL = "claude-sonnet-5-5"

_FACES_SPAN_WITH_NOTES = 60      # faces fill 0-60 %, notes 60-100 %
_MAX_DETAILS = 400


class ShowSetupRunner:
    def __init__(self, db_path: Path, project_root: Path,
                 env_provider: Callable[[], dict],
                 spawn: Callable = subprocess.Popen,
                 on_ready: Callable[[str], None] = lambda show_id: None) -> None:
        self._db_path = Path(db_path)
        self._root = Path(project_root)
        self._env_provider = env_provider
        self._spawn = spawn
        self._on_ready = on_ready
        self._lock = threading.RLock()
        self._job: Optional[dict] = None
        self._proc = None
        self._cancel = False
        self._thread: Optional[threading.Thread] = None
        self._with_notes = False
        self._facts: dict = {}

    # ── public API ──────────────────────────────────────────────────────────

    def status(self) -> Optional[dict]:
        with self._lock:
            if self._job is None:
                return None
            snap = dict(self._job)
            snap["details"] = list(self._job["details"])
            return snap

    def start(self, show_name: str, tmdb_id: int, poster_path: Optional[str],
              with_notes: bool) -> str:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise JobBusy("Another show is still being set up.")
            show_id = _show_id(show_name, tmdb_id)
            self._with_notes = with_notes
            self._cancel = False
            self._facts = {}
            self._job = {
                "show_id": show_id, "show_name": show_name, "state": "running",
                "step": "cast", "current": 0, "total": 0, "percent": 0,
                "message": "Finding the cast…", "summary": None, "details": [],
            }
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="ShowSetup",
                args=(show_name, tmdb_id, poster_path, show_id))
            self._thread.start()
            return show_id

    def cancel(self) -> None:
        with self._lock:
            if not self._job or self._job["state"] != "running":
                return
            self._cancel = True
            proc = self._proc
        if proc is not None:
            self._terminate(proc)

    @staticmethod
    def _terminate(proc) -> None:
        try:
            if getattr(proc, "pid", 0):
                os.killpg(proc.pid, signal.SIGTERM)   # whole process group
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass

    # ── internals ───────────────────────────────────────────────────────────

    def _env(self, extra: Optional[dict] = None) -> dict:
        env = dict(os.environ)
        env.update(self._env_provider())
        if extra:
            env.update(extra)
        env.update({"CINEID_EVENTS": "1", "TQDM_DISABLE": "1",
                    "PYTHONUNBUFFERED": "1"})
        return env

    def _set(self, **kw) -> None:
        with self._lock:
            self._job.update(kw)

    def _detail(self, line: str) -> None:
        with self._lock:
            d = self._job["details"]
            d.append(line)
            del d[:-_MAX_DETAILS]

    def _percent(self, step: str, current: int, total: int) -> int:
        frac = (current / total) if total else 0.0
        if step == "faces":
            span = _FACES_SPAN_WITH_NOTES if self._with_notes else 100
            return min(span, int(span * frac))
        if step == "notes":
            return 60 + min(40, int(40 * frac))
        return 0

    def _apply_event(self, e: dict) -> None:
        kind = e.get("event")
        if kind == "cast":
            self._set(step="cast", total=int(e.get("total", 0)),
                      message="Finding the cast…")
        elif kind == "faces":
            cur, tot = int(e.get("current", 0)), int(e.get("total", 0))
            self._set(step="faces", current=cur, total=tot,
                      percent=self._percent("faces", cur, tot),
                      message=f"Learning faces — {cur} of {tot}")
        elif kind == "faces_done":
            self._facts["ingested"] = int(e.get("ingested", 0))
            self._facts["skipped"] = int(e.get("skipped", 0))
        elif kind == "notes":
            cur, tot = int(e.get("current", 0)), int(e.get("total", 0))
            self._set(step="notes", current=cur, total=tot,
                      percent=self._percent("notes", cur, tot),
                      message=f"Writing character notes — {cur} of {tot}")
        elif kind == "notes_done":
            self._facts["notes_usable"] = int(e.get("usable", 0))
            self._facts["notes_total"] = int(e.get("total", 0))

    def _run_proc(self, cmd: list, extra_env: Optional[dict] = None) -> int:
        proc = self._spawn(cmd, cwd=str(self._root), env=self._env(extra_env),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, start_new_session=True)
        with self._lock:
            self._proc = proc
            cancelled_early = self._cancel      # cancel() ran before spawn finished
        if cancelled_early:
            self._terminate(proc)
        for line in proc.stdout:
            event = job_events.parse_line(line)
            if event is not None:
                self._apply_event(event)
            elif line.strip():
                self._detail(line.rstrip("\n"))
        rc = proc.wait()
        with self._lock:
            self._proc = None
        return rc

    def _mark_failed_in_db(self, show_id: str) -> None:
        try:
            db = CharacterDB(db_path=self._db_path)
            if db.show_exists(show_id):
                db.update_show_status(show_id, "failed")
            db.close()
        except Exception as exc:                     # never mask the real result
            self._detail(f"(could not update database: {exc})")

    def _finish_failed(self, show_name: str, show_id: str, why: str) -> None:
        self._mark_failed_in_db(show_id)
        self._set(state="failed", step="finishing",
                  message=f"Couldn't finish setting up {show_name}. {why}",
                  summary=None)

    def _run(self, show_name: str, tmdb_id: int, poster_path: Optional[str],
             show_id: str) -> None:
        try:
            py = sys.executable
            rc = self._run_proc([py, str(self._root / "src" / "ingest.py"),
                                 "--show", show_name, "--tmdb-id", str(tmdb_id)])
            if self._cancel:
                self._mark_failed_in_db(show_id)
                self._set(state="cancelled", message="Cancelled.")
                return
            if rc != 0:
                self._finish_failed(show_name, show_id,
                                    "Show details has the technical reason.")
                return
            ingested = self._facts.get("ingested", 0)
            if ingested <= 0:
                self._finish_failed(show_name, show_id,
                                    "No usable faces were found for this show.")
                return

            if poster_path:
                try:
                    db = CharacterDB(db_path=self._db_path)
                    db.set_show_poster(show_id, poster_path)
                    db.close()
                except Exception as exc:
                    self._detail(f"(poster not saved: {exc})")
            self._on_ready(show_id)

            notes_ok = False
            if self._with_notes:
                self._set(step="notes", current=0, percent=60,
                          message="Writing character notes…")
                rc2 = self._run_proc([py, str(self._root / "src" / "populate_metadata.py"),
                                      "--show", show_id, "--all-episodes"],
                                     extra_env={"CINEID_MODEL": os.environ.get(
                                         "CINEID_NOTES_MODEL", DEFAULT_NOTES_MODEL)})
                if self._cancel:
                    # faces are usable; notes were cut short
                    self._set(message="Cancelled before notes finished.")
                notes_ok = rc2 == 0
                if not notes_ok:
                    self._detail("Character notes did not finish.")

            skipped = self._facts.get("skipped", 0)
            total = ingested + skipped
            parts = [f"{ingested} of {total} characters ready."]
            if skipped:
                parts.append(f"{skipped} had no usable photos.")
            if self._with_notes:
                usable = self._facts.get("notes_usable", 0) if notes_ok else 0
                if notes_ok:
                    parts.append(f"{usable} of {ingested} have character notes.")
                else:
                    parts.append("No character notes yet — face recognition still "
                                 "works. You can retry notes later.")
            self._set(state="done", step="finishing", percent=100,
                      message=f"{show_name} is ready.", summary=" ".join(parts))
        except Exception as exc:
            self._detail(f"internal error: {exc}")
            self._finish_failed(show_name, show_id, "Show details has the technical reason.")
