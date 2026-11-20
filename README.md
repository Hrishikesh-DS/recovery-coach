# recovery-coach

Whoop-style **Recovery, Strain, Sleep, Stress, training load and Body Age** from an Apple Watch — computed by a small, zero-dependency Python engine and shown as a daily card inside Claude.

Built for an Apple Watch Ultra 2 (no Apple Readiness score on that model), but it works with any Apple Watch that records HRV, heart rate and sleep stages.

> Not a medical device and not medical advice. Scores are personal trend estimates; Body Age is a habit-based estimate from population research, not a medical test.

## What it computes

| Score | Range | In short |
|---|---|---|
| Recovery | 0–100 % (green ≥ 67, yellow 34–66, red ≤ 33) | Last night's HRV (60 %), sleeping heart rate (20 %), sleep performance (15 %) and breathing rate (5 %), each against your own 60-day normal (median ± MAD), mapped through a logistic. One very bad signal caps the score so it can't be averaged away. |
| Strain | 0–21 | Edwards TRIMP over the whole day (heart-rate zones 50–100 % HRmax, weights 1–5), mapped as `21 × (1 − e^(−TRIMP/K))`, K calibrated to your own days. Per-workout strain too. |
| Sleep | % of need | Need = your goal + recent sleep debt (14-night weighted, ≤ 2 h) + extra after high-strain days. Also efficiency, consistency, interruptions and a suggested bedtime. |
| Strain target | band | Suggested strain for today from the recovery zone, lowered when form (TSB) is very negative. |
| Training load | CTL / ATL / TSB, ACWR | Fitness (42-day), fatigue (7-day), form, and an acute:chronic ratio flag for spikes or detraining. |
| Stress | 0–3 | Heart rate during still, awake hours against your 14-day baseline, blended with daytime HRV when present. |
| Health monitor | flags | HRV, sleeping HR, breathing rate, SpO₂ and wrist temperature against your normal; two or more out of range raises a "possible illness or strain" flag. |
| Body Age and Pace of Aging | years, −1×…3× | openbodyscore healthspan v1.2: sleep duration, sleep regularity, steps + strength, VO₂ max, resting HR (and BMI if available), each converted to years through published mortality curves. |
| Journal | associations | Yes/no habits (alcohol, late caffeine, …) compared with next-morning recovery once there are ≥ 5 days each way. |

Each module's docstring documents its formulas, constants and sources.

## How it is used

The engine runs inside a Claude skill. Each morning, from the Claude phone app (the only place Claude can read Apple Health), you ask for your recovery. Claude:

1. queries Apple Health for last night and yesterday,
2. writes compact input files,
3. downloads a pinned release of this engine and runs it,
4. shows the daily card inline and saves the day to a private dashboard.

The skill itself (the instructions Claude follows) lives in the user's Claude account, not in this repository.

## Command line

```
python3 -m recovery_coach run      --in DIR --out DIR [--date YYYY-MM-DD]   # morning scores + daily card
python3 -m recovery_coach backfill --in DIR --out DIR [--date YYYY-MM-DD]   # first run: history, calibration, back-test
python3 -m recovery_coach weekly   --in DIR --out DIR [--date YYYY-MM-DD]   # weekly report + card
```

Input file formats are documented in `recovery_coach/inputs.py`; the result and card schema in `recovery_coach/card.py`. Malformed input exits with code 2 and names the file and line.

The skill's courier steps (time-zone localizing, minute-heart-rate windows, aggregating raw samples, printing outputs for the dashboard) ship with the engine: `python3 -m recovery_coach.helper COMMAND`, documented in `recovery_coach/helper.py`.

## Development

```
python3 -m pytest -W error          # 200+ tests, standard library only at runtime
python3 tests/render_cards.py       # dev harness: screenshots the cards (needs Playwright + Chrome)
```

The repository contains code and synthetic fixtures only — never real health data.

## Credits

This project re-implements published methods and ports tables from:

- **openbodyscore / Watch2Whoop** (Apache-2.0) — recovery, strain, sleep-need and healthspan methodology; v1.2 healthspan tables ported verbatim in `recovery_coach/healthspan_tables.py` (see its header and `tests/fixtures/healthspan_v12.NOTICE`).
- **OpenReadiness** (MIT) — missing-signal weight redistribution and limiting-factor caps.
- **ridgwayb/readiness** — one-directional breathing-rate and temperature deviations.
- **Open Wearables** (MIT) — sleep-quality components.
- Research: Edwards 1993, Tanaka 2001, Banister / Morton 1990, Plews 2012–13, Buchheit 2014, Radin 2020, Yin 2017, Windred 2024, Ding 2025, Momma 2022, Kodama 2009, Kaminsky 2022, Jensen 2013.

## License

MIT for this project's code (see `LICENSE`). Ported openbodyscore material remains under Apache-2.0; its notices are kept with the ported files.
