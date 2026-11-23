"""Weekly report assembly: windows, best/worst day, focus rules, weekly snapshots (synthetic rows)."""
import math
from datetime import date, timedelta

import pytest

from recovery_coach.card import weekly_card
from recovery_coach.whoop_age import Item, WhoopAge
from recovery_coach.inputs import DayRow, Settings, State, Week
from recovery_coach.weekly import focus, weekly_report

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


def ba(*items):
    return WhoopAge(27.0 + sum(y for _, y in items), sum(y for _, y in items),
                    [Item(n, 1.0, "", 0.0, y) for n, y in items], [])


def test_focus_rules_in_order():
    rows = mk_rows()
    rows[-1].debt_min = 75.0
    text = focus(rows, ba(("Resting HR", 1.0)), 480)
    assert text.startswith("Protect sleep") and "pm" in text
    rows[-1].debt_min, rows[-1].acwr = 20.0, 1.7
    assert focus(rows, ba(("Resting HR", 1.0)), 480).startswith("Ease the load")
    rows[-1].acwr = 1.1
    assert "steps" in focus(rows, ba(("Resting HR", 0.4), ("Steps", 1.2), ("VO2 max", -2.0)), 480).lower()
    assert "VO₂ max" in focus(rows, ba(("VO2 max", 2.0), ("Steps", 1.2)), 480)
    assert focus(rows, ba(("Resting HR", -0.4)), 480) is None


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
    assert any(m[0] == "Hours of sleep" and "only 25 days" in m[1]                 # too little data: says so
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


def test_weekly_body_age_is_the_whoop_style_age():
    out = weekly_report(State(SETTINGS, mk_rows(n=70), []), END)
    body = out["card"]["body_age"]
    assert body["model"] == "whoop-v1"
    assert [i["name"] for i in body["items"]][:3] == ["Hours of sleep", "Sleep consistency", "Steps"]
    assert body["body_age"] - body["age"] == pytest.approx(sum(i["years"] for i in body["items"]), abs=0.05)
    assert body["pace"] == pytest.approx(1.0) and body["pace_reason"] is None      # steady habits
    assert out["week"]["delta"] == pytest.approx(body["body_age"] - body["age"], abs=0.01)
    assert "Hours of sleep" in out["week"]["items"] and "Zones 1-3" not in out["week"]["items"]   # no zone data
