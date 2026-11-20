"""Daily pipeline helpers: the wiring rules between the input files and the engine modules (synthetic values)."""
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from recovery_coach.inputs import DayRow, HourStat, HrvReading, Workout
from recovery_coach.pipeline import (
    activity_from, hrv_band, load_for_day, minutes_at_or_above, night_hrv, rel_min, sleeping_hr, strength_minutes,
)

CDT = timezone(timedelta(hours=-5))
CST = timezone(timedelta(hours=-6))
IST = timezone(timedelta(hours=5, minutes=30))
ONSET = datetime(2026, 11, 18, 22, 40, tzinfo=CST)
WAKE = datetime(2026, 11, 19, 6, 40, tzinfo=CST)


def at(h, m=0, day=19, tz=CST):
    return datetime(2026, 11, day, h, m, tzinfo=tz)


def test_night_hrv_uses_window_median_of_ln():
    readings = [HrvReading(at(22, 0, 18), 20.0, "S"),          # before onset: ignored
                HrvReading(at(1), 40.0, "S"), HrvReading(at(3), 50.0, "S"), HrvReading(at(5), 90.0, "S"),
                HrvReading(at(9), 10.0, "S")]                  # after wake: ignored
    assert night_hrv(readings, ONSET, WAKE, "sdnn") == pytest.approx(math.log(50))


def test_night_hrv_uses_only_the_settings_type():
    s = [HrvReading(at(1), 40.0, "S"), HrvReading(at(3), 44.0, "S")]
    r = [HrvReading(at(2), 30.0, "R"), HrvReading(at(4), 36.0, "R")]
    assert night_hrv(s + r, ONSET, WAKE, "rmssd") == pytest.approx((math.log(30) + math.log(36)) / 2)
    assert night_hrv(s, ONSET, WAKE, "rmssd") is None   # never SDNN in an RMSSD baseline: no HRV that night
    assert night_hrv(s + r, ONSET, WAKE, "sdnn") == pytest.approx((math.log(40) + math.log(44)) / 2)
    assert night_hrv(r, ONSET, WAKE, "sdnn") is None
    assert night_hrv([], ONSET, WAKE, "sdnn") is None


def test_sleeping_hr_is_lowest_full_hour_inside_sleep():
    hours = [HourStat(at(22, 0, 18), 40.0), HourStat(at(23, 0, 18), 52.0), HourStat(at(2), 49.5),
             HourStat(at(3), None), HourStat(at(5), 50.0), HourStat(at(6), 45.0),   # 06:00-07:00 ends after wake
             HourStat(at(9), 44.0)]
    assert sleeping_hr(hours, ONSET, WAKE) == 49.5
    assert sleeping_hr([], ONSET, WAKE) is None


def test_rel_min_uses_the_timestamps_own_offset():
    assert rel_min(ONSET, date(2026, 11, 19)) == -80
    assert rel_min(WAKE, date(2026, 11, 19)) == 400
    # night across the DST change: onset in CDT, wake in CST, both wall-clock
    assert rel_min(datetime(2026, 10, 31, 22, 30, tzinfo=CDT), date(2026, 11, 1)) == -90
    assert rel_min(datetime(2026, 11, 1, 6, 30, tzinfo=CST), date(2026, 11, 1)) == 390
    assert rel_min(datetime(2026, 12, 3, 7, 15, 40, tzinfo=IST), date(2026, 12, 3)) == 436


def test_strength_minutes_dedupes_and_writes_zero_on_worn_days():
    s = at(18, 0, 18)
    lift = Workout(s, s + timedelta(minutes=50), "traditionalStrengthTraining", 330.0, 116.0)
    dup = Workout(s + timedelta(minutes=1), s + timedelta(minutes=50, seconds=40), "other", 320.0, None)
    func = Workout(at(7, 0, 18), at(7, 20, 18), "functionalStrengthTraining", None, None)
    run = Workout(at(12, 0, 18), at(12, 40, 18), "running", 400.0, 150.0)
    assert strength_minutes([lift, dup, run], date(2026, 11, 18), worn=True) == 50.0
    assert strength_minutes([lift, func], date(2026, 11, 18), worn=True) == 70.0
    assert strength_minutes([run], date(2026, 11, 18), worn=True) == 0.0
    assert strength_minutes([lift], date(2026, 11, 19), worn=True) == 0.0   # other day
    assert strength_minutes([], date(2026, 11, 18), worn=False) is None


def test_minutes_at_or_above_uses_trimp_durations():
    t0 = at(18, 0, 18)
    readings = [(t0 + timedelta(minutes=i), bpm) for i, bpm in enumerate([150, 150, 120, 150])]
    readings.append((t0 + timedelta(minutes=30), 150))     # gap capped at 5 min, last reading counts 2.5
    assert minutes_at_or_above(readings, 146.0) == pytest.approx(1 + 1 + 5 + 2.5)
    assert minutes_at_or_above([], 146.0) == 0.0


def rows(trimps, last=date(2026, 11, 18)):
    first = last - timedelta(days=len(trimps) - 1)
    return [DayRow(date=(first + timedelta(days=i)).isoformat(), trimp=t, ctl=50.0, atl=50.0)
            for i, t in enumerate(trimps)]


def test_load_for_day_uses_a_date_window_and_carries_ctl():
    hist = rows([100.0] * 27)
    out = load_for_day(hist, date(2026, 11, 19), 200.0)
    assert out["acwr"] == pytest.approx((6 * 100 + 200) / 7 / ((27 * 100 + 200) / 28))
    assert out["ctl"] > 50 and out["atl"] > out["ctl"] and out["tsb"] == pytest.approx(out["ctl"] - out["atl"])
    gap = [r for r in hist if r.date != "2026-11-10"]   # a missing day in the 28-day window: no ratio
    assert load_for_day(gap, date(2026, 11, 19), 200.0)["acwr"] is None
    unknown = load_for_day(hist, date(2026, 11, 19), None)    # no HR data: fitness/fatigue carried, no ratio
    assert (unknown["ctl"], unknown["atl"], unknown["acwr"]) == (50.0, 50.0, None)


def test_activity_from_catches_up_at_most_three_days():
    t = date(2026, 11, 19)
    def logged(*days_back):   # wake rows for every day, activity on the given days before t
        return [DayRow(date=(t - timedelta(days=i)).isoformat(), recovery=60, trimp=100.0 if i in days_back else None)
                for i in range(10, 0, -1)]
    assert activity_from(logged(2, 3, 4), t) == date(2026, 11, 18)        # daily runs: yesterday only
    assert activity_from(logged(1, 2, 3), t) == date(2026, 11, 18)        # a re-run: yesterday again
    assert activity_from(logged(3, 4), t) == date(2026, 11, 17)           # one morning skipped
    assert activity_from(logged(4, 5), t) == date(2026, 11, 16)           # two skipped
    assert activity_from(logged(6, 7), t) == date(2026, 11, 16)           # five days: capped at T - 3
    assert activity_from([], t) == date(2026, 11, 16)
    off = [DayRow(date="2026-11-17", ctl=50.0, atl=50.0)]                 # watch off: load carried, still done
    assert activity_from(off, t) == date(2026, 11, 18)
    assert activity_from([DayRow(date="2026-11-17", still_hr=[60.0])], t) == date(2026, 11, 18)
    assert activity_from([DayRow(date="2026-11-17", journal={"alcohol": True}, vo2=48.0)], t) == date(2026, 11, 16)


def test_hrv_band_in_ms():
    hist = [DayRow(date=(date(2026, 9, 1) + timedelta(days=i)).isoformat(), hrv_ln=math.log(50) + (i % 3 - 1) * 0.1)
            for i in range(30)]
    centre, spread = hrv_band(hist)
    assert centre == pytest.approx(50.0)
    assert spread == pytest.approx((math.exp(math.log(50) + 0.14826) - math.exp(math.log(50) - 0.14826)) / 2)
    assert hrv_band(hist[:13]) == (None, None)
