"""The first run (spec 6): rows for every past day with data, calibrated HRmax and K, a back-test, weekly snapshots.

Inputs: state.json (settings; rows already logged before the span are used as history), plus sleep.csv, hrv.csv and
workouts.csv over the whole span, daily.csv (one line per day) and hours.csv; minutes.csv is optional. The courier
route sends hours.csv header-only (no hourly data: Apple's resting HR stands in for sleeping HR) and no minutes.csv;
the import route (export.py) sends both for the whole span.
Days are computed in order and each uses only the rows before it, exactly as if the daily runs had happened then.
- A day whose hours.csv or minutes.csv hold heart rate (pipeline.worn) gets its activity exactly as the morning run
  computes it (pipeline.day_activity, fed that day's slice of the inputs): strain from the real whole-day TRIMP,
  stress, still-hour heart rates, vigorous and strength minutes, fitness and fatigue.
- Any other day's TRIMP is seeded from its workouts' average heart rate (load.seed_from_workouts, approximate until
  real daily logs replace it); its stress stays empty.
K is calibrated from the days' TRIMPs (real where there are, seeded elsewhere); fitness and fatigue start from the
mean of the first 14 days'.

backfill(inputs, until) -> {"rows": [DayRow dicts], "settings_patch": {hrmax, k, hrv_type},
                            "backtest": [{date, recovery, zone}], "weeks": [{date, delta, items}],
                            "state_next": <daily state, 60 days>, "state_long_next": <long state, 180 days
                            and the weekly snapshots>}   (state.py)
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import replace
from datetime import date, timedelta

from types import SimpleNamespace

from . import load, zones
from .whoop_age import whoop_age
from .inputs import DayRow, InputError, Inputs, State, Week, row_to_dict
from .monitor import health_flags
from .pipeline import (HISTORY_DAYS, NO_PREV_NIGHT, age_on, day_activity, load_for_day, night_hrv, rel_min,
                       sleep_windows, sleeping_hr, stored, strength_minutes, window, worn)
from .recovery import recovery_score
from .state import long_state, merged_rows, next_state
from .sleep import build_nights, nap_finder, sleep_debt, sleep_need
from .strain import calibrate_k, hr_max, strain, trimp

# my call: RMSSD only when at least this many nights have it, and no fewer than have SDNN. Each night uses only the
# chosen type (the two are on different scales), so RMSSD that started part-way through the span keeps the history on
# SDNN: every night stays scored and no baseline mixes the two.
MIN_RMSSD_NIGHTS = 14
SNAPSHOT_WEEKDAY = 6        # Sunday: weekly snapshots end on the day before the Monday report
SNAPSHOT_MIN_DAYS = 30      # WHOOP-style Age metrics need 30 days of data
NEAR = timedelta(days=1)    # what a day's activity can touch: intervals within a day of it (offsets, overnight)


def _by_date(items, start, end=None) -> dict[date, list]:
    """Items under each local date from start(x) - 1 day to end(x) + 1 day (only start(x)'s date when end is None)."""
    out: dict[date, list] = {}
    for x in items:
        if end is None:
            out.setdefault(start(x).date(), []).append(x)
            continue
        d, last = start(x).date() - NEAR, end(x).date() + NEAR
        while d <= last:
            out.setdefault(d, []).append(x)
            d += timedelta(days=1)
    return out


def backfill(inp: Inputs, until: date | None = None) -> dict:
    s = inp.state.settings
    samples = inp.sleep or []
    hrv = sorted(inp.hrv or [], key=lambda x: x.time)
    hrv_times = [x.time for x in hrv]
    workouts = inp.workouts or []
    daily = {d.date: d for d in inp.daily or []}
    nights = {n.wake_date: n for n in build_nights(samples)}
    hours_on = _by_date(inp.hours or [], lambda h: h.start)
    mins_on = _by_date(inp.minutes or [], lambda m: m[0])
    covered = {d for d in set(hours_on) | set(mins_on) if worn(hours_on.get(d, []), mins_on.get(d, []))}
    have = sorted(set(daily) | set(nights) | covered)
    if until is not None:
        have = [d for d in have if d <= until]
    if not have:
        raise InputError("sleep.csv, daily.csv", None, "no days with sleep or daily data to backfill")
    first, last = have[0], have[-1]
    span = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    age = age_on(s.dob, last)

    hrmax = round(hr_max([d.max_hr for d in daily.values() if d.max_hr is not None], age), 1)

    def in_night(n):
        return hrv[bisect_left(hrv_times, n.onset):bisect_right(hrv_times, n.wake)]

    def nights_with(kind: str) -> int:
        return sum(1 for n in nights.values() if any(x.type == kind for x in in_night(n)))
    rmssd_nights = nights_with("R")
    hrv_type = "rmssd" if rmssd_nights >= max(MIN_RMSSD_NIGHTS, nights_with("S")) else "sdnn"
    rest_rows = [SimpleNamespace(date=d.isoformat(), rhr=x.rhr, sleep_hr=None) for d, x in daily.items()]

    def lim_for(d):
        return zones.limits(s.zones, hrmax, rest_rows, d)
    seeded = load.seed_from_workouts(workouts, hrmax, span, lim_for)
    day_trimps = {d: trimp(mins_on.get(d, []), hours_on.get(d, []), hrmax, lim_for(d))[0] if d in covered
                  else seeded[d] for d in span}
    k = round(calibrate_k(list(day_trimps.values())), 1)
    goal = s.sleep_goal_min

    # each day's slice of the inputs, for the morning's activity code
    act_state = State(replace(s, hrv_type=hrv_type), [], [])
    hrv_on = _by_date(hrv, lambda x: x.time)
    workouts_near = _by_date(workouts, lambda w: w.start, lambda w: w.end)
    asleep_near = _by_date(sleep_windows(list(nights.values()), samples), lambda x: x[0], lambda x: x[1])
    naps = nap_finder(samples)

    rows = sorted((r for r in inp.state.rows if r.date < first.isoformat()), key=lambda r: r.date)
    if not any(r.ctl is not None for r in rows):   # fitness and fatigue start from the mean of the first 14 days
        start = load.seed([day_trimps[d] for d in span])
        rows.append(DayRow(date=(first - timedelta(days=1)).isoformat(), ctl=start["ctl"], atl=start["atl"]))
        seed_row = rows[-1]
    else:
        seed_row = None
    new: list[DayRow] = []
    ordered = sorted(nights)
    for d in span:
        ds, night = daily.get(d), nights.get(d)
        if ds is None and night is None and d not in covered:
            continue
        hrv_ln = shr = sleep_min = onset_min = wake_min = perf = None
        debt = sleep_debt(rows, goal)
        prev = rows[-1] if rows and rows[-1].date == (d - timedelta(days=1)).isoformat() else None
        need = sleep_need(goal, debt, prev.strain if prev else None)
        if night is not None:
            hrv_ln = night_hrv(in_night(night), night.onset, night.wake, hrv_type)
            shr = sleeping_hr([h for x in (d - NEAR, d, d + NEAR) for h in hours_on.get(x, [])], night.onset,
                              night.wake)
            earlier = [nights[w] for w in ordered if w < d]
            prev_wake = earlier[-1].wake if earlier else night.onset - NO_PREV_NIGHT
            sleep_min = night.asleep_min + naps(prev_wake, night.onset)
            onset_min, wake_min = rel_min(night.onset, d), rel_min(night.wake, d)
            perf = min(sleep_min / need, 1.0)
        values = {"hrv_ln": hrv_ln, "sleep_hr": shr, "rhr": ds.rhr if ds else None, "perf": perf,
                  "resp": ds.resp if ds else None}
        hist = window(rows, d, HISTORY_DAYS)
        rec = recovery_score(values, hist)
        row = DayRow(date=d.isoformat(), recovery=rec.score, zone=rec.zone, hrv_ln=hrv_ln, sleep_hr=shr,
                     rhr=values["rhr"], resp=values["resp"], sleep_min=sleep_min, need_min=need, perf=perf,
                     debt_min=debt, onset_min=onset_min, wake_min=wake_min,
                     mvpa_min=ds.exercise_min if ds else None, vo2=ds.vo2 if ds else None,
                     lbm_pct=(inp.body or {}).get(d),
                     flags=health_flags(values, hist))
        near = workouts_near.get(d, [])
        if d in covered:
            day = Inputs(act_state, hrv=hrv_on.get(d, []), hours=hours_on.get(d, []), minutes=mins_on.get(d, []),
                         workouts=near)
            patch, _ = day_activity(day, rows + [row], d, False, hrmax, k, asleep_near.get(d, []))
            if ds is not None and ds.steps is not None:
                patch["steps"] = ds.steps   # Health's daily total (the import writes the hours' sum)
            row = replace(row, **patch)
        else:
            on = ds is not None and ds.max_hr is not None
            day_trimp = seeded[d] if on or seeded[d] > 0 else None
            ld = load_for_day(rows, d, day_trimp)
            row = replace(row, strain=strain(day_trimp, k) if day_trimp is not None else None, trimp=day_trimp,
                          ctl=ld["ctl"], atl=ld["atl"], tsb=ld["tsb"], acwr=ld["acwr"],
                          steps=ds.steps if ds else None, strength_min=strength_minutes(near, d, on))
        row = stored(row)
        rows.append(row)
        new.append(row)

    log = [r for r in rows if r is not seed_row]
    weeks: list[Week] = []
    for d in span:
        if d.weekday() != SNAPSHOT_WEEKDAY or (d - first).days + 1 < SNAPSHOT_MIN_DAYS:
            continue
        a = age_on(s.dob, d)
        wa = whoop_age([r for r in log if r.date <= d.isoformat()], d, a, s.sex)
        missed = {n for n, _ in wa.missing}
        if len(missed) < len(wa.items):
            weeks.append(Week(d, round(wa.delta, 2), tuple(i.name for i in wa.items if i.name not in missed)))
    patch = {"hrmax": hrmax, "k": k, "hrv_type": hrv_type}
    new_rows = [row_to_dict(r) for r in new]
    kept = [w for w in inp.state.weeks if w.date not in {x.date for x in weeks}]
    return {"rows": new_rows, "settings_patch": patch,
            "backtest": [{"date": r.date, "recovery": r.recovery, "zone": r.zone} for r in new],
            "weeks": [{"date": w.date.isoformat(), "delta": w.delta, "items": list(w.items)} for w in weeks],
            "state_next": next_state(inp.state, new_rows, patch),
            "state_long_next": long_state(merged_rows(inp.state, new_rows), kept + weeks)}
