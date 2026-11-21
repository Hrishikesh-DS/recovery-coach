"""Approximate training effect, recovery hours and benefit label (synthetic samples)."""
from datetime import datetime, timedelta, timezone

from recovery_coach.effect import benefit_label, recovered_pct, training_effect

CST = timezone(timedelta(hours=-6))
T0 = datetime(2026, 11, 21, 8, 0, tzinfo=CST)


def series(fn, seconds, step=5):
    return [(T0 + timedelta(seconds=s), fn(s)) for s in range(0, seconds, step)]


def run(fn, seconds, vo2=50.0, step=5):
    hr = series(fn, seconds, step)
    return training_effect(hr, T0, T0 + timedelta(seconds=seconds), 55, 185, vo2, [0, 0, 0, 0, 0])


def test_te_worked_example():
    r = run(lambda s: 150, 50 * 60)
    assert round(r["peak_epoc"], 1) == 41.4 and round(r["te"], 1) == 3.1 and round(r["rec_h"]) == 25
    assert r["approx"] is True


def test_te_caps_at_5():
    r = run(lambda s: 184, 90 * 60)
    assert r["te"] == 5.0 and r["rec_h"] <= 96


def test_anaerobic_needs_bursts():
    assert run(lambda s: 150, 40 * 60)["te_an"] < 0.5

    def intervals(s):
        cyc = s % 150
        return 185 if cyc < 60 else 130
    assert run(intervals, 8 * 150)["te_an"] >= 2


def test_te_an_none_when_sparse():
    r = run(lambda s: 150, 40 * 60, step=60)
    assert r["te_an"] is None and r["reason"] == "heart rate too sparse for anaerobic effect"
    assert r["te"] > 0


def test_benefit_labels():
    assert benefit_label(3.0, 2.5, [5, 5, 5, 5, 20]) == "Anaerobic"
    assert benefit_label(0.8, 0.0, [30, 0, 0, 0, 0]) == "Recovery"
    assert benefit_label(2.5, 0.2, [5, 40, 5, 0, 0]) == "Base"
    assert benefit_label(3.2, 0.5, [0, 5, 30, 5, 0]) == "Tempo"
    assert benefit_label(3.8, 1.0, [0, 0, 5, 30, 2]) == "Threshold"
    assert benefit_label(4.2, 1.2, [0, 0, 5, 5, 20]) == "VO2 max"


def test_recovered_pct():
    assert recovered_pct(24, 6) == 25 and recovered_pct(24, 30) == 100 and recovered_pct(0, 1) == 100
