from recovery_coach.inputs import DayRow
from recovery_coach.monitor import health_flags


def _hist(n=30):
    rows = []
    for i in range(n):
        j = (i % 3) - 1  # small variation -> MAD > 0
        rows.append(DayRow(
            date=f"2026-10-{i + 1:02d}", hrv_ln=4.0 + 0.1 * j, sleep_hr=50 + j,
            rhr=55 + j, resp=15 + 0.5 * j, spo2=96 + 0.5 * j, wtemp=0.1 * j,
        ))
    return rows


NORMAL = dict(hrv_ln=4.0, sleep_hr=50, rhr=55, resp=15, spo2=96, wtemp=0.0)


def test_two_signals_flag():
    today = dict(NORMAL, hrv_ln=3.0, sleep_hr=60)
    flags = health_flags(today, _hist())
    assert flags == ["hrv_low", "sleep_hr_high", "illness_warning"]


def test_one_signal_no_flag():
    flags = health_flags(dict(NORMAL, resp=20), _hist())
    assert flags == ["resp_high"]


def test_good_direction_not_flagged():
    today = dict(NORMAL, hrv_ln=5.0, sleep_hr=40, resp=10, spo2=99, wtemp=-1.0)
    assert health_flags(today, _hist()) == []


def test_missing_values_and_thin_history_skipped():
    assert health_flags(dict(NORMAL, hrv_ln=None, resp=None), _hist()) == []
    assert health_flags(dict(NORMAL, hrv_ln=1.0, sleep_hr=90), _hist(10)) == []


def test_falls_back_to_rhr_without_sleep_hr():
    hist = _hist()
    for r in hist:
        r.sleep_hr = None
    flags = health_flags(dict(NORMAL, sleep_hr=None, rhr=70, spo2=90), hist)
    assert flags == ["sleep_hr_high", "spo2_low", "illness_warning"]
