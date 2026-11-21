"""Command line: python3 -m recovery_coach COMMAND --in DIR --out DIR [--date YYYY-MM-DD]
(import: python3 -m recovery_coach import --export PATH --out DIR --until YYYY-MM-DD [--days 180] [--nights 90])

run       the morning: scores for wake date --date (default: the date of the newest input timestamp);
          writes result.json ({"card", "rows", "settings_patch", "state_next"}, see pipeline.py) and card.html
backfill  the first run: rows for every day with data up to --date, calibrated hrmax / k / hrv_type, a back-test;
          writes result.json ({"rows", "settings_patch", "backtest", "weeks", "state_next", "state_long_next"},
          see backfill.py) and backtest.html
workout   the workouts in the inputs not scored yet (and effort answers): result.json ({"workouts", "updates",
          "card", "state_next"}, see session.py)
weekly    the weekly report for the 7 days ending --date (default: the latest row), from state.json and the
          optional state_long.json; writes weekly.json ({"card", "week", "state_long_next"}, see weekly.py) and
          weekly.html
import    history from an Apple Health export (export.zip or export.xml): writes the back-fill's input files
          (daily, sleep, hrv, hours, minutes, workouts .csv and vitals.json {}) for the span ending --until, and
          prints a line per file and the RMSSD finding (see export.py). state.json is the caller's.

Two stored documents (state.py), each passed back verbatim: state_next becomes the next run's state.json (60 days),
state_long_next the next weekly's state_long.json (180 days and the weekly snapshots). "rows" are the per-day
documents for the dashboard.

Input files, their formats and the files each command requires: inputs.py. Exit status: 0 done; 2 bad input or arguments, with one line on stderr
("<file> line <n>: <problem>", or "<file>: <problem>"); anything else is an engine bug (traceback).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .backfill import backfill
from .card import backtest_card, daily_card, weekly_card
from .export import import_export, summary_lines
from .inputs import REQUIRED, InputError, load_inputs
from .pipeline import run_day, run_workouts
from .weekly import weekly_report

COMMANDS = {
    "run": "morning scores and the daily card",
    "backfill": "first run: past rows, calibration and a back-test",
    "weekly": "weekly report and card",
    "workout": "score the workouts not scored yet (right after a session)",
}
IMPORT_HELP = "history from an Apple Health export into the back-fill's input files"


def _date(text: str | None, name: str = "--date") -> date | None:
    if text is None:
        return None
    try:
        if len(text) != 10:
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise InputError(name, None, f"{text!r} is not a valid date (YYYY-MM-DD)") from None


def _write(folder: Path, files: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m recovery_coach", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in COMMANDS.items():
        p = sub.add_parser(name, help=text, description=text)
        p.add_argument("--in", dest="inp", required=True, type=Path, help="folder with the input files")
        p.add_argument("--out", required=True, type=Path, help="folder for the outputs (created if missing)")
        p.add_argument("--date", help="YYYY-MM-DD (see the defaults above)")
    p = sub.add_parser("import", help=IMPORT_HELP, description=IMPORT_HELP)
    p.add_argument("--export", required=True, type=Path, help="export.zip, or the export.xml inside it")
    p.add_argument("--out", required=True, type=Path, help="folder for the input files (created if missing)")
    p.add_argument("--until", required=True, help="YYYY-MM-DD, the last local date (normally yesterday)")
    p.add_argument("--days", type=int, default=180, help="days of daily, hourly, minute and workout data (180)")
    p.add_argument("--nights", type=int, default=90, help="nights of sleep and HRV (90)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:   # usage errors (argparse already printed the message) and --help
        return e.code if isinstance(e.code, int) else 2
    try:
        if args.command == "import":
            summary = import_export(args.export, args.out, _date(args.until, "--until"), args.days, args.nights)
            print("\n".join(summary_lines(summary)))
            return 0
        as_of = _date(args.date)
        inp = load_inputs(args.inp, REQUIRED[args.command])
        if args.command == "run":
            result = run_day(inp, as_of)
            _write(args.out, {"result.json": _json(result), "card.html": daily_card(result["card"])})
            rec = result["card"]["recovery"]
            print(f"{result['card']['date']}: recovery {rec['score'] if rec['score'] is not None else '-'}"
                  f" ({rec['zone'] or rec['reason_missing']}); wrote result.json, card.html")
        elif args.command == "workout":
            result = run_workouts(inp, as_of)
            _write(args.out, {"result.json": _json(result)})
            print(f"{len(result['workouts'])} workout(s) scored; wrote result.json")
        elif args.command == "backfill":
            result = backfill(inp, as_of)
            _write(args.out, {"result.json": _json(result), "backtest.html": backtest_card(result["backtest"])})
            print(f"backfilled {len(result['rows'])} days; wrote result.json, backtest.html")
        else:
            result = weekly_report(inp.state, as_of, inp.vitals, inp.state_long)
            _write(args.out, {"weekly.json": _json(result), "weekly.html": weekly_card(result["card"])})
            print(f"week {result['card']['week_start']} - {result['card']['week_end']}; wrote weekly.json, weekly.html")
    except InputError as e:
        print(e, file=sys.stderr)
        return 2
    return 0
