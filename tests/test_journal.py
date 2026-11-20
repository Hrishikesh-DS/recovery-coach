from datetime import date, timedelta

from recovery_coach.inputs import DayRow
from recovery_coach.journal import Effect, habit_effects

START = date(2026, 1, 1)


def d(i):
    return (START + timedelta(days=i)).isoformat()


def build(answers, rec_next_yes, rec_next_no, q="alcohol"):
    """Row i holds answer i; row i+1's recovery follows the answer on row i."""
    rows = []
    for i, a in enumerate(answers):
        prev = answers[i - 1] if i else None
        rec = None if prev is None else (rec_next_yes if prev else rec_next_no)
        rows.append(DayRow(date=d(i), recovery=rec, journal={q: a}))
    return rows


def test_threshold_counts():
    # 4 yes, 6 no (paired days) -> hidden; 5 yes -> shown
    four = [True] * 4 + [False] * 6 + [False]
    assert habit_effects(build(four, 40, 70), ["alcohol"]) == []
    five = [True] * 5 + [False] * 5 + [False]
    out = habit_effects(build(five, 40, 70), ["alcohol"])
    assert len(out) == 1 and out[0].yes_n == 5 and out[0].no_n == 5


def test_pairs_next_morning():
    answers = [True, False] * 6
    out = habit_effects(build(answers, 40, 70), ["alcohol"])
    e = out[0]
    assert isinstance(e, Effect)
    assert e.yes_mean == 40 and e.no_mean == 70 and e.diff == -30


def test_pairs_exact_next_day_and_skips_gaps_and_unsorted():
    rows = build([True, False] * 6, 40, 70)
    # drop a row -> pairs whose D+1 is missing are skipped; shuffle order
    del rows[5]
    rows.reverse()
    e = habit_effects(rows, ["alcohol"])
    assert e == []  # 5 yes + 4 no left: below threshold
    # recovery None skipped
    rows2 = build([True, False] * 6, 40, 70)
    rows2[3].recovery = None  # drops the pair from row 2 (a yes)
    out = habit_effects(rows2, ["alcohol"])
    assert (out[0].yes_n, out[0].no_n) == (5, 5)


def test_small_diff_hidden():
    answers = [True, False] * 6
    assert habit_effects(build(answers, 66, 70), ["alcohol"]) == []
    assert len(habit_effects(build(answers, 65, 70), ["alcohol"])) == 1  # |diff| == 5


def test_exact_boundary_not_lost_to_float_error():
    # means 3.2 and 8.2: exact diff -5, float arithmetic gives -4.999999999999999
    yes_rec, no_rec = [3, 3, 3, 3, 4], [8, 8, 8, 8, 9]
    rows = []
    for k in range(10):  # answer rows at even indexes, recovery rows at odd indexes
        answer = k < 5
        rec = (yes_rec if answer else no_rec)[k % 5]
        rows.append(DayRow(date=d(2 * k), recovery=None, journal={"q": answer}))
        rows.append(DayRow(date=d(2 * k + 1), recovery=rec, journal={}))
    out = habit_effects(rows, ["q"])
    assert len(out) == 1
    assert (out[0].yes_n, out[0].no_n) == (5, 5)
    assert abs(out[0].diff - (-5.0)) < 1e-9


def test_sorted_by_abs_diff_and_question_filter():
    # recovery on day i+1 is 70 after an "a" day, else 40 -> a: diff 30
    # b equals a except i=0 (a yes, b no) and i=1 (a no, b yes) -> yes mean 65, no mean 45, diff 20
    rows = []
    for i in range(13):
        a = i % 2 == 0
        rec = None if i == 0 else (70 if (i - 1) % 2 == 0 else 40)
        b = {0: False, 1: True}.get(i, a)
        rows.append(DayRow(date=d(i), recovery=rec, journal={"a": a, "b": b, "c": a}))
    out = habit_effects(rows, ["b", "a"])  # "c" not requested; input order b, a
    assert [e.question for e in out] == ["a", "b"]
    assert [e.diff for e in out] == [30, 20]


def test_missing_answer_not_counted():
    rows = build([True, False] * 6, 40, 70)
    for r in rows:
        r.journal = {}
    assert habit_effects(rows, ["alcohol"]) == []
