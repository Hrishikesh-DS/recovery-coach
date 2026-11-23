"""Shared data model, and the compact input files Claude writes for each run.

Every file is CSV (first line = exactly the header shown; blank lines skipped; an empty cell = no value where a field
allows it) or JSON. Timestamps are ISO 8601 with the UTC offset the Health data carries, e.g. 2026-11-19T06:20-0600
(also accepted: -06:00, Z, seconds, fractional seconds, a space for the T or before the offset); minute resolution is
enough everywhere
and keeps a morning's files small. Local dates and clock times always come from each timestamp's own offset, so DST
changes and trips abroad need nothing special.
Anything malformed or implausible stops the run with InputError, printed as "<file> line <n>: <problem>" (JSON value
problems: "<file>: <where>: <problem>"); nothing is guessed.

The input folder. Each command requires some files (REQUIRED below) and stops with exit 2 and
"<file>: file not found (write it even when it has no data)" when one is absent, so a forgotten file is never read as
"no data". A file that is present but empty means no data: a CSV with only its header line (or zero bytes), and
vitals.json as {} (or zero bytes). Any other file is optional.
  run       state.json, sleep.csv, hrv.csv, hours.csv, minutes.csv, workouts.csv, vitals.json (journal.json optional)
  backfill  state.json, sleep.csv, hrv.csv, hours.csv, workouts.csv, daily.csv (minutes.csv optional)
  weekly    state.json (state_long.json optional)
  workout   state.json, workouts.csv, wk_stats.json, wk_hr.csv, wk_series.csv
For backfill every file covers its whole span instead of the morning's (backfill.py); `import` (export.py) writes
them all, minutes.csv included, from an Apple Health export.

state.json      the daily state: the previous run's "state_next", copied verbatim (state.py)
    {"settings": {...}, "cols": {...}, "crc": n}                  compact form, what the engine writes
    {"settings": {...}, "rows": [{...}, ...], "weeks": [...]}     row form, also accepted (not both)
    crc       the checksum of cols (and weeks), state_crc below. When present it must match, else
              "state.json: checksum mismatch (the state was not copied exactly; copy it again)"; a state without
              it (row form, hand-built) is read unchecked.
    The row form is the recovery path when the stored state is lost: settings plus the per-day rows `run` and
    `backfill` emitted (merged by date, as the dashboard keeps them; every key a row can carry is accepted).
    settings  dob "YYYY-MM-DD" and sex "male"|"female" (both required); sleep_goal_min (default 480, 240-720);
              hrmax (null or 100-230 bpm); k (null or 10-2000); hrv_type "sdnn"|"rmssd" (default "sdnn");
              questions [str] (journal); weight_kg (null or 30-250, kg only); height_m (null or 1.2-2.3, metres)
    cols      {"date": ["YYYY-MM-DD", ...], <field>: [value per date], ...}: one column per DayRow field, aligned
              to "date" (null = no value). A column shorter than "date" covers the most recent dates (the earlier
              ones are null). Journal answers: one column per question, "journal:<question>", 1 / 0 / null.
    rows      one object per date with DayRow's keys (a missing key = null), in any order
    In both forms a missing zone is derived from recovery, and a missing tsb from ctl - atl.
    weeks     weekly Body Age snapshots [{"date", "delta" (body age - age), "items": [item names]}] (items may be
              missing in old snapshots); used by `weekly`
state_long.json the long state (`weekly` only, optional): the last weekly's or backfill's "state_long_next",
    {"cols": {...}, "weeks": [...], "crc": n} (up to 180 days; settings are taken from state.json; crc as above).
    Without it, `weekly` works from state.json alone.
D below is the first day needing activity (pipeline.activity_from; `python3 -m recovery_coach.helper first-day`):
yesterday (T-1) normally, earlier after skipped mornings, never before T-3. The four activity files run from D 00:00
to now; days before T-1 that they don't cover simply stay without activity.
sleep.csv       start,end,code
    Sleep Analysis samples that end after 12:00 (clock time) on the day before D, normally T-2, up to now: the
    night before last is then whole at any time of day, so its tail is never taken for a nap. code: C core, D deep,
    R REM, A awake, S asleep (no stage), B in bed. end >= start.
hrv.csv         time,ms,type
    HRV readings, D 00:00 -> now. ms 5-300. type: S SDNN, R RMSSD. Only readings of settings.hrv_type
    count (the two are on different scales and never share a baseline); the other type is ignored.
hours.csv       hour_start,avg,min,max,steps
    One line per clock hour, D 00:00 -> now: average / lowest / highest heart rate (25-250 bpm; empty when
    the hour has no readings) and the hour's step count (>= 0; empty = 0).
minutes.csv     time,bpm
    Heart rate minute by minute (25-250 bpm) inside workouts and inside hours whose max reaches 50 % of HRmax,
    D 00:00 -> now.
workouts.csv    start,end,kind,kcal,avg_hr
    Workouts that started D 00:00 -> now. kind = the HealthKit activity type (running,
    traditionalStrengthTraining, other, ...). kcal (>= 0) and avg_hr (25-250) may be empty.
vitals.json
    {"resp", "spo2", "wtemp", "rhr", "vo2", "exercise_min_yesterday", "weight_kg", "height_m"}, each a number or
    null (a missing key = null): last night's respiratory rate (4-40 /min), SpO2 (70-100 %; a 0.70-1.00 fraction is
    converted), wrist temperature (30-42, Celsius only), today's Apple resting HR (25-250 bpm), a VO2 max reading
    from yesterday (10-90), yesterday's exercise minutes (0-1440), weight (30-250, kg only) and height (1.2-2.3,
    metres only; a value in cm is rejected). Convert before writing: a weight in pounds (e.g. 165) also lies inside
    the kg range, so it can't be caught here; the courier divides pounds by 2.2046 (likewise F -> C, cm or ft/in -> m).
journal.json
    {"date": "YYYY-MM-DD", "answers": {question: true|false}}: answers about behaviour on that date.
Post-workout files (all optional for `run`; `workout` requires workouts.csv, wk_stats.json, wk_hr.csv and
wk_series.csv). A workout's id is its local start to the minute with its offset, e.g. 2026-11-21T08:23-0600
(workout_id below); efforts.json keys are normalised to it.
wk_hr.csv       time,bpm
    Raw heart-rate samples (25-250 bpm) inside each new workout, from its start to 3 minutes after its end.
wk_series.csv   time,speed,power,dist
    Run samples: running speed (0-12 m/s), running power (0-1500 W) and cumulative distance since the run's start
    (0-300000 m); any cell may be empty.
wk_stats.json   {"workouts": [{"start", "end", "kind", "indoor", "dist" (m), "elev_up", "elev_down" (m),
                 "temp_c", "avg_hr", "max_hr", "avg_speed" (m/s), "avg_power" (W), "stride" (m), "vo_cm",
                 "gct_ms", "effort", "effort_est" (Apple Effort 1-10, rated / estimated), "cardio_recovery"
                 (Apple's 1-minute recovery, bpm), "duration" (moving seconds, pauses excluded),
                 "laps": [{"start", "end", "dist"}]}]}
    start, end and kind are required; every other key may be missing or null.
night_hr.csv    time,bpm
    Heart rate minute by minute (25-250 bpm) across last night's main sleep (the Recovery Index).
efforts.json    {"answers": {"<workout start>": 1-10}}: his own effort ratings for workouts Apple has none for.
daily.csv       date,steps,exercise_min,rhr,resp,vo2,max_hr          (backfill only)
    One line per day of Health daily statistics; every field but date may be empty: steps (>= 0), exercise minutes
    (0-1440), resting HR, respiratory rate, VO2 max reading, highest heart rate of the day (same ranges as above).
"""
from __future__ import annotations

import csv
import json
import math
import re
import zlib
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


class InputError(Exception):
    """Malformed input, naming the file and line (line None: a whole-file or JSON value problem)."""

    def __init__(self, file: str, line: int | None, message: str):
        self.file = file
        self.line = line
        self.message = message
        super().__init__(f"{file} line {line}: {message}" if line is not None else f"{file}: {message}")


@dataclass
class SleepSample:
    start: datetime
    end: datetime
    stage: str  # 'core'|'deep'|'rem'|'awake'|'asleep'|'inbed'


@dataclass
class Night:
    wake_date: date
    onset: datetime
    wake: datetime
    asleep_min: float
    awake_bouts: int
    deep_min: float
    rem_min: float
    core_min: float
    efficiency: float


@dataclass
class Workout:
    start: datetime
    end: datetime
    kind: str
    energy_kcal: float | None = None
    avg_hr: float | None = None


@dataclass
class RunPoint:
    time: datetime
    speed: float | None = None   # m/s
    power: float | None = None   # W
    dist: float | None = None    # cumulative metres since the run's start


@dataclass
class Lap:
    start: datetime
    end: datetime
    dist: float | None = None


@dataclass
class WorkoutStats:
    start: datetime
    end: datetime
    kind: str
    indoor: bool | None = None
    dist: float | None = None
    elev_up: float | None = None
    elev_down: float | None = None
    temp_c: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_speed: float | None = None
    avg_power: float | None = None
    stride: float | None = None
    vo_cm: float | None = None
    gct_ms: float | None = None
    effort: float | None = None
    effort_est: float | None = None
    cardio_recovery: float | None = None
    duration: float | None = None
    laps: list[Lap] = field(default_factory=list)


@dataclass
class HourStat:
    start: datetime
    avg_hr: float | None = None
    min_hr: float | None = None
    max_hr: float | None = None
    steps: float = 0.0


@dataclass
class DayRow:
    """One per wake date; stored in the dashboard."""

    date: str
    recovery: int | None = None
    zone: str | None = None
    hrv_ln: float | None = None
    sleep_hr: float | None = None
    rhr: float | None = None
    resp: float | None = None
    spo2: float | None = None
    wtemp: float | None = None
    sleep_min: float | None = None
    need_min: float | None = None
    perf: float | None = None
    debt_min: float | None = None
    onset_min: int | None = None
    wake_min: int | None = None
    strain: float | None = None
    trimp: float | None = None
    stress: float | None = None
    still_hr: list[float] = field(default_factory=list)
    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    acwr: float | None = None
    steps: float | None = None
    mvpa_min: float | None = None
    vig_min: float | None = None
    strength_min: float | None = None
    vo2: float | None = None
    flags: list[str] = field(default_factory=list)
    journal: dict[str, bool] = field(default_factory=dict)
    wload: float | None = None      # workout-only Banister load that day (rest days 0)
    srpe: float | None = None       # session RPE that day (effort x minutes)
    azm: float | None = None        # Active Zone Minutes
    ri_h: float | None = None       # Recovery Index: hours slept after the overnight heart-rate low
    mono: float | None = None       # monotony of the 7 days ending that day
    tstrain: float | None = None    # training strain (weekly workout load x monotony)
    form_pct: float | None = None
    ramp: float | None = None
    pi7: float | None = None        # Polarization Index of the 7 days ending that day
    z13_min: float | None = None    # minutes in heart-rate zones 1-3 (50-80% HRmax) inside logged workouts
    z45_min: float | None = None    # minutes in zones 4-5 (>= 80% HRmax) inside logged workouts
    lbm_pct: float | None = None    # lean body mass, % of body mass (a reading that day)


@dataclass
class Contributor:
    name: str
    value: float
    baseline: float
    z: float
    points: float


@dataclass
class RecoveryResult:
    score: int | None
    zone: str | None
    contributors: list[Contributor] = field(default_factory=list)
    cap_reason: str | None = None
    reason_missing: str | None = None
    uncapped: int | None = None   # the score before any cap


@dataclass
class HrvReading:
    time: datetime
    ms: float
    type: str  # 'S' SDNN | 'R' RMSSD


@dataclass
class Vitals:
    resp: float | None = None
    spo2: float | None = None
    wtemp: float | None = None
    rhr: float | None = None
    vo2: float | None = None
    exercise_min_yesterday: float | None = None
    weight_kg: float | None = None
    height_m: float | None = None
    lbm_pct: float | None = None


@dataclass
class DailyStat:
    date: date
    steps: float | None = None
    exercise_min: float | None = None
    rhr: float | None = None
    resp: float | None = None
    vo2: float | None = None
    max_hr: float | None = None


@dataclass
class Journal:
    date: date
    answers: dict[str, bool]


@dataclass
class Settings:
    dob: date
    sex: str
    sleep_goal_min: float = 480.0
    hrmax: float | None = None
    k: float | None = None
    hrv_type: str = "sdnn"
    questions: list[str] = field(default_factory=list)
    weight_kg: float | None = None
    height_m: float | None = None


@dataclass
class Week:
    """A weekly Body Age snapshot: body age - age, and which items it was computed from (None: unknown)."""
    date: date
    delta: float | None
    items: tuple[str, ...] | None = None


@dataclass
class State:
    settings: Settings | None                                # None: a long state (settings live in state.json)
    rows: list[DayRow]                                       # sorted by date
    weeks: list[Week] = field(default_factory=list)          # sorted by date
    workouts: list = field(default_factory=list)             # workout_log.LogEntry, sorted by id


@dataclass
class Inputs:
    state: State
    sleep: list[SleepSample] | None = None
    hrv: list[HrvReading] | None = None
    hours: list[HourStat] | None = None
    minutes: list[tuple[datetime, float]] | None = None
    workouts: list[Workout] | None = None
    vitals: Vitals | None = None
    journal: Journal | None = None
    daily: list[DailyStat] | None = None
    state_long: State | None = None
    wk_hr: list[tuple[datetime, float]] | None = None
    wk_series: list[RunPoint] | None = None
    wk_stats: list[WorkoutStats] | None = None
    night_hr: list[tuple[datetime, float]] | None = None
    efforts: dict[str, float] | None = None
    body: dict[date, float] | None = None


# ---------------------------------------------------------------- plausible ranges
HR = (25, 250)
HRV_MS = (5, 300)
RESP = (4, 40)
SPO2 = (70, 100)
WTEMP = (30, 42)
WEIGHT = (30, 250)
HEIGHT = (1.2, 2.3)
VO2 = (10, 90)
LBM = (30, 100)
DAY_MIN = (0, 1440)
HOUR_STEPS = (0, 30_000)
DAY_STEPS = (0, 200_000)
KCAL = (0, 10_000)
SPEED = (0, 12)          # m/s
POWER = (0, 1500)        # W
DIST = (0, 300_000)      # m
EFFORT = (1, 10)
ELEV = (0, 20_000)       # m
TEMP_C = (-40, 60)
STRIDE = (0.2, 3.0)      # m
VO_CM = (1, 30)
GCT_MS = (50, 1000)
HRR_BPM = (0, 100)
DURATION_S = (0, 172_800)   # moving seconds, up to 48 h

SLEEP_CODES = {"C": "core", "D": "deep", "R": "rem", "A": "awake", "S": "asleep", "B": "inbed"}
HRV_TYPES = {"S", "R"}

_TS = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)? ?(Z|[+-]\d{2}:?\d{2})?")


def parse_time(text: str, file: str, line: int | None, naive_ok: bool = False) -> datetime:
    """An ISO 8601 timestamp that must carry a UTC offset (also with a space before it, e.g. 06:20:09 -0600).
    naive_ok: a timestamp without an offset is returned naive instead (the helper's localize attaches the zone)."""
    m = _TS.fullmatch(text.strip())
    if not m or (m.group(8) is None and not naive_ok):
        raise InputError(file, line, f"bad timestamp {text!r} (want e.g. 2026-11-19T06:20:09-06:00, with its offset)")
    y, mo, d, h, mi, s, frac, off = m.groups()
    try:
        if off is None:
            tz = None
        elif off == "Z":
            tz = timezone.utc
        else:
            digits = off[1:].replace(":", "")
            delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
            tz = timezone(-delta if off[0] == "-" else delta)
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), int((frac or "0")[:6].ljust(6, "0")),
                        tzinfo=tz)
    except ValueError:
        raise InputError(file, line, f"bad timestamp {text!r}") from None


def _parse_date(text, file: str, line: int | None, where: str = "date") -> date:
    try:
        if not isinstance(text, str) or len(text) != 10:
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise InputError(file, line, f"{where} {text!r} is not a valid YYYY-MM-DD date") from None


def _check(v: float, file: str, line: int | None, name: str, rng: tuple, hint: str = "") -> float:
    if not math.isfinite(v) or not rng[0] <= v <= rng[1]:
        raise InputError(file, line, f"{name} {v:g} is outside {rng[0]:g}-{rng[1]:g}{hint}")
    return v


def _cell(text: str, file: str, line: int, name: str, rng: tuple, empty_ok: bool = False) -> float | None:
    if text == "":
        if empty_ok:
            return None
        raise InputError(file, line, f"{name} is empty")
    try:
        v = float(text)
    except ValueError:
        raise InputError(file, line, f"{name} {text!r} is not a number") from None
    return _check(v, file, line, name, rng)


def _csv_rows(path: Path, header: list[str]):
    """(line number, stripped cells) for each data line, after checking the header and the field count."""
    name = path.name
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            first = next(reader, None)
            if first is None:   # zero bytes: no data
                return
            if [c.strip() for c in first] != header:
                got = ",".join(first)
                raise InputError(name, 1, f"expected header {','.join(header)}, got {got!r}")
            for row in reader:
                if not any(c.strip() for c in row):
                    continue
                if len(row) != len(header):
                    raise InputError(name, reader.line_num,
                                     f"expected {len(header)} fields ({','.join(header)}), got {len(row)}")
                yield reader.line_num, [c.strip() for c in row]
        except csv.Error as e:
            raise InputError(name, reader.line_num, f"unreadable CSV ({e})") from None


def read_sleep(path: Path) -> list[SleepSample]:
    name, out = path.name, []
    for n, (start, end, code) in _csv_rows(path, ["start", "end", "code"]):
        s, e = parse_time(start, name, n), parse_time(end, name, n)
        if code not in SLEEP_CODES:
            raise InputError(name, n, f"unknown sleep code {code!r} (use C, D, R, A, S or B)")
        if e < s:
            raise InputError(name, n, "end is before start")
        out.append(SleepSample(s, e, SLEEP_CODES[code]))
    return out


def read_hrv(path: Path) -> list[HrvReading]:
    name, out = path.name, []
    for n, (t, ms, kind) in _csv_rows(path, ["time", "ms", "type"]):
        when, value = parse_time(t, name, n), _cell(ms, name, n, "ms", HRV_MS)
        if kind not in HRV_TYPES:
            raise InputError(name, n, f"unknown HRV type {kind!r} (use S for SDNN or R for RMSSD)")
        out.append(HrvReading(when, value, kind))
    return out


def read_hours(path: Path) -> list[HourStat]:
    name, out = path.name, []
    for n, (t, avg, lo, hi, steps) in _csv_rows(path, ["hour_start", "avg", "min", "max", "steps"]):
        start = parse_time(t, name, n)
        a, mn, mx = (_cell(x, name, n, k, HR, empty_ok=True) for x, k in ((avg, "avg"), (lo, "min"), (hi, "max")))
        if mn is not None and mx is not None and mn > mx:
            raise InputError(name, n, f"min {mn:g} is above max {mx:g}")
        st = _cell(steps, name, n, "steps", HOUR_STEPS, empty_ok=True)
        out.append(HourStat(start, a, mn, mx, st or 0.0))
    return out


def read_minutes(path: Path) -> list[tuple[datetime, float]]:
    name = path.name
    return [(parse_time(t, name, n), _cell(bpm, name, n, "bpm", HR)) for n, (t, bpm) in _csv_rows(path, ["time", "bpm"])]


def read_workouts(path: Path) -> list[Workout]:
    name, out = path.name, []
    for n, (start, end, kind, kcal, avg) in _csv_rows(path, ["start", "end", "kind", "kcal", "avg_hr"]):
        s, e = parse_time(start, name, n), parse_time(end, name, n)
        if e < s:
            raise InputError(name, n, "end is before start")
        if not kind:
            raise InputError(name, n, "kind is empty")
        out.append(Workout(s, e, kind, _cell(kcal, name, n, "kcal", KCAL, empty_ok=True),
                           _cell(avg, name, n, "avg_hr", HR, empty_ok=True)))
    return out


def read_daily(path: Path) -> list[DailyStat]:
    name, out, seen = path.name, [], set()
    header = ["date", "steps", "exercise_min", "rhr", "resp", "vo2", "max_hr"]
    for n, (d, steps, ex, rhr, resp, vo2, mx) in _csv_rows(path, header):
        day = _parse_date(d, name, n)
        if day in seen:
            raise InputError(name, n, f"duplicate date {d}")
        seen.add(day)
        out.append(DailyStat(day, _cell(steps, name, n, "steps", DAY_STEPS, True),
                             _cell(ex, name, n, "exercise_min", DAY_MIN, True), _cell(rhr, name, n, "rhr", HR, True),
                             _cell(resp, name, n, "resp", RESP, True), _cell(vo2, name, n, "vo2", VO2, True),
                             _cell(mx, name, n, "max_hr", HR, True)))
    return out


def read_body(path: Path) -> dict[date, float]:
    """body.csv (the import's body composition): date,lbm_pct -> {date: lean mass % of body mass}."""
    name, out = path.name, {}
    for n, (d, pct) in _csv_rows(path, ["date", "lbm_pct"]):
        day = _parse_date(d, name, n)
        if day in out:
            raise InputError(name, n, f"duplicate date {d}")
        out[day] = _cell(pct, name, n, "lbm_pct", LBM, False)
    return out


def read_wk_hr(path: Path) -> list[tuple[datetime, float]]:
    return read_minutes(path)


def read_night_hr(path: Path) -> list[tuple[datetime, float]]:
    return read_minutes(path)


def read_wk_series(path: Path) -> list[RunPoint]:
    name = path.name
    return [RunPoint(parse_time(t, name, n), _cell(sp, name, n, "speed", SPEED, True),
                     _cell(pw, name, n, "power", POWER, True), _cell(d, name, n, "dist", DIST, True))
            for n, (t, sp, pw, d) in _csv_rows(path, ["time", "speed", "power", "dist"])]


def workout_id(start: datetime) -> str:
    """A workout's id: its local start to the minute with the UTC offset as +-HHMM (2026-11-21T08:23-0600)."""
    off = start.utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "-" if mins < 0 else "+"
    return f"{start:%Y-%m-%dT%H:%M}{sign}{abs(mins) // 60:02d}{abs(mins) % 60:02d}"


# ---------------------------------------------------------------- JSON files
def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise InputError(path.name, e.lineno, f"invalid JSON: {e.msg} (column {e.colno})") from None


def _num(v, file: str, where: str, rng: tuple | None = None, hint: str = "") -> float | None:
    """A JSON number (or null) inside rng."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise InputError(file, None, f"{where} must be a number or null, got {v!r}")
    return float(v) if rng is None else _check(float(v), file, None, where, rng, hint)


def _object(v, file: str, where: str, allowed) -> dict:
    if not isinstance(v, dict):
        raise InputError(file, None, f"{where} must be a JSON object")
    unknown = [k for k in v if k not in allowed]
    if unknown:
        raise InputError(file, None, f"{where}: unknown key {unknown[0]!r} (expected {', '.join(allowed)})")
    return v


def read_vitals(path: Path) -> Vitals:
    name = path.name
    doc = _object(_json(path) if path.stat().st_size else {}, name, "vitals", [f.name for f in fields(Vitals)])
    spo2 = _num(doc.get("spo2"), name, "spo2")
    if spo2 is not None and 0.70 <= spo2 <= 1.0:
        spo2 *= 100   # a fraction
    return Vitals(
        resp=_num(doc.get("resp"), name, "resp", RESP),
        spo2=None if spo2 is None else _check(spo2, name, None, "spo2", SPO2, " (%)"),
        wtemp=_num(doc.get("wtemp"), name, "wtemp", WTEMP, " (C)"),
        rhr=_num(doc.get("rhr"), name, "rhr", HR),
        vo2=_num(doc.get("vo2"), name, "vo2", VO2),
        exercise_min_yesterday=_num(doc.get("exercise_min_yesterday"), name, "exercise_min_yesterday", DAY_MIN),
        weight_kg=_num(doc.get("weight_kg"), name, "weight_kg", WEIGHT, " (kg)"),
        height_m=_num(doc.get("height_m"), name, "height_m", HEIGHT, " (metres, not cm)"),
        lbm_pct=_num(doc.get("lbm_pct"), name, "lbm_pct", LBM, " (%, not a fraction)"),
    )


def _answers(v, file: str, where: str) -> dict[str, bool]:
    if not isinstance(v, dict):
        raise InputError(file, None, f"{where} must be a JSON object of question: true|false")
    for q, a in v.items():
        if not isinstance(a, bool):
            raise InputError(file, None, f"{where}: answer to {q!r} must be true or false, got {a!r}")
    return dict(v)


def read_journal(path: Path) -> Journal:
    name = path.name
    doc = _object(_json(path), name, "journal", ["date", "answers"])
    if "date" not in doc or "answers" not in doc:
        raise InputError(name, None, "needs both date and answers")
    return Journal(_parse_date(doc["date"], name, None), _answers(doc["answers"], name, "answers"))


_STAT_NUMS = {"dist": DIST, "elev_up": ELEV, "elev_down": ELEV, "temp_c": TEMP_C, "avg_hr": HR, "max_hr": HR,
              "avg_speed": SPEED, "avg_power": POWER, "stride": STRIDE, "vo_cm": VO_CM, "gct_ms": GCT_MS,
              "effort": EFFORT, "effort_est": EFFORT, "cardio_recovery": HRR_BPM, "duration": DURATION_S}


def _time(v, file: str, where: str) -> datetime:
    if not isinstance(v, str):
        raise InputError(file, None, f"{where} must be a timestamp string")
    try:
        return parse_time(v, file, None)
    except InputError as e:
        raise InputError(file, None, f"{where}: {e.message}") from None


def read_wk_stats(path: Path) -> list[WorkoutStats]:
    name = path.name
    doc = _object(_json(path) if path.stat().st_size else {"workouts": []}, name, "wk_stats", ["workouts"])
    items = doc.get("workouts") or []
    if not isinstance(items, list):
        raise InputError(name, None, "workouts must be a list")
    out = []
    for i, w in enumerate(items):
        where = f"workouts[{i}]"
        w = _object(w, name, where, ["start", "end", "kind", "indoor", "laps", *_STAT_NUMS])
        if "start" not in w or "end" not in w or not w.get("kind"):
            raise InputError(name, None, f"{where} needs start, end and kind")
        s, e = _time(w["start"], name, f"{where}.start"), _time(w["end"], name, f"{where}.end")
        if e < s:
            raise InputError(name, None, f"{where}: end is before start")
        indoor = w.get("indoor")
        if indoor is not None and not isinstance(indoor, bool):
            raise InputError(name, None, f"{where}.indoor must be true, false or null")
        laps = []
        for j, lap in enumerate(w.get("laps") or []):
            lw = f"{where}.laps[{j}]"
            lap = _object(lap, name, lw, ["start", "end", "dist"])
            laps.append(Lap(_time(lap.get("start"), name, f"{lw}.start"), _time(lap.get("end"), name, f"{lw}.end"),
                            _num(lap.get("dist"), name, f"{lw}.dist", DIST)))
        nums = {k: _num(w.get(k), name, f"{where}.{k}", rng) for k, rng in _STAT_NUMS.items()}
        out.append(WorkoutStats(s, e, str(w["kind"]), indoor, laps=laps, **nums))
    return out


def read_efforts(path: Path) -> dict[str, float]:
    name = path.name
    doc = _object(_json(path) if path.stat().st_size else {}, name, "efforts", ["answers"])
    answers = doc.get("answers") or {}
    if not isinstance(answers, dict):
        raise InputError(name, None, "answers must be a JSON object of workout start: 1-10")
    return {workout_id(_time(k, name, "answers key")): _num(v, name, f"answers[{k!r}]", EFFORT)
            for k, v in answers.items()}


# ---------------------------------------------------------------- state.json and rows
_INT_FIELDS = {"recovery", "onset_min", "wake_min"}
_SPECIAL = {"date", "zone", "still_hr", "flags", "journal"}
ZONES = {"green", "yellow", "red"}
ROW_DECIMALS = {"hrv_ln": 4, "sleep_hr": 1, "rhr": 1, "resp": 2, "spo2": 1, "wtemp": 2, "sleep_min": 1,
                "need_min": 1, "perf": 3, "debt_min": 1, "strain": 2, "trimp": 1, "stress": 2, "ctl": 2, "atl": 2,
                "tsb": 2, "acwr": 3, "steps": 0, "mvpa_min": 1, "vig_min": 1, "strength_min": 1, "vo2": 1,
                "wload": 1, "srpe": 0, "azm": 0, "ri_h": 2, "mono": 2, "tstrain": 0, "form_pct": 1, "ramp": 1,
                "pi7": 2, "z13_min": 1, "z45_min": 1, "lbm_pct": 1}
ROW_KEYS = [f.name for f in fields(DayRow)]


def row_from_dict(d, file: str = "state.json", where: str = "row") -> DayRow:
    """A DayRow from a log row (missing keys = null; recovery and clock minutes become ints)."""
    if not isinstance(d, dict):
        raise InputError(file, None, f"{where} must be a JSON object")
    if "date" not in d:
        raise InputError(file, None, f"{where}: date is required")
    _parse_date(d["date"], file, None, f"{where}: date")
    where = f"{where} ({d['date']})"
    _object(d, file, where, ROW_KEYS)
    out = {"date": d["date"]}
    for k, v in d.items():
        if k in _SPECIAL:
            continue
        x = _num(v, file, f"{where}: {k}")
        out[k] = None if x is None else int(round(x)) if k in _INT_FIELDS else x
    if out.get("recovery") is not None and not 0 <= out["recovery"] <= 100:
        raise InputError(file, None, f"{where}: recovery {out['recovery']} is outside 0-100")
    zone = d.get("zone")
    if zone is not None and zone not in ZONES:
        raise InputError(file, None, f"{where}: zone must be green, yellow, red or null, got {zone!r}")
    out["zone"] = zone
    still = d.get("still_hr") or []
    if not isinstance(still, list):
        raise InputError(file, None, f"{where}: still_hr must be a list of numbers")
    out["still_hr"] = [_num(v, file, f"{where}: still_hr", HR) for v in still]
    flags = d.get("flags") or []
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        raise InputError(file, None, f"{where}: flags must be a list of strings")
    out["flags"] = list(flags)
    out["journal"] = _answers(d.get("journal") or {}, file, f"{where}: journal")
    if zone is None and out.get("recovery") is not None:
        from .recovery import zone_for   # local: recovery imports this module
        out["zone"] = zone_for(out["recovery"])
    if out.get("tsb") is None and out.get("ctl") is not None and out.get("atl") is not None:
        out["tsb"] = out["ctl"] - out["atl"]
    return DayRow(**out)


JOURNAL_COL = "journal:"


def _rows_from_cols(cols, file: str) -> list[dict]:
    """Row dicts from the compact columns (validated later by row_from_dict)."""
    if not isinstance(cols, dict):
        raise InputError(file, None, "cols must be a JSON object")
    dates = cols.get("date")
    if not isinstance(dates, list):
        raise InputError(file, None, "cols.date is required: a list of YYYY-MM-DD dates")
    rows = [{"date": d} for d in dates]
    for key, vals in cols.items():
        if key == "date":
            continue
        if key not in ROW_KEYS and not key.startswith(JOURNAL_COL):
            raise InputError(file, None, f"cols: unknown column {key!r}")
        if key == "journal":
            raise InputError(file, None, f"cols: use one journal:<question> column per question, not {key!r}")
        if not isinstance(vals, list):
            raise InputError(file, None, f"cols.{key} must be a list")
        if len(vals) > len(dates):
            raise InputError(file, None, f"cols.{key} has {len(vals)} values for {len(dates)} dates")
        start = len(dates) - len(vals)
        for row, v in zip(rows[start:], vals):
            if v is None:
                continue
            if key.startswith(JOURNAL_COL):
                if v not in (0, 1) or isinstance(v, float):
                    raise InputError(file, None, f"cols.{key} ({row['date']}): answer must be 1, 0 or null, got {v!r}")
                row.setdefault("journal", {})[key[len(JOURNAL_COL):]] = bool(v)
            else:
                row[key] = v
    return rows


def row_to_dict(row: DayRow, keys=None) -> dict:
    """A log row (rounded to the log's precision); with keys, only those keys (and the date)."""
    out = {}
    for k in ROW_KEYS if keys is None else ["date", *[k for k in keys if k != "date"]]:
        v = getattr(row, k)
        if k in ROW_DECIMALS and v is not None:
            v = round(float(v), ROW_DECIMALS[k])
        elif k == "still_hr":
            v = [round(float(x), 1) for x in v]
        elif k in ("flags", "journal"):
            v = type(v)(v)
        out[k] = v
    return out


def merge_rows(rows: list[dict], patches: list[dict]) -> list[dict]:
    """The log after a run: each patch overwrites the keys it carries in the row of its date (a new date adds a row);
    every other key is kept. Returns new dicts sorted by date; the inputs are not modified."""
    by = {r["date"]: dict(r) for r in rows}
    for p in patches:
        by.setdefault(p["date"], {"date": p["date"]}).update(p)
    return [by[d] for d in sorted(by)]


SETTING_KEYS = [f.name for f in fields(Settings)]


def _settings(doc, file: str) -> Settings:
    s = _object(doc, file, "settings", SETTING_KEYS)
    for key in ("dob", "sex"):
        if s.get(key) is None:
            raise InputError(file, None, f"settings.{key} is required")
    if s["sex"] not in ("male", "female"):
        raise InputError(file, None, f"settings.sex must be 'male' or 'female', got {s['sex']!r}")
    hrv_type = s.get("hrv_type") or "sdnn"
    if hrv_type not in ("sdnn", "rmssd"):
        raise InputError(file, None, f"settings.hrv_type must be 'sdnn' or 'rmssd', got {hrv_type!r}")
    questions = s.get("questions") or []
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise InputError(file, None, "settings.questions must be a list of strings")
    goal = _num(s.get("sleep_goal_min"), file, "settings.sleep_goal_min", (240, 720))
    return Settings(
        dob=_parse_date(s["dob"], file, None, "settings.dob"), sex=s["sex"],
        sleep_goal_min=480.0 if goal is None else goal,
        hrmax=_num(s.get("hrmax"), file, "settings.hrmax", (100, 230)),
        k=_num(s.get("k"), file, "settings.k", (10, 2000)),
        hrv_type=hrv_type, questions=list(questions),
        weight_kg=_num(s.get("weight_kg"), file, "settings.weight_kg", WEIGHT, " (kg)"),
        height_m=_num(s.get("height_m"), file, "settings.height_m", HEIGHT, " (metres, not cm)"),
    )


def read_state_long(path: Path) -> State:
    """state_long.json: like state.json but settings may be left out (they are taken from state.json)."""
    return read_state(path, settings_required=False)


def _canonical(v):
    """A JSON value as any JSON store reads it back: whole floats as ints (54.0 -> 54), recursively."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        return [_canonical(x) for x in v]
    if isinstance(v, dict):
        return {k: _canonical(x) for k, x in v.items()}
    return v


def state_crc(cols, weeks=None, workouts=None) -> int:
    """The checksum of a state document: zlib.crc32 of the canonical JSON of {"cols": cols} (with "weeks" and
    "workouts" when the document has them): sorted keys, no spaces, ASCII, whole numbers without ".0". A document
    without "workouts" checks exactly as before the workout log existed."""
    payload = {"cols": cols}
    if weeks is not None:
        payload["weeks"] = weeks
    if workouts is not None:
        payload["workouts"] = workouts
    return zlib.crc32(json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":")).encode("ascii"))


def read_state(path: Path, settings_required: bool = True) -> State:
    name = path.name
    doc = _object(_json(path), name, "state", ["settings", "cols", "rows", "weeks", "workouts", "crc"])
    if "settings" not in doc and settings_required:
        raise InputError(name, None, "settings is required")
    if "cols" in doc and "rows" in doc:
        raise InputError(name, None, "has both cols and rows: use one form, not both")
    if "crc" in doc:
        if isinstance(doc["crc"], bool) or not isinstance(doc["crc"], int):
            raise InputError(name, None, f"crc must be a whole number, got {doc['crc']!r}")
        if "cols" not in doc:
            raise InputError(name, None, "crc goes with the cols form (leave it out of a rows state)")
        if doc["crc"] != state_crc(doc["cols"], doc.get("weeks"), doc.get("workouts")):
            raise InputError(name, None, "checksum mismatch (the state was not copied exactly; copy it again)")
    settings = _settings(doc["settings"], name) if "settings" in doc else None
    if "cols" in doc:
        raw, where = _rows_from_cols(doc["cols"], name), "cols"
    else:
        raw, where = doc.get("rows") or [], "rows"
        if not isinstance(raw, list):
            raise InputError(name, None, "rows must be a list")
    rows, seen = [], set()
    for i, r in enumerate(raw):
        row = row_from_dict(r, name, f"{where}[{i}]")
        if row.date in seen:
            raise InputError(name, None, f"{where}[{i}]: duplicate date {row.date}")
        seen.add(row.date)
        rows.append(row)
    weeks = []
    for i, w in enumerate(doc.get("weeks") or []):
        w = _object(w, name, f"weeks[{i}]", ["date", "delta", "items"])
        items = w.get("items")
        if items is not None and (not isinstance(items, list) or not all(isinstance(x, str) for x in items)):
            raise InputError(name, None, f"weeks[{i}].items must be a list of item names")
        weeks.append(Week(_parse_date(w.get("date"), name, None, f"weeks[{i}].date"),
                          _num(w.get("delta"), name, f"weeks[{i}].delta", (-50, 50)),
                          None if items is None else tuple(items)))
    from .workout_log import entry_from_dict
    log = doc.get("workouts") or []
    if not isinstance(log, list):
        raise InputError(name, None, "workouts must be a list")
    entries = [entry_from_dict(w, name, f"workouts[{i}]") for i, w in enumerate(log)]
    return State(settings, sorted(rows, key=lambda r: r.date), sorted(weeks, key=lambda w: w.date),
                 sorted(entries, key=lambda e: e.id))


OPTIONAL = {"sleep.csv": ("sleep", read_sleep), "hrv.csv": ("hrv", read_hrv), "hours.csv": ("hours", read_hours),
            "minutes.csv": ("minutes", read_minutes), "workouts.csv": ("workouts", read_workouts),
            "vitals.json": ("vitals", read_vitals), "journal.json": ("journal", read_journal),
            "daily.csv": ("daily", read_daily), "state_long.json": ("state_long", read_state_long),
            "wk_hr.csv": ("wk_hr", read_wk_hr), "wk_series.csv": ("wk_series", read_wk_series),
            "wk_stats.json": ("wk_stats", read_wk_stats), "night_hr.csv": ("night_hr", read_night_hr),
            "efforts.json": ("efforts", read_efforts), "body.csv": ("body", read_body)}


REQUIRED = {
    "run": ("state.json", "sleep.csv", "hrv.csv", "hours.csv", "minutes.csv", "workouts.csv", "vitals.json"),
    "backfill": ("state.json", "sleep.csv", "hrv.csv", "hours.csv", "workouts.csv", "daily.csv"),
    "weekly": ("state.json",),
    "workout": ("state.json", "workouts.csv", "wk_stats.json", "wk_hr.csv", "wk_series.csv"),
}


def load_inputs(folder: Path, required: tuple[str, ...] = ("state.json",)) -> Inputs:
    """Read every input file in the folder. The files named in `required` (REQUIRED[command]) must exist, else
    InputError "<file>: file not found (write it even when it has no data)" (state.json: "file not found"); a file
    that exists but is empty is no data. Any other missing file is None."""
    folder = Path(folder)
    for name in dict.fromkeys(("state.json", *required)):
        if not (folder / name).is_file():
            raise InputError(name, None, "file not found" if name == "state.json"
                             else "file not found (write it even when it has no data)")
    found = {attr: reader(folder / name) for name, (attr, reader) in OPTIONAL.items() if (folder / name).is_file()}
    return Inputs(read_state(folder / "state.json"), **found)
