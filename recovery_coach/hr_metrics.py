"""Per-workout heart-rate metrics from raw samples (Apple Watch: about one every 5 s during a workout and for about
3 minutes after it). Every function takes samples as [(time, bpm)] sorted by time.

Each sample counts for the time to the next sample (or to `end`), capped at 60 s, so a gap never inflates a total.

- Zones: Edwards' five zones, 50-60 ... 90-100 % HRmax (Edwards 1993); above HRmax counts as zone 5, below 50 %
  in none. split = minutes low (zones 1-2) / mid (3-4) / high (5), the grouping intervals.icu uses for its
  Polarization Index.
- Banister TRIMP (Banister 1991; Runalyze Trimp/Calculator): sum of minutes x r x e^(k r), r = heart-rate reserve
  fraction (hr - rest) / (hrmax - rest) clamped 0-1, k = 1.92 men / 1.67 women. The leading constant A (0.64 /
  0.86) is left out: it cancels in HRSS and in the intensity %.
- HRSS (Elevate, intervals.icu "HRSS"; TrainingPeaks TSS scale): 100 x TRIMP / (60 x r_LT x e^(k r_LT)), so one hour
  at threshold heart rate = 100. Threshold heart rate defaults to 0.9 x HRmax (Garmin's figure for experienced
  runners) until a tested value exists.
- Intensity % (Athlytic's TRIMPexp gauge): TRIMP as a share of the most a session of that length could score
  (r = 1 throughout: minutes x e^k).
- Active Zone Minutes (Fitbit; credit 1x / 2x per Fitabase): 1 a minute at 40-59 % of heart-rate reserve, 2 a minute
  at 60 % and above. The cutoffs are Fitbit's heart-rate-reserve zones as reported, not verified against Google: approx.
- Heart-rate recovery (Cole et al. NEJM 1999: a 1-minute drop <= 12 bpm is abnormal; Bevel counts only workouts that
  reach zone 4): end HR = the highest 10-second mean in the last 2 minutes before End (a mid-run peak would
  overstate recovery); HRR60 = end HR - the mean of readings 50-70 s after End; HRR120 = end HR - the lowest reading
  in the 2 minutes after End. Only when end HR >= 80 % HRmax and the readings are dense (median gap <= 15 s).
  Apple's own Cardio Recovery is a predicted, capped value and is kept apart, never mixed with these.
- HRRc (intervals.icu, as described on its forum): the largest drop within 60 s after at least 60 s continuously
  above threshold heart rate, inside the workout.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta

from . import zones

DENSE_GAP_S = 15.0
MAX_STEP_S = 60.0
ZONE_LIMITS = (0.5, 0.6, 0.7, 0.8, 0.9)
ZONE4 = 0.8
LOW_HRR60 = 12
RECOVERY_WAIT_S = 125
AZM_FAT_BURN, AZM_CARDIO = 0.40, 0.60

Samples = list[tuple[datetime, float]]


def dense(samples: Samples) -> bool:
    """True when the median gap between readings is at most 15 s."""
    if len(samples) < 2:
        return False
    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(samples, samples[1:])]
    return statistics.median(gaps) <= DENSE_GAP_S


def _weighted(samples: Samples, start: datetime, end: datetime):
    """(bpm, minutes) for each reading inside start-end, weighted by the time to the next reading (cap 60 s)."""
    inside = [s for s in samples if start <= s[0] < end]
    for i, (t, bpm) in enumerate(inside):
        nxt = inside[i + 1][0] if i + 1 < len(inside) else end
        yield bpm, min((nxt - t).total_seconds(), MAX_STEP_S) / 60.0


def _r(bpm: float, rest: float, hrmax: float) -> float:
    return min(max((bpm - rest) / (hrmax - rest), 0.0), 1.0)


def zone_minutes(samples: Samples, start: datetime, end: datetime, hrmax: float,
                 lim: list[float] | None = None) -> list[float]:
    """Minutes in zones 1-5; lim = the zone limits (zones.limits), default % of HRmax."""
    lim = lim or zones.default_limits(hrmax)
    z = [0.0] * 5
    for bpm, m in _weighted(samples, start, end):
        i = zones.zone_of(bpm, lim)
        if i:
            z[i - 1] += m
    return z


def split(zone_min: list[float]) -> list[float]:
    return [zone_min[0] + zone_min[1], zone_min[2] + zone_min[3], zone_min[4]]


def banister(samples: Samples, start: datetime, end: datetime, rest: float, hrmax: float, k: float) -> float:
    return sum(m * _r(b, rest, hrmax) * math.exp(k * _r(b, rest, hrmax)) for b, m in _weighted(samples, start, end))


def hrss(samples: Samples, start: datetime, end: datetime, rest: float, hrmax: float, k: float, lt_hr: float) -> float:
    r_lt = _r(lt_hr, rest, hrmax)
    return 100.0 * banister(samples, start, end, rest, hrmax, k) / (60.0 * r_lt * math.exp(k * r_lt))


def intensity_pct(banister_val: float, minutes: float, k: float) -> float:
    if minutes <= 0:
        return 0.0
    return 100.0 * banister_val / (minutes * math.exp(k))


def azm(samples: Samples, start: datetime, end: datetime, rest: float, hrmax: float) -> float:
    total = 0.0
    for b, m in _weighted(samples, start, end):
        r = (b - rest) / (hrmax - rest)
        total += 2 * m if r >= AZM_CARDIO else m if r >= AZM_FAT_BURN else 0.0
    return total


def _window_mean(samples: Samples, lo: datetime, hi: datetime) -> float | None:
    vals = [b for t, b in samples if lo <= t <= hi]
    return sum(vals) / len(vals) if vals else None


def end_hr(samples: Samples, end: datetime) -> float | None:
    """The highest 10-second mean heart rate in the 2 minutes before end."""
    lo = end - timedelta(seconds=120)
    best = None
    for t, _ in samples:
        if lo <= t <= end:
            m = _window_mean(samples, t, min(t + timedelta(seconds=10), end))
            if m is not None and (best is None or m > best):
                best = m
    return best


def hr_recovery(samples: Samples, end: datetime, hrmax: float, now: datetime) -> dict:
    out = {"hrr60": None, "hrr120": None, "end_hr": None, "flag": None, "reason": None, "provisional": False}
    around = [s for s in samples if end - timedelta(seconds=120) <= s[0] <= end + timedelta(seconds=180)]
    if now < end + timedelta(seconds=RECOVERY_WAIT_S):
        out.update(provisional=True, reason="ended too recently")
        return out
    if not dense(around):
        out["reason"] = "heart rate too sparse"
        return out
    peak = end_hr(samples, end)
    out["end_hr"] = round(peak, 1) if peak is not None else None
    if peak is None or peak < ZONE4 * hrmax:   # an effort level (80 % HRmax), not the display zones
        out["reason"] = "didn't reach zone 4"
        return out
    at60 = _window_mean(samples, end + timedelta(seconds=50), end + timedelta(seconds=70))
    after = [b for t, b in samples if end < t <= end + timedelta(seconds=120)]
    out["hrr60"] = round(peak - at60, 1) if at60 is not None else None
    out["hrr120"] = round(peak - min(after), 1) if after else None
    if out["hrr60"] is None:
        out["reason"] = "no reading a minute after the end"
    elif out["hrr60"] <= LOW_HRR60:
        out["flag"] = "low"
    return out


def hrrc(samples: Samples, start: datetime, end: datetime, lt_hr: float) -> float | None:
    inside = [s for s in samples if start <= s[0] <= end]
    if not dense(inside):
        return None
    best, run_start = None, None
    for i, (t, b) in enumerate(inside):
        if b > lt_hr:
            run_start = run_start or t
            continue
        if run_start is not None and (t - run_start).total_seconds() >= 60:
            prev = inside[i - 1][0]
            peak = _window_mean(inside, prev - timedelta(seconds=10), prev)
            nxt = [x for u, x in inside if prev < u <= prev + timedelta(seconds=60)]
            if peak is not None and nxt:
                drop = peak - min(nxt)
                best = drop if best is None or drop > best else best
        run_start = None
    return round(best, 1) if best is not None else None
