import json
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import job_events
from database import CharacterDB
from show_jobs import JobBusy, ShowSetupRunner


def ev(event, **kw):
    return "@@CINEID " + json.dumps({"event": event, **kw}) + "\n"


class FakeProc:
    def __init__(self, lines, rc=0, hold=None):
        self.stdout = iter(lines)
        self.returncode = None
        self._rc = rc
        self._hold = hold          # threading.Event: block until set
        self.terminated = False
        self.pid = 0

    def wait(self, timeout=None):
        if self._hold is not None:
            self._hold.wait(5)
        self.returncode = -15 if self.terminated else self._rc
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if self._hold is not None:
            self._hold.set()


class Harness:
    def __init__(self, procs, ready=None):
        self.dir = Path(tempfile.mkdtemp())
        self.db_path = self.dir / "t.db"
        self.procs = list(procs)
        self.cmds = []
        self.ready_calls = []
        self.snapshots = []

        def spawn(cmd, **kw):
            self.cmds.append((cmd, kw))
            return self.procs.pop(0)

        self.runner = ShowSetupRunner(
            db_path=self.db_path, project_root=self.dir,
            env_provider=lambda: {"TMDB_API_KEY": "k"},
            spawn=spawn, on_ready=self.ready_calls.append)

    def wait(self):
        for _ in range(200):
            st = self.runner.status()
            if st and st["state"] != "running":
                return st
            time.sleep(0.02)
        raise AssertionError("job did not finish: %r" % self.runner.status())


def _db_with_show(h, show_id="breaking-bad", status="ingesting"):
    db = CharacterDB(db_path=h.db_path)
    db.add_show(show_id, "Breaking Bad")
    db.update_show_status(show_id, status)
    db.close()


def test_parse_line_roundtrip():
    assert job_events.parse_line(ev("faces", current=1)) == {"event": "faces", "current": 1}
    assert job_events.parse_line("tqdm noise") is None
    assert job_events.parse_line("@@CINEID {not json") is None


def test_emit_is_silent_unless_enabled(monkeypatch, capsys):
    monkeypatch.delenv("CINEID_EVENTS", raising=False)
    job_events.emit("faces", current=1)
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("CINEID_EVENTS", "1")
    job_events.emit("faces", current=1)
    assert job_events.parse_line(capsys.readouterr().out) == {"event": "faces", "current": 1}


def test_progress_maps_to_percent_with_notes():
    h = Harness([
        FakeProc([ev("cast", total=20), ev("faces", current=10, total=20, name="A", ok=True),
                  ev("faces_done", ingested=20, skipped=0)]),
        FakeProc([ev("notes", current=10, total=20, name="A", ok=True),
                  ev("notes_done", ok=20, failed=0, usable=20, total=20)]),
    ])
    seen = []
    orig = h.runner._apply_event
    h.runner._apply_event = lambda e: (orig(e), seen.append(dict(h.runner.status())))
    h.runner.start("Breaking Bad", 1396, "/p.jpg", with_notes=True)
    h.wait()
    by_step = {(s["step"], s["current"]): s["percent"] for s in seen if s.get("step")}
    assert by_step[("faces", 10)] == 30
    assert by_step[("notes", 10)] == 80


def test_without_notes_faces_map_to_full_range():
    h = Harness([FakeProc([ev("faces", current=10, total=20, name="A", ok=True),
                           ev("faces_done", ingested=20, skipped=0)])])
    seen = []
    orig = h.runner._apply_event
    h.runner._apply_event = lambda e: (orig(e), seen.append(h.runner.status()["percent"]))
    h.runner.start("Breaking Bad", 1396, None, with_notes=False)
    st = h.wait()
    assert 50 in seen and st["state"] == "done" and st["percent"] == 100
    assert len(h.cmds) == 1                       # populate never spawned


def test_second_start_raises_job_busy():
    import threading
    hold = threading.Event()
    h = Harness([FakeProc([], hold=hold)])
    h.runner.start("Breaking Bad", 1, None, with_notes=False)
    with pytest.raises(JobBusy):
        h.runner.start("Other", 2, None, with_notes=False)
    hold.set()
    h.wait()


def test_ingest_failure_sets_failed_with_plain_message():
    h = Harness([FakeProc(["Traceback boom\n"], rc=1)])
    _db_with_show(h)
    h.runner.start("Breaking Bad", 1396, None, with_notes=False)
    st = h.wait()
    assert st["state"] == "failed"
    assert "Couldn't finish setting up Breaking Bad" in st["message"]
    assert "Traceback" not in st["message"] and any("Traceback" in d for d in st["details"])
    assert h.ready_calls == []


def test_ingest_with_zero_faces_is_failure_even_on_exit_zero():
    h = Harness([FakeProc([ev("faces_done", ingested=0, skipped=5)])])
    h.runner.start("Breaking Bad", 1396, None, with_notes=False)
    assert h.wait()["state"] == "failed"
    assert h.ready_calls == []


def test_populate_failure_keeps_show_ready_and_reports_shortfall():
    h = Harness([
        FakeProc([ev("faces_done", ingested=18, skipped=2)]),
        FakeProc(["OpenAI error\n"], rc=1),
    ])
    h.runner.start("Breaking Bad", 1396, "/p.jpg", with_notes=True)
    st = h.wait()
    assert st["state"] == "done"
    assert h.ready_calls == ["breaking-bad"]
    assert "18 of 20 characters ready" in st["summary"]
    assert "no character notes" in st["summary"].lower()


def test_summary_reports_notes_and_missing_photos():
    h = Harness([
        FakeProc([ev("faces_done", ingested=17, skipped=3)]),
        FakeProc([ev("notes_done", ok=15, failed=2, usable=15, total=17)]),
    ])
    h.runner.start("Breaking Bad", 1396, None, with_notes=True)
    st = h.wait()
    assert "17 of 20 characters ready. 3 had no usable photos." in st["summary"]
    assert "15 of 17 have character notes" in st["summary"]


def test_cancel_marks_show_failed_not_ingesting():
    import threading
    hold = threading.Event()
    h = Harness([FakeProc([ev("cast", total=5)], hold=hold)])
    _db_with_show(h)
    h.runner.start("Breaking Bad", 1396, None, with_notes=False)
    h.runner.cancel()
    st = h.wait()
    assert st["state"] == "cancelled"
    db = CharacterDB(db_path=h.db_path)
    assert db.get_all_shows()[0]["status"] == "failed"
    assert h.ready_calls == []


def test_non_event_lines_go_to_details_only():
    h = Harness([FakeProc(["Searching TMDB…\n", ev("faces_done", ingested=1, skipped=0)])])
    h.runner.start("Breaking Bad", 1396, None, with_notes=False)
    st = h.wait()
    assert "Searching TMDB…" in st["details"]
    assert all("@@CINEID" not in d for d in st["details"])


def test_subprocess_env_and_command():
    h = Harness([FakeProc([ev("faces_done", ingested=1, skipped=0)])])
    h.runner.start("Breaking Bad", 1396, None, with_notes=False)
    h.wait()
    cmd, kw = h.cmds[0]
    assert cmd[1].endswith("ingest.py") and "--tmdb-id" in cmd and "1396" in cmd
    assert kw["env"]["CINEID_EVENTS"] == "1" and kw["env"]["TMDB_API_KEY"] == "k"
    assert kw["env"]["TQDM_DISABLE"] == "1"


def test_character_notes_use_the_stronger_model_but_ingest_does_not():
    """Haiku left half a new show's characters without notes; every new show's notes step
    now runs on Sonnet 5.5. (Face learning needs no model; Watch Buddy stays on cheap Haiku.)"""
    h = Harness([FakeProc([ev("faces_done", ingested=3, skipped=0)]),
                 FakeProc([ev("notes_done", ok=3, failed=0, usable=3, total=3)])])
    h.runner.start("Breaking Bad", 1396, None, with_notes=True)
    h.wait()
    ingest_env, populate_env = h.cmds[0][1]["env"], h.cmds[1][1]["env"]
    assert h.cmds[1][0][1].endswith("populate_metadata.py")
    assert populate_env["CINEID_MODEL"] == "claude-sonnet-5-5"
    assert "CINEID_MODEL" not in ingest_env or ingest_env["CINEID_MODEL"] != "claude-sonnet-5-5"


def test_notes_model_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CINEID_NOTES_MODEL", "claude-haiku-5-5")
    h = Harness([FakeProc([ev("faces_done", ingested=1, skipped=0)]),
                 FakeProc([ev("notes_done", ok=1, failed=0, usable=1, total=1)])])
    h.runner.start("Breaking Bad", 1396, None, with_notes=True)
    h.wait()
    assert h.cmds[1][1]["env"]["CINEID_MODEL"] == "claude-haiku-5-5"
