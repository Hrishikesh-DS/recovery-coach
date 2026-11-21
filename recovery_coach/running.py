"""Running metrics for one run, from Apple Watch workout statistics, the run's speed / power / distance samples and
its heart-rate readings. Pace-based fitness numbers are meant for outdoor runs (the caller decides).

- Efficiency Factor and aerobic decoupling (TrainingPeaks; Friel): the first 10 minutes dropped as warm-up, the rest
  split into halves by time; EF = mean speed (m/min) / mean heart rate (power / heart rate without speed);
  decoupling % = 100 (EF1 - EF2) / EF1, only with at least 20 steady minutes (TrainingPeaks: shorter sessions "aren't
  as valid"); under 5 % is good endurance. Laps stand in for the samples when there are none.
- Effective VO2 max (Runalyze LegacyEffectiveVO2max, after Daniels & Gilbert): %vVO2max = e^((HR/HRmax - 1.00466) /
  0.68725); v100 = speed (m/min) / %vVO2max; VO2 = -4.6 + 0.182258 v + 0.000104 v^2; valid for 50-1000 m/min.
- Running Index (Polar-style, Elevate's open formula): rate = 213.9 / t_min x d_km^1.06 + 3.5; intensity =
  min(1.45 HR/HRmax - 0.3, 1); index = rate / intensity; only for runs > 12 min, > 6 km/h, average heart-rate
  reserve 52-98 % (Polar's conditions).
- Mechanics: vertical ratio = 100 x vertical oscillation / stride length (Garmin, Runalyze); efficiency index =
  speed / power (GoldenCheetah); muscle load = average power x seconds / 1000 kJ (Polar Muscle Load).
- Power-vs-heart-rate line (ImReady4Py's idea): least squares power = a + b HR across easy runs, >= 4 runs.
- Stress scores, approx: flat running stress = hours x (speed / threshold speed)^2 x 100 (TrainingPeaks rTSS without
  grade adjustment); running-power stress = hours x (power / CP)^2 x 100 (Stryd's RSS has unpublished constants;
  this is the TSS form on power).
- Best efforts (Strava, Athlytic), approx: the fastest time for 400 m, 1 km, 1 mile, 5 km, 10 km and the half inside
  the run, from cumulative distance samples, interpolated between samples.
- Uth VO2 max (Uth et al. 2004): 15.3 x HRmax / resting HR, a rough cross-check.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .inputs import Lap, RunPoint, WorkoutStats

WARMUP_MIN = 10.0
STEADY_MIN = 20.0
EF_MIN = 10.0
HILLY_M_PER_KM = 10.0
BEST = (("400m", 400.0), ("1k", 1000.0), ("mile", 1609.344), ("5k", 5000.0), ("10k", 10000.0), ("half", 21097.5))


def is_run(kind: str) -> bool:
    return kind == "running"


def hilly(stats: WorkoutStats) -> bool:
    if not stats.dist or stats.elev_up is None:
        return False
    return stats.elev_up / (stats.dist / 1000.0) > HILLY_M_PER_KM


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _ef(series, hr, lo, hi):
    spd = _mean(p.speed for p in series if lo <= p.time < hi)
    pw = _mean(p.power for p in series if lo <= p.time < hi)
    h = _mean(b for t, b in hr if lo <= t < hi)
    if not h:
        return None
    if spd:
        return spd * 60.0 / h
    return pw / h if pw else None


def _lap_ef(laps: list[Lap], hr, lo, hi):
    dist = sum(l.dist or 0 for l in laps if lo <= l.start and l.end <= hi)
    secs = sum((l.end - l.start).total_seconds() for l in laps if lo <= l.start and l.end <= hi)
    h = _mean(b for t, b in hr if lo <= t < hi)
    return dist / secs * 60.0 / h if dist and secs and h else None


def efficiency(series: list[RunPoint], hr, start: datetime, end: datetime, laps: list[Lap] | None = None) -> dict:
    lo = start + timedelta(minutes=WARMUP_MIN)
    steady = (end - lo).total_seconds() / 60.0
    if steady < EF_MIN:
        return {"ef": None, "dec": None, "reason": "too short for efficiency"}
    mid = lo + (end - lo) / 2
    has_series = any(p.speed or p.power for p in series)
    f = _ef if has_series else (lambda s, h, a, b: _lap_ef(laps or [], h, a, b))
    ef = f(series, hr, lo, end)
    if ef is None:
        return {"ef": None, "dec": None, "reason": "no speed, power or laps"}
    if steady < STEADY_MIN:
        return {"ef": ef, "dec": None, "reason": "under 20 steady minutes"}
    e1, e2 = f(series, hr, lo, mid), f(series, hr, mid, end)
    dec = 100.0 * (e1 - e2) / e1 if e1 and e2 else None
    return {"ef": ef, "dec": dec, "reason": None if dec is not None else "no speed in one half"}


def effective_vo2(dist_m: float | None, secs: float | None, avg_hr: float | None, hrmax: float) -> float | None:
    if not dist_m or not secs or not avg_hr:
        return None
    v = dist_m / secs * 60.0
    p = math.exp((avg_hr / hrmax - 1.00466) / 0.68725)
    v100 = v / p
    if not 50 <= v100 <= 1000:
        return None
    return -4.6 + 0.182258 * v100 + 0.000104 * v100 ** 2


def running_index(dist_m, secs, avg_hr, hrmax: float, rest: float) -> float | None:
    if not dist_m or not secs or not avg_hr:
        return None
    t_min, d_km = secs / 60.0, dist_m / 1000.0
    hrr = (avg_hr - rest) / (hrmax - rest)
    if t_min <= 12 or d_km / (t_min / 60.0) <= 6 or not 0.52 <= hrr <= 0.98:
        return None
    rate = 213.9 / t_min * d_km ** 1.06 + 3.5
    intensity = min(1.45 * avg_hr / hrmax - 0.3, 1.0)
    return rate / intensity


def mechanics(stats: WorkoutStats) -> dict:
    secs = (stats.end - stats.start).total_seconds()
    return {"vert_ratio": 100.0 * stats.vo_cm / (stats.stride * 100.0) if stats.vo_cm and stats.stride else None,
            "eff_index": stats.avg_speed / stats.avg_power if stats.avg_speed and stats.avg_power else None,
            "muscle_kj": stats.avg_power * secs / 1000.0 if stats.avg_power and secs > 0 else None}


def power_hr_line(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """(a, b) for power = a + b x HR by least squares over (HR, power) points; None under 4 points."""
    if len(points) < 4:
        return None
    n = len(points)
    mx = sum(h for h, _ in points) / n
    my = sum(p for _, p in points) / n
    sxx = sum((h - mx) ** 2 for h, _ in points)
    if sxx == 0:
        return None
    b = sum((h - mx) * (p - my) for h, p in points) / sxx
    return my - b * mx, b


def rtss_flat(secs: float, avg_speed: float, threshold_speed: float) -> float:
    return secs / 3600.0 * (avg_speed / threshold_speed) ** 2 * 100.0


def rss(secs: float, avg_power: float, cp: float) -> float:
    return secs / 3600.0 * (avg_power / cp) ** 2 * 100.0


def best_efforts(series: list[RunPoint]) -> dict[str, float]:
    pts = [(p.time, p.dist) for p in series if p.dist is not None]
    if len(pts) < 2:
        return {}
    t0 = pts[0][0]
    ts = [(t - t0).total_seconds() for t, _ in pts]
    ds = [d for _, d in pts]
    out = {}
    for label, target in BEST:
        if ds[-1] - ds[0] < target:
            continue
        best, i = None, 0
        for j in range(1, len(ds)):
            while i + 1 < j and ds[j] - ds[i + 1] >= target:
                i += 1
            if ds[j] - ds[i] < target:
                continue
            want = ds[j] - target   # interpolate when the effort started
            k = i
            while k + 1 <= j and ds[k + 1] <= want:
                k += 1
            if ds[k + 1] == ds[k]:
                start_t = ts[k]
            else:
                start_t = ts[k] + (want - ds[k]) / (ds[k + 1] - ds[k]) * (ts[k + 1] - ts[k])
            dt = ts[j] - start_t
            best = dt if best is None or dt < best else best
        if best is not None:
            out[label] = best
    return out


def uth_vo2(hrmax: float, rest: float) -> float:
    return 15.3 * hrmax / rest
