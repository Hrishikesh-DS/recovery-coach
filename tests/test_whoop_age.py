import math
from datetime import date, timedelta

import pytest

from recovery_coach import whoop_age as wa
from recovery_coach.inputs import DayRow

END = date(2026, 11, 22)

TABLE2 = [("male", dict(sleep_h=6.35, consistency=65, steps=5200, z13=69, z45=0, strength=0, vo2=42.4, rhr=67, lbm=73.9), 6.0),
          ("male", dict(sleep_h=7.0, consistency=67, steps=10900, z13=141, z45=6, strength=46, vo2=46.8, rhr=58, lbm=81), -1.6),
          ("female", dict(sleep_h=6.35, consistency=65, steps=5000, z13=46, z45=0, strength=0, vo2=30.2, rhr=72, lbm=62.2), 7.5),
          ("female", dict(sleep_h=7.3, consistency=68, steps=11500, z13=124, z45=7, strength=56, vo2=40.0, rhr=63, lbm=73), -1.6)]

ON_TARGET = dict(sleep_h=6.999, consistency=70, steps=8000, z13=100, z45=10, strength=40, vo2=44, rhr=60, lbm=80)


def row(d: date, **kw) -> DayRow:
    base = dict(sleep_min=480, onset_min=-60, wake_min=420, steps=8000, strength_min=40 / 7, vo2=44.0, sleep_hr=60,
                z13_min=100 / 7, z45_min=10 / 7, lbm_pct=80.0)
    base.update(kw)
    r = DayRow(date=d.isoformat())
    for k, v in base.items():
        setattr(r, k, v)
    return r


def rows_for(n: int, end: date = END, **kw) -> list[DayRow]:
    return [row(end - timedelta(days=i), **kw) for i in range(n - 1, -1, -1)]


def test_table2_anchors_within_0_6_years():
    for sex, v, want in TABLE2:
        assert abs(sum(wa.metric_years(v, 30, sex).values()) - want) <= 0.6


def test_on_target_is_zero_years():
    assert all(abs(y) < 0.01 for y in wa.metric_years(ON_TARGET, 30, "male").values())


def test_names_in_fixed_order():
    assert list(wa.metric_years(ON_TARGET, 30, "male")) == list(wa.NAMES)
    assert wa.NAMES == ("Hours of sleep", "Sleep consistency", "Steps", "Zones 1-3", "Zones 4-5", "Strength",
                        "VO2 max", "Resting HR", "Lean body mass")


def test_sleep_shape_is_never_scaled():
    assert wa.metric_years(dict(sleep_h=6.0), 30, "male")["Hours of sleep"] == pytest.approx(10 * math.log(1.12))
    assert wa.metric_years(dict(sleep_h=8.0), 30, "male")["Hours of sleep"] == pytest.approx(10 * math.log(0.98))
    assert wa.metric_years(dict(sleep_h=9.5), 30, "male")["Hours of sleep"] == 0


def test_other_metrics_scaled_by_overlap_factor():
    y = wa.metric_years(dict(rhr=70), 30, "male")["Resting HR"]
    assert y == pytest.approx(wa.OVERLAP * 10 * math.log(1.09))


def test_vo2_target_falls_with_age():
    assert wa.metric_years(dict(vo2=44), 40, "male")["VO2 max"] < 0     # target 39.2 at 40


def test_missing_value_counts_zero():
    assert wa.metric_years(dict(), 30, "male") == {n: 0.0 for n in wa.NAMES}


def test_thin_history_lists_every_metric_missing():
    r = wa.whoop_age(rows_for(10, vo2=None, lbm_pct=None), END, 27.7, "male")
    assert r.body_age == pytest.approx(27.7)
    assert [n for n, _ in r.missing] == list(wa.NAMES)
    assert r.pace is None and r.pace_reason
    assert [i.name for i in r.items] == list(wa.NAMES) and all(i.years == 0 and i.value is None for i in r.items)


def test_delta_is_sum_of_item_years_and_age_added():
    r = wa.whoop_age(rows_for(182), END, 30.0, "male")
    assert not r.missing
    assert r.delta == pytest.approx(sum(i.years for i in r.items))
    assert r.body_age == pytest.approx(30.0 + r.delta)
    cons = next(i for i in r.items if i.name == "Sleep consistency")
    assert cons.value == pytest.approx(100.0) and cons.years == pytest.approx(-0.012 * 25 * 10)


def test_window_is_182_days():
    rows = rows_for(400, steps=2000) + []
    for r in rows[-182:]:
        r.steps = 8000
    out = wa.whoop_age(rows, END, 30.0, "male")
    assert next(i for i in out.items if i.name == "Steps").value == pytest.approx(8000)


def test_lean_mass_uses_latest_within_90_days():
    rows = rows_for(182, lbm_pct=None)
    rows[-100].lbm_pct = 70.0
    out = wa.whoop_age(rows, END, 30.0, "male")
    assert "Lean body mass" in [n for n, _ in out.missing]
    rows[-20].lbm_pct = 77.2
    out = wa.whoop_age(rows, END, 30.0, "male")
    assert next(i for i in out.items if i.name == "Lean body mass").value == pytest.approx(77.2)


def test_vo2_needs_two_readings_latest_within_120_days():
    rows = rows_for(182, vo2=None)
    rows[0].vo2, rows[1].vo2 = 40.0, 40.0       # 181 and 180 days ago: too old
    assert "VO2 max" in [n for n, _ in wa.whoop_age(rows, END, 30.0, "male").missing]
    rows[-5].vo2 = 42.0
    out = wa.whoop_age(rows, END, 30.0, "male")
    assert next(i for i in out.items if i.name == "VO2 max").value == pytest.approx(122 / 3)


def test_consistency_identical_nights_is_100():
    assert wa.consistency(rows_for(6)) == pytest.approx(100.0)


def test_consistency_skips_nights_with_fewer_than_two_comparable():
    rows = rows_for(3)
    rows[2].onset_min, rows[2].wake_min = 0, 480     # overlap with [-60, 420]: 420 / 540
    # night 0: no prior nights; night 1: one prior night -> skipped; night 2: two priors -> scored
    assert wa.consistency(rows) == pytest.approx(100 * 420 / 540)


def test_consistency_none_without_times():
    assert wa.consistency(rows_for(5, onset_min=None, wake_min=None)) is None


def test_pace_one_when_habits_steady():
    assert wa.whoop_age(rows_for(182), END, 30.0, "male").pace == pytest.approx(1.0)


def test_pace_formula_and_clip():
    rows = rows_for(182)
    for r in rows[-30:]:
        r.sleep_min = 300                           # 5 h in the last 30 days
    out = wa.whoop_age(rows, END, 30.0, "male")
    last30 = wa.whoop_age(rows[-30:], END, 30.0, "male", min_days=wa.PACE_MIN_DAYS)
    assert out.pace == pytest.approx(min(3.0, 1 + (last30.delta - out.delta) / 0.5), abs=0.01)
    assert -1 <= out.pace <= 3


def test_pace_withheld_with_thin_last_month():
    rows = rows_for(182)
    for r in rows[-30:-10]:
        r.sleep_min = None
    out = wa.whoop_age(rows, END, 30.0, "male")
    assert out.pace is None and "21" in out.pace_reason
