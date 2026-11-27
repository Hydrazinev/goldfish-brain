"""Spot reference photos that are clearly not the character's actor.

Each photo is scored by its cosine similarity to the mean of the *other*
photos of that character. Pure numpy: no model, no file changes.
"""
from __future__ import annotations

import numpy as np

# Measured on the owner's existing shows (see docs/ACCURACY.md).
OUTLIER_CUTOFF = 0.3
MIN_PHOTOS_TO_CHECK = 3


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def similarities(embeddings: list) -> list[float]:
    """Similarity of each embedding to the mean of the others (needs >= 2)."""
    if len(embeddings) < 2:
        return [1.0] * len(embeddings)
    e = np.stack([_unit(x) for x in embeddings])
    return [float(e[i] @ _unit(np.delete(e, i, axis=0).mean(axis=0))) for i in range(len(e))]


def select_keep(embeddings: list, cutoff: float = OUTLIER_CUTOFF, keep_min: int = 3) -> list[int]:
    """Indices to keep, in original order. Nothing is dropped below
    MIN_PHOTOS_TO_CHECK photos, and never more than leaves `keep_min` (the
    best-scoring photos survive if the cutoff would drop too many)."""
    n = len(embeddings)
    if n < MIN_PHOTOS_TO_CHECK:
        return list(range(n))
    sims = similarities(embeddings)
    keep = [i for i, s in enumerate(sims) if s >= cutoff]
    if len(keep) < min(keep_min, n):
        keep = sorted(sorted(range(n), key=lambda i: sims[i], reverse=True)[:min(keep_min, n)])
    return keep
