"""Personal heart-rate zones (zones.py) and their use across the engine."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from recovery_coach import hr_metrics as hm
from recovery_coach import zones
from recovery_coach.helper import main as helper_main
from recovery_coach.inputs import InputError, _settings
from recovery_coach.strain import trimp, zone_weight

TZ = timezone(timedelta(hours=-6))
APPLE = {"hrmax": 189.0, "rest": 69.0, "rest_mean": 68.4}


def rows(rhr: float, until: date, days: int = 30):
    return [SimpleNamespace(date=(until - timedelta(days=i)).isoformat(), rhr=rhr, sleep_hr=None)
            for i in range(days)]


def test_default_is_percent_of_hrmax():
    assert zones.limits(None, 200.0) == pytest.approx([100, 120, 140, 160, 180])


def test_from_apple_fits_reserve_zones():
    assert zones.from_apple([141, 153, 165, 177]) == (189.0, 69.0)


@pytest.mark.parametrize("bad", [[141, 153, 165], [141, 153, 153, 177], [177, 165, 153, 141]])
def test_from_apple_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        zones.from_apple(bad)


def test_anchor_day_matches_the_watch():
    lim = zones.limits(APPLE, 188.6, rows(68.4, date(2026, 11, 24)), date(2026, 11, 24))
    assert [round(x) for x in lim[1:]] == [141, 153, 165, 177]


def test_zones_follow_resting_hr():
    d = date(2027, 1, 1)
    lower = zones.limits(APPLE, 188.6, rows(64.4, d), d)        # resting HR 4 bpm lower
    assert lower[1] == pytest.approx(141 - 4 * 0.4)              # zone 2 moves by (1 - 60 %) of the change
    assert lower[4] == pytest.approx(177 - 4 * 0.1)


def test_no_resting_data_keeps_the_anchor():
    lim = zones.limits(APPLE, 188.6, [], date(2027, 1, 1))
    assert [round(x) for x in lim[1:]] == [141, 153, 165, 177]


def test_rest_mean_falls_back_to_sleeping_hr():
    r = [SimpleNamespace(date="2026-11-20", rhr=None, sleep_hr=58.0)]
    assert zones.rest_mean(r, date(2026, 11, 24)) == 58.0
    assert zones.rest_mean(r, date(2027, 1, 24)) is None


def test_zone_of():
    lim = [129, 141, 153, 165, 177]
    assert [zones.zone_of(b, lim) for b in (120, 129, 140, 141, 158, 165, 200)] == [0, 1, 1, 2, 3, 4, 5]


def test_edwards_weight_uses_limits():
    lim = [129, 141, 153, 165, 177]
    assert zone_weight(155, 188.6) == 4          # 82 % of HRmax
    assert zone_weight(155, 188.6, lim) == 3     # Apple's zone 3


def test_zone_minutes_and_trimp_use_limits():
    t0 = datetime(2026, 11, 23, 18, 0, tzinfo=TZ)
    hr = [(t0 + timedelta(minutes=i), 155.0) for i in range(11)]
    lim = [129, 141, 153, 165, 177]
    assert hm.zone_minutes(hr, t0, t0 + timedelta(minutes=10), 188.6, lim) == pytest.approx([0, 0, 10, 0, 0])
    assert hm.zone_minutes(hr, t0, t0 + timedelta(minutes=10), 188.6) == pytest.approx([0, 0, 0, 10, 0])
    assert trimp(hr, [], 188.6, lim)[0] < trimp(hr, [], 188.6)[0]


def test_hr_recovery_gate_stays_percent_of_hrmax():
    t0 = datetime(2026, 11, 23, 18, 0, tzinfo=TZ)
    end = t0 + timedelta(minutes=10)
    hr = [(t0 + timedelta(seconds=5 * i), 158.0 if t0 + timedelta(seconds=5 * i) <= end else 130.0)
          for i in range(12 * 15)]
    assert hm.hr_recovery(hr, end, 188.6, end + timedelta(minutes=10))["reason"] != "didn't reach zone 4"


def test_settings_validate_zones():
    base = {"dob": "1999-01-01", "sex": "male"}
    assert _settings(base, "state.json").zones is None
    assert _settings({**base, "zones": APPLE}, "state.json").zones == APPLE
    with pytest.raises(InputError):
        _settings({**base, "zones": {"hrmax": 189}}, "state.json")
    with pytest.raises(InputError):
        _settings({**base, "zones": {"hrmax": 110, "rest": 115}}, "state.json")


def test_helper_zones(tmp_path, capsys):
    import json
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"settings": {"dob": "1999-01-01", "sex": "male"},
                                 "rows": [{"date": "2026-11-20", "rhr": 70}, {"date": "2026-11-23", "rhr": 68}],
                                 "weeks": []}))
    assert helper_main(["zones", str(state), "2026-11-24", "141", "153", "165", "177"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert json.loads(out[0]) == {"hrmax": 189.0, "rest": 69.0, "rest_mean": 69.0}
    assert out[1] == "limits on 2026-11-24: 129, 141, 153, 165, 177"
    assert helper_main(["zones", str(state), "2026-11-24", "177", "165", "153", "141"]) == 2


def test_backfill_with_personal_zones(tmp_path):
    """Reserve zones sit above % of HRmax zones at the same heart rate, so the same history scores less TRIMP,
    and the settings keep the zones."""
    import json
    from fixtures.synthetic_90d import generate as gen
    from recovery_coach.cli import main
    out = {}
    for name, z in (("plain", None), ("apple", {"hrmax": 189.0, "rest": 69.0, "rest_mean": 68.0})):
        d = tmp_path / name
        gen.write_backfill(d)
        if z:
            st = json.loads((d / "state.json").read_text())
            st["settings"]["zones"] = z
            (d / "state.json").write_text(json.dumps(st))
        assert main(["backfill", "--in", str(d), "--out", str(d / "out")]) == 0
        out[name] = json.loads((d / "out" / "result.json").read_text())
    plain, apple = (sum(r["trimp"] for r in out[n]["rows"]) for n in ("plain", "apple"))
    assert apple < plain
    assert out["apple"]["state_next"]["settings"]["zones"]["rest"] == 69.0
