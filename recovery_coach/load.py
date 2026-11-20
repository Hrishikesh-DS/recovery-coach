"""Training load: CTL / ATL / TSB and acute:chronic ratio (spec 5.5)."""
from __future__ import annotations

import math
import statistics
from datetime import date

from .inputs import DayRow, Workout
from .strain import dedupe_workouts, zone_weight

CTL_TAU = 42.0
ATL_TAU = 7.0
SEED_DAYS = 14
ACWR_DAYS = 28
ACWR_ACUTE_DAYS = 7
SPIKE_ABOVE = 1.5
DETRAINING_BELOW = 0.8


def _step(prev: float, trimp: float, tau: float) -> float:
    alpha = 1.0 - math.exp(-1.0 / tau)
    return prev + alpha * (trimp - prev)


def update(prev: DayRow | None, trimp: float) -> dict:
    if prev is None or prev.ctl is None or prev.atl is None:
        ctl = atl = float(trimp)
    else:
        ctl = _step(prev.ctl, trimp, CTL_TAU)
        atl = _step(prev.atl, trimp, ATL_TAU)
    return {"ctl": ctl, "atl": atl, "tsb": ctl - atl}


def seed(first_trimps: list[float]) -> dict:
    head = first_trimps[:SEED_DAYS]
    m = statistics.fmean(head) if head else 0.0
    return {"ctl": m, "atl": m, "tsb": 0.0}


def acwr(history: list[DayRow]) -> float | None:
    rows = sorted(history, key=lambda r: r.date)[-ACWR_DAYS:]
    if len(rows) < ACWR_DAYS or any(r.trimp is None for r in rows):
        return None
    chronic = statistics.fmean(r.trimp for r in rows)
    if chronic == 0:
        return None
    acute = statistics.fmean(r.trimp for r in rows[-ACWR_ACUTE_DAYS:])
    return acute / chronic


def acwr_flag(x: float) -> str | None:
    if x > SPIKE_ABOVE:
        return "spike"
    if x < DETRAINING_BELOW:
        return "detraining"
    return None


def seed_from_workouts(ws: list[Workout], hrmax: float, days: list[date]) -> dict[date, float]:
    """Approximate daily TRIMP: duration x zone weight of avg HR, per local start date."""
    out = {d: 0.0 for d in days}
    for w in dedupe_workouts(ws):
        d = w.start.date()  # local date from the timestamp's own offset
        if d in out and w.avg_hr is not None:
            out[d] += (w.end - w.start).total_seconds() / 60.0 * zone_weight(w.avg_hr, hrmax)
    return out
