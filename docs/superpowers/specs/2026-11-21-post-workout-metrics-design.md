# Post-workout metrics for Recovery Coach: design

Date: 2026-11-21. Status: draft for the owner's review.

## Intent and why

Recovery Coach (this engine + the owner's Claude skill + his private dashboard artifact) already gives a WHOOP-style
morning view. The owner wants the post-workout insight that Strava, Runna, WHOOP, Garmin, Polar, Coros and the Apple
Watch apps give, and Apple Fitness doesn't. A native iPhone/Watch app would unlock more, but it is weeks of work, so
it comes later at its own pace. This project gets as much as possible **now, inside the current skill**, from the data
the Claude iPhone app's Health tool can read.

"As much as possible" is literal: every metric in the research catalogue (77 entries, see the coverage map at the
end) is either built, already covered by an existing score, or skipped with a stated reason. The research report
"Post workout metrics beyond Apple Health" (kept outside this repo, as it was delivered in chat) holds the formulas,
constants and sources; this design names which ones to use.

## Who it's for and how it's used

One person, Apple Watch Ultra 2, Claude iPhone app. A typical week (from his own data, summarised): outdoor runs at
weekends, shorter weekday runs (often indoor) usually followed by a strength session, strength about twice a week
(each logged twice: Apple's strength workout plus an "other" copy from a second app; the engine's de-duplication
already handles this), and evening walks. VO2 max is the biggest drag on his Body Age, so running fitness matters most.

Two ways in, both in the existing skill:
1. **Morning** ("recovery", unchanged habit): scores any of yesterday's workouts that weren't already scored, then
   the daily and weekly figures below.
2. **On demand** ("workout", or "how was my run/workout"): right after a session, scores today's new workouts and
   shows the result within a few minutes. The morning run then reuses it instead of reading the workout again.

## Goals

- Per-workout analysis, saved to the dashboard, for every workout (runs get the most, walks and strength less).
- Daily and weekly training figures that go beyond today's CTL/ATL/TSB/ACWR.
- Every metric labelled honestly: exact, or "approx" when it rebuilds an unpublished vendor model (training effect,
  status labels, performance condition, running tolerance).
- The morning reply stays at most five short lines; the workout reply at most four. Detail lives on the dashboard.
- Trends become useful quickly: an optional one-time back-fill of recent workouts.

## Non-goals

- A native iPhone/Watch app (separate, later project).
- Anything needing a GPS route, beat-to-beat (RR) data, per-second cycling power, raw motion data, or another brand's
  sensor (the 11 "No" entries in the coverage map).
- Changing the Recovery score, Strain score or Body Age model. (Feeding heart-rate recovery into Body Age needs a
  mortality relation the v1.2 tables don't have; revisit later.)
- Switching recovery HRV to RMSSD: his watch records no RMSSD samples (checked: none in the last week).
- Runna-style pace compliance: target paces aren't in Apple Health.
- Sets, reps and weights from his lifting app: Apple Health stores none.

## Anti-goals (failure even if it "works")

- A morning run that becomes much slower or flakier than today. Extra Health queries are bounded per workout.
- Approximations presented as exact copies of Garmin/WHOOP numbers.
- Medical claims (heart-rate recovery flags are "check how you feel" signals, as with the existing health monitor).
- Personal data in this repo: code and synthetic fixtures only.
- Breaking existing state: old `state.json` documents must still load; new keys are optional.

## Constraints and data reality (checked against his Health data on 2026-11-21)

- Engine: Python standard library only; tests with pytest; formulas documented in module docstrings, as now.
- The skill pins an engine commit; shipping means a new commit and a skill update. The owner pushes/merges only
  after he says so.
- Readable through the Health tool and confirmed present in his data:
  - heart-rate samples during workouts about every 5 seconds (130 samples in 12 minutes of a strength session),
    continuing about 3 minutes after the workout ends;
  - `workoutEffortScore` on most runs and walks, but on only one of his recent strength sessions;
    `estimatedWorkoutEffortScore` on runs and walks only;
  - `heartRateRecoveryOneMinute` (Apple's Cardio Recovery) only after outdoor runs and walks, and it barely varies
    between sessions (Apple's value is predicted and capped), so it is a weak signal; keep it as a separate series;
  - workouts with summary, statistics (indoor flag, elevation up/down, temperature, per-workout averages of HR,
    running speed, running power, stride length, vertical oscillation, ground contact time, steps) and breakdown
    (laps, pauses) detail; `runningSpeed`, `runningPower`, `distanceWalkingRunning` samples;
  - `physicalEffort` (METs) is listed as readable; its presence in his data is unchecked.
- Not readable or not recorded: GPS routes, RR intervals, RMSSD (none recorded), Apple's Readiness/Training Load
  values.
- Every Health result passes through the chat, and the tool has a per-call size limit with paging. Per-workout raw
  heart rate is the largest new input (about 700 samples for a 50-minute run).
- The dashboard is a published artifact with a database; new document paths must be allowed by its rules.

## Approaches

1. Morning only: yesterday's workouts are scored each morning. Nothing new to remember, but results come a day late.
2. **Morning + on demand (picked by the owner).** As 1, plus "workout" right after a session; the morning reuses
   saved results, so mornings don't get slower.
3. On demand only: dropped, because a workout he forgets to check would never be scored.

## Design

### Flows

**Workout check (new skill section).** Current time; get the engine; `db_get` settings, the daily state and the
saved workout index. One Health call: today's workouts (statistics detail), effort scores, Cardio Recovery,
breakdown (laps) for runs. For each workout not already saved: one Health call with raw `heartRate` samples from
start to end + 3 minutes, plus `runningSpeed`, `runningPower` and `distanceWalkingRunning` samples for runs. Write the
input files, run `python3 -m recovery_coach workout ...`, save each workout's result and the latest-workout card,
reply in four lines. If a strength session has no effort rating, ask "How hard was it, 1–10?" and save the answer
the same way journal answers are saved (my call).

**Morning (existing section, extended).** Same as today, plus: yesterday's workouts not yet in the saved index go
through the same per-workout queries and the engine scores them inside `run`; one extra query for minute heart rate
across the main sleep (for the Recovery Index). Daily figures are added to the day rows. The Strain line of the
reply gains yesterday's workout headline (e.g. training effect, recovery hours, heart-rate recovery), so the reply
stays at five lines (my call).

**Weekly (existing).** The weekly report adds the week's training figures (monotony and strain, intensity split,
polarization, status label, race predictions) to the dashboard's Workouts tab; the Body Age card is unchanged.

**History back-fill (optional, once).** A "workout history" command scores the last 30 days of runs and strength
sessions (walks skipped) the same way, so "vs your usual" and trends work from day one (my call on 30 days and on
skipping walks).

### Engine changes

- New optional input files (old input sets stay valid):
  - per-workout raw heart rate (time, bpm) and per-workout run series (time, speed, power, cumulative distance);
  - per-workout statistics (indoor, distance, elevation up/down, temperature, averages of HR, speed, power, stride,
    vertical oscillation, ground contact time; laps), effort (rated and estimated) and Apple Cardio Recovery;
  - overnight minute heart rate for the main sleep (kept out of `minutes.csv` so strain and stress don't change);
  - effort answers for unrated strength sessions.
- New modules for per-workout metrics, running performance, and training figures; a new `workout` command; `run`
  calls the same per-workout code for unsaved workouts; `weekly` adds weekly training figures.
- State: the daily state gains a compact per-workout log (last 90 days; only the fields the context, trend and race
  calculations read), covered by the checksum. Old states without it load as an empty log.
- Result schema: a `workouts` list (one dict per workout) and new day-row keys; the card schema grows a workout card.
  The existing daily card is unchanged apart from the strain line's workout headline.
- If a workout's raw heart rate has a median gap above 15 seconds (low-power mode, thinned old data), fall back to
  minute heart rate and mark the burst- and recovery-based metrics as unavailable (my call on 15 s, from the report).

### Which load series each figure uses (settled after the builder check)

- **Existing whole-day series** (Edwards TRIMP, its CTL/ATL/TSB/ACWR, strain): Form %, ramp rate, the easy-day cap
  (shown as a strain value through the existing TRIMP→strain mapping), load-ratio bands, training status, and each
  workout's fitness/fatigue increments and load level (a workout's own Edwards TRIMP, the unit CTL already uses).
- **New workout-only daily load** = the sum of that day's workouts' Banister TRIMP, rest days 0: monotony and
  training strain (Foster's thresholds assume rest days count as 0, which the whole-day series never does) and the
  weekly target range.
- **HRSS** is per workout only, for comparison with TrainingPeaks/intervals.icu; no second CTL line.
- **Session RPE** (effort × minutes) has its own 7:28 ratio and is never mixed into TRIMP-based series.

### Indoor runs (settled after the builder check)

Pace-based fitness numbers use outdoor runs only: Effective VO2 max, VDOT, training paces, race predictions, critical
speed and D′, best efforts, and the efficiency and power-vs-heart-rate trends (Apple calibrates VO2 max outdoors only,
indoor pace comes from wrist motion, and treadmill incline is unknown). Indoor runs still get every heart-rate-based
metric (zones, TRIMP, HRSS, training effect, recovery hours, heart-rate recovery) and are labelled "indoor".
Hilly outdoor runs (from elevation gain) are left out of the efficiency trend.

### Metrics to build (formulas and sources: research report, "Add these twelve" table and catalogue)

Per workout:
- Banister TRIMP (k from sex), HRSS (100 = 1 h at threshold HR; threshold = 0.9 × HRmax until tested), intensity %
  vs the maximum possible TRIMP for that duration, Active Zone Minutes.
- Time in the five heart-rate zones; intensity split low (zones 1–2) / mid (3–4) / high (5).
- Training effect, aerobic and anaerobic, plus recovery hours and a "primary benefit" label: the open RunningMate-style
  EPOC model, labelled approx. Recovery % = recovery hours elapsed vs total, shown until recovered.
- Heart-rate recovery at 60 and 120 s from raw heart rate. End heart rate = highest 10-second mean in the last
  2 minutes before End (my call: a mid-run peak would overstate recovery). Counted only when end heart rate reaches
  zone 4 (≥ 80 % HRmax). Flag ≤ 12 bpm at 60 s. HRRc-style largest drop inside the workout after more than a minute
  above threshold heart rate. Apple's Cardio Recovery saved alongside, never mixed.
- Effort: rated, else estimated, else his answer; session RPE = effort × minutes.
- Context: load level vs the mean of the same activity type over 90 days (Polar bands), fitness/fatigue increments,
  z-score vs the last 10 of the same type.
- Runs only: efficiency factor and aerobic decoupling (steady runs ≥ 20 min, first 10 min dropped, halves from the
  speed series, laps as fallback); Effective VO2 max (Runalyze/Daniels); Polar-style Running Index (open Elevate
  formula); performance condition, approx (this run's efficiency vs the 28-day typical, scaled to −20…+20); vertical
  ratio, efficiency index (speed ÷ power), muscle load (kJ); a power-vs-heart-rate line across easy runs; flat
  running stress score from speed vs threshold pace (threshold pace from VDOT); running-power stress score, approx,
  with "critical power" taken as power at threshold heart rate from that line; best efforts (400 m to the run's
  length) from the cumulative distance series, approx. Indoor and hilly runs: see above.
- Strength: session RPE load. No separate sleep-need top-up: sleep need already rises after high-strain days, and
  changing it would change Recovery (sleep performance), which is a non-goal.

Daily (added to day rows):
- Recovery Index: hours slept after the overnight heart-rate low (lowest 5-minute mean), flag a low in the second half
  of the night and workouts ending within 3 h of sleep onset (my cutoff).
- HRV status: 7-day mean of nightly HRV vs the 60-day baseline band (balanced / unbalanced / low).
- Form % = 100 × (CTL − ATL) ÷ CTL; ramp rate = CTL change over 7 days; easy-day load = (41·CTL − 36·ATL) ÷ 5.
- Monotony (7-day mean ÷ SD of daily TRIMP, cap 10) and training strain; warn at 1.5, alert at 2.0.
- Session-RPE 7:28 ratio (Apple-style training load, as numbers) and its Polar bands.
- Load ratio labels: Polar bands and Garmin bands both shown on the existing ACWR, the source named.
- Intensity split and Polarization Index over 7 and 28 days; load-focus shortage flags over 4 weeks.
- Weekly target load range from the previous 4 weeks; running tolerance (weekly distance cap), approx.
- Training status label, approx (Polar bands on the load ratio, adjusted by VO2 max trend and recovery).

Weekly / race:
- VDOT from the best recent Effective VO2 max shape, Daniels training paces, race predictions for 5 km, 10 km, half
  and full marathon with the marathon-shape penalty; Riegel shown for 5–10 km only; critical speed and D′ from the
  best efforts, approx, only once two or more hard efforts of 2–15 minutes exist.
- Uth VO2 max (15.3 × HRmax ÷ resting HR) as a cross-check next to Apple's VO2 max.

### Dashboard

A new **Workouts** tab in the existing style: the latest workout card (as the reply, in full), a list of recent
workouts, and trend charts (heart-rate recovery by type, efficiency factor of easy runs, run VO2 max vs Apple VO2 max,
weekly intensity split and polarization, monotony, load ratios with both vendors' bands, race predictions). New
documents: one per workout (`data/rc/workouts/<local start, minute precision>`) and `data/rc/workout/latest`; the
rules must allow them. Day-row additions show on the existing charts where they fit.

### Skill changes

New "Workout check" and "Workout history" sections; morning steps 2–7 extended (per-workout queries for unsaved
workouts, the overnight minute heart-rate query, the new saves); reply templates; the description gains the
"workout" triggers. Delivered as a proposed skill edit for the owner to save.

## Calls I made (the owner can overrule any)

On-demand trigger words; end-heart-rate definition and zone-4 gate for recovery; threshold HR = 0.9 × HRmax;
the 15-second density fallback; asking for effort only on unrated strength sessions, only in the workout check;
no strength sleep top-up; the load series per figure and outdoor-only running fitness (both above); the Recovery
Index 3-hour cutoff; 30-day back-fill without walks; workout headline
folded into the existing Strain line; both Polar and Garmin bands shown rather than picking one.

## Left to the builder

Exact file formats and module split; constants named in the research report; precision of stored values; how the
per-workout log is compacted; chart choices within the existing dashboard style; test fixtures (synthetic, with the
report's worked examples as tests: TE 3.1 / 25 h, HRSS ≈ 50, Effective VO2 max 48.3, PI 2.38).

## Appendix: coverage map (all 77 catalogue entries)

NEW = built by this project; HAVE = already covered by an existing score; SKIP = not built, with reason;
NO = needs data Apple Health doesn't give.

| # | Entry | Status |
|---|---|---|
| 1 | Strava Relative Effort | HAVE (per-workout Edwards strain, zone time) |
| 2 | Banister TRIMP / Polar Cardio Load | NEW |
| 3 | HRSS / TRIMP100 | NEW |
| 4 | TrainingPeaks hrTSS, tTSS | HAVE via HRSS |
| 5 | rTSS / NGP | NEW, flat version only |
| 6 | Power TSS, NP, IF, VI (cycling) | NO (no power meter) |
| 7 | Stryd Running Stress Score | NEW, approx (running power) |
| 8 | Stryd LBSS | NO |
| 9 | Coros Training Load | HAVE via TRIMP |
| 10 | Google Cardio Load | HAVE via TRIMP |
| 11 | Polar Muscle Load | NEW (runs with power) |
| 12 | Perceived Load / session RPE | NEW |
| 13 | Athlytic Effort | HAVE via Apple Effort + intensity % |
| 14 | Athlytic TRIMPexp gauge | NEW (intensity %) |
| 15 | Bevel Strain % | HAVE (Strain) |
| 16 | Bevel Cardio Points | SKIP (unpublished; overlaps Strain) |
| 17 | Polar session load level | NEW |
| 18 | Athlytic Fitness/Fatigue bump, vs typical | NEW |
| 19 | Fitbit Active Zone Minutes | NEW |
| 20 | Amazfit PAI | SKIP (equation unpublished; overlaps weekly load) |
| 21 | Aerobic Training Effect | NEW, approx |
| 22 | Anaerobic Training Effect | NEW, approx |
| 23 | Garmin Primary Benefit / Polar Training Benefit | NEW, approx label |
| 24 | Recovery Time | NEW, approx |
| 25 | Coros Recovery % | NEW, approx |
| 26 | Heart-rate recovery | NEW |
| 27 | intervals.icu HRRc | NEW |
| 28 | Oura Recovery Index | NEW |
| 29 | Polar Nightly Recharge | HAVE via Recovery (SDNN, sleeping HR, breathing) |
| 30 | Polar orthostatic test | NO (RR data) |
| 31 | Garmin HRV Status | NEW |
| 32 | Garmin Training Readiness | HAVE via Recovery (+ recovery hours shown) |
| 33 | WHOOP muscular load → sleep need | HAVE (sleep need already rises after high-strain days) |
| 34 | Efficiency Factor | NEW |
| 35 | Aerobic decoupling | NEW |
| 36 | HRV4Training Aerobic Efficiency | HAVE via EF trend on filtered easy runs |
| 37 | GoldenCheetah Efficiency Index | NEW |
| 38 | Runalyze Effective VO2 max | NEW |
| 39 | Polar Running Index | NEW |
| 40 | Garmin Performance Condition | NEW, approx |
| 41 | Uth VO2 max | NEW (cross-check) |
| 42 | Vertical ratio | NEW |
| 43 | Power-vs-HR line | NEW |
| 44 | Grade Adjusted Pace | NO (GPS) |
| 45 | Garmin Running Economy, Step Speed Loss | NO (HRM 600) |
| 46 | Garmin lactate threshold detection | NO (chest strap); threshold HR estimated instead |
| 47 | DFA-alpha1 | NO (RR data) |
| 48 | VDOT and training paces | NEW |
| 49 | Runalyze race prognosis + marathon shape | NEW |
| 50 | Riegel | NEW (5–10 km only) |
| 51 | Strava Performance Predictions | NO (GPS runs, ML) |
| 52 | Coros Running Fitness | SKIP (weights unpublished; VDOT covers it) |
| 53 | Garmin Endurance Score | SKIP (unpublished; marathon shape covers it) |
| 54 | Garmin Hill Score / Runalyze Climb Score | NO (elevation profile) |
| 55 | Best efforts / PRs | NEW, approx (distance series) |
| 56 | Critical speed, D′ | NEW, approx (needs hard efforts) |
| 57 | W′ balance / Garmin Stamina | NO (per-second power model) |
| 58 | Runna Pace Insights | SKIP (targets not in Health) |
| 59 | Garmin Running Tolerance | NEW, approx |
| 60 | Garmin Impact Load | NO (elevation profile) |
| 61 | Fitness & Freshness / PMC / Coros / Suunto | HAVE (CTL/ATL/TSB) |
| 62 | Form % | NEW |
| 63 | Ramp rate | NEW |
| 64 | Runalyze easy TRIMP | NEW |
| 65 | Monotony and Strain | NEW |
| 66 | Polar Cardio Load Status | NEW (bands on load ratio) |
| 67 | Garmin Load Ratio | HAVE (ACWR) + NEW bands |
| 68 | Garmin Load Focus / Bevel Cardio Focus | NEW |
| 69 | Polarization Index | NEW |
| 70 | Garmin Training Status / Bevel Cardio Status | NEW, approx |
| 71 | Weekly target load | NEW |
| 72 | Oura Activity Balance | HAVE (ACWR) |
| 73 | Gentler Streak Activity Path | HAVE (ACWR + target range) |
| 74 | WHOOP Muscular Load | NO (strap motion data); session RPE covers strength load |
| 75 | Bevel Muscular Strain, Muscle Breakdown | SKIP (needs set logs) |
| 76 | Volume load, estimated 1RM | SKIP (needs set logs) |
| 77 | Session RPE for lifting | NEW |
