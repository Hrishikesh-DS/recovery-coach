"""Body Age and Pace of Aging (spec 5.9).

Re-implementation, in this project's own code, of the algorithm of openbodyscore ``healthspan`` v1.2
(https://github.com/SongyuQi-Francisco/Watch2Whoop, commit 003882464e197d42fd4b3163bcc47219d6e1e40e,
packages/openbodyscore/src/openbodyscore/healthspan.py and body_engine/.../healthspan_store.py + sleep_detail.py for the
orchestration and the SRI; Apache-2.0). The parameter tables are in healthspan_tables.py.

1. Each item is the mean of the last 180 days, the last 30 days weighted x2 (``weighted_mean``); an item needs >= 30 data
   days (SRI: 30 pairs of adjacent nights; VO2 max: >= 2 readings in 180 days, the latest within 120 days).
2. Each item -> all-cause-mortality hazard ratio from a published dose-response curve, relative to an anchor
   (ln HR interpolated linearly between knots, held flat beyond the ends).
3. Years = ln(HR) / ln 2 x 8, capped at +-4 per item (the activity domain once, +-4). Total = 0.6 x sum of items, capped
   at +-10. Body Age = age + total.
4. Pace of aging: weekly (body age - age) -> 4-week moving average -> least squares over the last <= 26 points (t in years);
   Pace = 1 + slope, clipped to -1 ... 3, needs >= 8 weeks.

v1.2 activity domain: one hazard ratio for steps (Ding 2025 curve under 60, Paluch 2022 from 60) x strength training
(Momma 2022), capped once. Heart-rate moderate/vigorous minutes are NOT counted in v1.2 (the source keeps them as a
reference only, "counted": False, because walking is already in the steps), so DayRow.mvpa_min / vig_min are not used here.
The step-equivalent conversion of swimming / cycling / rowing needs a workout type, which DayRow does not carry, so
that term is 0 (see ``step_equivalents``, ported for the golden test).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from . import healthspan_tables as T
from .inputs import DayRow

DAY_MIN = 1440
SRI_MIN_PAIRS = T.MIN_DAYS   # 30 pairs of adjacent nights


# ---------------------------------------------------------------- result types
@dataclass
class Item:
    """One body-age item. ``years`` is the item's contribution BEFORE the x 0.6 total scaling (after its own +-4 cap)."""
    name: str
    value: float | None
    years: float


@dataclass
class BodyAgeResult:
    body_age: float | None
    items: list[Item] = field(default_factory=list)
    missing: list[tuple[str, str]] = field(default_factory=list)   # (item name, reason it was skipped)


# ---------------------------------------------------------------- curve primitives (ported)
def weighted_mean(values: dict[date, float], as_of: date) -> tuple[float | None, int]:
    """values: date -> value; only dates inside the 180-day window ending at as_of count, the last 30 days weigh x2.
    Returns (weighted mean, number of data days)."""
    lo = as_of - timedelta(days=T.WINDOW_DAYS - 1)
    recent = as_of - timedelta(days=T.RECENT_DAYS - 1)
    num = den = 0.0
    n = 0
    for d, v in values.items():
        if lo <= d <= as_of and v is not None:
            w = T.RECENT_WEIGHT if d >= recent else 1.0
            num += w * v
            den += w
            n += 1
    return (num / den if den else None), n


def interp_log_hr(x: float, knots: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation of ln(HR) between knots [(x, HR)], held flat beyond both ends."""
    if x <= knots[0][0]:
        return knots[0][1]
    if x >= knots[-1][0]:
        return knots[-1][1]
    for (x0, h0), (x1, h1) in zip(knots, knots[1:]):
        if x0 <= x <= x1:
            f = (x - x0) / (x1 - x0)
            return math.exp(math.log(h0) + f * (math.log(h1) - math.log(h0)))
    raise AssertionError("unreachable")


def _rel(x: float, knots, anchor: float) -> float:
    """Hazard ratio relative to the anchor."""
    return interp_log_hr(x, knots) / interp_log_hr(anchor, knots)


def self_report_hours(measured: float) -> float:
    """Watch-measured sleep hours -> questionnaire scale (Lauderdale 2008)."""
    a, b = T.SLEEP_SELF_REPORT
    return a + b * measured


def hr_sleep_duration(hours_measured: float) -> float:
    """U-shaped (Yin 2017), nadir 7 h self-report = anchor; the watch duration is converted to self-report first."""
    h = self_report_hours(hours_measured)
    if h < T.SLEEP_NADIR_H:
        return T.SLEEP_RR_PER_H_SHORT ** (T.SLEEP_NADIR_H - h)
    return T.SLEEP_RR_PER_H_LONG ** (h - T.SLEEP_NADIR_H)


def hr_regularity(sri: float) -> float:
    return _rel(sri, T.SRI_KNOTS, T.SRI_ANCHOR)


def hr_steps(steps: float, age: float) -> float:
    if age < 60:
        return _rel(steps, T.STEPS_KNOTS_UNDER60, T.STEPS_ANCHOR_UNDER60)
    return _rel(steps, T.STEPS_KNOTS_60PLUS, T.STEPS_ANCHOR_60PLUS)


def hr_strength(min_week: float) -> float:
    return _rel(min_week, T.STRENGTH_KNOTS, T.STRENGTH_ANCHOR)


def moderate_equiv_minutes(minutes: float, met: float) -> float:
    """Questionnaire-style minutes (Lee 2022): moderate (3-6 MET) x1, vigorous (>= 6 MET) x2, light 0."""
    if met < T.MODERATE_MET:
        return 0.0
    return minutes * (T.VIGOROUS_FACTOR if met >= T.VIGOROUS_MET else 1.0)


def step_equivalents(minutes: float, met: float) -> float:
    """Steps a walk / run of the same MET would take in those minutes (100 steps/min = 3 MET, 130 = 6 MET, linear
    between, flat above). Light activity (< 3 MET) = 0."""
    if met < T.MODERATE_MET:
        return 0.0
    (m0, s0), (m1, s1) = T.CADENCE_MET_KNOTS
    return minutes * (s0 + (s1 - s0) * (min(met, m1) - m0) / (m1 - m0))


def hr_activity(steps_equiv: float, strength_min_week: float | None, age: float) -> dict:
    """v1.2 activity domain: one hazard ratio = step-volume curve x strength curve. Returns {hr, hr_volume, hr_strength};
    the caller caps the domain's years once (ACTIVITY_CAP_YEARS)."""
    hv = hr_steps(steps_equiv, age)
    hs = 1.0 if strength_min_week is None else hr_strength(strength_min_week)
    return {"hr": hv * hs, "hr_volume": hv, "hr_strength": hs}


def _by_age(table: dict, age: float) -> float:
    """Linear interpolation at age-band midpoints, held flat beyond the ends."""
    pts = sorted(table.items())
    if age <= pts[0][0]:
        return pts[0][1]
    if age >= pts[-1][0]:
        return pts[-1][1]
    for (a0, v0), (a1, v1) in zip(pts, pts[1:]):
        if a0 <= age <= a1:
            return v0 + (age - a0) / (a1 - a0) * (v1 - v0)
    raise AssertionError("unreachable")


def vo2_norm(age: float, sex: str) -> float | None:
    """Same-age same-sex median VO2 max (FRIEND 2022); None for an unknown sex."""
    table = T.VO2_MEDIAN.get(sex)
    return _by_age(table, age) if table else None


def hr_vo2(vo2: float, norm: float) -> float:
    """Risk x 0.87 per MET (3.5 mL/kg/min) above the norm (Kodama 2009); the gap is limited to +-3 MET."""
    met = max(-T.VO2_MAX_DELTA_MET, min(T.VO2_MAX_DELTA_MET, (vo2 - norm) / 3.5))
    return T.VO2_RR_PER_MET ** met


def hr_rhr(rhr: float) -> float:
    """x 1.16 per 10 bpm higher (Jensen 2013), flat below 45 bpm, anchor 65."""
    return T.RHR_RR_PER_10 ** ((max(rhr, T.RHR_FLOOR) - T.RHR_ANCHOR) / 10)


def hr_bmi(bmi: float) -> float:
    return _rel(bmi, T.BMI_KNOTS, T.BMI_ANCHOR)


def years_from_hr(hr: float, cap: float | None = None) -> float:
    """ln(HR) / ln 2 x 8, limited to +-cap (default ITEM_CAP_YEARS; math.inf = uncapped)."""
    cap = T.ITEM_CAP_YEARS if cap is None else cap
    y = math.log(hr) / math.log(2) * T.MORTALITY_DOUBLING_YEARS
    return max(-cap, min(cap, y))


def combine(items: list[Item]) -> float:
    """Total change in years: 0.6 x the sum of the (already capped) item years, limited to +-10."""
    total = T.SHRINKAGE * sum(i.years for i in items)
    return max(-T.TOTAL_CAP_YEARS, min(T.TOTAL_CAP_YEARS, total))


# ---------------------------------------------------------------- sleep regularity index
def _utc(t: datetime) -> datetime:
    """The same instant in UTC (naive datetimes are taken as UTC wall-clock). Python subtracts two aware datetimes that
    share one tzinfo by wall-clock time, i.e. an hour off across a DST change; in UTC every difference is elapsed time."""
    if t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def _day_block(d: date, tz) -> tuple[datetime, datetime]:
    """[d-1 12:00, d 12:00) local as UTC instants (23 or 25 h long on a DST day)."""
    return (_utc(datetime.combine(d - timedelta(days=1), time(12), tz)), _utc(datetime.combine(d, time(12), tz)))


def _asleep_minutes(intervals: list[tuple[datetime, datetime]], d: date, tz) -> bytearray:
    """Per-minute asleep state (1 = asleep) of the 24-hour block for wake date d."""
    lo, hi = _day_block(d, tz)
    out = bytearray(DAY_MIN)
    for a, b in intervals:
        a, b = _utc(a), _utc(b)
        if b <= lo or a >= hi:
            continue
        i0 = max(0, int((a - lo).total_seconds() // 60))
        i1 = min(DAY_MIN, int((b - lo).total_seconds() // 60))
        out[i0:i1] = b"\x01" * (i1 - i0)
    return out


def _sri_days(intervals: list[tuple[datetime, datetime]]) -> dict[date, bytearray]:
    """wake date (in the wake timestamp's own offset) -> minute states of its 24-hour block. Every interval contributes
    to the states; the block's time zone is that of the longest interval ending on that wake date."""
    longest: dict[date, tuple[float, object]] = {}
    for a, b in intervals:
        wd = b.date()
        length = (_utc(b) - _utc(a)).total_seconds()
        if wd not in longest or length > longest[wd][0]:
            longest[wd] = (length, b.tzinfo if b.tzinfo is not None else timezone.utc)
    return {wd: _asleep_minutes(intervals, wd, tz) for wd, (_, tz) in longest.items()}


def _sri_pairs(days: dict[date, bytearray], lo: date | None = None, as_of: date | None = None,
               weighted: bool = False) -> tuple[float | None, int]:
    """SRI = 200 x P(same state 24 h apart) - 100 over pairs of adjacent wake dates that both exist. With `weighted`
    the pair whose first day lies in the last 30 days (ending as_of) counts double. Returns (SRI, pairs)."""
    recent = as_of - timedelta(days=T.RECENT_DAYS - 1) if (weighted and as_of) else None
    same = total = 0.0
    pairs = 0
    for d, cur in days.items():
        nxt = days.get(d + timedelta(days=1))
        if nxt is None:
            continue
        if lo is not None and d < lo:
            continue
        if as_of is not None and d + timedelta(days=1) > as_of:
            continue
        w = T.RECENT_WEIGHT if (recent is not None and d >= recent) else 1.0
        same += w * sum(1 for x, y in zip(cur, nxt) if x == y)
        total += w * DAY_MIN
        pairs += 1
    return (200 * same / total - 100 if total else None), pairs


def sleep_regularity(intervals: list[tuple[datetime, datetime]]) -> float | None:
    """Sleep Regularity Index (Phillips 2017) from main-sleep (onset, wake) intervals at minute resolution:
    SRI = 200 x P(same asleep/awake state 24 h apart) - 100, 100 = identical every day, 0 = chance, -100 = opposite.
    This is the raw index the source feeds into its curve (it is not rescaled; the source only clamps to 0-100 for
    display). Each interval belongs to its wake date; days run noon to noon; only adjacent wake dates are paired;
    needs >= 30 pairs, else None. Aware datetimes are compared by elapsed time (DST-safe); naive ones as plain wall-clock.
    Rounded to 0.1 as in the source."""
    v, pairs = _sri_pairs(_sri_days(intervals))
    if v is None or pairs < SRI_MIN_PAIRS:
        return None
    return round(v, 1)


# ---------------------------------------------------------------- pace of aging
def _smooth(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """4-week moving average: each point averages the weekly values in (d - 4 weeks, d]."""
    weeks = T.PACE_SMOOTH_WEEKS
    return [(d, sum(xs) / len(xs)) for d, _ in points
            for xs in [[v for d2, v in points if d - timedelta(weeks=weeks) < d2 <= d]]]


def pace_of_aging(weekly_deltas: list[float | None]) -> float | None:
    """weekly_deltas: (body age - age) snapshots, oldest first, one per week (None = a week without a snapshot, skipped).
    4-week moving average, least-squares slope over the last <= 26 points with t in years, Pace = 1 + slope, rounded to
    0.01 and clipped to -1 ... 3. None with fewer than 8 points."""
    base = date(2000, 1, 3)
    pts = [(base + timedelta(weeks=i), v) for i, v in enumerate(weekly_deltas) if v is not None]
    sm = _smooth(pts)[-T.PACE_WEEKS:]
    if len(sm) < T.PACE_MIN_WEEKS:
        return None
    last = sm[-1][0]
    xs = [(d - last).days / 365.2425 for d, _ in sm]
    ys = [v for _, v in sm]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    raw = 1 + sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return round(max(T.PACE_RANGE[0], min(T.PACE_RANGE[1], raw)), 2)


# ---------------------------------------------------------------- body age
def _series(rows: list[DayRow], attr: str) -> dict[date, float]:
    return {date.fromisoformat(r.date): getattr(r, attr) for r in rows if getattr(r, attr) is not None}


def body_age(age: float, sex: str, history: list[DayRow], weight_kg: float | None,
             height_m: float | None) -> BodyAgeResult:
    """Body Age = age + total years, from up to 180 days of DayRows (sorted by date; the window is the 180 days ending at
    the last row's date).

    Items (name: value): sleep_duration (mean hours asleep), sleep_regularity (SRI), activity (steps volume per day;
    strength minutes per week when there are no steps), vo2max, rhr (bpm), body_comp (BMI, only when both weight_kg
    and height_m are given; the caller makes sure the weight is recent). ``Item.years`` is that item's contribution
    BEFORE the x 0.6 scaling and total cap (each item already capped at +-4). ``missing`` lists (item name, reason) for
    items skipped for insufficient data; ``body_age`` is None when no item has enough data.
    sex: 'male' | 'female' (VO2 max norms; anything else skips the VO2 max item).
    """
    items: list[Item] = []
    missing: list[tuple[str, str]] = []

    def add(name, value, hr, years=None):
        items.append(Item(name, value, years_from_hr(hr) if years is None else years))

    if not history:
        reasons = ("sleep_duration", "sleep_regularity", "activity", "vo2max", "rhr", "body_comp")
        return BodyAgeResult(None, [], [(n, "no history") for n in reasons])
    as_of = max(date.fromisoformat(r.date) for r in history)
    lo = as_of - timedelta(days=T.WINDOW_DAYS - 1)
    rows = [r for r in history if lo <= date.fromisoformat(r.date) <= as_of]

    # ---- sleep duration
    hours = {d: m / 60.0 for d, m in _series(rows, "sleep_min").items()}
    v, n = weighted_mean(hours, as_of)
    if n >= T.MIN_DAYS:
        add("sleep_duration", v, hr_sleep_duration(v))
    else:
        missing.append(("sleep_duration", f"only {n} nights of watch sleep in 180 days (need >= {T.MIN_DAYS})"))

    # ---- sleep regularity (main sleep intervals of consecutive wake dates)
    intervals = []
    for r in rows:
        if r.onset_min is None or r.wake_min is None or r.wake_min <= r.onset_min:
            continue
        mid = datetime.combine(date.fromisoformat(r.date), time(0))
        intervals.append((mid + timedelta(minutes=r.onset_min), mid + timedelta(minutes=r.wake_min)))
    sri, pairs = _sri_pairs(_sri_days(intervals), lo, as_of, weighted=True)
    if sri is not None and pairs >= T.MIN_DAYS:
        add("sleep_regularity", sri, hr_regularity(sri))
    else:
        missing.append(("sleep_regularity", f"only {pairs} pairs of adjacent nights with sleep times (need >= {T.MIN_DAYS})"))

    # ---- activity domain (v1.2): steps x strength, one cap
    steps, n_steps = weighted_mean(_series(rows, "steps"), as_of)
    has_steps = n_steps >= T.MIN_DAYS
    first_strength = next((date.fromisoformat(r.date) for r in rows if r.strength_min is not None), None)
    strength = ({date.fromisoformat(r.date): (r.strength_min or 0.0) for r in rows
                 if first_strength is not None and date.fromisoformat(r.date) >= first_strength}
                if first_strength else {})
    str_mean, n_str = weighted_mean(strength, as_of)
    tracked = n_str >= T.MIN_DAYS
    str_week = str_mean * 7 if tracked else None
    if not has_steps and not tracked:
        missing.append(("activity", f"only {n_steps} days of steps (need >= {T.MIN_DAYS}) and no strength records yet"))
    else:
        if has_steps:
            r = hr_activity(steps * T.WRIST_STEPS_FACTOR, str_week, age)   # + step equivalents of non-step exercise: 0
            value = steps
        else:   # volume unknown (HR 1): the domain is the strength curve alone
            hs = hr_strength(str_week)
            r, value = {"hr": hs}, str_week
            missing.append(("steps", f"only {n_steps} days of steps (need >= {T.MIN_DAYS}); activity uses strength only"))
        if not tracked:
            missing.append(("strength", f"only {n_str} days since strength records began (need >= {T.MIN_DAYS}); activity uses steps only"))
        add("activity", value, r["hr"], years_from_hr(r["hr"], T.ACTIVITY_CAP_YEARS))

    # ---- VO2 max
    vo2 = _series(rows, "vo2")
    norm = vo2_norm(age, sex)
    if norm is None:
        missing.append(("vo2max", f"unknown sex {sex!r}: no same-age same-sex norm"))
    elif len(vo2) < T.VO2_MIN_READINGS or (as_of - max(vo2)).days > T.VO2_MAX_AGE_DAYS:
        missing.append(("vo2max", f"{len(vo2)} VO2 max reading(s) in 180 days, latest {(as_of - max(vo2)).days if vo2 else '-'} days ago "
                                  f"(need >= {T.VO2_MIN_READINGS} readings and the latest within {T.VO2_MAX_AGE_DAYS} days)"))
    else:
        v, _ = weighted_mean(vo2, as_of)
        add("vo2max", v, hr_vo2(v, norm))

    # ---- resting heart rate
    v, n = weighted_mean(_series(rows, "rhr"), as_of)
    if n >= T.MIN_DAYS:
        add("rhr", v, hr_rhr(v))
    else:
        missing.append(("rhr", f"only {n} days of resting heart rate (need >= {T.MIN_DAYS})"))

    # ---- BMI
    if weight_kg and height_m:
        bmi = weight_kg / height_m ** 2
        add("body_comp", bmi, hr_bmi(bmi))
    else:
        missing.append(("body_comp", "no weight or height given"))

    return BodyAgeResult(age + combine(items) if items else None, items, missing)
