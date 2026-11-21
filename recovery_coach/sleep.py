"""Sleep: main sleep per night, naps, debt, need, quality, bedtime (spec 5.3).

All durations use real elapsed time (aware datetimes), so a night that spans a
DST change or carries a foreign offset is measured and dated correctly.
"""
from __future__ import annotations

import statistics
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .inputs import DayRow, Night, SleepSample

ASLEEP_STAGES = {"core", "deep", "rem", "asleep"}
JOIN_GAP_MIN = 60          # asleep samples this close belong to one block
MAIN_MIN_ASLEEP = 180      # my call: a block this long is main sleep
MAIN_ONSET_FROM = time(20, 0)   # my call: onset 20:00-04:00 with >= 90 min is a night
MAIN_ONSET_TO = time(4, 0)
MAIN_NIGHT_MIN_ASLEEP = 90      # my call
AWAKE_BOUT_MIN = 5
LATENCY_MIN = 15           # my call: typical time to fall asleep
DEBT_NIGHTS = 14
DEBT_DECAY = 0.75
DEBT_CAP = 120.0
STRAIN_PIVOT = 7
STRAIN_MIN_PER_POINT = 3
RECENT_NIGHTS = 7

Interval = tuple[datetime, datetime]


def _minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


def _union(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def _subtract(intervals: list[Interval], holes: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for s, e in intervals:
        cur = s
        for hs, he in holes:
            if he <= cur or hs >= e:
                continue
            if hs > cur:
                out.append((cur, hs))
            cur = max(cur, he)
        if cur < e:
            out.append((cur, e))
    return out


def _overlap_min(a: Interval, holes: list[Interval], ends: list[datetime]) -> float:
    """Minutes of a inside the holes (sorted and disjoint, as _union makes them; ends = their ends)."""
    total = 0.0
    for hs, he in holes[bisect_right(ends, a[0]):]:
        if hs >= a[1]:
            break
        total += max(0.0, _minutes(max(a[0], hs), min(a[1], he)))
    return total


@dataclass
class _Block:
    onset: datetime
    wake: datetime
    asleep: list[Interval]       # effective asleep intervals (awake removed)
    samples: list[SleepSample]   # asleep-stage samples in the block
    asleep_min: float

    @property
    def wake_date(self) -> date:
        return self.wake.date()  # local date in the end timestamp's own offset

    def qualifies_as_main(self) -> bool:
        t = self.onset.timetz().replace(tzinfo=None)
        in_window = t >= MAIN_ONSET_FROM or t < MAIN_ONSET_TO
        return (self.asleep_min >= MAIN_MIN_ASLEEP
                or (in_window and self.asleep_min >= MAIN_NIGHT_MIN_ASLEEP))


def _blocks(samples: list[SleepSample]) -> tuple[list[_Block], list[Interval]]:
    asleep_samples = sorted((s for s in samples if s.stage in ASLEEP_STAGES and s.end > s.start),
                            key=lambda s: s.start)
    awake = _union([(s.start, s.end) for s in samples if s.stage == "awake"])
    groups: list[list[SleepSample]] = []
    group_end: datetime | None = None
    for s in asleep_samples:
        if groups and _minutes(group_end, s.start) <= JOIN_GAP_MIN:
            groups[-1].append(s)
            group_end = max(group_end, s.end)
        else:
            groups.append([s])
            group_end = s.end
    blocks = []
    for g in groups:
        eff = _subtract(_union([(s.start, s.end) for s in g]), awake)
        if not eff:
            continue
        blocks.append(_Block(g[0].start, max(s.end for s in g), eff, g,
                             sum(_minutes(a, b) for a, b in eff)))
    return blocks, awake


def _main_blocks(blocks: list[_Block]) -> dict[date, _Block]:
    best: dict[date, _Block] = {}
    for b in blocks:
        if not b.qualifies_as_main():
            continue
        cur = best.get(b.wake_date)
        if cur is None or b.asleep_min > cur.asleep_min:
            best[b.wake_date] = b
    return best


def build_nights(samples: list[SleepSample]) -> list[Night]:
    """One Night per wake date (main sleep), ascending by wake date."""
    blocks, awake = _blocks(samples)
    awake_ends = [e for _, e in awake]
    inbed = [(s.start, s.end) for s in samples if s.stage == "inbed"]
    nights = []
    for wd, b in sorted(_main_blocks(blocks).items()):
        onset, wake = b.onset, b.wake  # first asleep sample start, last end
        bouts = sum(1 for (_, e1), (s2, _) in zip(b.asleep, b.asleep[1:])
                    if _minutes(e1, s2) >= AWAKE_BOUT_MIN)
        stage_min = {"core": 0.0, "deep": 0.0, "rem": 0.0}
        for s in b.samples:
            if s.stage in stage_min:
                stage_min[s.stage] += _minutes(s.start, s.end) - _overlap_min((s.start, s.end), awake, awake_ends)
        span = _minutes(onset, wake)
        covering = [i for i in inbed if i[1] > onset and i[0] < wake]
        if covering:
            span = max(span, _minutes(min(i[0] for i in covering), max(i[1] for i in covering)))
        eff = min(1.0, b.asleep_min / span) if span > 0 else 0.0
        nights.append(Night(wd, onset, wake, b.asleep_min, bouts, stage_min["deep"],
                            stage_min["rem"], stage_min["core"], eff))
    return nights


def nap_finder(samples: list[SleepSample]) -> Callable[[datetime, datetime], float]:
    """naps_between(samples, ., .) for many windows over the same samples: the blocks are found once."""
    blocks, _ = _blocks(samples)
    main = {id(b) for b in _main_blocks(blocks).values()}
    naps = [b for b in blocks if id(b) not in main]

    def between(prev_wake: datetime, onset: datetime) -> float:
        return sum(b.asleep_min for b in naps if b.onset >= prev_wake and b.wake <= onset)
    return between


def naps_between(samples: list[SleepSample], prev_wake: datetime, onset: datetime) -> float:
    """Asleep minutes in non-main blocks lying entirely between prev_wake and onset."""
    return nap_finder(samples)(prev_wake, onset)


def sleep_debt(history: list[DayRow], goal_min: float) -> float:
    """Weighted mean deficit over the last 14 usable nights, clipped to [0, 120]."""
    usable = [r.sleep_min for r in history if r.sleep_min is not None][-DEBT_NIGHTS:]
    if not usable:
        return 0.0
    weights = [DEBT_DECAY ** k for k in range(len(usable))]  # k=0 is most recent
    deficits = [goal_min - m for m in reversed(usable)]
    debt = sum(w * d for w, d in zip(weights, deficits)) / sum(weights)
    return max(0.0, min(DEBT_CAP, debt))


def sleep_need(goal_min: float, debt_min: float, prev_strain: float | None) -> float:
    need = goal_min + min(debt_min, DEBT_CAP)
    if prev_strain is not None and prev_strain > STRAIN_PIVOT:
        need += STRAIN_MIN_PER_POINT * (prev_strain - STRAIN_PIVOT)
    return need


def _midnight(d: date, tz) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def _clock_min(t: datetime) -> float:
    """Local wall-clock minutes after midnight, in the timestamp's own offset."""
    return t.hour * 60 + t.minute + t.second / 60


def _rel_clock_min(t: datetime, wake_date: date) -> float:
    """Wall-clock minutes relative to the wake date's midnight (negative before it)."""
    return (t.date() - wake_date).days * 1440 + _clock_min(t)


def _latest(recent: list[Night]) -> list[Night]:
    return sorted(recent, key=lambda n: n.wake_date)[-RECENT_NIGHTS:]


def sleep_quality(night: Night, recent: list[Night]) -> dict:
    """Detail metrics; consistency is the population SD of sleep midpoints (minutes)."""
    mids = []
    window = [n for n in recent if n.wake_date <= night.wake_date]
    if all(n.wake_date != night.wake_date for n in window):
        window.append(night)
    for n in _latest(window):
        mids.append((_rel_clock_min(n.onset, n.wake_date)
                     + _rel_clock_min(n.wake, n.wake_date)) / 2)
    return {
        "efficiency": night.efficiency,
        "consistency_min": statistics.pstdev(mids) if mids else 0.0,
        "interruptions": night.awake_bouts,
        "deep_min": night.deep_min,
        "rem_min": night.rem_min,
    }


def bedtime_tonight(recent: list[Night], need_min: float) -> datetime:
    """Usual wake (median clock time) minus tonight's need minus time to fall asleep."""
    if not recent:
        raise ValueError("bedtime_tonight needs at least one night")
    nights = _latest(recent)
    usual_wake = statistics.median(
        _clock_min(n.wake) for n in nights)
    latest = nights[-1]
    tomorrow = _midnight(latest.wake_date + timedelta(days=1), latest.wake.tzinfo)
    return tomorrow + timedelta(minutes=usual_wake - need_min - LATENCY_MIN)
