"""Health monitor (spec 5.6): flags only, never part of any score."""
from __future__ import annotations

from .inputs import DayRow
from .stats import robust_baseline, zscore

Z_LIMIT = 2.0
# (code, today key, history attribute, floor, bad direction: +1 high / -1 low)
_SIGNALS = [
    ("hrv_low", "hrv_ln", "hrv_ln", 0.05, -1),
    ("sleep_hr_high", "sleep_hr", "sleep_hr", 1.0, +1),
    ("resp_high", "resp", "resp", 0.5, +1),
    ("spo2_low", "spo2", "spo2", 0.5, -1),  # my call
    ("wtemp_high", "wtemp", "wtemp", 0.2, +1),  # my call
]
MIN_VALUES = 14


def _values(history: list[DayRow], attr: str) -> list[float]:
    return [v for v in (getattr(r, attr) for r in history) if v is not None]


def health_flags(today: dict, history: list[DayRow]) -> list[str]:
    """Codes for signals >= 2 spreads out in the bad direction; adds
    'illness_warning' when two or more are present."""
    flags: list[str] = []
    for code, key, attr, floor, direction in _SIGNALS:
        hist_attr, today_key = attr, key
        if code == "sleep_hr_high" and len(_values(history, "sleep_hr")) < MIN_VALUES:
            hist_attr, today_key = "rhr", "rhr"  # fall back to resting HR
        value = today.get(today_key)
        if value is None:
            continue
        base = robust_baseline(_values(history, hist_attr), floor)
        if base is None:
            continue
        z = zscore(value, base[0], base[1])
        if z * direction >= Z_LIMIT:
            flags.append(code)
    if len(flags) >= 2:
        flags.append("illness_warning")
    return flags
