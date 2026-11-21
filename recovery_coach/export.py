"""Import history from an Apple Health export, straight into the back-fill input files (no courier):

    python3 -m recovery_coach import --export PATH --out DIR --until YYYY-MM-DD [--days 180] [--nights 90]

PATH is export.zip (Health app > profile > Export All Health Data) or the export.xml inside it. From the zip only
apple_health_export/export.xml is read, decompressed as it streams (nothing is extracted; export_cda.xml, workout
routes and ECG files are never opened). The XML (0.5-3 GB in real exports) is read with ElementTree.iterparse and
each element is cleared once read, so memory stays flat: what is kept is bounded by the span, not by the file.

Written into DIR, in the formats of inputs.py, over the span ending U = --until (local dates):
  daily.csv     days U-days+1 .. U (a line for each day with any value): steps, exercise_min, rhr, resp, vo2,
                max_hr. steps = the day's hours.csv steps; rhr, resp and vo2 the day's average, exercise_min its sum,
                max_hr its highest heart rate.
  sleep.csv     Sleep Analysis samples of the nights U-nights+1 .. U: those ending after 12:00 on U-nights (the
                afternoon before the first night) up to U 24:00. Codes C, D, R, A, S (asleep, no stage; also the
                old "Asleep"), B.
  hrv.csv       every SDNN reading (S) and every RMSSD reading (R; any type whose name holds "RMSSD") from 12:00 on
                U-nights to U 24:00, to the whole ms.
  hours.csv     U-days+1 .. U: per clock hour, average (1 decimal), lowest and highest heart rate and the steps.
  minutes.csv   U-days+1 .. U: mean heart rate of each minute inside workouts and inside the hours whose max reaches
                50 % of HRmax (the morning's rule, helper.MINUTE_SHARE). HRmax here is the span's observed one
                (strain.hr_max of the daily maxima, without the age floor), never above the back-fill's calibrated
                HRmax, so every minute the morning rule would send is there.
  workouts.csv  workouts started U-days+1 .. U, from every source. kind = HKWorkoutActivityType without the prefix,
                lower camel as the Health tool returns it (running, traditionalStrengthTraining, walking, other);
                kcal = active energy (old exports: totalEnergyBurned); avg_hr = the workout's heart-rate statistic
                average when it has one, else the mean heart rate of the minutes inside it.
  vitals.json   {}
state.json is not touched (the caller writes it).

Timestamps keep each sample's own UTC offset as the export writes it (2026-01-15 22:40:12 -0600), so local dates and
clock hours come from each sample's offset; written to the minute (2026-01-15T22:40-0600).

Sources. The export holds every app and device that writes to Health:
- heart rate, HRV (SDNN and RMSSD), resting HR, respiratory rate, VO2 max and exercise minutes: the Apple Watch only
  (its `device` mentions "Watch", or the sourceName "Apple Watch"). Another source counts for a type only on a day
  without any watch sample of that type (by the sample's local date). SpO2 and wrist temperature are not imported:
  no back-fill file carries them.
- steps: per clock hour, the largest single source's total (the Watch and the iPhone count the same steps, so they
  are never summed).
- sleep: the watch's samples; other sources only for nights it didn't record (a night = noon to noon, by sample end).
- workouts: every source, as logged. Copies of one session (e.g. Strava + Hevy) are collapsed later, by
  strain.dedupe_workouts.
Values outside the engine's plausible ranges (inputs.py), unreadable values and unknown sleep codes are dropped and
counted in the summary.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
import zipfile
from array import array
from contextlib import ExitStack
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from .helper import MINUTE_SHARE
from .inputs import DAY_MIN, DAY_STEPS, HOUR_STEPS, HR, HRV_MS, KCAL, RESP, VO2, InputError, parse_time
from .strain import hr_max

Q = "HKQuantityTypeIdentifier"
KINDS = {f"{Q}HeartRate": "hr", f"{Q}HeartRateVariabilitySDNN": "S", f"{Q}RestingHeartRate": "rhr",
         f"{Q}RespiratoryRate": "resp", f"{Q}VO2Max": "vo2", f"{Q}AppleExerciseTime": "exercise",
         f"{Q}StepCount": "steps", "HKCategoryTypeIdentifierSleepAnalysis": "sleep"}
RANGES = {"hr": HR, "S": HRV_MS, "R": HRV_MS, "rhr": HR, "resp": RESP, "vo2": VO2, "exercise": DAY_MIN,
          "steps": DAY_STEPS}
DAILY_KINDS = ("rhr", "resp", "vo2", "exercise")
SLEEP_CODES = {"HKCategoryValueSleepAnalysisInBed": "B", "HKCategoryValueSleepAnalysisAsleepUnspecified": "S",
               "HKCategoryValueSleepAnalysisAsleep": "S", "HKCategoryValueSleepAnalysisAwake": "A",
               "HKCategoryValueSleepAnalysisAsleepCore": "C", "HKCategoryValueSleepAnalysisAsleepDeep": "D",
               "HKCategoryValueSleepAnalysisAsleepREM": "R"}
WORKOUT_PREFIX = "HKWorkoutActivityType"
ENERGY, HEART_RATE = f"{Q}ActiveEnergyBurned", f"{Q}HeartRate"
WATCH, OTHER = 0, 1
NOON = 12 * 60
MEMBER = "apple_health_export/export.xml"
FILES = ("daily.csv", "sleep.csv", "hrv.csv", "hours.csv", "minutes.csv", "workouts.csv")
HEADERS = {"daily.csv": "date,steps,exercise_min,rhr,resp,vo2,max_hr", "sleep.csv": "start,end,code",
           "hrv.csv": "time,ms,type", "hours.csv": "hour_start,avg,min,max,steps", "minutes.csv": "time,bpm",
           "workouts.csv": "start,end,kind,kcal,avg_hr"}


class _Skip(Exception):
    """A record with an unreadable date or value: dropped and counted."""


# ---------------------------------------------------------------- timestamps: (local date, minute of day, offset)
@lru_cache(maxsize=8192)
def _ordinal(day: str) -> int:
    try:
        return date.fromisoformat(day).toordinal()
    except ValueError:
        raise _Skip from None


@lru_cache(maxsize=8192)
def _day(ordinal: int) -> str:
    return date.fromordinal(ordinal).isoformat()


def _stamp(text: str | None) -> tuple[str, int, int]:
    """(local date, minute of the day, UTC offset in minutes) of an export timestamp, '2026-01-15 22:40:12 -0600'
    (the export's own format, read by slicing; anything else inputs.parse_time reads is accepted too)."""
    s = text or ""
    if len(s) == 25 and s[10] == " " and s[19] == " " and s[20] in "+-":
        try:
            h, m, off = int(s[11:13]), int(s[14:16]), int(s[21:23]) * 60 + int(s[23:25])
        except ValueError:
            raise _Skip from None
        if h > 23 or m > 59:
            raise _Skip
        _ordinal(s[:10])
        return s[:10], h * 60 + m, -off if s[20] == "-" else off
    try:
        t = parse_time(s, "export.xml", None)
    except InputError:
        raise _Skip from None
    return t.date().isoformat(), t.hour * 60 + t.minute, t.utcoffset() // timedelta(minutes=1)


def _utc(st: tuple[str, int, int]) -> int:
    """The stamp's UTC minute (minutes since 0001-01-01)."""
    return _ordinal(st[0]) * 1440 + st[1] - st[2]


def _local(utc_min: int, off: int) -> tuple[str, int, int]:
    local = utc_min + off
    return _day(local // 1440), local % 1440, off


def _fmt(st: tuple[str, int, int]) -> str:
    day, mod, off = st
    a = abs(off)
    return f"{day}T{mod // 60:02d}:{mod % 60:02d}{'-' if off < 0 else '+'}{a // 60:02d}{a % 60:02d}"


def _num(text: str | None, rng: tuple | None = None) -> float:
    try:
        v = float(text)
    except (TypeError, ValueError):
        raise _Skip from None
    if v != v or (rng is not None and not rng[0] <= v <= rng[1]):
        raise _Skip
    return v


def _is_watch(el) -> bool:
    # Health writes the watch's name with a no-break space ("Name's Apple\u00a0Watch") and sleep records carry no
    # device, so compare with spaces normalised and case ignored.
    device = (el.get("device") or "").replace("\u00a0", " ").lower()
    source = (el.get("sourceName") or "").replace("\u00a0", " ").lower()
    return "watch" in device or "apple watch" in source


def workout_kind(activity_type: str | None) -> str:
    """HKWorkoutActivityTypeTraditionalStrengthTraining -> traditionalStrengthTraining (as the Health tool says it)."""
    k = activity_type or ""
    if k.startswith(WORKOUT_PREFIX):
        k = k[len(WORKOUT_PREFIX):]
    return k[:1].lower() + k[1:] if k else "other"


def _kcal(value: str | None, unit: str | None) -> float:
    v = _num(value)
    return v / 4.184 if (unit or "").lower() == "kj" else v   # "Cal" and "kcal" are both kilocalories


# ---------------------------------------------------------------- the stream
def _top_level(stream):
    """The export's top-level elements (the children of HealthData), each cleared from the tree once used. Records
    nested in a Correlation (blood pressure, food) are not top level and are skipped."""
    depth, root = 0, None
    try:
        for event, el in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                if root is None:
                    if el.tag != "HealthData":
                        raise InputError("export.xml", None, f"not an Apple Health export (it starts with <{el.tag}>)")
                    root = el
                depth += 1
                continue
            depth -= 1
            if depth == 1:
                yield el
                root.clear()
    except ET.ParseError as e:
        raise InputError("export.xml", e.position[0],
                         f"unreadable XML ({e}): the export is incomplete or damaged; export it again") from None
    if root is None:
        raise InputError("export.xml", None, "is empty")


class _Collector:
    """Everything the span needs, gathered in one pass."""

    def __init__(self, until: date, days: int, nights: int):
        self.lo = (until - timedelta(days=days - 1)).isoformat()
        self.hi = until.isoformat()
        self.night_lo = (until - timedelta(days=nights)).isoformat()
        # minute heart rate: sums per UTC minute in fixed arrays (flat memory whatever the sampling rate), covering
        # the span's local dates at any offset (UTC+14 .. UTC-12)
        self.base = _ordinal(self.lo) * 1440 - 14 * 60
        self.n = (_ordinal(self.hi) + 1) * 1440 + 12 * 60 - self.base
        self.m_sum = [array("d", [0.0]) * self.n for _ in (WATCH, OTHER)]
        self.m_cnt = [array("I", [0]) * self.n for _ in (WATCH, OTHER)]
        self.m_off = [array("h", [0]) * self.n for _ in (WATCH, OTHER)]
        self.hr_hour: list[dict] = [{}, {}]       # (day, hour, offset) -> [sum, n, min, max]
        self.hr_day: list[dict] = [{}, {}]        # day -> highest bpm
        self.steps: dict = {}                     # (day, hour, offset) -> {source: steps}
        self.daily = {(k, c): {} for k in DAILY_KINDS for c in (WATCH, OTHER)}   # day -> [sum, n]
        self.hrv: list = []                       # (stamp, ms, type, class)
        self.sleep: list = []                     # (night key, class, start, end, code)
        self.workouts: list = []                  # [start, end, kind, kcal, avg_hr]
        self.records = self.dropped = self.rmssd = 0
        self.rmssd_types: set[str] = set()
        self.exported: str | None = None
        self.watch: dict = {}

    # ---- one element
    def take(self, el) -> None:
        tag = el.tag
        if tag == "Record":
            self.records += 1
            try:
                self._record(el)
            except _Skip:
                self.dropped += 1
        elif tag == "Workout":
            self.records += 1
            try:
                self._workout(el)
            except _Skip:
                self.dropped += 1
        elif tag == "ExportDate":
            self.exported = (el.get("value") or "")[:10] or None

    def _record(self, el) -> None:
        t = el.get("type") or ""
        kind = KINDS.get(t)
        if kind is None:
            if "RMSSD" not in t:
                return
            kind = "R"
            self.rmssd += 1
            self.rmssd_types.add(t)
        start = el.get("startDate") or ""
        if kind == "sleep":
            return self._sleep(el, start)
        day = start[:10]
        if kind in ("S", "R"):
            if not self.night_lo <= day <= self.hi:
                return
            st = _stamp(start)
            if (st[0], st[1]) >= (self.night_lo, NOON):
                self.hrv.append((st, _num(el.get("value"), HRV_MS), kind, WATCH if _is_watch(el) else OTHER))
            return
        if not self.lo <= day <= self.hi:
            return
        st = _stamp(start)
        v = _num(el.get("value"), RANGES[kind])
        if kind == "hr":
            c = WATCH if _is_watch(el) else OTHER
            i = _utc(st) - self.base
            if not 0 <= i < self.n:   # an offset beyond UTC-12 .. UTC+14
                raise _Skip
            self.m_sum[c][i] += v
            self.m_cnt[c][i] += 1
            self.m_off[c][i] = st[2]
            key = (st[0], st[1] // 60, st[2])
            acc = self.hr_hour[c].get(key)
            if acc is None:
                self.hr_hour[c][key] = [v, 1, v, v]
            else:
                acc[0] += v
                acc[1] += 1
                if v < acc[2]:
                    acc[2] = v
                if v > acc[3]:
                    acc[3] = v
            if v > self.hr_day[c].get(st[0], 0.0):
                self.hr_day[c][st[0]] = v
        elif kind == "steps":
            per = self.steps.setdefault((st[0], st[1] // 60, st[2]), {})
            src = el.get("sourceName") or ""
            per[src] = per.get(src, 0.0) + v
        else:
            acc = self.daily[(kind, WATCH if _is_watch(el) else OTHER)].setdefault(st[0], [0.0, 0])
            acc[0] += v
            acc[1] += 1

    def _sleep(self, el, start: str) -> None:
        end = el.get("endDate") or ""
        if not self.night_lo <= end[:10] <= self.hi:
            return
        code = SLEEP_CODES.get(el.get("value") or "")
        if code is None:
            raise _Skip
        s, e = _stamp(start), _stamp(end)
        if (e[0], e[1]) <= (self.night_lo, NOON):
            return
        if _utc(e) < _utc(s):
            raise _Skip
        key = _ordinal(e[0]) - (1 if e[1] < NOON else 0)   # noon to noon
        self.sleep.append((key, WATCH if _is_watch(el) else OTHER, s, e, code))

    def _workout(self, el) -> None:
        start = el.get("startDate") or ""
        if not self.lo <= start[:10] <= self.hi:
            return
        s, e = _stamp(start), _stamp(el.get("endDate"))
        if _utc(e) < _utc(s):
            raise _Skip
        kcal = avg = None
        for child in el:   # the workout's own totals (a WorkoutActivity's statistics are nested deeper)
            if child.tag != "WorkoutStatistics":
                continue
            kind = child.get("type")
            try:
                if kind == ENERGY:
                    kcal = _kcal(child.get("sum"), child.get("unit"))
                elif kind == HEART_RATE:
                    avg = _num(child.get("average"))
            except _Skip:
                self.dropped += 1
        if kcal is None and el.get("totalEnergyBurned") is not None:
            try:
                kcal = _kcal(el.get("totalEnergyBurned"), el.get("totalEnergyBurnedUnit"))
            except _Skip:
                self.dropped += 1
        if kcal is not None and not KCAL[0] <= kcal <= KCAL[1]:
            kcal, self.dropped = None, self.dropped + 1
        if avg is not None and not HR[0] <= avg <= HR[1]:
            avg, self.dropped = None, self.dropped + 1
        self.workouts.append([s, e, workout_kind(el.get("workoutActivityType")), kcal, avg])

    # ---- the files
    def files(self) -> dict[str, list[str]]:
        days = [_day(o) for o in range(_ordinal(self.lo), _ordinal(self.hi) + 1)]
        use = {d: WATCH if d in self.hr_day[WATCH] else OTHER
               for d in set(self.hr_day[WATCH]) | set(self.hr_day[OTHER])}

        hr_hours = {key: acc for c in (WATCH, OTHER) for key, acc in self.hr_hour[c].items() if use[key[0]] == c}
        step_hours = {}
        for key, per in self.steps.items():
            top = max(per.values())
            if top <= HOUR_STEPS[1]:
                step_hours[key] = top
            else:
                self.dropped += 1
        hours = []
        for key in set(hr_hours) | set(step_hours):
            acc = hr_hours.get(key)
            cells = [f"{acc[0] / acc[1]:.1f}", f"{acc[2]:.0f}", f"{acc[3]:.0f}"] if acc else ["", "", ""]
            st = (key[0], key[1] * 60, key[2])
            hours.append((_utc(st), ",".join([_fmt(st), *cells, f"{step_hours.get(key, 0):.0f}"])))

        day_steps: dict[str, float] = {}
        for key, v in step_hours.items():
            day_steps[key[0]] = day_steps.get(key[0], 0.0) + v
        maxima = {d: self.hr_day[c][d] for d, c in use.items()}
        daily = []
        for d in days:
            vals = {}
            for k in DAILY_KINDS:
                acc = self.daily[(k, WATCH)].get(d) or self.daily[(k, OTHER)].get(d)
                vals[k] = None if acc is None else acc[0] if k == "exercise" else acc[0] / acc[1]
            steps = day_steps.get(d)
            if steps is not None and steps > DAY_STEPS[1]:
                steps, self.dropped = None, self.dropped + 1
            if vals["exercise"] is not None and vals["exercise"] > DAY_MIN[1]:
                vals["exercise"], self.dropped = None, self.dropped + 1
            row = [steps, vals["exercise"], vals["rhr"], vals["resp"], vals["vo2"], maxima.get(d)]
            if any(v is not None for v in row):
                nd = (0, 0, 1, 1, 1, 0)
                daily.append(",".join([d, *("" if v is None else f"{v:.{p}f}" for v, p in zip(row, nd))]))

        # minute windows: workouts, and hours whose max reaches 50 % of the span's observed HRmax
        ref = hr_max(list(maxima.values())) or min(maxima.values(), default=None)
        mask = array("b", [0]) * self.n

        def mark(a: int, b: int) -> None:   # UTC minutes a .. b - 1
            a, b = max(a - self.base, 0), min(b - self.base, self.n)
            if a < b:
                mask[a:b] = array("b", [1]) * (b - a)

        for w in self.workouts:
            mark(_utc(w[0]), _utc(w[1]) + 1)
        if ref is not None:
            for key, acc in hr_hours.items():
                if acc[3] >= MINUTE_SHARE * ref:
                    h = _utc((key[0], key[1] * 60, key[2]))
                    mark(h, h + 60)
        minutes = []
        for c in (WATCH, OTHER):
            m_sum, m_cnt, m_off = self.m_sum[c], self.m_cnt[c], self.m_off[c]
            for i in range(self.n):
                if m_cnt[i] and mask[i]:
                    st = _local(self.base + i, m_off[i])
                    if use.get(st[0]) == c:
                        minutes.append((self.base + i, f"{_fmt(st)},{m_sum[i] / m_cnt[i]:.0f}"))

        workouts = []
        for s, e, kind, kcal, avg in self.workouts:
            c = use.get(s[0])
            if avg is None and c is not None:
                a, b = max(_utc(s) - self.base, 0), min(_utc(e) - self.base + 1, self.n)
                n = sum(self.m_cnt[c][a:b])
                avg = sum(self.m_sum[c][a:b]) / n if n else None
            workouts.append((_utc(s), ",".join([_fmt(s), _fmt(e), kind, "" if kcal is None else f"{kcal:.0f}",
                                                "" if avg is None else f"{avg:.0f}"])))

        watch_nights = {key for key, c, *_ in self.sleep if c == WATCH}
        last = _ordinal(self.hi)   # samples ending on U after noon: only that day's naps, no night
        self.watch = {"hr_days": len(use), "watch_hr_days": sum(c == WATCH for c in use.values()),
                      "nights": len({key for key, *_ in self.sleep if key < last}),
                      "watch_nights": len({key for key in watch_nights if key < last})}
        sleep = [(_utc(s), _utc(e), f"{_fmt(s)},{_fmt(e)},{code}") for key, c, s, e, code in self.sleep
                 if c == WATCH or key not in watch_nights]
        watch_hrv = {(kind, st[0]) for st, _, kind, c in self.hrv if c == WATCH}
        hrv = [(_utc(st), kind, f"{_fmt(st)},{ms:.0f},{kind}") for st, ms, kind, c in self.hrv
               if c == WATCH or (kind, st[0]) not in watch_hrv]

        def lines(items) -> list[str]:
            return [x[-1] for x in sorted(items)]

        return {"daily.csv": daily, "sleep.csv": lines(sleep), "hrv.csv": lines(hrv), "hours.csv": lines(hours),
                "minutes.csv": lines(minutes), "workouts.csv": lines(workouts)}


def _open(path: Path, stack: ExitStack):
    if not path.is_file():
        raise InputError(path.name, None, "file not found")
    if not zipfile.is_zipfile(path):
        return stack.enter_context(open(path, "rb"))
    try:
        z = stack.enter_context(zipfile.ZipFile(path))
        names = [n for n in z.namelist() if n.rsplit("/", 1)[-1] == "export.xml"]
        if not names:
            raise InputError(path.name, None, "has no export.xml (not an Apple Health export)")
        return stack.enter_context(z.open(min(names, key=lambda n: (n != MEMBER, n.count("/"), n))))
    except zipfile.BadZipFile as e:
        raise InputError(path.name, None, f"unreadable zip ({e}): export it again") from None


def import_export(export: Path, out: Path, until: date, days: int = 180, nights: int = 90) -> dict:
    """Read the export and write the back-fill input files into out. Returns the summary:
    {"files": {name: {"rows", "first", "last", "bytes"}}, "records", "dropped", "rmssd" (readings in the whole
    export), "rmssd_types", "rmssd_rows" (in hrv.csv), "exported" (the export's date or None), "seconds",
    "watch": {"hr_days", "watch_hr_days", "nights", "watch_nights"} (days with heart rate and nights with sleep in
    the span, and how many of them the Apple Watch recorded)}."""
    if days < 1 or nights < 1:
        raise InputError("--days" if days < 1 else "--nights", None, "must be at least 1")
    t0 = time.monotonic()
    col = _Collector(until, days, nights)
    with ExitStack() as stack:
        for el in _top_level(_open(Path(export), stack)):
            col.take(el)
    files = col.files()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name in FILES:
        text = "\n".join([HEADERS[name], *files[name]]) + "\n"
        (out / name).write_text(text, encoding="utf-8")
        rows = files[name]
        summary[name] = {"rows": len(rows), "first": rows[0][:10] if rows else None,
                         "last": rows[-1][:10] if rows else None, "bytes": len(text.encode())}
    (out / "vitals.json").write_text("{}", encoding="utf-8")
    return {"files": summary, "records": col.records, "dropped": col.dropped, "rmssd": col.rmssd,
            "rmssd_types": sorted(col.rmssd_types), "rmssd_rows": sum(r.endswith(",R") for r in files["hrv.csv"]),
            "exported": col.exported, "seconds": round(time.monotonic() - t0, 1), "watch": col.watch}


def summary_lines(s: dict) -> list[str]:
    """What was read, which days the Apple Watch recorded, one line per file (rows, span, bytes), then the RMSSD
    finding."""
    head = f"read {s['records']:,} records in {s['seconds']:g} s"
    if s["exported"]:
        head += f" (export of {s['exported']})"
    if s["dropped"]:
        head += f"; dropped {s['dropped']:,} unreadable or implausible values"
    w = s["watch"]
    out = [head, f"Apple Watch: heart rate on {w['watch_hr_days']} of {w['hr_days']} days, sleep on "
                 f"{w['watch_nights']} of {w['nights']} nights (other sources only on the rest)"]
    for name, f in s["files"].items():
        span = f"{f['first']} .. {f['last']}" if f["rows"] else "no data"
        out.append(f"{name:13} {f['rows']:>8,} rows  {span:24} {f['bytes']:>12,} B")
    out.append("vitals.json   {}")
    if s["rmssd"]:
        out.append(f"RMSSD: {s['rmssd']:,} reading{'s' * (s['rmssd'] != 1)} in the export "
                   f"({', '.join(s['rmssd_types'])}), {s['rmssd_rows']:,} in hrv.csv")
    else:
        out.append("RMSSD: none in the export (hrv.csv has SDNN only)")
    return out
