import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from static_files import NoCacheStaticFiles


def test_ui_files_are_revalidated_every_time():
    """The app's web view cached an old app.js, so a rebuilt page never showed up."""
    d = Path(tempfile.mkdtemp())
    (d / "app.js").write_text("console.log(1)")
    (d / "index.html").write_text("<p>hi</p>")
    app = FastAPI()
    app.mount("/ui", NoCacheStaticFiles(directory=str(d), html=True))
    c = TestClient(app)
    for path in ("/ui/app.js", "/ui/"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
    # still cheap: unchanged files are answered with 304 via the ETag
    etag = c.get("/ui/app.js").headers["etag"]
    assert c.get("/ui/app.js", headers={"If-None-Match": etag}).status_code == 304
