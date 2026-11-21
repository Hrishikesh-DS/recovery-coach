"""Visual check harness for the cards (not collected by pytest; needs Playwright and Chrome).

Puts each card on a plain light (#ffffff) and dark (#1f1f1e) host page, as the inline visual host would (the card
brings its own fixed palette, so the host only shows around the night panel), and screenshots it at phone and desktop
widths:

    python3 tests/render_cards.py [OUT_DIR]          (default: <tmp>/card-shots)

Variants: the two fixtures, a red day with health flags, the size worst case from test_card.py, mostly-null daily
and weekly results, and the first run's back-test.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery_coach.card import backtest_card, daily_card, weekly_card, workout_card  # noqa: E402
from test_card import backtest_days, worst_daily  # noqa: E402  (same directory as this script)

FIX = Path(__file__).parent / "fixtures"
CHROME = "/usr/bin/google-chrome"
WIDTHS = (380, 1000)
MODES = {"light": "#ffffff", "dark": "#1f1f1e"}


def page(fragment: str, mode: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>body{{margin:0;background:{MODES[mode]}}}"
            "</style></head><body>" + fragment + "</body></html>")


def variants() -> dict[str, str]:
    daily = json.loads((FIX / "card_daily.json").read_text())
    weekly = json.loads((FIX / "card_weekly.json").read_text())
    red = copy.deepcopy(daily)
    red["recovery"].update(score=31, zone="red", uncapped=44, cap_reason="HRV far below your normal")
    for c in red["recovery"]["contributors"]:
        c["z"], c["points"] = -c["z"] - 0.6, -abs(c["points"]) / 2
    red["flags"] = ["hrv_low", "sleep_hr_high", "wtemp_high", "illness_warning"]
    red["strain"]["flag"] = "may be low: 9 h of heart-rate data"
    red["load"]["acwr_flag"], red["load"]["acwr"] = "spike", 1.62
    null_daily = {"date": "2026-11-19",
                  "recovery": {"score": None, "reason_missing": "9 of 14 nights collected"},
                  "sleep": None, "strain": None,
                  "stress": {"score": None, "reason": "building stress baseline: 31 of 50 still hours"},
                  "load": None, "flags": [], "trend": [], "bands": {}}
    null_weekly = {"week_start": "2026-11-09", "week_end": "2026-11-15", "averages": {}, "prior": {},
                   "best_day": None, "worst_day": None, "series": [], "trends": {}, "load": [],
                   "body_age": {"body_age": None, "age": 30.6, "pace": None, "items": [],
                                "missing": [["sleep_duration", "only 12 nights of watch sleep in 180 days (need >= 30)"],
                                            ["vo2max", "0 VO2 max reading(s) in 180 days"]]},
                   "journal": [], "focus": None}
    return {"daily": daily_card(daily), "daily-red": daily_card(red), "daily-worst": daily_card(worst_daily(daily)),
            "daily-null": daily_card(null_daily),
            "weekly": weekly_card(weekly), "weekly-null": weekly_card(null_weekly),
            "backtest": backtest_card(backtest_days()),
            "workout": workout_card(json.loads((FIX / "card_workout.json").read_text()))}


def main(out: Path) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    cards = variants()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        for name, html in cards.items():
            for mode in MODES:
                for width in WIDTHS:
                    pg = browser.new_page(viewport={"width": width, "height": 800}, device_scale_factor=2)
                    pg.set_content(page(html, mode))
                    path = out / f"{name}-{mode}-{width}.png"
                    pg.screenshot(path=str(path), full_page=True)
                    pg.close()
                    print(path, f"{len(html.encode())} bytes")
        browser.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "card-shots")
