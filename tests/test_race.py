"""Race predictions, training paces, marathon shape and critical speed (synthetic)."""
import math
from datetime import date

from recovery_coach.race import (
    critical_speed, marathon_shape, paces, pct_max, predict, predictions, riegel, shape, vdot_from_race, vo2_at,
)
from recovery_coach.workout_log import LogEntry


def e(day, evo2=None, secs=3000.0, dist=8000.0, indoor=False, kind="running"):
    return LogEntry(id=f"{day}T08:00-0600", date=day, kind=kind, minutes=secs / 60, indoor=indoor, evo2=evo2,
                    secs=secs, dist=dist)


def test_daniels_parts():
    assert math.isclose(vo2_at(200), -4.6 + 0.182258 * 200 + 0.000104 * 40000)
    assert 0.8 < pct_max(50) < 1.0


def test_vdot_round_trip():
    v = vdot_from_race(10000, 3000)
    assert abs(predict(10000, v, 1000) - 3000) < 1


def test_marathon_penalty_lowers_prediction():
    assert predict(42195, 45, 30) > predict(42195, 45, 300)
    assert set(predictions(45, 100)) == {"5k", "10k", "half", "marathon"}


def test_paces_order():
    p = paces(45)
    assert p["easy"] > p["marathon"] > p["threshold"] > p["interval"] > p["repetition"] > 0


def test_critical_speed_two_efforts():
    cs, d = critical_speed([(180, 1000), (720, 3400)])
    assert math.isclose(cs, 2400 / 540) and math.isclose(d, 1000 - 2400 / 540 * 180)
    assert critical_speed([(180, 1000)]) is None
    assert critical_speed([(60, 400), (1800, 7000)]) is None   # outside 2-15 minutes


def test_shape_uses_outdoor_only():
    log = [e("2026-11-14", 45.0), e("2026-11-18", 30.0, indoor=True), e("2026-11-20", 47.0, secs=1000)]
    assert math.isclose(shape(log, date(2026, 11, 21)), (45 * 3000 + 47 * 1000) / 4000)
    assert shape([e("2026-11-18", 30.0, indoor=True)], date(2026, 11, 21)) is None


def test_marathon_shape_grows_with_volume():
    few = [e(f"2026-11-{d:02d}", 45.0, dist=5000) for d in (7, 14, 21)]
    many = [e(f"2026-11-{d:02d}", 45.0, dist=10000) for d in range(1, 22)]
    assert marathon_shape(many, date(2026, 11, 21), 45) > marathon_shape(few, date(2026, 11, 21), 45) > 0


def test_riegel():
    assert math.isclose(riegel(1500, 5000, 10000), 1500 * 2 ** 1.06)
