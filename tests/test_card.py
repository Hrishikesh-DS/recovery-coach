"""Daily and weekly cards: content, the inline visual host's rules, the fixed design system, and graceful nulls."""
import copy
import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest

from recovery_coach.card import STRESS_RAMP, backtest_card, daily_card, delta_tone, weekly_card

FIX = Path(__file__).parent / "fixtures"
DAILY_LIMIT = 14_000   # bytes, "< 14 KB"
WEEKLY_LIMIT = 24_000  # bytes, "< 24 KB"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def daily():
    return load("card_daily.json")


@pytest.fixture
def weekly():
    return load("card_weekly.json")


def mostly_null_daily():
    return {
        "date": "2026-11-19",
        "recovery": {"score": None, "zone": None, "uncapped": None, "contributors": [], "cap_reason": None,
                     "reason_missing": "9 of 14 nights collected"},
        "sleep": None,
        "strain": None,
        "stress": {"score": None, "band": None, "hourly": [], "top_hours": [],
                   "reason": "building stress baseline: 31 of 50 still hours"},
        "load": None,
        "flags": [],
        "trend": [],
        "bands": {},
    }


def mostly_null_weekly():
    keys = ("recovery", "strain", "sleep_perf", "sleep_min", "hrv", "rhr", "stress")
    return {
        "week_start": "2026-11-09", "week_end": "2026-11-15",
        "averages": {k: None for k in keys}, "prior": {k: None for k in keys},
        "best_day": None, "worst_day": None, "series": [], "trends": {}, "load": [],
        "body_age": None, "journal": [], "focus": None,
    }


def heavy_daily(daily):
    """Pessimistic day for size: every flag, a cap, four workouts, 18 stress hours."""
    d = copy.deepcopy(daily)
    d["flags"] = ["hrv_low", "sleep_hr_high", "resp_high", "spo2_low", "wtemp_high", "illness_warning"]
    d["recovery"].update(score=31, zone="red", uncapped=44, cap_reason="HRV far below your normal")
    w = d["strain"]["workouts"][0]
    d["strain"]["workouts"] = [dict(w, kind=k) for k in
                               ("running", "traditionalStrengthTraining", "walking", "cycling")]
    d["strain"]["flag"] = "may be low: 9 h of heart-rate data"
    d["load"]["acwr_flag"] = "spike"
    base = d["stress"]["hourly"][0][0]
    d["stress"]["hourly"] = [[base.replace("T07:", f"T{6 + i:02d}:"), 0.17 * i] for i in range(18)]
    return d


def worst_daily(daily):
    """Worst case for size: 8 workouts, every flag (twice), every section populated, a 24-hour stress strip, long
    contributor names and reasons."""
    d = heavy_daily(daily)
    base = d["strain"]["workouts"][0]
    kinds = ("running", "traditionalStrengthTraining", "walking", "cycling", "highIntensityIntervalTraining",
             "functionalStrengthTraining", "mixedCardio", "other")
    d["strain"]["workouts"] = [dict(base, kind=k, start=base["start"].replace("T06:", f"T{6 + 2 * i:02d}:"),
                                    strain=round(4.0 + 1.3 * i, 1)) for i, k in enumerate(kinds)]
    for c in d["recovery"]["contributors"]:
        c["name"] = c["name"] + " measured across the main sleep window"
    d["recovery"]["cap_reason"] = "HRV far below your normal for the third night in a row"
    d["strain"]["flag"] = "may be low: only 9 h of heart-rate data were recorded yesterday"
    base_hour = d["stress"]["hourly"][0][0]
    d["stress"]["hourly"] = [[base_hour.replace("T06:", f"T{h:02d}:"), round(0.12 * h, 2)] for h in range(24)]
    d["flags"] = d["flags"] * 2
    d["stress"]["top_hours"] = [h for h, _ in d["stress"]["hourly"][-3:]]
    return d


def heavy_weekly(weekly):
    w = copy.deepcopy(weekly)
    j = w["journal"][0]
    w["journal"] = [dict(j, question=q) for q in
                    ("alcohol", "caffeine after 2 pm", "late meal", "screens in bed", "stressful day")]
    w["body_age"]["missing"] = [[n, "only 12 days of data in 180 days (need >= 30)"] for n in
                                ("vo2max", "body_comp", "steps")]
    return w


# ---------------------------------------------------------------- host rules
class _Balance(HTMLParser):
    VOID = {"br", "img", "hr", "input", "meta", "link", "path", "circle", "rect", "line", "polyline", "polygon"}

    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        assert self.stack and self.stack[-1] == tag, f"unbalanced </{tag}>, open: {self.stack[-3:]}"
        self.stack.pop()


ACRONYMS = {"HRV", "REM", "SRI", "BMI", "HIIT", "ACWR"}
# the fixed palette: the card looks the same on a light or a dark host
PALETTE = {"#0D1218", "#151C24", "#232D38", "#EDF1F5", "#8F9BA8", "#5F6B78", "#34E089", "#F5C542", "#FF5468",
           "#3EA6FF", "#9A8CFF", "#FF8FAB", "#FF7A45", *STRESS_RAMP}
TYPE_SCALE = {11, 13, 15, 20, 28, 64}


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def visible_text(html):
    """The card's words as a reader sees them: tags dropped, whitespace collapsed, the screen-reader summary and the
    stylesheet left out."""
    body = re.sub(r'<h2 class="sr-only">.*?</h2>|<style>.*?</style>', "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def check_host_rules(html):
    low = html.lower()
    for banned in ("http://", "https://", "<script", "@import", "<!--", "/*", "xmlns", "url(", "@font-face",
                   "<!doctype", "<html", "<head", "<body", "display:none", "display: none", "whoop",
                   "var(--",                                            # no host theme variables: fixed palette
                   "gradient", "shadow", "blur(", "filter", "glow"):    # flat fills only (they flash while streaming)
        assert banned not in low, banned
    assert not re.search(r"text-transform\s*:\s*uppercase", low)
    assert not re.search(r"position\s*:\s*fixed", low)
    # weights 400/500 only
    weights = re.findall(r"font-weight\s*:\s*(\w+)", low) + re.findall(r"font\s*:\s*(\d+)\s", low)
    assert weights and set(weights) <= {"400", "500"}, set(weights)
    # sizes from the type scale only
    sizes = [float(s) for s in re.findall(r"font-size\s*:\s*([\d.]+)px", low)]
    sizes += [float(s) for s in re.findall(r"font\s*:[^;\"]*?([\d.]+)px", low)]
    sizes += [float(s) for s in re.findall(r'font-size="([\d.]+)"', low)]
    assert sizes and set(sizes) <= TYPE_SCALE, sorted(set(sizes) - TYPE_SCALE)
    # every colour comes from the palette (low-alpha tracks and bands are palette colours too)
    hexes = {h.upper() for h in re.findall(r"#[0-9a-fA-F]{6}\b", html)}
    assert hexes <= PALETTE, hexes - PALETTE
    rgbas = re.findall(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", html)
    assert {tuple(map(int, c[:3])) for c in rgbas} <= {_rgb(h) for h in PALETTE}, rgbas
    assert not re.search(r"#[0-9a-fA-F]{3}\b", html)
    assert not any(0x1F300 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF or c == "\ufe0f" for c in html)
    assert html.startswith('<h2 class="sr-only">')
    # outermost container: transparent (no background on it or its class rule); inside it, one night panel
    outer, panel = re.search(r"</style>\s*(<div[^>]*>)\s*(<div[^>]*>)", html).groups()
    assert "background" not in outer
    rc_rule = re.search(r"\.rc\{[^}]*\}", html).group(0)
    assert "background" not in rc_rule
    assert panel == '<div class="np">'
    np_rule = re.search(r"\.np\{[^}]*\}", html).group(0)
    assert "background:#0D1218" in np_rule and "border-radius:16px" in np_rule
    for svg in re.findall(r"<svg[^>]*>", html):
        assert 'role="img"' in svg and "aria-label=" in svg, svg
    # sentence case: no shouty words in visible text; no "A · B" meta strings
    for text in re.findall(r">([^<]+)<", html):
        for word in re.findall(r"\b[A-Z]{3,}\b", text):
            assert word in ACRONYMS, (word, text)
        assert "·" not in text, text
    p = _Balance()
    p.feed(html)
    p.close()
    assert p.stack == [], p.stack


def test_host_rules_catch_host_variables_and_effects(daily):
    html = daily_card(daily)
    for bad in ('<style>.x{color:var(--text-primary,#0b0b0b)}</style>', '<style>.x{box-shadow:0 1px #000}</style>',
                '<style>.x{background:linear-gradient(#000,#fff)}</style>', '<style>.x{font-size:12px}</style>',
                '<style>.x{color:#123456}</style>', '<style>.x{font-weight:600}</style>'):
        with pytest.raises(AssertionError):
            check_host_rules(html.replace("<style>", bad + "<style>", 1))
    with pytest.raises(AssertionError):
        check_host_rules(html.replace('<div class="np">', '<div class="np" style="background:#000">', 1)
                         .replace('class="rc"', 'class="rc" style="background:#0D1218"', 1))


# ---------------------------------------------------------------- daily
def test_daily_size(daily):
    assert len(daily_card(daily).encode()) < DAILY_LIMIT
    assert len(daily_card(heavy_daily(daily)).encode()) < DAILY_LIMIT


def test_daily_size_worst_case(daily):
    assert len(daily_card(worst_daily(daily)).encode()) < DAILY_LIMIT
    # free text and unknown codes are clipped, so even absurd inputs stay inside the limit
    d = worst_daily(daily)
    d["flags"] = [f"unknown_flag_code_number_{i}" for i in range(6)] + d["flags"]
    d["strain"]["workouts"] = [dict(w, kind=f"someVeryLongUnknownWorkoutKind{i}")
                               for i, w in enumerate(d["strain"]["workouts"])]
    d["recovery"]["cap_reason"] = "x" * 300
    d["strain"]["flag"] = "y" * 300
    assert len(daily_card(d).encode()) < DAILY_LIMIT


def test_workouts_capped_at_four_highest_strain_in_time_order(daily):
    html = daily_card(worst_daily(daily))
    # strains 4.0, 5.3, ... 13.1 in start order: the four highest are the last four workouts
    shown = ["HIIT", "Strength training", "Mixed cardio", "Other workout"]
    positions = [html.index(name, html.index("Yesterday’s workouts")) for name in shown]
    assert positions == sorted(positions)
    for hidden in ("Running", "Walking", "Cycling"):
        assert hidden not in html
    assert "+4 more" in html


def test_long_stress_day_lists_top_hours_instead_of_cell_values(daily):
    html = daily_card(worst_daily(daily))
    assert not re.search(r'<i class="k\d">[\d.]+</i>', html)
    assert "Most stressed: 9 pm (2.5), 10 pm (2.6), 11 pm (2.8)" in html
    eighteen = daily_card(heavy_daily(daily))
    assert re.search(r'<i class="k\d">[\d.]+</i>', eighteen)


def test_daily_host_rules(daily):
    check_host_rules(daily_card(daily))
    check_host_rules(daily_card(heavy_daily(daily)))
    check_host_rules(daily_card(worst_daily(daily)))
    check_host_rules(daily_card(mostly_null_daily()))
    check_host_rules(daily_card({}))


def test_no_external_refs(daily, weekly):
    for html in (daily_card(daily), weekly_card(weekly)):
        for banned in ("http", "<script src", "@import"):
            assert banned not in html


def test_no_brand_terms(daily, weekly):
    assert "whoop" not in daily_card(daily).lower()
    assert "whoop" not in weekly_card(weekly).lower()


def test_values_present(daily):
    html = daily_card(daily)
    for s in ("82",            # recovery score
              "Green",         # zone word next to the colour
              "13.4",          # yesterday's strain
              "91%",           # sleep performance
              "Ready to push — HRV well above your normal",
              "68 ms", ">+22<", ">−2<",          # HRV contributor, points
              "7h 47m", "8h 33m", "14m",          # sleep vs need, debt
              "10:35 pm",                         # bedtime tonight
              "14–18",                            # strain target
              "Running", "Strength training", "10.8", "7.9",
              "1.1", "Medium",                    # stress score and band
              "121", "135", "−13", "1.11",        # fitness, fatigue, form, ACWR
              "61", "44", "29",                   # 7-day recovery
              "15.9", "97%"):                     # 7-day strain, sleep
        assert s in html, s


def test_missing_recovery_renders_dash(daily):
    daily["recovery"] = {"score": None, "zone": None, "uncapped": None, "contributors": [],
                         "cap_reason": None, "reason_missing": "no HRV readings last night"}
    html = daily_card(daily)
    assert "—" in html
    assert "no HRV readings last night" in html
    assert "Ready to push" not in html


def test_mostly_null_daily_renders_reasons():
    html = daily_card(mostly_null_daily())
    assert html.count("—") >= 4
    assert "9 of 14 nights collected" in html
    assert "building stress baseline: 31 of 50 still hours" in html


def test_empty_daily_does_not_crash():
    assert "—" in daily_card({})


def test_null_values_inside_sections(daily):
    for key in ("sleep", "strain", "load"):
        daily[key] = {k: None for k in daily[key]}
    daily["trend"] = [{"date": t["date"], "recovery": None, "zone": None, "strain": None, "perf": None}
                      for t in daily["trend"]]
    daily["bands"] = {"hrv": [], "sleep_hr": None}
    check_host_rules(daily_card(daily))


def test_flags_banner(daily):
    assert "take it easy" not in daily_card(daily).lower()
    daily["flags"] = ["hrv_low", "sleep_hr_high", "illness_warning"]
    html = daily_card(daily)
    assert "Possible illness or strain — take it easy" in html
    assert "Outside your normal range: low HRV, high sleeping heart rate" in html


def test_headline_by_zone(daily):
    daily["recovery"].update(score=52, zone="yellow")
    assert "Steady day —" in daily_card(daily)
    daily["recovery"].update(score=25, zone="red")
    assert "Take it easy —" in daily_card(daily)


def test_breathing_rate_phrase_uses_below_and_above(daily):
    resp = {"name": "Respiratory rate", "value": 13.1, "baseline": 14.8, "z": 1.4, "points": 6.0}
    daily["recovery"].update(score=70, zone="green", contributors=[resp])
    html = daily_card(daily)
    assert "Ready to push — breathing rate well below your normal" in html
    daily["recovery"].update(score=40, zone="yellow", contributors=[dict(resp, z=-1.4, points=-6.0)])
    assert "Steady day — breathing rate well above your normal" in daily_card(daily)
    assert "well at" not in html


def test_zone_legend_under_recovery_bars(daily, weekly):
    legend = "Green 67+", "Yellow 34–66", "Red 0–33"
    for html in (daily_card(daily), weekly_card(weekly)):
        for word in legend:
            assert word in html


def test_headline_shows_as_zone_lead_and_detail_under_the_ring(daily):
    html = daily_card(daily)
    assert '<div class="hl c1">Ready to push</div><div>HRV well above your normal</div>' in html
    assert "Ready to push HRV well above your normal" in visible_text(html)
    assert "Green: ready to perform" in visible_text(html)            # the zone in words, not only colour
    daily["recovery"].update(score=50, zone="yellow", uncapped=66, cap_reason="sleep performance under 60%")
    assert "Steady day Held back by sleep performance under 60%" in visible_text(daily_card(daily))
    daily["flags"] = ["illness_warning"]
    assert '<div class="hl c3">Take it easy</div>' in daily_card(daily)   # illness overrides the zone colour


def test_stress_colour_matches_printed_number(daily):
    base = daily["stress"]["hourly"][0][0]
    daily["stress"]["hourly"] = [[base, 0.98], [base.replace("T07:", "T08:"), 0.44], [base.replace("T07:", "T09:"), 1.96]]
    html = daily_card(daily)
    assert '<i class="k3">1.0</i>' in html     # prints 1.0, so it is coloured as 1.0 (medium), not as 0.98
    assert '<i class="k1">0.4</i>' in html
    assert '<i class="k5">2.0</i>' in html


def test_cap_reason_shown(daily):
    daily["recovery"].update(score=50, zone="yellow", uncapped=66, cap_reason="sleep performance under 60%")
    html = daily_card(daily)
    assert "sleep performance under 60%" in html
    assert "66" in html


def test_text_is_escaped(daily):
    daily["recovery"] = {"score": None, "reason_missing": "<script>alert(1)</script>"}
    html = daily_card(daily)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------- weekly
def test_weekly_size(weekly):
    assert len(weekly_card(weekly).encode()) < WEEKLY_LIMIT
    assert len(weekly_card(heavy_weekly(weekly)).encode()) < WEEKLY_LIMIT


def test_weekly_host_rules(weekly):
    check_host_rules(weekly_card(weekly))
    check_host_rules(weekly_card(heavy_weekly(weekly)))
    check_host_rules(weekly_card(mostly_null_weekly()))
    check_host_rules(weekly_card({}))


def test_weekly_values_present(weekly):
    html = weekly_card(weekly)
    for s in ("67%", "+13",                         # recovery average and delta vs prior 4 weeks
              "51 bpm",                             # resting HR average
              "86", "38",                           # best and worst day
              "HRV 74 ms, highest in 3 weeks",      # why
              "28.7", "−1.9 years",                 # body age and difference
              "0.8×",                               # pace of aging
              "−0.7", "−0.9", "+0.2",               # item years x 0.6 (activity, VO2 max, regularity)
              "approx.",                            # rounded parts do not add up exactly
              "On days with alcohol, next-morning recovery averaged 15 points lower",
              "Protect sleep before hard sessions"):
        assert s in html, s
    assert "estimate from population research, not a medical test" in html.lower()


def test_weekly_hides_strength_note_when_body_age_present(weekly):
    assert "since strength records began" not in weekly_card(weekly)
    weekly["body_age"]["body_age"] = None
    weekly["body_age"]["items"] = []
    assert "since strength records began" in weekly_card(weekly)


def test_weekly_parts_add_up_exactly_no_approx(weekly):
    ba = weekly["body_age"]
    ba["items"] = [{"name": "rhr", "value": 52, "years": -1.0}, {"name": "vo2max", "value": 49, "years": 0.5}]
    ba["body_age"] = ba["age"] - 0.3
    html = weekly_card(weekly)
    assert "approx." not in html
    assert "−0.6" in html and "+0.3" in html


def test_weekly_sri_clamped_for_display(weekly):
    for item in weekly["body_age"]["items"]:
        if item["name"] == "sleep_regularity":
            item["value"] = -12.3
    html = weekly_card(weekly)
    assert "SRI 0" in html
    assert "SRI −12" not in html


def test_weekly_mostly_null_renders_dash():
    html = weekly_card(mostly_null_weekly())
    assert html.count("—") >= 7


def test_weekly_empty_does_not_crash():
    assert "—" in weekly_card({})


def test_day_zone_matches_printed_number_at_half_points(weekly):
    weekly["best_day"]["recovery"] = 66.5   # prints 67 -> Green, not Yellow
    weekly["worst_day"]["recovery"] = 33.5  # prints 34 -> Yellow, not Red
    html = weekly_card(weekly)
    assert re.search(r"67%.{0,80}Green", html) and not re.search(r"67%.{0,80}Yellow", html)
    assert re.search(r"34%.{0,80}Yellow", html) and not re.search(r"34%.{0,80}Red<", html)


def test_delta_tone_good_and_bad():
    assert delta_tone("recovery", 67, 54) == "good"
    assert delta_tone("rhr", 51, 53) == "good"
    assert delta_tone("stress", 1.3, 1.1) == "bad"
    assert delta_tone("sleep_perf", 0.80, 0.89) == "bad"
    assert delta_tone("strain", 13, 11) == "neutral"
    assert delta_tone("hrv", None, 57) == "neutral"


# ---------------------------------------------------------------- back-test (first run)
def backtest_days(n=60):
    first = date(2026, 8, 22)   # a Saturday
    out = []
    for i in range(n):
        score = None if i < 14 else (i * 37) % 100
        zone = None if score is None else "green" if score >= 67 else "yellow" if score >= 34 else "red"
        out.append({"date": (first + timedelta(days=i)).isoformat(), "recovery": score, "zone": zone})
    return out


def test_backtest_card():
    days = backtest_days()
    html = backtest_card(days)
    check_host_rules(html)
    assert len(html.encode()) < DAILY_LIMIT
    scored = [d for d in days if d["recovery"] is not None]
    for d in scored:
        assert f'>{d["recovery"]}</i>' in html
    zones = [d["zone"] for d in scored]
    text = visible_text(html)
    assert f'{zones.count("green")} green {zones.count("yellow")} yellow {zones.count("red")} red' in text
    assert "14 days not scored" in text
    assert f'{zones.count("green")} green, {zones.count("yellow")} yellow, {zones.count("red")} red' in html  # summary
    assert "Aug 17" in html            # weeks start on Monday: the first row is the week of Aug 17
    assert "Green 67+" in html          # zone colours always come with a word


def test_backtest_card_empty():
    check_host_rules(backtest_card([]))
    check_host_rules(backtest_card([{"date": "bad"}, None]))


# ---------- workout card and training blocks ----------

from recovery_coach.card import workout_card  # noqa: E402

WORKOUT = json.loads((Path(__file__).parent / "fixtures" / "card_workout.json").read_text())


def test_workout_card_size_limit():
    assert len(workout_card(WORKOUT).encode()) < 14_000


def test_workout_card_prints_every_number():
    html = workout_card(WORKOUT)
    for text in ("3.7", "mostly threshold", "38 h", "17", "34", "26", "14.1", "57", "357", "6:47", "0.948", "6.6",
                 "32.5", "41.8", "6:47", "8.1", "734", "approx"):
        assert text in html, text


def test_workout_card_nulls_render_dash_with_reason():
    bare = {"id": "x", "kind": "traditionalStrengthTraining", "start": "2026-11-20T07:09:00-06:00",
            "end": "2026-11-20T07:57:00-06:00", "minutes": 48, "hr": {}, "load": {}, "effect": {},
            "recovery": {"reason": "heart rate too sparse"}, "run": None, "notes": ["no heart-rate readings"]}
    html = workout_card(bare)
    assert "—" in html and "Heart rate too sparse" in html and "No heart-rate readings" in html


def test_workout_card_host_rules():
    html = workout_card(WORKOUT)
    assert "<script" not in html and "gradient" not in html and "xmlns" not in html
    assert "font-weight:600" not in html and "font-weight:700" not in html


def test_daily_card_workout_line():
    d = json.loads((Path(__file__).parent / "fixtures" / "card_daily.json").read_text())
    d["strain"]["workouts"] = [{"kind": "running", "start": "2026-11-20T08:23:00-06:00", "minutes": 51,
                                "strain": 14.1, "id": "2026-11-20T08:23-0600", "te": 3.7, "rec_h": 37.7,
                                "hrr60": 17.0, "benefit": "Threshold"}]
    d["training"] = {"form_pct": -8.3, "ramp": 2.1, "easy_strain": 9.4, "monotony": 1.62, "mono_flag": "warn",
                     "tstrain": 1450, "srpe_ratio": 1.12, "polar_band": "productive", "garmin_band": "optimal",
                     "split7": [120, 40, 8], "split28": [500, 120, 20], "pi7": 2.05, "pi28": 1.9, "focus_short": [],
                     "target_week": [600, 980], "run_tol_km": 23.0, "status": "productive",
                     "hrv_status": "balanced", "recovery_index": {"hours": 6.4, "late_nadir": False,
                                                                  "late_workout": False}}
    html = daily_card(d)
    assert "TE 3.7" in html and "38 h" in html and "HRR 17" in html
    assert "1.62" in html and "Productive" in html and "Garmin: optimal" in html and "6.4 h" in html
    assert len(html.encode()) < 14_000


def test_weekly_card_training_section():
    w = json.loads((Path(__file__).parent / "fixtures" / "card_weekly.json").read_text())
    w["training"] = {"week_wload": 320.0, "monotony": 1.4, "tstrain": 448, "split": [120.0, 48.0, 12.0],
                     "pi": 2.12, "focus_short": ["anaerobic"], "status": "productive",
                     "race": {"shape": 41.2, "marathon_shape": 38, "vdot": 41.2,
                              "predictions": {"5k": 1500, "10k": 3120, "half": 6930, "marathon": 15100},
                              "riegel": {"5k": 1560, "10k": 3250},
                              "paces": {"easy": 400, "marathon": 340, "threshold": 320, "interval": 295,
                                        "repetition": 275}, "cs": 3.45, "d_prime": 180},
                     "uth_vo2": 43.6}
    html = weekly_card(w)
    for text in ("25:00", "52:00", "1:55:30", "4:11:40", "41.2", "2.12", "6:40", "anaerobic", "43.6"):
        assert text in html, text
    assert len(html.encode()) < 24_000
