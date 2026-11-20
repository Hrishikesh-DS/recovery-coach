"""Tests for recovery_coach.sleep (all fixtures synthetic)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from recovery_coach.inputs import DayRow, Night, SleepSample
from recovery_coach.sleep import (
    bedtime_tonight, build_nights, naps_between, sleep_debt, sleep_need,
    sleep_quality,
)

CDT = timezone(timedelta(hours=-5))
CST = timezone(timedelta(hours=-6))
IST = timezone(timedelta(hours=5, minutes=30))
UTC6 = timezone(timedelta(hours=-6))


def dt(s, tz=UTC6):
    return datetime.fromisoformat(s).replace(tzinfo=tz) if datetime.fromisoformat(s).tzinfo is None else datetime.fromisoformat(s)


def S(a, b, stage="core", tz=UTC6):
    return SleepSample(dt(a, tz), dt(b, tz), stage)


def mk_night(wake_date, onset_h, wake_h, asleep=420, bouts=1, deep=60, rem=90, eff=0.9, tz=UTC6):
    """Night with onset/wake given as hours relative to wake_date midnight."""
    mid = datetime(wake_date.year, wake_date.month, wake_date.day, tzinfo=tz)
    return Night(wake_date, mid + timedelta(hours=onset_h), mid + timedelta(hours=wake_h),
                 asleep, bouts, deep, rem, asleep - deep - rem, eff)


def row(d, sleep_min):
    return DayRow(date=d, sleep_min=sleep_min)


# ---------- build_nights ----------

@pytest.mark.parametrize("onset, wake, awake, wake_date, utc_wake_date", [
    # US Central falls back at 02:00 CDT: 8 h 15 on the clock, 9 h 15 elapsed
    ("2026-10-31T22:10:00-05:00", "2026-11-01T06:25:00-06:00",
     ("2026-11-01T01:30:00-05:00", "2026-11-01T01:42:00-05:00"), date(2026, 11, 1), date(2026, 11, 1)),
    # Sydney falls back at 03:00 AEDT; the 06:35 wake is still 4 April in UTC, so only its local date gives 5 April
    ("2026-04-04T22:20:00+11:00", "2026-04-05T06:35:00+10:00",
     ("2026-04-05T02:30:00+11:00", "2026-04-05T02:42:00+11:00"), date(2026, 4, 5), date(2026, 4, 4)),
])
def test_dst_night(onset, wake, awake, wake_date, utc_wake_date):
    samples = [SleepSample(datetime.fromisoformat(onset), datetime.fromisoformat(wake), "core"),
               SleepSample(datetime.fromisoformat(awake[0]), datetime.fromisoformat(awake[1]), "awake")]
    assert samples[0].end.astimezone(timezone.utc).date() == utc_wake_date
    nights = build_nights(samples)
    assert len(nights) == 1
    n = nights[0]
    assert n.wake_date == wake_date
    elapsed = (samples[0].end - samples[0].start).total_seconds() / 60
    assert elapsed == pytest.approx(555)   # real time, one hour more than the clocks show
    assert n.asleep_min == pytest.approx(elapsed - 12)
    assert n.awake_bouts == 1


def test_india_offset():
    # a 05:05 wake in India is 23:35 UTC the evening before: the night belongs to the local wake date
    n = build_nights([S("2026-12-20T21:50:00", "2026-12-21T05:05:00", tz=IST)])
    assert n[0].wake.astimezone(timezone.utc).date() == date(2026, 12, 20)
    assert [x.wake_date for x in n] == [date(2026, 12, 21)]
    assert n[0].asleep_min == pytest.approx(435)


def test_nap_not_main():
    nap = S("2026-03-10T15:00:00", "2026-03-10T16:10:00")
    night = S("2026-03-10T23:10:00", "2026-03-11T07:25:00")
    nights = build_nights([nap, night])
    assert len(nights) == 1
    assert nights[0].wake_date == date(2026, 3, 11)
    assert nights[0].onset == night.start
    assert naps_between([nap, night], dt("2026-03-10T07:00:00"), night.start) == pytest.approx(70)
    # a nap before prev_wake is not counted
    assert naps_between([nap, night], dt("2026-03-10T16:30:00"), night.start) == 0


def test_split_night_joined():
    a = S("2026-03-10T23:00:00", "2026-03-11T02:00:00")
    b = S("2026-03-11T02:40:00", "2026-03-11T06:40:00")
    nights = build_nights([a, b])
    assert len(nights) == 1
    n = nights[0]
    assert n.onset == a.start and n.wake == b.end
    assert n.asleep_min == pytest.approx(180 + 240)
    assert n.awake_bouts == 1  # the 40 min gap
    assert n.efficiency == pytest.approx(420 / 460)


def test_split_night_separate():
    a = S("2026-03-10T23:00:00", "2026-03-11T01:00:00")  # 120 min
    b = S("2026-03-11T02:30:00", "2026-03-11T06:30:00")  # 240 min, gap 90
    nights = build_nights([a, b])
    assert len(nights) == 1
    assert nights[0].onset == b.start and nights[0].asleep_min == pytest.approx(240)
    assert naps_between([a, b], dt("2026-03-10T18:00:00"), dt("2026-03-12T00:00:00")) == pytest.approx(120)


def test_watch_off():
    s = [S("2026-03-10T23:00:00", "2026-03-11T06:00:00"),
         S("2026-03-12T23:00:00", "2026-03-13T06:00:00")]
    dates = [n.wake_date for n in build_nights(s)]
    assert dates == [date(2026, 3, 11), date(2026, 3, 13)]
    assert build_nights([]) == []


def test_stages_and_unspecified_asleep():
    s = [S("2026-03-10T23:00:00", "2026-03-11T00:00:00", "core"),
         S("2026-03-11T00:00:00", "2026-03-11T01:00:00", "deep"),
         S("2026-03-11T01:00:00", "2026-03-11T02:00:00", "rem"),
         S("2026-03-11T02:00:00", "2026-03-11T03:00:00", "asleep"),
         S("2026-03-11T03:00:00", "2026-03-11T03:10:00", "awake"),
         S("2026-03-11T03:10:00", "2026-03-11T04:10:00", "core"),
         S("2026-03-10T22:50:00", "2026-03-11T04:20:00", "inbed")]
    n = build_nights(s)[0]
    assert (n.core_min, n.deep_min, n.rem_min) == (pytest.approx(120), pytest.approx(60), pytest.approx(60))
    assert n.asleep_min == pytest.approx(300)  # includes unspecified hour
    assert n.awake_bouts == 1
    assert n.efficiency == pytest.approx(300 / 330)  # inbed span 22:50-04:20


def test_short_awake_not_a_bout():
    s = [S("2026-03-10T23:00:00", "2026-03-11T03:00:00"),
         S("2026-03-11T03:03:00", "2026-03-11T07:00:00")]
    assert build_nights(s)[0].awake_bouts == 0


def test_short_evening_sleep_qualifies_by_onset_only():
    # 2 h at 23:00 is main; 2 h at 15:00 is not; 1 h at 23:00 is too short
    assert build_nights([S("2026-03-10T23:00:00", "2026-03-11T01:00:00")])
    assert build_nights([S("2026-03-10T15:00:00", "2026-03-10T17:00:00")]) == []
    assert build_nights([S("2026-03-10T23:00:00", "2026-03-11T00:00:00")]) == []


def test_evening_nap_with_watch_off_overnight_is_not_a_night():
    nap = S("2026-03-10T18:30:00", "2026-03-10T19:30:00")
    night = S("2026-03-10T23:00:00", "2026-03-11T07:00:00")
    nights = build_nights([nap, night])
    assert [n.wake_date for n in nights] == [date(2026, 3, 11)]
    assert naps_between([nap, night], dt("2026-03-10T07:00:00"), night.start) == pytest.approx(60)
    assert build_nights([nap]) == []


def test_four_hour_daytime_sleep_is_that_dates_night():
    n = build_nights([S("2026-03-10T09:00:00", "2026-03-10T13:00:00")])
    assert [x.wake_date for x in n] == [date(2026, 3, 10)]


# ---------- debt / need ----------

def test_debt_weights():
    goal = 480
    hist = [row(f"2026-03-{d:02d}", goal - 60) for d in range(1, 15)]
    assert sleep_debt(hist, goal) == pytest.approx(60)
    hist = [row(f"2026-03-{d:02d}", goal) for d in range(1, 14)] + [row("2026-03-14", goal - 120)]
    sw = sum(0.75 ** k for k in range(14))
    assert sleep_debt(hist, goal) == pytest.approx(120 / sw)


def test_debt_uses_last_14_usable_rows():
    hist = [row("2026-02-01", 0)] + [row(f"2026-03-{d:02d}", 420) for d in range(1, 15)]
    hist.insert(5, row("2026-03-03b", None))
    assert sleep_debt(hist, 480) == pytest.approx(60)


def test_debt_cap_floor():
    assert sleep_debt([row(f"2026-03-{d:02d}", 600) for d in range(1, 15)], 480) == 0
    assert sleep_debt([row(f"2026-03-{d:02d}", 0) for d in range(1, 15)], 480) == 120
    assert sleep_debt([], 480) == 0
    assert sleep_debt([row("2026-03-01", None)], 480) == 0


def test_need_strain_addon():
    assert sleep_need(480, 30, 21) == pytest.approx(480 + 30 + 42)
    assert sleep_need(480, 30, 6) == 510
    assert sleep_need(480, 30, 7) == 510
    assert sleep_need(480, 30, None) == 510
    assert sleep_need(480, 500, None) == 600  # debt capped


# ---------- quality / bedtime ----------

def test_quality_keys_and_consistency():
    recent = [mk_night(date(2026, 3, d), -1, 7) for d in range(1, 8)]  # identical midpoints
    q = sleep_quality(recent[-1], recent)
    assert set(q) == {"efficiency", "consistency_min", "interruptions", "deep_min", "rem_min"}
    assert q["consistency_min"] == pytest.approx(0)
    assert q["interruptions"] == 1 and q["deep_min"] == 60 and q["rem_min"] == 90
    assert q["efficiency"] == 0.9


def test_quality_midnight_straddle_and_window():
    # midpoints at -1h (23:00-... ) vs +1h around midnight relative to own wake date
    a = mk_night(date(2026, 3, 1), -7, 5)   # mid = -1 h = -60
    b = mk_night(date(2026, 3, 2), -5, 7)   # mid = +1 h = +60
    q = sleep_quality(b, [a, b])
    assert q["consistency_min"] == pytest.approx(60)
    # only latest 7 are used
    old = mk_night(date(2026, 2, 1), -5, 3)
    same = [mk_night(date(2026, 3, d), -1, 7) for d in range(1, 8)]
    assert sleep_quality(same[-1], [old] + same)["consistency_min"] == pytest.approx(0)


def test_bedtime_tonight():
    recent = [mk_night(date(2026, 3, d), -1, 6.5 + (0.5 if d == 7 else 0)) for d in range(1, 8)]
    # wake times: six at 06:30, one at 07:00 -> median 06:30
    bt = bedtime_tonight(recent, 480)
    expect = datetime(2026, 3, 8, 6, 30, tzinfo=UTC6) - timedelta(minutes=480 + 15)
    assert bt == expect
    assert bt.utcoffset() == recent[-1].wake.utcoffset()


def test_consistency_clock_based_across_dst():
    dst = Night(date(2026, 11, 1), datetime.fromisoformat("2026-10-31T23:00:00-05:00"),
                datetime.fromisoformat("2026-11-01T07:00:00-06:00"), 460, 0, 60, 90, 310, 0.9)
    plain = Night(date(2026, 11, 2), datetime.fromisoformat("2026-11-01T23:00:00-06:00"),
                  datetime.fromisoformat("2026-11-02T07:00:00-06:00"), 460, 0, 60, 90, 310, 0.9)
    assert sleep_quality(plain, [dst, plain])["consistency_min"] == pytest.approx(0)


def test_quality_window_anchored_to_scored_night():
    def n(day, mid_h):  # 8 h night centred on mid_h hours after the wake date's midnight
        return mk_night(date(2026, 3, day), mid_h - 4, mid_h + 4)
    early = [n(1, 2), n(2, 3), n(3, 4)]            # midpoints 120/180/240 -> pstdev 48.99
    late = [n(4, 9), n(5, 0), n(6, 12), n(7, 1)]   # very different pattern
    expected = 60 * (2 / 3) ** 0.5
    assert sleep_quality(early[-1], early + late)["consistency_min"] == pytest.approx(expected)
    # scored night absent from recent is still included
    assert sleep_quality(early[-1], early[:-1] + late)["consistency_min"] == pytest.approx(expected)
