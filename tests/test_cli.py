"""End to end: the CLI over the synthetic 90-day data set, carried forward morning by morning as the skill does it."""
import importlib.util
import json
import math
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from recovery_coach import load
from recovery_coach.card import daily_card, weekly_card
from recovery_coach.cli import main
from recovery_coach.inputs import Week, parse_time, row_from_dict
from recovery_coach.state import DAILY_DAYS, long_state

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "synthetic_90d"
DAILY_LIMIT, WEEKLY_LIMIT, INPUT_LIMIT, WEEKLY_INPUT_LIMIT = 14_000, 24_000, 15_000, 30_000
HEALTH_FILES = ("sleep.csv", "hrv.csv", "hours.csv", "minutes.csv", "workouts.csv", "vitals.json", "journal.json")


def _load_generator():
    spec = importlib.util.spec_from_file_location("synthetic_90d_generate", FIX / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    work = tmp_path_factory.mktemp("chain")
    return work, gen.run_chain(work)


def cli(*args):
    return subprocess.run([sys.executable, "-m", "recovery_coach", *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True)


def day_n(d: str) -> int:
    return (date.fromisoformat(d) - gen.FIRST_DAY).days + 1


# ---------------------------------------------------------------- end to end
def test_e2e_synthetic(chain):
    work, res = chain
    days = res["days"]
    assert sorted(days) == list(range(61, 91))

    # every morning: the card dict renders, its file is written and small
    for n, r in days.items():
        assert r["card"]["date"] == gen.day_date(n).isoformat()
        assert [x["date"] for x in r["rows"]][:2] == [gen.day_date(n).isoformat(), gen.day_date(n - 1).isoformat()]
        html = (work / f"day{n:03d}" / "out" / "card.html").read_text()
        assert html == daily_card(r["card"]) and len(html.encode()) < DAILY_LIMIT

    # planted illness dip: red on each dip morning, with the illness warning
    for n in gen.DIP:
        card = days[n]["card"]
        assert card["recovery"]["zone"] == "red" and card["recovery"]["score"] <= 33, n
        assert "hrv_low" in card["flags"] and "illness_warning" in card["flags"], (n, card["flags"])
    others = [days[n]["card"]["recovery"] for n in days if n not in gen.DIP and n != gen.DIP[-1] + 1]
    assert [o["zone"] for o in others].count("red") <= 0.25 * len(others)   # red stays the exception
    dip_mean = sum(days[n]["card"]["recovery"]["score"] for n in gen.DIP) / len(gen.DIP)
    assert dip_mean < sum(o["score"] for o in others) / len(others) - 30
    assert all("illness_warning" not in days[n]["card"]["flags"] for n in days if n not in gen.DIP)

    # hard training block: the load ratio spikes while the block runs (shown the morning after each day)
    flagged = {n for n, r in days.items() if r["card"]["load"]["acwr_flag"] == "spike"}
    assert flagged & set(range(gen.BLOCK.start + 3, gen.BLOCK.stop + 1)), flagged
    assert not flagged & set(range(61, gen.BLOCK.start + 1)), flagged

    # the duplicate strength session counts once
    morning = days[gen.DUP_DAY + 1]["card"]["strain"]["workouts"]
    assert [w["kind"] for w in morning] == ["traditionalStrengthTraining"]
    dup_row = next(r for r in res["rows"] if r["date"] == gen.day_date(gen.DUP_DAY).isoformat())
    lift = gen.world().days[gen.DUP_DAY].workouts[0]
    assert dup_row["strength_min"] == pytest.approx((lift["end"] - lift["start"]).total_seconds() / 60)

    # the log after 30 mornings: one row per day, scores filled in
    rows = {r["date"]: r for r in res["rows"]}
    assert len(rows) == 90
    run_rows = [rows[gen.day_date(n).isoformat()] for n in range(61, 90)]
    assert all(r["recovery"] is not None and r["strain"] is not None and r["sleep_hr"] is not None for r in run_rows)
    assert sum(r["stress"] is not None for r in run_rows) >= 20
    assert rows["2026-11-01"]["onset_min"] < 0 < rows["2026-11-01"]["wake_min"]   # the DST night

    # both documents carried forward: each morning's state.json is the previous state_next (60 days), each
    # weekly's state_long.json the previous state_long_next
    for n, r in days.items():
        assert set(r["state_next"]) == {"settings", "cols", "crc"}
        dates = r["state_next"]["cols"]["date"]
        assert dates[-1] == gen.day_date(n).isoformat() and len(dates) == DAILY_DAYS
    assert "cols" in json.loads((work / "day090" / "state.json").read_text())
    for n, w in res["weeklies"].items():
        assert set(w["state_long_next"]) == {"cols", "weeks", "crc"}
        assert w["state_long_next"]["cols"]["date"][0] == gen.day_date(1).isoformat()   # nothing lost
        assert w["state_long_next"]["cols"]["date"][-1] == gen.day_date(n).isoformat()

    # weekly report: body age with a pace from 8+ snapshots sharing one item set, card small
    weekly = res["weekly"]["card"]
    assert weekly["week_end"] == gen.day_date(90).isoformat()
    assert weekly["body_age"]["body_age"] is not None
    assert {i["name"] for i in weekly["body_age"]["items"]} >= {"sleep_duration", "activity", "rhr", "vo2max"}
    weeks = res["state_long"]["weeks"]
    full = set(res["weekly"]["week"]["items"])
    assert set(weeks[0]["items"]) < full                  # the first snapshot lacked an item (no 30 nights of SRI)
    assert sum(set(w["items"]) == full for w in weeks) >= 8
    assert -1 < weekly["body_age"]["pace"] < 3            # no longer pinned at the -1x clip by that early drift
    assert weekly["worst_day"] is not None and weekly["best_day"] is not None
    html = (work / "weekly090" / "out" / "weekly.html").read_text()
    assert html == weekly_card(weekly) and len(html.encode()) < WEEKLY_LIMIT
    for n, w in res["weeklies"].items():
        assert w["week"]["date"] == gen.day_date(n - 1).isoformat() and w["week"]["items"]


def test_backfill_outputs(chain):
    work, res = chain
    bf = res["backfill"]
    patch = bf["settings_patch"]
    assert patch["hrmax"] >= 208 - 0.7 * 31 and 100 < patch["k"] < 400 and patch["hrv_type"] == "sdnn"
    assert [b["date"] for b in bf["backtest"]] == [gen.day_date(n).isoformat() for n in range(1, 61)]
    scored = [b for b in bf["backtest"] if b["recovery"] is not None]
    assert len(scored) >= 40 and {b["zone"] for b in scored} <= {"green", "yellow", "red"}
    assert all(r["trimp"] is not None and r["ctl"] is not None for r in bf["rows"])
    assert bf["weeks"] and all(w["delta"] is not None and w["items"] for w in bf["weeks"])
    daily, long = bf["state_next"], bf["state_long_next"]
    assert daily["settings"]["hrmax"] == patch["hrmax"] and daily["cols"]["date"][-1] == gen.day_date(60).isoformat()
    assert len(daily["cols"]["date"]) == 60 and "weeks" not in daily
    assert len(long["cols"]["date"]) == 60 and long["weeks"] == bf["weeks"] and "settings" not in long
    html = (work / "backfill" / "out" / "backtest.html").read_bytes()
    assert 0 < len(html) < DAILY_LIMIT and b"http" not in html


def _backfill_with_rmssd(tmp_path, name, since) -> dict:
    """The synthetic back-fill, with an RMSSD reading 25 % below each SDNN reading taken from `since` on."""
    d = tmp_path / name
    gen.write_backfill(d)
    lines = (d / "hrv.csv").read_text().splitlines()
    extra = []
    for line in lines[1:]:
        t, ms, _ = line.split(",")
        if since is not None and parse_time(t, "hrv.csv", None) >= since:
            extra.append(f"{t},{round(0.75 * int(ms))},R")
    (d / "hrv.csv").write_text("\n".join(lines + extra) + "\n")
    assert main(["backfill", "--in", str(d), "--out", str(d / "out")]) == 0
    return json.loads((d / "out" / "result.json").read_text())


def test_backfill_never_mixes_rmssd_and_sdnn(tmp_path):
    sdnn = _backfill_with_rmssd(tmp_path, "sdnn", None)
    reds = [b["date"] for b in sdnn["backtest"] if b["zone"] == "red"]
    # RMSSD on the last 20 of 60 nights: the history stays on SDNN, every night scored as before
    partial = _backfill_with_rmssd(tmp_path, "partial", gen.at(gen.day_date(40), 12 * 60))
    assert partial["settings_patch"]["hrv_type"] == "sdnn"
    assert [b["date"] for b in partial["backtest"] if b["zone"] == "red"] == reds
    assert partial["rows"] == sdnn["rows"]
    # RMSSD on every night: the whole history is RMSSD, one scale throughout
    full = _backfill_with_rmssd(tmp_path, "full", gen.at(gen.day_date(0), 0))
    assert full["settings_patch"]["hrv_type"] == "rmssd"
    pairs = [(f["hrv_ln"], s["hrv_ln"]) for f, s in zip(full["rows"], sdnn["rows"])]
    assert all((f is None) == (s is None) for f, s in pairs)
    assert all(f - s == pytest.approx(math.log(0.75), abs=0.03) for f, s in pairs if f is not None)


def _size(doc) -> int:
    return len(json.dumps(doc, separators=(",", ":")).encode())


def test_row_form_from_dashboard_days_is_a_recovery_path(chain, tmp_path):
    """Lost or damaged state: state.json rebuilt from the dashboard's per-day documents (the rows `backfill` and `run`
    emitted, merged by date exactly as emitted, keys in any order) runs the morning."""
    work, res = chain
    rows = [dict(reversed(list(r.items()))) for r in res["rows"]]
    keys = {k for r in rows for k in r}
    assert {"journal", "flags", "need_min", "mvpa_min", "vig_min", "still_hr", "zone", "tsb"} <= keys
    d = copy_day90(tmp_path)
    day90 = gen.day_date(90).isoformat()
    doc = {"settings": res["state"]["settings"], "rows": [r for r in rows if r["date"] < day90], "weeks": []}
    (d / "state.json").write_text(json.dumps(doc))
    assert main(["run", "--in", str(d), "--out", str(tmp_path / "out"), "--date", day90]) == 0
    card = json.loads((tmp_path / "out" / "result.json").read_text())["card"]
    expected = res["days"][90]["card"]["recovery"]
    assert card["recovery"]["zone"] == expected["zone"]
    assert card["recovery"]["score"] == pytest.approx(expected["score"], abs=1)   # rows keep a little more precision


def _morning(res, tmp_path, name, n, state_after, **kw) -> dict:
    """Day n's run with the daily state left by the morning `state_after` (earlier mornings skipped)."""
    d = tmp_path / name
    gen.write_morning(n, d, **kw)
    (d / "state.json").write_text(json.dumps(res["days"][state_after]["state_next"]))
    assert main(["run", "--in", str(d), "--out", str(d / "out"), "--date", gen.day_date(n).isoformat()]) == 0
    return json.loads((d / "out" / "result.json").read_text())


def _asleep(samples) -> float:
    return sum((e - s).total_seconds() / 60 for s, e, c in samples if c in "CDRS")


def test_any_run_time_finds_the_same_night_and_naps(chain, tmp_path):
    """Sleep samples ending after noon two days back: the night before last is whole at any hour, so a late run never
    takes its tail for a nap. Day 83: naps on both days before (only yesterday's counts)."""
    work, res = chain
    n, w = 83, gen.world()
    assert w.days[n - 1].naps and w.days[n - 2].naps and not w.days[n].naps
    results = {h: _morning(res, tmp_path, f"at{h}", n, n - 1, now=gen.at(gen.day_date(n), h * 60))
               for h in (7, 13, 18, 23)}
    for h, r in results.items():
        assert r["rows"][:2] == results[7]["rows"][:2], h
    nap = _asleep(w.days[n - 1].naps[0])
    assert results[23]["card"]["sleep"]["naps_min"] == round(nap)
    assert results[23]["rows"][0]["sleep_min"] == pytest.approx(_asleep(w.nights[n].samples) + nap, abs=0.1)
    # the old "samples from the last 36 hours" at 18:00 cut the night before last and counted its tail as a nap
    d = tmp_path / "at18"
    since = gen.at(gen.day_date(n), 18 * 60) - timedelta(hours=36)
    lines = (d / "sleep.csv").read_text().splitlines()
    kept = [x for x in lines[1:] if parse_time(x.split(",")[1], "sleep.csv", None) > since]
    (d / "sleep.csv").write_text("\n".join([lines[0], *kept]) + "\n")
    assert main(["run", "--in", str(d), "--out", str(d / "old"), "--date", gen.day_date(n).isoformat()]) == 0
    old = json.loads((d / "old" / "result.json").read_text())
    assert old["card"]["sleep"]["naps_min"] > round(nap) + 30


ACTIVITY = ("strain", "trimp", "stress", "still_hr", "ctl", "atl", "tsb", "acwr", "steps", "vig_min", "strength_min")


@pytest.mark.parametrize("skipped", [1, 2])
def test_skipped_mornings_patch_activity_as_daily_runs_would(chain, tmp_path, skipped):
    work, res = chain
    n = 76
    first = n - 1 - skipped
    out = _morning(res, tmp_path, f"skip{skipped}", n, n - 1 - skipped, first=first)
    assert [r["date"] for r in out["rows"][:skipped + 2]] == [gen.day_date(n - i).isoformat() for i in range(skipped + 2)]
    for m in range(first, n):
        got = next(r for r in out["rows"] if r["date"] == gen.day_date(m).isoformat())
        daily = res["days"][m + 1]["rows"][1]          # the patch the run of the next morning wrote
        assert daily["date"] == got["date"]
        for key in ACTIVITY:
            if key == "stress":   # its HRV baseline lacks the skipped mornings' nights
                assert got[key] == pytest.approx(daily[key], abs=0.03), (m, key)
            else:
                assert got[key] == daily[key], (m, key)
    cols = out["state_next"]["cols"]
    logged = dict(zip(cols["date"][-len(cols["trimp"]):], cols["trimp"]))
    for m in range(first, n):
        daily = res["days"][m + 1]["rows"][1]
        assert logged[daily["date"]] == pytest.approx(daily["trimp"], abs=0.05)   # carried into the next state


def test_a_five_day_gap_patches_only_the_last_three(chain, tmp_path):
    work, res = chain
    n = 76
    out = _morning(res, tmp_path, "gap5", n, n - 5, first=n - 5)   # files from the first missed day: more than needed
    patched = [r["date"] for r in out["rows"][1:] if "trimp" in r]
    assert patched == [gen.day_date(n - i).isoformat() for i in (1, 2, 3)]
    assert not {gen.day_date(n - 5).isoformat(), gen.day_date(n - 4).isoformat()} & {r["date"] for r in out["rows"]}
    before = {r["date"]: r for r in res["rows"]}
    last = row_from_dict(before[gen.day_date(n - 6).isoformat()])
    got = next(r for r in out["rows"] if r["date"] == gen.day_date(n - 3).isoformat())
    stepped = load.update(last, got["trimp"])   # one step from the last logged day: the two unknown days carry
    assert got["ctl"] == pytest.approx(stepped["ctl"], abs=0.05) and got["atl"] == pytest.approx(stepped["atl"], abs=0.05)
    assert got["acwr"] is None                 # the 28-day ratio needs every day


def test_input_budget(chain):
    """What Claude transcribes: each morning's Health files plus its daily state.json; each weekly's two state
    documents."""
    work, res = chain
    mornings = {n: sum((work / f"day{n:03d}" / f).stat().st_size for f in (*HEALTH_FILES, "state.json"))
                for n in range(61, 91)}
    assert max(mornings.values()) < INPUT_LIMIT, mornings
    weeklies = {n: sum((work / f"weekly{n:03d}" / f).stat().st_size for f in ("state.json", "state_long.json"))
                for n in [*res["weeklies"], 90]}
    assert max(weeklies.values()) < WEEKLY_INPUT_LIMIT, weeklies


def test_weekly_input_with_a_full_long_state(chain):
    """The largest weekly courier: a 180-day long state (the log's 90 days twice) with 30 snapshots, plus the
    60-day daily state."""
    work, res = chain
    rows = [row_from_dict(r) for r in res["rows"]]
    earlier = [row_from_dict(dict(r, date=(date.fromisoformat(r["date"]) - timedelta(days=90)).isoformat()))
               for r in res["rows"]]
    items = tuple(res["weekly"]["week"]["items"])
    long = long_state(earlier + rows, [Week(gen.day_date(90) - timedelta(weeks=i), -3.25, items) for i in range(30)])
    assert len(long["cols"]["date"]) == 180 and len(long["weeks"]) == 30
    daily = (work / "day090" / "state.json").stat().st_size
    assert _size(long) + daily < WEEKLY_INPUT_LIMIT, (_size(long), daily)


# ---------------------------------------------------------------- committed fixture and the real CLI
def test_committed_fixture_is_current(tmp_path):
    gen.write_morning(90, tmp_path)
    for f in HEALTH_FILES:
        assert (tmp_path / f).read_text() == (FIX / "day90" / f).read_text(), f


def test_committed_fixture_runs(tmp_path):
    p = cli("run", "--in", FIX / "day90", "--out", tmp_path)
    assert p.returncode == 0, p.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["card"]["date"] == "2026-11-19" and result["card"]["recovery"]["score"] is not None
    assert len((tmp_path / "card.html").read_bytes()) < DAILY_LIMIT


def copy_day90(tmp_path) -> Path:
    d = tmp_path / "in"
    shutil.copytree(FIX / "day90", d)
    return d


def test_truncated_csv_error(tmp_path):
    d = copy_day90(tmp_path)
    with open(d / "sleep.csv", "a") as f:
        f.write("2026-11-19T06:20:09-0600,2026-\n")
    n = len((d / "sleep.csv").read_text().splitlines())
    p = cli("run", "--in", d, "--out", tmp_path / "out")
    assert p.returncode == 2
    assert p.stderr.strip() == f"sleep.csv line {n}: expected 3 fields (start,end,code), got 2"
    assert not (tmp_path / "out" / "card.html").exists()


def test_missing_file_error(tmp_path):
    d = copy_day90(tmp_path)
    (d / "state.json").unlink()
    p = cli("run", "--in", d, "--out", tmp_path / "out")
    assert p.returncode == 2 and p.stderr.strip() == "state.json: file not found"


RUN_REQUIRED = ("sleep.csv", "hrv.csv", "hours.csv", "minutes.csv", "workouts.csv", "vitals.json")
HEADERS = {"sleep.csv": "start,end,code", "hrv.csv": "time,ms,type", "hours.csv": "hour_start,avg,min,max,steps",
           "minutes.csv": "time,bpm", "workouts.csv": "start,end,kind,kcal,avg_hr", "daily.csv": "date,steps,exercise_min,rhr,resp,vo2,max_hr"}


def write_empty(folder: Path, names) -> None:
    """The files as the courier writes them when Health has no data of that kind: header only / {}."""
    for n in names:
        (folder / n).write_text("{}" if n.endswith(".json") else HEADERS[n] + "\n")


def test_missing_required_file_is_an_error(tmp_path):
    d = copy_day90(tmp_path)
    (d / "sleep.csv").unlink()
    p = cli("run", "--in", d, "--out", tmp_path / "out", "--date", "2026-11-19")
    assert p.returncode == 2
    assert p.stderr.strip() == "sleep.csv: file not found (write it even when it has no data)"
    assert not (tmp_path / "out" / "card.html").exists()


def test_header_only_sleep_file_withholds_with_reason(tmp_path):
    d = copy_day90(tmp_path)
    write_empty(d, ["sleep.csv"])
    assert main(["run", "--in", str(d), "--out", str(tmp_path / "out"), "--date", "2026-11-19"]) == 0
    card = json.loads((tmp_path / "out" / "result.json").read_text())["card"]
    assert card["recovery"]["score"] is None and "sleep" in card["recovery"]["reason_missing"]
    assert card["sleep"]["actual_min"] is None and card["strain"]["yesterday"] is not None


@pytest.mark.parametrize("name", RUN_REQUIRED)
def test_each_required_file_is_required_by_run(tmp_path, name):
    d = copy_day90(tmp_path)
    (d / name).unlink()
    p = cli("run", "--in", d, "--out", tmp_path / "out", "--date", "2026-11-19")
    assert p.returncode == 2
    assert p.stderr.strip() == f"{name}: file not found (write it even when it has no data)"


def test_journal_is_optional(tmp_path):
    d = copy_day90(tmp_path)
    (d / "journal.json").unlink()
    assert main(["run", "--in", str(d), "--out", str(tmp_path / "out"), "--date", "2026-11-19"]) == 0


@pytest.mark.parametrize("name", ["sleep.csv", "hrv.csv", "hours.csv", "minutes.csv", "workouts.csv", "vitals.json"])
def test_zero_byte_file_means_no_data(tmp_path, name):
    d = copy_day90(tmp_path)
    (d / name).write_text("")
    assert main(["run", "--in", str(d), "--out", str(tmp_path / "out"), "--date", "2026-11-19"]) == 0


@pytest.mark.parametrize("name", ["sleep.csv", "hrv.csv", "hours.csv", "workouts.csv", "daily.csv", "state.json"])
def test_each_required_file_is_required_by_backfill(tmp_path, name):
    d = tmp_path / "bf"
    d.mkdir()
    gen.write_backfill(d)
    write_empty(d, ["hours.csv"])
    (d / name).unlink()
    p = cli("backfill", "--in", d, "--out", tmp_path / "out")
    assert p.returncode == 2
    assert p.stderr.strip() == f"{name}: file not found" + ("" if name == "state.json" else " (write it even when it has no data)")


def test_weekly_needs_only_state(tmp_path):
    d = tmp_path / "wk"
    d.mkdir()
    shutil.copy(FIX / "day90" / "state.json", d)
    assert main(["weekly", "--in", str(d), "--out", str(tmp_path / "out"), "--date", "2026-11-18"]) == 0


def test_run_date_defaults_to_the_inputs_today(tmp_path):
    assert main(["run", "--in", str(FIX / "day90"), "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "result.json").read_text())["card"]["date"] == "2026-11-19"


def test_bad_date_argument(tmp_path, capsys):
    assert main(["run", "--in", str(FIX / "day90"), "--out", str(tmp_path), "--date", "2026-11-31"]) == 2
    assert "--date" in capsys.readouterr().err


def test_thin_inputs_withhold_with_reasons(tmp_path):
    only_state = tmp_path / "only_state"
    only_state.mkdir()
    shutil.copy(FIX / "day90" / "state.json", only_state)
    write_empty(only_state, RUN_REQUIRED)
    assert main(["run", "--in", str(only_state), "--out", str(tmp_path / "o1"), "--date", "2026-11-19"]) == 0
    card = json.loads((tmp_path / "o1" / "result.json").read_text())["card"]
    assert card["recovery"]["score"] is None and card["recovery"]["reason_missing"]
    assert card["strain"]["yesterday"] is None and card["strain"]["flag"] == "no heart-rate data yesterday"
    assert card["strain"]["target"] is None and card["load"]["acwr"] is None and card["stress"]["reason"]
    assert main(["run", "--in", str(only_state), "--out", str(tmp_path / "o2")]) == 2   # no date to infer

    first = copy_day90(tmp_path)   # a first morning without any history
    doc = json.loads((first / "state.json").read_text())
    (first / "state.json").write_text(json.dumps({"settings": dict(doc["settings"], hrmax=None, k=None), "rows": []}))
    assert main(["run", "--in", str(first), "--out", str(tmp_path / "o3")]) == 0
    card = json.loads((tmp_path / "o3" / "result.json").read_text())["card"]
    assert card["recovery"]["reason_missing"] == "0 of 14 nights collected"
    assert card["stress"]["reason"].startswith("building stress baseline")
    assert card["strain"]["yesterday"] is not None and card["sleep"]["bedtime_tonight"] is not None

    empty = tmp_path / "empty"
    empty.mkdir()
    write_empty(empty, ["sleep.csv", "hrv.csv", "hours.csv", "workouts.csv", "daily.csv"])
    (empty / "state.json").write_text(json.dumps({"settings": doc["settings"], "rows": []}))
    assert main(["backfill", "--in", str(empty), "--out", str(tmp_path / "o4")]) == 2
    assert main(["weekly", "--in", str(empty), "--out", str(tmp_path / "o5")]) == 2
