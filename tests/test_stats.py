import math

from recovery_coach.stats import logistic, percentile, robust_baseline, zscore


def test_logistic_check_values():
    assert round(logistic(0)) == 57
    assert round(logistic(0.5)) == 75
    assert round(logistic(-0.5)) == 38


def test_baseline_needs_14_values():
    assert robust_baseline([1.0] * 13, 0.05) is None
    assert robust_baseline([1.0] * 14, 0.05) is not None


def test_baseline_median_and_mad():
    vals = [float(i) for i in range(1, 16)]  # median 8, MAD 4
    centre, spread = robust_baseline(vals, 0.05)
    assert centre == 8
    assert math.isclose(spread, 1.4826 * 4)


def test_baseline_spread_floor():
    centre, spread = robust_baseline([5.0] * 20, 0.5)
    assert centre == 5.0 and spread == 0.5


def test_zscore_clipped():
    assert zscore(100, 0, 1) == 3
    assert zscore(-100, 0, 1) == -3
    assert zscore(1.5, 1, 0.5) == 1.0


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([1, 2, 3, 4], 90) == 3.7
