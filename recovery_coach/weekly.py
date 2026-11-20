"""The weekly report (spec 5.8): the week ending on a date vs the 4 weeks before, with Body Age and Pace of Aging.

weekly_report(state, as_of, vitals, long) -> {"card": <weekly card dict, card.py schema>,
                                               "week": {date, delta, items, body_age, pace},
                                               "state_long_next": <long state, state.py>}.
state is the daily state (state.json), long the long state (state_long.json, optional); their rows are merged by
date, the daily state winning where both hold a date. The week is as_of - 6 .. as_of (default as_of: the latest
row's date; on Mondays the skill passes yesterday). "week" is this report's Body Age snapshot (delta = body age -
age, items = the Body Age items it counted); state_long_next is the merged rows with that snapshot added (the skill
stores it after the Monday report, so Pace of Aging can use it).
Pace of Aging uses only the trailing run of snapshots computed from the same items as the latest one: an item
joining once its data reaches 30 days changes the measurement, not the person (8 such snapshots needed).
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from .healthspan import BodyAgeResult, body_age, pace_of_aging
from .inputs import DayRow, InputError, State, Vitals, Week
from .journal import habit_effects
from .load import SPIKE_ABOVE
from .pipeline import age_on, window
from .recovery import recovery_score
from .sleep import LATENCY_MIN
from .state import long_state, merge_states

WEEK = 7
PRIOR_DAYS = 28
SERIES_DAYS = 28
VO2_DAYS = 180
BODY_DAYS = 180
DEBT_FOCUS_MIN = 60   # my call: more sleep debt than this makes sleep the week's focus
FOCUS_ITEM = {   # my call: one line per Body Age item, for the item adding the most years
    "sleep_duration": "Sleep longer: 7-8 hours of sleep a night is the habit adding the most years right now.",
    "sleep_regularity": "Keep a steady schedule: bed and wake times within 30 minutes every day, weekends included.",
    "activity": "Move more: build toward 8,000+ steps a day, with two strength sessions a week.",
    "vo2max": "Build fitness: one interval session a week is the surest way to lift VO₂ max.",
    "rhr": "Bring resting heart rate down: more easy aerobic minutes and steady sleep.",
    "body_comp": "Body composition adds the most years: small, steady changes to food and activity.",
}


def _mean(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(statistics.fmean(vals), 2) if vals else None


def _averages(rows: list[DayRow]) -> dict:
    return {"recovery": _mean(r.recovery for r in rows), "strain": _mean(r.strain for r in rows),
            "sleep_perf": _mean(r.perf for r in rows), "sleep_min": _mean(r.sleep_min for r in rows),
            "hrv": _mean(math.exp(r.hrv_ln) if r.hrv_ln is not None else None for r in rows),
            "rhr": _mean(r.rhr for r in rows), "stress": _mean(r.stress for r in rows)}


def _phrase(c) -> str:
    if c.name == "HRV":
        return f"HRV {math.exp(c.value):.0f} ms vs {math.exp(c.baseline):.0f} normal"
    if c.name == "Sleep performance":
        return f"slept {c.value:.0%} of need"
    if c.name == "Respiratory rate":
        return f"breathing {c.value:.1f}/min vs {c.baseline:.1f} normal"
    return f"{c.name[0].lower() + c.name[1:]} {c.value:.0f} bpm vs {c.baseline:.0f} normal"


def _why(row: DayRow, rows: list[DayRow], best: bool) -> str:
    """The two inputs that pushed that morning's Recovery up (best) or down (worst) the most, else its zone."""
    today = {"hrv_ln": row.hrv_ln, "sleep_hr": row.sleep_hr, "rhr": row.rhr, "perf": row.perf, "resp": row.resp}
    rec = recovery_score(today, window(rows, date.fromisoformat(row.date), 60))
    cs = sorted((c for c in rec.contributors if (c.points > 0 if best else c.points < 0)), key=lambda c: -abs(c.points))
    text = "; ".join(_phrase(c) for c in cs[:2]) or f"{row.zone or 'unscored'} zone"
    if not best and "illness_warning" in row.flags:
        text = "possible illness signals; " + text
    return text[0].upper() + text[1:]


def _day(row: DayRow | None, rows: list[DayRow], best: bool) -> dict | None:
    return None if row is None else {"date": row.date, "recovery": row.recovery, "why": _why(row, rows, best)}


def _clock(minutes: float) -> str:
    m = round(minutes) % 1440
    h, mm = divmod(m, 60)
    return f"{h % 12 or 12}:{mm:02d} {'am' if h < 12 else 'pm'}"


def focus(rows: list[DayRow], ba: BodyAgeResult, goal_min: float) -> str | None:
    """One line for the coming week (my call): sleep debt first, then a load spike, then the Body Age item adding
    the most years; None when nothing stands out."""
    if not rows:
        return None
    last = rows[-1]
    if last.debt_min is not None and last.debt_min > DEBT_FOCUS_MIN:
        wakes = [r.wake_min for r in rows[-WEEK:] if r.wake_min is not None]
        when = f" by {_clock(statistics.median(wakes) - goal_min - last.debt_min - LATENCY_MIN)}" if wakes else ""
        return f"Protect sleep: aim for bedtime{when} to pay back {last.debt_min:.0f} min of sleep debt."
    acwr = next((r.acwr for r in reversed(rows[-WEEK:]) if r.acwr is not None), None)
    if acwr is not None and acwr > SPIKE_ABOVE:
        return (f"Ease the load: the last 7 days were {acwr:.1f}× your 4-week average; keep two easy days "
                f"between hard sessions.")
    worst = max((i for i in ba.items if i.years > 0), key=lambda i: i.years, default=None)
    return FOCUS_ITEM.get(worst.name) if worst is not None else None


def pace_points(weeks: list[Week], current: Week) -> list[float | None]:
    """The deltas Pace of Aging may use: the trailing run of snapshots whose item set equals the latest one's (the
    current snapshot replaces a stored one of the same date; snapshots without items never join), placed on a weekly
    grid from the run's first snapshot (nearest week; the later date wins a shared slot; None = a week without one)."""
    by = {w.date: w for w in weeks}
    by[current.date] = current
    ordered = sorted(by.values(), key=lambda w: w.date)
    target = ordered[-1].items
    run: list[Week] = []
    for w in reversed(ordered):
        if target is None or w.items is None or set(w.items) != set(target):
            break
        run.append(w)
    run = run[::-1] or [ordered[-1]]
    d0 = run[0].date
    slots = {}
    for w in run:
        slots[round((w.date - d0).days / 7)] = w.delta
    return [slots.get(i) for i in range(max(slots) + 1)]


def weekly_report(state: State, as_of: date | None = None, vitals: Vitals | None = None,
                  long: State | None = None) -> dict:
    s = state.settings
    all_rows = merge_states(long, state)
    snapshots = long.weeks if long is not None else state.weeks
    rows = list(all_rows)
    if not rows:
        raise InputError("state.json", None, "rows is empty: nothing to report on")
    end = as_of or date.fromisoformat(rows[-1].date)
    rows = [r for r in rows if r.date <= end.isoformat()]
    after = end + timedelta(days=1)
    week = window(rows, after, WEEK)
    start = end - timedelta(days=WEEK - 1)
    prior = window(rows, start, PRIOR_DAYS)
    by = {r.date: r for r in rows}
    dates = [(end - timedelta(days=i)).isoformat() for i in range(SERIES_DAYS - 1, -1, -1)]

    scored = [r for r in week if r.recovery is not None]
    best = max(scored, key=lambda r: (r.recovery, r.date), default=None)
    worst = min(scored, key=lambda r: (r.recovery, r.date), default=None)
    series = [{"date": d, "recovery": by[d].recovery, "zone": by[d].zone, "strain": by[d].strain, "perf": by[d].perf,
               "stress": by[d].stress} if d in by else
              {"date": d, "recovery": None, "zone": None, "strain": None, "perf": None, "stress": None} for d in dates]
    trend_rows = window(rows, after, SERIES_DAYS)
    trends = {"hrv": [{"date": r.date, "value": round(math.exp(r.hrv_ln), 1)} for r in trend_rows if r.hrv_ln is not None],
              "rhr": [{"date": r.date, "value": r.rhr} for r in trend_rows if r.rhr is not None],
              "vo2": [{"date": r.date, "value": r.vo2} for r in window(rows, after, VO2_DAYS) if r.vo2 is not None]}
    load_series = [{"date": d, "ctl": by[d].ctl if d in by else None, "atl": by[d].atl if d in by else None,
                    "tsb": by[d].tsb if d in by else None} for d in dates]

    age = age_on(s.dob, end)
    weight = (vitals.weight_kg if vitals and vitals.weight_kg else None) or s.weight_kg
    height = (vitals.height_m if vitals and vitals.height_m else None) or s.height_m
    ba = body_age(age, s.sex, window(rows, after, BODY_DAYS), weight, height)
    delta = round(ba.body_age - age, 2) if ba.body_age is not None else None
    snapshot = Week(end, delta, tuple(i.name for i in ba.items))
    pace = pace_of_aging(pace_points(snapshots, snapshot))
    body = {"body_age": round(ba.body_age, 2) if ba.body_age is not None else None, "age": round(age, 2),
            "pace": pace, "items": [{"name": i.name, "value": round(i.value, 2) if i.value is not None else None,
                                     "years": round(i.years, 2)} for i in ba.items],
            "missing": [[n, why] for n, why in ba.missing]}
    questions = s.questions or sorted({q for r in rows for q in r.journal})
    journal = [{"question": e.question, "yes_n": e.yes_n, "no_n": e.no_n, "yes_mean": round(e.yes_mean, 1),
                "no_mean": round(e.no_mean, 1), "diff": round(e.diff, 1)} for e in habit_effects(rows, questions)]
    card = {"week_start": start.isoformat(), "week_end": end.isoformat(), "averages": _averages(week),
            "prior": _averages(prior), "best_day": _day(best, rows, True), "worst_day": _day(worst, rows, False),
            "series": series, "trends": trends, "load": load_series, "body_age": body, "journal": journal,
            "focus": focus(rows, ba, s.sleep_goal_min)}
    weeks = [w for w in snapshots if w.date != end] + [snapshot]
    return {"card": card, "week": {"date": end.isoformat(), "delta": delta, "items": list(snapshot.items),
                                   "body_age": body["body_age"], "pace": pace},
            "state_long_next": long_state(all_rows, weeks)}
