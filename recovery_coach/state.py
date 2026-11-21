"""The compact state documents the engine hands back, stored by the skill and passed back verbatim.

Two documents keep every morning small (spec 3, the courier budget):
- the daily state (state.json; `run` returns it as "state_next"): the last DAILY_DAYS days with settings. It holds
  what `run` reads (recovery and monitor baselines 60 days, load 29, stress 15) and, for the same 60 days, the
  fields only `weekly` reads: the days since the last weekly live only here until `weekly` folds them in;
- the long state (state_long.json; `weekly` and `backfill` return it as "state_long_next"): up to LONG_DAYS days
  and the last KEEP_WEEKS weekly Body Age snapshots, no settings. `weekly` merges it with the daily state, the daily
  state winning on dates both hold.
The format is documented in inputs.py (read_state). In both documents:
- only fields some command reads, each kept for as many recent days as its readers look back (STATE_FIELDS, capped
  at the document's length) and at a precision that changes no score; a column shorter than "date" covers the most
  recent dates;
- zone and tsb are derived on reading (zone from recovery, tsb = ctl - atl); need_min, mvpa_min and vig_min are
  read by nothing and dropped (they stay in the per-day rows the dashboard keeps for charts);
- journal answers as one 1/0 column per question;
- "crc", a checksum of cols (and weeks): a value lost or changed in copying would otherwise shift or alter a column
  silently, so read_state refuses a document whose checksum doesn't match (inputs.state_crc).
"""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, timedelta

from .inputs import _INT_FIELDS, JOURNAL_COL, DayRow, Settings, State, Week, merge_rows, row_from_dict, row_to_dict, state_crc

DAILY_DAYS = 60
LONG_DAYS = 180
KEEP_WEEKS = 30   # Pace of Aging: 26 points after a 4-week moving average
# field: (decimals, most recent days kept) -- the days each field's readers need, counted back from the latest row
#   60: recovery and health-monitor baselines; 180: Body Age (sleep, regularity, steps, strength, VO2 max, resting HR)
#   and journal effects (with recovery); 36: weekly averages (week + 4 weeks before, ending the day before the
#   latest row); 29: training load chart and the 28-day load ratio; 15: stress baseline; 8: weekly focus and flags
STATE_FIELDS: dict[str, tuple[int | None, int]] = {
    "recovery": (0, 180), "hrv_ln": (3, 60), "sleep_hr": (1, 60), "rhr": (1, 180), "resp": (1, 60),
    "spo2": (1, 60), "wtemp": (1, 60), "sleep_min": (0, 180), "perf": (3, 60), "debt_min": (0, 8),
    "onset_min": (0, 180), "wake_min": (0, 180), "strain": (1, 36), "trimp": (1, 29), "stress": (2, 36),
    "still_hr": (1, 15), "ctl": (1, 29), "atl": (1, 29), "acwr": (2, 8), "steps": (0, 180),
    "strength_min": (0, 180), "vo2": (1, 180), "flags": (None, 8),
    "wload": (1, 29), "srpe": (0, 29),
}
JOURNAL_DAYS = 180


def _num(v, nd: int):
    """Rounded to nd decimals, written as an int when whole (54.0 -> 54) to save bytes."""
    x = round(float(v), nd)
    return int(x) if nd == 0 or x == int(x) else x


def _encode(name: str, v, nd):
    if name == "flags":
        return list(v) or None
    if name == "still_hr":
        return [_num(x, nd) for x in v] or None
    return None if v is None else _num(v, nd)


def as_read_back(row: DayRow) -> DayRow:
    """The row as the next run reads it back from the daily state: each STATE_FIELDS value at its precision."""
    out = {}
    for name, (nd, _) in STATE_FIELDS.items():
        v = getattr(row, name)
        if nd is None or v is None:
            continue
        cast = int if name in _INT_FIELDS else float
        out[name] = [float(_num(x, nd)) for x in v] if name == "still_hr" else cast(_num(v, nd))
    return replace(row, **out)


def settings_dict(s: Settings) -> dict:
    out = {f.name: getattr(s, f.name) for f in fields(Settings)}
    out["dob"] = s.dob.isoformat()
    out["questions"] = list(s.questions)
    return out


def _column(rows: list[DayRow], days: int, value) -> list | None:
    """One value per row for the last `days` dates, leading nulls dropped (the column then covers the latest dates)."""
    cut = (date.fromisoformat(rows[-1].date) - timedelta(days=days - 1)).isoformat()
    vals = [value(r) if r.date >= cut else None for r in rows]
    while vals and vals[0] is None:
        vals.pop(0)
    return vals or None


def compact_state(settings: Settings | None, rows: list[DayRow], weeks: list[Week] | None,
                  keep_days: int = LONG_DAYS, workouts: list | None = None) -> dict:
    """A compact state document for these rows (any order): the last keep_days dates, with settings and weekly
    snapshots when given (None leaves the key out), and the workout log (workout_log.LogEntry) when non-empty."""
    rows = sorted(rows, key=lambda r: r.date)
    if rows:
        lo = (date.fromisoformat(rows[-1].date) - timedelta(days=keep_days - 1)).isoformat()
        rows = [r for r in rows if r.date >= lo]
    cols: dict[str, list] = {"date": [r.date for r in rows]}
    if rows:
        for name, (nd, days) in STATE_FIELDS.items():
            col = _column(rows, min(days, keep_days), lambda r, n=name, d=nd: _encode(n, getattr(r, n), d))
            if col:
                cols[name] = col
        first = list(settings.questions) if settings is not None else []
        asked = first + sorted({q for r in rows for q in r.journal} - set(first))
        for q in asked:
            col = _column(rows, min(JOURNAL_DAYS, keep_days),
                          lambda r, q=q: None if q not in r.journal else int(r.journal[q]))
            if col:
                cols[JOURNAL_COL + q] = col
    doc = {} if settings is None else {"settings": settings_dict(settings)}
    doc["cols"] = cols
    if weeks is not None:
        snaps = sorted(weeks, key=lambda w: w.date)[-KEEP_WEEKS:]
        doc["weeks"] = [{"date": w.date.isoformat(), "delta": w.delta,
                         **({"items": list(w.items)} if w.items is not None else {})} for w in snaps]
    if workouts:
        from .workout_log import CARD_DAYS, entry_to_dict
        cut = (max(date.fromisoformat(e.date) for e in workouts) - timedelta(days=CARD_DAYS)).isoformat()
        doc["workouts"] = [entry_to_dict(e, keep_card=e.date >= cut) for e in sorted(workouts, key=lambda e: e.id)]
    doc["crc"] = state_crc(cols, doc.get("weeks"), doc.get("workouts"))
    return doc


def merge_states(long: State | None, daily: State) -> list[DayRow]:
    """The rows of both documents by date; the daily state's row wins on a date both hold."""
    by = {r.date: r for r in (long.rows if long is not None else [])}
    by.update({r.date: r for r in daily.rows})
    return [by[d] for d in sorted(by)]


def merged_rows(state: State, patches: list[dict]) -> list[DayRow]:
    """The state's rows with a run's row patches merged by date (inputs.merge_rows)."""
    merged = merge_rows([row_to_dict(r) for r in state.rows], patches)
    return [row_from_dict(d, "result", "row") for d in merged]


def next_state(state: State, patches: list[dict], settings_patch: dict | None = None,
               workouts: list | None = None) -> dict:
    """The daily state after a run: patches merged, settings patched, the last DAILY_DAYS days, and the workout
    log (the state's own when workouts is None)."""
    settings = replace(state.settings, **(settings_patch or {}))
    log = state.workouts if workouts is None else workouts
    return compact_state(settings, merged_rows(state, patches), None, DAILY_DAYS, workouts=log)


def long_state(rows: list[DayRow], weeks: list[Week]) -> dict:
    """The long state: the last LONG_DAYS days and the weekly snapshots, no settings."""
    return compact_state(None, rows, weeks, LONG_DAYS)
