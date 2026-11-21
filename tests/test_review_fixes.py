"""Findings from the final whole-branch review, each reproduced before its fix (synthetic data)."""
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from recovery_coach.effect import training_effect
from recovery_coach.helper import main as helper_main
from recovery_coach.inputs import REQUIRED, Settings, load_inputs, read_state
from recovery_coach.pipeline import run_workouts
from recovery_coach.running import effective_vo2
from recovery_coach.state import compact_state
from recovery_coach.workout_log import LogEntry, entry_to_dict

from test_session import folder, hr_trace, run_series, run_stats, ts

CST = timezone(timedelta(hours=-6))
IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 11, 21, 8, 0, 20, tzinfo=CST)


def _score(d, today=date(2026, 11, 21)):
    return run_workouts(load_inputs(d, REQUIRED["workout"]), today)


def _key(w):
    return (w["effect"]["te"], w["effect"]["rec_h"], w["recovery"]["hrr60"], w["recovery"]["hrr120"],
            w["load"]["edwards"], w["run"]["evo2"] if w["run"] else None)


def test_localize_keeps_seconds_and_scores_identically(tmp_path, capsys):
    end = START + timedelta(minutes=50, seconds=30)            # an end second the minute stamp would cut
    hr = [(START + timedelta(seconds=s), 150 + (s % 20) / 4) for s in range(0, 3030, 5)]
    hr += [(end + timedelta(seconds=s), 155 - 25 * min(s, 120) / 120) for s in range(0, 181, 5)]
    d = folder(tmp_path, [(START, end, "running")], [run_stats(START, 50)], hr, run_series(START, 50))
    raw = _score(d)["workouts"][0]
    loc = tmp_path / "loc"
    shutil.copytree(d, loc)
    assert helper_main(["localize", str(loc), "America/Chicago"]) == 0
    assert _key(_score(loc)["workouts"][0]) == _key(raw)
    assert (loc / "wk_hr.csv").read_text().splitlines()[1].startswith("2026-11-21T08:00:20-0600")


def test_pause_rule_has_no_cliff_between_10_and_15_seconds():
    t0 = START
    end = t0 + timedelta(minutes=50)

    def te(step):
        hr = [(t0 + timedelta(seconds=s), 150) for s in range(0, 3000, step)]
        return training_effect(hr, t0, end, 55, 185, 50, [0, 0, 0, 0, 0])
    base = te(5)
    for step in (11, 12, 15):
        r = te(step)
        assert abs(r["te"] - base["te"]) < 0.15 and abs(r["rec_h"] - base["rec_h"]) < 2, step


def test_running_uses_moving_duration(tmp_path):
    end = START + timedelta(minutes=50)
    st = run_stats(START, 50, dist=8000)
    st["duration"] = 2820                                       # 3 minutes paused
    d = folder(tmp_path, [(START, end, "running")], [st], hr_trace(START, 50), run_series(START, 50, dist=8000))
    res = _score(d)
    w = res["workouts"][0]
    assert w["run"]["secs"] == 2820 and res["state_next"]["workouts"][0]["secs"] == 2820
    assert w["run"]["evo2"] == round(effective_vo2(8000, 2820, w["hr"]["avg"] and st["avg_hr"], 188), 1)


def test_logged_by_instant_not_by_string(tmp_path):
    # the other app's copy (18:00:40) was scored first; the watch's own log (17:59:55) arrives later
    other = LogEntry(id="2026-11-20T18:00-0600", date="2026-11-20", kind="other", minutes=46.0, edwards=90.0,
                     banister=60.0)
    s = datetime(2026, 11, 20, 17, 59, 55, tzinfo=CST)
    e = s + timedelta(minutes=46)
    d = folder(tmp_path, [(s, e, "traditionalStrengthTraining")],
               [{"start": ts(s), "end": ts(e), "kind": "traditionalStrengthTraining"}], hr_trace(s, 46, bpm=120),
               log=[other])
    res = _score(d)
    ids = [w["id"] for w in res["state_next"]["workouts"]]
    assert ids == ["2026-11-20T17:59-0600"] and res["state_next"]["workouts"][0]["kind"] == "traditionalStrengthTraining"
    # the same instant written on another clock is the same workout
    run = LogEntry(id="2026-11-21T19:30+0530", date="2026-11-21", kind="running", minutes=50.0, edwards=150.0)
    s2 = datetime(2026, 11, 21, 8, 0, 10, tzinfo=CST)
    d2 = folder(tmp_path / "b", [(s2, s2 + timedelta(minutes=50), "running")], [], hr_trace(s2, 50), log=[run]) \
        if (tmp_path / "b").mkdir() is None else None
    assert _score(d2)["workouts"] == []


def test_effort_answer_matched_by_instant(tmp_path):
    old = LogEntry(id="2026-11-20T07:09-0600", date="2026-11-20", kind="traditionalStrengthTraining", minutes=47.0)
    d = folder(tmp_path, [], [], [], log=[old], efforts={"2026-11-20T13:09:50Z": 6})
    assert _score(d)["updates"][0]["id"] == "2026-11-20T07:09-0600"


def test_log_drops_unread_fields_and_old_card_fields():
    e = LogEntry(id="2026-11-01T08:00-0600", date="2026-11-01", kind="running", minutes=50.0, edwards=150.0,
                 banister=88.0, hrss=60.0, te=3.1, te_an=0.4, rec_h=25.0, hrr60=24.0, hrr120=40.0, ef=1.2, dec=3.0,
                 evo2=41.0, dist=8000.0, secs=3000.0, avg_hr=150.0, effort=7.0, srpe=350.0, split=[30.0, 18.0, 2.0],
                 best={"1k": 300.0}, power=240.0, azm=80.0, benefit="Tempo")
    old = entry_to_dict(e, keep_card=False)
    assert not {"hrss", "hrr120", "dec", "effort", "te", "te_an", "rec_h", "hrr60", "benefit"} & set(old)
    assert len(json.dumps(old, separators=(",", ":"))) < 260
    doc = compact_state(Settings(dob=date(1996, 4, 12), sex="male"), [], None, 60,
                        workouts=[e, LogEntry(id="2026-11-21T08:00-0600", date="2026-11-21", kind="running",
                                              minutes=50.0, te=3.0)])
    assert "te" not in doc["workouts"][0] and doc["workouts"][1]["te"] == 3


def test_back_to_back_session_voids_recovery(tmp_path):
    end = START + timedelta(minutes=40)
    lift = end + timedelta(seconds=60)
    hr = hr_trace(START, 40) + [(lift + timedelta(seconds=s), 125) for s in range(0, 1800, 5)]
    d = folder(tmp_path, [(START, end, "running"), (lift, lift + timedelta(minutes=30), "traditionalStrengthTraining")],
               [run_stats(START, 40)], hr, run_series(START, 40))
    run = next(w for w in _score(d)["workouts"] if w["kind"] == "running")
    assert run["recovery"]["hrr60"] is None and run["recovery"]["reason"] == "another workout started right after"


def test_daily_card_marks_training_effect_approx():
    from recovery_coach.card import daily_card
    d = json.loads((Path(__file__).parent / "fixtures" / "card_daily.json").read_text())
    d["strain"]["workouts"] = [{"kind": "running", "start": "2026-11-20T08:23:00-06:00", "minutes": 51, "strain": 14.1,
                                "te": 3.7, "rec_h": 37.7, "hrr60": 17.0}]
    assert "TE 3.7 approx" in daily_card(d)


def test_critical_speed_needs_hard_efforts():
    from recovery_coach.weekly import _race
    easy = [LogEntry(id=f"2026-11-{d:02d}T08:00-0600", date=f"2026-11-{d:02d}", kind="running", minutes=50.0,
                     evo2=42.0, secs=3000.0, dist=7500.0, best={"1k": 400.0, "mile": 645.0})
            for d in range(1, 15, 3)]
    assert _race(easy, date(2026, 11, 15))["cs"] is None


def test_new_workouts_matches_by_instant_with_exact_window(tmp_path, capsys):
    log = [LogEntry(id="2026-11-21T19:30+0530", date="2026-11-21", kind="running", minutes=50.0),
           LogEntry(id="2026-11-20T18:00-0600", date="2026-11-20", kind="other", minutes=46.0)]
    st = tmp_path / "state.json"
    st.write_text(json.dumps(compact_state(Settings(dob=date(1996, 4, 12), sex="male"), [], None, 60, workouts=log)))
    d = tmp_path / "in"
    d.mkdir()
    (d / "workouts.csv").write_text("start,end,kind,kcal,avg_hr\n"
                                    "2026-11-21T08:00:10-0600,2026-11-21T08:50:10-0600,running,500,\n"
                                    "2026-11-20T17:59:55-0600,2026-11-20T18:45:59-0600,traditionalStrengthTraining,200,\n")
    capsys.readouterr()
    assert helper_main(["new-workouts", str(st), str(d)]) == 0
    out = json.loads(capsys.readouterr().out.splitlines()[0])
    assert out == [{"id": "2026-11-20T17:59-0600", "kind": "traditionalStrengthTraining",
                    "from": "2026-11-20T17:59:55-0600", "to": "2026-11-20T18:48:59-0600"}]


def test_workout_ring_marks_recovery_time_approx():
    from recovery_coach.card import workout_card
    d = json.loads((Path(__file__).parent / "fixtures" / "card_workout.json").read_text())
    assert "to recover, approx" in workout_card(d)
