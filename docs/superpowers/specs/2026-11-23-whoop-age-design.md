# WHOOP-style Age for Recovery Coach: design

Date: 2026-11-23. Status: draft for the owner's review.

## Intent and why

The owner's friends wear WHOOP and see a WHOOP Age. He wants his Body Age to be computed the way WHOOP computes
WHOOP Age, so the two numbers can be compared side by side. The current Body Age (openbodyscore healthspan v1.2:
population-median anchors, 8-year doubling, x0.6, a U-shaped sleep curve) answers a different question and reads
about 3 years older than a WHOOP-style estimate on the same data (30.0 vs 26.8 on 2026-11-22).

Decision (owner, 2026-11-23): **replace** Body Age with the WHOOP-style model (not side by side), and **rebuild the
history from a fresh Apple Health export** (approach A below).

## Source

WHOOP 2025 white paper "The WHOOP Healthspan Feature" (Section 01, Table 1, Table 2). It publishes the framework,
the nine metrics, their targets and the cited studies, but **not** the hazard-ratio functions or the overlap
(structural-equation) adjustment factors. So parity is approximate by construction: curves come from the studies the
paper cites, and one overlap factor is fitted to the paper's own Table 2 worked examples.

## Goals

- Nine metrics, WHOOP's targets, WHOOP's conversion: years = 10 x ln(HR) against a health-optimised target, summed.
- WHOOP Age from the last 6 months; Pace of Aging from the last 30 days, WHOOP's way.
- Land within about half a year of every Table 2 anchor (30-year-old male / female, U.S. average and WHOOP member).
- Full 6 months of every metric from day one, via the Health export.
- Dashboard's Body age tab shows the new number, Pace, and the nine metrics with value, target and years.

## Non-goals

- Exact WHOOP parity (impossible without the unpublished curves and factors; device differences remain).
- Any change to Recovery, Strain, Sleep, Stress, workouts or the race/training cards.
- Keeping the old model selectable. It stays in git history only.

## Anti-goals

- A number that looks precise but swings week to week on thin data (each metric needs enough days, else neutral).
- Counting the same exercise twice (zone time comes only from de-duplicated logged workouts).
- Medical framing. It remains "an estimate from population research, not a medical test".
- Pushing or merging without the owner's explicit OK.

## Constraints

- Engine: Python standard library only; inputs written by the courier (the skill); state documents carry a checksum.
- Dashboard is an artifact made from the Dashboard type: content lives in its database (layout via the type's
  instructions, data in `data/rc/...`). The engine writes `data/rc/report/latest` and `data/rc/weeks/<date>`.
- The skill pins an engine commit; a new pin goes in a skill update the owner saves.
- The Health export is processed only in the session workspace and deleted after reading.

## Approaches

- **A. Replace and rebuild from a Health export (chosen).** New model; the import computes zone minutes for every
  past workout from its minute heart rate and reads body fat / lean mass; the weekly history is recomputed on the new
  method. Accurate from day one, no step in the chart. Costs one export and import work for zones.
- **B. Replace from today, no export (not chosen).** Zone minutes only from workouts scored since 2026-10-22, so the
  zone metrics rest on ~5 weeks until 6 months accumulate; the chart keeps old points with a marked step.
- **C. Side by side (rejected by the owner).** Two ages that disagree; more dashboard work.

## The model

Window: the 182 days ending on the report date, plain mean (WHOOP describes no weighting). Per-week metrics = total
over the days with data x 7 / days. A metric with fewer than 30 days of data (VO2 max: fewer than 2 readings, the
latest within 120 days; lean mass: none within 90 days) counts 0 years and is listed as missing.

| Metric | Value used | Target (age 30, adjusts with age) | ln HR vs target |
|---|---|---|---|
| Hours of sleep | mean measured sleep (h) | 7 h | < 7: (7 - h) x ln 1.12; 7-9: ln 0.98; > 9: 0 |
| Sleep consistency | mean nightly score, 0-100% (below) | 70% | -0.012 x (score - 70), score clamped 40-95 |
| Steps | mean steps/day | 8,000 (< 60), 5,600 (60+) | Ding 2025 curve (2k-12k), relative to target |
| HR zones 1-3 | min/week at 50-80% HRmax in logged workouts | 100, -10%/decade after 30 | Lee 2022 moderate curve (10-600 min), relative |
| HR zones 4-5 | min/week at >= 80% HRmax in logged workouts | 10, -10%/decade after 30 | knots (0, 1.00) (17.5, 0.79) (31, 0.72) (150, 0.69) |
| Strength | min/week of strength workouts | 40 | knots (0, 1/0.83) (40, 1.00) (60, 0.98) (120, 0.98) |
| VO2 max | mean Apple VO2 max readings | 44 M / 38 F, -11%/decade after 30 | (target - v) / 3.5 x -ln 0.87 |
| Resting HR | mean sleeping heart rate | 60 M / 64 F | (b - target) / 10 x ln 1.09 |
| Lean body mass | latest lean mass % (90 days) | 80% M / 67% F (only age-30 values are published; held at all ages) | (target - pct) / 10 x ln 1.11; missing = 0 |

- Years per metric = 10 x ln HR. Overlap factor 0.70 multiplies every metric except the two sleep metrics (fitted by
  least squares to Table 2; recheck in a test). Total = sum. WHOOP Age = age + total. No caps (WHOOP states none).
- Sleep consistency (WHOOP: similarity of the last 24 h's sleep/wake times to the previous 4 days): for each night,
  the mean overlap (intersection / union of the [sleep onset, wake] windows on the clock) with each of the previous
  4 nights that have times (at least 2), x 100.
- Zones use the engine's existing limits (50/60/70/80/90% of HRmax) and its workout de-duplication.
- Pace of Aging = 1 + (total over the last 30 days - total over 182 days) / 0.5, clipped to -1 ... 3; needs at
  least 21 of the last 30 days with sleep data, else withheld with a reason.
- Spike result on the owner's data (2026-11-22, zones from 5 weeks only): 26.8 (age 27.7), Pace 0.28x.

## Data changes

- Day rows gain `z13_min` and `z45_min` (logged-workout zone minutes that day), filled by the morning run (from the
  workouts it scores) and by the back-fill (from the import's minute heart rate inside workouts).
- Vitals gain `lbm_pct` (from `leanBodyMass` / `bodyMass`, or 100 - `bodyFatPercentage`); the import reads the same
  types from the export. Stored as the latest value with its date.
- Weekly report card: `body_age` = {body_age, age, pace, items: [{name, value, unit, target, years}], missing,
  model: "whoop-v1"}. Weekly rows and `state_long.weeks` keep their current shape (delta still recorded for the chart).
- The import is run again to rebuild days, weeks and state; earlier `data/rc/weeks` documents are overwritten.

## Dashboard and skill

- Body age tab: headline "WHOOP-style Age", Pace, and nine rows (value, target, +/- years); the trend chart reads the
  rebuilt weeks. Exact edits follow the Dashboard type's instructions (describe_type) at build time.
- Skill: add the body-composition Health queries to the morning run, Body Age wording updated, new engine pin,
  delivered as a skill update for the owner to save.

## Testing

- Unit tests for each metric curve at and around its target, the consistency score and the Pace formula.
- Calibration test: the four Table 2 profiles land within 0.6 years of WHOOP's values.
- Back-fill test on the existing fixtures: zone columns filled from workout minute heart rate.

## Calls made by the designer (owner can override)

- Resting HR = sleeping heart rate (WHOOP measures it during sleep), not Apple's daytime resting HR (+0.6 years).
- VO2 max as the Watch reports it, no bias correction (WHOOP uses its own estimate; a +6 correction would read -1.7).
- Zone time only inside logged workouts (as WHOOP counts activities), not all-day heart rate.
- The curves and the 0.70 factor above (WHOOP doesn't publish its own).

## Left to the builder

File and function layout, test structure, the dashboard edit mechanics, how missing metrics are worded on the card.
