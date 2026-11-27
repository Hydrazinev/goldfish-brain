from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Optional

import cv2
import faiss
import numpy as np
from insightface.app import FaceAnalysis

_EMBED_DIM = 512
_DET_SCORE_MIN = 0.7
_MIN_CLEAN_FACES = 3


class FaceRecogniser:
    def __init__(self, model_name: str = "buffalo_l") -> None:
        self._app = FaceAnalysis(name=model_name, providers=["CPUExecutionProvider"])
        self._app.prepare(ctx_id=0, det_size=(640, 640))
        self._warmup()

    def _warmup(self) -> None:
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self._app.get(dummy)

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_faces(
        self, image_array: np.ndarray
    ) -> list[tuple[np.ndarray, list[float], Any]]:
        """Return (face_crop, bbox, face) triples for every face detected in a BGR image."""
        faces = self._app.get(image_array)
        h, w = image_array.shape[:2]
        results = []
        for face in faces:
            x1, y1, x2, y2 = face.bbox
            x1c = max(0, int(x1))
            y1c = max(0, int(y1))
            x2c = min(w, int(x2))
            y2c = min(h, int(y2))
            crop = image_array[y1c:y2c, x1c:x2c]
            results.append((crop, face.bbox.tolist(), face))
        return results

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def get_embedding(self, image: np.ndarray, face: Any) -> Optional[np.ndarray]:
        """Extract the pre-computed embedding from a face object returned by detect_faces()."""
        emb = getattr(face, 'normed_embedding', None)
        if emb is None:
            emb = getattr(face, 'embedding', None)
        if emb is None:
            return None
        return emb.copy()

    def photo_embeddings(
        self, image_paths: list[str | Path]
    ) -> list[tuple[Path, np.ndarray]]:
        """(path, embedding) for each image with exactly one clean face (det_score >= 0.7)."""
        out: list[tuple[Path, np.ndarray]] = []
        for path in image_paths:
            image = cv2.imread(str(path))
            if image is None:
                continue
            faces = [f for f in self._app.get(image) if f.det_score >= _DET_SCORE_MIN]
            if len(faces) == 1:
                out.append((Path(path), faces[0].embedding.copy()))
        return out

    # ------------------------------------------------------------------
    # Gallery building
    # ------------------------------------------------------------------

    def build_gallery(
        self, image_paths: list[str | Path]
    ) -> Optional[tuple[np.ndarray, int]]:
        """Compute a mean embedding from a set of reference images.

        Only faces with det_score >= 0.7 are kept. Returns None if fewer
        than 3 clean faces are found across all images.
        """
        embeddings: list[np.ndarray] = []

        for path in image_paths:
            image = cv2.imread(str(path))
            if image is None:
                continue
            for face in self._app.get(image):
                if face.det_score >= _DET_SCORE_MIN:
                    embeddings.append(face.embedding.copy())

        if len(embeddings) < _MIN_CLEAN_FACES:
            return None

        mean_vec = np.mean(embeddings, axis=0).astype(np.float32)
        return mean_vec, len(embeddings)


class FaceIndex:
    def __init__(self) -> None:
        self._index = faiss.IndexFlatIP(_EMBED_DIM)
        self._ids: list[str] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_character(self, character_id: str, embedding: np.ndarray) -> None:
        """Normalise embedding and add it to the index under character_id."""
        vec = embedding.copy().reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(vec)
        self._index.add(vec)
        self._ids.append(character_id)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self, embedding: np.ndarray, top_k: int = 3
    ) -> list[tuple[str, float]]:
        """Return up to top_k (character_id, similarity_score) tuples."""
        if self._index.ntotal == 0:
            return []

        vec = embedding.copy().reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(vec)
        k = min(top_k, self._index.ntotal)
        distances, indices = self._index.search(vec, k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx >= 0:
                results.append((self._ids[int(idx)], float(dist)))
        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Write the FAISS index to path and character IDs to path.with_suffix('.meta')."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(path))
        with open(path.with_suffix(".meta"), "wb") as f:
            pickle.dump(self._ids, f)

    def load(self, path: str | Path) -> None:
        """Replace the current index and ID list from disk."""
        path = Path(path)
        self._index = faiss.read_index(str(path))
        with open(path.with_suffix(".meta"), "rb") as f:
            self._ids = pickle.load(f)


if __name__ == "__main__":
    recogniser = FaceRecogniser()
    print("recogniser ready")
