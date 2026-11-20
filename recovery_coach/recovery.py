"""Recovery score (0-100) from HRV, sleeping HR, sleep performance, respiratory rate."""
from __future__ import annotations

from .inputs import Contributor, DayRow, RecoveryResult
from .stats import MIN_BASELINE_VALUES, logistic, robust_baseline, zscore

WEIGHTS = {"hrv": 0.60, "hr": 0.20, "perf": 0.15, "resp": 0.05}
FLOORS = {"hrv": 0.05, "hr": 1.0, "perf": 0.05, "resp": 0.5}
NORMAL_SCORE = 57  # logistic value at z-bar 0, as specified

GREEN_MIN = 67
YELLOW_MIN = 34

# Limiting-factor caps (thresholds are my call)
HRV_CAP_Z, HRV_CAP = -2.0, 33  # my call
HR_CAP_Z, HR_CAP = -2.0, 50  # my call
PERF_CAP_BELOW, PERF_CAP = 0.60, 50  # my call


def _history_values(history: list[DayRow], attr: str) -> list[float]:
    return [v for v in (getattr(r, attr) for r in history) if v is not None]


def zone_for(score: int) -> str:
    if score >= GREEN_MIN:
        return "green"
    if score >= YELLOW_MIN:
        return "yellow"
    return "red"


def recovery_score(today: dict[str, float | None], history: list[DayRow]) -> RecoveryResult:
    """Score a night. `history` is the previous 60 days of DayRows."""
    if today.get("hrv_ln") is None:
        return RecoveryResult(None, None, reason_missing="no HRV readings last night")
    nights = len(_history_values(history, "hrv_ln"))
    if nights < MIN_BASELINE_VALUES:
        return RecoveryResult(
            None, None, reason_missing=f"{nights} of {MIN_BASELINE_VALUES} nights collected")

    # Sleeping HR: own history when >= 14 nights exist, else Apple resting HR.
    if today.get("sleep_hr") is not None and len(_history_values(history, "sleep_hr")) >= MIN_BASELINE_VALUES:
        hr_name, hr_attr, hr_today = "Sleeping HR", "sleep_hr", today["sleep_hr"]
    else:
        hr_name, hr_attr, hr_today = "Resting HR", "rhr", today.get("rhr")

    # key -> (name, today's value, history attr)
    inputs = {
        "hrv": ("HRV", today["hrv_ln"], "hrv_ln"),
        "hr": (hr_name, hr_today, hr_attr),
        "perf": ("Sleep performance", today.get("perf"), "perf"),
        "resp": ("Respiratory rate", today.get("resp"), "resp"),
    }

    present = []  # (key, name, value, centre, signed_z, weight)
    for key, (name, value, attr) in inputs.items():
        if value is None:
            continue
        base = robust_baseline(_history_values(history, attr), FLOORS[key])
        if base is None:
            continue
        centre, spread = base
        z = zscore(value, centre, spread)
        if key == "hr":
            z = -z  # higher sleeping HR is worse
        elif key == "resp":
            z = -z if z > 0 else 0.0  # higher is worse; only counts when worse
        present.append((key, name, value, centre, z, WEIGHTS[key]))

    total_w = sum(p[5] for p in present)
    zbar = sum(p[5] * p[4] for p in present) / total_w
    uncapped = logistic(zbar)
    delta = uncapped - NORMAL_SCORE

    contributors = []
    for key, name, value, centre, z, w in present:
        share = w * z / total_w
        points = share / zbar * delta if zbar != 0 else 0.0
        contributors.append(Contributor(name, value, centre, round(z, 2), round(points, 1)))

    # Caps apply after the logistic; the lowest applicable cap wins.
    zs = {p[0]: p[4] for p in present}
    caps: list[tuple[int, str]] = []
    if zs["hrv"] <= HRV_CAP_Z:
        caps.append((HRV_CAP, "HRV far below your normal"))
    if "hr" in zs and zs["hr"] <= HR_CAP_Z:
        caps.append((HR_CAP, f"{hr_name} far above your normal"))
    if today.get("perf") is not None and today["perf"] < PERF_CAP_BELOW:  # absolute; needs no baseline
        caps.append((PERF_CAP, "sleep performance under 60%"))

    score = uncapped
    cap_reason = None
    if caps:
        cap, reason = min(caps)
        cap_reason = reason  # the limiting factor, even if the score is already below the cap
        score = min(uncapped, cap)
    final = int(round(score))
    return RecoveryResult(final, zone_for(final), contributors, cap_reason, None, int(round(uncapped)))
