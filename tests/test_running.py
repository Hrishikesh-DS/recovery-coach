"""Running efficiency, fitness and mechanics (synthetic runs)."""
import math
from datetime import datetime, timedelta, timezone

from recovery_coach.inputs import RunPoint, WorkoutStats
from recovery_coach.running import (
    best_efforts, effective_vo2, efficiency, hilly, is_run, mechanics, power_hr_line, rss, rtss_flat, running_index,
    uth_vo2,
)

CST = timezone(timedelta(hours=-6))
T0 = datetime(2026, 11, 21, 8, 0, tzinfo=CST)


def run_series(minutes, speed=3.0, hr=lambda s: 150, step=5):
    pts = [RunPoint(T0 + timedelta(seconds=s), speed, 230.0, speed * s) for s in range(0, minutes * 60, step)]
    hrs = [(T0 + timedelta(seconds=s), hr(s)) for s in range(0, minutes * 60, step)]
    return pts, hrs, T0 + timedelta(minutes=minutes)


def test_effective_vo2_worked_example():
    assert round(effective_vo2(10000, 3000, 160, 190), 1) == 48.3


def test_decoupling_steady_vs_drift():
    pts, hrs, end = run_series(40)
    r = efficiency(pts, hrs, T0, end)
    assert abs(r["dec"]) < 0.5 and math.isclose(r["ef"], 180 / 150, rel_tol=1e-3)
    pts, hrs, end = run_series(40, hr=lambda s: 150 if s < 25 * 60 else 162)
    assert round(efficiency(pts, hrs, T0, end)["dec"], 1) == 7.4


def test_efficiency_short_run_reason():
    pts, hrs, end = run_series(25)
    r = efficiency(pts, hrs, T0, end)
    assert r["dec"] is None and r["reason"] == "under 20 steady minutes"


def test_running_index_validity_window():
    assert running_index(10000, 3000, 160, 190, 55) is not None
    assert running_index(1500, 600, 160, 190, 55) is None      # 10 minutes: too short
    assert running_index(10000, 3000, 70, 190, 55) is None     # HRR 11 %: outside 52-98 %


def test_mechanics_none_when_missing():
    w = WorkoutStats(T0, T0 + timedelta(minutes=30), "running")
    assert mechanics(w) == {"vert_ratio": None, "eff_index": None, "muscle_kj": None}
    w2 = WorkoutStats(T0, T0 + timedelta(minutes=30), "running", avg_speed=3.0, avg_power=240, stride=1.1,
                      vo_cm=8.8)
    m = mechanics(w2)
    assert round(m["vert_ratio"], 1) == 8.0 and round(m["eff_index"], 4) == 0.0125 and round(m["muscle_kj"]) == 432


def test_best_efforts_constant_pace():
    speed = 10000 / 3000
    pts = [RunPoint(T0 + timedelta(seconds=s), speed, None, speed * s) for s in range(0, 3001, 5)]
    b = best_efforts(pts)
    assert abs(b["1k"] - 300) < 1 and abs(b["5k"] - 1500) < 1 and abs(b["10k"] - 3000) < 1
    assert "half" not in b


def test_power_hr_line_needs_four():
    assert power_hr_line([(140, 210), (150, 225), (160, 240)]) is None
    a, b = power_hr_line([(140, 210), (150, 225), (160, 240), (170, 255)])
    assert math.isclose(b, 1.5) and math.isclose(a, 0.0, abs_tol=1e-9)


def test_zero_distance_returns_none():
    assert effective_vo2(0, 1800, 150, 190) is None and running_index(0, 1800, 150, 190, 55) is None
    assert best_efforts([]) == {}


def test_stress_scores_and_helpers():
    assert math.isclose(rtss_flat(3600, 3.0, 3.0), 100) and math.isclose(rss(1800, 250, 250), 50)
    assert round(uth_vo2(188, 66), 1) == 43.6 and is_run("running") and not is_run("walking")
    assert hilly(WorkoutStats(T0, T0, "running", dist=5000, elev_up=80))
    assert not hilly(WorkoutStats(T0, T0, "running", dist=5000, elev_up=20))
