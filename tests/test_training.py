"""Daily and weekly training figures (synthetic)."""
import math
from datetime import date, timedelta

from recovery_coach import load
from recovery_coach.inputs import DayRow
from recovery_coach.training import (
    easy_cap_trimp, focus_shortage, form_pct, garmin_band, hrv_status, increments, monotony, performance_condition,
    polar_band, polarization, ramp, running_tolerance, session_level, split_window, srpe_ratio, status, typical_z,
    weekly_target,
)
from recovery_coach.workout_log import LogEntry

D = date(2026, 11, 21)


def rows(n, **kw):
    return [DayRow(date=(D - timedelta(days=n - 1 - i)).isoformat(), **{k: (v(i) if callable(v) else v)
                                                                         for k, v in kw.items()}) for i in range(n)]


def ent(day_offset, kind="running", **kw):
    d = (D - timedelta(days=day_offset)).isoformat()
    return LogEntry(id=f"{d}T08:00-0600", date=d, kind=kind, minutes=kw.pop("minutes", 45), **kw)


def test_monotony_rest_days_zero():
    m, s, flag = monotony([100, 0, 100, 0, 100, 0, 0])
    mean = 300 / 7
    sd = math.sqrt(sum((x - mean) ** 2 for x in [100, 0, 100, 0, 100, 0, 0]) / 7)
    assert math.isclose(m, mean / sd) and math.isclose(s, 300 * m) and flag is None


def test_monotony_flat_week_capped():
    assert monotony([80] * 7) == (10.0, 5600.0, "alert")
    assert monotony([0] * 7) == (0.0, 0.0, None)
    assert monotony([100, 50, 120, 0, 90, 60, 80])[2] == "warn"     # monotony 1.96


def test_easy_cap_matches_derivation():
    ctl, atl = 140.0, 120.0
    cap = easy_cap_trimp(ctl, atl)
    assert cap > 0
    nxt = load.update(DayRow(date="x", ctl=ctl, atl=atl), cap)
    assert abs(nxt["tsb"]) < 1e-6
    assert easy_cap_trimp(50.0, 200.0) == 0.0


def test_increments_follow_engine_update():
    df, dfat = increments(200.0, 120.0, 140.0)
    nxt = load.update(DayRow(date="x", ctl=120.0, atl=140.0), 200.0)
    assert math.isclose(120 + df, nxt["ctl"]) and math.isclose(140 + dfat, nxt["atl"])


def test_form_and_ramp():
    assert math.isclose(form_pct(100, 110), -10.0) and form_pct(0, 5) is None
    r = rows(15, ctl=lambda i: 100 + i)
    assert ramp(r, D) == 7


def test_polarization_worked_example():
    assert round(polarization([80, 5, 15]), 2) == 2.38


def test_polarization_none_when_no_high():
    assert polarization([90, 10, 0]) is None and polarization([10, 5, 20]) is None


def test_bands_edges():
    assert [polar_band(x) for x in (0.7, 0.9, 1.1, 1.4, 1.6)] == [
        "detraining", "maintaining", "productive", "overreaching", "injury risk"]
    assert [garmin_band(x) for x in (0.7, 1.0, 1.6, 2.0)] == ["low", "optimal", "high", "very high"]


def test_srpe_ratio_needs_28_days():
    assert srpe_ratio(rows(20, srpe=100.0), D) is None
    r = rows(28, srpe=lambda i: 200.0 if i >= 21 else 100.0)
    assert math.isclose(srpe_ratio(r, D), 1400 / ((2100 + 1400) / 4))


def test_split_window_and_focus():
    log = [ent(1, split=[40, 4, 1]), ent(10, split=[30, 10, 5]), ent(40, split=[1, 1, 1])]
    assert split_window(log, D, 28) == [70, 14, 6]
    assert focus_shortage([ent(1, split=[10, 30, 0])], D) == ["low aerobic", "anaerobic"]


def test_weekly_target_and_tolerance():
    r = rows(28, wload=100.0)
    lo, hi = weekly_target(r, D)
    assert math.isclose(lo, 560) and math.isclose(hi, 910)
    log = [ent(1, dist=8000), ent(3, dist=5000), ent(9, dist=20000)]
    assert math.isclose(running_tolerance(log, D), 20 * 1.15)
    assert running_tolerance([], D) is None


def test_session_level_needs_three():
    log = [ent(i, edwards=100.0) for i in (2, 4)]
    new = ent(0, edwards=210.0)
    assert session_level(new, log) is None
    assert session_level(new, log + [ent(6, edwards=100.0)]) == "very high"


def test_typical_z_and_performance_condition():
    log = [ent(i, ef=1.20 + 0.01 * (i % 3), avg_hr=150 + i) for i in range(2, 12)]
    assert performance_condition(ent(0, ef=1.32), log) == 18   # 100 x (1.32 - 1.21) / 1.21 x 2
    new = ent(0, ef=1.40)
    assert typical_z(new, log, "ef") > 2
    assert performance_condition(new, log) == 20
    assert performance_condition(new, log[:2]) is None


def test_hrv_status_low():
    base = rows(67, hrv_ln=lambda i: 4.0 + (0.05 if i % 2 else -0.05) if i < 60 else 3.6)
    assert hrv_status(base, D) == "low"
    ok = rows(67, hrv_ln=lambda i: 4.0 + (0.05 if i % 2 else -0.05))
    assert hrv_status(ok, D) == "balanced"


def test_status_labels():
    assert status(1.1, 0.5, 60) == "productive"
    assert status(1.1, -0.8, 60) == "unproductive"
    assert status(1.4, 0.0, 30) == "strained"
    assert status(None, 0, 60) is None
