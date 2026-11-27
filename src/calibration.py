"""Calibrated confidence: P(the top-1 name is the right character) from the face's match numbers.

A two-feature logistic regression (top-1 similarity, margin to the runner-up) fitted on SYNTHETIC
frames of seven calibration shows and checked on four disjoint held-out shows (docs/ACCURACY.md,
"ML experiments"). It is shown nowhere on the card: it is only added to the /identify result as
`calibrated_confidence` and to the local accuracy log as `p_correct`. Thresholds do not use it.

Treat it as optimistic: it was fitted on synthetic data in which no cast face was ever given a wrong name,
with an assumed 50/50 mix of cast and non-cast faces. Refit on real frames (data/feedback) before relying on it.
"""
from __future__ import annotations

import math
from typing import Optional

MEAN = (0.184643, 0.10618)
SD = (0.207315, 0.190852)
BETA = (5.367134, 1.899008)      # per standardised feature
BIAS = -1.924837


def calibrated_confidence(score, margin) -> Optional[float]:
    """Probability in 0..1 that the top-1 identity is right, or None when the inputs are unusable."""
    try:
        s, m = float(score), float(margin)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(s) and math.isfinite(m)):
        return None
    z = BIAS + BETA[0] * (s - MEAN[0]) / SD[0] + BETA[1] * (m - MEAN[1]) / SD[1]
    z = max(-40.0, min(40.0, z))
    return round(1.0 / (1.0 + math.exp(-z)), 4)
