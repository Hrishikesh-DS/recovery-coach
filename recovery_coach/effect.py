"""Approximate training effect (aerobic and anaerobic, 0-5), recovery hours and a "primary benefit" label.

Firstbeat (Garmin, Suunto, Amazfit) never published its EPOC equations; its white papers give the shape (EPOC from
heart-rate-derived intensity, Training Effect 0-5 from peak EPOC scaled to fitness: 2-2.9 maintaining, 3-3.9
improving, 4-4.9 highly improving, 5 overreaching). This module follows the open RunningMate documentation
(axelmichel/runningmate, metrics/trainings-effect), re-implemented from its formulas, so every output is "approx":

- intensity I = heart-rate reserve fraction, clamped 0-1. Each step dt (minutes, the time to the next reading,
  capped at 1 minute) adds EPOC toward a steady state: g = 0.015 (e^(6.5 I) - 1), tau = 10 + 50 I^3 minutes,
  ss = g tau, EPOC = ss + (EPOC - ss) e^(-dt / tau). With dense readings (median gap <= 15 s) a gap over 10 s is a
  pause and counts I = 0 after its first 5 s.
- aerobic TE from peak EPOC (ml/kg) against thresholds T = (6, 15, 35, 80, 180) x s, s = clamp(VO2max / 45, 0.75,
  1.4) (s = 1 without VO2 max): linear 0 -> 1 below T1, k + ln(peak / T_k) / ln(T_k+1 / T_k) between, 5.0 at T5.
- anaerobic TE (dense readings only): A = sum((I - 0.85) / 0.15)^2 dt while I > 0.85; bursts = stretches of >= 15 s
  at I >= 0.9; b = 1 + min(0.25 (n - 1), 2); TE_an = 5 (1 - e^(-A b / 15)).
- recovery hours = min(24 (peak / (35 s))^0.75, 96).
- benefit (my rule, after Garmin's seven labels): Anaerobic when TE_an >= 2 and TE_an >= TE - 1; Recovery when TE < 1;
  otherwise by the zone with the most minutes: 1 Recovery, 2 Base, 3 Tempo, 4 Threshold, 5 VO2 max.
"""
from __future__ import annotations

import math

from .hr_metrics import Samples, dense

THRESHOLDS = (6.0, 15.0, 35.0, 80.0, 180.0)
PAUSE_S = 10.0
BURST_S = 15.0
BENEFIT_BY_ZONE = ("Recovery", "Base", "Tempo", "Threshold", "VO2 max")


def _scale(vo2max: float | None) -> float:
    return 1.0 if vo2max is None else min(max(vo2max / 45.0, 0.75), 1.4)


def aerobic_te(peak: float, s: float) -> float:
    t = [x * s for x in THRESHOLDS]
    if peak <= 0:
        return 0.0
    if peak < t[0]:
        return peak / t[0]
    if peak >= t[4]:
        return 5.0
    for k in range(4):
        if t[k] <= peak < t[k + 1]:
            return (k + 1) + math.log(peak / t[k]) / math.log(t[k + 1] / t[k])
    return 5.0


def benefit_label(te: float, te_an: float | None, zone_min: list[float]) -> str:
    if te_an is not None and te_an >= 2 and te_an >= te - 1:
        return "Anaerobic"
    if te < 1 or not any(zone_min):
        return "Recovery"
    return BENEFIT_BY_ZONE[max(range(5), key=lambda i: zone_min[i])]


def recovered_pct(rec_h: float, hours_since_end: float) -> int:
    if rec_h <= 0:
        return 100
    return int(min(max(100.0 * hours_since_end / rec_h, 0.0), 100.0))


def training_effect(samples: Samples, start, end, rest: float, hrmax: float, vo2max: float | None,
                    zone_min: list[float]) -> dict:
    inside = [x for x in samples if start <= x[0] < end]
    is_dense = dense(inside)
    s = _scale(vo2max)
    epoc = peak = a_load = 0.0
    bursts, burst_s = 0, 0.0
    for i, (t, bpm) in enumerate(inside):
        nxt = inside[i + 1][0] if i + 1 < len(inside) else end
        gap = min((nxt - t).total_seconds(), 60.0)
        intensity = min(max((bpm - rest) / (hrmax - rest), 0.0), 1.0)
        steps = [(gap, intensity)]
        if is_dense and gap > PAUSE_S:
            steps = [(5.0, intensity), (gap - 5.0, 0.0)]
        for sec, inten in steps:
            dt = sec / 60.0
            g = 0.015 * (math.exp(6.5 * inten) - 1.0)
            tau = 10.0 + 50.0 * inten ** 3
            ss = g * tau
            epoc = ss + (epoc - ss) * math.exp(-dt / tau)
            peak = max(peak, epoc)
            if inten > 0.85:
                a_load += ((inten - 0.85) / 0.15) ** 2 * dt
            if inten >= 0.9:
                burst_s += sec
            else:
                if burst_s >= BURST_S:
                    bursts += 1
                burst_s = 0.0
    if burst_s >= BURST_S:
        bursts += 1
    te = aerobic_te(peak, s)
    te_an, reason = None, None
    if is_dense:
        b = 1.0 + min(0.25 * (bursts - 1), 2.0) if bursts else 1.0
        te_an = 5.0 * (1.0 - math.exp(-a_load * b / 15.0))
    else:
        reason = "heart rate too sparse for anaerobic effect"
    rec_h = min(24.0 * (peak / (35.0 * s)) ** 0.75, 96.0) if peak > 0 else 0.0
    return {"te": round(te, 2), "te_an": round(te_an, 2) if te_an is not None else None, "peak_epoc": peak,
            "rec_h": rec_h, "benefit": benefit_label(te, te_an, zone_min), "approx": True, "reason": reason}
