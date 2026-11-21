"""The compact workout log the daily state carries (state.json "workouts"): one entry per scored workout for the
last LOG_DAYS days, holding only what later commands read (context against earlier sessions, trends, the running
shape and race predictions, the weekly intensity split). The full per-workout result (traces, laps, every figure)
lives in the dashboard's per-workout documents, not here.

Entry keys (null values are left out): id (workout_id: local start to the minute with its offset), date (local
start date), kind (HealthKit activity type), minutes, indoor, provisional (scored before its 2-minute recovery
window was complete: the next run scores it again), edwards (the workout's Edwards TRIMP, the unit CTL uses),
banister (Banister TRIMP), hrss, te, te_an, rec_h, hrr60, hrr120, ef, dec, evo2 (Effective VO2 max of an outdoor
run), dist (m), secs (moving seconds), avg_hr, effort (1-10), srpe (effort x minutes), split (minutes low / mid /
high intensity), best ({distance label: seconds}), power (average running power, W), azm (Active Zone Minutes), benefit (the
training-effect label).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, timedelta

LOG_DAYS = 90
DECIMALS = {"minutes": 1, "edwards": 1, "banister": 1, "hrss": 1, "te": 1, "te_an": 1, "rec_h": 1, "hrr60": 0,
            "hrr120": 0, "ef": 3, "dec": 2, "evo2": 1, "dist": 0, "secs": 0, "avg_hr": 0, "effort": 1, "srpe": 0,
            "power": 0, "azm": 0}


@dataclass
class LogEntry:
    id: str
    date: str
    kind: str
    minutes: float
    indoor: bool | None = None
    provisional: bool = False
    edwards: float | None = None
    banister: float | None = None
    hrss: float | None = None
    te: float | None = None
    te_an: float | None = None
    rec_h: float | None = None
    hrr60: float | None = None
    hrr120: float | None = None
    ef: float | None = None
    dec: float | None = None
    evo2: float | None = None
    dist: float | None = None
    secs: float | None = None
    avg_hr: float | None = None
    effort: float | None = None
    srpe: float | None = None
    split: list[float] = field(default_factory=list)
    best: dict[str, float] = field(default_factory=dict)
    power: float | None = None
    azm: float | None = None
    benefit: str | None = None


KEYS = [f.name for f in fields(LogEntry)]


def _round(v, nd: int):
    v = round(float(v), nd)
    return int(v) if nd == 0 else v


def entry_to_dict(e: LogEntry) -> dict:
    """A log entry as stored: nulls, False provisional and empty lists left out; numbers rounded (DECIMALS)."""
    out = {}
    for k in KEYS:
        v = getattr(e, k)
        if v is None or (k == "provisional" and not v) or (k in ("split", "best") and not v):
            continue
        if k in DECIMALS:
            v = _round(v, DECIMALS[k])
        elif k == "split":
            v = [_round(x, 1) for x in v]
        elif k == "best":
            v = {lab: _round(s, 1) for lab, s in v.items()}
        out[k] = v
    return out


def entry_from_dict(d, file: str, where: str) -> LogEntry:
    from .inputs import InputError, _num, _object
    d = _object(d, file, where, KEYS)
    for k in ("id", "date", "kind"):
        if not isinstance(d.get(k), str) or not d.get(k):
            raise InputError(file, None, f"{where}.{k} must be a non-empty string")
    if d.get("minutes") is None:
        raise InputError(file, None, f"{where}.minutes is required")
    kw = {k: _num(d.get(k), file, f"{where}.{k}") for k in DECIMALS}
    benefit = d.get("benefit")
    if benefit is not None and not isinstance(benefit, str):
        raise InputError(file, None, f"{where}.benefit must be a string")
    indoor, prov = d.get("indoor"), d.get("provisional", False)
    if indoor is not None and not isinstance(indoor, bool) or not isinstance(prov, bool):
        raise InputError(file, None, f"{where}: indoor and provisional must be true or false")
    split = [_num(x, file, f"{where}.split") for x in d.get("split") or []]
    best = {str(k): _num(v, file, f"{where}.best") for k, v in (d.get("best") or {}).items()}
    return LogEntry(id=d["id"], date=d["date"], kind=d["kind"], indoor=indoor, provisional=prov, split=split,
                    best=best, benefit=benefit, **kw)


def merge_log(log: list[LogEntry], new: list[LogEntry], today: date) -> list[LogEntry]:
    """The log with new entries merged by id (new wins), keeping dates >= today - (LOG_DAYS - 1), sorted by id."""
    by = {e.id: e for e in log}
    by.update({e.id: e for e in new})
    lo = (today - timedelta(days=LOG_DAYS - 1)).isoformat()
    return [by[i] for i in sorted(by) if by[i].date >= lo]
