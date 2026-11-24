"""Heart-rate zones: the five limits (bpm where zones 1-5 start) every zone-based number uses (Edwards TRIMP and
strain, zone minutes, the benefit label, the card's zone bars). Heart-rate recovery's gate stays an effort level,
80 % of HRmax (hr_metrics), whatever the zones.

- Default (no settings.zones): Edwards' zones, 50/60/70/80/90 % of HRmax (Edwards 1993).
- settings.zones = {"hrmax": M, "rest": R, "rest_mean": B}: Apple Watch's zones (watchOS 9 and later), the same
  cut points as % of heart-rate reserve (Karvonen): limit_i = rest + f_i x (M - rest). Set from the four limits the
  watch shows (`helper zones`, which fits M and R to them and stores B, his 30-day mean resting heart rate that day),
  so on that day the limits are the watch's; after that rest = R + (the 30-day mean on the day - B), so the zones
  follow his resting heart rate as Apple's do, from the same baseline.
  Apple's zone 1 is everything below zone 2, so zone 1 here starts where Edwards' does, 50 % of HRmax (light
  activity such as walking still counts), and only below that is no zone.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

FRACTIONS = (0.5, 0.6, 0.7, 0.8, 0.9)
REST_DAYS = 30
APPLE_FRACTIONS = FRACTIONS[1:]   # the watch shows where zones 2-5 start


def rest_mean(rows, d: date) -> float | None:
    """Mean resting heart rate of the REST_DAYS days to d (Apple's daily figure; sleeping HR only when none)."""
    lo, hi = (d - timedelta(days=REST_DAYS - 1)).isoformat(), d.isoformat()
    near = [r for r in rows if lo <= r.date <= hi]
    vals = [r.rhr for r in near if r.rhr] or [r.sleep_hr for r in near if r.sleep_hr]
    return statistics.fmean(vals) if vals else None


def default_limits(hrmax: float) -> list[float]:
    return [f * hrmax for f in FRACTIONS]


def limits(spec: dict | None, hrmax: float, rows=(), d: date | None = None) -> list[float]:
    """The five limits for day d. spec = settings.zones (None: % of HRmax)."""
    if not spec:
        return default_limits(hrmax)
    rest = spec["rest"]
    now = rest_mean(rows, d) if d is not None else None
    if now is not None and spec.get("rest_mean") is not None:
        rest += now - spec["rest_mean"]
    top = spec["hrmax"]
    reserve = [rest + f * (top - rest) for f in FRACTIONS[1:]]
    return [min(FRACTIONS[0] * hrmax, reserve[0])] + reserve


def zone_of(bpm: float, lim: list[float]) -> int:
    """1-5, or 0 below zone 1 (above the top limit is still zone 5)."""
    for i in range(4, -1, -1):
        if bpm >= lim[i]:
            return i + 1
    return 0


def from_apple(starts: list[float]) -> tuple[float, float]:
    """(hrmax, rest) whose reserve zones start where the watch shows zones 2-5 start: a least-squares line
    limit = rest + f x (hrmax - rest) through the four points."""
    if len(starts) != 4 or any(b <= a for a, b in zip(starts, starts[1:])):
        raise ValueError("give the four heart rates where zones 2, 3, 4 and 5 start, rising")
    fm = statistics.fmean(APPLE_FRACTIONS)
    lm = statistics.fmean(starts)
    slope = (sum((f - fm) * (x - lm) for f, x in zip(APPLE_FRACTIONS, starts))
             / sum((f - fm) ** 2 for f in APPLE_FRACTIONS))     # = hrmax - rest
    rest = lm - slope * fm
    return round(rest + slope, 1), round(rest, 1)
