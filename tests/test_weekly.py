"""Weekly report assembly: windows, best/worst day, focus rules, weekly snapshots (synthetic rows)."""
import math
from datetime import date, timedelta

import pytest

from recovery_coach.card import weekly_card
from recovery_coach.healthspan import BodyAgeResult, Item
from recovery_coach.healthspan import pace_of_aging
from recovery_coach.inputs import DayRow, Settings, State, Week
from recovery_coach.weekly import focus, pace_points, weekly_report

END = date(2026, 11, 15)   # a Sunday
SETTINGS = Settings(dob=date(1996, 4, 12), sex="male", questions=["alcohol"])


def day(i):
    return (END - timedelta(days=i)).isoformat()


def mk_rows(n=70, gap=()):
    out = []
    for i in range(n - 1, -1, -1):
        if i in gap:
            continue
        this_week = i < 7
        out.append(DayRow(date=day(i), recovery=80 if this_week else 50, zone="green" if this_week else "yellow",
                          hrv_ln=math.log(60 if this_week else 50) + (i % 3 - 1) * 0.05, rhr=50.0 + i % 2,
                          sleep_min=440.0, perf=0.9, strain=12.0 if this_week else 10.0, trimp=100.0, ctl=90.0,
                          atl=95.0, tsb=-5.0, acwr=1.1, stress=1.2, debt_min=20.0, onset_min=-75, wake_min=405,
                          steps=8000.0, strength_min=0.0, sleep_hr=48.0, resp=14.5,
                          journal={"alcohol": i % 4 == 0}))
    return out


def test_weekly_windows_and_series():
    rows = mk_rows(gap=(3,))
    rows[-1].recovery, rows[-1].zone = 95, "green"   # END: the best day
    out = weekly_report(State(SETTINGS, rows, []), END)
    card = out["card"]
    assert (card["week_start"], card["week_end"]) == ("2026-11-09", "2026-11-15")
    assert card["averages"]["recovery"] == pytest.approx((80 * 5 + 95) / 6)
    assert card["prior"]["recovery"] == 50 and card["averages"]["strain"] == 12.0
    assert card["averages"]["hrv"] == pytest.approx(60, abs=3) and card["prior"]["hrv"] == pytest.approx(50, abs=3)
    assert [s["date"] for s in card["series"]] == [day(i) for i in range(27, -1, -1)]   # one per calendar day
    assert card["series"][-4]["recovery"] is None                                      # the missing day
    assert card["best_day"]["date"] == END.isoformat() and card["best_day"]["recovery"] == 95
    assert card["best_day"]["why"]
    assert card["worst_day"]["recovery"] == 80
    assert len(card["load"]) == 28 and card["trends"]["hrv"][0]["value"] == pytest.approx(50, abs=3)
    assert out["week"]["date"] == END.isoformat()
    assert len(weekly_card(card)) < 24_000


def test_weekly_default_date_is_latest_row():
    out = weekly_report(State(SETTINGS, mk_rows(), []), None)
    assert out["card"]["week_end"] == END.isoformat()


ALL = ("sleep_duration", "sleep_regularity", "activity", "vo2max", "rhr", "body_comp")


def test_pace_points_place_snapshots_on_a_weekly_grid():
    sundays = [Week(date(2026, 9, 20) + timedelta(weeks=i), float(i), ALL) for i in range(6)]
    del sundays[2]                                        # a week without a snapshot
    pts = pace_points(sundays, Week(date(2026, 10, 29), 9.0, ALL))   # Thursday after the last Sunday: its own slot
    assert pts == [0.0, 1.0, None, 3.0, 4.0, 5.0, 9.0]
    pts = pace_points(sundays, Week(date(2026, 10, 25), 7.0, ALL))   # same date as the last snapshot: replaces it
    assert pts == [0.0, 1.0, None, 3.0, 4.0, 7.0]
    assert pace_points([], Week(END, None, ())) == [None]


def test_pace_uses_only_the_trailing_run_with_the_latest_item_set():
    """Items phase in as their data reaches 30 days; a snapshot with a different set of items is a different
    measurement, so Pace waits for 8 snapshots with the same set."""
    def items(k):
        return ALL[:3] if k < 6 else ALL[:4] if k < 10 else ALL

    weeks, paces = [], []
    for k in range(20):
        current = Week(date(2026, 1, 4) + timedelta(weeks=k), -1.0 - 0.3 * min(k, 10), items(k))
        pts = pace_points(weeks, current)
        paces.append(pace_of_aging(pts))
        weeks.append(current)
        assert len([p for p in pts if p is not None]) == k - (0 if k < 6 else 6 if k < 10 else 10) + 1
    assert all(p is None for p in paces[:17])             # the full set starts at week 10: 8 snapshots at week 17
    assert all(p is not None and -1 < p < 3 for p in paces[17:])
    legacy = [Week(date(2026, 1, 4) + timedelta(weeks=k), -2.0, None) for k in range(10)]
    assert pace_points(legacy, Week(date(2026, 3, 15), -2.0, ALL)) == [-2.0]   # unknown items never join a run


def ba(*items):
    return BodyAgeResult(27.0 + 0.6 * sum(y for _, y in items), [Item(n, 1.0, y) for n, y in items], [])


def test_focus_rules_in_order():
    rows = mk_rows()
    rows[-1].debt_min = 75.0
    text = focus(rows, ba(("rhr", 1.0)), 480)
    assert text.startswith("Protect sleep") and "pm" in text
    rows[-1].debt_min, rows[-1].acwr = 20.0, 1.7
    assert focus(rows, ba(("rhr", 1.0)), 480).startswith("Ease the load")
    rows[-1].acwr = 1.1
    assert "steps" in focus(rows, ba(("rhr", 0.4), ("activity", 1.2), ("vo2max", -2.0)), 480).lower()
    assert focus(rows, ba(("rhr", -0.4)), 480) is None
    assert focus(rows, BodyAgeResult(None), 480) is None


def test_weekly_merges_long_and_daily_state_daily_wins():
    rows = mk_rows(n=70)
    daily = [r for r in rows if r.date > day(40)]                          # the daily doc: last 40 days
    long = [DayRow(**{**r.__dict__, "recovery": 10, "zone": "red"}) for r in rows if r.date <= day(5)]  # stale
    out = weekly_report(State(SETTINGS, daily, []), END, long=State(None, long, [Week(END - timedelta(weeks=1),
                                                                                      -1.0, ("rhr",))]))
    card = out["card"]
    assert card["averages"]["recovery"] == 80 and card["prior"]["recovery"] == 50   # overlap: the daily doc wins
    assert card["series"][0]["recovery"] == 50
    cols = out["state_long_next"]["cols"]
    assert cols["date"][0] == day(69) and cols["date"][-1] == END.isoformat()      # long-only days kept
    assert cols["recovery"][-1] == 80 and cols["recovery"][0] == 10
    assert [w["date"] for w in out["state_long_next"]["weeks"]] == [(END - timedelta(weeks=1)).isoformat(),
                                                                     END.isoformat()]
    assert "settings" not in out["state_long_next"]
    alone = weekly_report(State(SETTINGS, daily[-25:], []), END)                    # no long doc: daily alone
    assert alone["card"]["averages"]["recovery"] == 80
    assert any(m[0] == "sleep_duration" and "25 nights" in m[1]                    # too little data: says so
               for m in alone["card"]["body_age"]["missing"])


# ---------- training and race section ----------

from recovery_coach.workout_log import LogEntry  # noqa: E402


def wk_log(outdoor=True):
    out = []
    for i in range(0, 40, 2):
        d = day(i)
        out.append(LogEntry(id=f"{d}T07:00-0600", date=d, kind="running", minutes=45.0, indoor=not outdoor,
                            banister=80.0, edwards=120.0, evo2=44.0 + (i % 3), secs=2700.0, dist=7500.0,
                            split=[30.0, 12.0, 3.0], best={"1k": 290.0, "mile": 480.0, "5k": 1560.0},
                            avg_hr=150.0, power=240.0))
    return out


def test_weekly_training_section_present():
    out = weekly_report(State(SETTINGS, mk_rows(), [], wk_log()), END)
    tr = out["card"]["training"]
    assert set(tr) >= {"week_wload", "monotony", "tstrain", "split", "pi", "focus_short", "status", "race", "uth_vo2"}
    assert tr["week_wload"] == 320.0 and tr["split"] == [120.0, 48.0, 12.0]
    race = tr["race"]
    assert race["vdot"] is not None and set(race["predictions"]) == {"5k", "10k", "half", "marathon"}
    assert race["predictions"]["5k"] < race["predictions"]["10k"] < race["predictions"]["marathon"]
    assert set(race["paces"]) == {"easy", "marathon", "threshold", "interval", "repetition"}
    assert race["riegel"]["10k"] > 1560


def test_weekly_race_none_without_outdoor_runs():
    tr = weekly_report(State(SETTINGS, mk_rows(), [], wk_log(outdoor=False)), END)["card"]["training"]
    assert tr["race"]["vdot"] is None and tr["race"]["predictions"] is None and tr["race"]["paces"] is None


def test_weekly_body_age_unchanged():
    a = weekly_report(State(SETTINGS, mk_rows(), []), END)["card"]["body_age"]
    b = weekly_report(State(SETTINGS, mk_rows(), [], wk_log()), END)["card"]["body_age"]
    assert a == b
