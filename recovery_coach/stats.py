"""Robust statistics helpers shared by every scoring module."""
from __future__ import annotations

import math
from statistics import median

MIN_BASELINE_VALUES = 14
MAD_TO_SD = 1.4826


def robust_baseline(values: list[float], floor: float) -> tuple[float, float] | None:
    """Return (median, max(1.4826 * MAD, floor)), or None with < 14 values."""
    vals = [v for v in values if v is not None]
    if len(vals) < MIN_BASELINE_VALUES:
        return None
    centre = median(vals)
    mad = median([abs(v - centre) for v in vals])
    return centre, max(MAD_TO_SD * mad, floor)


def zscore(x: float, centre: float, spread: float, clip: float = 3.0) -> float:
    """(x - centre) / spread, clipped to +/- clip."""
    if spread <= 0:
        return 0.0
    return max(-clip, min(clip, (x - centre) / spread))


def logistic(zbar: float) -> float:
    """Recovery logistic: 100 / (1 + e^-(1.6 z + 0.3))."""
    return 100.0 / (1.0 + math.exp(-(1.6 * zbar + 0.3)))


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in 0-100)."""
    if not values:
        raise ValueError("percentile of empty list")
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
