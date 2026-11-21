"""Per-workout heart-rate metrics (synthetic samples)."""
import math
from datetime import datetime, timedelta, timezone

from recovery_coach.hr_metrics import (
    azm, banister, dense, end_hr, hr_recovery, hrrc, hrss, intensity_pct, split, zone_minutes,
)

CST = timezone(timedelta(hours=-6))
T0 = datetime(2026, 11, 21, 8, 0, tzinfo=CST)


def series(fn, seconds, step=5, t0=T0):
    return [(t0 + timedelta(seconds=s), fn(s)) for s in range(0, seconds, step)]


def test_hrss_worked_example():
    hr = series(lambda s: 150, 45 * 60)
    end = T0 + timedelta(minutes=45)
    assert round(hrss(hr, T0, end, 55, 185, 1.92, 166.5), 1) == 50.1


def test_zone_minutes_sum_to_duration():
    hr = series(lambda s: 100 + s / 30, 40 * 60)   # 100 -> 180 bpm, all above 50 % of 188
    z = zone_minutes(hr, T0, T0 + timedelta(minutes=40), 188)
    assert len(z) == 5 and math.isclose(sum(z), 40, abs_tol=0.01)
    assert split(z) == [z[0] + z[1], z[2] + z[3], z[4]]


def test_intensity_pct_bounds():
    hr = series(lambda s: 150, 30 * 60)
    end = T0 + timedelta(minutes=30)
    b = banister(hr, T0, end, 55, 185, 1.92)
    x = intensity_pct(b, 30, 1.92)
    assert 0 < x <= 100
    assert math.isclose(intensity_pct(30 * math.e ** 1.92, 30, 1.92), 100)


def recovery_samples(drop60=20, drop120=35):
    before = series(lambda s: 170, 600, t0=T0)
    end = T0 + timedelta(seconds=600)

    def after(s):
        if s <= 60:
            return 170 - drop60 * s / 60
        return 170 - drop60 - (drop120 - drop60) * (s - 60) / 60
    return before + series(after, 181, t0=end), end


def test_hr_recovery_basic():
    hr, end = recovery_samples()
    r = hr_recovery(hr, end, 188, end + timedelta(minutes=5))
    assert round(r["hrr60"]) == 20 and round(r["hrr120"]) == 35 and r["end_hr"] == 170
    assert r["flag"] is None and r["reason"] is None and r["provisional"] is False


def test_hr_recovery_low_flag():
    hr, end = recovery_samples(drop60=10, drop120=18)
    assert hr_recovery(hr, end, 188, end + timedelta(minutes=5))["flag"] == "low"


def test_hr_recovery_sparse():
    hr, end = recovery_samples()
    sparse = hr[::12]   # one reading a minute
    r = hr_recovery(sparse, end, 188, end + timedelta(minutes=5))
    assert r["hrr60"] is None and r["hrr120"] is None and r["reason"] == "heart rate too sparse"
    assert not dense(sparse) and dense(hr)


def test_hr_recovery_too_recent():
    hr, end = recovery_samples()
    now = end + timedelta(seconds=60)
    r = hr_recovery([x for x in hr if x[0] <= now], end, 188, now)
    assert r["provisional"] is True and r["hrr120"] is None and r["reason"] == "ended too recently"


def test_hr_recovery_not_zone4():
    before = series(lambda s: 140, 600)
    end = T0 + timedelta(seconds=600)
    hr = before + series(lambda s: 140 - s / 6, 181, t0=end)
    r = hr_recovery(hr, end, 188, end + timedelta(minutes=5))
    assert r["hrr60"] is None and r["reason"] == "didn't reach zone 4"


def test_end_hr_takes_highest_10s_in_last_two_minutes():
    hr = series(lambda s: 175 if s >= 590 else 150, 600)
    assert end_hr(hr, T0 + timedelta(seconds=600)) == 175


def test_azm_credits():
    hr = series(lambda s: 105 if s < 600 else 150, 1200)   # rest 55, max 185: 0.38 HRR then 0.73 HRR
    assert round(azm(hr, T0, T0 + timedelta(minutes=20), 55, 185)) == 20
    hr2 = series(lambda s: 115, 600)                     # 0.46 HRR: fat burn
    assert round(azm(hr2, T0, T0 + timedelta(minutes=10), 55, 185)) == 10


def test_hrrc_interval():
    def f(s):
        cyc = s % 240
        return 175 if cyc < 120 else 140
    hr = series(f, 960)
    x = hrrc(hr, T0, T0 + timedelta(seconds=960), 166)
    assert x is not None and round(x) == 35
    assert hrrc(series(lambda s: 150, 600), T0, T0 + timedelta(seconds=600), 166) is None
