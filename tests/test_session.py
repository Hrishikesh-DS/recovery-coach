"""Scoring workouts: the `workout` command and the morning run's per-workout step (synthetic inputs)."""
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from recovery_coach.cli import main
from recovery_coach.inputs import REQUIRED, load_inputs
from recovery_coach.pipeline import run_day, run_workouts
from recovery_coach.session import TRACE_MAX

CST = timezone(timedelta(hours=-6))
FIX = Path(__file__).parent / "fixtures" / "synthetic_90d" / "day90"
SETTINGS = {"dob": "1996-04-12", "sex": "male", "hrmax": 188}
RUN_START = datetime(2026, 11, 21, 8, 0, tzinfo=CST)


def ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S%z")


def folder(tmp_path, workouts, stats, hr, series=(), log=None, efforts=None, rows=None):
    d = tmp_path / "in"
    d.mkdir(exist_ok=True)
    state = {"settings": SETTINGS, "rows": rows or [{"date": "2026-11-20", "rhr": 60, "ctl": 120, "atl": 130,
                                                       "vo2": 45}]}
    if log is not None:
        from recovery_coach.inputs import state_crc
        from recovery_coach.state import compact_state
        from recovery_coach.inputs import read_state
        p = d / "state.json"
        p.write_text(json.dumps(state))
        st = read_state(p)
        state = compact_state(st.settings, st.rows, None, 60, workouts=log)
    (d / "state.json").write_text(json.dumps(state))
    (d / "workouts.csv").write_text("start,end,kind,kcal,avg_hr\n" + "".join(
        f"{ts(s)},{ts(e)},{k},300,\n" for s, e, k in workouts))
    (d / "wk_stats.json").write_text(json.dumps({"workouts": stats}))
    (d / "wk_hr.csv").write_text("time,bpm\n" + "".join(f"{ts(t)},{b}\n" for t, b in hr))
    (d / "wk_series.csv").write_text("time,speed,power,dist\n" + "".join(
        f"{ts(t)},{sp},{pw},{di}\n" for t, sp, pw, di in series))
    if efforts is not None:
        (d / "efforts.json").write_text(json.dumps({"answers": efforts}))
    return d


def hr_trace(start, minutes, bpm=160, after_s=180, step=5, end_drop=30):
    out = [(start + timedelta(seconds=s), bpm) for s in range(0, minutes * 60, step)]
    end = start + timedelta(minutes=minutes)
    out += [(end + timedelta(seconds=s), bpm - end_drop * min(s, 120) / 120) for s in range(0, after_s + 1, step)]
    return out


def run_stats(start, minutes, indoor=False, dist=8000):
    end = start + timedelta(minutes=minutes)
    return {"start": ts(start), "end": ts(end), "kind": "running", "indoor": indoor, "dist": dist, "avg_hr": 160,
            "avg_speed": dist / (minutes * 60), "avg_power": 240, "stride": 1.05, "vo_cm": 8.5, "gct_ms": 250,
            "effort_est": 7, "cardio_recovery": 26}


def run_series(start, minutes, dist=8000):
    v = dist / (minutes * 60)
    return [(start + timedelta(seconds=s), round(v, 3), 240, round(v * s, 1)) for s in range(0, minutes * 60, 5)]


def score(d, today=date(2026, 11, 21)):
    return run_workouts(load_inputs(d, REQUIRED["workout"]), today)


def test_duplicate_logs_scored_once(tmp_path):
    s = datetime(2026, 11, 20, 7, 9, 50, tzinfo=CST)
    e = s + timedelta(minutes=47)
    d = folder(tmp_path, [(s, e, "traditionalStrengthTraining"), (s - timedelta(seconds=1), e, "other")],
               [{"start": ts(s), "end": ts(e), "kind": "traditionalStrengthTraining", "effort": 5}],
               hr_trace(s, 47, bpm=120, end_drop=10))
    res = score(d)
    assert len(res["workouts"]) == 1 and res["workouts"][0]["kind"] == "traditionalStrengthTraining"
    assert res["workouts"][0]["load"]["effort"] == 5 and res["workouts"][0]["load"]["srpe"] == 5 * 47


def test_provisional_rescored_next_run(tmp_path):
    hr = hr_trace(RUN_START, 50)
    end = RUN_START + timedelta(minutes=50)
    early = [x for x in hr if x[0] <= end + timedelta(seconds=60)]
    d = folder(tmp_path, [(RUN_START, end, "running")], [run_stats(RUN_START, 50)], early,
               run_series(RUN_START, 50))
    first = score(d)
    w = first["workouts"][0]
    assert w["provisional"] is True and w["recovery"]["hrr120"] is None
    from recovery_coach.inputs import read_state
    (tmp_path / "s.json").write_text(json.dumps(first["state_next"]))
    log = read_state(tmp_path / "s.json").workouts
    assert log[0].provisional is True
    d2 = folder(tmp_path / "b", [(RUN_START, end, "running")], [run_stats(RUN_START, 50)], hr,
                run_series(RUN_START, 50), log=log) if (tmp_path / "b").mkdir() is None else None
    again = score(d2)
    assert len(again["workouts"]) == 1 and again["workouts"][0]["provisional"] is False
    assert round(again["workouts"][0]["recovery"]["hrr120"]) == 30


def test_first_workout_without_history(tmp_path):
    end = RUN_START + timedelta(minutes=50)
    d = folder(tmp_path, [(RUN_START, end, "running")], [run_stats(RUN_START, 50)], hr_trace(RUN_START, 50),
               run_series(RUN_START, 50))
    w = score(d)["workouts"][0]
    assert w["load"]["level"] is None and w["load"]["z_typical"] is None
    assert "no history yet" in w["notes"]
    assert w["effect"]["te"] > 0 and w["run"]["evo2"] is not None and w["run"]["best"]["5k"] > 0


def test_indoor_run_skips_pace_fitness(tmp_path):
    end = RUN_START + timedelta(minutes=40)
    d = folder(tmp_path, [(RUN_START, end, "running")], [run_stats(RUN_START, 40, indoor=True, dist=3300)],
               hr_trace(RUN_START, 40), run_series(RUN_START, 40, dist=3300))
    w = score(d)["workouts"][0]
    assert w["indoor"] is True and w["run"]["evo2"] is None and w["run"]["best"] == {}
    assert w["run"]["ef"] is None and w["effect"]["te"] > 0 and w["recovery"]["hrr60"] is not None
    assert "indoor: pace-based numbers skipped" in w["notes"]


def test_effort_answer_applied_to_logged_workout(tmp_path):
    from recovery_coach.workout_log import LogEntry
    old = LogEntry(id="2026-11-20T07:09-0600", date="2026-11-20", kind="traditionalStrengthTraining", minutes=47.0)
    d = folder(tmp_path, [], [], [], log=[old], efforts={"2026-11-20T07:09:50-06:00": 6})
    res = score(d)
    assert res["workouts"] == []
    assert res["updates"] == [{"id": "2026-11-20T07:09-0600", "effort": 6.0, "effort_src": "you", "srpe": 282.0}]
    assert res["state_next"]["workouts"][0]["effort"] == 6


def test_trace_point_cap(tmp_path):
    end = RUN_START + timedelta(minutes=180)
    d = folder(tmp_path, [(RUN_START, end, "running")], [run_stats(RUN_START, 180, dist=27000)],
               hr_trace(RUN_START, 180, bpm=150), run_series(RUN_START, 180, dist=27000))
    w = score(d)["workouts"][0]
    assert 0 < len(w["hr"]["trace"]) <= TRACE_MAX and len(w["run"]["pace_trace"]) <= TRACE_MAX


def test_cli_workout_command_writes_files(tmp_path):
    end = RUN_START + timedelta(minutes=50)
    d = folder(tmp_path, [(RUN_START, end, "running")], [run_stats(RUN_START, 50)], hr_trace(RUN_START, 50),
               run_series(RUN_START, 50))
    out = tmp_path / "out"
    assert main(["workout", "--in", str(d), "--out", str(out)]) == 0
    res = json.loads((out / "result.json").read_text())
    assert len(res["workouts"]) == 1 and res["card"]["id"] == res["workouts"][0]["id"]
    assert res["state_next"]["workouts"][0]["id"] == "2026-11-21T08:00-0600"


def test_run_without_wk_files_unchanged(tmp_path):
    d = tmp_path / "day90"
    shutil.copytree(FIX, d)
    res = run_day(load_inputs(d, REQUIRED["run"]))
    assert res["workouts"] == [] and res["workout_updates"] == []
    assert "workouts" not in res["state_next"]
    y = res["rows"][1]
    assert "wload" not in y and "mono" not in y


def test_run_scores_yesterdays_workout_and_adds_training(tmp_path):
    d = tmp_path / "day90"
    shutil.copytree(FIX, d)
    inp = load_inputs(d, REQUIRED["run"])
    t = inp.state.rows[-1].date
    y0 = datetime.fromisoformat(t + "T07:00:00-06:00")
    end = y0 + timedelta(minutes=45)
    (d / "workouts.csv").write_text((d / "workouts.csv").read_text() + f"{ts(y0)},{ts(end)},running,400,\n")
    (d / "wk_stats.json").write_text(json.dumps({"workouts": [run_stats(y0, 45)]}))
    (d / "wk_hr.csv").write_text("time,bpm\n" + "".join(f"{ts(a)},{b}\n" for a, b in hr_trace(y0, 45)))
    (d / "wk_series.csv").write_text("time,speed,power,dist\n" + "".join(
        f"{ts(a)},{sp},{pw},{di}\n" for a, sp, pw, di in run_series(y0, 45)))
    res = run_day(load_inputs(d, REQUIRED["run"]))
    ran = [w for w in res["workouts"] if w["kind"] == "running"]
    assert len(ran) == 1 and ran[0]["effect"]["te"] is not None
    shown = [w for w in res["card"]["strain"]["workouts"] if w.get("id") == ran[0]["id"]]
    assert shown and shown[0]["te"] is not None and shown[0]["rec_h"] is not None
    tr = res["card"]["training"]
    assert set(tr) >= {"form_pct", "ramp", "easy_strain", "monotony", "polar_band", "garmin_band", "pi7", "status",
                       "hrv_status", "recovery_index"}
    y = next(r for r in res["rows"] if r["date"] == y0.date().isoformat())
    assert y["wload"] > 0 and "mono" in y
    assert "running" in [e["kind"] for e in res["state_next"]["workouts"]]
