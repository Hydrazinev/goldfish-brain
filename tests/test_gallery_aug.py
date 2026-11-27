import random
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import gallery_aug
import rebuild_gallery
from recogniser import FaceIndex


def E(i):
    v = np.zeros(512, np.float32)
    v[i] = 1.0
    return v


def _photo():
    return np.random.RandomState(1).randint(0, 255, (300, 300, 3), np.uint8)


class FakeRecogniser:
    """Every image has one face; clean images embed to e1, degraded (640 canvas) ones to e2."""
    def detect_faces(self, img):
        degraded = img.shape[0] == gallery_aug.CANVAS
        face = SimpleNamespace(det_score=0.99, emb=E(1) if degraded else E(0))
        return [(None, [10.0, 10.0, 60.0, 90.0], face)]

    def get_embedding(self, img, face):
        return face.emb.copy()

    def photo_embeddings(self, paths):
        return [(Path(p), E(0)) for p in paths]

    def build_gallery(self, paths):
        return (E(0), len(paths)) if len(paths) >= 3 else None


def _write(tmp_path, n=3, cid="c1"):
    d = tmp_path / "raw" / cid
    d.mkdir(parents=True)
    for i in range(n):
        cv2.imwrite(str(d / f"p{i}.jpg"), _photo())
    return sorted(d.glob("*.jpg"))


def test_degraded_image_is_a_small_soft_face_on_a_canvas_and_repeatable():
    a = gallery_aug.degraded_face_image(_photo(), [100, 80, 200, 220], random.Random(5))
    b = gallery_aug.degraded_face_image(_photo(), [100, 80, 200, 220], random.Random(5))
    assert a.shape == (gallery_aug.CANVAS, gallery_aug.CANVAS, 3)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, gallery_aug.degraded_face_image(_photo(), [100, 80, 200, 220], random.Random(6)))


def test_gallery_mean_includes_the_degraded_copies(tmp_path):
    paths = _write(tmp_path)
    mean, n = gallery_aug.build_gallery(FakeRecogniser(), paths, copies=3)
    assert n == 3
    assert np.allclose(mean, 0.25 * E(0) + 0.75 * E(1))     # 3 clean (e1) + 9 degraded (e2), equal weight each


def test_switch_off_and_failures_fall_back_to_the_plain_gallery(tmp_path, monkeypatch):
    paths = _write(tmp_path)
    monkeypatch.setenv("CINEID_GALLERY_AUG", "0")
    assert np.allclose(gallery_aug.build_gallery(FakeRecogniser(), paths)[0], E(0))
    monkeypatch.delenv("CINEID_GALLERY_AUG")

    class Broken(FakeRecogniser):
        def photo_embeddings(self, paths):
            raise RuntimeError("boom")
    assert np.allclose(gallery_aug.build_gallery(Broken(), paths)[0], E(0))
    assert gallery_aug.build_gallery(FakeRecogniser(), paths[:2]) is None


def test_rebuild_is_a_dry_run_unless_apply(tmp_path):
    show = tmp_path / "show"
    _write(show, 3, "c1")
    idx = FaceIndex()
    idx.add_character("c1", E(0))
    idx.add_character("c2", E(2))     # no photos on disk: keeps its vector
    idx.save(show / "embeddings.faiss")
    before = (show / "embeddings.faiss").read_bytes()
    lines = []
    s = rebuild_gallery.rebuild(show, FakeRecogniser(), FaceIndex, apply=False, copies=2, out=lines.append)
    assert s == {"changed": 1, "kept": 1, "applied": False}
    assert (show / "embeddings.faiss").read_bytes() == before and not (show / "embeddings.faiss.bak").exists()
    s = rebuild_gallery.rebuild(show, FakeRecogniser(), FaceIndex, apply=True, copies=2, out=lines.append)
    assert s["applied"] and (show / "embeddings.faiss.bak").read_bytes() == before
    new = FaceIndex(); new.load(show / "embeddings.faiss")
    assert new._ids == ["c1", "c2"]
    top = new.search(E(1), top_k=2)
    assert top[0][0] == "c1" and top[0][1] > 0.3            # c1 now also resembles the degraded embedding
