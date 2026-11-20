"""Journal effects (spec 5.7): average next-morning Recovery with vs without a habit.

A row's ``journal`` holds answers about behaviour ON that row's date. The answer on
day D is paired with the Recovery of the row dated exactly D + 1 day; pairs where
D + 1 is absent or its recovery is None are skipped.
"""

from dataclasses import dataclass
from fractions import Fraction
from datetime import date, timedelta

from recovery_coach.inputs import DayRow

MIN_PER_SIDE = 5
MIN_DIFF = 5.0


@dataclass
class Effect:
    question: str
    yes_n: int
    no_n: int
    yes_mean: float
    no_mean: float
    diff: float  # yes_mean - no_mean; negative = habit lowers next-morning recovery


def habit_effects(history: list[DayRow], questions: list[str]) -> list[Effect]:
    rows = sorted(history, key=lambda r: r.date)
    recovery = {r.date: r.recovery for r in rows if r.recovery is not None}
    out: list[Effect] = []
    for q in questions:
        yes: list[float] = []
        no: list[float] = []
        for r in rows:
            if q not in r.journal:
                continue
            nxt = (date.fromisoformat(r.date) + timedelta(days=1)).isoformat()
            if nxt not in recovery:
                continue
            (yes if r.journal[q] else no).append(recovery[nxt])
        if len(yes) < MIN_PER_SIDE or len(no) < MIN_PER_SIDE:
            continue
        # exact comparison: float means can land just under the threshold (-4.999...)
        exact = Fraction(sum(yes), len(yes)) - Fraction(sum(no), len(no))
        if abs(exact) < MIN_DIFF:
            continue
        ym, nm = sum(yes) / len(yes), sum(no) / len(no)
        diff = ym - nm
        out.append(Effect(q, len(yes), len(no), ym, nm, diff))
    out.sort(key=lambda e: abs(e.diff), reverse=True)
    return out
