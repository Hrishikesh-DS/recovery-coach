"""Courier helpers the skill runs between the Health queries and the engine (shipped with the engine, so they are
downloaded with the same pinned version instead of being retyped each morning):

    cd /tmp/rc/engine && python3 -m recovery_coach.helper COMMAND ARGS

localize FOLDER TZ         give every timestamp in the folder's CSV files the local offset, written to the minute
                           (2026-11-19T06:20-0600). A UTC stamp (Z or +00:00) is converted to TZ, a stamp without an
                           offset gets TZ (an ambiguous clock time takes its first occurrence, zoneinfo fold=0), any
                           other offset is already local and kept. TZ: an IANA zone (America/Chicago) or a fixed
                           offset (+05:30). Only the timestamp columns change: sleep.csv start,end; hrv.csv time;
                           hours.csv hour_start; minutes.csv time; workouts.csv start,end; raw_hr.csv time;
                           raw_steps.csv start,end. A cell that isn't a valid timestamp is left for the engine to report.
windows FOLDER HRMAX       the time windows that need minute heart rate, from the localized hours.csv and
                           workouts.csv: workouts, and clock hours whose max reaches 50 % of HRMAX, overlapping or
                           touching ones merged. Prints a JSON list of [start, end] and "<n> minutes in <m> windows".
aggregate FOLDER HRMAX     when Health can't aggregate by hour or minute: hours.csv and minutes.csv built from raw
                           samples, raw_hr.csv (time,bpm) and raw_steps.csv (start,end,count; counted in the hour
                           its start falls in), plus workouts.csv, all localized. hours.csv: average (1 decimal),
                           lowest and highest bpm and the steps per clock hour; minutes.csv: the average bpm of each
                           minute inside the windows `windows` would print.
first-day STATE T          the first day needing activity for a run on wake date T (pipeline.activity_from):
                           normally T-1, earlier after skipped mornings, never before T-3. The activity files
                           (hours, minutes, workouts, HRV) start at its 00:00, the sleep samples at noon the day
                           before it (inputs.py).
emit FILE KEY [SINCE]      one part of an engine output (result.json, weekly.json) as compact JSON, for a dashboard
                           write; for "rows", SINCE (YYYY-MM-DD) keeps the rows dated SINCE or later.
week FILE                  the dashboard's weekly row from weekly.json.
backfill-weeks FILE DOB    the dashboard's weekly rows from a back-fill result.json (Body Age = age + delta; no
                           pace yet).
sizes FOLDER               the size of each file in the folder, and the total.
new-workouts STATE FOLDER  the workouts in the folder's localized workouts.csv not yet scored (not in the state's
                           workout log, or provisional), duplicates collapsed: a JSON list of {"id", "kind", "from",
                           "to"} (from = start, to = end + 3 minutes: the window to fetch raw heart rate for).
workout-doc FILE ID        one workout's dashboard document from a result.json ("workouts"), compact; traces are
                           thinned until it fits in 60 KB.
localize also rewrites wk_hr.csv, wk_series.csv and night_hr.csv, and the start / end of each workout and lap in
wk_stats.json.

Exit status: 0 done; 2 bad input or arguments, with one line on stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .inputs import (DAY_STEPS, HR, InputError, _cell, _csv_rows, parse_time, read_hours, read_state, workout_id,
                     read_workouts)
from .pipeline import activity_from

STAMP = "%Y-%m-%dT%H:%M%z"
TIME_COLUMNS = {"sleep.csv": 2, "hrv.csv": 1, "hours.csv": 1, "minutes.csv": 1, "workouts.csv": 2, "raw_hr.csv": 1,
                "raw_steps.csv": 2, "wk_hr.csv": 1, "wk_series.csv": 1, "night_hr.csv": 1}
AFTER_WORKOUT = timedelta(minutes=3)
DOC_LIMIT = 60_000
HOUR = timedelta(hours=1)
MINUTE_SHARE = 0.5   # hours whose max reaches 50 % of HRmax are sent minute by minute (spec 3)


def zone(spec: str):
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", spec)
    if m:
        delta = timedelta(hours=int(m[2]), minutes=int(m[3]))
        return timezone(-delta if m[1] == "-" else delta)
    try:
        return ZoneInfo(spec)
    except (ZoneInfoNotFoundError, ValueError):
        raise InputError("TZ", None,
                         f"unknown time zone {spec!r} (use an IANA name like America/Chicago, or +05:30)") from None


def local(t: datetime, tz) -> datetime:
    if t.tzinfo is None:
        return t.replace(tzinfo=tz)             # fold=0: an ambiguous clock time is its first occurrence
    if t.utcoffset() == timedelta(0):
        return t.astimezone(tz)
    return t


def localize(folder: Path, spec: str) -> None:
    tz = zone(spec)
    for name, n in TIME_COLUMNS.items():
        p = folder / name
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8-sig").splitlines()
        out = lines[:1]
        for i, line in enumerate(lines[1:], start=2):
            if not line.strip():
                continue
            cells = line.split(",")
            for c in range(min(n, len(cells))):
                try:
                    cells[c] = local(parse_time(cells[c], name, i, naive_ok=True), tz).strftime(STAMP)
                except InputError:
                    pass   # not a timestamp: the engine names the file and line
            out.append(",".join(cells))
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
    stats = folder / "wk_stats.json"
    if stats.exists() and stats.stat().st_size:
        try:
            doc = json.loads(stats.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            doc = None   # the engine names the problem
        if isinstance(doc, dict) and isinstance(doc.get("workouts"), list):
            def fix(obj):
                for key in ("start", "end"):
                    if isinstance(obj.get(key), str):
                        try:
                            obj[key] = local(parse_time(obj[key], "wk_stats.json", None, naive_ok=True), tz).strftime(STAMP)
                        except InputError:
                            pass
            for w in doc["workouts"]:
                if isinstance(w, dict):
                    fix(w)
                    for lap in w.get("laps") or []:
                        if isinstance(lap, dict):
                            fix(lap)
            stats.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    print("localized")


def _merge(spans: list[list[datetime]]) -> list[list[datetime]]:
    merged: list[list[datetime]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _workout_spans(folder: Path) -> list[list[datetime]]:
    return [[w.start, w.end] for w in read_workouts(folder / "workouts.csv")]


def windows(folder: Path, hrmax: float) -> None:
    spans = [[h.start, h.start + HOUR] for h in read_hours(folder / "hours.csv")
             if h.max_hr is not None and h.max_hr >= MINUTE_SHARE * hrmax]
    merged = _merge(spans + _workout_spans(folder))
    print(json.dumps([[s.strftime(STAMP), e.strftime(STAMP)] for s, e in merged]))
    print(f"{sum((e - s).total_seconds() for s, e in merged) / 60:.0f} minutes in {len(merged)} windows")


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def aggregate(folder: Path, hrmax: float) -> None:
    hr = []
    for n, (t, bpm) in _csv_rows(folder / "raw_hr.csv", ["time", "bpm"]):
        v = _cell(bpm, "raw_hr.csv", n, "bpm", HR, empty_ok=True)
        if v is not None:
            hr.append((parse_time(t, "raw_hr.csv", n), v))
    steps: dict[datetime, float] = {}
    for n, (start, _end, count) in _csv_rows(folder / "raw_steps.csv", ["start", "end", "count"]):
        v = _cell(count, "raw_steps.csv", n, "count", DAY_STEPS, empty_ok=True)
        if v is not None:
            h = _hour(parse_time(start, "raw_steps.csv", n))
            steps[h] = steps.get(h, 0.0) + v
    by_hour: dict[datetime, list[float]] = {}
    for t, b in hr:
        by_hour.setdefault(_hour(t), []).append(b)
    lines = ["hour_start,avg,min,max,steps"]
    for h in sorted(set(by_hour) | set(steps)):
        b = by_hour.get(h)
        cells = [f"{sum(b) / len(b):.1f}", f"{min(b):.0f}", f"{max(b):.0f}"] if b else ["", "", ""]
        lines.append(",".join([h.strftime(STAMP), *cells, f"{steps.get(h, 0):.0f}"]))
    (folder / "hours.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    spans = [[h, h + HOUR] for h, b in by_hour.items() if max(b) >= MINUTE_SHARE * hrmax]
    spans = _merge(spans + _workout_spans(folder))
    by_min: dict[datetime, list[float]] = {}
    for t, b in hr:
        if any(s <= t < e for s, e in spans):
            by_min.setdefault(t.replace(second=0, microsecond=0), []).append(b)
    out = ["time,bpm"] + [f"{m.strftime(STAMP)},{sum(b) / len(b):.0f}" for m, b in sorted(by_min.items())]
    (folder / "minutes.csv").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"hours.csv {len(lines) - 1} hours, minutes.csv {len(out) - 1} minutes")


def _date(text: str, where: str) -> date:
    try:
        if len(text) != 10:
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise InputError(where, None, f"{text!r} is not a valid date (YYYY-MM-DD)") from None


def first_day(state: Path, t: str) -> None:
    print(activity_from(read_state(state).rows, _date(t, "T")).isoformat())


def _load(path: Path, key: str | None = None):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(path.name, None, "file not found") from None
    except json.JSONDecodeError as e:
        raise InputError(path.name, e.lineno, f"invalid JSON: {e.msg}") from None
    if key is not None and (not isinstance(doc, dict) or key not in doc):
        raise InputError(path.name, None, f"has no {key!r}")
    return doc if key is None else doc[key]


def _print(doc) -> None:
    print(json.dumps(doc, separators=(",", ":")))


def emit(path: Path, key: str, since: str | None = None) -> None:
    doc = _load(path, key)
    if key == "rows" and since:
        doc = [r for r in doc if r["date"] >= _date(since, "SINCE").isoformat()]
    _print(doc)


def week(path: Path) -> None:
    w = _load(path)
    ba = w["card"].get("body_age") or {}
    _print({"date": w["week"]["date"], "body_age": w["week"].get("body_age"), "age": ba.get("age"),
            "pace": w["week"].get("pace"), "items": w["week"].get("items"), "journal": w["card"].get("journal") or []})


def backfill_weeks(path: Path, dob: str) -> None:
    born = _date(dob, "DOB")
    out = []
    for w in _load(path, "weeks"):
        age = round((date.fromisoformat(w["date"]) - born).days / 365.2425, 2)
        out.append({"date": w["date"], "body_age": None if w.get("delta") is None else round(age + w["delta"], 2),
                    "age": age, "pace": None, "items": w.get("items"), "journal": []})
    _print(out)


def new_workouts(state: Path, folder: Path) -> None:
    from .strain import dedupe_workouts
    st = read_state(state)
    done = {e.id for e in st.workouts if not e.provisional}
    out = []
    for w in dedupe_workouts(read_workouts(folder / "workouts.csv")):
        wid = workout_id(w.start)
        if wid not in done:
            out.append({"id": wid, "kind": w.kind, "from": w.start.strftime(STAMP),
                        "to": (w.end + AFTER_WORKOUT).strftime(STAMP)})
    print(json.dumps(out))
    print(f"{len(out)} workout(s) to score")


def _thin(points: list, keep: int) -> list:
    if len(points) <= keep:
        return points
    step = len(points) / keep
    return [points[int(i * step)] for i in range(keep)]


def workout_doc(path: Path, wid: str) -> None:
    docs = _load(path, "workouts")
    doc = next((d for d in docs if isinstance(d, dict) and d.get("id") == wid), None)
    if doc is None:
        raise InputError(path.name, None, f"no workout {wid!r}")
    text = json.dumps(doc, separators=(",", ":"))
    while len(text.encode()) > DOC_LIMIT:
        hr, run = doc.get("hr") or {}, doc.get("run") or {}
        before = len(text)
        for block, key in ((hr, "trace"), (run, "pace_trace")):
            if isinstance(block.get(key), list) and len(block[key]) > 10:
                block[key] = _thin(block[key], len(block[key]) // 2)
        text = json.dumps(doc, separators=(",", ":"))
        if len(text) == before:
            raise InputError(path.name, None, f"workout {wid} is too large for one document")
    print(text)


def sizes(folder: Path) -> None:
    total = 0
    for p in sorted(folder.iterdir()):
        total += p.stat().st_size
        print(f"{p.name:16} {p.stat().st_size:7,} B")
    print(f"{'total':16} {total:7,} B")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m recovery_coach.helper", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, args in (("localize", ["folder", "tz"]), ("windows", ["folder", "hrmax"]),
                       ("aggregate", ["folder", "hrmax"]), ("first-day", ["state", "t"]),
                       ("emit", ["file", "key", "since?"]), ("week", ["file"]), ("backfill-weeks", ["file", "dob"]),
                       ("sizes", ["folder"]), ("new-workouts", ["state", "folder"]), ("workout-doc", ["file", "id"])):
        p = sub.add_parser(name)
        for a in args:
            p.add_argument(a.rstrip("?"), nargs="?" if a.endswith("?") else None)
    try:
        a = parser.parse_args(argv)
    except SystemExit as e:   # usage errors (argparse already printed the message) and --help
        return e.code if isinstance(e.code, int) else 2
    try:
        if a.command in ("windows", "aggregate"):
            try:
                hrmax = float(a.hrmax)
            except ValueError:
                raise InputError("HRMAX", None, f"{a.hrmax!r} is not a number") from None
            (windows if a.command == "windows" else aggregate)(Path(a.folder), hrmax)
        elif a.command == "localize":
            localize(Path(a.folder), a.tz)
        elif a.command == "first-day":
            first_day(Path(a.state), a.t)
        elif a.command == "emit":
            emit(Path(a.file), a.key, a.since)
        elif a.command == "week":
            week(Path(a.file))
        elif a.command == "backfill-weeks":
            backfill_weeks(Path(a.file), a.dob)
        elif a.command == "new-workouts":
            new_workouts(Path(a.state), Path(a.folder))
        elif a.command == "workout-doc":
            workout_doc(Path(a.file), a.id)
        else:
            sizes(Path(a.folder))
    except InputError as e:
        print(e, file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"{e.filename}: file not found", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
