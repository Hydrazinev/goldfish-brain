import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _never_touch_the_real_last_capture(tmp_path, monkeypatch):
    """/identify keeps the latest frame at data/last_capture.jpg: tests must never overwrite the real one."""
    import server
    monkeypatch.setattr(server, "_DATA_DIR", tmp_path / "engine-data")
