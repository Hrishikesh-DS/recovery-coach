"""Strain (0-21), Edwards TRIMP, workout de-duplication and strain targets (spec 5.2, 5.4)."""
from __future__ import annotations

import math
import statistics
from datetime import datetime

from .inputs import HourStat, Workout

GAP_CAP_MIN = 5.0
LAST_READING_MIN = 2.5
DEFAULT_K = 170.0
MIN_CALIBRATION_DAYS = 14
MIN_FULL_COVERAGE_HOURS = 12
MIN_TRIMP_WITH_WORKOUT = 40.0  # my call
TARGETS = {"green": (14, 18), "yellow": (10, 14), "red": (0, 10)}
TSB_SHIFT_BELOW = -20.0  # my call
TSB_SHIFT = 2  # my call
DUPLICATE_OVERLAP = 0.8


def zone_weight(bpm: float, hrmax: float) -> int:
    """Edwards zone weight: 50/60/70/80/90 % of HRmax -> 1..5, below 50 % -> 0."""
    pct = bpm / hrmax * 100.0
    for limit, weight in ((90, 5), (80, 4), (70, 3), (60, 2), (50, 1)):
        if pct >= limit:
            return weight
    return 0


def _duration_min(w: Workout) -> float:
    return (w.end - w.start).total_seconds() / 60.0


def _overlap_ratio(a: Workout, b: Workout) -> float:
    overlap = (min(a.end, b.end) - max(a.start, b.start)).total_seconds()
    shorter = min((a.end - a.start).total_seconds(), (b.end - b.start).total_seconds())
    if overlap <= 0 or shorter <= 0:
        return 0.0
    return overlap / shorter


def dedupe_workouts(ws: list[Workout]) -> list[Workout]:
    """Collapse duplicate logs of one session (overlap / shorter duration > 0.8).

    Preference: has avg_hr, has energy, kind != 'other', longer. Sorted by start.
    """
    ranked = sorted(
        ws,
        key=lambda w: (
            w.avg_hr is None,
            w.energy_kcal is None,
            w.kind == "other",
            -_duration_min(w),
            w.start,
        ),
    )
    kept: list[Workout] = []
    for w in ranked:
        if all(_overlap_ratio(w, k) <= DUPLICATE_OVERLAP for k in kept):
            kept.append(w)
    return sorted(kept, key=lambda w: w.start)


def hr_max(daily_max: list[float], age: float | None = None) -> float | None:
    """Observed HRmax = max of daily maxima after dropping the top 2 outliers.

    With an age, the Tanaka (2001) estimate 208 - 0.7 * age is a floor.
    """
    observed = None
    if len(daily_max) >= 3:
        observed = max(sorted(daily_max)[:-2])
    estimate = 208.0 - 0.7 * age if age is not None else None
    if observed is None:
        return estimate
    if estimate is None:
        return observed
    return max(estimate, observed)


def _trimp_from(readings: list[tuple[datetime, float]], hrmax: float) -> float:
    total = 0.0
    for i, (t, bpm) in enumerate(readings):
        if i + 1 < len(readings):
            minutes = min((readings[i + 1][0] - t).total_seconds() / 60.0, GAP_CAP_MIN)
        else:
            minutes = LAST_READING_MIN
        total += minutes * zone_weight(bpm, hrmax)
    return total


def trimp(
    minutes: list[tuple[datetime, float]], hours: list[HourStat], hrmax: float
) -> tuple[float, float]:
    """Edwards TRIMP from minute readings; coverage = hours with an average HR."""
    readings = sorted(minutes, key=lambda r: r[0])
    coverage = float(sum(1 for h in hours if h.avg_hr is not None))
    return _trimp_from(readings, hrmax), coverage


def strain(trimp: float, k: float) -> float:
    return 21.0 * (1.0 - math.exp(-trimp / k))


def calibrate_k(daily_trimps: list[float]) -> float:
    """K such that the 90th-percentile day maps to strain 15; 170 when data is thin."""
    days = [t for t in daily_trimps if t > 0]
    if len(days) < MIN_CALIBRATION_DAYS:
        return DEFAULT_K
    t90 = statistics.quantiles(days, n=10, method="inclusive")[8]
    return -t90 / math.log(1 - 15.0 / 21.0)


def day_strain(trimp: float, coverage_hours: float, k: float) -> tuple[float | None, str | None]:
    """Day strain plus a flag: 'may be low' (< 12 h HR but a workout-sized TRIMP) or withheld."""
    if coverage_hours >= MIN_FULL_COVERAGE_HOURS:
        return strain(trimp, k), None
    if trimp >= MIN_TRIMP_WITH_WORKOUT:
        return strain(trimp, k), "may be low"
    return None, "not enough heart-rate data"


def workout_strain(
    w: Workout, minutes: list[tuple[datetime, float]], hrmax: float, k: float
) -> float:
    inside = sorted((r for r in minutes if w.start <= r[0] <= w.end), key=lambda r: r[0])
    return strain(_trimp_from(inside, hrmax), k)


def strain_target(zone: str, tsb: float | None) -> tuple[float, float]:
    lo, hi = TARGETS[zone]
    if tsb is not None and tsb < TSB_SHIFT_BELOW:
        lo, hi = max(0, lo - TSB_SHIFT), max(0, hi - TSB_SHIFT)
    return (lo, hi)
