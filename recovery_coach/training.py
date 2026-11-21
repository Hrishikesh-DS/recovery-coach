"""Daily and weekly training figures beyond CTL / ATL / TSB / ACWR.

Load series (design, "Which load series each figure uses"): the engine's whole-day Edwards series (DayRow.trimp,
ctl, atl, acwr) for Form %, ramp, the easy-day cap, load-ratio bands, the status label and each workout's fitness /
fatigue increments; a workout-only daily Banister load (DayRow.wload, rest days 0) for monotony, training strain
and the weekly target; session RPE (DayRow.srpe) only in its own 7:28 ratio.

- Monotony and training strain (Foster 1998, 2001; Runalyze Monotony.php): over 7 days, rest days 0, monotony =
  mean / population SD, capped at 10 (10 when every day is equal, 0 for a week with no load); strain = weekly load x
  monotony; warn at 1.5, alert at 2.0.
- Form % (intervals.icu) = 100 (CTL - ATL) / CTL; ramp (TrainingPeaks) = CTL change over 7 days.
- Easy-day cap (Runalyze "easy TRIMP"): the largest load tomorrow that keeps TSB >= 0, solved from the engine's own
  update (load.update: x + (1 - e^(-1/tau)) (load - x), tau 42 / 7): load <= (CTL (1 - a42) - ATL (1 - a7)) /
  (a7 - a42); never below 0.
- Fitness / fatigue increments of a workout (Athlytic): a42 (load - CTL) and a7 (load - ATL), the same update.
- Session-RPE ratio (Apple's Training Load as numbers; Polar Perceived Load): 7-day sum / (28-day sum / 4).
- Load-ratio bands: Polar Training Load Pro white paper (<= 0.8 detraining, 0.8-1.0 maintaining, 1.0-1.3
  productive, > 1.3 overreaching, > 1.5 injury risk); Garmin manual (< 0.8 low, 0.8-1.4 optimal, 1.5-1.9 high,
  >= 2.0 very high).
- Session level (Polar): this workout's load / the mean of the same activity over 90 days (>= 3 sessions); bands
  < 0.5 very low, 0.5-0.75 low, 0.75-1.25 medium, 1.25-2 high, >= 2 very high.
- Intensity split and Polarization Index (Treff et al. 2019; intervals.icu): minutes low (zones 1-2) / mid (3-4) /
  high (5); PI = log10((low / mid) x high x 100) on fractions, none when high = 0, mid = 0 or high > low; >= 2 means
  polarized. Load-focus shortage (Garmin, Bevel), my thresholds, approx: over 28 days, low < 60 %, mid < 10 %,
  high < 3 %.
- Weekly target (Google, Strava): the mean weekly workout load of the 4 weeks before x 0.8 - 1.3.
- Running tolerance (Garmin, 2025), approx: the biggest week of running in the last 4 weeks x 1.15.
- Performance condition (Garmin), approx: this steady outdoor run's efficiency against the median of the 28 days
  before (>= 3 runs): 2 x the % difference, clamped to -20..+20.
- HRV status (Garmin), my rule: the 7-day mean of nightly ln HRV against the 60 days before (median +- MAD, >= 14
  nights): inside = balanced; above, or below by under 2 MAD = unbalanced; below by 2 MAD or more = low.
- Status (Polar's band, adjusted after Garmin / Bevel), approx: "unproductive" when the band is productive or
  overreaching but VO2 max is trending down; "strained" when the ratio is above 1.3 and the 7-day recovery mean is
  below 34.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from .inputs import DayRow
from .load import ATL_TAU, CTL_TAU
from .workout_log import LogEntry

A42 = 1.0 - math.exp(-1.0 / CTL_TAU)
A7 = 1.0 - math.exp(-1.0 / ATL_TAU)
MONO_WARN, MONO_ALERT, MONO_CAP = 1.5, 2.0, 10.0
FOCUS_MIN = (0.60, 0.10, 0.03)
FOCUS_NAMES = ("low aerobic", "high aerobic", "anaerobic")


def monotony(loads7: list[float]) -> tuple[float, float, str | None]:
    days = (list(loads7) + [0.0] * 7)[:7]
    total = sum(days)
    if total == 0:
        return 0.0, 0.0, None
    mean = total / 7.0
    sd = statistics.pstdev(days)
    mono = MONO_CAP if sd == 0 else min(mean / sd, MONO_CAP)
    flag = "alert" if mono >= MONO_ALERT else "warn" if mono >= MONO_WARN else None
    return mono, total * mono, flag


def form_pct(ctl: float | None, atl: float | None) -> float | None:
    if not ctl or atl is None:
        return None
    return 100.0 * (ctl - atl) / ctl


def _by_date(rows: list[DayRow]) -> dict[str, DayRow]:
    return {r.date: r for r in rows}


def ramp(rows: list[DayRow], d: date) -> float | None:
    by = _by_date(rows)
    now, then = by.get(d.isoformat()), by.get((d - timedelta(days=7)).isoformat())
    if not now or not then or now.ctl is None or then.ctl is None:
        return None
    return now.ctl - then.ctl


def easy_cap_trimp(ctl: float, atl: float) -> float:
    return max((ctl * (1 - A42) - atl * (1 - A7)) / (A7 - A42), 0.0)


def increments(load: float, ctl: float, atl: float) -> tuple[float, float]:
    return A42 * (load - ctl), A7 * (load - atl)


def _window(rows: list[DayRow], d: date, days: int) -> list[DayRow]:
    lo = (d - timedelta(days=days - 1)).isoformat()
    return [r for r in rows if lo <= r.date <= d.isoformat()]


def srpe_ratio(rows: list[DayRow], d: date) -> float | None:
    w = _window(rows, d, 28)
    if len(w) < 28:
        return None
    chronic = sum(r.srpe or 0.0 for r in w) / 4.0
    if chronic == 0:
        return None
    return sum(r.srpe or 0.0 for r in _window(rows, d, 7)) / chronic


def polar_band(ratio: float) -> str:
    if ratio <= 0.8:
        return "detraining"
    if ratio <= 1.0:
        return "maintaining"
    if ratio <= 1.3:
        return "productive"
    if ratio <= 1.5:
        return "overreaching"
    return "injury risk"


def garmin_band(ratio: float) -> str:
    if ratio < 0.8:
        return "low"
    if ratio < 1.5:
        return "optimal"
    if ratio < 2.0:
        return "high"
    return "very high"


def _entries_in(entries: list[LogEntry], d: date, days: int) -> list[LogEntry]:
    lo = (d - timedelta(days=days - 1)).isoformat()
    return [e for e in entries if lo <= e.date <= d.isoformat()]


def split_window(entries: list[LogEntry], d: date, days: int) -> list[float]:
    out = [0.0, 0.0, 0.0]
    for e in _entries_in(entries, d, days):
        for i, v in enumerate(e.split[:3]):
            out[i] += v
    return out


def polarization(split: list[float]) -> float | None:
    total = sum(split)
    if total <= 0:
        return None
    low, mid, high = (x / total for x in split)
    if high == 0 or mid == 0 or high > low:
        return None
    return math.log10(low / mid * high * 100.0)


def focus_shortage(entries: list[LogEntry], d: date) -> list[str]:
    s = split_window(entries, d, 28)
    total = sum(s)
    if total <= 0:
        return []
    return [name for name, share, floor in zip(FOCUS_NAMES, (x / total for x in s), FOCUS_MIN) if share < floor]


def weekly_target(rows: list[DayRow], d: date) -> tuple[float, float] | None:
    w = _window(rows, d, 28)
    if len(w) < 28:
        return None
    week = sum(r.wload or 0.0 for r in w) / 4.0
    return 0.8 * week, 1.3 * week


def running_tolerance(entries: list[LogEntry], d: date) -> float | None:
    weeks = []
    for k in range(4):
        end = d - timedelta(days=7 * k)
        km = sum(e.dist or 0.0 for e in _entries_in(entries, end, 7) if e.kind == "running") / 1000.0
        weeks.append(km)
    best = max(weeks)
    return best * 1.15 if best > 0 else None


def session_level(entry: LogEntry, entries: list[LogEntry]) -> str | None:
    d = date.fromisoformat(entry.date)
    same = [e.edwards for e in _entries_in(entries, d, 90) if e.kind == entry.kind and e.id != entry.id
            and e.edwards is not None]
    if len(same) < 3 or entry.edwards is None:
        return None
    ratio = entry.edwards / (sum(same) / len(same))
    for limit, name in ((0.5, "very low"), (0.75, "low"), (1.25, "medium"), (2.0, "high")):
        if ratio < limit:
            return name
    return "very high"


def typical_z(entry: LogEntry, entries: list[LogEntry], field: str) -> float | None:
    value = getattr(entry, field)
    prior = [getattr(e, field) for e in sorted(entries, key=lambda e: e.id)
             if e.kind == entry.kind and e.id < entry.id and getattr(e, field) is not None][-10:]
    if value is None or len(prior) < 3:
        return None
    sd = statistics.stdev(prior)
    return None if sd == 0 else (value - statistics.fmean(prior)) / sd


def performance_condition(entry: LogEntry, entries: list[LogEntry]) -> int | None:
    if entry.ef is None or entry.kind != "running" or entry.indoor is True:
        return None
    d = date.fromisoformat(entry.date)
    prior = [e.ef for e in _entries_in(entries, d - timedelta(days=1), 28)
             if e.kind == "running" and e.indoor is not True and e.ef is not None and e.id != entry.id]
    if len(prior) < 3:
        return None
    med = statistics.median(prior)
    return int(max(-20, min(20, round(200.0 * (entry.ef - med) / med))))


def hrv_status(rows: list[DayRow], d: date) -> str | None:
    recent = [r.hrv_ln for r in _window(rows, d, 7) if r.hrv_ln is not None]
    base = [r.hrv_ln for r in _window(rows, d - timedelta(days=7), 60) if r.hrv_ln is not None]
    if len(recent) < 3 or len(base) < 14:
        return None
    med = statistics.median(base)
    mad = statistics.median(abs(x - med) for x in base) or 1e-9
    now = statistics.fmean(recent)
    if now < med - 2 * mad:
        return "low"
    if med - mad <= now <= med + mad:
        return "balanced"
    return "unbalanced"


def status(ratio: float | None, vo2_trend: float | None, recovery_avg7: float | None) -> str | None:
    if ratio is None:
        return None
    band = polar_band(ratio)
    if ratio > 1.3 and recovery_avg7 is not None and recovery_avg7 < 34:
        return "strained"
    if band in ("productive", "overreaching") and vo2_trend is not None and vo2_trend < 0:
        return "unproductive"
    return band
