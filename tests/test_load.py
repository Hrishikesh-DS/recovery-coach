import math
from datetime import date, datetime, timedelta, timezone

from recovery_coach import load
from recovery_coach.inputs import DayRow, Workout

TZ = timezone(timedelta(hours=-6))


def rows(trimps, start=date(2026, 1, 1)):
    return [DayRow(date=(start + timedelta(days=i)).isoformat(), trimp=t) for i, t in enumerate(trimps)]


def test_ctl_atl():
    # start from a seeded 0 so the smoothing constants actually matter
    prev = DayRow(date="x", ctl=0.0, atl=0.0)
    for day in range(1, 121):
        out = load.update(prev, 100.0)
        prev = DayRow(date="x", **out)
        if day == 7:
            assert out["atl"] > out["ctl"] + 30 and out["tsb"] < -30
            assert abs(out["atl"] - 100 * (1 - math.exp(-7 / 7))) < 1e-9
            assert abs(out["ctl"] - 100 * (1 - math.exp(-7 / 42))) < 1e-9
    assert abs(out["atl"] - 100) < 0.1
    assert abs(out["ctl"] - 100 * (1 - math.exp(-120 / 42))) < 1e-9  # ~94.3
    assert out["ctl"] > 90 and abs(out["tsb"] - (out["ctl"] - out["atl"])) < 1e-9


def test_ctl_single_step():
    out = load.update(DayRow(date="x", ctl=50.0, atl=50.0), 100.0)
    assert abs(out["ctl"] - (50 + (1 - math.exp(-1 / 42)) * 50)) < 1e-9


def test_update_first_and_step():
    out = load.update(None, 80.0)
    assert out == {"ctl": 80.0, "atl": 80.0, "tsb": 0.0}
    out = load.update(DayRow(date="x", ctl=50.0, atl=50.0), 100.0)
    assert abs(out["atl"] - (50 + (1 - 2.718281828 ** (-1 / 7)) * 50)) < 1e-3
    assert out["tsb"] == out["ctl"] - out["atl"] and out["tsb"] < 0


def test_seed():
    out = load.seed([10.0] * 20 + [1000.0])
    assert out == {"ctl": 10.0, "atl": 10.0, "tsb": 0.0}
    assert load.seed([10.0, 20.0])["ctl"] == 15.0


def test_acwr_spike():
    h = rows([100.0] * 21 + [200.0] * 7)
    x = load.acwr(h)
    assert abs(x - 1.6) < 1e-9
    assert load.acwr_flag(x) == "spike"
    assert load.acwr_flag(0.7) == "detraining"
    assert load.acwr_flag(1.0) is None


def test_acwr_thin_or_zero():
    assert load.acwr(rows([100.0] * 27)) is None
    assert load.acwr(rows([0.0] * 28)) is None
    h = rows([100.0] * 28)
    h[3].trimp = None
    assert load.acwr(h) is None


def test_seed_from_workouts():
    d1, d2 = date(2026, 11, 8), date(2026, 11, 9)
    s = datetime(2026, 11, 8, 17, 0, tzinfo=TZ)
    ws = [
        Workout(s, s + timedelta(minutes=30), "running", 200, 150.0),  # 75% of 200 -> w3
        Workout(s, s + timedelta(minutes=30), "other", 200, None),  # duplicate-ish, deduped away
        Workout(s + timedelta(hours=3), s + timedelta(hours=4), "other", 100, None),  # no hr -> 0
    ]
    out = load.seed_from_workouts(ws, 200.0, [d1, d2])
    assert out == {d1: 90.0, d2: 0.0}
