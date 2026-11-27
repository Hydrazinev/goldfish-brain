"""Degraded copies of a reference photo, for building a sturdier character gallery.

A small, soft, compressed face (a wide shot on a streaming service) embeds differently from a clean TMDB
profile photo. `degraded_face_image` renders one such copy of a photo: the face is shrunk to 35-110 px
tall, pasted into a noisy canvas, blurred, dimmed and JPEG-compressed. Pure OpenCV/numpy: no model needed.
"""
from __future__ import annotations

import logging
import os
import random
from typing import Optional

import cv2
import numpy as np

CANVAS = 640
FACE_H = (35, 110)
JPEG_Q = (40, 70)


def degraded_face_image(photo: np.ndarray, bbox, rng: random.Random) -> np.ndarray:
    """One degraded copy of `photo` (BGR) whose face (bbox x1,y1,x2,y2) is 35-110 px tall on a CANVAS square."""
    x1, y1, x2, y2 = bbox
    fh = max(y2 - y1, 1)
    target = rng.randint(*FACE_H)
    cx, cy, half = (x1 + x2) / 2, (y1 + y2) / 2, fh * 2.2
    h, w = photo.shape[:2]
    a, b = int(max(0, cx - half)), int(max(0, cy - half))
    c, d = int(min(w, cx + half)), int(min(h, cy + half))
    crop = photo[b:d, a:c]
    s = target / fh
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))))
    bg = np.full((CANVAS, CANVAS, 3), rng.randint(20, 90), np.uint8)
    noise = np.random.RandomState(rng.randint(0, 9999)).randint(0, 25, bg.shape, dtype=np.uint8)
    bg = cv2.add(bg, noise)
    crop = crop[:CANVAS, :CANVAS]
    ch, cw = crop.shape[:2]
    ox, oy = rng.randint(0, CANVAS - cw), rng.randint(0, CANVAS - ch)
    bg[oy:oy + ch, ox:ox + cw] = crop
    if rng.random() < 0.85:
        k = rng.choice([3, 5])
        bg = cv2.GaussianBlur(bg, (k, k), rng.uniform(0.5, 1.5))
    bg = cv2.convertScaleAbs(bg, alpha=rng.uniform(0.5, 1.0), beta=rng.uniform(-20, 5))
    ok, buf = cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(*JPEG_Q)])
    return cv2.imdecode(buf, 1)


COPIES = 4
_log = logging.getLogger(__name__)


def enabled() -> bool:
    """On by default; CINEID_GALLERY_AUG=0 switches the degraded copies off (new shows only)."""
    return os.environ.get("CINEID_GALLERY_AUG", "1") != "0"


def degraded_embeddings(recogniser, image_paths, copies: int = COPIES, seed: int = 23) -> list[np.ndarray]:
    """Embeddings of `copies` degraded versions of every reference photo that has one clean face.
    Uses only the recogniser's public detect/embed methods; copies where no face is found are skipped."""
    rng = random.Random(seed)
    out: list[np.ndarray] = []
    for path in image_paths:
        img = cv2.imread(str(path))
        if img is None:
            continue
        faces = [t for t in recogniser.detect_faces(img) if getattr(t[2], "det_score", 1.0) >= 0.7]
        if len(faces) != 1:
            continue
        for _ in range(copies):
            det = [t for t in recogniser.detect_faces(degraded_face_image(img, faces[0][1], rng))
                   if getattr(t[2], "det_score", 1.0) >= 0.5]
            if not det:
                continue
            _crop, bbox, face = max(det, key=lambda t: (t[1][2] - t[1][0]) * (t[1][3] - t[1][1]))
            emb = recogniser.get_embedding(None, face)
            if emb is not None:
                out.append(emb)
    return out


def build_gallery(recogniser, image_paths, copies: int = COPIES) -> Optional[tuple[np.ndarray, int]]:
    """Like FaceRecogniser.build_gallery, but the mean also includes degraded copies of each photo
    (docs/ACCURACY.md, "ML experiments"). Falls back to the plain gallery on any problem.
    Returns (mean vector, number of CLEAN photos)."""
    plain = recogniser.build_gallery(image_paths)
    if plain is None or not enabled() or copies <= 0:
        return plain
    try:
        pairs = recogniser.photo_embeddings(image_paths)
        extra = degraded_embeddings(recogniser, [p for p, _ in pairs], copies)
        if not pairs or not extra:
            return plain
        mean = np.mean([e for _, e in pairs] + extra, axis=0).astype(np.float32)
        return mean, plain[1]
    except Exception as exc:          # an extra: never block ingest
        _log.warning("degraded-gallery step skipped: %s", exc)
        return plain
