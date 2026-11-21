"""Synthetic Apple Health exports for the import tests. Every value is invented; no real health data.

ExportWriter writes the export.xml format record by record (so a large file costs no memory): the XML declaration,
a DOCTYPE with an internal DTD, ExportDate, Me, Record (with MetadataEntry and HRV beat-to-beat children), Workout
(MetadataEntry, WorkoutEvent, a nested WorkoutActivity with its own statistics, WorkoutStatistics, WorkoutRoute),
Correlation and ActivitySummary. Timestamps are written as the export writes them, "2026-01-15 22:40:12 -0600".

steady(writer, first, days) writes a fictional person with a steady weekly routine in US Central time (so a
DST change falls inside any span that holds Nov 1 or the second Sunday of March), from these sources:
- the Apple Watch: heart rate every 10 minutes (every 10 seconds in workouts), sleep stages, SDNN at night and three
  times by day (and RMSSD at the same instants, 0.8 x SDNN, unless rmssd=False), resting HR, respiratory rate at
  night, VO2 max on Saturdays, exercise minutes, steps;
- the iPhone: steps (fewer than the watch while both are carried; a 21:00-21:15 walk with the phone only, the watch
  charging) and an "in bed" sample each night (its sleep schedule), which the import must drop;
- Polar Beat (a chest strap app): heart rate during the Saturday run, 3 bpm above the watch, which the import must
  drop on days the watch recorded;
- Hevy and Strava: each Monday, Wednesday and Friday strength session 18:00-18:50 is logged twice, by Hevy as
  traditionalStrengthTraining (energy, no heart-rate statistic) and by Strava as "other" (energy and a heart-rate
  statistic); Tuesday and Thursday 07:00-07:40 and Saturday 08:30-09:45 runs by the watch (heart-rate statistic);
- a 35-minute nap on Sunday afternoons (watch).

write_zip(xml, zip) packs an export.xml as the Health app does: apple_health_export/export.xml beside
export_cda.xml (here deliberately not XML at all, so a reader that opened it would fail), a workout route and an
ECG file.

As a script, a large export for the import's time and memory check:
    python3 tests/fixtures/synthetic_export.py OUT.zip --days 3000 --until 2026-11-20
"""
from __future__ import annotations

import argparse
import math
import random
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import quoteattr

UTC = timezone.utc
CDT = timezone(timedelta(hours=-5))
CST = timezone(timedelta(hours=-6))
Q = "HKQuantityTypeIdentifier"
HR_TYPE, SDNN, RMSSD = f"{Q}HeartRate", f"{Q}HeartRateVariabilitySDNN", f"{Q}HeartRateVariabilityRMSSD"
STEPS, RHR, RESP, VO2, EXERCISE = (f"{Q}StepCount", f"{Q}RestingHeartRate", f"{Q}RespiratoryRate", f"{Q}VO2Max",
                                   f"{Q}AppleExerciseTime")
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
SLEEP_VALUES = {"C": "AsleepCore", "D": "AsleepDeep", "R": "AsleepREM", "A": "Awake", "S": "AsleepUnspecified",
                "B": "InBed", "old": "Asleep"}

WATCH = ("Sam’s Apple Watch", "<<HKDevice: 0x2814a1cc0>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, "
                              "hardware:Watch7,5, software:11.1>")
PHONE = ("Sam’s iPhone", "<<HKDevice: 0x2814a1d40>, name:iPhone, manufacturer:Apple Inc., model:iPhone, "
                         "hardware:iPhone16,1, software:26.1>")
POLAR = ("Polar Beat", "<<HKDevice: 0x2814a1e00>, name:Polar H10, manufacturer:Polar Electro Oy, model:H10, "
                       "hardware:1, software:5.0>")
STRAVA = ("Strava", None)
HEVY = ("Hevy", None)
AUTOSLEEP = ("AutoSleep", None)
HRV_APP = ("HRV Tracker", None)

HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!-- HealthKit Export Version: 14 -->
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary|ClinicalRecord|Audiogram)*)>
<!ATTLIST HealthData
  locale CDATA #REQUIRED
>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate
  value CDATA #REQUIRED
>
<!ELEMENT Record ((MetadataEntry|HeartRateVariabilityMetadataList)*)>
<!ATTLIST Record
  type          CDATA #REQUIRED
  unit          CDATA #IMPLIED
  value         CDATA #IMPLIED
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
]>
<HealthData locale="en_US">
 <ExportDate value="{exported}"/>
 <Me HKCharacteristicTypeIdentifierDateOfBirth="" HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet" HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet" HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet" HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>
"""
MOTION = '  <MetadataEntry key="HKMetadataKeyHeartRateMotionContext" value="0"/>\n'


# ---------------------------------------------------------------- US Central time
def _dst(year: int) -> tuple[datetime, datetime]:
    """UTC start and end of daylight time: the second Sunday of March 02:00 CST, the first Sunday of November
    02:00 CDT."""
    mar = date(year, 3, 1)
    nov = date(year, 11, 1)
    start = mar + timedelta(days=(6 - mar.weekday()) % 7 + 7)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return datetime.combine(start, time(8), UTC), datetime.combine(end, time(7), UTC)


def zone(u: datetime) -> timezone:
    start, end = _dst(u.year)
    return CDT if start <= u < end else CST


def at(d: date, minutes: float) -> datetime:
    """The UTC instant of local wall time d 00:00 + minutes (an ambiguous time: its first occurrence)."""
    naive = datetime.combine(d, time()) + timedelta(minutes=minutes)
    u = naive.replace(tzinfo=CDT).astimezone(UTC)
    return u if zone(u) is CDT else naive.replace(tzinfo=CST).astimezone(UTC)


def stamp(u: datetime) -> str:
    """As the export writes it: 2026-01-15 22:40:12 -0600."""
    return u.astimezone(zone(u)).strftime("%Y-%m-%d %H:%M:%S %z")


# ---------------------------------------------------------------- the XML
class ExportWriter:
    """export.xml, written record by record. Use as a context manager (close writes the closing tag)."""

    def __init__(self, path: Path, exported: datetime | None = None):
        self.f = open(path, "w", encoding="utf-8")
        self.count = 0
        self.f.write(HEAD.replace("{exported}", stamp(exported or datetime(2026, 11, 20, 16, 0, tzinfo=UTC))))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        if self.f.closed:
            return
        t = datetime(2020, 1, 6, 15, 0, tzinfo=UTC)
        self.f.write(f' <Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Cuff" '
                     f'creationDate="{stamp(t)}" startDate="{stamp(t)}" endDate="{stamp(t)}">\n'
                     f'  <Record type="{HR_TYPE}" sourceName="Cuff" unit="count/min" startDate="{stamp(t)}" '
                     f'endDate="{stamp(t)}" value="201"/>\n'
                     f' </Correlation>\n'
                     f' <ActivitySummary dateComponents="2026-11-19" activeEnergyBurned="512" '
                     f'activeEnergyBurnedGoal="600" activeEnergyBurnedUnit="Cal" appleExerciseTime="45" '
                     f'appleExerciseTimeGoal="30" appleStandHours="12" appleStandHoursGoal="12"/>\n'
                     "</HealthData>\n")
        self.f.close()

    @staticmethod
    def _source(source) -> str:
        name, device = source
        dev = f" device={quoteattr(device)}" if device else ""
        return f"sourceName={quoteattr(name)} sourceVersion=\"11.1\"{dev}"

    def record(self, type_: str, source, start: datetime, end: datetime | None = None, value=None,
               unit: str | None = "count/min", children: str = "", raw_start: str | None = None) -> None:
        end = end or start
        u = f' unit="{unit}"' if unit else ""
        v = "" if value is None else f" value={quoteattr(str(value))}"
        attrs = (f'type="{type_}" {self._source(source)}{u} creationDate="{stamp(end)}" '
                 f'startDate="{raw_start or stamp(start)}" endDate="{stamp(end)}"{v}')
        self.f.write(f" <Record {attrs}>\n{children} </Record>\n" if children else f" <Record {attrs}/>\n")
        self.count += 1

    def hr(self, source, t: datetime, bpm) -> None:
        self.record(HR_TYPE, source, t, value=bpm, children=MOTION)

    def hrv(self, source, t: datetime, ms, rmssd: bool = False) -> None:
        beats = "".join(f'   <InstantaneousBeatsPerMinute bpm="{58 + k % 5}" time="1:0{k}:03.45 AM"/>\n'
                        for k in range(6))
        self.record(RMSSD if rmssd else SDNN, source, t, t + timedelta(seconds=60), ms, "ms",
                    f"  <HeartRateVariabilityMetadataList>\n{beats}  </HeartRateVariabilityMetadataList>\n")

    def sleep(self, source, start: datetime, end: datetime, code: str) -> None:
        self.record(SLEEP, source, start, end, f"HKCategoryValueSleepAnalysis{SLEEP_VALUES[code]}", None)

    def workout(self, kind: str, source, start: datetime, end: datetime, kcal: float | None = None,
                hr_avg: float | None = None, legacy: bool = False, kj: bool = False) -> None:
        minutes = (end - start).total_seconds() / 60
        head = (f'workoutActivityType="HKWorkoutActivityType{kind}" duration="{minutes:.4f}" durationUnit="min"')
        if legacy:   # the pre-iOS 16 form: totals as attributes, no statistics
            energy = "" if kcal is None else f' totalEnergyBurned="{kcal}" totalEnergyBurnedUnit="kcal"'
            self.f.write(f" <Workout {head}{energy} {self._source(source)} creationDate=\"{stamp(end)}\" "
                         f"startDate=\"{stamp(start)}\" endDate=\"{stamp(end)}\">\n"
                         f'  <MetadataEntry key="HKIndoorWorkout" value="0"/>\n </Workout>\n')
            self.count += 1
            return
        span = f'startDate="{stamp(start)}" endDate="{stamp(end)}"'
        body = ['  <MetadataEntry key="HKIndoorWorkout" value="0"/>\n',
                f'  <WorkoutEvent type="HKWorkoutEventTypeSegment" date="{stamp(start)}" duration="10" '
                'durationUnit="min"/>\n',
                f'  <WorkoutActivity uuid="00000000-0000-0000-0000-000000000000" {span} duration="{minutes:.4f}" '
                'durationUnit="min">\n'
                f'   <WorkoutStatistics type="{HR_TYPE}" {span} average="61" minimum="55" maximum="70" '
                'unit="count/min"/>\n'   # nested deeper: not the workout's own total
                '  </WorkoutActivity>\n']
        if kcal is not None:
            unit, value = ("kJ", round(kcal * 4.184, 1)) if kj else ("Cal", kcal)
            body.append(f'  <WorkoutStatistics type="{Q}ActiveEnergyBurned" {span} sum="{value}" unit="{unit}"/>\n')
        if hr_avg is not None:
            body.append(f'  <WorkoutStatistics type="{HR_TYPE}" {span} average="{hr_avg}" minimum="90" '
                        'maximum="175" unit="count/min"/>\n')
        body.append(f'  <WorkoutRoute {self._source(source)} creationDate="{stamp(end)}" {span}>\n'
                    f'   <FileReference path="/workout-routes/route_{start:%Y-%m-%d_%H.%M}.gpx"/>\n'
                    '  </WorkoutRoute>\n')
        self.f.write(f" <Workout {head} {self._source(source)} creationDate=\"{stamp(end)}\" {span}>\n"
                     + "".join(body) + " </Workout>\n")
        self.count += 1

    def raw(self, text: str) -> None:
        self.f.write(text)


def write_zip(xml: Path, out: Path) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(xml, "apple_health_export/export.xml")
        z.writestr("apple_health_export/export_cda.xml", "this is not XML <<<")
        z.writestr("apple_health_export/workout-routes/route_2026-11-19_7.00am.gpx", "<gpx/>")
        z.writestr("apple_health_export/electrocardiograms/ecg_2026-11-19.csv", "Name,Sam\n")
    return out


# ---------------------------------------------------------------- the steady routine
def _night(rng: random.Random, wake_day: date) -> tuple[datetime, datetime, list]:
    """The main sleep ending on wake_day: stage cycles from ~22:45 the evening before, ~440 minutes asleep."""
    onset = at(wake_day - timedelta(days=1), 22 * 60 + 45 + rng.gauss(0, 12))
    t, asleep, target, out = onset, 0.0, 440 + rng.gauss(0, 15), []
    cycle = 0
    while target - asleep >= 1:
        for code, dur in (("C", 30 + rng.uniform(-5, 5)), ("D", max(5.0, 28 - 6 * cycle)), ("C", 10.0),
                          ("R", 15 + 5 * cycle)):
            dur = min(dur, target - asleep)
            if dur < 1:
                break
            out.append((t, t + timedelta(minutes=dur), code))
            t += timedelta(minutes=dur)
            asleep += dur
        cycle += 1
        if target - asleep >= 1:
            out.append((t, t + timedelta(minutes=3), "A"))
            t += timedelta(minutes=3)
    return onset, t, out


def steady(w: ExportWriter, first: date, days: int, seed: int = 7, rmssd: bool = True, hr_every_min: int = 10) -> None:
    """The routine for days first .. first + days - 1 (each with the night that ends on it)."""
    rng = random.Random(seed)
    nights = {first + timedelta(days=n): _night(rng, first + timedelta(days=n)) for n in range(days + 1)}
    for n in range(days):
        d = first + timedelta(days=n)
        wd = d.weekday()
        onset, wake, stages = nights[d]
        tonight = nights[d + timedelta(days=1)]
        asleep = [(onset, wake), (tonight[0], tonight[1])]

        # ---- the night ending on d
        w.sleep(PHONE, onset - timedelta(minutes=10), wake + timedelta(minutes=5), "B")
        for s, e, code in stages:
            w.sleep(WATCH, s, e, code)
        for k in range(int((wake - onset).total_seconds() // 1800)):
            t = onset + timedelta(minutes=30 * k + 20, seconds=rng.randrange(60))
            ms = round(math.exp(math.log(55) + rng.gauss(0, 0.12)), 4)
            w.hrv(WATCH, t, ms)
            if rmssd:
                w.hrv(WATCH, t, round(0.8 * ms, 4), rmssd=True)
            if k % 2 == 0:
                w.record(RESP, WATCH, t, value=round(14.6 + rng.gauss(0, 0.3), 2))
        w.record(RHR, WATCH, at(d, 6 * 60 + 55), value=round(55 + rng.gauss(0, 1)))

        # ---- the day's plan
        walks = [(8 * 60, 20), (17 * 60 + 30, 20)] if wd < 5 else [(16 * 60, 40)]
        workouts = []   # (kind, start, minutes, level)
        if wd in (0, 2, 4):
            workouts.append(("TraditionalStrengthTraining", 18 * 60, 50, 0))
        elif wd in (1, 3):
            workouts.append(("Running", 7 * 60, 40, 150))
        elif wd == 5:
            workouts.append(("Running", 8 * 60 + 30, 75, 145))
        naps = [(14 * 60, 35)] if wd == 6 else []
        charging = (at(d, 21 * 60), at(d, 21 * 60 + 30))

        busy = [(at(d, s), at(d, s) + timedelta(minutes=m)) for _, s, m, _ in workouts]
        walk_spans = [(at(d, s), at(d, s) + timedelta(minutes=m)) for s, m in walks]
        nap_spans = [(at(d, s), at(d, s) + timedelta(minutes=m)) for s, m in naps]

        def inside(t, spans):
            return any(a <= t < b for a, b in spans)

        # ---- background heart rate (the watch), every hr_every_min minutes
        t, end = at(d, 0), at(d + timedelta(days=1), 0)
        while t < end:
            if not inside(t, busy) and not inside(t, [charging]):
                if inside(t, asleep):
                    bpm = 52 + rng.gauss(0, 1.5)
                elif inside(t, nap_spans):
                    bpm = 58 + rng.gauss(0, 1.5)
                elif inside(t, walk_spans):
                    bpm = 102 + rng.gauss(0, 4)
                else:
                    bpm = 70 + rng.gauss(0, 4)
                w.hr(WATCH, t + timedelta(seconds=rng.randrange(60)), round(bpm))
            t += timedelta(minutes=hr_every_min)

        # ---- workouts: watch heart rate every 10 s, the logs, steps and exercise minutes
        for kind, s, m, level in workouts:
            a = at(d, s)
            b = a + timedelta(minutes=m)
            bpms = []
            for k in range(m * 6):
                tt = a + timedelta(seconds=10 * k)
                if level:
                    bpm = 100 + min(1.0, (k + 1) / 36) * (level - 100) + rng.gauss(0, 4)
                else:
                    bpm = (132 if k % 18 < 6 else 104) + rng.gauss(0, 5)
                bpms.append(round(bpm))
                w.hr(WATCH, tt, bpms[-1])
                if wd == 5 and k % 6 == 0:
                    w.hr(POLAR, tt + timedelta(seconds=2), bpms[-1] + 3)
            avg = round(sum(bpms) / len(bpms))
            if level:
                w.workout(kind, WATCH, a, b, kcal=round(m * 11.5), hr_avg=avg)
            else:
                w.workout(kind, HEVY, a, b, kcal=round(m * 6.2))
                w.workout("Other", STRAVA, a + timedelta(seconds=20), b - timedelta(seconds=10), kcal=round(m * 6.0),
                          hr_avg=avg)
            for k in range(0, m, 10):
                c0 = a + timedelta(minutes=k)
                c1 = c0 + timedelta(minutes=min(10, m - k))
                if level:
                    w.record(STEPS, WATCH, c0, c1, 1600 + rng.randrange(60), "count")
                w.record(EXERCISE, WATCH, c0, c1, min(10, m - k), "min")
            if wd == 5:
                w.record(VO2, WATCH, b, value=round(47.5 + rng.gauss(0, 0.2), 2), unit="mL/min·kg")

        # ---- walks: both the watch and the phone count them (the phone a little less)
        for s, m in walks:
            for k in range(0, m, 10):
                c0 = at(d, s + k)
                c1 = c0 + timedelta(minutes=10)
                w.record(STEPS, WATCH, c0, c1, 1100 + rng.randrange(40), "count")
                w.record(STEPS, PHONE, c0 + timedelta(seconds=30), c1, 1000 + rng.randrange(40), "count")
                w.record(EXERCISE, WATCH, c0, c1, 10, "min")
        for h in range(7, 22):   # pottering about: one small count an hour from the watch
            c0 = at(d, h * 60 + 25)
            w.record(STEPS, WATCH, c0, c0 + timedelta(minutes=5), 40 + rng.randrange(30), "count")
        w.record(STEPS, PHONE, charging[0], charging[0] + timedelta(minutes=15), 600 + rng.randrange(40), "count")

        # ---- daytime HRV and the nap
        for h in (10, 13, 16):
            t = at(d, h * 60 + 40) + timedelta(seconds=rng.randrange(60))
            if not inside(t, busy):
                ms = round(math.exp(math.log(42) + rng.gauss(0, 0.2)), 4)
                w.hrv(WATCH, t, ms)
                if rmssd:
                    w.hrv(WATCH, t, round(0.8 * ms, 4), rmssd=True)
        for a, b in nap_spans:
            w.sleep(WATCH, a, b, "C")


def main() -> None:
    p = argparse.ArgumentParser(description="write a large synthetic Apple Health export (export.zip)")
    p.add_argument("out", type=Path)
    p.add_argument("--days", type=int, default=3000)
    p.add_argument("--until", default="2026-11-20")
    p.add_argument("--hr-every", type=int, default=5, help="minutes between background heart-rate readings")
    a = p.parse_args()
    last = date.fromisoformat(a.until)
    xml = a.out.with_suffix(".xml")
    with ExportWriter(xml) as w:
        steady(w, last - timedelta(days=a.days - 1), a.days, hr_every_min=a.hr_every)
    print(f"{w.count:,} records, {xml.stat().st_size:,} B of XML")
    if a.out.suffix == ".zip":
        write_zip(xml, a.out)
        xml.unlink()
        print(f"{a.out}: {a.out.stat().st_size:,} B")


if __name__ == "__main__":
    main()
