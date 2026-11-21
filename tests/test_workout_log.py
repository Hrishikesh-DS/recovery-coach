"""The compact 90-day workout log carried in the daily state (all values synthetic)."""
import json
from datetime import date

import pytest

from recovery_coach.inputs import DayRow, InputError, Settings, read_state, state_crc
from recovery_coach.state import compact_state, next_state
from recovery_coach.workout_log import LOG_DAYS, LogEntry, entry_from_dict, entry_to_dict, merge_log

S = Settings(dob=date(1996, 4, 12), sex="male")
ROWS = [DayRow(date="2026-11-20", recovery=60, trimp=120.0), DayRow(date="2026-11-21", recovery=70)]


def entry(id_="2026-11-21T08:23-0600", **kw):
    base = dict(id=id_, date=id_[:10], kind="running", minutes=50.5, edwards=150.0, banister=88.12, te=3.14,
                rec_h=25.2, hrr60=24.0, ef=1.2345, evo2=41.27, split=[30.0, 18.5, 2.0], best={"1k": 300.4})
    base.update(kw)
    return LogEntry(**base)


def write(tmp_path, doc):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(doc))
    return p


def test_old_state_crc_unchanged(tmp_path):
    doc = compact_state(S, ROWS, None, 60)
    assert "workouts" not in doc and doc["crc"] == state_crc(doc["cols"])
    st = read_state(write(tmp_path, doc))
    assert st.workouts == []


def test_state_round_trip_with_log(tmp_path):
    doc = compact_state(S, ROWS, None, 60, workouts=[entry()])
    assert doc["crc"] == state_crc(doc["cols"], None, doc["workouts"])
    st = read_state(write(tmp_path, doc))
    (e,) = st.workouts
    assert e.id == "2026-11-21T08:23-0600" and e.kind == "running" and e.te == 3.1 and e.ef == 1.234
    assert e.split == [30.0, 18.5, 2.0] and e.best == {"1k": 300.4} and e.hrss is None


def test_crc_detects_changed_log_value(tmp_path):
    doc = compact_state(S, ROWS, None, 60, workouts=[entry()])
    doc["workouts"][0]["te"] = 4.0
    with pytest.raises(InputError, match="checksum mismatch"):
        read_state(write(tmp_path, doc))


def test_merge_log_drops_old_and_replaces_provisional():
    old = entry("2026-08-01T07:00-0500", kind="walking")
    prov = entry(provisional=True, hrr60=None)
    final = entry(hrr60=26.0)
    other = entry("2026-11-20T07:09-0600", kind="traditionalStrengthTraining")
    out = merge_log([old, prov, other], [final], date(2026, 11, 21))
    assert [e.id for e in out] == ["2026-11-20T07:09-0600", "2026-11-21T08:23-0600"]
    assert out[1].provisional is False and out[1].hrr60 == 26.0
    assert LOG_DAYS == 90


def test_entry_to_dict_drops_nulls():
    d = entry_to_dict(entry())
    assert "hrss" not in d and "provisional" not in d and d["banister"] == 88.1 and d["te"] == 3.1
    assert entry_from_dict(d, "state.json", "workouts[0]").minutes == 50.5


def test_next_state_carries_log(tmp_path):
    st = read_state(write(tmp_path, compact_state(S, ROWS, None, 60, workouts=[entry()])))
    doc = next_state(st, [{"date": "2026-11-21", "recovery": 71}], workouts=st.workouts + [entry("2026-11-21T18:00-0600")])
    assert [w["id"] for w in doc["workouts"]] == ["2026-11-21T08:23-0600", "2026-11-21T18:00-0600"]
