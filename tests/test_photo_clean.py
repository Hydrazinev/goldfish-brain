"""Reference-photo clean-up and the read-only audit (fake embeddings, no model)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import audit_photos
import ingest
import photo_clean


def _same(n, rng, noise=0.05):
    base = rng.normal(size=512)
    return [base + noise * rng.normal(size=512) for _ in range(n)]


def test_outlier_photo_is_dropped_and_good_ones_kept():
    rng = np.random.default_rng(0)
    embs = _same(5, rng) + [rng.normal(size=512)]       # last one: someone else
    keep = photo_clean.select_keep(embs, 0.25)
    assert keep == [0, 1, 2, 3, 4]


def test_never_drops_below_keep_min_and_skips_tiny_sets():
    rng = np.random.default_rng(1)
    embs = [rng.normal(size=512) for _ in range(4)]     # nobody agrees with anybody
    assert len(photo_clean.select_keep(embs, 0.9, keep_min=2)) == 2
    assert len(photo_clean.select_keep(embs, 0.9, keep_min=3)) == 3
    assert photo_clean.select_keep(embs[:2], 0.9) == [0, 1]


class FakeRec:
    def __init__(self, embs):
        self.embs = embs

    def photo_embeddings(self, paths):
        return [(Path(p), self.embs[Path(p).name]) for p in paths]


def test_ingest_leaves_out_outliers_but_not_the_files(tmp_path):
    rng = np.random.default_rng(2)
    good = _same(4, rng)
    embs = {f"g{i}.jpg": e for i, e in enumerate(good)}
    embs["bad.jpg"] = rng.normal(size=512)
    paths = []
    for n in embs:
        (tmp_path / n).write_bytes(b"x")
        paths.append(tmp_path / n)
    kept = ingest._drop_outlier_photos(paths, FakeRec(embs), "X")
    assert sorted(p.name for p in kept) == ["g0.jpg", "g1.jpg", "g2.jpg", "g3.jpg"]
    assert (tmp_path / "bad.jpg").exists()


def test_ingest_cleanup_failure_keeps_everything(tmp_path):
    class Boom:
        def photo_embeddings(self, paths):
            raise RuntimeError("model gone")
    paths = [tmp_path / f"{i}.jpg" for i in range(4)]
    assert ingest._drop_outlier_photos(paths, Boom(), "X") == paths


def test_audit_lists_suspects_and_changes_nothing(tmp_path):
    rng = np.random.default_rng(3)
    cdir = tmp_path / "raw" / "s-a"
    cdir.mkdir(parents=True)
    embs = {f"g{i}.jpg": e for i, e in enumerate(_same(4, rng))}
    embs["bad.jpg"] = rng.normal(size=512)
    for n in embs:
        (cdir / n).write_bytes(b"x")
    (tmp_path / "raw" / "s-b").mkdir()
    before = sorted(p.name for p in tmp_path.rglob("*"))
    out = audit_photos.audit_show(tmp_path, lambda paths: [(p, embs[Path(p).name]) for p in paths])
    assert [(s["character_id"], s["photo"]) for s in out] == [("s-a", "bad.jpg")]
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_audit_missing_show_returns_nothing(tmp_path):
    assert audit_photos.audit_show(tmp_path / "nope", lambda p: []) == []
