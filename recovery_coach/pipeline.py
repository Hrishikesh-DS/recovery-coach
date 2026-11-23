"""The morning computation: one wake date's scores from the input files and the log (spec 5, 6).

A run on wake date T produces
- today's row (date T): the night and its scores: recovery, zone, hrv_ln, sleep_hr, rhr, resp, spo2, wtemp,
  sleep_min (main sleep + naps since the previous main sleep), need_min, perf, debt_min, onset_min, wake_min, flags;
- an activity patch for yesterday's row (T - 1), what he did that day: strain, trimp, stress, still_hr, ctl, atl,
  tsb, acwr, steps, vig_min, strength_min, plus mvpa_min, vo2 and journal when the inputs carry them;
- after skipped mornings, an activity patch for each missed day too (same keys but mvpa_min and vo2, which come only
  from vitals.json for yesterday). The days patched run from the first day needing activity (activity_from: the
  day after the latest logged day with activity) to T - 1, never more than CATCH_UP_DAYS (3) days before T. A day
  before T - 1 is patched only when hours.csv or minutes.csv has data on it (the input files simply span more days:
  from that first day's 00:00 to now); a day they don't cover stays missing. Days are computed in date order, each
  seeing the ones before it, so fitness and fatigue step once per calendar day with a TRIMP (a day without one
  carries them, load_for_day);
- the daily card dict, exactly card.py's schema (HRV in ms for display).
result = {"card": {...}, "rows": [today_row, yesterday_patch, (earlier activity patches, newest first),
          (journal patch for another date)], "settings_patch": {},
          "state_next": <compact state: the input state with these rows merged, state.py>}.
Rows are merged into the log by date (inputs.merge_rows): a row overwrites the keys it carries, nothing else. The
dashboard stores the rows (per-day documents for charts); the next run's state.json is state_next.

Windows (the log holds earlier days only; each is sliced by calendar date): recovery and the health monitor use the
60 days before T; the stress baseline uses still-hour heart rates from the 14 days before the scored day and the
night HRV of the 60 days up to it; sleep debt uses the rows before T; the acute:chronic ratio needs all 28 days
ending on the scored day.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import replace
from datetime import date, datetime, timedelta

from . import load
from .inputs import (DayRow, HourStat, HrvReading, InputError, Inputs, Night, Vitals, Workout, row_from_dict, row_to_dict,
                     workout_id)
from .monitor import health_flags
from .recovery import FLOORS, recovery_score
from .sleep import ASLEEP_STAGES, bedtime_tonight, build_nights, naps_between, sleep_debt, sleep_need, sleep_quality
from .stats import robust_baseline
from .strain import (DEFAULT_K, GAP_CAP_MIN, LAST_READING_MIN, day_strain, dedupe_workouts, hr_max, strain,
                     strain_target, trimp, workout_strain)
from .state import as_read_back, next_state
from . import hr_metrics, session
from .sleep import recovery_index
from .stress import stress_day

HOUR = timedelta(hours=1)
HISTORY_DAYS = 60
STILL_BASELINE_DAYS = 14
TREND_DAYS = 7
BAND_DAYS = 14
STRENGTH_KINDS = {"traditionalStrengthTraining", "functionalStrengthTraining"}
VIGOROUS_FRACTION = 0.77   # my call: minutes at >= 77 % of HRmax count as vigorous
NO_PREV_NIGHT = timedelta(hours=24)   # my call: without the previous night, naps count from 24 h before onset
TODAY_KEYS = ("recovery", "zone", "hrv_ln", "sleep_hr", "rhr", "resp", "spo2", "wtemp", "sleep_min", "need_min",
              "perf", "debt_min", "onset_min", "wake_min", "flags")
CATCH_UP_DAYS = 3   # my call: after skipped mornings, activity is recomputed for at most the 3 days before T
ACTIVITY_MARKS = ("trimp", "strain", "ctl", "atl", "steps", "strength_min", "stress")   # set only by activity


# ---------------------------------------------------------------- small helpers (unit-tested)
def age_on(dob: date, d: date) -> float:
    return (d - dob).days / 365.2425


def window(rows: list[DayRow], d: date, days: int) -> list[DayRow]:
    """Rows dated d - days .. d - 1, by date."""
    lo, hi = (d - timedelta(days=days)).isoformat(), d.isoformat()
    return [r for r in rows if lo <= r.date < hi]


def _preferred(readings: list[HrvReading], hrv_type: str) -> list[HrvReading]:
    """The readings of the settings' HRV type only: RMSSD for "rmssd", SDNN for "sdnn". Never the other type: the two
    are on different scales, so one baseline must not mix them (a night without a reading of the type has no HRV)."""
    want = "R" if hrv_type == "rmssd" else "S"
    return [x for x in readings if x.type == want]


def night_hrv(readings: list[HrvReading], onset: datetime, wake: datetime, hrv_type: str) -> float | None:
    """Median ln(ms) of the readings inside the main-sleep window."""
    picked = _preferred([x for x in readings if onset <= x.time <= wake], hrv_type)
    return statistics.median(math.log(x.ms) for x in picked) if picked else None


def sleeping_hr(hours: list[HourStat], onset: datetime, wake: datetime) -> float | None:
    """Lowest hourly average among the clock hours lying wholly inside the main sleep (spec 3)."""
    vals = [h.avg_hr for h in hours if h.avg_hr is not None and onset <= h.start and h.start + HOUR <= wake]
    return min(vals) if vals else None


def rel_min(t: datetime, d: date) -> int:
    """Wall-clock minutes relative to d's local midnight, in t's own offset (negative before midnight)."""
    return round((t.date() - d).days * 1440 + t.hour * 60 + t.minute + t.second / 60)


def strength_minutes(workouts: list[Workout], d: date, worn: bool) -> float | None:
    """Minutes of (de-duplicated) strength training started on d; 0.0 on a worn day without any; None when the
    watch recorded nothing that day."""
    total = sum((w.end - w.start).total_seconds() / 60 for w in dedupe_workouts(workouts)
                if w.kind in STRENGTH_KINDS and w.start.date() == d)
    if total > 0:
        return total
    return 0.0 if worn else None


def workout_zone_minutes(workouts: list[Workout], minutes: list[tuple[datetime, float]], d: date, hrmax: float,
                         worn: bool) -> tuple[float | None, float | None]:
    """(zones 1-3, zones 4-5) minutes of minute heart rate inside the (de-duplicated) workouts started on d, by the
    zone limits of hr_metrics (50/60/70/80/90% HRmax), as WHOOP counts zone time of logged activities. (0, 0) on a
    worn day without any; (None, None) when the watch recorded nothing that day."""
    if not worn:
        return None, None
    z13 = z45 = 0.0
    readings = sorted(minutes)
    for w in dedupe_workouts(workouts):
        if w.start.date() != d:
            continue
        z = hr_metrics.zone_minutes(readings, w.start, w.end, hrmax)
        z13 += z[0] + z[1] + z[2]
        z45 += z[3] + z[4]
    return z13, z45


def minutes_at_or_above(readings: list[tuple[datetime, float]], bpm: float) -> float:
    """Minutes spent at or above bpm, each reading lasting until the next (capped as in TRIMP)."""
    rs = sorted(readings)
    total = 0.0
    for i, (t, v) in enumerate(rs):
        dur = min((rs[i + 1][0] - t).total_seconds() / 60, GAP_CAP_MIN) if i + 1 < len(rs) else LAST_READING_MIN
        if v >= bpm:
            total += dur
    return total


def load_for_day(history: list[DayRow], d: date, day_trimp: float | None) -> dict:
    """CTL / ATL / TSB after day d and the acute:chronic ratio over the 28 calendar days ending d (None unless every
    one of them has a TRIMP). history = rows before d. No TRIMP (watch off): fitness and fatigue carry over unchanged
    (my call: an unknown day is not a rest day)."""
    before = [r for r in history if r.date < d.isoformat()]
    prev = next((r for r in reversed(before) if r.ctl is not None and r.atl is not None), None)
    if day_trimp is None:
        if prev is None:
            return {"ctl": None, "atl": None, "tsb": None, "acwr": None}
        return {"ctl": prev.ctl, "atl": prev.atl, "tsb": prev.ctl - prev.atl, "acwr": None}
    out = load.update(prev, day_trimp)
    win = window(before, d, load.ACWR_DAYS - 1) + [DayRow(date=d.isoformat(), trimp=day_trimp)]
    out["acwr"] = load.acwr(win) if len(win) == load.ACWR_DAYS else None
    return out


def hrv_band(history: list[DayRow]) -> tuple[float | None, float | None]:
    """The robust ln-HRV baseline in ms: (centre, half-width of the centre +- spread band)."""
    base = robust_baseline([r.hrv_ln for r in history], FLOORS["hrv"])
    if base is None:
        return None, None
    c, s = base
    return math.exp(c), (math.exp(c + s) - math.exp(c - s)) / 2


def hr_band(history: list[DayRow]) -> tuple[float | None, float | None]:
    base = robust_baseline([r.sleep_hr for r in history], FLOORS["hr"])
    return base if base is not None else (None, None)


def _r(x, nd: int):
    return None if x is None else round(x, nd)


def _on(t: datetime, d: date) -> bool:
    return t.date() == d


def infer_today(inp: Inputs) -> date:
    """The local date of the newest timestamp in the inputs (the morning of the run)."""
    stamps = ([h.start for h in inp.hours or []] + [t for t, _ in inp.minutes or []]
              + [s.end for s in inp.sleep or []] + [x.time for x in inp.hrv or []])
    if not stamps:
        raise InputError("--date", None, "no timestamps in the inputs to tell today's date; pass --date YYYY-MM-DD")
    return max(stamps).date()


def stored(row: DayRow) -> DayRow:
    """The row as the log will hold it (rounded), so later days see exactly what was logged."""
    return row_from_dict(row_to_dict(row), "result", "row")


def has_activity(r: DayRow) -> bool:
    """Whether a run (or the back-fill) has computed this day's activity (any value only an activity patch sets)."""
    return any(getattr(r, k) is not None for k in ACTIVITY_MARKS) or bool(r.still_hr)


def activity_from(rows: list[DayRow], t: date) -> date:
    """The first day whose activity a run on wake date t computes: the day after the latest row before t with
    activity, but not before t - CATCH_UP_DAYS, and never after t - 1 (yesterday is always computed). Normally
    t - 1; after skipped mornings earlier. The skill queries heart rate, steps, HRV and workouts from its 00:00."""
    last = max((r.date for r in rows if r.date < t.isoformat() and has_activity(r)), default=None)
    lo, y = t - timedelta(days=CATCH_UP_DAYS), t - timedelta(days=1)
    first = lo if last is None else date.fromisoformat(last) + timedelta(days=1)
    return min(max(first, lo), y)


# ---------------------------------------------------------------- activity: what he did on a day
def worn(hours: list[HourStat], mins: list[tuple[datetime, float]]) -> bool:
    """Whether the day's hours and minutes hold any heart rate (the watch was worn): its activity is then computed
    from them."""
    return any(h.avg_hr is not None for h in hours) or bool(mins)


def sleep_windows(nights: list[Night], samples: list) -> list[tuple[datetime, datetime]]:
    """The intervals stress leaves out: each main sleep, and every asleep sample (naps)."""
    return [(n.onset, n.wake) for n in nights] + [(x.start, x.end) for x in samples if x.stage in ASLEEP_STAGES]


def day_activity(inp: Inputs, log: list[DayRow], d: date, yesterday: bool, hrmax: float, k: float,
                 asleep: list[tuple[datetime, datetime]]) -> tuple[dict, dict]:
    """(row patch values for day d, card parts: strain flag, workouts, stress, load; {} unless yesterday). log =
    the rows before T, with the activity of the days before d already patched in (the back-fill: the rows before d
    and d's own night). asleep = sleep_windows(). Used by the morning run and by the back-fill, which passes each
    day's slice of its inputs."""
    s = inp.state.settings
    hours = [h for h in inp.hours or [] if _on(h.start, d)]
    mins = [(t, b) for t, b in inp.minutes or [] if _on(t, d)]
    workouts = inp.workouts or []
    vitals = inp.vitals or Vitals()
    on = worn(hours, mins)
    if on:
        day_trimp, coverage = trimp(mins, hours, hrmax)
        day_str, flag = day_strain(day_trimp, coverage, k)
        flag = f"{flag}: {coverage:g} h of heart-rate data" if flag else None
    else:
        day_trimp, day_str, flag = None, None, "no heart-rate data yesterday"
    ld = load_for_day(log, d, day_trimp)

    night_hist = [r.hrv_ln for r in window(log, d + timedelta(days=1), HISTORY_DAYS) if r.hrv_ln is not None]
    baseline = [v for r in window(log, d, STILL_BASELINE_DAYS) for v in r.still_hr]
    d_hrv = [(x.time, x.ms) for x in _preferred([x for x in inp.hrv or [] if _on(x.time, d)], s.hrv_type)]
    st = stress_day(hours, workouts, asleep, d_hrv, baseline, night_hist)

    patch = {"strain": day_str, "trimp": day_trimp, "stress": st.score, "still_hr": st.still_readings,
             "ctl": ld["ctl"], "atl": ld["atl"], "tsb": ld["tsb"], "acwr": ld["acwr"],
             "steps": sum(h.steps for h in hours) if hours else None,
             "vig_min": minutes_at_or_above(mins, VIGOROUS_FRACTION * hrmax) if on else None,
             "strength_min": strength_minutes(workouts, d, on)}
    patch["z13_min"], patch["z45_min"] = workout_zone_minutes(workouts, mins, d, hrmax, on)
    if yesterday and vitals.exercise_min_yesterday is not None:
        patch["mvpa_min"] = vitals.exercise_min_yesterday
    if yesterday and vitals.vo2 is not None:
        patch["vo2"] = vitals.vo2
    if inp.journal is not None and inp.journal.date == d:
        patch["journal"] = inp.journal.answers
    if not yesterday:
        return patch, {}

    shown = []
    for w in dedupe_workouts(workouts):
        if not _on(w.start, d):
            continue
        has_hr = any(w.start <= t <= w.end for t, _ in inp.minutes or [])
        shown.append({"kind": w.kind, "start": w.start.isoformat(),
                      "minutes": round((w.end - w.start).total_seconds() / 60),
                      "strain": round(workout_strain(w, inp.minutes or [], hrmax, k), 1) if has_hr else None})
    stress = {"score": _r(st.score, 2), "band": st.band, "hourly": [[t.isoformat(), round(v, 2)] for t, v in st.hourly],
              "top_hours": [t.isoformat() for t in st.top_hours], "reason": st.reason}
    load_card = {"ctl": _r(ld["ctl"], 1), "atl": _r(ld["atl"], 1), "tsb": _r(ld["tsb"], 1), "acwr": _r(ld["acwr"], 2),
                 "acwr_flag": load.acwr_flag(ld["acwr"]) if ld["acwr"] is not None else None}
    return patch, {"flag": flag, "workouts": shown, "stress": stress, "load": load_card}


# ---------------------------------------------------------------- today: last night
def _pseudo_night(r: DayRow, tz) -> Night | None:
    """A Night rebuilt from a logged row's clock minutes (for medians of wake time and midpoints)."""
    if r.onset_min is None or r.wake_min is None:
        return None
    mid = datetime.combine(date.fromisoformat(r.date), datetime.min.time(), tzinfo=tz)
    return Night(date.fromisoformat(r.date), mid + timedelta(minutes=r.onset_min), mid + timedelta(minutes=r.wake_min),
                 r.sleep_min or 0.0, 0, 0.0, 0.0, 0.0, 0.0)


def _contributor(c) -> dict:
    if c.name == "HRV":
        value, base = math.exp(c.value), math.exp(c.baseline)
    else:
        value, base = c.value, c.baseline
    nd = 3 if c.name == "Sleep performance" else 1
    return {"name": c.name, "value": round(value, nd), "baseline": round(base, nd), "z": c.z, "points": c.points}


def run_day(inp: Inputs, today: date | None = None) -> dict:
    """The morning result for wake date `today` (default: the date of the newest input timestamp)."""
    t = today or infer_today(inp)
    y = t - timedelta(days=1)
    s = inp.state.settings
    rows = [r for r in inp.state.rows if r.date < t.isoformat()]
    age = age_on(s.dob, t)
    daily_max = {}
    for h in inp.hours or []:
        if h.max_hr is not None:
            daily_max[h.start.date()] = max(daily_max.get(h.start.date(), 0.0), h.max_hr)
    hrmax = s.hrmax or hr_max(list(daily_max.values()), age)
    k = s.k or DEFAULT_K
    samples = inp.sleep or []
    vitals = inp.vitals or Vitals()
    if inp.journal is not None and inp.journal.date > t:
        raise InputError("journal.json", None, f"date {inp.journal.date} is after today ({t})")

    nights = build_nights(samples)
    night = next((n for n in nights if n.wake_date == t), None)
    windows = sleep_windows(nights, samples)

    # ---- activity: yesterday, and the days missed since the last run that the inputs cover, in date order
    first = activity_from(rows, t)
    covered = {h.start.date() for h in inp.hours or []} | {x.date() for x, _ in inp.minutes or []}
    act_days = [d for d in (first + timedelta(days=i) for i in range((y - first).days + 1)) if d == y or d in covered]
    by = {r.date: r for r in rows}
    seen = dict(by)   # what each later day reads: patched days as the next state.json will hold them
    patches: dict[date, dict] = {}
    for d in act_days:
        patches[d], parts = day_activity(inp, sorted(seen.values(), key=lambda r: r.date), d, d == y, hrmax, k,
                                         windows)
        by[d.isoformat()] = stored(replace(by.get(d.isoformat()) or DayRow(date=d.isoformat()), **patches[d]))
        seen[d.isoformat()] = as_read_back(by[d.isoformat()])
    y_patch, y_card = patches[y], parts

    # ---- workouts: score the ones not yet scored, then the training figures that read the workout log
    wlog, docs, updates = list(inp.state.workouts), [], []
    if inp.wk_stats is not None or inp.wk_hr is not None or inp.efforts:
        docs, updates, wlog = session.score_new(inp, hrmax, k, sorted(seen.values(), key=lambda r: r.date), t)
    if wlog:
        extra = session.day_training(wlog, sorted(seen.values(), key=lambda r: r.date), y)
        patches[y].update(extra)
        by[y.isoformat()] = stored(replace(by[y.isoformat()], **extra))
        seen[y.isoformat()] = as_read_back(by[y.isoformat()])

    # ---- last night
    goal = s.sleep_goal_min
    debt = sleep_debt(rows, goal)
    need = sleep_need(goal, debt, y_patch["strain"])   # withheld strain: no strain term
    hrv_ln = shr = sleep_min = naps = onset_min = wake_min = perf = None
    if night is not None:
        hrv_ln = night_hrv(inp.hrv or [], night.onset, night.wake, s.hrv_type)
        shr = sleeping_hr(inp.hours or [], night.onset, night.wake)
        prev = [n for n in nights if n.wake_date < t]
        prev_wake = prev[-1].wake if prev else night.onset - NO_PREV_NIGHT
        naps = naps_between(samples, prev_wake, night.onset)
        sleep_min = night.asleep_min + naps
        onset_min, wake_min = rel_min(night.onset, t), rel_min(night.wake, t)
        perf = min(sleep_min / need, 1.0)
    values = {"hrv_ln": hrv_ln, "sleep_hr": shr, "rhr": vitals.rhr, "perf": perf, "resp": vitals.resp,
              "spo2": vitals.spo2, "wtemp": vitals.wtemp}
    hist = window(rows, t, HISTORY_DAYS)
    rec = recovery_score(values, hist)
    if night is None:
        rec.reason_missing = "no main sleep recorded last night"
    flags = health_flags(values, hist)
    ri = recovery_index(inp.night_hr, night, inp.workouts) if night is not None and inp.night_hr else None
    ri_h = round(ri["hours"], 2) if ri and ri["hours"] is not None else None
    today_row = stored(DayRow(date=t.isoformat(), recovery=rec.score, zone=rec.zone, hrv_ln=hrv_ln, sleep_hr=shr,
                              rhr=vitals.rhr, resp=vitals.resp, spo2=vitals.spo2, wtemp=vitals.wtemp,
                              sleep_min=sleep_min, need_min=need, perf=perf, debt_min=debt, onset_min=onset_min,
                              wake_min=wake_min, flags=flags, ri_h=ri_h, lbm_pct=vitals.lbm_pct))

    # ---- the log as it stands after this run
    by[t.isoformat()] = today_row
    log = sorted(by.values(), key=lambda r: r.date)

    # ---- sleep details and tonight
    sleep = {"actual_min": _r(sleep_min, 0), "need_min": round(need), "perf": _r(perf, 3), "debt_min": round(debt),
             "naps_min": _r(naps, 0), "efficiency": None, "consistency_min": None, "interruptions": None,
             "deep_min": None, "rem_min": None, "onset": None, "wake": None, "bedtime_tonight": None}
    if night is not None:
        tz = night.wake.tzinfo
        recent = [n for n in (_pseudo_night(r, tz) for r in window(log, t, TREND_DAYS - 1)) if n] + [night]
        q = sleep_quality(night, recent)
        tonight = sleep_need(goal, sleep_debt(log, goal), None)
        sleep.update(efficiency=round(q["efficiency"], 3), interruptions=q["interruptions"],
                     consistency_min=round(q["consistency_min"]) if len(recent) >= 2 else None,
                     deep_min=round(q["deep_min"]), rem_min=round(q["rem_min"]), onset=night.onset.isoformat(),
                     wake=night.wake.isoformat(),
                     bedtime_tonight=bedtime_tonight(recent, tonight).isoformat(timespec="minutes"))

    # ---- strain card
    t_hours = [h for h in inp.hours or [] if _on(h.start, t)]
    t_mins = [(x, b) for x, b in inp.minutes or [] if _on(x, t)]
    so_far = strain(trimp(t_mins, t_hours, hrmax)[0], k) if t_hours or t_mins else None
    target = list(strain_target(rec.zone, y_patch["tsb"])) if rec.zone is not None else None
    strain_card = {"yesterday": _r(y_patch["strain"], 1), "flag": y_card["flag"], "today_so_far": _r(so_far, 1),
                   "target": target, "workouts": y_card["workouts"]}

    # ---- trend and bands
    trend = []
    for i in range(TREND_DAYS - 1, -1, -1):
        d = t - timedelta(days=i)
        r = by.get(d.isoformat()) or DayRow(date=d.isoformat())
        trend.append({"date": r.date, "recovery": r.recovery, "zone": r.zone,
                      "strain": None if d == t else _r(r.strain, 1), "perf": _r(r.perf, 3)})
    bands = {"hrv": [], "sleep_hr": []}
    for i in range(BAND_DAYS - 1, -1, -1):
        d = t - timedelta(days=i)
        r = by.get(d.isoformat()) or DayRow(date=d.isoformat())
        before = window(log, d, HISTORY_DAYS)
        c, sp = hrv_band(before)
        bands["hrv"].append({"date": r.date, "value": _r(math.exp(r.hrv_ln), 1) if r.hrv_ln is not None else None,
                             "centre": _r(c, 1), "spread": _r(sp, 1)})
        c, sp = hr_band(before)
        bands["sleep_hr"].append({"date": r.date, "value": _r(r.sleep_hr, 1), "centre": _r(c, 1), "spread": _r(sp, 1)})

    card = {
        "date": t.isoformat(),
        "recovery": {"score": rec.score, "zone": rec.zone, "uncapped": rec.uncapped,
                     "contributors": [_contributor(c) for c in rec.contributors], "cap_reason": rec.cap_reason,
                     "reason_missing": rec.reason_missing},
        "sleep": sleep, "strain": strain_card, "stress": y_card["stress"], "load": y_card["load"], "flags": flags,
        "trend": trend, "bands": bands,
        "training": session.training_card(wlog, log, y, t, k, y_patch.get("acwr"), ri) if wlog else None,
    }
    by_id = {e.id: e for e in wlog}
    for item in strain_card["workouts"]:
        e = by_id.get(workout_id(datetime.fromisoformat(item["start"])))
        if e is not None:
            item.update(id=e.id, te=e.te, te_an=e.te_an, rec_h=e.rec_h, hrr60=e.hrr60, benefit=e.benefit)
    extra = (("ri_h",) if ri_h is not None else ()) + (("lbm_pct",) if vitals.lbm_pct is not None else ())
    out_rows = [row_to_dict(today_row, TODAY_KEYS + extra)]
    out_rows += [row_to_dict(by[d.isoformat()], patches[d].keys()) for d in sorted(patches, reverse=True)]
    if inp.journal is not None and inp.journal.date not in patches:
        out_rows.append({"date": inp.journal.date.isoformat(), "journal": dict(inp.journal.answers)})
    return {"card": card, "rows": out_rows, "settings_patch": {}, "workouts": docs, "workout_updates": updates,
            "state_next": next_state(inp.state, out_rows, workouts=wlog)}


def run_workouts(inp: Inputs, today: date | None = None, now: datetime | None = None) -> dict:
    """The `workout` command: score the workouts in the inputs that aren't in the log yet (or are provisional),
    apply effort answers, and return {"workouts", "updates", "card", "state_next"}; card = the latest scored workout
    with {"context": {"recovery_today", "recovered_pct"}}. now = the current time for "% recovered" (default: the
    newest sample, which is only minutes after the end)."""
    s = inp.state.settings
    rows = inp.state.rows
    starts = [w.start for w in inp.workouts or []]
    t = today or (max(starts).date() if starts else date.fromisoformat(rows[-1].date) if rows else date.today())
    hrmax = s.hrmax or 208.0 - 0.7 * age_on(s.dob, t)
    k = s.k or DEFAULT_K
    docs, updates, log = session.score_new(inp, hrmax, k, rows, t)
    card = None
    if docs:
        card = dict(max(docs, key=lambda d: d["start"]))
        now = now or session.newest_time(inp)
        rec_h = card["effect"]["rec_h"]
        since = (now - datetime.fromisoformat(card["end"])).total_seconds() / 3600.0 if now else 0.0
        today_row = next((r for r in rows if r.date == t.isoformat()), None)
        from .effect import recovered_pct
        card["context"] = {"recovery_today": today_row.recovery if today_row else None,
                           "recovered_pct": recovered_pct(rec_h, since) if rec_h is not None else None}
    return {"workouts": docs, "updates": updates, "card": card,
            "state_next": next_state(inp.state, [], workouts=log)}
