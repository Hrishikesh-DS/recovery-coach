import math
from datetime import datetime, timedelta

from recovery_coach.inputs import HourStat, Workout
from recovery_coach.stress import stress_day

D = datetime(2026, 11, 10)
BASE = [60.0] * 50  # median 60, MAD 0 -> spread floor 3
NIGHT = [4.0 + 0.1 * ((i % 3) - 1) for i in range(30)]


def hr(h, avg, steps=0.0):
    return HourStat(start=D + timedelta(hours=h), avg_hr=avg, steps=steps)


def run(hours, **kw):
    args = dict(workouts=[], sleep_windows=[], hrv_today=[], baseline_still_hr=BASE,
                night_hrv_hist=NIGHT)
    args.update(kw)
    return stress_day(hours, args["workouts"], args["sleep_windows"], args["hrv_today"],
                      args["baseline_still_hr"], args["night_hrv_hist"])


def test_still_filter():
    wo = Workout(start=D + timedelta(hours=10, minutes=30), end=D + timedelta(hours=11, minutes=15),
                 kind="run", energy_kcal=300, avg_hr=150)
    sleep = (D, D + timedelta(hours=7))
    hours = [hr(3, 55), hr(8, 60), hr(9, 60, steps=150), hr(10, 99), hr(11, 99),
             hr(12, None), hr(13, 66)]
    r = run(hours, workouts=[wo], sleep_windows=[sleep])
    assert r.still_readings == [60, 66]  # 8h, 13h only
    assert [h for h, _ in r.hourly] == [D + timedelta(hours=8), D + timedelta(hours=13)]


def test_baseline_min_50():
    r = run([hr(8, 60)], baseline_still_hr=[60.0] * 40)
    assert r.score is None and r.band is None
    assert r.reason == "building stress baseline: 40 of 50 still hours"


def test_no_still_hours():
    r = run([hr(8, 60, steps=500)])
    assert r.score is None and r.reason == "no still hours"


def test_band_edges():
    for z, score, band in [(0.4, 0.9, "low"), (1.0, 1.5, "medium"), (2.0, 2.5, "high")]:
        r = run([hr(8, 60 + 3 * z)])
        assert math.isclose(r.score, score) and r.band == band


def test_clipping():
    assert run([hr(8, 40)]).score == 0.0
    assert run([hr(8, 120)]).score == 3.0


def test_hrv_blend():
    mean = sum(NIGHT) / len(NIGHT)
    sd = math.sqrt(sum((x - mean) ** 2 for x in NIGHT) / (len(NIGHT) - 1))
    ms = math.exp(mean - 1.0 * sd)  # z_hrv = -1 -> stress part 1
    h = [hr(8, 63)]  # z_hr = 1
    r = run(h, hrv_today=[(D + timedelta(hours=8, minutes=20), ms)])
    assert math.isclose(r.score, 0.5 + 0.7 * 1.0 + 0.3 * 1.0, rel_tol=1e-9)
    # HRV above baseline adds nothing but still blends 0.7 weight
    r2 = run(h, hrv_today=[(D + timedelta(hours=8, minutes=20), math.exp(mean + sd))])
    assert math.isclose(r2.score, 0.5 + 0.7)
    # too little HRV history -> plain z_hr
    r3 = run(h, hrv_today=[(D + timedelta(hours=8, minutes=20), ms)], night_hrv_hist=NIGHT[:10])
    assert math.isclose(r3.score, 1.5)
    # HRV in a different hour -> plain z_hr
    r4 = run(h, hrv_today=[(D + timedelta(hours=9, minutes=1), ms)])
    assert math.isclose(r4.score, 1.5)


def test_top_hours_and_day_mean():
    hours = [hr(8, 60), hr(9, 66), hr(10, 63), hr(11, 69), hr(12, 61)]
    r = run(hours)
    assert r.top_hours == [D + timedelta(hours=h) for h in (11, 9, 10)]
    assert math.isclose(r.score, sum(s for _, s in r.hourly) / 5)
