"""Hourly stress 0-3 (spec 5.10), from still-hour heart rate (+ night-HRV blend)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, stdev

from .inputs import HourStat, Workout
from .stats import robust_baseline
from .strain import dedupe_workouts

STILL_STEPS = 150
MIN_STILL_BASELINE = 50
MIN_HRV_HIST = 14
SPREAD_FLOOR = 3.0
HRV_SD_FLOOR = 0.05
HR_WEIGHT, HRV_WEIGHT = 0.7, 0.3
HOUR = timedelta(hours=1)


@dataclass
class StressResult:
    score: float | None
    band: str | None
    hourly: list[tuple[datetime, float]] = field(default_factory=list)
    top_hours: list[datetime] = field(default_factory=list)
    still_readings: list[float] = field(default_factory=list)
    reason: str | None = None


def _overlaps(start: datetime, end: datetime, windows) -> bool:
    return any(start < w_end and w_start < end for w_start, w_end in windows)


def _band(score: float) -> str:
    return "low" if score < 1 else "medium" if score < 2 else "high"


def stress_day(
    hours: list[HourStat],
    workouts: list[Workout],
    sleep_windows: list[tuple[datetime, datetime]],
    hrv_today: list[tuple[datetime, float]],
    baseline_still_hr: list[float],
    night_hrv_hist: list[float],
) -> StressResult:
    blocked = [(w.start, w.end) for w in dedupe_workouts(workouts)] + list(sleep_windows)
    still = sorted(
        (h for h in hours
         if h.avg_hr is not None and h.steps < STILL_STEPS
         and not _overlaps(h.start, h.start + HOUR, blocked)),
        key=lambda h: h.start,
    )
    readings = [h.avg_hr for h in still]
    if len(baseline_still_hr) < MIN_STILL_BASELINE:
        return StressResult(
            None, None, still_readings=readings,
            reason=f"building stress baseline: {len(baseline_still_hr)} of {MIN_STILL_BASELINE} still hours",
        )
    if not still:
        return StressResult(None, None, reason="no still hours")

    centre, spread = robust_baseline(baseline_still_hr, SPREAD_FLOOR)
    hrv_ok = len(night_hrv_hist) >= MIN_HRV_HIST
    if hrv_ok:
        hrv_mean = mean(night_hrv_hist)
        hrv_sd = max(stdev(night_hrv_hist), HRV_SD_FLOOR)

    hourly: list[tuple[datetime, float]] = []
    for h in still:
        z = (h.avg_hr - centre) / spread
        if hrv_ok:
            samples = [ms for t, ms in hrv_today if h.start <= t < h.start + HOUR and ms > 0]
            if samples:
                z_hrv = (math.log(mean(samples)) - hrv_mean) / hrv_sd
                z = HR_WEIGHT * z + HRV_WEIGHT * max(0.0, -z_hrv)
        hourly.append((h.start, max(0.0, min(3.0, 0.5 + z))))

    score = mean(s for _, s in hourly)
    top = [t for t, _ in sorted(hourly, key=lambda p: (-p[1], p[0]))[:3]]
    return StressResult(score, _band(score), hourly, top, readings)
