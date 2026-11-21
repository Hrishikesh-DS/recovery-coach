"""Race predictions, training paces, marathon shape and critical speed.

- Daniels & Gilbert (Oxygen Power, 1979; Runalyze DanielsGilbertFormula): VO2(v) = -4.6 + 0.182258 v + 0.000104 v^2
  (v in m/min); %max(t) = 0.8 + 0.1894393 e^(-0.012778 t) + 0.2989558 e^(-0.1932605 t) (t in min);
  race VDOT = VO2(v) / %max(t).
- Running shape (Runalyze JD/Shape): the duration-weighted mean of the Effective VO2 max of outdoor runs over the
  last 30 days.
- Marathon shape (Runalyze BasicEndurance): weekly-km target max(VO2, 25)^1.135; long-run target
  ln(max(VO2, 25) / 4) x 12 - 13 km beyond the 13 km minimum; week-km % = run km in the window x 7 / days /
  target (window up to 182 days, at least 70 counted); each run over 13 km in the last 70 days scores
  (2 - 2/70 x days ago) x ((km - 13) / target)^2, long-run % = sum x 7 / 70; shape % = 100 (0.67 week + 0.33 long).
- Prediction (Runalyze Prognosis/VO2max): required = km^1.23; factor = max(0, 1 - (required - shape %) / 100);
  adj = min(1, 0.6 + 0.4 factor); the time t solving VO2(d / t) / %max(t) = VO2 x adj, by bisection between 2 and
  10 min/km.
- Riegel (1981): t2 = t1 (d2 / d1)^1.06, shown for 5-10 km only (too fast for the marathon: Vickers & Vertosick 2016).
- Training paces: speeds at fixed shares of vVDOT (GoldenCheetah's Daniels table): easy 0.72, marathon 0.85,
  threshold 0.90, interval 0.98, repetition 1.05; returned as seconds per km.
- Critical speed and D' (Monod & Scherrer; Jones & Vanhatalo 2017), approx: least squares distance = CS x t + D'
  over best efforts of 2-15 minutes, needing at least two different durations.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .workout_log import LogEntry

SHAPE_DAYS = 30
WEEK_WINDOW, WEEK_MIN_DAYS, LONG_WINDOW, LONG_MIN_KM = 182, 70, 70, 13.0
RACES = {"5k": 5000.0, "10k": 10000.0, "half": 21097.5, "marathon": 42195.0}
PACE_SHARES = {"easy": 0.72, "marathon": 0.85, "threshold": 0.90, "interval": 0.98, "repetition": 1.05}
CS_RANGE = (120.0, 900.0)


def vo2_at(v: float) -> float:
    return -4.6 + 0.182258 * v + 0.000104 * v * v


def pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot_from_race(dist_m: float, secs: float) -> float:
    t = secs / 60.0
    return vo2_at(dist_m / t) / pct_max(t)


def _days_ago(e: LogEntry, as_of: date) -> int:
    return (as_of - date.fromisoformat(e.date)).days


def shape(entries: list[LogEntry], as_of: date, days: int = SHAPE_DAYS) -> float | None:
    use = [e for e in entries if e.kind == "running" and e.indoor is not True and e.evo2 and e.secs
           and 0 <= _days_ago(e, as_of) < days]
    total = sum(e.secs for e in use)
    return sum(e.evo2 * e.secs for e in use) / total if total else None


def marathon_shape(entries: list[LogEntry], as_of: date, vo2: float) -> float:
    v = max(vo2, 25.0)
    week_target = v ** 1.135
    long_target = math.log(v / 4.0) * 12.0 - LONG_MIN_KM
    runs = [e for e in entries if e.kind == "running" and e.dist and 0 <= _days_ago(e, as_of) < WEEK_WINDOW]
    if not runs:
        return 0.0
    span = max(_days_ago(e, as_of) for e in runs) + 1
    days = max(min(WEEK_WINDOW, span), WEEK_MIN_DAYS)
    week = sum(e.dist for e in runs) / 1000.0 * 7.0 / days / week_target
    points = 0.0
    for e in runs:
        ago, km = _days_ago(e, as_of), e.dist / 1000.0
        if ago < LONG_WINDOW and km > LONG_MIN_KM:
            points += (2.0 - 2.0 / LONG_WINDOW * ago) * ((km - LONG_MIN_KM) / long_target) ** 2
    long = points * 7.0 / LONG_WINDOW
    return 100.0 * (0.67 * week + 0.33 * long)


def predict(dist_m: float, vo2: float, shape_pct: float) -> float:
    km = dist_m / 1000.0
    factor = max(0.0, 1.0 - (km ** 1.23 - shape_pct) / 100.0)
    want = vo2 * min(1.0, 0.6 + 0.4 * factor)
    lo, hi = 2.0 * km, 10.0 * km   # minutes
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if vo2_at(dist_m / mid) / pct_max(mid) > want:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 * 60.0


def predictions(vo2: float, shape_pct: float) -> dict[str, float]:
    return {k: predict(d, vo2, shape_pct) for k, d in RACES.items()}


def riegel(t1: float, d1: float, d2: float, e: float = 1.06) -> float:
    return t1 * (d2 / d1) ** e


def _v_at_vo2(vo2: float) -> float:
    a, b, c = 0.000104, 0.182258, -4.6 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def paces(vdot: float) -> dict[str, float]:
    v = _v_at_vo2(vdot)   # m/min at VO2 max
    return {k: 1000.0 / (v * share) * 60.0 for k, share in PACE_SHARES.items()}


def critical_speed(efforts: list[tuple[float, float]]) -> tuple[float, float] | None:
    pts = [(t, d) for t, d in efforts if CS_RANGE[0] <= t <= CS_RANGE[1]]
    if len({t for t, _ in pts}) < 2:
        return None
    n = len(pts)
    mt, md = sum(t for t, _ in pts) / n, sum(d for _, d in pts) / n
    stt = sum((t - mt) ** 2 for t, _ in pts)
    cs = sum((t - mt) * (d - md) for t, d in pts) / stt
    return cs, md - cs * mt
