"""WHOOP-style Age and Pace of Aging (replaces the openbodyscore Body Age; design 2026-11-23-whoop-age-design.md).

Method of the WHOOP 2025 white paper "The WHOOP Healthspan Feature": nine metrics, each compared with a health-optimised
target; the hazard ratio (HR) against the target becomes years with t = 10 x ln(HR) (about 10% higher all-cause
mortality per year of age); WHOOP Age = age + the sum. WHOOP does not publish its HR curves or its overlap adjustment,
so the curves below come from the studies the paper cites, and one overlap factor, applied to every metric but the two
sleep metrics, is fitted to the paper's Table 2 worked examples (tests/test_whoop_age.py).

Window: the 182 days ending on as_of, plain means. A metric with fewer than 30 days of data counts 0 years and is listed
missing (VO2 max: >= 2 readings with the latest within 120 days; lean mass: the latest value within 90 days).
Pace of Aging = 1 + (total from the last 30 days - total from 182 days) / 0.5, clipped to [-1, 3]: the last 30 days held
for 6 months against today. VO2 max and lean mass, measured only now and then, take the same value in both totals.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .inputs import DayRow

MODEL = "whoop-v1"
NAMES = ("Hours of sleep", "Sleep consistency", "Steps", "Zones 1-3", "Zones 4-5", "Strength", "VO2 max", "Resting HR",
         "Lean body mass")
SLEEP_NAMES = ("Hours of sleep", "Sleep consistency")
YEARS_PER_LN_HR = 10.0            # t = ln(HR) / 0.1 (white paper, Section 01)
OVERLAP = 0.70                    # fitted to Table 2 (least squares over the four anchors), not on the sleep metrics
WINDOW_DAYS, MIN_DAYS = 182, 30
PACE_DAYS, PACE_MIN_DAYS = 30, 21
PACE_RANGE = (-1.0, 3.0)
VO2_MIN_READINGS, VO2_MAX_AGE_DAYS = 2, 120
LBM_MAX_AGE_DAYS = 90
CONSISTENCY_PRIOR_NIGHTS, CONSISTENCY_MIN_PRIOR = 4, 2

# ---------------------------------------------------------------- curves (ln HR against the target)
# Hours of sleep: < 7 h adds years (short sleep RR 1.12, Cappuccio 2010, per hour short); 7-9 h a minor reduction
# (design choice: HR 0.98); > 9 h neutral (the paper neither rewards nor penalises long sleep).
SLEEP_TARGET_H, SLEEP_SHORT_RR_PER_H, SLEEP_IN_RANGE_HR, SLEEP_LONG_H = 7.0, 1.12, 0.98, 9.0
# Sleep consistency (WHOOP 0-100%): break-even 70%; Windred 2024 (most vs least regular: 20-48% lower mortality) spread
# over the usual range: 0.012 ln HR per point, clamped to 40-95%.
CONSISTENCY_TARGET, CONSISTENCY_SLOPE, CONSISTENCY_CLAMP = 70.0, 0.012, (40.0, 95.0)
# Steps/day: Ding D et al. Lancet Public Health 2025 (HR vs 2,000 steps); targets 8,000 (< 60), 5,600 (60+).
STEPS_KNOTS = [(2000, 1.00), (3000, 0.77), (4000, 0.64), (5000, 0.57), (6000, 0.54), (7000, 0.53), (8000, 0.52),
               (9000, 0.52), (10000, 0.52), (11000, 0.49), (12000, 0.45)]
STEPS_TARGET, STEPS_TARGET_60PLUS = 8000.0, 5600.0
# Zones 1-3 (min/week): Lee DH et al. Circulation 2022 moderate activity, falls up to 600 min/week; target 100.
ZONES13_KNOTS = [(10, 1.00), (47, 0.91), (112, 0.84), (187, 0.80), (262, 0.79), (337, 0.74), (412, 0.74), (525, 0.69),
                 (600, 0.68)]
ZONES13_TARGET = 100.0
# Zones 4-5 (min/week): 15-20 min/week of vigorous bursts, 18-24% lower mortality (paper, ref 48); VILPA ~31 min/week
# ~28% lower (Stamatakis 2022); Lee 2022 vigorous plateau; target 10.
ZONES45_KNOTS = [(0, 1.00), (17.5, 0.79), (31, 0.72), (150, 0.69)]
ZONES45_TARGET = 10.0
ZONE_TARGET_DECLINE = 0.10        # per decade after 30 ("decreases slightly with age"; design choice)
# Strength (min/week): Momma H et al. BJSM 2022 (lowest RR 0.83 at 40 min/week); break-even 40, no gain past 2 h.
STRENGTH_KNOTS = [(0, 1 / 0.83), (40, 1.00), (60, 0.98), (120, 0.98)]
STRENGTH_TARGET = 40.0
# VO2 max: 13% lower mortality per MET (3.5 mL/kg/min); target 44 (M) / 38 (F) at 30, falling 11% a decade after 30.
VO2_RR_PER_MET, ML_PER_MET = 0.87, 3.5
VO2_TARGET = {"male": 44.0, "female": 38.0}
VO2_DECLINE = 0.11
# Resting heart rate (during sleep): +9% per 10 bpm (Zhang D et al. CMAJ 2016); neutral 60 (M) / 64 (F).
RHR_RR_PER_10, RHR_TARGET = 1.09, {"male": 60.0, "female": 64.0}
# Lean body mass %: +11% per 10 points of body fat (paper, ref 83); target 80% (M) / 67% (F), the published age-30
# values, held at all ages.
LBM_RR_PER_10, LBM_TARGET = 1.11, {"male": 80.0, "female": 67.0}

UNITS = {"Hours of sleep": "h", "Sleep consistency": "%", "Steps": "steps/day", "Zones 1-3": "min/week",
         "Zones 4-5": "min/week", "Strength": "min/week", "VO2 max": "mL/kg/min", "Resting HR": "bpm",
         "Lean body mass": "%"}
KEYS = {"Hours of sleep": "sleep_h", "Sleep consistency": "consistency", "Steps": "steps", "Zones 1-3": "z13",
        "Zones 4-5": "z45", "Strength": "strength", "VO2 max": "vo2", "Resting HR": "rhr", "Lean body mass": "lbm"}


@dataclass
class Item:
    name: str
    value: float | None
    unit: str
    target: float
    years: float


@dataclass
class WhoopAge:
    body_age: float
    delta: float
    items: list[Item] = field(default_factory=list)
    missing: list[tuple[str, str]] = field(default_factory=list)
    pace: float | None = None
    pace_reason: str | None = None


def _ln_interp(x: float, knots: list[tuple[float, float]]) -> float:
    """ln HR, linear in ln HR between knots [(x, HR)], flat beyond both ends."""
    if x <= knots[0][0]:
        return math.log(knots[0][1])
    if x >= knots[-1][0]:
        return math.log(knots[-1][1])
    for (x0, h0), (x1, h1) in zip(knots, knots[1:]):
        if x0 <= x <= x1:
            f = (x - x0) / (x1 - x0)
            return math.log(h0) + f * (math.log(h1) - math.log(h0))
    raise AssertionError("unreachable")


def _decline(age: float, per_decade: float) -> float:
    return 1 - per_decade * max(age - 30.0, 0.0) / 10.0


def targets(age: float, sex: str) -> dict[str, float]:
    return {"Hours of sleep": SLEEP_TARGET_H, "Sleep consistency": CONSISTENCY_TARGET,
            "Steps": STEPS_TARGET if age < 60 else STEPS_TARGET_60PLUS,
            "Zones 1-3": ZONES13_TARGET * _decline(age, ZONE_TARGET_DECLINE),
            "Zones 4-5": ZONES45_TARGET * _decline(age, ZONE_TARGET_DECLINE), "Strength": STRENGTH_TARGET,
            "VO2 max": VO2_TARGET[sex] * _decline(age, VO2_DECLINE), "Resting HR": RHR_TARGET[sex],
            "Lean body mass": LBM_TARGET[sex]}


def _ln_hr(name: str, x: float, target: float) -> float:
    if name == "Hours of sleep":
        if x < SLEEP_TARGET_H:
            return (SLEEP_TARGET_H - x) * math.log(SLEEP_SHORT_RR_PER_H)
        return math.log(SLEEP_IN_RANGE_HR) if x <= SLEEP_LONG_H else 0.0
    if name == "Sleep consistency":
        lo, hi = CONSISTENCY_CLAMP
        return -CONSISTENCY_SLOPE * (min(max(x, lo), hi) - target)
    knots = {"Steps": STEPS_KNOTS, "Zones 1-3": ZONES13_KNOTS, "Zones 4-5": ZONES45_KNOTS,
             "Strength": STRENGTH_KNOTS}.get(name)
    if knots is not None:
        return _ln_interp(x, knots) - _ln_interp(target, knots)
    if name == "VO2 max":
        return (target - x) / ML_PER_MET * -math.log(VO2_RR_PER_MET)
    if name == "Resting HR":
        return (x - target) / 10.0 * math.log(RHR_RR_PER_10)
    if name == "Lean body mass":
        return (target - x) / 10.0 * math.log(LBM_RR_PER_10)
    raise ValueError(name)


def metric_years(values: dict[str, float | None], age: float, sex: str) -> dict[str, float]:
    """Years per metric (after the overlap factor), in NAMES order; a missing value counts 0."""
    tg = targets(age, sex)
    out = {}
    for name in NAMES:
        x = values.get(KEYS[name])
        if x is None:
            out[name] = 0.0
            continue
        y = YEARS_PER_LN_HR * _ln_hr(name, x, tg[name])
        out[name] = y if name in SLEEP_NAMES else OVERLAP * y
    return out


def _jaccard(a: tuple[float, float], b: tuple[float, float]) -> float | None:
    union = max(a[1], b[1]) - min(a[0], b[0])
    if union <= 0:
        return None
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) / union


def _night_scores(rows: list[DayRow]) -> list[float]:
    by = {date.fromisoformat(r.date): r for r in rows if r.onset_min is not None and r.wake_min is not None}
    scores = []
    for d in sorted(by):
        here = (by[d].onset_min, by[d].wake_min)
        sims = [_jaccard(here, (by[p].onset_min, by[p].wake_min))
                for p in (d - timedelta(days=k) for k in range(1, CONSISTENCY_PRIOR_NIGHTS + 1)) if p in by]
        sims = [s for s in sims if s is not None]
        if len(sims) >= CONSISTENCY_MIN_PRIOR:
            scores.append(100.0 * sum(sims) / len(sims))
    return scores


def consistency(rows: list[DayRow]) -> float | None:
    """WHOOP-like sleep consistency, 0-100%: per night, the mean overlap (intersection / union) of its [onset, wake]
    window with each of the previous 4 nights that have times (at least 2), then the mean over nights."""
    scores = _night_scores(rows)
    return sum(scores) / len(scores) if scores else None


def _vals(rows: list[DayRow], attr: str) -> list[float]:
    return [v for v in (getattr(r, attr, None) for r in rows) if v is not None]


def _values(rows: list[DayRow], as_of: date, days: int, min_days: int) -> tuple[dict, list[tuple[str, str]]]:
    lo = (as_of - timedelta(days=days - 1)).isoformat()
    w = [r for r in rows if lo <= r.date <= as_of.isoformat()]
    values: dict[str, float | None] = {}
    missing: list[tuple[str, str]] = []

    def mean(name, attr, scale=1.0, weekly=False):
        v = _vals(w, attr)
        if len(v) < min_days:
            missing.append((name, f"only {len(v)} days of data in {days} (need >= {min_days})"))
            return None
        return sum(v) / len(v) * (7.0 if weekly else 1.0) * scale

    values["sleep_h"] = mean("Hours of sleep", "sleep_min", 1 / 60)
    nights = _night_scores(w)
    if len(nights) < min_days:
        missing.append(("Sleep consistency", f"only {len(nights)} nights with sleep times (need >= {min_days})"))
        values["consistency"] = None
    else:
        values["consistency"] = sum(nights) / len(nights)
    values["steps"] = mean("Steps", "steps")
    values["z13"] = mean("Zones 1-3", "z13_min", weekly=True)
    values["z45"] = mean("Zones 4-5", "z45_min", weekly=True)
    values["strength"] = mean("Strength", "strength_min", weekly=True)
    vo2 = [(r.date, r.vo2) for r in w if r.vo2 is not None]
    recent = (as_of - timedelta(days=VO2_MAX_AGE_DAYS - 1)).isoformat()
    if len(vo2) >= VO2_MIN_READINGS and max(d for d, _ in vo2) >= recent:
        values["vo2"] = sum(v for _, v in vo2) / len(vo2)
    else:
        values["vo2"] = None
        missing.append(("VO2 max", f"{len(vo2)} readings in {days} days (need >= {VO2_MIN_READINGS}, the latest within "
                                   f"{VO2_MAX_AGE_DAYS} days)"))
    values["rhr"] = mean("Resting HR", "sleep_hr")
    lbm_lo = (as_of - timedelta(days=LBM_MAX_AGE_DAYS - 1)).isoformat()
    lbm = [(r.date, getattr(r, "lbm_pct", None)) for r in rows if lbm_lo <= r.date <= as_of.isoformat()]
    lbm = [x for x in lbm if x[1] is not None]
    if lbm:
        values["lbm"] = max(lbm)[1]
    else:
        values["lbm"] = None
        missing.append(("Lean body mass", f"no body fat or lean mass reading in the last {LBM_MAX_AGE_DAYS} days"))
    order = {n: i for i, n in enumerate(NAMES)}
    return values, sorted(missing, key=lambda m: order[m[0]])


def whoop_age(rows: list[DayRow], as_of: date, age: float, sex: str, days: int = WINDOW_DAYS,
              min_days: int = MIN_DAYS) -> WhoopAge:
    values, missing = _values(rows, as_of, days, min_days)
    years = metric_years(values, age, sex)
    tg = targets(age, sex)
    items = [Item(n, values[KEYS[n]], UNITS[n], tg[n], years[n]) for n in NAMES]
    delta = sum(years.values())
    out = WhoopAge(age + delta, delta, items, missing)
    lo = (as_of - timedelta(days=PACE_DAYS - 1)).isoformat()
    slept = sum(1 for r in rows if lo <= r.date <= as_of.isoformat() and r.sleep_min is not None)
    if slept < PACE_MIN_DAYS:
        out.pace_reason = f"only {slept} of the last {PACE_DAYS} days have sleep data (need >= {PACE_MIN_DAYS})"
        return out
    v30, _ = _values(rows, as_of, PACE_DAYS, PACE_MIN_DAYS)
    v30["vo2"], v30["lbm"] = values["vo2"], values["lbm"]
    t30 = sum(metric_years(v30, age, sex).values())
    out.pace = min(max(1 + (t30 - delta) / 0.5, PACE_RANGE[0]), PACE_RANGE[1])
    return out
