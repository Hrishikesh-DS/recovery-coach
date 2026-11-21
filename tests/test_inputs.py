"""Parsing and validation of the compact input files (all values synthetic)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from recovery_coach.inputs import (
    DayRow, InputError, Week, load_inputs, merge_rows, parse_time, read_daily, read_hours, read_hrv, read_journal,
    read_minutes, read_sleep, read_state, read_vitals, read_workouts, row_to_dict,
)

CST = timezone(timedelta(hours=-6))
SETTINGS = {"dob": "1996-04-12", "sex": "male"}


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def state(tmp_path, **kw):
    return write(tmp_path, "state.json", json.dumps({"settings": SETTINGS, "rows": [], **kw}))


# ---------- timestamps ----------

@pytest.mark.parametrize("text, expected", [
    ("2026-11-19T06:20:09-0600", datetime(2026, 11, 19, 6, 20, 9, tzinfo=CST)),
    ("2026-11-19T06:20:09-06:00", datetime(2026, 11, 19, 6, 20, 9, tzinfo=CST)),
    ("2026-11-19T06:20-06:00", datetime(2026, 11, 19, 6, 20, tzinfo=CST)),
    ("2026-11-19 06:20:09.5-06:00", datetime(2026, 11, 19, 6, 20, 9, 500000, tzinfo=CST)),
    ("2026-11-19T12:20:09Z", datetime(2026, 11, 19, 12, 20, 9, tzinfo=timezone.utc)),
    ("2026-11-19T17:50:00+05:30", datetime(2026, 11, 19, 17, 50, tzinfo=timezone(timedelta(hours=5.5)))),
    ("2026-11-19 06:20:09 -0600", datetime(2026, 11, 19, 6, 20, 9, tzinfo=CST)),
    ("2026-11-19T06:20:09.1234567-06:00", datetime(2026, 11, 19, 6, 20, 9, 123456, tzinfo=CST)),
])
def test_timestamp_formats(text, expected):
    t = parse_time(text, "x.csv", 2)
    assert t == expected and t.utcoffset() == expected.utcoffset()


def test_naive_timestamps_only_on_request():
    assert parse_time("2026-11-19 06:20:09", "x.csv", 2, naive_ok=True) == datetime(2026, 11, 19, 6, 20, 9)
    with pytest.raises(InputError):
        parse_time("2026-11-19 06:20:09", "x.csv", 2)


@pytest.mark.parametrize("text", ["2026-11-19T06:20:09", "2026-11-19", "2026-", "2026-13-01T00:00:00-06:00",
                                  "2026-11-19T25:00:00-06:00", ""])
def test_bad_timestamps(text):
    with pytest.raises(InputError) as e:
        parse_time(text, "x.csv", 7)
    assert (e.value.file, e.value.line) == ("x.csv", 7)


# ---------- CSV files ----------

def test_sleep_codes(tmp_path):
    rows = ["2026-11-18T22:30:00-06:00,2026-11-19T06:40:00-06:00,B"] + [
        f"2026-11-18T23:{i:02d}:00-06:00,2026-11-18T23:{i + 1:02d}:00-06:00,{c}" for i, c in enumerate("CDRASB")]
    samples = read_sleep(write(tmp_path, "sleep.csv", "start,end,code\n" + "\n".join(rows) + "\n"))
    assert [s.stage for s in samples] == ["inbed", "core", "deep", "rem", "awake", "asleep", "inbed"]
    assert samples[1].start == datetime(2026, 11, 18, 23, 0, tzinfo=CST)


def test_truncated_row_names_file_and_line(tmp_path):
    text = ("start,end,code\n2026-11-19T05:20:09-0600,2026-11-19T06:20:09-0600,C\n"
            "2026-11-19T06:20:09-0600,2026-\n")
    with pytest.raises(InputError) as e:
        read_sleep(write(tmp_path, "sleep.csv", text))
    assert (e.value.file, e.value.line) == ("sleep.csv", 3)
    assert str(e.value).startswith("sleep.csv line 3: ")


@pytest.mark.parametrize("name, reader, text, line, words", [
    ("sleep.csv", read_sleep, "start,end,code\n2026-11-19T05:00:00-06:00,2026-11-19T06:00:00-06:00,X\n", 2,
     "sleep code"),
    ("sleep.csv", read_sleep, "start,end,code\n2026-11-19T06:00:00-06:00,2026-11-19T05:00:00-06:00,C\n", 2,
     "before"),
    ("sleep.csv", read_sleep, "start,end\n", 1, "header"),
    ("sleep.csv", read_sleep, "\n", 1, "header"),
    ("hrv.csv", read_hrv, "time,ms,type\n2026-11-19T03:00:00-06:00,2,S\n", 2, "ms"),
    ("hrv.csv", read_hrv, "time,ms,type\n2026-11-19T03:00:00-06:00,42,X\n", 2, "type"),
    ("hrv.csv", read_hrv, "time,ms,type\n2026-11-19T03:00:00-06:00,nan,S\n", 2, "ms"),
    ("hours.csv", read_hours, "hour_start,avg,min,max,steps\n2026-11-19T03:00:00-06:00,61,50,260,0\n", 2, "max"),
    ("hours.csv", read_hours, "hour_start,avg,min,max,steps\n2026-11-19T03:00:00-06:00,61,50,70,-5\n", 2,
     "steps"),
    ("hours.csv", read_hours, "hour_start,avg,min,max,steps\n2026-11-19T03:00:00-06:00,61,80,70,0\n", 2, "min"),
    ("minutes.csv", read_minutes, "time,bpm\n2026-11-19T03:00:00-06:00,20\n", 2, "bpm"),
    ("minutes.csv", read_minutes, "time,bpm\n2026-11-19T03:00:00-06:00,1e9\n", 2, "bpm"),
    ("workouts.csv", read_workouts,
     "start,end,kind,kcal,avg_hr\n2026-11-19T03:00:00-06:00,2026-11-19T04:00:00-06:00,running,300,251\n", 2,
     "avg_hr"),
    ("workouts.csv", read_workouts,
     "start,end,kind,kcal,avg_hr\n2026-11-19T03:00:00-06:00,2026-11-19T04:00:00-06:00,,300,140\n", 2, "kind"),
    ("daily.csv", read_daily, "date,steps,exercise_min,rhr,resp,vo2,max_hr\n2026-11-19,9000,30,55,14.5,95,180\n",
     2, "vo2"),
    ("daily.csv", read_daily, "date,steps,exercise_min,rhr,resp,vo2,max_hr\n2026-11-31,9000,30,55,14.5,,180\n",
     2, "date"),
])
def test_csv_validation(tmp_path, name, reader, text, line, words):
    with pytest.raises(InputError) as e:
        reader(write(tmp_path, name, text))
    assert (e.value.file, e.value.line) == (name, line)
    assert words in e.value.message


def test_csv_values(tmp_path):
    hours = read_hours(write(tmp_path, "hours.csv", "hour_start,avg,min,max,steps\n"
                                                    "2026-11-19T03:00:00-06:00,,,,\n"
                                                    "2026-11-19T04:00:00-06:00,52.5,49,57,12\n\n"))
    assert hours[0].avg_hr is None and hours[0].steps == 0.0
    assert (hours[1].avg_hr, hours[1].min_hr, hours[1].max_hr, hours[1].steps) == (52.5, 49, 57, 12)
    ws = read_workouts(write(tmp_path, "workouts.csv", "start,end,kind,kcal,avg_hr\n"
                                                       "2026-11-18T18:00:00-06:00,2026-11-18T18:50:00-06:00,other,,\n"))
    assert ws[0].kind == "other" and ws[0].energy_kcal is None and ws[0].avg_hr is None
    hrv = read_hrv(write(tmp_path, "hrv.csv", "time,ms,type\n2026-11-19T03:00:00-06:00,48.5,R\n"))
    assert (hrv[0].ms, hrv[0].type) == (48.5, "R")
    daily = read_daily(write(tmp_path, "daily.csv", "date,steps,exercise_min,rhr,resp,vo2,max_hr\n"
                                                     "2026-11-19,9000,30,55,14.5,,180\n"))
    assert daily[0].date == date(2026, 11, 19) and daily[0].vo2 is None and daily[0].max_hr == 180


# ---------- JSON files ----------

@pytest.mark.parametrize("vitals, words", [
    ({"resp": 50}, "resp"), ({"spo2": 60}, "spo2"), ({"spo2": 0.5}, "spo2"), ({"wtemp": 45}, "wtemp"),
    ({"weight_kg": 300}, "weight_kg"), ({"height_m": 178}, "height_m"), ({"vo2": 5}, "vo2"),
    ({"rhr": "55"}, "rhr"), ({"resp_rate": 15}, "resp_rate"), ({"exercise_min_yesterday": -1}, "exercise"),
])
def test_vitals_validation(tmp_path, vitals, words):
    with pytest.raises(InputError) as e:
        read_vitals(write(tmp_path, "vitals.json", json.dumps(vitals)))
    assert e.value.file == "vitals.json" and words in e.value.message


def test_vitals_values(tmp_path):
    v = read_vitals(write(tmp_path, "vitals.json", json.dumps({"spo2": 0.965, "resp": 14.2, "rhr": None})))
    assert v.spo2 == pytest.approx(96.5) and v.resp == 14.2 and v.rhr is None and v.wtemp is None


def test_json_syntax_error_names_line(tmp_path):
    with pytest.raises(InputError) as e:
        read_vitals(write(tmp_path, "vitals.json", '{"resp": 14.2,\n "spo2": }'))
    assert (e.value.file, e.value.line) == ("vitals.json", 2)


def test_journal(tmp_path):
    j = read_journal(write(tmp_path, "journal.json", json.dumps({"date": "2026-11-18", "answers": {"alcohol": True}})))
    assert j.date == date(2026, 11, 18) and j.answers == {"alcohol": True}
    with pytest.raises(InputError) as e:
        read_journal(write(tmp_path, "journal.json", json.dumps({"date": "2026-11-18", "answers": {"alcohol": "y"}})))
    assert "alcohol" in e.value.message


def test_state(tmp_path):
    rows = [{"date": "2026-11-18", "recovery": 57.0, "zone": "yellow", "hrv_ln": 4.0, "still_hr": [61.2],
             "journal": {"alcohol": False}},
            {"date": "2026-11-17", "strain": 12.5}]
    st = read_state(state(tmp_path, rows=rows, weeks=[{"date": "2026-11-15", "delta": -1.2}]))
    assert st.settings.dob == date(1996, 4, 12) and st.settings.sleep_goal_min == 480
    assert st.settings.hrv_type == "sdnn" and st.settings.hrmax is None and st.settings.k is None
    assert [r.date for r in st.rows] == ["2026-11-17", "2026-11-18"]   # sorted by date
    assert st.rows[1].recovery == 57 and isinstance(st.rows[1].recovery, int)
    assert st.rows[1].still_hr == [61.2] and st.rows[1].journal == {"alcohol": False}
    assert st.weeks == [Week(date(2026, 11, 15), -1.2, None)]   # an old snapshot: items unknown


@pytest.mark.parametrize("doc, words", [
    ({"settings": {"sex": "male"}, "rows": []}, "dob"),
    ({"settings": {**SETTINGS, "sex": "x"}, "rows": []}, "sex"),
    ({"settings": {**SETTINGS, "hrv_type": "pnn50"}, "rows": []}, "hrv_type"),
    ({"settings": {**SETTINGS, "sleep_goal": 450}, "rows": []}, "sleep_goal"),
    ({"settings": SETTINGS, "rows": [{"date": "2026-11-18", "hrv": 4.0}]}, "hrv"),
    ({"settings": SETTINGS, "rows": [{"date": "2026-11-18", "hrv_ln": "4"}]}, "hrv_ln"),
    ({"settings": SETTINGS, "rows": [{"date": "2026-11-18"}, {"date": "2026-11-18"}]}, "duplicate"),
    ({"settings": SETTINGS, "rows": [{"recovery": 50}]}, "date"),
    ({"settings": SETTINGS, "rows": [{"date": "2026-11-18", "zone": "blue"}]}, "zone"),
    ({"rows": []}, "settings"),
])
def test_state_validation(tmp_path, doc, words):
    with pytest.raises(InputError) as e:
        read_state(write(tmp_path, "state.json", json.dumps(doc)))
    assert e.value.file == "state.json" and words in str(e.value)


def test_zero_byte_files_are_no_data(tmp_path):
    for name, reader in (("sleep.csv", read_sleep), ("hrv.csv", read_hrv), ("hours.csv", read_hours)):
        assert reader(write(tmp_path, name, "")) == []
    assert read_vitals(write(tmp_path, "vitals.json", "")) == read_vitals(write(tmp_path, "v2.json", "{}"))


def test_required_files(tmp_path):
    state(tmp_path)
    with pytest.raises(InputError) as e:
        load_inputs(tmp_path, ("state.json", "hrv.csv"))
    assert str(e.value) == "hrv.csv: file not found (write it even when it has no data)"


def test_missing_files(tmp_path):
    with pytest.raises(InputError) as e:
        load_inputs(tmp_path)
    assert e.value.file == "state.json" and e.value.line is None and str(e.value) == "state.json: file not found"
    state(tmp_path)
    inp = load_inputs(tmp_path)
    assert inp.sleep is None and inp.hrv is None and inp.hours is None and inp.minutes is None
    assert inp.workouts is None and inp.vitals is None and inp.journal is None and inp.daily is None


# ---------- rows ----------

def test_row_to_dict_rounds_and_merge():
    d = row_to_dict(DayRow(date="2026-11-18", hrv_ln=4.012345678, strain=12.3456, still_hr=[61.234]))
    assert d["hrv_ln"] == 4.0123 and d["strain"] == 12.35 and d["still_hr"] == [61.2] and d["recovery"] is None
    rows = [{"date": "2026-11-18", "recovery": 57, "strain": None}, {"date": "2026-11-16", "recovery": 40}]
    out = merge_rows(rows, [{"date": "2026-11-18", "strain": 12.3}, {"date": "2026-11-19", "recovery": 80}])
    assert [r["date"] for r in out] == ["2026-11-16", "2026-11-18", "2026-11-19"]
    assert out[1] == {"date": "2026-11-18", "recovery": 57, "strain": 12.3}
    assert rows[0]["strain"] is None   # inputs untouched


# ---------- workout inputs (post-workout metrics) ----------

from recovery_coach.inputs import (  # noqa: E402
    REQUIRED, read_efforts, read_night_hr, read_wk_hr, read_wk_series, read_wk_stats, workout_id,
)


def test_read_wk_stats_full_and_minimal(tmp_path):
    doc = {"workouts": [
        {"start": "2026-11-21T08:23:39-06:00", "end": "2026-11-21T09:14:11-06:00", "kind": "running"},
        {"start": "2026-11-21T08:23-0600", "end": "2026-11-21T09:14-0600", "kind": "running", "indoor": False,
         "dist": 7516, "elev_up": 40, "elev_down": 38, "temp_c": 12.5, "avg_hr": 151, "max_hr": 176,
         "avg_speed": 2.6, "avg_power": 230, "stride": 1.0, "vo_cm": 8.9, "gct_ms": 262, "effort": 7,
         "effort_est": 7, "cardio_recovery": 26,
         "laps": [{"start": "2026-11-21T08:23-0600", "end": "2026-11-21T08:30-0600", "dist": 1000}]},
    ]}
    ws = read_wk_stats(write(tmp_path, "wk_stats.json", json.dumps(doc)))
    a, b = ws
    assert a.kind == "running" and a.indoor is None and a.dist is None and a.effort is None and a.laps == []
    assert b.indoor is False and b.dist == 7516 and b.effort == 7 and b.cardio_recovery == 26
    assert len(b.laps) == 1 and b.laps[0].dist == 1000


def test_effort_out_of_range_names_file(tmp_path):
    doc = {"workouts": [{"start": "2026-11-21T08:23-0600", "end": "2026-11-21T09:14-0600", "kind": "running",
                         "effort": 11}]}
    with pytest.raises(InputError) as e:
        read_wk_stats(write(tmp_path, "wk_stats.json", json.dumps(doc)))
    assert str(e.value).startswith("wk_stats.json: workouts[0].effort")


def test_read_wk_series_blank_cells(tmp_path):
    p = write(tmp_path, "wk_series.csv",
              "time,speed,power,dist\n2026-11-21T08:24:00-0600,2.6,231,120\n2026-11-21T08:24:05-0600,,,\n")
    pts = read_wk_series(p)
    assert pts[0].speed == 2.6 and pts[0].power == 231 and pts[0].dist == 120
    assert pts[1].speed is None and pts[1].power is None and pts[1].dist is None


def test_read_wk_hr_and_night_hr(tmp_path):
    hr = read_wk_hr(write(tmp_path, "wk_hr.csv", "time,bpm\n2026-11-21T08:24:00-0600,141\n"))
    nh = read_night_hr(write(tmp_path, "night_hr.csv", "time,bpm\n2026-11-21T02:00-0600,52\n"))
    assert hr[0][1] == 141 and nh[0][1] == 52


def test_read_efforts_normalises_keys(tmp_path):
    p = write(tmp_path, "efforts.json", json.dumps({"answers": {"2026-11-21T08:23:39-06:00": 6}}))
    assert read_efforts(p) == {"2026-11-21T08:23-0600": 6.0}


def test_workout_id_format():
    assert workout_id(datetime(2026, 11, 21, 8, 23, 39, tzinfo=CST)) == "2026-11-21T08:23-0600"
    assert workout_id(datetime(2026, 3, 1, 7, 5, tzinfo=timezone(timedelta(hours=5.5)))) == "2026-03-01T07:05+0530"


def test_run_inputs_without_new_files_still_load(tmp_path):
    state(tmp_path)
    for name, text in (("sleep.csv", "start,end,code\n"), ("hrv.csv", "time,ms,type\n"),
                       ("hours.csv", "hour_start,avg,min,max,steps\n"), ("minutes.csv", "time,bpm\n"),
                       ("workouts.csv", "start,end,kind,kcal,avg_hr\n"), ("vitals.json", "{}")):
        write(tmp_path, name, text)
    inp = load_inputs(tmp_path, REQUIRED["run"])
    assert inp.wk_hr is None and inp.wk_series is None and inp.wk_stats is None
    assert inp.night_hr is None and inp.efforts is None
    assert REQUIRED["workout"] == ("state.json", "workouts.csv", "wk_stats.json", "wk_hr.csv", "wk_series.csv")
