"""The compact columnar state (state_next): round trip, retention, precision, validation (synthetic rows)."""
import json
from datetime import date, timedelta

import pytest

from recovery_coach.inputs import DayRow, InputError, Settings, State, Week, read_state
from recovery_coach.state import DAILY_DAYS, STATE_FIELDS, compact_state, long_state, next_state

SETTINGS = Settings(dob=date(1996, 4, 12), sex="male", questions=["alcohol", "late meal"], weight_kg=74.0,
                    height_m=1.78)
LAST = date(2026, 11, 19)


def day(i):
    return (LAST - timedelta(days=i)).isoformat()


def full_rows(n=200):
    rows = []
    for i in range(n - 1, -1, -1):
        rows.append(DayRow(
            date=day(i), recovery=40 + i % 50, zone="yellow", hrv_ln=4.012345 + i / 1000, sleep_hr=50.04, rhr=54.0,
            resp=14.83, spo2=96.24, wtemp=35.637, sleep_min=452.4, need_min=500.0, perf=0.91234, debt_min=33.6,
            onset_min=-75, wake_min=405, strain=12.345, trimp=150.06, stress=0.854, still_hr=[65.44, 70.0],
            ctl=100.04, atl=110.06, tsb=-10.02, acwr=1.0525, steps=8123.4, mvpa_min=40.0, vig_min=5.0,
            strength_min=50.6, vo2=49.25 if i % 10 == 0 else None, flags=["hrv_low"] if i == 3 else [],
            wload=88.06, srpe=315.4,
            journal={"alcohol": i % 2 == 0, "late meal": False} if i < 30 else {}))
    return rows


def write(tmp_path, doc):
    p = tmp_path / "state.json"
    p.write_text(json.dumps(doc))
    return p


def test_compact_keeps_engine_fields_for_their_days_at_their_precision(tmp_path):
    doc = compact_state(SETTINGS, full_rows(), [])
    cols = doc["cols"]
    assert len(cols["date"]) == 180 and cols["date"][-1] == LAST.isoformat()   # trimmed to 180 days
    for name in ("zone", "tsb", "need_min", "mvpa_min", "vig_min", "journal"):
        assert name not in cols                                                # derived or read by nothing
    for name, (_, days) in STATE_FIELDS.items():
        assert len(cols.get(name, [])) <= days, name                           # end-aligned, kept for its days
    assert len(cols["hrv_ln"]) == 60 and len(cols["still_hr"]) == 15 and len(cols["sleep_min"]) == 180
    assert cols["hrv_ln"][-1] == 4.012 and cols["sleep_hr"][-1] == 50 and cols["resp"][-1] == 14.8
    assert cols["wtemp"][-1] == 35.6 and cols["perf"][-1] == 0.912 and cols["sleep_min"][-1] == 452
    assert cols["steps"][-1] == 8123 and cols["strain"][-1] == 12.3 and cols["trimp"][-1] == 150.1
    assert cols["ctl"][-1] == 100 and cols["still_hr"][-1] == [65.4, 70]
    assert len(cols["wload"]) == 29 and cols["wload"][-1] == 88.1 and cols["srpe"][-1] == 315
    assert cols["journal:alcohol"][0] == 0 and len(cols["journal:alcohol"]) == 30   # leading days without answers
    assert cols["flags"] == [["hrv_low"], None, None, None]                       # only the last days are read
    assert doc["settings"]["dob"] == "1996-04-12" and doc["settings"]["questions"] == ["alcohol", "late meal"]


def test_compact_round_trip(tmp_path):
    rows = full_rows(40)
    st = read_state(write(tmp_path, compact_state(SETTINGS, rows, [Week(date(2026, 11, 15), -1.5, ("rhr",))])))
    assert st.settings == SETTINGS and st.weeks == [Week(date(2026, 11, 15), -1.5, ("rhr",))]
    assert [r.date for r in st.rows] == [r.date for r in rows]
    last = st.rows[-1]
    assert (last.recovery, last.zone, last.hrv_ln, last.tsb) == (40, "yellow", 4.012, pytest.approx(-10.1))
    assert last.journal == {"alcohol": True, "late meal": False} and last.need_min is None
    assert st.rows[0].hrv_ln is not None and st.rows[0].still_hr == [] and st.rows[0].sleep_min == 452  # 15-day still_hr
    assert st.rows[-4].flags == ["hrv_low"] and st.rows[-1].still_hr == [65.4, 70.0]


def test_cols_validation(tmp_path):
    base = {"settings": {"dob": "1996-04-12", "sex": "male"}}
    cases = [
        ({"cols": {"date": ["2026-11-18"]}, "rows": []}, "both"),
        ({"cols": {"recovery": [50]}}, "date"),
        ({"cols": {"date": ["2026-11-18"], "recovery": [50, 60]}}, "recovery"),
        ({"cols": {"date": ["2026-11-18"], "hrv": [4.0]}}, "hrv"),
        ({"cols": {"date": ["2026-11-18", "2026-11-18"]}}, "duplicate"),
        ({"cols": {"date": ["2026-11-18"], "journal:alcohol": ["yes"]}}, "journal:alcohol"),
        ({"cols": {"date": ["2026-11-18"], "hrv_ln": "4.0"}}, "hrv_ln"),
    ]
    for extra, words in cases:
        with pytest.raises(InputError) as e:
            read_state(write(tmp_path, {**base, **extra}))
        assert e.value.file == "state.json" and words in str(e.value), (extra, str(e.value))
    st = read_state(write(tmp_path, {**base, "cols": {"date": ["2026-11-17", "2026-11-18"], "recovery": [80],
                                                      "journal:alcohol": [1]}}))
    assert [r.recovery for r in st.rows] == [None, 80]                     # a short column covers the latest dates
    assert st.rows[1].zone == "green" and st.rows[1].journal == {"alcohol": True} and st.rows[0].journal == {}


def test_next_state_merges_patches_and_settings():
    st = State(SETTINGS, full_rows(5), [])
    doc = next_state(st, [{"date": day(0), "strain": 15.0}, {"date": (LAST + timedelta(days=1)).isoformat(),
                                                             "recovery": 70}], {"hrmax": 190.0})
    assert doc["cols"]["date"][-1] == "2026-11-20" and doc["cols"]["recovery"][-1] == 70
    assert doc["cols"]["strain"][-2:] == [15, None] and doc["settings"]["hrmax"] == 190


def test_daily_and_long_documents():
    rows = full_rows()
    daily = next_state(State(SETTINGS, rows, [Week(date(2026, 11, 15), -1.5, ("rhr",))]), [])
    assert set(daily) == {"settings", "cols", "crc"}                           # snapshots live in the long doc
    assert len(daily["cols"]["date"]) == DAILY_DAYS and len(daily["cols"]["steps"]) == DAILY_DAYS
    assert len(daily["cols"]["still_hr"]) == 15 and len(daily["cols"]["trimp"]) == 29
    long = long_state(rows, [Week(date(2026, 11, 15), -1.5, ("rhr",))])
    assert set(long) == {"cols", "weeks", "crc"} and len(long["cols"]["date"]) == 180
    assert long["weeks"] == [{"date": "2026-11-15", "delta": -1.5, "items": ["rhr"]}]
    assert len(json.dumps(daily)) < len(json.dumps(long))


MISMATCH = "checksum mismatch (the state was not copied exactly; copy it again)"


def test_checksum_round_trip_and_missing(tmp_path):
    doc = next_state(State(SETTINGS, full_rows(70), []), [])
    assert isinstance(doc["crc"], int)
    assert len(read_state(write(tmp_path, doc)).rows) == DAILY_DAYS
    del doc["crc"]                                             # hand-built: accepted unchecked
    assert len(read_state(write(tmp_path, doc)).rows) == DAILY_DAYS


@pytest.mark.parametrize("damage", ["drop", "change", "drop_weeks"])
def test_checksum_catches_a_copying_slip(tmp_path, damage):
    doc = long_state(full_rows(40), [Week(date(2026, 11, 15), -1.5, ("rhr",))])
    if damage == "drop":
        doc["cols"]["recovery"].pop(7)             # one value lost: the column would silently shift by a day
    elif damage == "change":
        doc["cols"]["hrv_ln"][3] += 0.001
    else:
        del doc["weeks"]
    p = tmp_path / "state_long.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(InputError) as e:
        read_state(p, settings_required=False)
    assert str(e.value) == f"state_long.json: {MISMATCH}"
    p = write(tmp_path, {"settings": next_state(State(SETTINGS, [], []), [])["settings"], **doc})
    with pytest.raises(InputError) as e:
        read_state(p)
    assert str(e.value) == f"state.json: {MISMATCH}"


def test_checksum_survives_a_store_that_rewrites_whole_numbers(tmp_path):
    doc = long_state(full_rows(20), [Week(date(2026, 11, 15), -3.0, ("rhr",))])
    assert doc["weeks"][0]["delta"] == -3.0
    doc["weeks"][0]["delta"] = -3                              # how a JavaScript store writes -3.0
    doc["cols"]["recovery"] = [float(v) if v is not None else None for v in doc["cols"]["recovery"]]
    p = tmp_path / "state_long.json"
    p.write_text(json.dumps(doc))
    assert len(read_state(p, settings_required=False).rows) == 20


def test_checksum_validation(tmp_path):
    doc = next_state(State(SETTINGS, full_rows(5), []), [])
    with pytest.raises(InputError) as e:
        read_state(write(tmp_path, {**doc, "crc": "12"}))
    assert "crc" in str(e.value)
    rows_form = {"settings": doc["settings"], "rows": [{"date": day(0)}], "crc": doc["crc"]}
    with pytest.raises(InputError) as e:
        read_state(write(tmp_path, rows_form))
    assert "crc" in str(e.value)


def test_long_document_reads_without_settings(tmp_path):
    from recovery_coach.inputs import read_state_long
    p = tmp_path / "state_long.json"
    p.write_text(json.dumps(long_state(full_rows(10), [Week(date(2026, 11, 15), -1.5, ("rhr",))])))
    st = read_state_long(p)
    assert st.settings is None and len(st.rows) == 10 and st.weeks[0].items == ("rhr",)


def test_round_trip_new_columns(tmp_path):
    rows = full_rows(200)
    for i, r in enumerate(rows):
        r.z13_min, r.z45_min, r.lbm_pct = 40.0 + i % 3, 12.0, (77.2 if i == 150 else None)
    back = read_state(write(tmp_path, compact_state(Settings(dob=date(1990, 1, 1), sex="male"), rows, None, 180)))
    by = {r.date: r for r in back.rows}
    last = rows[-1]
    assert by[last.date].z13_min == last.z13_min and by[last.date].z45_min == 12.0
    assert by[rows[150].date].lbm_pct == 77.2
    assert by[rows[-180].date].sleep_hr is not None      # sleeping HR now kept for 180 days


def test_old_state_without_new_columns_loads(tmp_path):
    rows = full_rows(30)
    doc = compact_state(Settings(dob=date(1990, 1, 1), sex="male"), rows, None, 60)
    for k in ("z13_min", "z45_min", "lbm_pct"):
        doc["cols"].pop(k, None)
    from recovery_coach.inputs import state_crc
    doc["crc"] = state_crc(doc["cols"])
    back = read_state(write(tmp_path, doc))
    assert all(r.z13_min is None and r.lbm_pct is None for r in back.rows)
