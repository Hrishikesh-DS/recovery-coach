"""Synthetic 90-day Apple Watch data set for the end-to-end test. Every number is invented; no real health data.

Deterministic: one seeded random.Random drives the whole simulation, so every machine writes the same bytes.

The person: male, born 1996-04-12, living in US Central time (the 2026-11-01 DST change falls inside the set).
Day n is FIRST_DAY + n - 1 (day 1 = 2026-08-22, day 90 = 2026-11-19). Night n is the main sleep that ends on the
morning of day n. The watch is worn all the time: a heart-rate reading every 5 minutes, every minute in workouts.

Planted events
- Illness dip: nights 80-82 (wake dates 2026-11-09..11): HRV about -42 %, sleeping HR +7 bpm, respiratory rate
  +2.2, wrist temperature +0.6 C, resting HR +6, SpO2 -1.2; no training and an afternoon nap on days 80-82.
- Hard training block: days 68-74 (2026-10-28..11-03): a hard 65-min run plus a strength session every day, so the
  acute:chronic load ratio climbs above 1.5.
- Duplicate workout: day 66's strength session is logged twice, as traditionalStrengthTraining (heart rate and
  energy) and as "other" (no heart rate) with near-identical start and end.
- Naps on some weekend afternoons; a few backfill nights recorded as plain "asleep" (no stages); journal habits
  (alcohol, late meal) lower that night's HRV.

Writers (file formats: recovery_coach/inputs.py):
    write_backfill(folder)     days 1-60: state.json (settings only), sleep.csv, hrv.csv, hours.csv (header only), workouts.csv, daily.csv
    write_morning(n, folder)   the morning of day n: sleep.csv, hrv.csv, hours.csv, minutes.csv, workouts.csv,
                               vitals.json, journal.json (state.json comes from the log, see run_chain); also
                               at another time of day (now=) or after skipped mornings (first=: the first day
                               needing activity), with the skill's spans (recovery_coach/inputs.py)
    run_chain(work)            the skill's first run (backfill) and the 30 mornings after it, through the CLI

Regenerate the committed fixture day90/ (the morning of 2026-11-19 with the log carried forward):
    python3 tests/fixtures/synthetic_90d/generate.py
"""
from __future__ import annotations

import json
import math
import random
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path

SEED = 20261119
FIRST_DAY = date(2026, 8, 22)
DAYS = 90
BACKFILL_DAYS = 60
DIP = (80, 81, 82)
BLOCK = range(68, 75)
DUP_DAY = 66
MINUTES_HR = 95   # ~50 % of HRmax: hours whose max reaches this are sent minute by minute
QUESTIONS = ["alcohol", "caffeine after 2 pm", "late meal", "screens in bed", "stressful day"]
SETTINGS = {"dob": "1996-04-12", "sex": "male", "sleep_goal_min": 480, "hrmax": None, "k": None,
            "hrv_type": "sdnn", "questions": QUESTIONS, "weight_kg": 74.0, "height_m": 1.78}
STRENGTH = "traditionalStrengthTraining"

UTC = timezone.utc
CDT = timezone(timedelta(hours=-5))
CST = timezone(timedelta(hours=-6))
DST_END = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)   # 02:00 CDT becomes 01:00 CST


def day_date(n: int) -> date:
    return FIRST_DAY + timedelta(days=n - 1)


def at(d: date, minutes: float) -> datetime:
    """The UTC instant of local wall time d 00:00 + minutes (US Central)."""
    naive = datetime.combine(d, time()) + timedelta(minutes=round(minutes))
    tz = CDT if naive < datetime(2026, 11, 1, 1, 0) else CST
    return naive.replace(tzinfo=tz).astimezone(UTC)


def iso(t: datetime) -> str:
    """Courier timestamp: local time to the minute with a compact offset, e.g. 2026-11-19T06:20-0600 (per-minute
    heart rate, sleep stages and hourly stats carry nothing finer; it keeps the morning's files small)."""
    local = t.astimezone(CDT if t < DST_END else CST)
    return local.strftime("%Y-%m-%dT%H:%M%z")


@dataclass
class Night:
    onset: datetime
    wake: datetime
    samples: list          # (start, end, code)
    hrv: float             # ln ms
    sleep_hr: float
    resp: float
    spo2: float
    wtemp: float


@dataclass
class Day:
    rhr: float
    now: datetime          # when the morning report is requested
    workouts: list = field(default_factory=list)   # dicts: start, end, kind, kcal, model, avg_hr
    walks: list = field(default_factory=list)      # (start, end)
    naps: list = field(default_factory=list)       # [(start, end, code)] per nap
    busy_hours: list = field(default_factory=list)
    stress_hours: list = field(default_factory=list)
    habits: dict = field(default_factory=dict)
    vo2: float | None = None


class World:
    """The whole 90-day simulation, minute by minute."""

    def __init__(self, seed: int = SEED):
        rng = random.Random(seed)
        self.nights: dict[int, Night] = {}
        self.days: dict[int, Day] = {}
        self._plan(rng)
        self._fill(rng)

    # ------------------------------------------------------------ plan: nights, workouts, habits
    def _plan(self, rng: random.Random) -> None:
        self.days[0] = Day(rhr=54.0, now=at(day_date(0), 7 * 60), habits=self._habits(rng, 0))
        for n in range(1, DAYS + 1):
            self.nights[n] = self._night(rng, n)
            self.days[n] = self._day(rng, n)

    def _habits(self, rng, n):
        d = day_date(n)
        weekend_eve = d.weekday() in (4, 5)
        sick = n in DIP or n + 1 in DIP
        return {
            "alcohol": not sick and rng.random() < (0.45 if weekend_eve else 0.15),
            "caffeine after 2 pm": rng.random() < 0.3,
            "late meal": rng.random() < 0.25,
            "screens in bed": rng.random() < 0.4,
            "stressful day": rng.random() < (0.1 if d.weekday() >= 5 else 0.35),
        }

    def _night(self, rng, n) -> Night:
        y = self.days[n - 1].habits
        hrv = math.log(55) + 0.0008 * n + rng.gauss(0, 0.08)
        shr = 50.0 - 0.01 * n + rng.gauss(0, 1.0)
        resp = 14.8 + rng.gauss(0, 0.25)
        spo2 = 97.2 + rng.gauss(0, 0.5)
        wtemp = 35.6 + rng.gauss(0, 0.12)
        if y["alcohol"]:
            hrv, shr = hrv - 0.15, shr + 2.0
        if y["late meal"]:
            hrv, shr = hrv - 0.05, shr + 1.0
        if n - 1 in BLOCK:
            hrv, shr = hrv - min(0.15, 0.03 * (n - BLOCK.start)), shr + 1.0
        if n in DIP:
            hrv, shr, resp, wtemp, spo2 = hrv - 0.55, shr + 7.0, resp + 2.2, wtemp + 0.6, spo2 - 1.2
        elif n == DIP[-1] + 1:
            hrv, shr, resp = hrv - 0.18, shr + 2.0, resp + 0.6
        eve = day_date(n - 1)
        onset_min = 22 * 60 + 40 + rng.gauss(0, 20)
        onset_min += 35 * (eve.weekday() in (4, 5)) + 20 * y["screens in bed"] + 15 * y["caffeine after 2 pm"]
        target = 440 + rng.gauss(0, 28) + 25 * (eve.weekday() in (4, 5)) - 20 * y["alcohol"]
        if n in DIP:
            onset_min, target = onset_min - 30, target + 40
        plain = n in (12, 37)   # an older watch face: whole night as "asleep", no stages
        samples, wake = self._sleep(rng, at(eve, onset_min), target, plain, fragmented=n in DIP)
        return Night(samples[1][0], wake, samples, hrv, shr, resp, min(99.5, spo2), wtemp)

    @staticmethod
    def _sleep(rng, onset, target, plain=False, fragmented=False, inbed=True):
        """Stage samples from onset until `target` minutes asleep (90-min cycles, short wakes between them)."""
        out, t, asleep, cycle, target = [], onset, 0, 0, round(target)
        while asleep < target:
            if plain:
                parts = [("S", rng.uniform(80, 140))]
            else:
                parts = [("C", rng.uniform(25, 40)), ("D", max(0.0, rng.gauss(28 - 7 * cycle, 5))),
                         ("C", rng.uniform(8, 15)), ("R", max(5.0, rng.gauss(12 + 6 * cycle, 4)))]
            for c, dur in parts:
                dur = round(min(dur, target - asleep))
                if dur < 1:
                    continue
                out.append((t, t + timedelta(minutes=dur), c))
                t += timedelta(minutes=dur)
                asleep += dur
            cycle += 1
            if asleep < target:
                p = rng.random()
                gap = round(rng.uniform(1, 4)) if p < 0.45 else (
                    round(rng.uniform(6, 14)) if p < 0.45 + (0.35 if fragmented else 0.12) else 0)
                if gap:
                    out.append((t, t + timedelta(minutes=gap), "A"))
                    t += timedelta(minutes=gap)
        if inbed:
            out.insert(0, (onset - timedelta(minutes=rng.randint(8, 25)), t + timedelta(minutes=rng.randint(2, 10)), "B"))
        return out, t

    def _day(self, rng, n) -> Day:
        d, night = day_date(n), self.nights[n]
        wd = d.weekday()
        sick = n in DIP
        rhr = 54.0 - 0.01 * n + rng.gauss(0, 1.2) + (6.0 if sick else 2.0 if n == DIP[-1] + 1 else 0.0)
        day = Day(rhr=rhr, now=night.wake + timedelta(minutes=rng.randint(20, 45)))

        def workout(kind, start_min, minutes, level):
            s = at(d, start_min + rng.uniform(-10, 10))
            e = s + timedelta(minutes=round(minutes + rng.uniform(-6, 6)))
            dur = (e - s).total_seconds() / 60
            kcal = round(dur * (11.5 if kind == "running" else 6.5) + rng.uniform(-20, 20))
            day.workouts.append({"start": s, "end": e, "kind": kind, "kcal": kcal, "level": level})

        if n in BLOCK:
            workout("running", 12 * 60 + 15, 65, 160)
            workout(STRENGTH, 18 * 60 + 30, 55, 0)
        elif not sick:
            plan = {0: [(STRENGTH, 18 * 60, 50, 0)], 1: [("running", 18 * 60 + 15, 45, 150)],
                    2: [(STRENGTH, 18 * 60, 55, 0)], 3: [("running", 18 * 60 + 15, 40, 155)], 4: [],
                    5: [("running", 8 * 60 + 30, 75, 145)], 6: []}[wd]
            for kind, start, minutes, level in plan:
                if n == DUP_DAY or rng.random() > 0.08:
                    workout(kind, start, minutes, level)
        if n == DUP_DAY:
            w = day.workouts[0]
            day.workouts.append({"start": w["start"] + timedelta(minutes=1), "end": w["end"] + timedelta(seconds=40),
                                 "kind": "other", "kcal": w["kcal"] - 12, "level": None})
        if not sick:
            if wd < 5:
                day.walks += [(at(d, 8 * 60 + 5), at(d, 8 * 60 + 20)), (at(d, 17 * 60 + 20), at(d, 17 * 60 + 35))]
                if n not in BLOCK and rng.random() < 0.5:
                    day.walks.append((at(d, 12 * 60 + 30), at(d, 12 * 60 + 50)))
            elif rng.random() < 0.7:
                s = at(d, rng.uniform(15 * 60 + 40, 17 * 60))   # after any nap
                day.walks.append((s, s + timedelta(minutes=rng.randint(30, 45))))
        if sick or (wd >= 5 and rng.random() < 0.3):
            start = at(d, 14 * 60 + rng.uniform(-30, 30))
            day.naps.append(self._sleep(rng, start, rng.randint(40, 70) if sick else rng.randint(20, 40),
                                        inbed=False)[0])
        day.busy_hours = rng.sample(range(9, 21), 2)
        day.habits = self._habits(rng, n)
        if wd < 5:
            day.stress_hours = rng.sample(range(9, 18), 3 if day.habits["stressful day"] else 2)
        if wd == 5 and day.workouts and rng.random() < 0.5:
            day.vo2 = round(48.6 + 0.012 * n + rng.gauss(0, 0.3), 1)
        return day

    # ------------------------------------------------------------ fill: minute heart rate, steps, readings
    def _fill(self, rng: random.Random) -> None:
        self.t0 = at(day_date(0), 0)
        end = at(day_date(DAYS + 1), 0)
        size = int((end - self.t0).total_seconds() // 60)
        hr = [0.0] * size
        steps = [0] * size
        inwork = [False] * size
        asleep = [False] * size
        day_of = [0] * size
        for n in range(0, DAYS + 1):
            a, b = self.idx(at(day_date(n), 0)), self.idx(at(day_date(n + 1), 0))
            day = self.days[n]
            for i in range(a, b):
                day_of[i] = n
                h = (self.time(i).astimezone(CDT if self.time(i) < DST_END else CST)).hour
                bump = 10.0 if h in day.stress_hours else 0.0
                hr[i] = day.rhr + 14 + bump + rng.gauss(0, 3)
                if h in day.busy_hours:
                    hr[i] += 5
                    steps[i] = rng.randint(5, 15)
                else:
                    steps[i] = 0 if rng.random() < 0.92 else rng.randint(4, 25)
            for s, e in day.walks:
                for i in range(self.idx(s), self.idx(e)):
                    hr[i] = day.rhr + 28 + rng.gauss(0, 4)
                    steps[i] = rng.randint(100, 115)
            for w in day.workouts:
                a, b = self.idx(w["start"]), self.idx(w["end"])
                if w["level"] is None:      # the duplicate log: the same minutes, no HR of its own
                    continue
                for k, i in enumerate(range(a, b)):
                    inwork[i] = True
                    if w["kind"] == "running":
                        ramp = min(1.0, (k + 1) / 6)
                        hr[i] = 100 + ramp * (w["level"] - 100 + 0.08 * k) + rng.gauss(0, 5)
                        steps[i] = rng.randint(155, 172)
                    else:
                        hr[i] = (135 if k % 3 == 0 else 106) + rng.gauss(0, 6)
                        steps[i] = rng.randint(0, 20)
        for n, night in self.nights.items():
            self._sleep_hr(rng, hr, steps, asleep, night.samples, night.onset, night.wake, night.sleep_hr)
            for nap in self.days[n].naps:
                self._sleep_hr(rng, hr, steps, asleep, nap, nap[0][0], nap[-1][1], night.sleep_hr + 8)
        self.hr, self.steps, self.inwork, self.asleep, self.day_of = hr, steps, inwork, asleep, day_of
        self.readings = []   # (instant, bpm)
        phase = 0
        for i in range(size):
            if i % 1440 == 0:
                phase = rng.randrange(5)
            if inwork[i] or i % 5 == phase:
                self.readings.append((self.time(i) + timedelta(seconds=rng.randrange(60)), round(hr[i])))
        for w in (w for day in self.days.values() for w in day.workouts):
            inside = [b for t, b in self.readings if w["start"] <= t < w["end"]] if w["level"] is not None else []
            w["avg_hr"] = round(sum(inside) / len(inside)) if inside else None
        self.hrv = []   # (instant, ms)
        for n, night in self.nights.items():
            mins = [i for s, e, c in night.samples if c in "CDRS" for i in range(self.idx(s), self.idx(e))]
            for i in sorted(rng.sample(mins, rng.randint(3, 5))):
                self.hrv.append((self.time(i) + timedelta(seconds=rng.randrange(60)),
                                 round(math.exp(night.hrv + rng.gauss(0, 0.12)))))
            d = day_date(n)
            calm = [i for i in range(self.idx(at(d, 9 * 60)), self.idx(at(d, 20 * 60)))
                    if not inwork[i] and not asleep[i] and steps[i] == 0]
            for i in sorted(rng.sample(calm, 3)):
                h = self.time(i).astimezone(CDT if self.time(i) < DST_END else CST).hour
                drop = 6 if h in self.days[n].stress_hours else 0
                self.hrv.append((self.time(i) + timedelta(seconds=rng.randrange(60)),
                                 max(8, round(math.exp(night.hrv - 0.3 + rng.gauss(0, 0.2))) - drop)))
        self.hrv.sort()

    def _sleep_hr(self, rng, hr, steps, asleep, samples, onset, wake, low):
        span = max(1, self.idx(wake) - self.idx(onset))
        for s, e, c in samples:
            if c == "B":
                continue
            for i in range(self.idx(s), self.idx(e)):
                u = (i - self.idx(onset)) / span
                hr[i] = low + 10 * (u - 0.65) ** 2 + rng.gauss(0, 1.2) + (3 if c == "R" else 10 if c == "A" else 0)
                steps[i] = 0
                asleep[i] = c != "A"

    # ------------------------------------------------------------ helpers
    def idx(self, t: datetime) -> int:
        return int((t - self.t0).total_seconds() // 60)

    def time(self, i: int) -> datetime:
        return self.t0 + timedelta(minutes=i)

    def day_steps(self, n: int) -> int:
        a, b = self.idx(at(day_date(n), 0)), self.idx(at(day_date(n + 1), 0))
        return sum(self.steps[a:b])

    def exercise_min(self, n: int) -> int:
        day = self.days[n]
        spans = [(w["start"], w["end"]) for w in day.workouts if w["level"] is not None] + day.walks
        return round(sum((e - s).total_seconds() / 60 for s, e in spans))

    def max_hr(self, n: int) -> int:
        a, b = at(day_date(n), 0), at(day_date(n + 1), 0)
        return max(bpm for t, bpm in self.readings if a <= t < b)

    def sleep_samples(self, nights, days) -> list:
        out = [s for n in nights for s in self.nights[n].samples]
        out += [s for n in days for nap in self.days[n].naps for s in nap]
        return sorted(out)


@lru_cache(maxsize=1)
def world() -> World:
    return World()


# ---------------------------------------------------------------- writers
def _csv(path: Path, header: str, lines) -> None:
    path.write_text(header + "\n" + "".join(line + "\n" for line in lines))


def _num(x) -> str:
    return "" if x is None else f"{x:g}" if isinstance(x, float) else str(x)


def _workout_lines(ws) -> list[str]:
    return [f'{iso(w["start"])},{iso(w["end"])},{w["kind"]},{w["kcal"]},{_num(w["avg_hr"])}'
            for w in sorted(ws, key=lambda w: w["start"])]


def write_backfill(folder: Path) -> None:
    """Days 1-60 for the first run: 60 nights of sleep samples, HRV, workouts and daily summaries."""
    w = world()
    folder.mkdir(parents=True, exist_ok=True)
    days = range(1, BACKFILL_DAYS + 1)
    (folder / "state.json").write_text(json.dumps({"settings": SETTINGS, "rows": [], "weeks": []}, indent=1))
    _csv(folder / "sleep.csv", "start,end,code",
         (f"{iso(s)},{iso(e)},{c}" for s, e, c in w.sleep_samples(days, days)))
    lo, hi = at(day_date(0), 12 * 60), at(day_date(BACKFILL_DAYS + 1), 0)
    _csv(folder / "hrv.csv", "time,ms,type", (f"{iso(t)},{ms},S" for t, ms in w.hrv if lo <= t < hi))
    _csv(folder / "hours.csv", "hour_start,avg,min,max,steps", [])   # no hourly data: Apple's resting HR is used
    _csv(folder / "workouts.csv", "start,end,kind,kcal,avg_hr",
         _workout_lines([x for n in days for x in w.days[n].workouts]))
    _csv(folder / "daily.csv", "date,steps,exercise_min,rhr,resp,vo2,max_hr",
         (f"{day_date(n)},{w.day_steps(n)},{w.exercise_min(n)},{round(w.days[n].rhr)},"
          f"{w.nights[n].resp:.1f},{_num(w.days[n].vo2)},{w.max_hr(n)}" for n in days))


def write_morning(n: int, folder: Path, now: datetime | None = None, first: int | None = None) -> None:
    """What the skill sends on day n (state.json excepted), asked at `now` (default: just after waking). `first` is
    the first day needing activity (default n - 1, yesterday; earlier after skipped mornings): heart rate, steps,
    HRV and workouts run from its midnight to now, sleep samples from those ending after noon the day before it."""
    w = world()
    folder.mkdir(parents=True, exist_ok=True)
    now = now or w.days[n].now
    first = n - 1 if first is None else first
    y0 = at(day_date(first), 0)
    since = at(day_date(first - 1), 12 * 60)
    nights = range(max(1, first - 1), min(n + 1, DAYS) + 1)
    samples = w.sleep_samples(nights, range(max(1, first - 1), n + 1))
    _csv(folder / "sleep.csv", "start,end,code",
         (f"{iso(s)},{iso(e)},{c}" for s, e, c in samples if since < e <= now))
    _csv(folder / "hrv.csv", "time,ms,type", (f"{iso(t)},{ms},S" for t, ms in w.hrv if y0 <= t < now))
    readings = [(t, b) for t, b in w.readings if y0 <= t < now]
    hours: dict[datetime, list] = {}
    for t, b in readings:
        hours.setdefault(t.replace(minute=0, second=0), []).append(b)
    lines = []
    for h, bpms in sorted(hours.items()):
        a = w.idx(h)
        st = sum(w.steps[a:min(a + 60, w.idx(now))])
        lines.append(f"{iso(h)},{sum(bpms) / len(bpms):.1f},{min(bpms)},{max(bpms)},{st}")
    _csv(folder / "hours.csv", "hour_start,avg,min,max,steps", lines)
    ws = [x for m in range(first, n + 1) for x in w.days[m].workouts if y0 <= x["start"] < now]
    windows = [(x["start"], x["end"]) for x in ws]
    active = {h for h, bpms in hours.items() if max(bpms) >= MINUTES_HR}
    _csv(folder / "minutes.csv", "time,bpm",
         (f"{iso(t)},{b}" for t, b in readings
          if t.replace(minute=0, second=0) in active or any(s <= t < e for s, e in windows)))
    _csv(folder / "workouts.csv", "start,end,kind,kcal,avg_hr", _workout_lines(ws))
    night, yday = w.nights[n], w.days[n - 1]
    vitals = {"resp": round(night.resp, 1), "spo2": round(night.spo2, 1), "wtemp": round(night.wtemp, 2),
              "rhr": round(w.days[n].rhr), "vo2": yday.vo2, "exercise_min_yesterday": w.exercise_min(n - 1)}
    compact = {"separators": (",", ":")}   # the courier leaves out spaces and null keys (missing key = null)
    (folder / "vitals.json").write_text(json.dumps({k: v for k, v in vitals.items() if v is not None}, **compact))
    (folder / "journal.json").write_text(json.dumps({"date": day_date(n - 1).isoformat(), "answers": yday.habits},
                                                    **compact))


# ---------------------------------------------------------------- the skill's mornings, through the CLI
def _write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, separators=(",", ":")))


def _cli(*args: str) -> None:
    from recovery_coach.cli import main
    code = main(list(args))
    if code != 0:
        raise RuntimeError(f"recovery_coach {' '.join(args)} exited {code}")


def run_chain(work: Path, last: int = DAYS) -> dict:
    """The first run (backfill of days 1-60), then each morning from day 61 to `last` exactly as the skill does it,
    with its two stored documents: each morning writes the inputs plus state.json = the daily state (the previous
    result's state_next, verbatim), runs `run`, and stores the new state_next and the result's rows (the dashboard's
    per-day documents, merged by date). On Mondays it also runs `weekly` for the week that ended yesterday, from
    state.json and state_long.json (the stored long state), and stores its state_long_next. Ends with an
    on-request `weekly` on the last day (not stored). Returns the results, the rows and both final documents."""
    from recovery_coach.inputs import merge_rows
    bf = work / "backfill"
    write_backfill(bf)
    _cli("backfill", "--in", str(bf), "--out", str(bf / "out"))
    backfill = json.loads((bf / "out" / "result.json").read_text())
    daily, long = backfill["state_next"], backfill["state_long_next"]
    rows = merge_rows([], backfill["rows"])
    days, weeklies = {}, {}

    def weekly(n, as_of):
        folder = work / f"weekly{n:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / "state.json", daily)
        _write_json(folder / "state_long.json", long)
        _cli("weekly", "--in", str(folder), "--out", str(folder / "out"), "--date", as_of.isoformat())
        return json.loads((folder / "out" / "weekly.json").read_text())

    for n in range(BACKFILL_DAYS + 1, last + 1):
        d = day_date(n)
        folder = work / f"day{n:03d}"
        write_morning(n, folder)
        _write_json(folder / "state.json", daily)
        _cli("run", "--in", str(folder), "--out", str(folder / "out"), "--date", d.isoformat())
        result = json.loads((folder / "out" / "result.json").read_text())
        rows = merge_rows(rows, result["rows"])
        daily = result["state_next"]
        days[n] = result
        if d.weekday() == 0:
            weeklies[n] = weekly(n, d - timedelta(days=1))
            long = weeklies[n]["state_long_next"]
    final = weekly(last, day_date(last))
    return {"backfill": backfill, "days": days, "weeklies": weeklies, "weekly": final, "state": daily,
            "state_long": long, "rows": rows}


def write_fixture(target: Path) -> None:
    """Regenerate the committed fixture: the morning of day 90 with the log as the chain leaves it."""
    with tempfile.TemporaryDirectory() as tmp:
        run_chain(Path(tmp))
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(Path(tmp) / f"day{DAYS:03d}", target, ignore=shutil.ignore_patterns("out"))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    write_fixture(Path(__file__).parent / "day90")
