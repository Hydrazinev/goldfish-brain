"""Real-model check; skipped when the buffalo_l model is not downloaded."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_ROOT = Path(__file__).parent.parent
_MODEL = Path.home() / ".insightface" / "models" / "buffalo_l"
_CAPTURE = _ROOT / "data" / "last_capture.jpg"

pytestmark = pytest.mark.skipif(not _MODEL.exists() or not _CAPTURE.exists(),
                                reason="needs buffalo_l model and data/last_capture.jpg")


def test_detects_a_face_in_the_last_capture():
    import cv2
    from recogniser import FaceRecogniser
    faces = FaceRecogniser().detect_faces(cv2.imread(str(_CAPTURE)))
    assert len(faces) >= 1
