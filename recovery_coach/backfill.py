"""The first run (spec 6): rows for every past day with data, calibrated HRmax and K, a back-test, weekly snapshots.

Inputs: state.json (settings; rows already logged before the span are used as history), plus sleep.csv, hrv.csv and
workouts.csv over the whole span and daily.csv (one line per day), and hours.csv (sleeping HR; header-only = none, so
Apple's resting HR is used).
Days are computed in order and each uses only the rows before it, exactly as if the daily runs had happened then.
Without minute heart rate, a day's TRIMP is seeded from its workouts' average heart rate (load.seed_from_workouts,
approximate until real daily logs replace it); stress needs hourly data and stays empty.

backfill(inputs, until) -> {"rows": [DayRow dicts], "settings_patch": {hrmax, k, hrv_type},
                            "backtest": [{date, recovery, zone}], "weeks": [{date, delta, items}],
                            "state_next": <daily state, 60 days>, "state_long_next": <long state, 180 days
                            and the weekly snapshots>}   (state.py)
"""
from __future__ import annotations

from datetime import date, timedelta

from . import load
from .healthspan import body_age
from .inputs import DayRow, InputError, Inputs, Week, row_to_dict
from .monitor import health_flags
from .pipeline import (HISTORY_DAYS, NO_PREV_NIGHT, age_on, load_for_day, night_hrv, rel_min, sleeping_hr, stored,
                       strength_minutes, window)
from .recovery import recovery_score
from .state import long_state, merged_rows, next_state
from .sleep import build_nights, naps_between, sleep_debt, sleep_need
from .strain import calibrate_k, hr_max, strain

# my call: RMSSD only when at least this many nights have it, and no fewer than have SDNN. Each night uses only the
# chosen type (the two are on different scales), so RMSSD that started part-way through the span keeps the history on
# SDNN: every night stays scored and no baseline mixes the two.
MIN_RMSSD_NIGHTS = 14
SNAPSHOT_WEEKDAY = 6        # Sunday: weekly snapshots end on the day before the Monday report
SNAPSHOT_MIN_DAYS = 30      # Body Age items need 30 days of data


def backfill(inp: Inputs, until: date | None = None) -> dict:
    s = inp.state.settings
    samples = inp.sleep or []
    hrv = inp.hrv or []
    workouts = inp.workouts or []
    daily = {d.date: d for d in inp.daily or []}
    nights = {n.wake_date: n for n in build_nights(samples)}
    have = sorted(set(daily) | set(nights))
    if until is not None:
        have = [d for d in have if d <= until]
    if not have:
        raise InputError("sleep.csv, daily.csv", None, "no days with sleep or daily data to backfill")
    first, last = have[0], have[-1]
    span = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    age = age_on(s.dob, last)

    hrmax = round(hr_max([d.max_hr for d in daily.values() if d.max_hr is not None], age), 1)
    def nights_with(kind: str) -> int:
        return sum(1 for n in nights.values() if any(x.type == kind and n.onset <= x.time <= n.wake for x in hrv))
    rmssd_nights = nights_with("R")
    hrv_type = "rmssd" if rmssd_nights >= max(MIN_RMSSD_NIGHTS, nights_with("S")) else "sdnn"
    seeded = load.seed_from_workouts(workouts, hrmax, span)
    k = round(calibrate_k(list(seeded.values())), 1)
    goal = s.sleep_goal_min

    rows = [r for r in inp.state.rows if r.date < first.isoformat()]
    if not any(r.ctl is not None for r in rows):   # fitness and fatigue start from the mean of the first 14 days
        start = load.seed([seeded[d] for d in span])
        rows.append(DayRow(date=(first - timedelta(days=1)).isoformat(), ctl=start["ctl"], atl=start["atl"]))
        seed_row = rows[-1]
    else:
        seed_row = None
    new: list[DayRow] = []
    ordered = sorted(nights)
    for d in span:
        ds, night = daily.get(d), nights.get(d)
        if ds is None and night is None:
            continue
        hrv_ln = shr = sleep_min = onset_min = wake_min = perf = None
        debt = sleep_debt(rows, goal)
        prev = next((r for r in rows if r.date == (d - timedelta(days=1)).isoformat()), None)
        need = sleep_need(goal, debt, prev.strain if prev else None)
        if night is not None:
            hrv_ln = night_hrv(hrv, night.onset, night.wake, hrv_type)
            shr = sleeping_hr(inp.hours or [], night.onset, night.wake)
            earlier = [nights[w] for w in ordered if w < d]
            prev_wake = earlier[-1].wake if earlier else night.onset - NO_PREV_NIGHT
            sleep_min = night.asleep_min + naps_between(samples, prev_wake, night.onset)
            onset_min, wake_min = rel_min(night.onset, d), rel_min(night.wake, d)
            perf = min(sleep_min / need, 1.0)
        values = {"hrv_ln": hrv_ln, "sleep_hr": shr, "rhr": ds.rhr if ds else None, "perf": perf,
                  "resp": ds.resp if ds else None}
        hist = window(rows, d, HISTORY_DAYS)
        rec = recovery_score(values, hist)
        worn = ds is not None and ds.max_hr is not None
        day_trimp = seeded[d] if worn or seeded[d] > 0 else None
        ld = load_for_day(rows, d, day_trimp)
        row = stored(DayRow(
            date=d.isoformat(), recovery=rec.score, zone=rec.zone, hrv_ln=hrv_ln, sleep_hr=shr, rhr=values["rhr"],
            resp=values["resp"], sleep_min=sleep_min, need_min=need, perf=perf, debt_min=debt, onset_min=onset_min,
            wake_min=wake_min, strain=strain(day_trimp, k) if day_trimp is not None else None, trimp=day_trimp,
            ctl=ld["ctl"], atl=ld["atl"], tsb=ld["tsb"], acwr=ld["acwr"], steps=ds.steps if ds else None,
            mvpa_min=ds.exercise_min if ds else None, strength_min=strength_minutes(workouts, d, worn),
            vo2=ds.vo2 if ds else None, flags=health_flags(values, hist)))
        rows.append(row)
        new.append(row)

    log = [r for r in rows if r is not seed_row]
    weeks: list[Week] = []
    for d in span:
        if d.weekday() != SNAPSHOT_WEEKDAY or (d - first).days + 1 < SNAPSHOT_MIN_DAYS:
            continue
        a = age_on(s.dob, d)
        ba = body_age(a, s.sex, window(log, d + timedelta(days=1), 180), s.weight_kg, s.height_m)
        if ba.body_age is not None:
            weeks.append(Week(d, round(ba.body_age - a, 2), tuple(i.name for i in ba.items)))
    patch = {"hrmax": hrmax, "k": k, "hrv_type": hrv_type}
    new_rows = [row_to_dict(r) for r in new]
    kept = [w for w in inp.state.weeks if w.date not in {x.date for x in weeks}]
    return {"rows": new_rows, "settings_patch": patch,
            "backtest": [{"date": r.date, "recovery": r.recovery, "zone": r.zone} for r in new],
            "weeks": [{"date": w.date.isoformat(), "delta": w.delta, "items": list(w.items)} for w in weeks],
            "state_next": next_state(inp.state, new_rows, patch),
            "state_long_next": long_state(merged_rows(inp.state, new_rows), kept + weeks)}
