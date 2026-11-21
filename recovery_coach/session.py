"""Scoring workouts: one result dict per workout (the dashboard's per-workout document) and its compact log entry
(workout_log.LogEntry), plus the daily training figures that read the log.

A workout is scored once: `workout` (on demand, right after a session) and `run` (the morning, for anything not yet
scored) both call score_new, which skips ids already in the log unless they are provisional (scored before the
2 minutes after the end had been recorded). Duplicate logs of one session (Apple's own and another app's copy) are
collapsed by strain.dedupe_workouts first.

Inputs per workout: its statistics (wk_stats.json, matched by start within 2 minutes, else by overlap), its raw heart
rate (wk_hr.csv, start to end + 3 min; minute heart rate from minutes.csv when there is none, which leaves the
burst- and recovery-based figures empty), its run samples (wk_series.csv) and an effort answer (efforts.json).
"now" is the newest timestamp in the inputs.

Result dict (every value may be null):
  id, kind, start, end (ISO), minutes, indoor, provisional
  hr        {avg, max, zones: [min in zones 1-5], split: [low, mid, high min], trace: [[sec from start, bpm]]
             (15-s means, wider when needed to stay within TRACE_MAX points, to 3 min after the end),
             limits: [bpm where zones 1-5 start]}
  load      {strain (0-21), edwards, banister, hrss, intensity_pct, azm, effort (1-10), effort_src "you"|"apple",
             srpe, level (Polar band vs the same activity, 90 days), d_fitness, d_fatigue, z_typical (vs the last 10
             of the same activity)}
  effect    {te, te_an, rec_h, benefit, approx: true, reason}
  recovery  {hrr60, hrr120, end_hr, flag, reason, provisional, apple (Apple's Cardio Recovery), hrrc}
  run       null unless a run: {dist, secs, pace_s_km, hilly, ef, dec, evo2, ri, perf_cond, vert_ratio, eff_index,
             muscle_kj, rtss, rss, best: {label: seconds}, pace_trace: [[sec, s/km]], splits: [{n, dist, secs,
             pace_s_km, avg_hr}], approx: [names of approximated fields]}
  notes     [short reasons a figure is missing, e.g. "no history yet", "indoor: pace-based numbers skipped"]
"""
from __future__ import annotations

import math
import statistics
from dataclasses import replace
from datetime import date, datetime, timedelta

from . import hr_metrics as hm
from . import race, running, training
from .effect import training_effect
from .inputs import DayRow, Inputs, RunPoint, Settings, Workout, WorkoutStats, workout_id
from .strain import dedupe_workouts, strain
from .workout_log import LogEntry, merge_log

TRACE_MAX = 400
TRACE_STEP_S = 15
PACE_STEP_S = 30
AFTER = timedelta(minutes=3)
MATCH_S = 120
LT_FRACTION = 0.9
DEFAULT_REST = 60.0


def _r(x, nd=1):
    return None if x is None else round(x, nd)


def match_stats(w: Workout, stats: list[WorkoutStats]) -> WorkoutStats | None:
    near = [s for s in stats if abs((s.start - w.start).total_seconds()) <= MATCH_S]
    if near:
        return min(near, key=lambda s: abs((s.start - w.start).total_seconds()))
    best, best_ov = None, 0.0
    for s in stats:
        ov = (min(s.end, w.end) - max(s.start, w.start)).total_seconds()
        span = max((w.end - w.start).total_seconds(), 1.0)
        if ov / span > best_ov:
            best, best_ov = s, ov / span
    return best if best_ov > 0.8 else None


def _trace(points: list[tuple[datetime, float]], start: datetime, end: datetime, step_s: int) -> list[list]:
    span = (end - start).total_seconds()
    step = max(step_s, math.ceil(span / TRACE_MAX)) if span > 0 else step_s
    buckets: dict[int, list[float]] = {}
    for t, v in points:
        if start <= t <= end and v is not None:
            buckets.setdefault(int((t - start).total_seconds() // step), []).append(v)
    return [[b * step, round(sum(vs) / len(vs), 1)] for b, vs in sorted(buckets.items())][:TRACE_MAX]


def _rest(rows: list[DayRow]) -> float:
    for r in reversed(rows):
        if r.rhr:
            return r.rhr
    for r in reversed(rows):
        if r.sleep_hr:
            return r.sleep_hr
    return DEFAULT_REST


def _vo2(rows: list[DayRow]) -> float | None:
    for r in reversed(rows):
        if r.vo2:
            return r.vo2
    return None


def _load_before(rows: list[DayRow], d: str) -> DayRow | None:
    prior = [r for r in rows if r.date < d and r.ctl is not None and r.atl is not None]
    return prior[-1] if prior else None


def _effort(wid: str, st: WorkoutStats | None, answers: dict[str, float]) -> tuple[float | None, str | None]:
    if wid in answers:
        return answers[wid], "you"
    if st is not None and st.effort is not None:
        return st.effort, "you"
    if st is not None and st.effort_est is not None:
        return st.effort_est, "apple"
    return None, None


def _run_block(w, st, hr, series, entry, log, hrmax, rest, notes) -> dict:
    secs = (w.end - w.start).total_seconds()
    dist = st.dist if st else None
    inside = [p for p in series if w.start <= p.time <= w.end]
    if dist is None:
        dists = [p.dist for p in inside if p.dist is not None]
        dist = max(dists) if dists else None
    indoor = bool(st and st.indoor)
    hilly = bool(st and running.hilly(st))
    avg_hr = (st.avg_hr if st and st.avg_hr else None) or entry.avg_hr
    eff = running.efficiency(inside, hr, w.start, w.end, st.laps if st else None)
    block = {"dist": _r(dist, 0), "secs": round(secs), "pace_s_km": _r(secs / (dist / 1000.0), 0) if dist else None,
             "hilly": hilly, "ef": None, "dec": None, "evo2": None, "ri": None, "perf_cond": None, "best": {},
             "rtss": None, "rss": None, "approx": ["perf_cond", "rtss", "rss", "best"],
             **{k: _r(v, 4 if k == "eff_index" else 1) for k, v in (running.mechanics(st).items() if st else
                                                                    (("vert_ratio", None), ("eff_index", None),
                                                                     ("muscle_kj", None)))}}
    if indoor:
        notes.append("indoor: pace-based numbers skipped")
    else:
        block.update(ef=_r(eff["ef"], 3), dec=_r(eff["dec"], 1),
                     evo2=_r(running.effective_vo2(dist, secs, avg_hr, hrmax)),
                     ri=_r(running.running_index(dist, secs, avg_hr, hrmax, rest)),
                     best={k: round(v, 1) for k, v in running.best_efforts(inside).items()})
        if eff["reason"]:
            notes.append(eff["reason"])
        entry.evo2 = block["evo2"]
        entry.best = dict(block["best"])
        if not hilly:
            entry.ef, entry.dec = block["ef"], block["dec"]
        block["perf_cond"] = training.performance_condition(entry, log)
    entry.dist = dist
    entry.power = st.avg_power if st else None
    vdot = race.shape(log, date.fromisoformat(entry.date)) or entry.evo2
    if vdot and dist and secs:
        thr = 1000.0 / race.paces(vdot)["threshold"]
        block["rtss"] = _r(running.rtss_flat(secs, dist / secs, thr), 0)
    pts = [(e.avg_hr, e.power) for e in log if e.kind == "running" and e.indoor is not True and e.avg_hr and e.power]
    if avg_hr and entry.power:
        pts.append((avg_hr, entry.power))
    line = running.power_hr_line(pts)
    if line and entry.power:
        cp = line[0] + line[1] * LT_FRACTION * hrmax
        block["rss"] = _r(running.rss(secs, entry.power, cp), 0) if cp > 0 else None
    speeds = [(p.time, 1000.0 / p.speed) for p in inside if p.speed]
    block["pace_trace"] = _trace(speeds, w.start, w.end, PACE_STEP_S)
    block["splits"] = []
    for n, lap in enumerate(st.laps if st else [], 1):
        ls = (lap.end - lap.start).total_seconds()
        lh = [b for t, b in hr if lap.start <= t < lap.end]
        block["splits"].append({"n": n, "dist": _r(lap.dist, 0), "secs": round(ls),
                                "pace_s_km": _r(ls / (lap.dist / 1000.0), 0) if lap.dist else None,
                                "avg_hr": _r(sum(lh) / len(lh), 0) if lh else None})
    return block


def score_workout(w: Workout, st: WorkoutStats | None, hr, series: list[RunPoint], settings: Settings,
                  hrmax: float, rest: float, vo2max: float | None, k: float, log: list[LogEntry],
                  rows: list[DayRow], now: datetime, answers: dict[str, float]) -> tuple[dict, LogEntry]:
    wid = workout_id(w.start)
    minutes = (w.end - w.start).total_seconds() / 60.0
    kind = w.kind
    indoor = st.indoor if st else None
    bk = 1.67 if settings.sex == "female" else 1.92
    lt = LT_FRACTION * hrmax
    notes: list[str] = []
    entry = LogEntry(id=wid, date=w.start.date().isoformat(), kind=kind, minutes=minutes, indoor=indoor,
                     secs=(w.end - w.start).total_seconds())
    history = [e for e in log if e.id != wid]
    if not any(e.kind == kind for e in history):
        notes.append("no history yet")
    inside = [x for x in hr if w.start <= x[0] < w.end]
    zones = hm.zone_minutes(hr, w.start, w.end, hrmax) if inside else [0.0] * 5
    hr_block = {"avg": _r(sum(b for _, b in inside) / len(inside), 0) if inside else None,
                "max": _r(max(b for _, b in inside), 0) if inside else None,
                "zones": [round(z, 1) for z in zones], "split": [round(z, 1) for z in hm.split(zones)],
                "trace": _trace(hr, w.start, w.end + AFTER, TRACE_STEP_S),
                "limits": [round(f * hrmax) for f in hm.ZONE_LIMITS]}
    effort, src = _effort(wid, st, answers)
    load = {"strain": None, "edwards": None, "banister": None, "hrss": None, "intensity_pct": None, "azm": None,
            "effort": effort, "effort_src": src, "srpe": _r(effort * minutes, 0) if effort else None,
            "level": None, "d_fitness": None, "d_fatigue": None, "z_typical": None}
    effect = {"te": None, "te_an": None, "rec_h": None, "benefit": None, "approx": True, "reason": None}
    rec = {"hrr60": None, "hrr120": None, "end_hr": None, "flag": None, "reason": None, "provisional": False,
           "apple": st.cardio_recovery if st else None, "hrrc": None}
    if inside:
        edw = sum(z * (i + 1) for i, z in enumerate(zones))
        ban = hm.banister(hr, w.start, w.end, rest, hrmax, bk)
        load.update(strain=_r(strain(edw, k), 1), edwards=_r(edw), banister=_r(ban),
                    hrss=_r(hm.hrss(hr, w.start, w.end, rest, hrmax, bk, lt)),
                    intensity_pct=_r(hm.intensity_pct(ban, minutes, bk), 0),
                    azm=_r(hm.azm(hr, w.start, w.end, rest, hrmax), 0))
        te = training_effect(hr, w.start, w.end, rest, hrmax, vo2max, zones)
        effect.update(te=_r(te["te"]), te_an=_r(te["te_an"]), rec_h=_r(te["rec_h"]), benefit=te["benefit"],
                      reason=te["reason"])
        rec.update(hm.hr_recovery(hr, w.end, hrmax, now))
        rec["hrrc"] = hm.hrrc(hr, w.start, w.end, lt)
        if effect["reason"]:
            notes.append(effect["reason"])
    else:
        notes.append("no heart-rate readings")
        rec["reason"] = "no heart-rate readings"
    if rec["reason"] and rec["reason"] not in notes:
        notes.append(rec["reason"])
    entry = replace(entry, provisional=bool(rec["provisional"]), edwards=load["edwards"], banister=load["banister"],
                    hrss=load["hrss"], te=effect["te"], te_an=effect["te_an"], rec_h=effect["rec_h"],
                    hrr60=rec["hrr60"], hrr120=rec["hrr120"], avg_hr=hr_block["avg"], effort=effort,
                    srpe=load["srpe"], split=list(hr_block["split"]), azm=load["azm"], benefit=effect["benefit"])
    if load["edwards"] is not None:
        load["level"] = training.session_level(entry, history)
        load["z_typical"] = _r(training.typical_z(entry, history, "edwards"), 2)
        before = _load_before(rows, entry.date)
        if before is not None:
            df, dfat = training.increments(load["edwards"], before.ctl, before.atl)
            load.update(d_fitness=_r(df, 1), d_fatigue=_r(dfat, 1))
    run = _run_block(w, st, hr, series, entry, history, hrmax, rest, notes) if running.is_run(kind) else None
    doc = {"id": wid, "kind": kind, "start": w.start.isoformat(), "end": w.end.isoformat(), "minutes": round(minutes, 1),
           "indoor": indoor, "provisional": entry.provisional, "hr": hr_block, "load": load, "effect": effect,
           "recovery": rec, "run": run, "notes": notes}
    return doc, entry


def newest_time(inp: Inputs) -> datetime | None:
    times = [t for t, _ in inp.wk_hr or []] + [p.time for p in inp.wk_series or []]
    times += [w.end for w in inp.workouts or []] + [h.start for h in inp.hours or []] + [t for t, _ in inp.minutes or []]
    return max(times) if times else None


def score_new(inp: Inputs, hrmax: float, k: float, rows: list[DayRow], today: date
              ) -> tuple[list[dict], list[dict], list[LogEntry]]:
    """(result dicts of newly scored workouts, effort updates for workouts scored earlier, the merged log)."""
    s = inp.state.settings
    log = list(inp.state.workouts)
    logged = {e.id: e for e in log}
    answers = inp.efforts or {}
    rest, vo2max = _rest(rows), _vo2(rows)
    now = newest_time(inp) or datetime.combine(today, datetime.min.time())
    docs, new = [], []
    for w in dedupe_workouts(inp.workouts or []):
        wid = workout_id(w.start)
        if wid in logged and not logged[wid].provisional:
            continue
        hr = sorted(x for x in inp.wk_hr or [] if w.start <= x[0] <= w.end + AFTER)
        if not hr:
            hr = sorted(x for x in inp.minutes or [] if w.start <= x[0] <= w.end + AFTER)
            if wid in logged:
                continue   # a provisional workout with nothing new to rescore it from
        st = match_stats(w, inp.wk_stats or [])
        doc, entry = score_workout(w, st, hr, inp.wk_series or [], s, hrmax, rest, vo2max, k, log + new, rows,
                                   now, answers)
        docs.append(doc)
        new.append(entry)
    updates = []
    fresh = {e.id for e in new}
    for wid, value in answers.items():
        if wid in logged and wid not in fresh:
            e = logged[wid]
            upd = replace(e, effort=value, srpe=round(value * e.minutes))
            new.append(upd)
            updates.append({"id": wid, "effort": value, "effort_src": "you", "srpe": float(upd.srpe)})
    return docs, updates, merge_log(log, new, today)


# ---------------------------------------------------------------- daily figures from the log
def _day_sum(log: list[LogEntry], d: date, attr: str) -> float:
    return sum(getattr(e, attr) or 0.0 for e in log if e.date == d.isoformat())


def _covers(log: list[LogEntry], d: date, days: int) -> bool:
    return bool(log) and min(e.date for e in log) <= (d - timedelta(days=days - 1)).isoformat()


def log_rows(log: list[LogEntry], d: date, days: int) -> list[DayRow]:
    """Day rows (wload, srpe) built from the log for the days ending d, from the log's first date on."""
    first = min(e.date for e in log) if log else d.isoformat()
    out = []
    for i in range(days - 1, -1, -1):
        day = d - timedelta(days=i)
        if day.isoformat() >= first:
            out.append(DayRow(date=day.isoformat(), wload=_day_sum(log, day, "banister"),
                              srpe=_day_sum(log, day, "srpe")))
    return out


def day_training(log: list[LogEntry], rows: list[DayRow], d: date) -> dict:
    """Row values for day d: wload, srpe, azm, mono, tstrain, form_pct, ramp, pi7."""
    out = {"wload": round(_day_sum(log, d, "banister"), 1), "srpe": round(_day_sum(log, d, "srpe")),
           "azm": round(_day_sum(log, d, "azm")), "mono": None, "tstrain": None, "form_pct": None, "ramp": None,
           "pi7": None}
    if _covers(log, d, 7):
        m, st, _ = training.monotony([r.wload for r in log_rows(log, d, 7)])
        out.update(mono=round(m, 2), tstrain=round(st))
    row = next((r for r in rows if r.date == d.isoformat()), None)
    if row is not None:
        fp = training.form_pct(row.ctl, row.atl)
        out["form_pct"] = _r(fp, 1)
    out["ramp"] = _r(training.ramp(rows, d), 1)
    out["pi7"] = _r(training.polarization(training.split_window(log, d, 7)), 2)
    return out


def _vo2_trend(rows: list[DayRow], d: date) -> float | None:
    recent = [r.vo2 for r in rows if r.vo2 and (d - timedelta(days=13)).isoformat() <= r.date <= d.isoformat()]
    older = [r.vo2 for r in rows if r.vo2 and (d - timedelta(days=60)).isoformat() <= r.date
             < (d - timedelta(days=13)).isoformat()]
    if not recent or not older:
        return None
    return statistics.fmean(recent) - statistics.fmean(older)


def training_card(log: list[LogEntry], rows: list[DayRow], y: date, t: date, k: float, acwr: float | None,
                  ri: dict | None) -> dict:
    row = next((r for r in rows if r.date == y.isoformat()), None)
    ctl, atl = (row.ctl, row.atl) if row is not None else (None, None)
    mono = flag = tstrain = None
    if _covers(log, y, 7):
        mono, tstrain, flag = training.monotony([r.wload for r in log_rows(log, y, 7)])
    lrows = log_rows(log, y, 28)
    ratio = training.srpe_ratio(lrows, y) if _covers(log, y, 28) else None
    split7, split28 = training.split_window(log, y, 7), training.split_window(log, y, 28)
    target = training.weekly_target(lrows, y) if _covers(log, y, 28) else None
    recs = [r.recovery for r in rows if r.recovery is not None and r.date > (t - timedelta(days=7)).isoformat()]
    return {
        "form_pct": _r(training.form_pct(ctl, atl), 1), "ramp": _r(training.ramp(rows, y), 1),
        "easy_strain": _r(strain(training.easy_cap_trimp(ctl, atl), k), 1) if ctl is not None and atl is not None
        else None,
        "monotony": _r(mono, 2), "mono_flag": flag, "tstrain": _r(tstrain, 0), "srpe_ratio": _r(ratio, 2),
        "polar_band": training.polar_band(acwr) if acwr is not None else None,
        "garmin_band": training.garmin_band(acwr) if acwr is not None else None,
        "split7": [round(x, 1) for x in split7], "split28": [round(x, 1) for x in split28],
        "pi7": _r(training.polarization(split7), 2), "pi28": _r(training.polarization(split28), 2),
        "focus_short": training.focus_shortage(log, y),
        "target_week": [round(x) for x in target] if target else None,
        "run_tol_km": _r(training.running_tolerance(log, y), 1),
        "status": training.status(acwr, _vo2_trend(rows, y), statistics.fmean(recs) if recs else None),
        "hrv_status": training.hrv_status(rows, t), "recovery_index": ri, "approx": ["run_tol_km", "status"],
    }
