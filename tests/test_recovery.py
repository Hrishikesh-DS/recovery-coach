import math

from recovery_coach.inputs import DayRow
from recovery_coach.recovery import recovery_score
from recovery_coach.stats import logistic

# History patterns: median 4.0, MAD 0.05 -> spread 1.4826*0.05 = 0.0741 (> floor 0.05)
OFFS = [-0.1, -0.05, 0.0, 0.05, 0.1]


def make_history(n=30, hrv_c=4.0, hr_c=50.0, perf_c=0.9, resp_c=14.0):
    rows = []
    for i in range(n):
        o = OFFS[i % 5]
        rows.append(DayRow(
            date=f"d{i}", hrv_ln=hrv_c + o, sleep_hr=hr_c + o * 20,
            rhr=hr_c + 8 + o * 20, perf=perf_c + o, resp=resp_c + o * 5))
    return rows


def spread(mult, floor):
    return max(1.4826 * 0.05 * mult, floor)


HRV_S = spread(1, 0.05)
HR_S = spread(20, 1)      # 1.4826 * 1.0
PERF_S = spread(1, 0.05)
RESP_S = spread(5, 0.5)   # 1.4826 * 0.25 = 0.37 -> floor 0.5


def today_at(hrv_z=0, hr_z=0, perf_z=0, resp_z=0):
    return {
        "hrv_ln": 4.0 + hrv_z * HRV_S,
        "sleep_hr": 50.0 - hr_z * HR_S,
        "rhr": None,
        "perf": 0.9 + perf_z * PERF_S,
        "resp": 14.0 - resp_z * RESP_S,
    }


def test_normal_day_is_57():
    r = recovery_score(today_at(), make_history())
    assert r.score == 57 and r.zone == "yellow" and r.cap_reason is None


def test_all_inputs_positive_half():
    # resp is better than normal, so it counts as 0 (weights 0.60+0.20+0.15 at +0.5)
    r = recovery_score(today_at(0.5, 0.5, 0.5, 0.5), make_history())
    uncapped = logistic(0.95 * 0.5)
    assert r.score == round(uncapped) and r.zone == "green"
    # points sum to uncapped - 57
    assert math.isclose(sum(c.points for c in r.contributors), uncapped - 57, abs_tol=0.3)


def test_weights_redistribute_when_resp_missing():
    t = today_at(hrv_z=1.0, hr_z=0.5, perf_z=-0.5)
    t["resp"] = None
    r = recovery_score(t, make_history())
    zbar = (0.60 * 1.0 + 0.20 * 0.5 + 0.15 * -0.5) / 0.95
    assert r.score == round(logistic(zbar))
    assert {c.name for c in r.contributors} == {"HRV", "Sleeping HR", "Sleep performance"}


def test_hrv_required():
    t = today_at()
    t["hrv_ln"] = None
    r = recovery_score(t, make_history())
    assert r.score is None and r.zone is None
    assert r.reason_missing == "no HRV readings last night"


def test_needs_14_nights():
    r = recovery_score(today_at(), make_history(13))
    assert r.score is None
    assert r.reason_missing == "13 of 14 nights collected"


def test_hrv_cap():
    # HR and perf at +3 (max) keep the uncapped score ~47, so the 33 cap actually binds
    t = today_at(hrv_z=-2.2, hr_z=3, perf_z=3)
    assert logistic((0.6 * -2.2 + 0.2 * 3 + 0.15 * 3) / 1.0) > 40
    r = recovery_score(t, make_history())
    assert r.score == 33 and r.zone == "red"
    assert "HRV" in r.cap_reason
    assert r.uncapped == round(logistic((0.6 * -2.2 + 0.2 * 3 + 0.15 * 3) / 1.0))   # the score before the cap


def test_uncapped_equals_score_without_a_cap():
    r = recovery_score(today_at(0.5, 0.5, 0.5, 0.5), make_history())
    assert r.uncapped == r.score and r.cap_reason is None
    assert recovery_score(today_at(), make_history(n=5)).uncapped is None


def test_hr_cap_and_lowest_cap_wins():
    r = recovery_score(today_at(hrv_z=2, hr_z=-2.5, perf_z=1), make_history())
    assert r.score == 50 and "HR" in r.cap_reason
    t = today_at(hrv_z=-2.5, hr_z=-2.5)
    r = recovery_score(t, make_history())
    assert r.score <= 33 and "HRV" in r.cap_reason  # HRV cap (33) beats HR cap (50)


def test_low_sleep_performance_cap():
    t = today_at(hrv_z=2, hr_z=1)
    t["perf"] = 0.55
    r = recovery_score(t, make_history())
    assert r.score <= 50 and "sleep" in r.cap_reason.lower()


def test_resp_only_counts_when_worse():
    r = recovery_score(today_at(resp_z=1.5), make_history())  # resp below baseline
    resp = next(c for c in r.contributors if c.name == "Respiratory rate")
    assert resp.points == 0
    r = recovery_score(today_at(resp_z=-1.5), make_history())  # resp above baseline
    resp = next(c for c in r.contributors if c.name == "Respiratory rate")
    assert resp.z < 0 and resp.points < 0


def test_robust_to_outlier():
    base = make_history()
    clean = recovery_score(today_at(), base)
    hrv_clean = next(c for c in clean.contributors if c.name == "HRV").baseline
    base[3].hrv_ln = math.log(300)
    dirty = recovery_score(today_at(), base)
    hrv_dirty = next(c for c in dirty.contributors if c.name == "HRV").baseline
    assert abs(hrv_dirty - hrv_clean) / hrv_clean < 0.02


def test_falls_back_to_resting_hr_without_sleep_hr_history():
    hist = make_history()
    for h in hist:
        h.sleep_hr = None
    t = today_at()
    t["sleep_hr"] = None
    t["rhr"] = 58.0
    r = recovery_score(t, hist)
    assert "Resting HR" in {c.name for c in r.contributors}


def test_other_input_with_thin_history_is_missing():
    hist = make_history()
    for h in hist[:20]:
        h.perf = None
    r = recovery_score(today_at(), hist)
    assert "Sleep performance" not in {c.name for c in r.contributors}
    assert r.score is not None


def test_perf_cap_applies_without_perf_baseline():
    hist = make_history()
    for h in hist:
        h.perf = None  # no perf baseline at all
    t = today_at(hrv_z=2, hr_z=1)
    t["perf"] = 0.55
    r = recovery_score(t, hist)
    assert r.score == 50 and "sleep" in r.cap_reason.lower()
    assert "Sleep performance" not in {c.name for c in r.contributors}
