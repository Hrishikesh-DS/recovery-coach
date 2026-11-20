import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from recovery_coach import healthspan as hs
from recovery_coach import healthspan_tables as T
from recovery_coach.inputs import DayRow

END = date(2026, 6, 30)
AGE = 30.0
GOLDEN = Path(__file__).parent / "fixtures" / "healthspan_v12.json"

# watch-measured hours whose self-report equivalent is exactly the 7 h anchor
ANCHOR_SLEEP_H = (T.SLEEP_NADIR_H - T.SLEEP_SELF_REPORT[0]) / T.SLEEP_SELF_REPORT[1]


def make_rows(n, end=END, fn=None, **fields):
    """n consecutive DayRows ending at `end`; fn(i, rows_total) -> dict of per-row overrides."""
    out = []
    for i in range(n):
        d = end - timedelta(days=n - 1 - i)
        kw = dict(fields)
        if fn:
            kw.update(fn(i, n))
        out.append(DayRow(date=d.isoformat(), **kw))
    return out


def item(result, name):
    return next(i for i in result.items if i.name == name)


def missing_names(result):
    return [m[0] for m in result.missing]


def anchor_rows(n=180):
    """A person at every anchor: 7 h self-report sleep, SRI ~81, 7803 steps, same-age same-sex VO2, RHR 65."""
    norm = hs.vo2_norm(AGE, "male")
    shift = 68   # alternate onset/wake by 68 min -> 136 mismatched minutes per pair -> SRI 81.1

    def fn(i, total):
        s = shift if i % 2 else 0
        return {"onset_min": -60 + s, "wake_min": 420 + s, "vo2": norm if i % 10 == 0 else None}
    return make_rows(n, fn=fn, sleep_min=ANCHOR_SLEEP_H * 60, steps=7803, rhr=65)


# ---------------------------------------------------------------- body_age
def test_anchor_person_is_calendar_age():
    r = hs.body_age(AGE, "male", anchor_rows(), None, None)
    assert {i.name for i in r.items} == {"sleep_duration", "sleep_regularity", "activity", "vo2max", "rhr"}
    assert sorted(missing_names(r)) == ["body_comp", "strength"]   # no strength records: activity is steps only
    assert r.body_age == pytest.approx(AGE, abs=0.02)
    for i in r.items:
        assert abs(i.years) < 0.05, i


def test_rhr_curve():
    rows = make_rows(60, rhr=75)
    r = hs.body_age(AGE, "male", rows, None, None)
    expected = math.log(1.16) / math.log(2) * 8
    assert item(r, "rhr").years == pytest.approx(expected)
    assert expected == pytest.approx(1.71, abs=0.01)
    assert item(r, "rhr").value == pytest.approx(75)
    # only RHR present: total = 0.6 x item (items are listed before the x 0.6 scaling)
    assert [i.name for i in r.items] == ["rhr"]
    assert r.body_age == pytest.approx(AGE + 0.6 * expected)


def test_rhr_flat_below_45():
    low = hs.body_age(AGE, "male", make_rows(40, rhr=38), None, None)
    at45 = hs.body_age(AGE, "male", make_rows(40, rhr=45), None, None)
    assert item(low, "rhr").years == pytest.approx(item(at45, "rhr").years)


def test_item_cap():
    r = hs.body_age(AGE, "male", make_rows(40, rhr=200), None, None)
    assert item(r, "rhr").years == 4.0
    assert r.body_age == pytest.approx(AGE + 0.6 * 4.0)


def test_total_scale_cap():
    norm = hs.vo2_norm(AGE, "male")

    def fn(i, total):   # alternate 5 h shifts: SRI far below the lowest knot
        s = 300 if i % 2 else 0
        return {"onset_min": -60 + s, "wake_min": 420 + s, "vo2": norm - 20 if i % 10 == 0 else None}
    rows = make_rows(180, fn=fn, sleep_min=14 * 60, steps=2000, rhr=200)
    r = hs.body_age(AGE, "male", rows, 150.0, 1.5)    # BMI 66.7 -> beyond the table, capped
    by = {i.name: i.years for i in r.items}
    assert by["sleep_duration"] == 4.0 and by["activity"] == 4.0 and by["vo2max"] == 4.0
    assert by["rhr"] == 4.0 and by["body_comp"] == 4.0
    # SRI can never reach the cap: its curve is flat below 65.1 at HR 1/0.75
    assert by["sleep_regularity"] == pytest.approx(math.log(1 / 0.75) / math.log(2) * 8)
    assert 0.6 * sum(by.values()) > 10.0
    assert r.body_age == pytest.approx(AGE + 10.0)


def test_negative_total_cap():
    norm = hs.vo2_norm(AGE, "male")
    rows = make_rows(180, sleep_min=ANCHOR_SLEEP_H * 60, steps=20000, rhr=40, strength_min=40,
                     vo2=norm + 40, onset_min=-60, wake_min=420)
    r = hs.body_age(AGE, "male", rows, 70.0, 1.80)
    assert r.body_age >= AGE - 10.0 - 1e-9
    assert item(r, "rhr").years < 0 and item(r, "vo2max").years == -4.0


def test_vo2_min_data():
    one = make_rows(180, rhr=65, vo2=None)
    one[100].vo2 = 40.0
    r = hs.body_age(AGE, "male", one, None, None)
    assert "vo2max" in missing_names(r)
    assert "1" in dict(r.missing)["vo2max"]

    two = make_rows(180, rhr=65, vo2=None)
    two[100].vo2 = 40.0
    two[150].vo2 = 42.0
    assert "vo2max" not in missing_names(hs.body_age(AGE, "male", two, None, None))

    stale = make_rows(180, rhr=65, vo2=None)   # latest reading 121 days before the last row
    stale[10].vo2 = 40.0
    stale[58].vo2 = 40.0
    assert "vo2max" in missing_names(hs.body_age(AGE, "male", stale, None, None))
    stale[59].vo2 = 40.0   # 120 days old: allowed
    assert "vo2max" not in missing_names(hs.body_age(AGE, "male", stale, None, None))


def test_vo2_same_age_same_sex_anchor():
    for sex in ("male", "female"):
        norm = hs.vo2_norm(AGE, sex)
        rows = make_rows(60, rhr=None, vo2=None)
        rows[10].vo2 = rows[50].vo2 = norm
        r = hs.body_age(AGE, sex, rows, None, None)
        assert item(r, "vo2max").years == pytest.approx(0.0, abs=1e-9)
    assert hs.vo2_norm(25, "male") == 46.5 and hs.vo2_norm(35, "female") == 28.3
    # +1 MET above the norm -> RR 0.87
    rows = make_rows(60, vo2=None)
    rows[10].vo2 = rows[50].vo2 = hs.vo2_norm(AGE, "male") + 3.5
    r = hs.body_age(AGE, "male", rows, None, None)
    assert item(r, "vo2max").years == pytest.approx(math.log(0.87) / math.log(2) * 8)


def test_vo2_requires_known_sex():
    rows = make_rows(60, vo2=None)
    rows[10].vo2 = rows[50].vo2 = 40.0
    r = hs.body_age(AGE, "other", rows, None, None)
    assert "vo2max" in missing_names(r)


def test_180d_weighting():
    def fn(i, total):
        return {"rhr": 80 if i >= total - 30 else 60}
    rows = make_rows(180, fn=fn)
    r = hs.body_age(AGE, "male", rows, None, None)
    want = (150 * 60 + 30 * 2.0 * 80) / (150 + 30 * 2.0)
    assert item(r, "rhr").value == pytest.approx(want)
    assert want == pytest.approx(65.714, abs=1e-3)


def test_window_is_180_days_ending_at_last_row():
    # 200 rows: the oldest 20 (extreme) lie outside the 180-day window and must be ignored
    rows = make_rows(200, fn=lambda i, n: {"rhr": 150 if i < 20 else 65})
    r = hs.body_age(AGE, "male", rows, None, None)
    assert item(r, "rhr").value == pytest.approx(65)
    assert item(r, "rhr").years == pytest.approx(0.0, abs=1e-9)


def test_min_30_days_per_item():
    short = hs.body_age(AGE, "male", make_rows(29, rhr=70, sleep_min=400, steps=8000), None, None)
    assert short.items == [] and short.body_age is None
    assert set(missing_names(short)) >= {"rhr", "sleep_duration", "sleep_regularity", "activity", "vo2max"}
    assert "29" in dict(short.missing)["rhr"]
    ok = hs.body_age(AGE, "male", make_rows(30, rhr=70), None, None)
    assert [i.name for i in ok.items] == ["rhr"]


def test_empty_history():
    r = hs.body_age(AGE, "male", [], None, None)
    assert r.body_age is None and r.items == [] and r.missing


def test_sleep_duration_uses_self_report_conversion():
    r = hs.body_age(AGE, "male", make_rows(40, sleep_min=7 * 60), None, None)
    sr = 3.365 + 0.567 * 7                  # 7 h measured -> 7.334 h self-report
    assert item(r, "sleep_duration").value == pytest.approx(7.0)
    assert item(r, "sleep_duration").years == pytest.approx(math.log(1.13 ** (sr - 7)) / math.log(2) * 8)
    short = hs.body_age(AGE, "male", make_rows(40, sleep_min=5 * 60), None, None)
    sr5 = 3.365 + 0.567 * 5
    assert item(short, "sleep_duration").years == pytest.approx(math.log(1.06 ** (7 - sr5)) / math.log(2) * 8)


def test_bmi_item_only_with_weight_and_height():
    rows = make_rows(40, rhr=65)
    assert "body_comp" not in [i.name for i in hs.body_age(AGE, "male", rows, 90.0, None).items]
    assert "body_comp" not in [i.name for i in hs.body_age(AGE, "male", rows, None, 1.8).items]
    r = hs.body_age(AGE, "male", rows, 90.0, 1.80)
    bmi = 90 / 1.8 ** 2
    assert item(r, "body_comp").value == pytest.approx(bmi)
    want = math.log(hs.interp_log_hr(bmi, T.BMI_KNOTS) / hs.interp_log_hr(25.0, T.BMI_KNOTS)) / math.log(2) * 8
    assert item(r, "body_comp").years == pytest.approx(want)
    assert item(r, "body_comp").years > 0


def test_activity_steps_only_and_age_curve():
    young = hs.body_age(30, "male", make_rows(40, steps=4000), None, None)
    assert item(young, "activity").years == pytest.approx(
        math.log(0.64 / hs.interp_log_hr(7803, T.STEPS_KNOTS_UNDER60)) / math.log(2) * 8)
    old = hs.body_age(70, "male", make_rows(40, steps=4000), None, None)
    assert item(old, "activity").years != pytest.approx(item(young, "activity").years)


def test_activity_strength_multiplies_and_is_capped_once():
    base = hs.body_age(AGE, "male", make_rows(40, steps=7803), None, None)
    assert item(base, "activity").years == pytest.approx(0.0, abs=1e-9)
    st = hs.body_age(AGE, "male", make_rows(40, steps=7803, strength_min=40 / 7), None, None)
    assert item(st, "activity").years == pytest.approx(math.log(0.83) / math.log(2) * 8)   # 40 min / week
    bad = hs.body_age(AGE, "male", make_rows(40, steps=2000, strength_min=0.0), None, None)
    assert item(bad, "activity").years == 4.0


def test_mvpa_minutes_not_counted_in_activity():
    # v1.2 source: heart-rate MVPA is a reference only (counted = False); walking is already in steps
    a = hs.body_age(AGE, "male", make_rows(40, steps=6000), None, None)
    b = hs.body_age(AGE, "male", make_rows(40, steps=6000, mvpa_min=90.0, vig_min=30.0), None, None)
    assert item(a, "activity").years == item(b, "activity").years


# ---------------------------------------------------------------- sleep_regularity
def night(d, onset_h=-1.0, wake_h=7.0, base=datetime(2026, 1, 1)):
    mid = datetime.combine(d, datetime.min.time())
    return (mid + timedelta(hours=onset_h), mid + timedelta(hours=wake_h))


def test_sri_identical_nights():
    iv = [night(date(2026, 1, 1) + timedelta(days=i)) for i in range(40)]
    assert hs.sleep_regularity(iv) == 100.0


def test_sri_needs_30_pairs():
    iv = [night(date(2026, 1, 1) + timedelta(days=i)) for i in range(30)]    # 29 pairs
    assert hs.sleep_regularity(iv) is None
    iv.append(night(date(2026, 1, 31)))
    assert hs.sleep_regularity(iv) == 100.0


def test_sri_only_adjacent_nights_pair_up():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(0, 80, 2)]   # every other day: no adjacent pairs
    assert hs.sleep_regularity([night(d) for d in days]) is None


def test_sri_known_value():
    # alternate nights shifted 2 h later: 4 h of mismatch per pair (2 h at onset edge + 2 h at wake edge)
    iv = [night(date(2026, 1, 1) + timedelta(days=i), -1 + (2 if i % 2 else 0), 7 + (2 if i % 2 else 0)) for i in range(40)]
    assert hs.sleep_regularity(iv) == pytest.approx(200 * (1 - 240 / 1440) - 100, abs=0.1)


def test_sri_dst_day_uses_elapsed_time():
    # identical local sleep (23:00-07:00) across the 2026-03-08 US spring-forward; with real elapsed time the
    # DST night is 7 h, so that pair is not identical but all other pairs are.
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Chicago")
    iv = []
    for i in range(40):
        d = date(2026, 2, 20) + timedelta(days=i)
        iv.append((datetime.combine(d - timedelta(days=1), datetime.min.time().replace(hour=23), tz),
                   datetime.combine(d, datetime.min.time().replace(hour=7), tz)))
    v = hs.sleep_regularity(iv)
    assert v is not None and 99.0 < v < 100.0


# ---------------------------------------------------------------- pace_of_aging
def test_pace_flat():
    assert hs.pace_of_aging([0.5] * 10) == 1.0
    assert hs.pace_of_aging([-2.0] * 30) == 1.0


def test_pace_needs_8():
    assert hs.pace_of_aging([0.5] * 7) is None
    assert hs.pace_of_aging([0.5] * 8) == 1.0
    assert hs.pace_of_aging([]) is None


def test_pace_slope_and_clip():
    weekly = [0.02 * i for i in range(30)]    # +0.02 y of body-age gap per week
    want = 1 + 0.02 * 365.2425 / 7
    assert hs.pace_of_aging(weekly) == pytest.approx(round(want, 2), abs=0.01)
    assert hs.pace_of_aging([0.2 * i for i in range(30)]) == 3.0
    assert hs.pace_of_aging([-0.2 * i for i in range(30)]) == -1.0


def test_pace_uses_last_26_points_and_smoothing():
    # a step change 30 weeks ago is outside the 26-point window; the tail is flat
    weekly = [5.0] * 10 + [0.0] * 30
    assert hs.pace_of_aging(weekly) == 1.0
    # 4-week moving average of a one-off spike keeps the slope finite but smaller than the raw jump would give
    spike = [0.0] * 20 + [4.0] + [0.0] * 5
    assert -1.0 <= hs.pace_of_aging(spike) <= 3.0


def test_pace_skips_missing_weeks():
    weekly = [0.3] * 5 + [None] + [0.3] * 5
    assert hs.pace_of_aging(weekly) == 1.0


# ---------------------------------------------------------------- golden (openbodyscore healthspan v1.2)
def rel(got, want, path=""):
    if isinstance(want, float) or isinstance(got, float):
        assert math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-9), f"{path}: {got!r} != {want!r}"
    elif isinstance(want, list):
        assert len(got) == len(want), path
        for i, (g, w) in enumerate(zip(got, want)):
            rel(g, w, f"{path}[{i}]")
    elif isinstance(want, dict):
        assert got.keys() == want.keys(), path
        for k in want:
            rel(got[k], want[k], f"{path}/{k}")
    else:
        assert got == want, path


def test_golden_v12():
    g = json.loads(GOLDEN.read_text())
    got = {
        "steps": [[hs.hr_steps(s, a) for s in (2000, 4000, 6000, T.STEPS_ANCHOR_UNDER60, 12000, 20000)] for a in (35, 60, 72)],
        "moderate_equiv": [hs.moderate_equiv_minutes(30, met) for met in (2.0, 2.99, 3.0, 3.5, 6.0, 8.0)],
        "step_equiv": [hs.step_equivalents(m, T.STRENGTH_MET) for m in (0, 30, 60)]
        + [hs.step_equivalents(45, T.WORKOUT_MET[t]) for t in sorted(T.NONSTEP_TYPES)],
        "activity": [hs.hr_activity(s, w, a) for s, w, a in
                     ((4000, None, 30), (6000, 0, 30), (6000, 40, 30), (9000, 200, 30), (5000, 40, 70))],
        "years": [hs.years_from_hr(x, T.ACTIVITY_CAP_YEARS) for x in (0.3, 0.8, 1.0, 1.3, 3.0)],
        "version": T.ALGO_VERSION,
    }
    assert set(got) == set(g)
    rel(got, g)
