"""The courier helper (python3 -m recovery_coach.helper): every command, on synthetic files."""
import json
from datetime import datetime
import subprocess
import sys
from datetime import timezone
from pathlib import Path

from recovery_coach.helper import main
from recovery_coach.inputs import parse_time

from test_cli import gen   # the synthetic 90-day world

ROOT = Path(__file__).resolve().parents[1]


def run(capsys, *args) -> tuple[int, str, str]:
    code = main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


def write(folder: Path, name: str, lines) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------- localize
def test_localize_utc_across_a_dst_night(tmp_path, capsys):
    write(tmp_path, "sleep.csv", ["start,end,code",
                                  "2026-11-01T03:10:00Z,2026-11-01T12:25:00Z,C",       # CDT onset, CST wake
                                  "",
                                  "2026-11-01T06:30:00.250Z,2026-11-01T06:42:59Z,A"])
    write(tmp_path, "hrv.csv", ["time,ms,type", "2026-11-01T08:00:00+00:00,48,S"])
    assert run(capsys, "localize", tmp_path, "America/Chicago")[:2] == (0, "localized\n")
    assert (tmp_path / "sleep.csv").read_text().splitlines() == [
        "start,end,code", "2026-10-31T22:10-0500,2026-11-01T06:25-0600,C",
        "2026-11-01T01:30-0500,2026-11-01T01:42-0500,A"]
    assert (tmp_path / "hrv.csv").read_text().splitlines()[1] == "2026-11-01T02:00-0600,48,S"


def test_localize_restores_a_whole_dst_morning_from_utc(tmp_path, capsys):
    """The morning of 2026-11-01 (clocks fall back overnight), every stamp sent as UTC: localize gives back the
    generator's local files exactly."""
    original, d = tmp_path / "local", tmp_path / "utc"
    gen.write_morning(72, original, first=70)
    d.mkdir()
    for name, n in (("sleep.csv", 2), ("hrv.csv", 1), ("hours.csv", 1), ("minutes.csv", 1), ("workouts.csv", 2)):
        lines = (original / name).read_text().splitlines()
        out = lines[:1]
        for line in lines[1:]:
            cells = line.split(",")
            for c in range(n):
                cells[c] = _utc(cells[c])
            out.append(",".join(cells))
        (d / name).write_text("\n".join(out) + "\n")
    assert "Z" in (d / "hours.csv").read_text()
    assert run(capsys, "localize", d, "America/Chicago")[0] == 0
    for name in ("sleep.csv", "hrv.csv", "hours.csv", "minutes.csv"):
        assert (d / name).read_text() == (original / name).read_text(), name
    # workouts.csv keeps seconds: the same instants and the same local offsets, written to the second
    def stamps(p):
        return [[datetime.fromisoformat(c).isoformat() for c in line.split(",")[:2]] for line in p.read_text().splitlines()[1:]]
    assert stamps(d / "workouts.csv") == stamps(original / "workouts.csv")


def _utc(stamp: str) -> str:
    return parse_time(stamp, "x", None).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_localize_naive_offset_and_fixed_zones(tmp_path, capsys):
    write(tmp_path, "workouts.csv", ["start,end,kind,kcal,avg_hr",
                                     "2026-11-01 01:30:00,2026-11-01 02:30:00,running,300,140",   # fold=0: CDT
                                     "2026-11-19T06:20:09 -0600,2026-11-19T07:00:00-06:00,walking,,"])
    write(tmp_path, "raw_steps.csv", ["start,end,count", "2026-11-19T12:00:00Z,2026-11-19T12:01:00Z,95"])
    write(tmp_path, "raw_hr.csv", ["time,bpm", "2026-11-19T12:00:30Z,88"])
    assert run(capsys, "localize", tmp_path, "America/Chicago")[0] == 0
    assert (tmp_path / "workouts.csv").read_text().splitlines()[1:] == [
        "2026-11-01T01:30:00-0500,2026-11-01T02:30:00-0600,running,300,140",
        "2026-11-19T06:20:09-0600,2026-11-19T07:00:00-0600,walking,,"]
    assert (tmp_path / "raw_steps.csv").read_text().splitlines()[1] == "2026-11-19T06:00-0600,2026-11-19T06:01-0600,95"
    assert (tmp_path / "raw_hr.csv").read_text().splitlines()[1] == "2026-11-19T06:00-0600,88"
    india = tmp_path / "india"
    write(india, "minutes.csv", ["time,bpm", "2026-12-20T23:35:00Z,61", "2026-12-21 05:10:00,64"])
    assert run(capsys, "localize", india, "+05:30")[0] == 0
    assert (india / "minutes.csv").read_text().splitlines()[1:] == ["2026-12-21T05:05+0530,61",
                                                                     "2026-12-21T05:10+0530,64"]


def test_localize_leaves_other_cells_and_bad_stamps_for_the_engine(tmp_path, capsys):
    write(tmp_path, "hours.csv", ["hour_start,avg,min,max,steps", "2026-13-01T00:00:00Z,61.5,50,70,12",
                                  "not a time,,,,"])
    assert run(capsys, "localize", tmp_path, "UTC")[0] == 0
    assert (tmp_path / "hours.csv").read_text().splitlines()[1:] == ["2026-13-01T00:00:00Z,61.5,50,70,12",
                                                                     "not a time,,,,"]
    code, _, err = run(capsys, "localize", tmp_path, "Mars/Olympus")
    assert code == 2 and "Mars/Olympus" in err


# ---------------------------------------------------------------- windows
def test_windows_merges_active_hours_and_workouts(tmp_path, capsys):
    write(tmp_path, "hours.csv", ["hour_start,avg,min,max,steps",
                                  "2026-11-18T07:00-0600,70,60,90,100",
                                  "2026-11-18T08:00-0600,80,60,100,900",      # 100 >= 95: active
                                  "2026-11-18T09:00-0600,85,60,120,800",      # adjacent: merged
                                  "2026-11-18T13:00-0600,,,,0",
                                  "2026-11-18T18:00-0600,90,70,140,500"])     # inside the workout
    write(tmp_path, "workouts.csv", ["start,end,kind,kcal,avg_hr",
                                     "2026-11-18T17:40-0600,2026-11-18T18:30-0600,running,400,150",
                                     "2026-11-19T06:00-0600,2026-11-19T06:20-0600,walking,,"])
    code, out, _ = run(capsys, "windows", tmp_path, 190)
    lines = out.splitlines()
    assert code == 0 and json.loads(lines[0]) == [
        ["2026-11-18T08:00-0600", "2026-11-18T10:00-0600"], ["2026-11-18T17:40-0600", "2026-11-18T19:00-0600"],
        ["2026-11-19T06:00-0600", "2026-11-19T06:20-0600"]]
    assert lines[1] == "220 minutes in 3 windows"


def test_windows_with_nothing_to_send(tmp_path, capsys):
    write(tmp_path, "hours.csv", ["hour_start,avg,min,max,steps"])
    write(tmp_path, "workouts.csv", ["start,end,kind,kcal,avg_hr"])
    assert run(capsys, "windows", tmp_path, 190)[1].splitlines() == ["[]", "0 minutes in 0 windows"]


# ---------------------------------------------------------------- aggregate
def test_aggregate_rebuilds_hours_and_minutes_from_raw_samples(tmp_path, capsys):
    """Raw heart-rate and step samples for a synthetic morning give exactly the hours.csv and minutes.csv the
    generator writes from the same world (its minute threshold, 95 bpm, is 50 % of HRmax 190)."""
    n, w = 90, gen.world()
    expected = tmp_path / "expected"
    gen.write_morning(n, expected)
    now, y0 = w.days[n].now, gen.at(gen.day_date(n - 1), 0)
    d = tmp_path / "raw"
    write(d, "raw_hr.csv", ["time,bpm", *(f"{gen.iso(t)},{b}" for t, b in w.readings if y0 <= t < now)])
    write(d, "raw_steps.csv", ["start,end,count", *(
        f"{gen.iso(w.time(i))},{gen.iso(w.time(i + 1))},{w.steps[i]}"
        for i in range(w.idx(y0), w.idx(now)) if w.steps[i])])
    (d / "workouts.csv").write_text((expected / "workouts.csv").read_text())
    code, out, _ = run(capsys, "aggregate", d, 2 * gen.MINUTES_HR)
    assert code == 0 and out.startswith("hours.csv ")
    assert (d / "hours.csv").read_text() == (expected / "hours.csv").read_text()
    assert (d / "minutes.csv").read_text() == (expected / "minutes.csv").read_text()


def test_aggregate_averages_a_minute_and_keeps_step_only_hours(tmp_path, capsys):
    write(tmp_path, "raw_hr.csv", ["time,bpm", "2026-11-18T08:10-0600,100", "2026-11-18T08:10-0600,103",
                                   "2026-11-18T08:20-0600,90", "2026-11-18T09:05-0600,"])
    write(tmp_path, "raw_steps.csv", ["start,end,count", "2026-11-18T08:10-0600,2026-11-18T08:20-0600,500",
                                      "2026-11-18T10:00-0600,2026-11-18T10:30-0600,40"])
    write(tmp_path, "workouts.csv", ["start,end,kind,kcal,avg_hr"])
    assert run(capsys, "aggregate", tmp_path, 190)[0] == 0
    assert (tmp_path / "hours.csv").read_text().splitlines() == [
        "hour_start,avg,min,max,steps", "2026-11-18T08:00-0600,97.7,90,103,500", "2026-11-18T10:00-0600,,,,40"]
    assert (tmp_path / "minutes.csv").read_text().splitlines() == [
        "time,bpm", "2026-11-18T08:10-0600,102", "2026-11-18T08:20-0600,90"]


# ---------------------------------------------------------------- first-day, emit, week, backfill-weeks, sizes
def test_first_day(tmp_path, capsys):
    rows = [{"date": "2026-11-15", "trimp": 90.0, "ctl": 80.0, "atl": 85.0}, {"date": "2026-11-16", "recovery": 60}]
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"settings": {"dob": "1996-04-12", "sex": "male"}, "rows": rows}))
    assert run(capsys, "first-day", p, "2026-11-17")[:2] == (0, "2026-11-16\n")   # daily: yesterday
    assert run(capsys, "first-day", p, "2026-11-18")[1] == "2026-11-16\n"         # one morning skipped
    assert run(capsys, "first-day", p, "2026-11-25")[1] == "2026-11-22\n"         # never before T-3
    code, _, err = run(capsys, "first-day", p, "2026-11-31")
    assert code == 2 and "2026-11-31" in err


def test_emit(tmp_path, capsys):
    p = tmp_path / "result.json"
    p.write_text(json.dumps({"rows": [{"date": "2026-11-17", "strain": 9.5}, {"date": "2026-11-18"}],
                             "state_next": {"cols": {"date": ["2026-11-18"]}, "crc": 1}}, indent=2))
    assert run(capsys, "emit", p, "rows", "2026-11-18")[1] == '[{"date":"2026-11-18"}]\n'
    assert run(capsys, "emit", p, "state_next")[1] == '{"cols":{"date":["2026-11-18"]},"crc":1}\n'
    code, _, err = run(capsys, "emit", p, "nope")
    assert code == 2 and "nope" in err


def test_week_and_backfill_weeks(tmp_path, capsys):
    weekly = tmp_path / "weekly.json"
    weekly.write_text(json.dumps({
        "card": {"body_age": {"age": 30.59, "body_age": 27.3}, "journal": [{"question": "alcohol", "diff": -7}]},
        "week": {"date": "2026-11-15", "body_age": 27.3, "pace": 0.8, "items": ["rhr", "activity"], "delta": -3.29}}))
    assert json.loads(run(capsys, "week", weekly)[1]) == {
        "date": "2026-11-15", "body_age": 27.3, "age": 30.59, "pace": 0.8, "items": ["rhr", "activity"],
        "journal": [{"question": "alcohol", "diff": -7}]}
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"weeks": [{"date": "2026-10-04", "delta": -2.5, "items": ["rhr"]},
                                            {"date": "2026-10-11", "delta": None, "items": []}]}))
    out = json.loads(run(capsys, "backfill-weeks", result, "1996-04-12")[1])
    assert out == [{"date": "2026-10-04", "body_age": 27.98, "age": 30.48, "pace": None, "items": ["rhr"],
                    "journal": []},
                   {"date": "2026-10-11", "body_age": None, "age": 30.5, "pace": None, "items": [], "journal": []}]


def test_sizes(tmp_path, capsys):
    (tmp_path / "a.csv").write_text("x" * 1200)
    (tmp_path / "b.json").write_text("{}")
    assert run(capsys, "sizes", tmp_path)[1].splitlines() == [
        "a.csv              1,200 B", "b.json                 2 B", "total              1,202 B"]


def test_usage_errors(capsys):
    assert run(capsys, "nope")[0] == 2
    assert run(capsys, "windows", "/nonexistent/folder", "190")[0] == 2


def test_runs_as_a_module(tmp_path):
    (tmp_path / "x.txt").write_text("hello")
    p = subprocess.run([sys.executable, "-m", "recovery_coach.helper", "sizes", str(tmp_path)], cwd=ROOT,
                       capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.splitlines()[-1].endswith("5 B")


# ---------- post-workout helpers ----------

def _state_with_log(tmp_path, entries):
    from recovery_coach.inputs import Settings
    from datetime import date as _date
    from recovery_coach.state import compact_state
    from recovery_coach.workout_log import LogEntry
    log = [LogEntry(id=i, date=i[:10], kind="running", minutes=45.0, provisional=p) for i, p in entries]
    doc = compact_state(Settings(dob=_date(1996, 4, 12), sex="male"), [], None, 60, workouts=log)
    p = tmp_path / "state.json"
    p.write_text(json.dumps(doc))
    return p


def _workouts(tmp_path, lines):
    d = tmp_path / "in"
    d.mkdir()
    (d / "workouts.csv").write_text("start,end,kind,kcal,avg_hr\n" + "".join(l + "\n" for l in lines))
    return d


def test_new_workouts_skips_logged_and_dupes(tmp_path, capsys):
    st = _state_with_log(tmp_path, [("2026-11-20T07:09-0600", False)])
    d = _workouts(tmp_path, ["2026-11-20T07:09-0600,2026-11-20T07:57-0600,traditionalStrengthTraining,239,",
                             "2026-11-21T08:23-0600,2026-11-21T09:14-0600,running,500,",
                             "2026-11-21T08:23-0600,2026-11-21T09:14-0600,other,498,"])
    code, out, _ = run(capsys, "new-workouts", str(st), str(d))
    assert code == 0
    assert json.loads(out.splitlines()[0]) == [{"id": "2026-11-21T08:23-0600", "kind": "running",
                                                "from": "2026-11-21T08:23:00-0600", "to": "2026-11-21T09:17:00-0600"}]


def test_new_workouts_includes_provisional(tmp_path, capsys):
    st = _state_with_log(tmp_path, [("2026-11-21T08:23-0600", True)])
    d = _workouts(tmp_path, ["2026-11-21T08:23-0600,2026-11-21T09:14-0600,running,500,"])
    _, out, _ = run(capsys, "new-workouts", str(st), str(d))
    assert [w["id"] for w in json.loads(out.splitlines()[0])] == ["2026-11-21T08:23-0600"]


def test_localize_new_files(tmp_path, capsys):
    d = tmp_path / "in"
    d.mkdir()
    (d / "wk_hr.csv").write_text("time,bpm\n2026-11-21T14:23:39Z,150\n")
    (d / "night_hr.csv").write_text("time,bpm\n2026-11-21T08:00:00Z,52\n")
    (d / "wk_series.csv").write_text("time,speed,power,dist\n2026-11-21T14:23:39Z,2.6,231,0\n")
    (d / "wk_stats.json").write_text(json.dumps({"workouts": [{
        "start": "2026-11-21T14:23:39Z", "end": "2026-11-21T15:14:11Z", "kind": "running",
        "laps": [{"start": "2026-11-21T14:23:39Z", "end": "2026-11-21T14:30:00Z", "dist": 1000}]}]}))
    assert run(capsys, "localize", str(d), "America/Chicago")[0] == 0
    assert (d / "wk_hr.csv").read_text().splitlines()[1].startswith("2026-11-21T08:23:39-0600")
    assert (d / "night_hr.csv").read_text().splitlines()[1].startswith("2026-11-21T02:00-0600")
    assert (d / "wk_series.csv").read_text().splitlines()[1].startswith("2026-11-21T08:23:39-0600")
    w = json.loads((d / "wk_stats.json").read_text())["workouts"][0]
    assert w["start"] == "2026-11-21T08:23:39-0600" and w["end"] == "2026-11-21T09:14:11-0600"
    assert w["laps"][0]["start"] == "2026-11-21T08:23:39-0600"


def test_emit_workout_doc_under_60kb(tmp_path, capsys):
    trace = [[i * 5, 150.0 + i % 7] for i in range(6000)]
    res = {"workouts": [{"id": "2026-11-21T08:23-0600", "hr": {"trace": trace}, "run": {"pace_trace": trace}}]}
    p = tmp_path / "result.json"
    p.write_text(json.dumps(res))
    code, out, _ = run(capsys, "workout-doc", str(p), "2026-11-21T08:23-0600")
    doc = json.loads(out)
    assert code == 0 and len(out.encode()) <= 60_000 and doc["id"] == "2026-11-21T08:23-0600"
    assert 0 < len(doc["hr"]["trace"]) < 6000
    assert run(capsys, "workout-doc", str(p), "nope")[0] == 2


def test_series_merges_raw_samples_with_cumulative_distance(tmp_path, capsys):
    d = _workouts(tmp_path, ["2026-11-21T08:00:00-0600,2026-11-21T08:10:00-0600,running,100,"])
    write(d, "raw_speed.csv", ["time,value", "2026-11-21T08:00:10-0600,2.6", "2026-11-21T08:00:05-0600,2.5",
                               "2026-11-21T07:50:00-0600,1.2"])
    write(d, "raw_power.csv", ["time,value", "2026-11-21T08:00:05-0600,180"])
    write(d, "raw_dist.csv", ["start,end,m", "2026-11-21T08:00:05-0600,2026-11-21T08:00:10-0600,12",
                              "2026-11-21T08:00:00-0600,2026-11-21T08:05:00-0600,900",   # another source's long copy
                              "2026-11-21T08:00:00-0600,2026-11-21T08:00:05-0600,10"])
    code, out, _ = run(capsys, "series", str(d))
    assert code == 0
    assert (d / "wk_series.csv").read_text().splitlines() == [
        "time,speed,power,dist", "2026-11-21T07:50:00-0600,1.2,,", "2026-11-21T08:00:05-0600,2.5,180,10.0",
        "2026-11-21T08:00:10-0600,2.6,,22.0"]


def test_series_without_raw_files_writes_a_header_only_file(tmp_path, capsys):
    d = _workouts(tmp_path, [])
    assert run(capsys, "series", str(d))[0] == 0
    assert (d / "wk_series.csv").read_text() == "time,speed,power,dist\n"


# ---------------------------------------------------------------- rebuild-age
from datetime import date as _date, timedelta as _td   # noqa: E402

import pytest   # noqa: E402

from recovery_coach.export import import_export   # noqa: E402
from recovery_coach.inputs import Settings, read_state, read_state_long, row_from_dict   # noqa: E402
from recovery_coach.state import compact_state, long_state   # noqa: E402
from test_export import SETTINGS as EXPORT_SETTINGS, U, sx   # noqa: E402

SUNDAY = _date(2026, 11, 15)


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory):
    work = tmp_path_factory.mktemp("rebuild")
    xml = work / "export.xml"
    with sx.ExportWriter(xml) as w:
        sx.steady(w, U - _td(days=69), 71)
    imp = work / "imp"
    import_export(xml, imp, U, 70, 70)
    (imp / "body.csv").write_text(f"date,lbm_pct\n{U - _td(days=5)},77.2\n")
    (imp / "state.json").write_text(json.dumps({"settings": EXPORT_SETTINGS, "rows": [], "weeks": []}))
    assert subprocess.run([sys.executable, "-m", "recovery_coach", "backfill", "--in", str(imp), "--out",
                           str(work / "bf"), "--date", U.isoformat()], cwd=ROOT).returncode == 0
    rows = [row_from_dict(r) for r in json.loads((work / "bf" / "result.json").read_text())["rows"]]
    live = U - _td(days=2)
    old = {r.date for r in rows[:-60]}
    for r in rows:                                  # the stored history: written before the new columns existed
        r.z13_min = r.z45_min = r.lbm_pct = None
        if r.date in old:
            r.sleep_hr = None                       # main kept sleeping HR for 60 days only
        if r.date == live.isoformat():
            r.z13_min = 99.0                        # a value a live morning stored: must survive
    s = Settings(dob=_date(1990, 6, 15), sex="male")
    (work / "daily.json").write_text(json.dumps(compact_state(s, rows, None, 60)))
    (work / "long.json").write_text(json.dumps(long_state(rows, [])))
    out = work / "out"
    code = main(["rebuild-age", str(imp), str(work / "daily.json"), str(work / "long.json"), str(out),
                 SUNDAY.isoformat()])
    return code, out, live


def test_rebuild_age_keeps_live_values_and_fills_the_rest(rebuilt):
    code, out, live = rebuilt
    assert code == 0
    daily = {r.date: r for r in read_state(out / "state_daily.json").rows}
    assert daily[live.isoformat()].z13_min == 99.0                     # kept
    assert daily[live.isoformat()].z45_min is not None                 # filled
    other = (live - _td(days=1)).isoformat()
    assert daily[other].z13_min is not None and daily[other].z45_min is not None
    assert daily[(U - _td(days=5)).isoformat()].lbm_pct == 77.2
    long = read_state_long(out / "state_long.json")
    assert {r.date for r in long.rows} >= set(daily)
    oldest = min(long.rows, key=lambda r: r.date)
    assert oldest.sleep_hr is not None                                  # sleeping HR filled beyond 60 days


def test_rebuild_age_days_patch_lists_only_filled_values(rebuilt):
    _, out, live = rebuilt
    days = {d["date"]: d for d in json.loads((out / "days.json").read_text())}
    assert "z13_min" not in days[live.isoformat()] and "z45_min" in days[live.isoformat()]
    assert days[(U - _td(days=5)).isoformat()]["lbm_pct"] == 77.2


def test_rebuild_age_weeks_end_on_the_sunday_with_the_new_model(rebuilt):
    _, out, _ = rebuilt
    weeks = json.loads((out / "weeks.json").read_text())
    assert 1 <= len(weeks) <= 30 and weeks[-1]["date"] == SUNDAY.isoformat()
    assert all(_date.fromisoformat(w["date"]).weekday() == 6 and w["items"] for w in weeks)
    assert all(set(w) == {"date", "body_age", "age", "pace", "items"} for w in weeks)   # journal left as stored
    long_weeks = read_state_long(out / "state_long.json").weeks
    assert [w.date.isoformat() for w in long_weeks] == [w["date"] for w in weeks]
    assert long_weeks[-1].delta == pytest.approx(weeks[-1]["body_age"] - weeks[-1]["age"], abs=0.02)
