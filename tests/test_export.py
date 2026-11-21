"""Import from an Apple Health export (recovery_coach/export.py): synthetic exports only (tests/fixtures/
synthetic_export.py), as bare export.xml and as export.zip."""
import importlib.util
import json
import shutil
import subprocess
import sys
import time
import tracemalloc
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from recovery_coach.cli import main
from recovery_coach.export import import_export, summary_lines, workout_kind
from recovery_coach.inputs import (load_inputs, read_daily, read_hours, read_hrv, read_minutes, read_sleep,
                                   read_workouts, REQUIRED)
from recovery_coach.strain import dedupe_workouts

ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    path = Path(__file__).parent / "fixtures" / "synthetic_export.py"
    spec = importlib.util.spec_from_file_location("synthetic_export", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sx = _load_generator()
U = date(2026, 11, 19)                      # --until: the last day imported
SETTINGS = {"dob": "1990-06-15", "sex": "male", "sleep_goal_min": 480, "hrmax": None, "k": None, "hrv_type": "sdnn",
            "questions": [], "weight_kg": None, "height_m": None}
CDT, CST = sx.CDT, sx.CST


def local(d: date, h: int, m: int = 0, s: int = 0, tz=None) -> datetime:
    """A UTC instant from a local wall time (US Central unless tz is given)."""
    if tz is None:
        return sx.at(d, h * 60 + m) + timedelta(seconds=s)
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=tz).astimezone(timezone.utc)


def lines(folder: Path, name: str) -> list[str]:
    return (folder / name).read_text().splitlines()[1:]


def small_export(tmp_path: Path, build, name: str = "export.xml") -> Path:
    path = tmp_path / name
    with sx.ExportWriter(path) as w:
        build(w)
    return path


# ---------------------------------------------------------------- the steady 130 days, imported and back-filled
@pytest.fixture(scope="module")
def steady_export(tmp_path_factory) -> Path:
    """July 13 .. Nov 20 2026 (the morning after U); the DST change of Nov 1 inside."""
    xml = tmp_path_factory.mktemp("steady") / "export.xml"
    with sx.ExportWriter(xml) as w:
        sx.steady(w, U - timedelta(days=129), 131)
    return xml


@pytest.fixture(scope="module")
def imported(steady_export, tmp_path_factory):
    """The skill's import route: import 120 days (90 nights), then back-fill up to U."""
    bf = tmp_path_factory.mktemp("bf")
    summary = import_export(steady_export, bf, U, 120, 90)
    (bf / "state.json").write_text(json.dumps({"settings": SETTINGS, "rows": [], "weeks": []}))
    assert main(["backfill", "--in", str(bf), "--out", str(bf / "out"), "--date", U.isoformat()]) == 0
    return bf, summary, json.loads((bf / "out" / "result.json").read_text())


def test_import_writes_every_backfill_file_over_its_span(imported):
    bf, summary, _ = imported
    assert set(REQUIRED["backfill"]) <= {p.name for p in bf.iterdir()} and (bf / "minutes.csv").is_file()
    assert (bf / "vitals.json").read_text() == "{}"
    inp = load_inputs(bf, REQUIRED["run"])   # every file reads back cleanly, in the engine's formats
    first = U - timedelta(days=119)
    assert [d.date for d in inp.daily] == [first + timedelta(days=i) for i in range(120)]
    assert min(h.start for h in inp.hours).date() == first and max(h.start for h in inp.hours).date() == U
    assert min(t for t, _ in inp.minutes).date() == first and max(t for t, _ in inp.minutes).date() == U
    assert min(w.start for w in inp.workouts).date() >= first and max(w.start for w in inp.workouts).date() == U
    # nights U-89 .. U, from the afternoon before the first one: the night ending U-89 is whole, nothing earlier
    sleep = inp.sleep
    assert min(s.start for s in sleep).date() == U - timedelta(days=90) and min(s.end for s in sleep).hour >= 12
    assert max(s.end for s in sleep).date() == U
    assert min(x.time for x in inp.hrv).date() == U - timedelta(days=90)
    assert {x.type for x in inp.hrv} == {"S", "R"}
    assert summary["files"]["daily.csv"] == {"rows": 120, "first": first.isoformat(), "last": U.isoformat(),
                                             "bytes": (bf / "daily.csv").stat().st_size}


def test_import_keeps_each_samples_own_offset_across_dst(imported):
    bf, _, res = imported
    sleep = lines(bf, "sleep.csv")
    night = [x for x in sleep if x.startswith("2026-10-31T2") or x.startswith("2026-11-01T0")]
    assert night[0].split(",")[0].endswith("-0500") and night[-1].split(",")[1].endswith("-0600")
    hours = [x.split(",")[0] for x in lines(bf, "hours.csv") if x.startswith("2026-11-01T01")]
    assert hours == ["2026-11-01T01:00-0500", "2026-11-01T01:00-0600"]   # the repeated clock hour, twice
    row = next(r for r in res["rows"] if r["date"] == "2026-11-01")
    assert row["onset_min"] < 0 < row["wake_min"] and 400 < row["sleep_min"] < 480   # elapsed time, not clock time


def test_backfill_from_an_import_uses_real_trimp_stress_and_naps(imported):
    bf, _, res = imported
    rows = res["rows"]
    assert len(rows) == 120 and [b["date"] for b in res["backtest"]] == [r["date"] for r in rows]
    assert all(r["trimp"] > 0 and r["ctl"] > 0 and r["strain"] is not None and r["vig_min"] is not None
               for r in rows)
    assert sum(r["stress"] is not None for r in rows) >= 100 and all(r["still_hr"] for r in rows)
    assert sum(r["recovery"] is not None for r in rows) >= 70   # 90 nights, scored once 14 make a baseline
    assert sum(r["acwr"] is not None for r in rows) >= 90
    assert res["settings_patch"]["k"] != 170 and res["settings_patch"]["hrv_type"] == "rmssd"
    # the Strava + Hevy copies of each strength session count once, as strength
    lifts = [r for r in rows if date.fromisoformat(r["date"]).weekday() in (0, 2, 4)]
    assert lifts and all(r["strength_min"] == 50 for r in lifts)
    assert all(r["strength_min"] == 0 for r in rows if date.fromisoformat(r["date"]).weekday() in (1, 3, 5, 6))
    # Sunday's nap counts toward the night after it (Monday's wake date)
    def mean_sleep(weekdays):
        vals = [r["sleep_min"] for r in rows if date.fromisoformat(r["date"]).weekday() in weekdays and r["sleep_min"]]
        return sum(vals) / len(vals)
    assert mean_sleep((0,)) - mean_sleep((1, 2, 3, 4, 5, 6)) == pytest.approx(35, abs=10)
    # steps: the larger of the watch and the phone each hour, never their sum (the daily total is the hours' sum)
    hours = read_hours(bf / "hours.csv")
    for d in read_daily(bf / "daily.csv"):
        assert d.steps == sum(h.steps for h in hours if h.start.date() == d.date)


def test_next_run_after_an_import_shows_no_false_load_spike(steady_export, imported, tmp_path):
    """The first daily run after the import: real TRIMP for the whole history, so a steady week is no spike."""
    bf, _, res = imported
    run = tmp_path / "run"
    t = U + timedelta(days=1)
    import_export(steady_export, run, t, 2, 2)   # the morning's files, in the same formats
    (run / "state.json").write_text(json.dumps(res["state_next"]))
    assert main(["run", "--in", str(run), "--out", str(run / "out"), "--date", t.isoformat()]) == 0
    load_card = json.loads((run / "out" / "result.json").read_text())["card"]["load"]
    assert load_card["acwr_flag"] is None and 0.8 <= load_card["acwr"] <= 1.3 and load_card["ctl"] > 0

    # the courier route the import replaces (no hourly or minute data, workouts without average heart rate):
    # TRIMP 0 for every past day, so the same steady morning showed a false spike
    old = tmp_path / "old"
    shutil.copytree(bf, old, ignore=shutil.ignore_patterns("out", "minutes.csv"))
    (old / "hours.csv").write_text("hour_start,avg,min,max,steps\n")
    w = (old / "workouts.csv").read_text().splitlines()
    (old / "workouts.csv").write_text("\n".join([w[0], *[x.rsplit(",", 1)[0] + "," for x in w[1:]]]) + "\n")
    assert main(["backfill", "--in", str(old), "--out", str(old / "out"), "--date", U.isoformat()]) == 0
    state = json.loads((old / "out" / "result.json").read_text())["state_next"]
    (run / "state.json").write_text(json.dumps(state))
    assert main(["run", "--in", str(run), "--out", str(run / "old"), "--date", t.isoformat()]) == 0
    assert json.loads((run / "old" / "result.json").read_text())["card"]["load"]["acwr_flag"] == "spike"


def test_backfill_seeds_from_workouts_only_where_hours_and_minutes_are_missing(imported, tmp_path):
    bf, _, res = imported
    d = tmp_path / "gap"
    shutil.copytree(bf, d, ignore=shutil.ignore_patterns("out"))
    gap = {(U - timedelta(days=i)).isoformat() for i in range(10, 13)}   # the watch's hourly data lost on 3 days
    for name in ("hours.csv", "minutes.csv"):
        text = (d / name).read_text().splitlines()
        (d / name).write_text("\n".join([text[0], *[x for x in text[1:] if x[:10] not in gap]]) + "\n")
    assert main(["backfill", "--in", str(d), "--out", str(d / "out"), "--date", U.isoformat()]) == 0
    got = {r["date"]: r for r in json.loads((d / "out" / "result.json").read_text())["rows"]}
    full = {r["date"]: r for r in res["rows"]}
    for day in gap:
        assert got[day]["stress"] is None and not got[day]["still_hr"] and got[day]["vig_min"] is None
        workouts = [w for w in read_workouts(d / "workouts.csv") if w.start.date().isoformat() == day]
        assert (got[day]["trimp"] > 0) == any(w.avg_hr is not None for w in dedupe_workouts(workouts))
        assert got[day]["trimp"] < full[day]["trimp"]   # the workouts alone: no walks
    later = (U - timedelta(days=5)).isoformat()
    assert got[later]["stress"] is not None and got[later]["trimp"] == full[later]["trimp"]


# ---------------------------------------------------------------- source rules
def test_heart_rate_comes_from_the_watch_and_another_writer_only_on_days_without_it(tmp_path):
    a, b = U - timedelta(days=1), U

    def build(w):
        w.hr(sx.WATCH, local(a, 10, 5), 60)
        w.hr(sx.WATCH, local(a, 10, 35), 70)
        w.hr(sx.POLAR, local(a, 10, 10), 150)         # a chest strap on a day the watch recorded: dropped
        w.hr(sx.POLAR, local(b, 11, 0), 80)           # the watch off all day: the strap is all there is
        w.hr(sx.POLAR, local(b, 11, 30), 90)
        w.hr(sx.PHONE, local(b, 11, 40, tz=CST), 100)

    out = tmp_path / "out"
    s = import_export(small_export(tmp_path, build), out, U, 2, 1)
    assert lines(out, "hours.csv") == ["2026-11-18T10:00-0600,65.0,60,70,0", "2026-11-19T11:00-0600,90.0,80,100,0"]
    assert s["watch"]["hr_days"] == 2 and s["watch"]["watch_hr_days"] == 1
    assert lines(out, "daily.csv") == ["2026-11-18,,,,,,70", "2026-11-19,,,,,,100"]


def test_steps_take_the_larger_source_each_hour_never_the_sum(tmp_path):
    def build(w):
        w.record(sx.STEPS, sx.WATCH, local(U, 9, 0), local(U, 9, 10), 500, "count")
        w.record(sx.STEPS, sx.WATCH, local(U, 9, 30), local(U, 9, 40), 300, "count")
        w.record(sx.STEPS, sx.PHONE, local(U, 9, 2), local(U, 9, 41), 700, "count")     # watch 800 > phone 700
        w.record(sx.STEPS, sx.WATCH, local(U, 21, 0), local(U, 21, 5), 100, "count")
        w.record(sx.STEPS, sx.PHONE, local(U, 21, 0), local(U, 21, 15), 400, "count")   # phone only, watch charging

    out = tmp_path / "out"
    import_export(small_export(tmp_path, build), out, U, 1, 1)
    assert lines(out, "hours.csv") == ["2026-11-19T09:00-0600,,,,800", "2026-11-19T21:00-0600,,,,400"]
    assert lines(out, "daily.csv") == ["2026-11-19,1200,,,,,"]


def test_sleep_comes_from_the_watch_and_other_sources_only_for_nights_it_missed(tmp_path):
    def build(w):
        # night ending U-1: the watch, plus the phone's sleep schedule (dropped)
        w.sleep(sx.PHONE, local(U - timedelta(days=2), 22, 30), local(U - timedelta(days=1), 7, 0), "B")
        w.sleep(sx.WATCH, local(U - timedelta(days=2), 22, 45), local(U - timedelta(days=1), 2, 0), "C")
        w.sleep(sx.WATCH, local(U - timedelta(days=1), 2, 0), local(U - timedelta(days=1), 6, 50), "D")
        # night ending U: the watch was charging; another sleep app recorded it (its old "Asleep" value)
        w.sleep(sx.AUTOSLEEP, local(U - timedelta(days=1), 23, 0), local(U, 6, 30), "old")
        w.sleep(sx.PHONE, local(U - timedelta(days=1), 22, 50), local(U, 6, 40), "B")
        # before the span: ends before 12:00 on U-2 (the afternoon before the first night is the limit)
        w.sleep(sx.WATCH, local(U - timedelta(days=2), 1, 0), local(U - timedelta(days=2), 11, 0), "C")
        w.sleep(sx.WATCH, local(U - timedelta(days=2), 14, 0), local(U - timedelta(days=2), 14, 30), "C")   # a nap
        w.record(sx.SLEEP, sx.WATCH, local(U, 13, 0), local(U, 13, 20), "HKCategoryValueSleepAnalysisMystery", None)

    out = tmp_path / "out"
    s = import_export(small_export(tmp_path, build), out, U, 2, 2)
    assert s["watch"]["nights"] == 2 and s["watch"]["watch_nights"] == 1
    assert lines(out, "sleep.csv") == ["2026-11-17T14:00-0600,2026-11-17T14:30-0600,C",
                                       "2026-11-17T22:45-0600,2026-11-18T02:00-0600,C",
                                       "2026-11-18T02:00-0600,2026-11-18T06:50-0600,D",
                                       "2026-11-18T22:50-0600,2026-11-19T06:40-0600,B",
                                       "2026-11-18T23:00-0600,2026-11-19T06:30-0600,S"]
    assert s["dropped"] == 1   # the unknown sleep value


def test_hrv_watch_first_and_the_rmssd_finding(tmp_path):
    def build(rmssd):
        def b(w):
            w.hrv(sx.WATCH, local(U, 2, 0, 30), 48.6)
            w.hrv(sx.HRV_APP, local(U, 2, 10), 80)                      # another HRV app, same day: dropped
            w.hrv(sx.HRV_APP, local(U - timedelta(days=1), 3, 0), 61)   # its reading on a day the watch had none
            if rmssd:
                w.hrv(sx.WATCH, local(U, 2, 0, 30), 39.2, rmssd=True)
        return b

    out = tmp_path / "with"
    s = import_export(small_export(tmp_path, build(True), "a.xml"), out, U, 2, 2)
    assert lines(out, "hrv.csv") == ["2026-11-18T03:00-0600,61,S", "2026-11-19T02:00-0600,39,R",
                                     "2026-11-19T02:00-0600,49,S"]
    assert summary_lines(s)[-1] == ("RMSSD: 1 reading in the export (HKQuantityTypeIdentifierHeartRateVariabilityRMSSD), "
                                    "1 in hrv.csv")
    out = tmp_path / "without"
    s = import_export(small_export(tmp_path, build(False), "b.xml"), out, U, 2, 2)
    assert [x.split(",")[2] for x in lines(out, "hrv.csv")] == ["S", "S"]
    assert summary_lines(s)[-1] == "RMSSD: none in the export (hrv.csv has SDNN only)"


def test_daily_values_are_the_watchs_average_and_the_days_highest_heart_rate(tmp_path):
    def build(w):
        w.record(sx.RHR, sx.WATCH, local(U, 6, 55), value=54)
        w.record(sx.RHR, sx.PHONE, local(U, 7, 0), value=70)         # not the watch: dropped (the watch has one)
        w.record(sx.RESP, sx.WATCH, local(U, 2, 0), value=14.25)
        w.record(sx.RESP, sx.WATCH, local(U, 4, 0), value=14.75)
        w.record(sx.RESP, sx.WATCH, local(U, 5, 0), value=80)        # implausible: dropped
        w.record(sx.VO2, sx.WATCH, local(U, 9, 0), value=47.46, unit="mL/min·kg")
        w.record(sx.EXERCISE, sx.WATCH, local(U, 9, 0), local(U, 9, 10), 10, "min")
        w.record(sx.EXERCISE, sx.WATCH, local(U, 18, 0), local(U, 18, 25), 25, "min")
        w.record(sx.EXERCISE, sx.PHONE, local(U, 18, 0), local(U, 18, 25), 25, "min")   # the phone's estimate: dropped
        w.hr(sx.WATCH, local(U, 18, 10), 171)
        w.hr(sx.WATCH, local(U, 18, 20), 300)                        # a glitch: dropped
        w.record(sx.RHR, sx.WATCH, local(U - timedelta(days=200), 7, 0), value=60)   # outside the span

    out = tmp_path / "out"
    s = import_export(small_export(tmp_path, build), out, U, 1, 1)
    assert lines(out, "daily.csv") == ["2026-11-19,,35,54.0,14.5,47.5,171"]
    assert s["dropped"] == 2


def test_workouts_every_source_with_kind_energy_and_heart_rate(tmp_path):
    a = local(U, 7, 0)

    def build(w):
        for k in range(40 * 6):   # the watch's heart rate in the run, every 10 s
            w.hr(sx.WATCH, a + timedelta(seconds=10 * k), 140 + k % 2 * 10)
        w.workout("Running", sx.WATCH, a, a + timedelta(minutes=40), kcal=460, hr_avg=151.4)
        w.workout("Walking", sx.PHONE, a + timedelta(minutes=5), a + timedelta(minutes=15), kcal=40)   # no statistic
        w.workout("TraditionalStrengthTraining", sx.HEVY, local(U, 18, 0), local(U, 18, 50), kcal=310, kj=True)
        w.workout("Cycling", sx.STRAVA, local(U, 12, 0), local(U, 12, 30), kcal=210, legacy=True)
        w.workout("HighIntensityIntervalTraining", sx.WATCH, local(U, 20, 0), local(U, 20, 20))
        w.workout("Running", sx.WATCH, local(U - timedelta(days=3), 7, 0), local(U - timedelta(days=3), 7, 40))

    out = tmp_path / "out"
    import_export(small_export(tmp_path, build), out, U, 2, 1)
    assert lines(out, "workouts.csv") == [
        "2026-11-19T07:00-0600,2026-11-19T07:40-0600,running,460,151",          # the workout's own statistic
        "2026-11-19T07:05-0600,2026-11-19T07:15-0600,walking,40,145",           # the minutes' mean heart rate
        "2026-11-19T12:00-0600,2026-11-19T12:30-0600,cycling,210,",             # old export: totalEnergyBurned
        "2026-11-19T18:00-0600,2026-11-19T18:50-0600,traditionalStrengthTraining,310,",   # kJ -> kcal
        "2026-11-19T20:00-0600,2026-11-19T20:20-0600,highIntensityIntervalTraining,,"]
    assert workout_kind("HKWorkoutActivityTypeOther") == "other" and workout_kind(None) == "other"


@pytest.mark.parametrize("hr_on", ["strength", "other"])
def test_strava_and_hevy_copies_of_one_session_count_once_as_strength(tmp_path, hr_on):
    """He logs each lift in Hevy (traditionalStrengthTraining) and Strava (other), same times; one copy has the
    average heart rate. Both are written; the engine's dedupe_workouts keeps one, and the day has its 50 minutes of
    strength."""
    a, b = local(U, 18, 0), local(U, 18, 50)

    def build(w):
        w.workout("TraditionalStrengthTraining", sx.HEVY, a, b, kcal=310, hr_avg=118 if hr_on == "strength" else None)
        w.workout("Other", sx.STRAVA, a + timedelta(seconds=20), b - timedelta(seconds=10), kcal=300,
                  hr_avg=118 if hr_on == "other" else None)

    out = tmp_path / "out"
    import_export(small_export(tmp_path, build), out, U, 1, 1)
    ws = read_workouts(out / "workouts.csv")
    assert sorted(w.kind for w in ws) == ["other", "traditionalStrengthTraining"]
    kept = dedupe_workouts(ws)
    assert len(kept) == 1 and kept[0].kind == "traditionalStrengthTraining" and kept[0].avg_hr == 118


def test_minutes_inside_workouts_and_hours_reaching_half_of_hrmax(tmp_path):
    """HRmax here: the span's observed one (the highest daily maxima, the top two dropped as outliers)."""
    def build(w):
        for n, top in enumerate((180, 182, 184, 199, 200)):   # observed HRmax 184: the minute line is 92 bpm
            d = U - timedelta(days=n)
            w.hr(sx.WATCH, local(d, 6, 0), top)
        w.hr(sx.WATCH, local(U, 9, 1, 10), 93)       # 09:00 reaches 92: its minutes go
        w.hr(sx.WATCH, local(U, 9, 1, 40), 95)       # two readings in one minute: their mean
        w.hr(sx.WATCH, local(U, 9, 30), 70)
        w.hr(sx.WATCH, local(U, 10, 15), 91)         # 10:00 stays below: none of it
        w.workout("Yoga", sx.WATCH, local(U, 11, 0, 30), local(U, 11, 20))
        w.hr(sx.WATCH, local(U, 11, 10), 80)         # inside a workout: it goes, whatever the hour's max
        w.hr(sx.WATCH, local(U, 11, 40), 79)

    out = tmp_path / "out"
    import_export(small_export(tmp_path, build), out, U, 5, 1)
    got = [x for x in lines(out, "minutes.csv") if x.startswith("2026-11-19T") and not x.startswith("2026-11-19T06")]
    assert got == ["2026-11-19T09:01-0600,94", "2026-11-19T09:30-0600,70", "2026-11-19T11:10-0600,80"]


def test_records_nested_in_a_correlation_are_skipped(tmp_path):
    t = local(U, 8, 0)

    def build(w):
        w.raw(f' <Correlation type="HKCorrelationTypeIdentifierBloodPressure" sourceName="Cuff" '
              f'startDate="{sx.stamp(t)}" endDate="{sx.stamp(t)}">\n'
              f'  <Record type="{sx.HR_TYPE}" sourceName="Cuff" unit="count/min" startDate="{sx.stamp(t)}" '
              f'endDate="{sx.stamp(t)}" value="77"/>\n </Correlation>\n')

    out = tmp_path / "out"
    import_export(small_export(tmp_path, build), out, U, 1, 1)
    assert lines(out, "hours.csv") == []


# ---------------------------------------------------------------- the file: zip or XML, errors, the command
def test_zip_reads_only_export_xml_and_matches_the_bare_file(steady_export, tmp_path):
    z = sx.write_zip(steady_export, tmp_path / "export.zip")   # export_cda.xml in it is not even XML
    a, b = tmp_path / "from_xml", tmp_path / "from_zip"
    import_export(steady_export, a, U, 30, 20)
    import_export(z, b, U, 30, 20)
    for name in ("daily.csv", "sleep.csv", "hrv.csv", "hours.csv", "minutes.csv", "workouts.csv", "vitals.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def cli(*args):
    return subprocess.run([sys.executable, "-m", "recovery_coach", *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True)


def test_command_prints_a_line_per_file_and_the_rmssd_finding(steady_export, tmp_path):
    z = sx.write_zip(steady_export, tmp_path / "export.zip")
    p = cli("import", "--export", z, "--out", tmp_path / "in", "--until", U.isoformat(), "--days", 30,
            "--nights", 20)
    assert p.returncode == 0, p.stderr
    out = p.stdout.splitlines()
    assert out[0].startswith("read ") and "(export of 2026-11-20)" in out[0]
    assert out[1] == "Apple Watch: heart rate on 30 of 30 days, sleep on 20 of 20 nights (other sources only on the rest)"
    for line, name in zip(out[2:8], ("daily.csv", "sleep.csv", "hrv.csv", "hours.csv", "minutes.csv",
                                     "workouts.csv")):
        size = (tmp_path / "in" / name).stat().st_size
        assert line.startswith(name) and " rows " in line and line.endswith(f"{size:,} B"), line
    assert out[2].split()[1:5] == ["30", "rows", (U - timedelta(days=29)).isoformat(), ".."]
    assert out[8] == "vitals.json   {}" and out[9].startswith("RMSSD: ") and len(out) == 10
    assert not (tmp_path / "in" / "state.json").exists()


@pytest.mark.parametrize("case, message", [
    ("missing", "nothing.zip: file not found"),
    ("not_export", "export.xml: not an Apple Health export (it starts with <html>)"),
    ("truncated", "export.xml line 3: unreadable XML (no element found: line 3, column 0): the export is incomplete "
                  "or damaged; export it again"),
    ("zip_without", "export.zip: has no export.xml (not an Apple Health export)"),
    ("bad_until", "--until: '2026-11-31' is not a valid date (YYYY-MM-DD)"),
    ("bad_days", "--days: must be at least 1"),
])
def test_bad_exports_and_arguments_exit_2_with_one_line(tmp_path, case, message):
    path, until, days = tmp_path / "export.xml", "2026-11-19", "180"
    if case == "missing":
        path = tmp_path / "nothing.zip"
    elif case == "not_export":
        path.write_text("<html><body>hello</body></html>")
    elif case == "truncated":
        path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n')
    elif case == "zip_without":
        path = tmp_path / "export.zip"
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("apple_health_export/export_cda.xml", "<ClinicalDocument/>")
    else:
        path.write_text('<HealthData locale="en_US"/>')
        until, days = ("2026-11-31", "180") if case == "bad_until" else ("2026-11-19", "0")
    p = cli("import", "--export", path, "--out", tmp_path / "out", "--until", until, "--days", days)
    assert p.returncode == 2 and p.stderr.strip() == message, p.stderr
    assert not (tmp_path / "out").exists()


def test_large_export_streams_with_flat_memory(tmp_path, monkeypatch):
    """About 200k records: memory doesn't grow with the file (the span, not the file, bounds what is kept), and the
    file is never parsed whole."""
    def forbid(*a, **k):
        raise AssertionError("the export must be streamed, not parsed whole")
    for name in ("parse", "fromstring", "XML"):
        monkeypatch.setattr(ET, name, forbid)
    monkeypatch.setattr(ET.ElementTree, "parse", forbid)

    def export(days: int) -> Path:
        path = tmp_path / f"export{days}.xml"
        with sx.ExportWriter(path) as w:
            sx.steady(w, U - timedelta(days=days - 1), days, hr_every_min=5)
        return path, w.count

    small, n_small = export(25)
    big, n_big = export(320)
    assert n_big >= 200_000 and big.stat().st_size > 100_000_000

    def peak(path: Path) -> int:
        tracemalloc.start()
        try:
            import_export(path, tmp_path / path.stem, U, 20, 20)
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    p_small, p_big = peak(small), peak(big)
    assert p_big < p_small + 1_000_000, (p_small, p_big)   # 13x the records, the same peak
    assert p_big < 0.05 * big.stat().st_size
    t = time.monotonic()
    import_export(big, tmp_path / "timed", U, 20, 20)
    seconds = time.monotonic() - t
    assert seconds < 60, seconds
    print(f"{n_big:,} records, {big.stat().st_size / 1e6:.0f} MB: {seconds:.1f} s, peak {p_big / 1e6:.1f} MB")


def test_watch_source_with_no_break_space_and_no_device():
    """Sleep records from the watch name it "Name's Apple Watch" and carry no device attribute."""
    import xml.etree.ElementTree as ET
    from recovery_coach.export import _is_watch
    el = ET.Element("Record", {"sourceName": "Sam’s Apple Watch"})
    assert _is_watch(el)
    assert not _is_watch(ET.Element("Record", {"sourceName": "Sam’s iPhone"}))
    assert _is_watch(ET.Element("Record", {"sourceName": "Health", "device": "<<HKDevice>, name:Apple Watch>"}))
