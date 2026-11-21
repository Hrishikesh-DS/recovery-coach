"""Daily and weekly cards: self-contained HTML fragments shown inline by Claude's visual widget host.

Host rules (the card is an iframe fragment, ~380 px wide on a phone): no DOCTYPE/html/head/body; a transparent outer
container holding one night panel (16 px radius) drawn in a fixed palette, the same on a light or a dark host, so the
card always looks like the app; flat fills only (no gradients, shadows, blur or filters: they flash while the host
streams the HTML); weights 400/500 only, sentence case, font sizes from the 11/13/15/20/28/64 px scale, no comments,
no scripts, no external resources, inline SVG without xmlns. Every number is printed (phones have no hover); every
value may be null and then renders "—" with the reason when one is given.

Result-dict schema (the contract; the CLI emits exactly this; any value may be null, any key may be missing):

daily_card(result):
  date            "YYYY-MM-DD" (the wake date)
  recovery        {score: int 0-100, zone: "green"|"yellow"|"red", uncapped: int (score before caps),
                   contributors: [{name, value, baseline, z, points}], cap_reason: str, reason_missing: str}
                   contributor names as recovery.py writes them ("HRV", "Sleeping HR" | "Resting HR",
                   "Sleep performance", "Respiratory rate"); value and baseline in DISPLAY units: HRV ms (not ln),
                   HR bpm, sleep performance 0-1, resp breaths/min; z is sign-flipped so positive = better;
                   points = what the input added to (or removed from) the normal-day score of 57
  sleep           {actual_min, need_min, perf (0-1), debt_min, naps_min, efficiency (0-1), consistency_min,
                   interruptions, deep_min, rem_min, onset (ISO), wake (ISO), bedtime_tonight (ISO)}
                   ISO datetimes carry their own UTC offset and are shown in that local time
  strain          {yesterday: float 0-21, flag: str (e.g. "may be low: 9 h of heart-rate data"), today_so_far: float,
                   target: [lo, hi], workouts: [{kind (HealthKit type, e.g. "running"), start: ISO, minutes,
                   strain (null = no heart-rate data)}]}
  stress          {score: float 0-3, band: "low"|"medium"|"high", hourly: [[ISO hour start, score 0-3]],
                   top_hours: [ISO], reason: str (why there is no score)}   -- yesterday's waking hours
  load            {ctl, atl, tsb, acwr, acwr_flag: "spike"|"detraining"|null}
  flags           ["hrv_low", "sleep_hr_high", "resp_high", "spo2_low", "wtemp_high", "illness_warning"]
  trend           [{date, recovery, zone, strain, perf}] last 7 days incl. today, oldest first; strain is that
                   calendar day's strain (null for today until the day is over)
  bands           {hrv: [{date, value, centre, spread}], sleep_hr: [...]} last 14 days, oldest first, display units
                   (HRV ms, HR bpm); the shaded "your normal" band is centre - spread .. centre + spread

weekly_card(result):
  week_start, week_end   "YYYY-MM-DD"
  averages        {recovery, strain, sleep_perf (0-1), sleep_min, hrv (ms), rhr (bpm), stress (0-3)} this week
  prior           same keys, mean of the 4 weeks before
  best_day        {date, recovery, why: str} | null          worst_day: same
  series          [{date, recovery, zone, strain, perf, stress}] 28 days ending week_end, oldest first
  trends          {hrv: [{date, value}], rhr: [...], vo2: [...]} oldest first (VO2 max readings may be sparse)
  load            [{date, ctl, atl, tsb}] oldest first
  body_age        {body_age: float, age: float, pace: float (-1..3), items: [{name, value, years}],
                   missing: [[item, reason]]} | null
                   item names: sleep_duration (h), sleep_regularity (raw SRI -100..100, shown clamped 0-100),
                   activity (steps/day; strength min/week when missing lists "steps"), vo2max, rhr, body_comp (BMI);
                   years are BEFORE the x0.6 scaling: the card shows years x 0.6 so the parts add up to body_age - age
  journal         [{question, yes_n, no_n, yes_mean, no_mean, diff}]   (diff = yes_mean - no_mean)
  focus           str | null   one line for the coming week

Rendering notes for the CLI: the daily headline is written here from zone, cap and the largest contributor (an
illness_warning flag overrides it) and shows as a zone-coloured lead under the recovery ring with its detail below;
the "your normal" band is drawn only when every day has centre and spread; stress hours missing between the first and
last hour show as empty cells; best/worst day and focus are hidden when null; the Body Age "strength" note in missing
is hidden when body_age is present; journal questions read "On days with <question>" ("late meal" and "stressful
day" have their own phrasing). Size: daily < 14 KB, weekly < 24 KB.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from .recovery import zone_for

DASH = "—"
MINUS = "−"

# ---------------------------------------------------------------- tokens: one fixed palette on any host
NIGHT, DECK, RULE = "#0D1218", "#151C24", "#232D38"     # panel, raised tiles, hairlines and tracks
CHALK, FOG, MIST = "#EDF1F5", "#8F9BA8", "#5F6B78"      # primary, secondary, muted text
ZONE = {"green": "#34E089", "yellow": "#F5C542", "red": "#FF5468"}
GREEN, YELLOW, RED = ZONE["green"], ZONE["yellow"], ZONE["red"]
STRAIN, SLEEP, HR_C, ORANGE = "#3EA6FF", "#9A8CFF", "#FF8FAB", "#FF7A45"
HRV_C = GREEN
STRESS_RAMP = ("#2A3442", "#3D4754", "#CCA842", YELLOW, ORANGE)   # calm -> yellow -> orange, 0-3; low stays calm
TEXT_FONT = 'system-ui,-apple-system,"SF Pro Text","Segoe UI",Roboto,sans-serif'
NUM_FONT = 'ui-rounded,"SF Pro Rounded",system-ui,sans-serif'

ZONE_CLASS = {"green": "z1", "yellow": "z2", "red": "z3"}
ZONE_WORD = {"green": "Green", "yellow": "Yellow", "red": "Red"}
ZONE_MEANING = {"green": "ready to perform", "yellow": "steady, maintain", "red": "recover today"}
ZONE_LEGEND = ('<span class="x"><i class="dot z1"></i>Green 67+ <i class="dot z2"></i>Yellow 34–66 '
               '<i class="dot z3"></i>Red 0–33</span>')  # zone colours always come with a word


def _a(colour: str, alpha: float) -> str:
    """A token colour at a flat alpha, for ring tracks, bands and earlier days."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha:g})".replace(",0.", ",.")


CSS = (  # shared by every card; class names are short because Claude retypes the card each morning
    f".rc{{font:400 15px/1.4 {TEXT_FONT};color:{CHALK}}}"
    f".np{{background:{NIGHT};border-radius:16px;padding:20px 20px 18px}}"
    f".n,.v,.w,.hv{{font-family:{NUM_FONT};font-variant-numeric:tabular-nums}}"
    ".rc b,.rc h3,.v,.w,.hv,.hl{font-weight:500}"
    f".s{{font-size:13px;color:{FOG}}}.m{{font-size:13px;color:{MIST}}}.x{{font-size:11px;color:{MIST}}}"
    ".v{font-size:20px;line-height:1.25}.w,.hv{letter-spacing:-.02em;line-height:1.1}.w{font-size:28px}"
    ".hv{font-size:64px;line-height:1}.hv small{font-size:28px;margin-left:2px}"
    ".rc h3{font-size:15px;margin:0 0 14px}.hl{font-size:20px}"
    f".sc{{border-top:1px solid {RULE};padding-top:20px;margin-bottom:24px}}.cols{{margin-top:24px}}"
    ".sb{display:flex;justify-content:space-between;align-items:baseline;gap:12px}"
    ".g2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 20px}"
    f".tl{{background:{DECK};border-radius:12px;padding:16px;min-width:0}}.tl .m{{color:{FOG}}}"
    ".dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin:0 5px 1px 0}"
    f".z1{{background:{GREEN}}}.z2{{background:{YELLOW}}}.z3{{background:{RED}}}.z0{{background:{RULE}}}"
    f".c1{{color:{GREEN}}}.c2{{color:{YELLOW}}}.c3{{color:{RED}}}"
    ".el{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.r{text-align:right}"
    f".db{{display:block;position:relative;height:4px;border-radius:2px;background:{RULE}}}"
    f".db:after{{content:\"\";position:absolute;left:50%;top:-4px;bottom:-4px;width:1px;background:{MIST}}}"
    ".db i{position:absolute;top:0;bottom:0;border-radius:2px}"
    ".sp{display:block;width:100%;overflow:visible}"
    "svg path{fill:none;stroke-linecap:round;stroke-linejoin:round}"
    ".sp path{stroke:currentColor;stroke-width:2;vector-effect:non-scaling-stroke}"
    f".sp .ba{{fill:currentColor;fill-opacity:.16;stroke:none}}.sp .h{{stroke:{NIGHT};stroke-width:12}}"
    ".sp .e{stroke-width:7}"
    ".st{display:flex;gap:3px}.st>*{flex:1;min-width:0;font-style:normal;font-size:11px;text-align:center}"
    f".st i{{height:28px;line-height:28px;border-radius:6px;color:{NIGHT}}}"
    + "".join(f".k{i}{{background:{c}}}" for i, c in enumerate(STRESS_RAMP, 1))
    + f".st .k1,.st .k2{{color:{CHALK}}}.k0{{outline:1px solid {RULE};outline-offset:-1px}}"
    ".sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}"
    "@media(min-width:760px){.np{padding:28px 32px 22px}.hd{display:grid;grid-template-columns:1fr 1fr;"
    "gap:24px 56px;align-items:center}.cols{columns:2;column-gap:56px}.cols>*{break-inside:avoid}}"
)
FOOTER = '<div class="x">Estimates from your watch data, not medical advice.</div>'


# ---------------------------------------------------------------- formatting
def _d(x) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x) -> list:
    return x if isinstance(x, list) else []


def _e(s) -> str:
    return escape(str(s))


def _f(x) -> float | None:
    """A finite number, or None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _half_up(v: float) -> int:
    """Round half away from zero, as every printed number is (Python's round() goes to even: 66.5 -> 66)."""
    return int(Decimal(repr(v)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _r(x, nd: int = 0, comma: bool = False) -> str:
    """Half-up rounding to nd decimals, true minus sign, "—" when missing."""
    v = _f(x)
    if v is None:
        return DASH
    q = Decimal(repr(v)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    if q == 0:
        q = abs(q)
    return (f"{q:,}" if comma else str(q)).replace("-", MINUS)


def _sg(x, nd: int = 0) -> str:
    """Signed: +1.2 / −1.2 / 0."""
    s = _r(x, nd)
    return s if s == DASH or s.startswith(MINUS) or _f(s) == 0 else "+" + s


def _unit(x, unit: str, nd: int = 0) -> str:
    return DASH if _f(x) is None else f"{_r(x, nd)}{unit}"


def _pct(x) -> str:
    v = _f(x)
    return DASH if v is None else _r(v * 100) + "%"


def _hm(minutes) -> str:
    v = _f(minutes)
    if v is None:
        return DASH
    h, m = divmod(_half_up(abs(v)), 60)
    return f"{h}h {m}m" if h and m else f"{h}h" if h else f"{m}m"


def _dt(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day(s) -> date | None:
    if not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _clock(s) -> str:
    t = _dt(s)
    return DASH if t is None else f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def _hour(t: datetime) -> str:
    return f"{t.hour % 12 or 12} {'am' if t.hour < 12 else 'pm'}"


def _md(s) -> str:
    d = _day(s)
    return DASH if d is None else f"{d:%b} {d.day}"


def _wd(s) -> str:
    d = _day(s)
    return DASH if d is None else f"{d:%a}, {d:%b} {d.day}"


def _long(s) -> str:
    d = _day(s)
    return DASH if d is None else f"{d:%A}, {d:%B} {d.day}"


def _clip(text, n: int) -> str:
    """Free text cut to n characters (keeps every card inside its size limit whatever the input says)."""
    text = str(text)
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _cap(text: str) -> str:
    """Sentence case for a phrase that now starts its own line."""
    return text[:1].upper() + text[1:]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- building blocks
def _section(title: str, body: str, extra: str = "") -> str:
    return f'<div class="sc"{extra}><h3>{_e(title)}</h3>{body}</div>'


def _empty(text: str) -> str:
    return f'<div class="s">{DASH} {_e(text)}</div>'


def _stat(label: str, value: str, sub: str = "", cls: str = "v") -> str:
    """A label over a numeral, with an optional muted line: numbers sit on the panel, no box around them."""
    return (f'<div><div class="s">{_e(label)}</div><div class="{cls}">{value}</div>'
            + (f'<div class="x">{sub}</div>' if sub else "") + "</div>")


def _ring(size: int, sw: int, frac, colour: str, label: str, centre: str, under: str = "", track: str = "") -> str:
    """Ring: flat track (the metric colour at low alpha), optional marks under the value arc, round-capped value arc
    clockwise from 12 o'clock, HTML text in the middle."""
    c, r = size // 2, (size - sw) // 2
    circ = 2 * math.pi * r
    f = _clamp(frac) if _f(frac) is not None else 0.0
    arc = (f'<circle cx="{c}" cy="{c}" r="{r}" stroke="{colour}" stroke-linecap="round" '
           f'stroke-dasharray="{f * circ:.1f} {circ:.1f}"/>') if f > 0 else ""
    return (f'<div class="dl"><svg width="{size}" height="{size}" fill="none" stroke-width="{sw}" role="img" '
            f'aria-label="{_e(label)}"><circle cx="{c}" cy="{c}" r="{r}" stroke="{track or _a(colour, .16)}"/>'
            f'{under}{arc}</svg><div class="dc">{centre}</div></div>')


def _diverge(v: float | None, full: float, good_right: bool = True) -> str:
    """Thin centred diverging bar: right of centre for positive values, left for negative; green for the good side."""
    if v is None:
        return '<i class="db"></i>'
    w = _clamp(abs(v) / full) * 50
    if w < 0.5:
        return '<i class="db"></i>'
    good = (v > 0) == good_right
    side = "left" if v > 0 else "right"
    return f'<i class="db"><i class="{"z1" if good else "z3"}" style="{side}:50%;width:{w:.0f}%"></i></i>'


def _sparkline(vals: list[float | None], colour: str, label: str, band: list[tuple[float, float]] | None = None,
               height: int = 56, xs: list[float] | None = None) -> str:
    """Line (2 px, end dot ringed in the panel colour) over an optional flat band; stretches to the width, strokes
    stay 2 px. xs: positions 0..1 for each value (default evenly spaced)."""
    pts = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(pts) < 2:
        return f'<div class="s" style="height:{height}px;line-height:{height}px">{DASH} Not enough readings</div>'
    xs = xs or [i / (len(vals) - 1) for i in range(len(vals))]
    every = [v for _, v in pts] + [b for pair in (band or []) for b in pair]
    lo, hi = min(every), max(every)
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad

    def xy(i, v):
        return f"{xs[i] * 200:.0f} {(1 - (v - lo) / (hi - lo)) * 60:.0f}"

    shade = ""
    if band:
        top = " ".join(xy(i, b[1]) for i, b in enumerate(band))
        bottom = " ".join(xy(i, b[0]) for i, b in reversed(list(enumerate(band))))
        shade = f'<path class="ba" d="M{top} {bottom}Z"/>'
    segs, cur = [], []
    for i, v in enumerate(vals):
        if v is None:
            segs, cur = (segs + [cur] if cur else segs), []
        else:
            cur.append(xy(i, v))
    segs += [cur] if cur else []
    line = " ".join("M" + " ".join(s) for s in segs if len(s) > 1)
    end = xy(*pts[-1])
    return (f'<svg class="sp" viewBox="0 0 200 60" preserveAspectRatio="none" style="height:{height}px;color:{colour}" '
            f'role="img" aria-label="{_e(label)}">{shade}<path d="{line}"/><path class="h" d="M{end}h0"/>'
            f'<path class="e" d="M{end}h0"/></svg>')


def _stress_cell(v: float | None, label: bool = True) -> str:
    """One 0-3 stress cell on the calm-to-orange ramp; the value is printed inside when label is True."""
    if v is None:
        return '<i class="k0"></i>'
    text = _r(v, 1)
    p = float(text.replace(MINUS, "-"))   # the colour always matches the printed number
    k = 1 if p < 0.5 else 2 if p < 1 else 3 if p < 1.5 else 4 if p < 2 else 5
    return f'<i class="k{k}">{text if label else ""}</i>'


def _tone(tone: str) -> str:
    """Class for a good/bad change; neutral inherits the surrounding colour."""
    return {"good": "c1", "bad": "c3"}.get(tone, "")


# ---------------------------------------------------------------- daily
DAILY_CSS = (
    ".dl{position:relative;width:max-content;margin:0 auto}.dl svg{display:block;transform:rotate(-90deg)}"
    ".dc{position:absolute;inset:0;display:grid;place-content:center;text-align:center}"
    ".hc{text-align:center;padding:12px 0 20px}.hc .hl{margin-top:16px}"
    ".cb{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto 56px;gap:16px 10px;align-items:center}"
    ".tw{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.tw .tl{text-align:center}"
    ".tw .dl{margin-bottom:12px}"
    f".br{{position:relative;height:8px;border-radius:4px;background:{_a(SLEEP, .16)};margin:12px 0 8px}}"
    f".br i{{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:{SLEEP}}}"
    f".br b{{position:absolute;top:-4px;bottom:-4px;width:2px;margin-left:-1px;border-radius:1px;background:{CHALK}}}"
    f".wo{{display:flex;align-items:center;gap:14px;padding:10px 0;border-top:1px solid {RULE}}}"
    ".rc h3+.wo,.fw+.wo{border:0;padding-top:0}.wo>b{min-width:44px;text-align:right}"
    f".mt{{position:relative;width:64px;height:4px;border-radius:2px;flex:none;background:{_a(STRAIN, .16)}}}"
    f".mt i{{position:absolute;left:0;top:0;bottom:0;border-radius:2px;background:{STRAIN}}}"
    ".bc{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px;align-items:end;text-align:center}"
    ".bc>*{display:grid;justify-items:center;gap:4px;font-size:13px}"
    f".bc i{{width:100%;max-width:24px;border-radius:4px 4px 0 0}}.bc b{{font-weight:400;color:{FOG}}}"
    f".bc>:last-child,.bc>:last-child b{{color:{CHALK};font-weight:500}}.bc>:not(:last-child) i{{opacity:.72}}"
    f".cs i{{background:{STRAIN}}}.cl i{{background:{SLEEP}}}.bh{{margin:16px 0 6px}}"
    f".fl{{background:{DECK};border-radius:12px;padding:12px 16px;margin:4px 0 12px}}"
)
FLAG_TEXT = {
    "hrv_low": "low HRV",
    "sleep_hr_high": "high sleeping heart rate",
    "resp_high": "high breathing rate",
    "spo2_low": "low blood oxygen",
    "wtemp_high": "high wrist temperature",
}
CONTRIB_SHORT = {"Sleep performance": "Sleep", "Respiratory rate": "Breathing"}
DRIVER = {  # name -> (subject, word when better, word when worse)
    "HRV": ("HRV", "above", "below"),
    "Sleeping HR": ("sleeping heart rate", "below", "above"),
    "Resting HR": ("resting heart rate", "below", "above"),
    "Sleep performance": ("sleep", "above", "below"),
    "Respiratory rate": ("breathing rate", "below", "above"),
}
WORKOUT_NAMES = {
    "traditionalStrengthTraining": "Strength training", "functionalStrengthTraining": "Strength training",
    "highIntensityIntervalTraining": "HIIT", "other": "Other workout", "coreTraining": "Core training",
}


def _contrib_value(name: str, v, unit: bool = True) -> str:
    if name == "Sleep performance":
        return _pct(v)
    if name == "Respiratory rate":
        return _unit(v, "/min" if unit else "", 1)
    return _unit(v, (" ms" if name == "HRV" else " bpm" if name.endswith("HR") else "") if unit else "")


def _driver_phrase(c: dict) -> str:
    subject, better, worse = DRIVER.get(c.get("name"), (str(c.get("name", "")).lower(), "above", "below"))
    z = _f(c.get("z")) or 0.0
    if abs(z) < 0.3:
        return f"{subject} near your normal"
    return f"{subject} {'well ' if abs(z) >= 1 else ''}{better if z > 0 else worse} your normal"


def _capped(rec: dict) -> bool:
    score, uncapped = _f(rec.get("score")), _f(rec.get("uncapped"))
    return bool(rec.get("cap_reason")) and score is not None and uncapped is not None and uncapped > score


def _headline_parts(rec: dict, flags: list) -> tuple[str, str]:
    """(lead, detail): the lead shows in the zone colour under the ring, the detail on the line below."""
    zone = rec.get("zone")
    if _f(rec.get("score")) is None or zone not in ZONE:
        return "No recovery score today", _clip(rec.get("reason_missing") or "not enough data", 80)
    if "illness_warning" in flags:
        return "Take it easy", "several body signals are out of your normal range"
    lead = {"green": "Ready to push", "yellow": "Steady day", "red": "Take it easy"}[zone]
    if _capped(rec):
        return lead, f"held back by {_clip(rec['cap_reason'], 56)}"
    cs = [c for c in _l(rec.get("contributors")) if isinstance(c, dict) and _f(c.get("points")) is not None]
    if zone == "green":
        cs = [c for c in cs if _f(c["points"]) > 0] or cs
    elif zone == "red":
        cs = [c for c in cs if _f(c["points"]) < 0] or cs
    if not cs:
        return lead, ZONE_MEANING[zone]
    return lead, _driver_phrase(max(cs, key=lambda c: abs(_f(c["points"]))))


def _headline(rec: dict, flags: list) -> str:
    return " — ".join(_headline_parts(rec, flags))


def _flags_banner(flags: list) -> str:
    if not flags:
        return ""
    ill = "illness_warning" in flags
    detail = ", ".join(_e(FLAG_TEXT.get(f, _clip(f.replace("_", " "), 24))) for f in flags if f != "illness_warning")
    title = "Possible illness or strain — take it easy" if ill else "Worth watching today"
    return (f'<div class="fl"><b class="{"c3" if ill else "c2"}">{title}</b>'
            + (f'<div class="s">Outside your normal range: {detail}</div>' if detail else "") + "</div>")


def _hero(rec: dict, flags: list) -> str:
    """The one bold element: the recovery ring in the zone colour, the lead and its detail centred under it."""
    score, zone = _f(rec.get("score")), rec.get("zone")
    lead, detail = _headline_parts(rec, flags)
    if score is None or zone not in ZONE:
        ring = _ring(180, 12, None, RULE, "Recovery: no score today",
                     f'<div class="hv" style="color:{MIST}">{DASH}</div><div class="s">Recovery</div>', track=RULE)
        return (f'<div class="hc">{ring}<div class="hl" style="color:{FOG}">{lead}</div>'
                f'<div>{_e(_cap(detail))}</div></div>')
    num = _r(score)
    ring = _ring(180, 12, score / 100, ZONE[zone], f"Recovery {num} of 100, {ZONE_WORD[zone]} zone",
                 f'<div class="hv">{num}<small>%</small></div><div class="s">Recovery</div>')
    tint = "c3" if "illness_warning" in flags else ZONE_CLASS[zone].replace("z", "c")
    return f'<div class="hc">{ring}<div class="hl {tint}">{lead}</div><div>{_e(_cap(detail))}</div></div>'


def _contributors(rec: dict) -> str:
    score, zone = _f(rec.get("score")), rec.get("zone")
    if score is None or zone not in ZONE:
        return ""
    rows = []
    for c in _l(rec.get("contributors"))[:4]:  # recovery.py writes at most four
        if not isinstance(c, dict):
            continue
        name, pts = str(c.get("name", "")), _f(c.get("points"))
        tone = "s" if pts is None or _r(pts) == "0" else "c1" if pts > 0 else "c3"
        rows.append(f'<span class="el">{_e(_clip(CONTRIB_SHORT.get(name, name), 16))}</span>'
                    f'<span class="n r">{_contrib_value(name, c.get("value"))}</span><span class="m n">vs '
                    f'{_contrib_value(name, c.get("baseline"), False)}</span><b class="n r {tone}">{_sg(pts)}</b>'
                    f'{_diverge(pts, 25)}')
    note = "Points added or removed vs a normal day (57)."
    if _capped(rec):
        note = f"Capped at {_r(score)} by {_e(_clip(rec['cap_reason'], 56))} (would be {_r(rec.get('uncapped'))}). " + note
    return f'<div class="cb">{"".join(rows)}</div><div class="x" style="margin-top:14px">{note}</div>'


def _strain_band(v: float | None) -> str:
    if v is None:
        return DASH
    return "Light" if v < 10 else "Moderate" if v < 14 else "High" if v < 18 else "All-out"


def _small_dials(st: dict, sl: dict) -> str:
    y, so_far, perf = _f(st.get("yesterday")), _f(st.get("today_so_far")), _f(sl.get("perf"))
    tgt = _l(st.get("target"))
    lo, hi = (_f(tgt[0]), _f(tgt[1])) if len(tgt) == 2 else (None, None)
    has_t = lo is not None and hi is not None
    band = ""
    if has_t:
        circ = 2 * math.pi * 44
        a0, a1 = _clamp(lo / 21), _clamp(hi / 21)
        band = (f'<circle cx="48" cy="48" r="44" stroke="{_a(STRAIN, .4)}" stroke-dasharray="{(a1 - a0) * circ:.1f} '
                f'{circ:.1f}" stroke-dashoffset="{-a0 * circ:.1f}"/>')
    tgt_txt = f"{_r(lo)}–{_r(hi)}" if has_t else DASH

    def notes(*pairs):  # muted lines, printed only when their value exists
        return "".join(f'<div class="m">{text}</div>' for text, value in pairs if _f(value) is not None)

    strain = ('<div class="tl">'
              + _ring(96, 8, (y or 0) / 21, STRAIN, f"Strain yesterday {_r(y, 1)} of 21, target today {tgt_txt}",
                      f'<div class="v">{_r(y, 1)}</div><div class="x">of 21</div>', band)
              + f'<div class="s">Strain yesterday</div><div>{_strain_band(y)}</div>'
              + notes((f"Target today {tgt_txt}", lo if has_t else None), (f"So far {_r(so_far, 1)}", so_far))
              + "</div>")
    sleep = ('<div class="tl">'
             + _ring(96, 8, perf, SLEEP, f"Sleep performance {_pct(perf)} of need",
                     f'<div class="v">{_pct(perf)}</div><div class="x">of need</div>')
             + f'<div class="s">Sleep performance</div><div>{_unit(sl.get("actual_min"), "") if _f(sl.get("actual_min")) is None else _hm(sl.get("actual_min")) + " asleep"}</div>'
             + notes((f'Need {_hm(sl.get("need_min"))}', sl.get("need_min")),
                     (f'Debt {_hm(sl.get("debt_min"))}', sl.get("debt_min")))
             + "</div>")
    return f'<div class="sc tw">{strain}{sleep}</div>'


def _sleep(sl: dict) -> str:
    actual, need = _f(sl.get("actual_min")), _f(sl.get("need_min"))
    if actual is None and need is None and _f(sl.get("perf")) is None:
        return _section("Sleep", _empty("No sleep recorded last night"))
    bar = ""
    if actual is not None and need:
        full = max(actual, need) * 1.08
        bar = f'<div class="br"><i style="width:{actual / full * 100:.1f}%"></i><b style="left:{need / full * 100:.1f}%"></b></div>'
    head = (f'<div class="sb"><span><b class="v">{_hm(actual)}</b> <span class="s">asleep</span></span>'
            f'<span class="s">Need {_hm(need)}</span></div>')
    times = (f'<div class="sb x"><span>{_clock(sl.get("onset"))} to {_clock(sl.get("wake"))}</span>'
             f'<span>Naps {_hm(sl.get("naps_min"))}</span></div>')
    bed = (f'<div class="sb" style="align-items:center;margin:20px 0 18px"><span class="s">Bedtime tonight</span>'
           f'<span class="w">{_clock(sl.get("bedtime_tonight"))}</span></div>')
    cons = _f(sl.get("consistency_min"))
    stats = (_stat("Efficiency", _pct(sl.get("efficiency"))) + _stat("Consistency", f"±{_r(cons)} min" if cons is not None else DASH)
             + _stat("Interruptions", _r(sl.get("interruptions")))
             + _stat("Deep / REM", f'{_hm(sl.get("deep_min"))} / {_hm(sl.get("rem_min"))}', "Low confidence"))
    return _section("Sleep", head + bar + times + bed + f'<div class="g2">{stats}</div>')


def _workout_name(kind: str) -> str:
    return WORKOUT_NAMES.get(kind) or "".join(" " + ch.lower() if ch.isupper() else ch for ch in kind).capitalize()


MAX_WORKOUTS = 4  # keeps the daily card under 14 KB on any day


def _shown_workouts(workouts: list) -> tuple[list[dict], int]:
    """The MAX_WORKOUTS highest-strain workouts (no strain counts lowest), in start-time order; and how many are hidden."""
    ws = [(i, w) for i, w in enumerate(workouts) if isinstance(w, dict)]
    top = sorted(ws, key=lambda iw: -(_f(iw[1].get("strain")) if _f(iw[1].get("strain")) is not None else -1.0))
    keep = top[:MAX_WORKOUTS]

    def when(iw):
        t = _dt(iw[1].get("start"))
        return (t.timestamp() if t else float("inf"), iw[0])

    return [w for _, w in sorted(keep, key=when)], len(ws) - len(keep)


def _workouts(st: dict) -> str:
    top = f'<div class="fw c2" style="font-size:13px;margin-bottom:10px">{_e(_cap(_clip(st["flag"], 64)))}</div>' if st.get("flag") else ""
    shown, hidden = _shown_workouts(_l(st.get("workouts")))
    rows = []
    for w in shown:
        s = _f(w.get("strain"))
        meter = f'<i class="mt"><i style="width:{_clamp(s / 21) * 100:.0f}%"></i></i>' if s is not None else ""
        fx = ""
        if _f(w.get("te")) is not None:
            fx = (f'<div class="x">TE {_r(w.get("te"), 1)} approx, {_r(w.get("rec_h"))} h to recover'
                  + (f', HRR {_r(w.get("hrr60"))}' if _f(w.get("hrr60")) is not None else "") + "</div>")
        rows.append(f'<div class="wo"><div class="el" style="flex:1">{_e(_clip(_workout_name(str(w.get("kind") or "workout")), 24))}'
                    f'<div class="m">{_clock(w.get("start"))}, {_unit(w.get("minutes"), " min")}</div>{fx}</div>{meter}'
                    + (f'<b class="v">{_r(s, 1)}</b>' if s is not None else '<span class="s">No HR</span>') + "</div>")
    if hidden:
        rows.append(f'<div class="wo m">+{hidden} more with lower strain</div>')
    body = "".join(rows) or '<div class="s">None logged yesterday</div>'
    return _section("Yesterday’s workouts", top + body)


def _bars(points: list[tuple[float | None, str, str]], scale: float, colour: str = "") -> str:
    """7-day small multiple: [(value, value text, bar class)], the value printed over each bar; today (the last
    column) in full colour, earlier days at 72 % (from the stylesheet). colour: a class giving every bar its colour
    (cs strain, cl sleep)."""
    cols = []
    for v, text, cls in points:
        h = 0 if v is None else max(2, round(_clamp(v / scale) * 40))
        attr = f' class="{cls}"' if cls else ""
        cols.append(f"<div><b>{text}</b>" + (f'<i{attr} style="height:{h}px"></i>' if h else "") + "</div>")
    return f'<div class="bc n{" " + colour if colour else ""}">{"".join(cols)}</div>'


def _trend(trend: list) -> str:
    rows = [t for t in trend if isinstance(t, dict)][-7:]
    if not rows:
        return _section("Last 7 days", _empty("No history yet"))
    labels = ["Today" if i == len(rows) - 1 else (f"{_day(t.get('date')):%a}" if _day(t.get("date")) else DASH)
              for i, t in enumerate(rows)]
    rec = [(_f(t.get("recovery")), _r(t.get("recovery")), ZONE_CLASS.get(t.get("zone"), "z0")) for t in rows]
    strain = [(_f(t.get("strain")), _r(t.get("strain"), 1), "") for t in rows]
    sleep = [(_f(t.get("perf")), _pct(t.get("perf")), "") for t in rows]
    days = "".join(f"<span>{_e(lab)}</span>" for lab in labels)
    body = (f'<div class="sb bh" style="margin-top:0"><span class="s">Recovery</span>{ZONE_LEGEND}</div>{_bars(rec, 100)}'
            f'<div class="s bh">Strain</div>{_bars(strain, 21, "cs")}'
            f'<div class="s bh">Sleep</div>{_bars(sleep, 1, "cl")}'
            f'<div class="bc x" style="margin-top:6px">{days}</div>')
    return _section("Last 7 days", body)


def _signal(name: str, rows: list, unit: str, colour: str) -> str:
    rows = [b for b in rows if isinstance(b, dict)]
    vals = [_f(b.get("value")) for b in rows]
    band = []
    for b in rows:
        c, s = _f(b.get("centre")), _f(b.get("spread"))
        band.append((c - s, c + s) if c is not None and s is not None else None)
    last = next((b for b in reversed(rows) if _f(b.get("value")) is not None), {})
    v, c, s = _f(last.get("value")), _f(last.get("centre")), _f(last.get("spread"))
    where = ""
    if v is not None and c is not None and s is not None:
        where = "Above normal" if v > c + s else "Below normal" if v < c - s else "Within normal"
    rng = f"{_r(c - s)}–{_r(c + s)}" if c is not None and s is not None else ""
    note = f"{where or 'Normal'} {rng}" if rng else (where or f"Normal range {DASH}")
    label = f"{name} last {len(rows)} days, latest {_r(v)} {unit}" + (f", normal {rng} {unit}" if rng else "")
    return (f'<div><div class="s">{name}</div><div class="v">{_unit(v, " " + unit)}</div>'
            f'<div class="x" style="margin-bottom:10px">{note}</div>'
            f'{_sparkline(vals, colour, label, band if band and all(band) else None)}</div>')


def _signals(bands: dict) -> str:
    hrv, hr = _l(bands.get("hrv"))[-14:], _l(bands.get("sleep_hr"))[-14:]
    if not any(isinstance(b, dict) and _f(b.get("value")) is not None for b in hrv + hr):
        return _section("Body signals", _empty("Not enough nights yet to show your normal range"))
    first = next((b.get("date") for b in hrv + hr if isinstance(b, dict) and b.get("date")), None)
    span = f"{_md(first)} to today. " if first else ""
    return _section("Body signals", f'<div class="g2">{_signal("HRV", hrv, "ms", HRV_C)}'
                                    f'{_signal("Sleeping HR", hr, "bpm", HR_C)}</div>'
                                    f'<div class="x" style="margin-top:12px">{span}The band is your normal range.</div>')


MAX_LABELLED_HOURS = 18  # beyond this a cell is too narrow at 380 px for "1.2"; the top hours are listed below


def _stress(stv: dict) -> str:
    score, band = _f(stv.get("score")), stv.get("band")
    if score is None:
        return _section("Stress yesterday", _empty(_clip(stv.get("reason") or "No data", 80)))
    head = (f'<div><span class="w">{_r(score, 1)}</span> <span class="s">'
            f'{_e(str(band).capitalize()) if band else DASH}, on a 0–3 scale</span></div>')
    hours = {}
    for h in _l(stv.get("hourly")):
        if isinstance(h, (list, tuple)) and len(h) == 2 and _dt(h[0]) is not None:
            hours[_dt(h[0]).replace(minute=0, second=0, microsecond=0)] = _f(h[1])
    strip = ""
    if hours:
        first = min(hours)
        n = min(24, int((max(hours) - first).total_seconds() // 3600) + 1)
        slots = [first + timedelta(hours=i) for i in range(n)]
        cells = "".join(_stress_cell(hours.get(t), n <= MAX_LABELLED_HOURS) for t in slots)
        ticks = ""
        for i in range(0, n, 3):  # one label per 3 cells, so each label starts at its hour's cell
            width = "" if n - i >= 3 else f' style="flex:{n - i}"'
            ticks += f'<span{width}>{_hour(slots[i]) if i + 2 < n else ""}</span>'
        strip = (f'<div class="st n" style="margin-top:14px" role="img" aria-label="Hourly stress yesterday, 0 to 3">'
                 f'{cells}</div><div class="st x" style="margin-top:6px">{ticks}</div>')
    tops = [_dt(t) for t in _l(stv.get("top_hours")) if _dt(t)]
    top = ", ".join(f"{_hour(t)} ({_r(hours.get(t.replace(minute=0, second=0, microsecond=0)), 1)})"
                    for t in tops[:3]) or DASH
    return _section("Stress yesterday", head + strip + f'<div class="s" style="margin-top:12px">Most stressed: {top}</div>')


def _load(ld: dict) -> str:
    acwr, flag = _f(ld.get("acwr")), ld.get("acwr_flag")
    if all(_f(ld.get(k)) is None for k in ("ctl", "atl", "tsb", "acwr")):
        return _section("Training load", _empty("No training history yet"))
    note = {"spike": "Spike, ramping fast", "detraining": "Low, detraining"}.get(
        flag, "In range 0.8–1.5" if acwr is not None else "")
    tone = "c3" if flag == "spike" else "c2" if flag == "detraining" else ""
    stats = (_stat("Fitness", _r(ld.get("ctl")), "42-day load") + _stat("Fatigue", _r(ld.get("atl")), "7-day load")
             + _stat("Form", _sg(ld.get("tsb")), "Fitness − fatigue")
             + _stat("Load ratio", _r(acwr, 2), f'<span class="{tone}">{note}</span>' if tone else note))
    return _section("Training load", f'<div class="g2">{stats}</div>')


def daily_card(result: dict) -> str:
    """Render the morning card for one result dict (schema in the module docstring)."""
    r = _d(result)
    rec, sl, st = _d(r.get("recovery")), _d(r.get("sleep")), _d(r.get("strain"))
    flags = list(dict.fromkeys(f for f in _l(r.get("flags")) if isinstance(f, str)))[:6]
    zone = rec.get("zone")
    summary = (f"Daily recovery card for {_long(r.get('date'))}: recovery {_r(rec.get('score'))}"
               f"{', ' + ZONE_WORD[zone] if zone in ZONE else ''}; strain yesterday {_r(st.get('yesterday'), 1)}; "
               f"sleep performance {_pct(sl.get('perf'))}. {_headline(rec, flags)}.")
    status = (f'<span>{ZONE_WORD[zone]}: {ZONE_MEANING[zone]}</span>'
              if zone in ZONE and _f(rec.get("score")) is not None else "")
    top = f'<div class="sb s"><span>{_wd(r.get("date"))}</span>{status}</div>'
    return (f'<h2 class="sr-only">{_e(summary)}</h2><style>{CSS}{DAILY_CSS}</style><div class="rc"><div class="np">'
            + top + '<div class="hd">' + _hero(rec, flags) + "<div>" + _flags_banner(flags) + _contributors(rec)
            + '</div></div><div class="cols">' + _small_dials(st, sl) + _sleep(sl) + _workouts(st)
            + _trend(_l(r.get("trend"))) + _signals(_d(r.get("bands"))) + _stress(_d(r.get("stress")))
            + _load(_d(r.get("load"))) + _training(_d(r.get("training"))) + "</div>" + FOOTER + "</div></div>")


MONO_WORD = {"warn": "Getting repetitive", "alert": "Too repetitive: vary it"}


def _training(tr: dict) -> str:
    """The morning card's training strip (session.training_card): only when there is a workout log."""
    if not tr:
        return ""
    ri = _d(tr.get("recovery_index"))
    ri_note = ("Low came late in the night" if ri.get("late_nadir") else
               "Workout ended near bedtime" if ri.get("late_workout") else "Slept after the overnight low")
    pb, gb = tr.get("polar_band"), tr.get("garmin_band")
    stats = (_stat("Form", f'{_sg(tr.get("form_pct"), 1)}%' if _f(tr.get("form_pct")) is not None else DASH,
                   "Share of fitness")
             + _stat("Monotony", _r(tr.get("monotony"), 2), MONO_WORD.get(tr.get("mono_flag"), "Varied enough")
                     if _f(tr.get("monotony")) is not None else "Needs 7 days of workouts")
             + _stat("Load ratio, Polar", _cap(pb) if pb else DASH, f"Garmin: {gb}" if gb else "")
             + _stat("Polarization", _r(tr.get("pi7"), 2), "7 days; 2 or more is polarized")
             + _stat("Easy-day cap", _r(tr.get("easy_strain"), 1), "Strain that keeps form level")
             + _stat("Status", _cap(tr.get("status")) if tr.get("status") else DASH, "approx")
             + _stat("Recovery index", f'{_r(ri.get("hours"), 1)} h' if _f(ri.get("hours")) is not None else DASH,
                     ri_note if ri else "Needs overnight heart rate")
             + _stat("HRV status", _cap(tr.get("hrv_status")) if tr.get("hrv_status") else DASH, "7 days vs your normal"))
    return _section("Training", f'<div class="g2">{stats}</div>')


# ---------------------------------------------------------------- weekly
GOOD_UP = {"recovery", "sleep_perf", "sleep_min", "hrv", "vo2"}
GOOD_DOWN = {"rhr", "stress"}
NOISE = {"recovery": 0.5, "strain": 0.05, "sleep_perf": 0.005, "sleep_min": 0.5, "hrv": 0.5, "rhr": 0.5,
         "stress": 0.05, "vo2": 0.05}


def delta_tone(metric: str, now, before) -> str:
    """'good' | 'bad' | 'neutral' for a change in a weekly metric (strain has no good direction)."""
    a, b = _f(now), _f(before)
    if a is None or b is None or abs(a - b) < NOISE.get(metric, 0.05):
        return "neutral"
    if metric in GOOD_UP:
        return "good" if a > b else "bad"
    if metric in GOOD_DOWN:
        return "bad" if a > b else "good"
    return "neutral"


WEEKLY_CSS = (
    ".hc{text-align:center;padding:8px 0 4px}.ga{position:relative;width:220px;margin:20px auto 0}"
    ".ga svg{display:block}.gc{position:absolute;left:0;right:0;bottom:22px;text-align:center}"
    ".wb{display:flex;gap:6px;align-items:flex-end;height:44px}.wb>div{display:flex;gap:2px;"
    "align-items:flex-end;flex:7}.wb i,.fb i{flex:1;min-width:0}.wb i{border-radius:3px 3px 0 0}"
    f".wb .o{{opacity:.72}}.cs i{{background:{STRAIN}}}.cl i{{background:{SLEEP}}}"
    ".wa{display:flex;gap:6px;margin-top:6px}.wa>span{flex:7}"
    ".fb{display:flex;gap:2px;height:48px;position:relative}.fb i{position:relative}"
    f".fb b{{position:absolute;left:0;right:0;opacity:.7}}.fb .u{{border-radius:2px 2px 0 0;background:{STRAIN}}}"
    f".fb .d{{border-radius:0 0 2px 2px;background:{ORANGE}}}"
    ".ax{position:absolute;right:100%;margin-right:8px;transform:translateY(-50%);line-height:1}"
    ".ib{display:grid;grid-template-columns:minmax(0,1fr) auto 72px;gap:14px 12px;align-items:center}"
    ".tr{display:grid;grid-template-columns:128px minmax(0,1fr);gap:16px;align-items:center;padding:8px 0}"
    ".jr{display:grid;grid-template-columns:minmax(0,1fr) 72px;gap:16px;align-items:center;padding:10px 0}"
    ".jr b{display:block;text-align:right;margin-bottom:6px}"
    ".ky{display:inline-block;width:14px;height:2px;border-radius:1px;vertical-align:middle;margin-right:6px}"
)
METRICS = [  # key, label, value formatter, delta formatter
    ("recovery", "Recovery", lambda v: _unit(v, "%"), _sg),
    ("strain", "Strain", lambda v: _r(v, 1), lambda d: _sg(d, 1)),
    ("sleep_perf", "Sleep performance", _pct, lambda d: _sg(d * 100) + " pts"),
    ("sleep_min", "Time asleep", _hm, lambda d: ("+" if d >= 0 else MINUS) + _hm(abs(d))),
    ("hrv", "HRV", lambda v: _unit(v, " ms"), lambda d: _sg(d) + " ms"),
    ("rhr", "Resting HR", lambda v: _unit(v, " bpm"), lambda d: _sg(d) + " bpm"),
    ("stress", "Stress", lambda v: _r(v, 1), lambda d: _sg(d, 1)),
]
ITEM_NAMES = {"sleep_duration": "Sleep duration", "sleep_regularity": "Sleep regularity", "activity": "Activity",
              "vo2max": "VO₂ max", "rhr": "Resting heart rate", "body_comp": "Body composition",
              "steps": "Steps", "strength": "Strength training"}
JOURNAL_LEAD = {"late meal": "On days with a late meal", "stressful day": "On stressful days"}


def _averages(avg: dict, prior: dict, series: list) -> str:
    title = "This week vs the 4 weeks before"
    if all(_f(avg.get(k)) is None for k, *_ in METRICS):
        return _section(title, _empty("Not enough scored days this week"))
    cells = []
    for key, label, fmt, dfmt in METRICS:
        a, b = _f(avg.get(key)), _f(prior.get(key))
        if a is not None and b is not None:
            sub = f'<span class="{_tone(delta_tone(key, a, b))}">{dfmt(a - b)}</span> vs {fmt(b)}'
        else:
            sub = f"Before {fmt(b)}"
        cells.append(_stat(label, fmt(a), sub))
    week = [s for s in series if isinstance(s, dict)][-7:]
    zones = [s.get("zone") for s in week if s.get("zone") in ZONE]
    sub = f"{zones.count('yellow')} yellow, {zones.count('red')} red" if zones else "No scored days"
    cells.append(_stat("Green days", f"{zones.count('green')} of {len(zones)}" if zones else DASH, sub))
    return _section(title, f'<div class="g2">{"".join(cells)}</div>')


def _day_tile(title: str, day) -> str:
    day = _d(day)
    rec = _f(day.get("recovery"))
    zone = zone_for(_half_up(rec)) if rec is not None else None   # same rounding as the printed number
    tint = ZONE_CLASS[zone].replace("z", "c") if zone else ""
    return (f'<div class="tl"><div class="s">{title}</div><div>{_wd(day.get("date"))}</div>'
            f'<div style="margin:6px 0 4px"><span class="w {tint}">{_unit(rec, "%")}</span> '
            f'<span class="s">{ZONE_WORD.get(zone, "")}</span></div><div class="m">{_e(day.get("why") or "")}</div></div>')


def _weekly_bars(series: list) -> str:
    rows = [s for s in series if isinstance(s, dict)][-28:]
    if not rows:
        return _section("Last 28 days", _empty("No history yet"))
    weeks = [rows[i:i + 7] for i in range(0, len(rows), 7)]

    def flex(wk, cls=""):  # a full week takes flex 7 from the stylesheet; earlier weeks at 72 %
        attr = f' class="{cls}"' if cls else ""
        return attr if len(wk) == 7 else f'{attr} style="flex:{len(wk)}"'

    def chart(name, key, scale, colour, fmt, legend=""):
        groups, avgs = [], []
        for i, wk in enumerate(weeks):
            vals = [_f(s.get(key)) for s in wk]
            bars = []
            for s, v in zip(wk, vals):
                if v is None:
                    bars.append("<i></i>")
                    continue
                cls = "" if colour else f' class="{ZONE_CLASS.get(s.get("zone"), "z0")}"'
                bars.append(f'<i{cls} style="height:{max(2, round(_clamp(v / scale) * 44))}px"></i>')
            groups.append(f'<div{flex(wk, "" if i == len(weeks) - 1 else "o")}>{"".join(bars)}</div>')
            ok = [v for v in vals if v is not None]
            avgs.append(f"<span{flex(wk)}>avg <b>{fmt(sum(ok) / len(ok)) if ok else DASH}</b></span>")
        return (f'<div class="sb" style="margin:16px 0 8px"><span class="s">{name}</span>{legend}</div>'
                f'<div class="wb {colour}" role="img" aria-label="{name}, daily, last {len(rows)} days">'
                f'{"".join(groups)}</div><div class="x wa n">{"".join(avgs)}</div>')

    axis = "".join(f'<span{flex(wk)}>{"This week" if i == len(weeks) - 1 else _md(wk[0].get("date"))}</span>'
                   for i, wk in enumerate(weeks))
    return _section("Last 28 days",
                    chart("Recovery", "recovery", 100, "", _r, ZONE_LEGEND).replace('margin:16px', 'margin:0', 1)
                    + chart("Strain", "strain", 21, "cs", lambda v: _r(v, 1))
                    + chart("Sleep performance", "perf", 1, "cl", _pct)
                    + f'<div class="x wa" style="margin-top:12px">{axis}</div>')


def _gauge(pace: float | None) -> str:
    """Pace of Aging as a half ring from −1× to 3×, filled from the left like a speedometer to his pace: green when
    slower than the calendar, red when faster; a tick marks calendar pace (1×) and the value sits inside the bowl."""
    cx, cy, r = 110, 100, 88

    def pt(p, rad=r):
        th = math.pi * (1 - (_clamp(p, -1, 3) + 1) / 4)
        return f"{cx + rad * math.cos(th):.1f} {cy - rad * math.sin(th):.1f}"

    track = f'<path d="M{pt(-1)} A{r} {r} 0 0 1 {pt(3)}" stroke="{_a(FOG, .16)}"/>'
    arc = ""
    if pace is not None:
        colour = GREEN if pace < 1 else RED if pace > 1 else FOG
        arc = f'<path d="M{pt(-1)} A{r} {r} 0 0 1 {pt(pace)}" stroke="{colour}"/>'
    tick = f'<path d="M{pt(1, r - 10)} L{pt(1, r + 10)}" stroke="{CHALK}" stroke-width="2"/>'
    text = (f'<text x="{cx}" y="-6" text-anchor="middle" font-size="11" fill="{MIST}">1× calendar</text>'
            f'<text x="{cx - r}" y="{cy + 24}" text-anchor="middle" font-size="11" fill="{MIST}">−1×</text>'
            f'<text x="{cx + r}" y="{cy + 24}" text-anchor="middle" font-size="11" fill="{MIST}">3×</text>')
    return (f'<svg width="220" height="146" viewBox="0 -18 220 146" stroke-width="12" role="img" '
            f'aria-label="Pace of aging {_r(pace, 1)} times calendar pace, on a scale of minus 1 to 3">'
            f'{track}{arc}{tick}{text}</svg>')


def _item_value(name: str, v, strength_only: bool) -> str:
    if _f(v) is None:
        return DASH
    return {
        "sleep_duration": lambda: f"{_r(v, 1)} h a night",
        "sleep_regularity": lambda: f"SRI {_r(_clamp(_f(v), 0, 100))}",
        "activity": lambda: f"{_r(v)} min strength a week" if strength_only else f"{_r(v, comma=True)} steps a day",
        "vo2max": lambda: f"{_r(v, 1)} ml/kg/min",
        "rhr": lambda: f"{_r(v)} bpm",
        "body_comp": lambda: f"BMI {_r(v, 1)}",
    }.get(name, lambda: _r(v, 1))()


def _body_age(ba) -> str:
    """The weekly hero: Body Age against calendar age, Pace of Aging as a half ring, then what each habit adds."""
    caveat = "Estimate from population research, not a medical test."
    if not isinstance(ba, dict):
        return (f'<div class="hc"><div class="s">Body age</div><div class="hv" style="color:{MIST}">{DASH}</div>'
                f'<div class="s" style="margin-top:8px">Not enough history yet</div></div>')
    value, age, pace = _f(ba.get("body_age")), _f(ba.get("age")), _f(ba.get("pace"))
    missing = [m for m in _l(ba.get("missing")) if isinstance(m, (list, tuple)) and len(m) == 2]
    miss = "".join(f'<div class="m">Not counted: {_e(ITEM_NAMES.get(m[0], m[0]))} — '
                   f'{_e(str(m[1]).replace(">=", "≥"))}</div>' for m in missing if value is None or m[0] != "strength")
    if value is None:
        return (f'<div class="hc"><div class="s">Body age</div><div class="hv" style="color:{MIST}">{DASH}</div>'
                f'<div class="s" style="margin:8px 0 16px">Not enough history yet for an estimate</div></div>'
                + miss + f'<div class="m" style="margin-top:6px">{caveat}</div>')
    diff = value - age if age is not None else None
    if diff is None:
        vs = f'<div class="s" style="margin-top:8px">Calendar age {DASH}</div>'
    elif abs(diff) < 0.05:
        vs = f'<div class="hl" style="margin-top:8px">Same as your calendar age</div><div class="s">{_r(age, 1)}</div>'
    else:
        vs = (f'<div class="hl {"c1" if diff < 0 else "c3"}" style="margin-top:8px">{_r(abs(diff), 1)} years '
              f'{"younger" if diff < 0 else "older"}</div><div class="s">than your calendar age of {_r(age, 1)}</div>')
    pace_word = ("Needs 8 weekly snapshots" if pace is None else "Ageing slower than the calendar" if pace < 1
                 else "Ageing faster than the calendar" if pace > 1 else "Ageing at calendar pace")
    hero = (f'<div class="hc"><div class="s">Body age</div><div class="hv">{_r(value, 1)}</div>{vs}'
            f'<div class="ga">{_gauge(pace)}<div class="gc"><div class="w">{_unit(pace, "×", 1)}</div>'
            f'<div class="x">Pace of aging</div></div></div><div class="s" style="margin-top:10px">{pace_word}</div></div>')
    strength_only = any(m[0] == "steps" for m in missing)
    items = [it for it in _l(ba.get("items")) if isinstance(it, dict) and _f(it.get("years")) is not None]
    rows = []
    for it in items:
        y = _f(it["years"]) * 0.6
        name = str(it.get("name"))
        tone = "c1" if y <= -0.05 else "c3" if y >= 0.05 else ""
        rows.append(f'<span class="el">{_e(ITEM_NAMES.get(name, name))}<div class="m el">'
                    f'{_item_value(name, it.get("value"), strength_only)}</div></span><b class="n r {tone}">{_sg(y, 1)} yr'
                    f'</b>{_diverge(y, 2.4, good_right=False)}')
    add = ""
    if items and diff is not None:
        total = sum(_f(it["years"]) * 0.6 for it in items)
        if abs(total - diff) > 0.05:
            add = f"Items sum to {_sg(total, 1)} years; the total is capped at ±10. "
        else:
            shown = sum(Decimal(_r(_f(it["years"]) * 0.6, 1).replace(MINUS, "-")) for it in items)
            approx = shown != Decimal(_r(diff, 1).replace(MINUS, "-"))
            add = f"Items add up to {_sg(diff, 1)} years{' (approx., rounded)' if approx else ''}. "
    body = (f'<div class="s" style="margin:20px 0 8px">Years each habit adds (right) or takes off (left)</div>'
            f'<div class="ib">{"".join(rows)}</div>' if rows else "")
    return (hero + f'<div>{body}<div class="m" style="margin-top:10px">{add}{caveat}</div>{miss}</div>')


def _trend_row(name: str, pts: list, unit: str, nd: int, key: str, colour: str) -> str:
    pts = sorted((p for p in pts if isinstance(p, dict) and _f(p.get("value")) is not None and _day(p.get("date"))),
                 key=lambda p: p["date"])
    if not pts:
        return f'<div class="tr"><div class="s">{name}</div>{_empty("No readings")}</div>'
    first, last = _f(pts[0]["value"]), _f(pts[-1]["value"])
    d0 = _day(pts[0]["date"])
    span = (_day(pts[-1]["date"]) - d0).days or 1
    xs = [(_day(p["date"]) - d0).days / span for p in pts]
    tone = _tone(delta_tone(key, last, first))
    change = (f'<span class="{tone}">{_sg(last - first, nd)}</span> since {_md(pts[0]["date"])}'
              if len(pts) > 1 else "One reading")
    spark = _sparkline([_f(p["value"]) for p in pts], colour, f"{name} from {_r(first, nd)} to {_r(last, nd)} {unit}",
                       height=44, xs=xs)
    return (f'<div class="tr"><div><div class="s">{name}</div><div class="v">{_r(last, nd)} '
            f'<span class="s">{unit}</span></div><div class="x">{change}</div></div><div>{spark}</div></div>')


def _nice_step(span: float) -> float:
    raw = span / 2 or 1
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)


def _load_chart(load: list) -> str:
    rows = [r for r in load if isinstance(r, dict)]
    ctl, atl, tsb = ([_f(r.get(k)) for r in rows] for k in ("ctl", "atl", "tsb"))
    vals = [v for v in ctl + atl if v is not None]
    if len(vals) < 2:
        return _section("Fitness, fatigue and form", _empty("Not enough training history yet"))
    step = _nice_step(max(vals) - min(vals))
    lo, hi = math.floor(min(vals) / step) * step, math.ceil(max(vals) / step) * step
    hi = hi if hi > lo else lo + step
    n = len(rows)

    def path(series):
        pts = [f"{i / max(1, n - 1) * 100:.1f} {(1 - (v - lo) / (hi - lo)) * 100:.1f}" for i, v in enumerate(series)
               if v is not None]
        return "M" + " L".join(pts) if len(pts) > 1 else ""

    grid, labels, t = "", "", lo
    while t <= hi + 1e-9:  # two or three faint reference lines
        y = (1 - (t - lo) / (hi - lo)) * 100
        grid += f'<path d="M0 {y:.1f}H100" style="stroke:{RULE};stroke-width:1"/>'
        labels += f'<span class="x ax n" style="top:{y:.1f}%">{_r(t)}</span>'
        t += step
    chart = (f'<div style="position:relative;height:110px;margin:14px 0 0 32px">{labels}'
             f'<svg class="sp" viewBox="0 0 100 100" preserveAspectRatio="none" style="position:absolute;inset:0;'
             f'height:100%" role="img" aria-label="Fitness and fatigue, last {n} days">{grid}<path d="{path(ctl)}" '
             f'style="stroke:{STRAIN}"/><path d="{path(atl)}" style="stroke:{ORANGE}"/></svg></div>')
    present = [v for v in tsb if v is not None]
    top, bot = max(present + [0.0]), min(present + [0.0])
    span = (top - bot) or 1.0
    zero = top / span * 100
    bars = "".join(("<i></i>" if v is None else
                    f'<i><b class="u" style="bottom:{100 - zero:.0f}%;height:{v / span * 100:.0f}%"></b></i>' if v >= 0
                    else f'<i><b class="d" style="top:{zero:.0f}%;height:{-v / span * 100:.0f}%"></b></i>') for v in tsb)
    span_txt = f"from {_sg(present[0])} to {_sg(present[-1])}" if present else DASH
    form = (f'<div class="fb" style="margin-left:32px" role="img" aria-label="Form by day, {span_txt}">'
            f'<span class="x ax n" style="top:{zero:.0f}%">0</span>'
            f'<span style="position:absolute;left:0;right:0;top:{zero:.0f}%;height:1px;background:{RULE}"></span>'
            + (f'<span class="x ax n" style="top:100%">{_r(bot)}</span>' if bot < 0 else "")
            + (f'<span class="x ax n" style="top:0">{_r(top)}</span>' if top > 0 else "") + f"{bars}</div>")
    last = rows[-1]
    key = (f'<div class="s" style="display:flex;flex-wrap:wrap;gap:4px 18px"><span><i class="ky" style="background:'
           f'{STRAIN}"></i>Fitness <b class="n">{_r(last.get("ctl"))}</b></span><span><i class="ky" style="background:'
           f'{ORANGE}"></i>Fatigue <b class="n">{_r(last.get("atl"))}</b></span>'
           f'<span>Form <b class="n">{_sg(last.get("tsb"))}</b></span></div>')
    dates = (f'<div class="x sb" style="margin:6px 0 0 32px"><span>{_md(rows[0].get("date"))}</span>'
             f'<span>{_md(last.get("date"))}</span></div>')
    return _section("Fitness, fatigue and form", key + chart + '<div class="s" style="margin:20px 0 12px">Form: above 0 '
                                                         'fresh, below 0 tired</div>' + form + dates)


def _stress_pattern(series: list, avg, prior) -> str:
    rows = [s for s in series if isinstance(s, dict) and _f(s.get("stress")) is not None and _day(s.get("date"))]
    if not rows:
        return _section("Stress pattern", _empty("No stress scores yet"))
    by = {i: [] for i in range(7)}
    for s in rows:
        by[_day(s["date"]).weekday()].append(_f(s["stress"]))
    means = {i: (sum(v) / len(v) if v else None) for i, v in by.items()}
    peak = max((i for i in means if means[i] is not None), key=lambda i: means[i])
    high = sum(_f(s["stress"]) >= 2 for s in rows)
    text = (f"Highest on {('Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays')[peak]}"
            f" ({_r(means[peak], 1)}). {high} high-stress day{'s' if high != 1 else ''} in the last {len(rows)}. "
            f"This week {_r(avg, 1)} vs {_r(prior, 1)} before.")
    names = "".join(f"<span>{n}</span>" for n in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
    return _section("Stress pattern", f'<div class="s" style="margin-bottom:10px">Average by weekday, 0–3</div>'
                                      f'<div class="st n" role="img" aria-label="Average stress by weekday">'
                                      f'{"".join(_stress_cell(means[i]) for i in range(7))}</div>'
                                      f'<div class="st x" style="margin-top:6px">{names}</div>'
                                      f'<div class="s" style="margin-top:12px">{_e(text)}</div>')


def _journal(effects: list) -> str:
    rows = []
    for j in effects:
        if not isinstance(j, dict) or _f(j.get("diff")) is None:
            continue
        q = str(j.get("question") or "this habit").strip()
        diff, ym, nm = _f(j["diff"]), _f(j.get("yes_mean")), _f(j.get("no_mean"))
        lead = JOURNAL_LEAD.get(q.lower(), f"On days with {q}")
        sentence = f"{lead}, next-morning recovery averaged {_r(abs(diff))} points {'lower' if diff < 0 else 'higher'}"
        detail = f"{_r(ym)} on {_r(j.get('yes_n'))} days with, {_r(nm)} on {_r(j.get('no_n'))} days without"
        rows.append(f'<div class="jr"><div>{_e(sentence)}.<div class="m" style="margin-top:2px">{detail}</div></div>'
                    f'<div><b class="n {"c1" if diff > 0 else "c3"}">{_sg(diff)}</b>{_diverge(diff, 20)}</div></div>')
    if not rows:
        return _section("Habits and next-morning recovery",
                        '<div class="s">No clear effects yet: each habit needs 5 days with and 5 without, and a '
                        'difference of 5 points or more.</div>')
    return _section("Habits and next-morning recovery", "".join(rows) + '<div class="m" style="margin-top:6px">Bars '
                    'show points vs days without. Associations in your own log, not proof of cause.</div>')


def weekly_card(result: dict) -> str:
    """Render the weekly report card for one result dict (schema in the module docstring)."""
    r = _d(result)
    avg, prior = _d(r.get("averages")), _d(r.get("prior"))
    series = _l(r.get("series"))
    a, b = _f(avg.get("recovery")), _f(prior.get("recovery"))
    if a is None:
        headline = "Weekly report — not enough data for averages yet"
    elif b is None:
        headline = f"Recovery averaged {_r(a)}% this week"
    else:
        d = _r(abs(a - b))
        move = "level with" if d == "0" else f"up {d} on" if a > b else f"down {d} on"
        headline = f"Recovery averaged {_r(a)}% this week, {move} the 4 weeks before"
    ws, we = _day(r.get("week_start")), _day(r.get("week_end"))
    span = (f"Week of {ws:%b} {ws.day} – " + (f"{we.day}" if ws.month == we.month else f"{we:%b} {we.day}")
            + f", {we.year}") if ws and we else f"Week of {DASH}"
    ba = _d(r.get("body_age"))
    summary = (f"Weekly recovery report for the {span[0].lower() + span[1:]}: recovery average {_r(a)}, "
               f"body age {_r(ba.get('body_age'), 1)}, pace of aging {_r(ba.get('pace'), 1)}.")
    header = f'<div class="s">{_e(span)}</div><div class="hl" style="margin-top:4px">{_e(headline)}</div>'
    focus = r.get("focus")
    focus_html = (f'<div class="tl" style="margin-top:16px"><div class="s">Focus for the coming week</div>'
                  f'<div style="margin-top:4px">{_e(focus)}</div></div>' if focus else "")
    days = (f'<div class="sc g2" style="gap:10px">{_day_tile("Best day", r.get("best_day"))}'
            f'{_day_tile("Toughest day", r.get("worst_day"))}</div>' if r.get("best_day") or r.get("worst_day") else "")
    tr = _d(r.get("trends"))
    trends = _section("HRV, resting heart rate and VO₂ max",
                      _trend_row("HRV", _l(tr.get("hrv")), "ms", 0, "hrv", HRV_C)
                      + _trend_row("Resting HR", _l(tr.get("rhr")), "bpm", 0, "rhr", HR_C)
                      + _trend_row("VO₂ max", _l(tr.get("vo2")), "ml/kg/min", 1, "vo2", CHALK))
    return (f'<h2 class="sr-only">{_e(summary)}</h2><style>{CSS}{WEEKLY_CSS}{SPLIT_CSS}</style><div class="rc"><div class="np">'
            + header + focus_html + f'<div class="hd sc" style="margin:24px 0 0">{_body_age(r.get("body_age"))}</div>'
            + '<div class="cols">' + _averages(avg, prior, series) + days + _weekly_bars(series) + trends
            + _load_chart(_l(r.get("load"))) + _stress_pattern(series, avg.get("stress"), prior.get("stress"))
            + _training_week(_d(r.get("training"))) + _journal(_l(r.get("journal"))) + "</div>" + FOOTER
            + "</div></div>")


SPLIT_COLOURS = (STRAIN, ORANGE, RED)
SPLIT_CSS = (".zb{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:4px 0 8px;gap:2px}"
             ".zl{display:grid;gap:4px}.zl>div{min-width:0}"
             ".zl i{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:4px}")
RACE_NAMES = (("5k", "5 km"), ("10k", "10 km"), ("half", "Half marathon"), ("marathon", "Marathon"))
PACE_NAMES = (("easy", "Easy"), ("marathon", "Marathon"), ("threshold", "Threshold"), ("interval", "Interval"),
              ("repetition", "Repetition"))


def _training_week(tr: dict) -> str:
    """The weekly card's training and racing section (weekly.training_section)."""
    if not tr:
        return ""
    split = [_f(x) or 0.0 for x in _l(tr.get("split"))][:3]
    body = ""
    if len(split) == 3 and sum(split) > 0:
        total = sum(split)
        bar = "".join(f'<i style="flex:{v / total:.3f};background:{c}"></i>'
                      for v, c in zip(split, SPLIT_COLOURS) if v > 0)
        legend = "".join(f'<div><div class="x"><i style="background:{c}"></i>{n}</div><div class="n">{_r(v)} min</div>'
                         f'</div>' for v, c, n in zip(split, SPLIT_COLOURS, ("Easy", "Moderate", "Hard")))
        body += (f'<div class="zb">{bar}</div><div class="zl" style="grid-template-columns:repeat(3,minmax(0,1fr))">'
                 f'{legend}</div>')
    short = ", ".join(_l(tr.get("focus_short")))
    stats = (_stat("Polarization", _r(tr.get("pi"), 2), "2 or more is polarized")
             + _stat("Monotony", _r(tr.get("monotony"), 2), "Under 1.5 is varied")
             + _stat("Status", _cap(tr.get("status")) if tr.get("status") else DASH, "approx")
             + _stat("Short on", _e(short) if short else "Nothing", "Over 28 days, approx"))
    body += f'<div class="g2" style="margin-top:16px">{stats}</div>'
    race = _d(tr.get("race"))
    preds = _d(race.get("predictions"))
    if preds:
        tiles = "".join(f'<div class="tl"><div class="s">{n}</div><div class="v">{_mmss(preds.get(k))}</div></div>'
                        for k, n in RACE_NAMES)
        body += (f'<div class="s" style="margin:20px 0 8px">Race predictions from your recent outdoor runs</div>'
                 f'<div class="g2" style="gap:10px">{tiles}</div>')
        paces = _d(race.get("paces"))
        rows = "".join(f'<div class="sb"><span class="s">{n}</span><span class="n">{_mmss(paces.get(k))} /km</span></div>'
                       for k, n in PACE_NAMES if _f(paces.get(k)) is not None)
        body += f'<div class="s" style="margin:18px 0 8px">Training paces</div>{rows}'
        cs = _f(race.get("cs"))
        extra = (_stat("Running VO₂ max", _r(race.get("vdot"), 1), "From your runs, 30 days")
                 + _stat("Marathon shape", f'{_r(race.get("marathon_shape"))}%', "Volume and long runs")
                 + _stat("Critical speed", f'{_mmss(1000 / cs)} /km' if cs else DASH, "approx")
                 + _stat("Heart-rate VO₂ max", _r(tr.get("uth_vo2"), 1), "Uth formula, a rough check"))
        body += f'<div class="g2" style="margin-top:16px">{extra}</div>'
    else:
        body += '<div class="s" style="margin-top:16px">Race predictions need an outdoor run in the last 30 days</div>'
    return _section("Training and racing", body)


# ---------------------------------------------------------------- back-test (first run)
BACKTEST_CSS = (
    ".bt{display:grid;grid-template-columns:48px repeat(7,minmax(0,1fr));gap:4px;align-items:center}"
    ".bt i{font-style:normal;font-size:13px;text-align:center;height:30px;line-height:30px;border-radius:6px}"
    + "".join(f".bt .z{i}{{background:{_a(c, .16)};color:{c}}}" for i, c in enumerate((GREEN, YELLOW, RED), 1))
    + f".bt .z0{{background:{DECK};color:{MIST}}}.bt>span{{text-align:center}}"
    ".bt>span:nth-child(8n+1){text-align:left}"
)
WEEKDAY_LETTERS = ("M", "T", "W", "T", "F", "S", "S")


def backtest_card(backtest: list) -> str:
    """The first run's back-test: [{date, recovery, zone}] as one zone-coloured cell per day (the number printed in
    it), in Monday-first weeks, oldest first, so he can compare the days with how he felt."""
    days = sorted((b for b in _l(backtest) if isinstance(b, dict) and _day(b.get("date"))), key=lambda b: b["date"])
    if not days:
        return (f'<h2 class="sr-only">Back-test of past recovery scores: no days yet.</h2><style>{CSS}</style>'
                f'<div class="rc"><div class="np"><div class="s">Back-test</div><div class="hl" style="margin:4px 0 '
                f'16px">No days to show yet</div>{FOOTER}</div></div>')
    by = {_day(b["date"]): b for b in days}
    first, last = min(by), max(by)
    grid = ["<span></span>"] + [f'<span class="x">{d}</span>' for d in WEEKDAY_LETTERS]
    counts = {"green": 0, "yellow": 0, "red": 0, None: 0}
    week = first - timedelta(days=first.weekday())
    while week <= last:
        grid.append(f'<span class="x">{_md(week.isoformat())}</span>')
        for i in range(7):
            b = by.get(week + timedelta(days=i))
            score = _f(b.get("recovery")) if b else None
            if b is None:
                grid.append("<i></i>")
            elif score is None:
                counts[None] += 1
                grid.append(f'<i class="z0">{DASH}</i>')
            else:
                zone = zone_for(_half_up(score))   # the colour always matches the printed number
                counts[zone] += 1
                grid.append(f'<i class="{ZONE_CLASS[zone]}">{_r(score)}</i>')
        week += timedelta(days=7)
    tally = f"{counts['green']} green, {counts['yellow']} yellow, {counts['red']} red"
    if counts[None]:
        tally += f", {counts[None]} days not scored"
    n = (last - first).days + 1
    span = f"{_md(first.isoformat())} – {_md(last.isoformat())}"
    summary = f"Back-test of recovery for {n} days, {span}: {tally}."
    note = ("Each day's recovery as it would have been scored that morning, using only the days before it. Do the "
            "red days match the days you felt run down? Days without 14 nights of history have no score.")
    cells = [(counts["green"], "green", "c1"), (counts["yellow"], "yellow", "c2"), (counts["red"], "red", "c3")]
    if counts[None]:
        cells.append((counts[None], "days not scored", "s"))
    stats = "".join(f'<div><span class="w {c}">{k}</span> <span class="s">{w}</span></div>' for k, w, c in cells)
    body = (f'<div style="display:flex;flex-wrap:wrap;gap:8px 24px;margin:20px 0">{stats}</div>'
            f'<div class="bt n" role="img" aria-label="{_e(summary)}">{"".join(grid)}</div>'
            f'<div style="margin-top:12px">{ZONE_LEGEND}</div><div class="m" style="margin:16px 0 20px">{note}</div>')
    header = f'<div class="sb s"><span>Back-test</span><span>{span}</span></div>'
    return (f'<h2 class="sr-only">{_e(summary)}</h2><style>{CSS}{BACKTEST_CSS}</style><div class="rc"><div class="np">'
            + header + f'<div class="hl" style="margin-top:4px">Your last {n} days, scored</div>' + body + FOOTER
            + "</div></div>")


# ---------------------------------------------------------------- workout card (the `workout` command)
# Heart-rate zones 1-5 as a ramp people know from sports watches: slate, blue, green, orange, red. The 3 minutes
# after End are drawn in chalk at low alpha: the recovery tail.
HRZ = ("#5D7286", STRAIN, GREEN, ORANGE, RED)
TAIL = _a(CHALK, .28)
RIBBON_BARS = 120
WORKOUT_CSS = (
    ".wh{display:flex;justify-content:space-between;align-items:center;gap:16px}"
    ".rb{position:relative;margin-top:18px}.rb svg{display:block;width:100%;height:84px}"
    ".rb path{stroke-linecap:butt}"
    ".ax{display:flex;justify-content:space-between;margin-top:6px}"
    ".zb{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:14px 0 8px;gap:2px}"
    ".zl{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px}"
    ".zl>div{min-width:0}.zl i{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:4px}"
    ".dl{position:relative;display:inline-block}.dc{position:absolute;inset:0;display:flex;flex-direction:column;"
    "align-items:center;justify-content:center;text-align:center}"
    ".spl{display:grid;grid-template-columns:28px 1fr 64px 56px;gap:6px 8px;align-items:center;font-size:13px}"
    f".spl i{{display:block;height:6px;border-radius:3px;background:{STRAIN}}}"
)
BENEFIT_WORD = {"Recovery": "light, recovery", "Base": "aerobic base", "Tempo": "tempo", "Threshold": "threshold",
                "VO2 max": "VO₂ max", "Anaerobic": "anaerobic"}
TE_WORD = ((1.0, "minor effect"), (2.0, "some effect"), (3.0, "maintaining"), (4.0, "improving"),
           (5.0, "highly improving"), (9.0, "overreaching"))


def _mmss(seconds) -> str:
    s = _f(seconds)
    if s is None:
        return DASH
    s = int(round(s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _te_word(te) -> str:
    v = _f(te)
    if v is None:
        return ""
    return next(word for lim, word in TE_WORD if v < lim)


def _zone_of(bpm: float, limits: list) -> int:
    z = -1
    for i, lim in enumerate(limits):
        if bpm >= lim:
            z = i
    return z


def _ribbon(hr: dict, minutes) -> str:
    trace = [p for p in _l(hr.get("trace")) if isinstance(p, list) and len(p) == 2 and _f(p[1]) is not None]
    limits = [x for x in _l(hr.get("limits")) if _f(x) is not None]
    if len(trace) < 4:
        return _empty("No heart-rate trace")
    end_s = (_f(minutes) or 0) * 60
    n = min(RIBBON_BARS, len(trace))
    step = len(trace) / n
    bars = []
    for i in range(n):
        chunk = trace[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]
        bars.append((chunk[0][0], sum(p[1] for p in chunk) / len(chunk)))
    hi = max(b for _, b in bars)
    lo = min(b for _, b in bars)
    lo -= max((hi - lo) * 0.35, 6)   # a floor under the lowest bar, so changes in heart rate read as height
    span = (hi - lo) or 1.0
    paths: dict[str, list[str]] = {}
    for i, (sec, bpm) in enumerate(bars):
        colour = TAIL if sec >= end_s > 0 else (HRZ[_zone_of(bpm, limits)] if _zone_of(bpm, limits) >= 0 else RULE)
        y = 84 - (bpm - lo) / span * 80
        paths.setdefault(colour, []).append(f"M{i + .5:g} {y:.1f}V84")
    svg = "".join(f'<path d="{"".join(d)}" stroke="{c}" stroke-width=".72"/>' for c, d in paths.items())
    total = trace[-1][0] or 1
    end_pct = min(end_s / total * 100, 100)
    label = f"Heart rate from {_r(min(p[1] for p in trace))} to {_r(max(p[1] for p in trace))} bpm"
    return (f'<div class="rb"><svg viewBox="0 0 {n} 84" preserveAspectRatio="none" role="img" '
            f'aria-label="{_e(label)}">{svg}</svg><div class="ax x"><span>Start</span>'
            f'<span style="margin-left:{max(end_pct - 60, 0):.0f}%">{_r(minutes)} min</span>'
            f'<span>End + 3 min</span></div></div>')


def _zones(hr: dict) -> str:
    z = [_f(x) or 0.0 for x in _l(hr.get("zones"))][:5]
    if len(z) < 5 or sum(z) <= 0:
        return ""
    total = sum(z)
    bar = "".join(f'<i style="flex:{v / total:.3f};background:{c}"></i>' for v, c in zip(z, HRZ) if v > 0)
    lims = _l(hr.get("limits"))
    legend = "".join(
        f'<div><div class="x"><i style="background:{c}"></i>Zone {i + 1}</div><div class="n">{_r(v)} min</div>'
        + (f'<div class="x">{_r(lims[i])}+ bpm</div>' if i < len(lims) else "") + "</div>"
        for i, (v, c) in enumerate(zip(z, HRZ)))
    return f'<div class="zb">{bar}</div><div class="zl">{legend}</div>'


def _hrr(rec: dict) -> str:
    if _f(rec.get("hrr60")) is None:
        why = rec.get("reason") or "not available"
        apple = (f'<div class="m" style="margin-top:6px">Apple’s Cardio Recovery: {_r(rec.get("apple"))} bpm</div>'
                 if _f(rec.get("apple")) is not None else "")
        return _section("Heart-rate recovery", _empty(_cap(why)) + apple)
    flag = rec.get("flag") == "low"
    one = _stat("In 1 minute", f'{_r(rec.get("hrr60"))} <small class="s">bpm</small>',
                '<span class="c2">12 or less: take it easy</span>' if flag else "13 or more is normal")
    two = _stat("In 2 minutes", f'{_r(rec.get("hrr120"))} <small class="s">bpm</small>', "22 or more is normal")
    apple = _stat("Apple’s estimate", f'{_r(rec.get("apple"))}', "Predicted, capped at 55") \
        if _f(rec.get("apple")) is not None else ""
    hrrc = _stat("Largest drop in the session", f'{_r(rec.get("hrrc"))}', "After a minute above threshold") \
        if _f(rec.get("hrrc")) is not None else ""
    return _section("Heart-rate recovery", f'<div class="m" style="margin:-8px 0 12px">From {_r(rec.get("end_hr"))} bpm '
                    f'at the end</div><div class="g2">{one}{two}{apple}{hrrc}</div>')


def _wload(ld: dict) -> str:
    src = {"you": "your rating", "apple": "Apple’s estimate"}.get(ld.get("effort_src"), "")
    level = ld.get("level")
    df, dfat = _f(ld.get("d_fitness")), _f(ld.get("d_fatigue"))
    stats = (_stat("Strain", _r(ld.get("strain"), 1), "0–21")
             + _stat("HRSS", _r(ld.get("hrss")), "100 = an hour at threshold")
             + _stat("Effort", _r(ld.get("effort")), src or "No rating")
             + _stat("Session RPE", _r(ld.get("srpe")), "Effort × minutes")
             + _stat("Compared with your usual", _cap(level) if level else DASH,
                     "Same activity, 90 days" if level else "No history yet")
             + _stat("Fitness, fatigue", f'{_sg(df, 1)}, {_sg(dfat, 1)}' if df is not None else DASH,
                     "What this adds")
             + _stat("Intensity", f'{_r(ld.get("intensity_pct"))}%', "Of the most this length could score")
             + _stat("Active Zone Minutes", _r(ld.get("azm")), "approx"))
    return _section("Load", f'<div class="g2">{stats}</div>')


def _run(run: dict) -> str:
    if not run:
        return ""
    km = _f(run.get("dist"))
    dec = _f(run.get("dec"))
    drift = ("Under 5%: steady aerobic engine" if dec < 5 else "5% or more: heart rate drifted up") if dec is not None \
        else "Needs 20 steady minutes"
    stats = (_stat("Pace", f'{_mmss(run.get("pace_s_km"))} <small class="s">/km</small>',
                   f'{_r(km / 1000, 2) if km else DASH} km')
             + _stat("Efficiency", _r(run.get("ef"), 3), "Metres a minute per beat")
             + _stat("Heart-rate drift", f'{_r(dec, 1)}%' if dec is not None else DASH, drift)
             + _stat("VO₂ max from this run", _r(run.get("evo2"), 1), "Effective, after Daniels")
             + _stat("Running index", _r(run.get("ri"), 1), "Polar-style")
             + _stat("Performance condition", _sg(run.get("perf_cond")), "approx, vs your last 28 days")
             + _stat("Vertical ratio", f'{_r(run.get("vert_ratio"), 1)}%', "Lower is more economical")
             + _stat("Muscle load", f'{_r(run.get("muscle_kj"))} kJ', "Power × time")
             + _stat("Running stress", _r(run.get("rtss")), "approx, flat-ground")
             + _stat("Power stress", _r(run.get("rss")), "approx"))
    best = _d(run.get("best"))
    names = (("400m", "400 m"), ("1k", "1 km"), ("mile", "Mile"), ("5k", "5 km"), ("10k", "10 km"), ("half", "Half"))
    best_html = "".join(f'<div><div class="s">{n}</div><div class="v">{_mmss(best[k])}</div></div>'
                        for k, n in names if _f(best.get(k)) is not None)
    best_html = (f'<div class="s" style="margin:18px 0 8px">Best efforts inside this run (approx)</div>'
                 f'<div class="g2" style="grid-template-columns:repeat(3,minmax(0,1fr))">{best_html}</div>'
                 if best_html else "")
    splits = [s for s in _l(run.get("splits")) if isinstance(s, dict)]
    split_html = ""
    if splits:
        paces = [_f(s.get("pace_s_km")) for s in splits if _f(s.get("pace_s_km"))]
        fast, slow = (min(paces), max(paces)) if paces else (0, 1)
        rows = "".join(
            f'<span class="x">{_r(s.get("n"))}</span>'
            f'<i style="width:{35 + 65 * ((slow - _f(s.get("pace_s_km"))) / ((slow - fast) or 1)):.0f}%"></i>'
            f'<span class="n r">{_mmss(s.get("pace_s_km"))}</span><span class="x r">{_r(s.get("avg_hr"))} bpm</span>'
            if _f(s.get("pace_s_km")) else "" for s in splits[:12])
        split_html = f'<div class="s" style="margin:18px 0 8px">Splits</div><div class="spl">{rows}</div>'
    return _section("Run", f'<div class="g2">{stats}</div>' + best_html + split_html)


def workout_card(result: dict) -> str:
    """Render one workout (session.py result dict, plus an optional "context") as a chat card."""
    r = _d(result)
    hr, ld, ef, rec, run = _d(r.get("hr")), _d(r.get("load")), _d(r.get("effect")), _d(r.get("recovery")), _d(r.get("run"))
    ctx = _d(r.get("context"))
    name = _workout_name(str(r.get("kind") or "workout"))
    km = _f(run.get("dist")) if run else None
    where = " indoors" if r.get("indoor") else ""
    top = (f'<div class="sb s"><span>{_e(name)}{where} at {_clock(r.get("start"))}, {_md(r.get("start"))}</span>'
           f'<span>{_unit(r.get("minutes"), " min")}{f", {_r(km / 1000, 1)} km" if km else ""}</span></div>')
    te = _f(ef.get("te"))
    benefit = BENEFIT_WORD.get(ef.get("benefit"), "")
    an = f', anaerobic {_r(ef.get("te_an"), 1)}' if _f(ef.get("te_an")) is not None else ""
    hero = (f'<div><div class="hv">{_r(te, 1)}</div><div class="hl" style="margin-top:6px">'
            f'{_e(_cap(_te_word(te) or "No training effect"))}{", mostly " + _e(benefit) if benefit else ""}</div>'
            f'<div class="s">Training effect out of 5, approx{an}</div></div>')
    rec_h = _f(ef.get("rec_h"))
    pct = _f(ctx.get("recovered_pct"))
    ring = _ring(96, 8, (pct or 0) / 100, GREEN, f"Recovery time {_r(rec_h)} hours",
                 f'<div class="v">{_r(rec_h)} h</div><div class="x">to recover, approx</div>') if rec_h is not None else ""
    notes = [n for n in _l(r.get("notes")) if isinstance(n, str)]
    notes_html = ("".join(f'<div class="m">{_e(_cap(n))}</div>' for n in notes[:4])) if notes else ""
    prov = ('<div class="m c2" style="margin-top:8px">Scored before the 2 minutes after End were recorded; '
            'the next check updates it.</div>' if r.get("provisional") else "")
    summary = (f"{name} workout card: training effect {_r(te, 1)}, {_r(rec_h)} hours to recover, heart-rate recovery "
               f"{_r(rec.get('hrr60'))} bpm in a minute, strain {_r(ld.get('strain'), 1)}.")
    return (f'<h2 class="sr-only">{_e(summary)}</h2><style>{CSS}{DAILY_CSS}{WORKOUT_CSS}</style><div class="rc">'
            f'<div class="np">{top}<div class="wh" style="margin-top:14px">{hero}{ring}</div>{prov}'
            + _ribbon(hr, r.get("minutes")) + _zones(hr) + '<div class="cols" style="margin-top:8px">'
            + _hrr(rec) + _wload(ld) + _run(run) + (_section("Notes", notes_html) if notes_html else "")
            + "</div>" + FOOTER + "</div></div>")
