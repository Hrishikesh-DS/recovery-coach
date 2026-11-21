import statistics
from datetime import datetime, timedelta, timezone

from recovery_coach.inputs import HourStat, Workout
from recovery_coach.strain import (
    calibrate_k, day_strain, dedupe_workouts, hr_max, strain, strain_target,
    trimp, workout_strain,
)

TZ = timezone(timedelta(hours=-6))
T0 = datetime(2026, 11, 10, 10, 0, tzinfo=TZ)


def mins(offsets_bpm):
    return [(T0 + timedelta(minutes=m), bpm) for m, bpm in offsets_bpm]


def hours(n):
    return [HourStat(T0 + timedelta(hours=i), 70.0, 60.0, 80.0, 0.0) for i in range(n)]


def test_trimp_180_maps_13_7():
    assert abs(strain(180, 170) - 13.7) <= 0.05


def test_edwards_zones():
    # 65% of 200 = 130 bpm; 10 one-minute intervals then a sub-50% reading closes them
    r = mins([(i, 130) for i in range(11)])
    r[-1] = (r[-1][0], 80)
    t, _ = trimp(r, [], 200)
    assert t == 20


def test_zone_boundaries():
    r = mins([(0, 99), (10, 100), (20, 120), (30, 140), (40, 160), (50, 180)])
    # each reading: 5 min (last 2.5): zones 0,1,2,3,4,5 -> 0+5+10+15+20+12.5
    t, _ = trimp(r, [], 200)
    assert t == 62.5


def test_gap_cap():
    r = mins([(0, 110), (20, 110)])  # 55% -> weight 1
    t, _ = trimp(r, [], 200)
    assert t == 7.5  # 5 min capped + 2.5 final, not 20+


def test_trimp_empty_and_coverage():
    t, cov = trimp([], hours(5) + [HourStat(T0, None)], 200)
    assert t == 0 and cov == 5


def test_calibrate_k():
    data = [float(x) for x in range(50, 450, 20)]
    k = calibrate_k(data)
    t90 = statistics.quantiles(data, n=10, method="inclusive")[8]
    assert abs(strain(t90, k) - 15) <= 0.05


def test_calibrate_k_default_when_thin():
    assert calibrate_k([100.0] * 13) == 170.0
    assert calibrate_k([0.0] * 30) == 170.0


def test_hr_max():
    assert hr_max([150, 160, 170, 180, 200, 210]) == 180  # top 2 dropped
    assert hr_max([150, 160]) is None
    assert hr_max([150, 160], age=27) == 208 - 0.7 * 27
    assert hr_max([], age=None) is None
    assert hr_max([150, 160, 170, 180, 200], age=27) == max(208 - 0.7 * 27, 170)


def test_dedupe_duplicate_strength_logs():
    a = Workout(datetime(2026, 11, 8, 17, 53, 12, tzinfo=TZ), datetime(2026, 11, 8, 18, 37, 32, tzinfo=TZ),
                "traditionalStrengthTraining", 250.0, None)
    b = Workout(datetime(2026, 11, 8, 17, 53, 11, tzinfo=TZ), datetime(2026, 11, 8, 18, 37, 31, tzinfo=TZ),
                "other", 248.0, None)
    out = dedupe_workouts([b, a])
    assert len(out) == 1 and out[0].kind == "traditionalStrengthTraining"


def test_dedupe_prefers_hr_and_keeps_distinct():
    a = Workout(T0, T0 + timedelta(minutes=30), "traditionalStrengthTraining", 200.0, None)
    b = Workout(T0, T0 + timedelta(minutes=30), "other", None, 120.0)
    c = Workout(T0 + timedelta(hours=3), T0 + timedelta(hours=4), "running", 300.0, 150.0)
    out = dedupe_workouts([c, a, b])
    # the copy with heart rate is kept, under the duplicate's more specific kind (it stays strength)
    assert [(w.kind, w.avg_hr) for w in out] == [("traditionalStrengthTraining", 120.0), ("running", 150.0)]
    assert dedupe_workouts([b])[0].kind == "other"
    # barely overlapping (<80%) are both kept
    d = Workout(T0 + timedelta(minutes=25), T0 + timedelta(minutes=55), "other", 50.0, None)
    assert len(dedupe_workouts([a, d])) == 2


def test_low_coverage_flag():
    r = mins([(i, 170) for i in range(61)])
    t, _ = trimp(r, hours(8), 200)  # 85% -> weight 4
    assert t >= 40
    assert day_strain(t, 8, 170.0)[1] == "may be low"
    assert day_strain(t, 8, 170.0)[0] is not None
    assert day_strain(5.0, 8, 170.0) == (None, "not enough heart-rate data")
    s, flag = day_strain(100.0, 14, 170.0)
    assert flag is None and abs(s - strain(100, 170)) < 1e-9


def test_workout_strain_window():
    w = Workout(T0, T0 + timedelta(minutes=10), "running", None, None)
    r = mins([(-30, 180), (0, 160), (5, 160), (9, 160), (30, 180)])
    # in window: 0->5 (5), 5->9 (4), 9 last in window (2.5) = 11.5 min x weight 4 = 46
    assert abs(workout_strain(w, r, 200, 170.0) - strain(46, 170.0)) < 1e-9


def test_target_bands():
    assert strain_target("green", None) == (14, 18)
    assert strain_target("yellow", None) == (10, 14)
    assert strain_target("red", None) == (0, 10)
    assert strain_target("green", -25) == (12, 16)
    assert strain_target("red", -25) == (0, 8)
    assert strain_target("yellow", -20) == (10, 14)


def test_short_walk_inside_a_long_other_session_does_not_rename_it():
    long_other = Workout(T0, T0 + timedelta(minutes=90), "other", 600.0, 140.0)
    walk = Workout(T0 + timedelta(minutes=20), T0 + timedelta(minutes=28), "walking", 40.0, None)
    kept = dedupe_workouts([long_other, walk])
    assert [w.kind for w in kept] == ["other"]
