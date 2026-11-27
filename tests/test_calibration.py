import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import calibration


def test_probability_rises_with_score_and_margin():
    low = calibration.calibrated_confidence(0.2, 0.05)
    mid = calibration.calibrated_confidence(0.5, 0.2)
    high = calibration.calibrated_confidence(0.85, 0.6)
    assert 0 <= low < mid < high <= 1
    assert calibration.calibrated_confidence(0.6, 0.4) > calibration.calibrated_confidence(0.6, 0.0)


def test_bad_inputs_give_none_not_a_crash():
    for s, m in [(None, 0.1), (0.5, None), ("x", 0.1), (float("nan"), 0.1), (0.5, float("inf"))]:
        assert calibration.calibrated_confidence(s, m) is None


def test_extremes_stay_in_range():
    assert calibration.calibrated_confidence(5, 5) <= 1.0
    assert calibration.calibrated_confidence(-5, -5) >= 0.0
