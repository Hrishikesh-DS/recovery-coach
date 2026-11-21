# Post-workout Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every workout (and the training around it) from Apple Watch data inside the existing Recovery Coach engine, skill and dashboard: 44 new metrics from the design's coverage map.

**Architecture:** New pure-function modules compute per-workout, running, race and training figures from new optional input files (raw workout heart rate, run series, workout statistics, overnight minute HR, effort answers). A new `workout` command scores unsaved workouts on demand; `run` scores yesterday's unsaved ones and adds daily figures; `weekly` adds race and weekly figures. The daily state gains a compact 90-day workout log so later runs see history. Cards and a new dashboard Workouts tab show it.

**Tech Stack:** Python ≥ 3.10, standard library only at runtime; pytest; HTML/CSS/inline SVG cards (card.py rules); the dashboard is a Dashboard-type artifact (store documents + `files/index.html`, d3 v7).

**Spec:** `docs/superpowers/specs/2026-11-21-post-workout-metrics-design.md` (approved 2026-11-21). Formulas and sources: research report "Post workout metrics beyond Apple Health" (`/home/claude/reports/`, not in repo) and its notes (`/home/claude/research_notes/Post workout metrics beyond Apple Health/`). Cite the paper or vendor page in each module docstring, as the repo already does; never copy GPL or unlicensed code.

## Global Constraints

- Runtime: standard library only; `python3 -m pytest -W error` must pass after every task.
- Old inputs stay valid: every new file is optional for `run`; an old `state.json` (no `workouts` key, with `crc`) loads and its checksum still verifies.
- Every approximated metric carries `"approx": true` in its result dict and the word "approx" wherever a card prints it (training effect, recovery hours, benefit label, performance condition, running tolerance, status label, RSS, best efforts, critical speed).
- Threshold HR = 0.9 × HRmax until a tested value exists. Banister k = 1.92 (male), 1.67 (female); A = 0.64 / 0.86 (A cancels in HRSS and intensity %).
- Heart-rate density: a workout's raw HR is "dense" when the median gap between samples ≤ 15 s; otherwise burst- and recovery-based metrics are `null` with a reason.
- Heart-rate recovery only when end HR ≥ 80 % HRmax; end HR = highest 10-s mean in the last 2 min before End; flag HRR60 ≤ 12 bpm.
- Pace-based fitness numbers use outdoor runs only; indoor runs are labelled "indoor"; hilly runs (elevation gain > 10 m per km, my call) are left out of the efficiency trend.
- Load series: whole-day Edwards series for Form %, ramp, easy cap, bands, status, per-workout fitness/fatigue increments and load level; workout-only daily Banister load (`wload`, rest days 0) for monotony, training strain and weekly target; HRSS per workout only; session RPE (`srpe`) only in its own 7:28 ratio.
- Bands: Polar 7:28 — ≤ 0.8 detraining, 0.8–1.0 maintaining, 1.0–1.3 productive, > 1.3 overreaching, > 1.5 injury risk. Garmin — < 0.8 low, 0.8–1.4 optimal, 1.5–1.9 high, ≥ 2.0 very high. Polar session level — < 0.5 very low, 0.5–0.75 low, 0.75–1.25 medium, 1.25–2 high, ≥ 2 very high. Monotony warn 1.5, alert 2.0, cap 10.
- No medical claims; no personal data in the repo (synthetic fixtures only); no push, merge, skill save or live-dashboard write without the owner's OK.
- Cards follow card.py's host rules (no scripts, flat fills, 11/13/15/20/28/64 px scale, weights 400/500, every number printed, palette tokens) and size limits (workout card < 14 KB).

## Review Focus

1. A workout logged twice (Apple strength + an "other" copy from another app) is scored and saved once — `dedupe_workouts` decides; test in Task 9.
2. A workout checked less than 3 minutes after it ended has no 2-minute recovery yet: it is saved `provisional`, and the next run rescores it — tests in Tasks 3 and 9.
3. Sparse heart rate (low-power mode, thinned old data): every burst/recovery metric is `null` with reason "heart rate too sparse", nothing raises — test in Task 3.
4. An old daily state (no `workouts`, with `crc`) and an empty first log: `run` works and the first workout gets "no history yet" context instead of a crash — tests in Tasks 2 and 9.
5. Indoor or zero-distance runs, and runs with no speed series: running metrics `null` with a reason, HR metrics still present — test in Task 6.

---

### Task 1: New input files

**Files:**
- Modify: `recovery_coach/inputs.py` (docstring file list; dataclasses; readers; `Inputs`; `OPTIONAL`; `REQUIRED`)
- Test: `tests/test_inputs.py`

**Interfaces:**
- Produces (in `inputs.py`):
  - `@dataclass RunPoint(time: datetime, speed: float | None, power: float | None, dist: float | None)` (m/s, W, cumulative m)
  - `@dataclass Lap(start: datetime, end: datetime, dist: float | None)`
  - `@dataclass WorkoutStats(start: datetime, end: datetime, kind: str, indoor: bool | None = None, dist: float | None = None, elev_up: float | None = None, elev_down: float | None = None, temp_c: float | None = None, avg_hr: float | None = None, max_hr: float | None = None, avg_speed: float | None = None, avg_power: float | None = None, stride: float | None = None, vo_cm: float | None = None, gct_ms: float | None = None, effort: float | None = None, effort_est: float | None = None, cardio_recovery: float | None = None, laps: list[Lap] = [])`
  - `read_wk_hr(path) -> list[tuple[datetime, float]]` — `wk_hr.csv` header `time,bpm`
  - `read_wk_series(path) -> list[RunPoint]` — `wk_series.csv` header `time,speed,power,dist` (empty cells allowed; speed 0–12, power 0–1500, dist 0–300000)
  - `read_wk_stats(path) -> list[WorkoutStats]` — `wk_stats.json`: `{"workouts": [{"start", "end", "kind", ...the fields above..., "laps": [{"start","end","dist"}]}]}`; effort 1–10; cardio_recovery 0–100
  - `read_night_hr(path) -> list[tuple[datetime, float]]` — `night_hr.csv` header `time,bpm`
  - `read_efforts(path) -> dict[str, float]` — `efforts.json` `{"answers": {"<start ISO, minute>": 1-10}}`; keys normalised to `YYYY-MM-DDTHH:MM±HHMM`
  - `Inputs` gains `wk_hr`, `wk_series`, `wk_stats`, `night_hr`, `efforts` (all `None` when absent)
  - `REQUIRED["workout"] = ("state.json", "workouts.csv", "wk_stats.json", "wk_hr.csv", "wk_series.csv")`; `run`'s tuple unchanged
  - `workout_id(start: datetime) -> str` — `start.isoformat(timespec="minutes")` with the offset as `±HHMM` (e.g. `2026-11-21T08:23-0600`); the one id used by the log, the dashboard path and `efforts.json`

- [ ] **Step 1: Write failing tests** `test_read_wk_stats_full_and_minimal`, `test_read_wk_series_blank_cells`, `test_read_efforts_normalises_keys`, `test_effort_out_of_range_names_file`, `test_run_inputs_without_new_files_still_load`, `test_workout_id_format`. Assertions: a minimal stats entry `{"start","end","kind"}` reads with all optional fields `None` and `laps == []`; effort 11 raises `InputError` whose text starts `wk_stats.json: workouts[0].effort`; `efforts.json` key `"2026-11-21T08:23:39-06:00"` comes back as `"2026-11-21T08:23-0600"`; `load_inputs(folder, REQUIRED["run"])` on today's fixture folder has `wk_hr is None`.
- [ ] **Step 2:** `python3 -m pytest tests/test_inputs.py -q` → the new tests fail (ImportError/AttributeError).
- [ ] **Step 3:** Implement the readers with the existing `_csv_rows`, `_cell`, `_num`, `_object` helpers and `parse_time`; extend the module docstring's file list with the five files and their ranges.
- [ ] **Step 4:** `python3 -m pytest -q -W error` → all pass.
- [ ] **Step 5:** Commit `feat(inputs): workout HR, run series, workout stats, overnight HR and effort answers`.

### Task 2: Workout log in the daily state

**Files:**
- Create: `recovery_coach/workout_log.py`
- Modify: `recovery_coach/inputs.py` (`read_state` accepts `"workouts"`; `state_crc(cols, weeks=None, workouts=None)`), `recovery_coach/state.py` (`compact_state`, `next_state`)
- Test: `tests/test_workout_log.py`, `tests/test_state.py`

**Interfaces:**
- Produces:
  - `@dataclass LogEntry(id: str, date: str, kind: str, minutes: float, indoor: bool | None = None, provisional: bool = False, edwards: float | None = None, banister: float | None = None, hrss: float | None = None, te: float | None = None, te_an: float | None = None, rec_h: float | None = None, hrr60: float | None = None, hrr120: float | None = None, ef: float | None = None, dec: float | None = None, evo2: float | None = None, dist: float | None = None, secs: float | None = None, avg_hr: float | None = None, effort: float | None = None, srpe: float | None = None, split: list[float] = [] (minutes low/mid/high), best: dict[str, float] = {} (distance label → seconds), power: float | None = None)`
  - `LOG_DAYS = 90`; `entry_to_dict(e) -> dict` (drops `None`, rounds: TRIMP/HRSS 1 dp, TE 1 dp, EF 3 dp, the rest as row precision) and `entry_from_dict(d, file, where) -> LogEntry`
  - `merge_log(log: list[LogEntry], new: list[LogEntry], today: date) -> list[LogEntry]` — by id, new wins, keeps ids whose date ≥ today − 89, sorted by id
  - `State` gains `workouts: list[LogEntry] = []`; `state_crc` includes `"workouts"` in the payload **only when the document has the key**; `compact_state(..., workouts=None)` writes the key only when given a non-empty list; `next_state(state, patches, settings_patch=None, workouts=None)`

- [ ] **Step 1: Write failing tests** `test_old_state_crc_unchanged` (an existing fixture state's `crc` still verifies and `state.workouts == []`), `test_state_round_trip_with_log` (compact → read → same entries, crc verifies), `test_crc_detects_changed_log_value`, `test_merge_log_drops_old_and_replaces_provisional`, `test_entry_to_dict_drops_nulls`.
- [ ] **Step 2:** Run them → fail.
- [ ] **Step 3:** Implement; `read_state` allowed keys become `["settings","cols","rows","weeks","workouts","crc"]`.
- [ ] **Step 4:** Full suite passes.
- [ ] **Step 5:** Commit `feat(state): 90-day workout log in the daily state`.

### Task 3: Heart-rate metrics per workout

**Files:**
- Create: `recovery_coach/hr_metrics.py`
- Test: `tests/test_hr_metrics.py`

**Interfaces:**
- Consumes: raw samples `list[tuple[datetime, float]]` (Task 1)
- Produces (all pure):
  - `dense(samples) -> bool` — median gap ≤ `DENSE_GAP_S = 15`
  - `zone_minutes(samples, start, end, hrmax) -> list[float]` — 5 Edwards zones (50–60 … 90–100 % HRmax), each sample weighted by the gap to the next (capped at 60 s), only inside start–end
  - `split(zone_min) -> list[float]` — `[z1+z2, z3+z4, z5]`
  - `banister(samples, start, end, rest, hrmax, k) -> float` — Σ minutes · r · e^(k·r), r clamped 0–1 (A omitted; documented)
  - `hrss(samples, start, end, rest, hrmax, k, lt_hr) -> float` — 100 · banister ÷ (60 · r_LT · e^(k·r_LT))
  - `intensity_pct(banister_val, minutes, k) -> float` — 100 · value ÷ (minutes · e^k)
  - `azm(samples, start, end, rest, hrmax) -> float` — 1/min at 40–59 % HRR, 2/min at ≥ 60 % (approx; Fitbit cutoffs unverified)
  - `end_hr(samples, end) -> float | None` — highest 10-s mean in [end − 120 s, end]
  - `hr_recovery(samples, end, hrmax, now) -> dict` — `{"hrr60", "hrr120", "end_hr", "flag": "low"|None, "reason": str|None, "provisional": bool}`; HR at end+60 s = mean of samples in [end+50, end+70]; HRR120 = end HR − min over (end, end+120]; reasons: "heart rate too sparse", "didn't reach zone 4", "ended too recently" (now < end + 125 s → provisional)
  - `hrrc(samples, start, end, lt_hr) -> float | None` — largest drop within 60 s that follows ≥ 60 s continuously above lt_hr (dense only)

- [ ] **Step 1: Write failing tests** `test_hrss_worked_example` (45 min constant 150 bpm, rest 55, max 185, lt 166.5 → `round(hrss, 1) == 50.1`), `test_zone_minutes_sum_to_duration`, `test_intensity_pct_bounds` (0 < x ≤ 100), `test_hr_recovery_basic` (synthetic: end HR 170, 150 at +60 s, 135 at +120 s, hrmax 188 → hrr60 20, hrr120 35), `test_hr_recovery_low_flag` (drop 10 → flag "low"), `test_hr_recovery_sparse` (samples every 60 s → both null, reason "heart rate too sparse"), `test_hr_recovery_too_recent` (now = end + 60 s → provisional True), `test_hr_recovery_not_zone4` (end HR 140 → reason "didn't reach zone 4"), `test_azm_credits`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement; module docstring cites Banister 1991, Elevate/intervals.icu HRSS, Cole 1999 (HRR ≤ 12), Bevel (zone-4 gate), Fitabase (AZM).
- [ ] **Step 4:** Suite passes.
- [ ] **Step 5:** Commit `feat: per-workout heart-rate metrics`.

### Task 4: Training effect and recovery time (approx)

**Files:**
- Create: `recovery_coach/effect.py`
- Test: `tests/test_effect.py`

**Interfaces:**
- Produces: `training_effect(samples, start, end, rest, hrmax, vo2max, zone_min) -> dict` → `{"te": float, "te_an": float|None, "peak_epoc": float, "rec_h": float, "benefit": str, "approx": True, "reason": str|None}`
- Model (RunningMate-style, constants pinned here): per step dt (min, gap to next sample, capped 1 min; pauses > 10 s count I = 0): `I = clamp((hr − rest)/(hrmax − rest), 0, 1)`, `g = 0.015·(e^(6.5·I) − 1)`, `tau = 10 + 50·I³`, `ss = g·tau`, `EPOC = ss + (EPOC − ss)·e^(−dt/tau)`; `s = clamp(vo2max/45, 0.75, 1.4)` (vo2max `None` → s = 1); thresholds `T = (6, 15, 35, 80, 180)·s`; TE: linear 0→1 below T1, `k + ln(peak/T_k)/ln(T_{k+1}/T_k)` between, 5.0 at ≥ T5; anaerobic: `A = Σ((I − 0.85)/0.15)²·dt` while I > 0.85, bursts = runs ≥ 15 s at I ≥ 0.9, `b = 1 + min(0.25·(n − 1), 2)` (n ≥ 1), `te_an = 5·(1 − e^(−A·b/15))`, `None` with reason when not dense; `rec_h = min(24·(peak/(35·s))^0.75, 96)`.
- Benefit label (my rule): te_an ≥ 2 and te_an ≥ te − 1 → "Anaerobic"; else the zone with the most minutes: z1 → "Recovery", z2 → "Base", z3 → "Tempo", z4 → "Threshold", z5 → "VO2 max"; te < 1 → "Recovery".
- Also `recovered_pct(rec_h, hours_since_end) -> int` = clamp(100·hours_since/rec_h, 0, 100).

- [ ] **Step 1: Write failing tests** `test_te_worked_example` (50 min at 150 bpm, 5-s samples, rest 55, max 185, VO2 50 → `round(peak_epoc,1) == 41.4`, `round(te,1) == 3.1`, `round(rec_h) == 25`), `test_te_caps_at_5`, `test_anaerobic_needs_bursts` (steady 150 → te_an < 0.5; 6 × 30 s at 180 → te_an ≥ 2), `test_te_an_none_when_sparse`, `test_benefit_labels`, `test_recovered_pct`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement (docstring: Firstbeat white papers, RunningMate docs, "approx"). **Step 4:** Suite passes.
- [ ] **Step 5:** Commit `feat: approximate training effect, recovery hours and benefit`.

### Task 5: Running metrics

**Files:**
- Create: `recovery_coach/running.py`
- Test: `tests/test_running.py`

**Interfaces:**
- Consumes: `WorkoutStats`, `RunPoint` (Task 1), HR samples.
- Produces:
  - `is_run(kind) -> bool` (`running`); `hilly(stats) -> bool` (elev_up per km > 10 m)
  - `efficiency(series, hr, start, end) -> dict` → `{"ef": m/min per bpm, "dec": % | None, "reason"}`: drop the first 10 min, steady = ≥ 20 min left; halves by time; EF_i = mean speed (m/min) ÷ mean HR; `dec = 100·(EF1 − EF2)/EF1`; fallback to laps when no series; power EF when speed missing
  - `effective_vo2(dist_m, secs, avg_hr, hrmax) -> float | None` — Runalyze: `p = e^((avg_hr/hrmax − 1.00466)/0.68725)`, `v100 = (dist/secs·60)/p`, `−4.6 + 0.182258·v100 + 0.000104·v100²`; None outside 50–1000 m/min
  - `running_index(dist_m, secs, avg_hr, hrmax, rest) -> float | None` — Elevate: `rate = 213.9/t_min·d_km^1.06 + 3.5`, `intensity = min(1.45·avg_hr/hrmax − 0.3, 1)`, RI = rate/intensity; None unless HRR 52–98 %, > 12 min, > 6 km/h
  - `mechanics(stats) -> dict` → `{"vert_ratio": 100·vo_cm/(stride·100), "eff_index": speed/power, "muscle_kj": power·secs/1000}` (each None when inputs missing)
  - `power_hr_line(points: list[tuple[float, float]]) -> tuple[float, float] | None` — least squares power = a + b·HR over easy runs, ≥ 4 points
  - `rtss_flat(secs, avg_speed, threshold_speed) -> float` — `hours·(speed/thr)²·100` (approx)
  - `rss(secs, avg_power, cp) -> float` — `hours·(power/cp)²·100` (approx; constants unpublished, documented)
  - `best_efforts(series) -> dict[str, float]` — fastest time for 400 m, 1 km, 1 mile, 5 km, 10 km, half within the run, from cumulative distance, linear interpolation (approx)
  - `uth_vo2(hrmax, rest) -> float` — 15.3·hrmax/rest

- [ ] **Step 1: Write failing tests** `test_effective_vo2_worked_example` (10 km, 3000 s, 160, 190 → `round(x,1) == 48.3`), `test_decoupling_steady_vs_drift` (constant HR → |dec| < 0.5; HR +8 % in second half → dec ≈ 7.4), `test_efficiency_short_run_reason` (< 30 min → dec None, reason "under 20 steady minutes"), `test_running_index_validity_window`, `test_mechanics_none_when_missing`, `test_best_efforts_constant_pace` (10 km in 3000 s → 1 km ≈ 300 s, 5 km ≈ 1500 s), `test_power_hr_line_needs_four`, `test_zero_distance_returns_none` (dist 0 or missing → effective_vo2, running_index and best efforts are None; Review Focus 5).
- [ ] **Step 2:** Run → fail. **Step 3:** Implement with docstring sources (Runalyze, Elevate, TrainingPeaks EF/decoupling, HRV4Training filter, Polar Muscle Load, GoldenCheetah EI, Stryd RSS, Uth 2004). **Step 4:** Suite passes.
- [ ] **Step 5:** Commit `feat: running efficiency, fitness and mechanics`.

### Task 6: Race predictions, paces and critical speed

**Files:**
- Create: `recovery_coach/race.py`
- Test: `tests/test_race.py`

**Interfaces:**
- Produces:
  - `vo2_at(v_m_min) -> float` (`−4.6 + 0.182258·v + 0.000104·v²`); `pct_max(t_min) -> float` (`0.8 + 0.1894393·e^(−0.012778·t) + 0.2989558·e^(−0.1932605·t)`)
  - `vdot_from_race(dist_m, secs) -> float`
  - `shape(entries: list[LogEntry], as_of: date, days=30) -> float | None` — duration-weighted mean of `evo2` over outdoor runs
  - `marathon_shape(entries, as_of, vo2) -> float` — Runalyze BasicEndurance: week-km target `max(vo2,25)^1.135`, long-run target `ln(max(vo2,25)/4)·12 − 13`, windows 182 (min 70) / 70 days, long-run points `(2 − 2/70·days_ago)·((km − 13)/target)²`, `100·(0.67·weekkm% + 0.33·longrun%)`
  - `predict(dist_m, vo2, shape_pct) -> float` seconds — `required = km^1.23`, `factor = max(0, 1 − (required − shape)/100)`, `adj = min(1, 0.6 + 0.4·factor)`, bisection on `vo2_at(d/t)/pct_max(t) = vo2·adj` between 2 and 10 min/km
  - `predictions(vo2, shape_pct) -> dict` for 5 km, 10 km, 21.0975 km, 42.195 km; `riegel(t1, d1, d2, e=1.06)` shown for 5–10 km only
  - `paces(vdot) -> dict` — speed at vVDOT × 0.72 easy, 0.85 marathon, 0.90 threshold, 0.98 interval, 1.05 repetition (s/km)
  - `critical_speed(efforts: list[tuple[float, float]]) -> tuple[float, float] | None` — least squares `dist = CS·t + D′` over efforts of 120–900 s, needs ≥ 2 distinct durations (approx)

- [ ] **Step 1: Write failing tests** `test_vdot_round_trip` (predict(10 km, vdot_from_race(10000, 3000), 1000) within 1 s of 3000), `test_marathon_penalty_lowers_prediction` (shape 30 vs 300 → longer marathon time), `test_paces_order` (easy > marathon > threshold > interval > repetition, in s/km), `test_critical_speed_two_efforts`, `test_shape_uses_outdoor_only`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement (docstring: Daniels & Gilbert, Runalyze prognosis/BasicEndurance, Riegel + Vickers 2016 caveat, Jones & Vanhatalo CS). **Step 4:** Suite passes.
- [ ] **Step 5:** Commit `feat: race predictions, training paces and critical speed`.

### Task 7: Daily and weekly training figures

**Files:**
- Create: `recovery_coach/training.py`
- Modify: `recovery_coach/inputs.py` (`DayRow` gains `wload, srpe, azm, ri_h, mono, tstrain, form_pct, ramp, pi7`; `ROW_DECIMALS` entries 1 dp, `mono` 2, `pi7` 2), `recovery_coach/state.py` (`STATE_FIELDS["wload"] = (1, 29)`, `STATE_FIELDS["srpe"] = (0, 29)`)
- Test: `tests/test_training.py`

**Interfaces:**
- Consumes: `DayRow` log, `LogEntry` log, existing `load` module.
- Produces:
  - `monotony(loads7: list[float]) -> tuple[float, float, str|None]` — (monotony, strain, "warn"|"alert"|None); population SD, rest days 0, cap 10
  - `form_pct(ctl, atl)`, `ramp(rows, d)` (CTL − CTL 7 days earlier), `easy_cap(ctl, atl, k) -> float` strain value of `(41·ctl − 36·atl)/5` through `strain(trimp, k)` (≤ 0 → 0)
  - `srpe_ratio(rows, d) -> float | None` — 7-day sum ÷ (28-day sum ÷ 4), needs 28 days
  - `polar_band(ratio) -> str`, `garmin_band(ratio) -> str`
  - `split_window(entries, d, days) -> list[float]` and `polarization(split) -> float | None` — `log10((low/mid)·high·100)` on fractions; None when high = 0 or high > low or mid = 0
  - `focus_shortage(entries, d) -> list[str]` — over 28 days flag "low aerobic" if low < 60 %, "high aerobic" if mid < 10 %, "anaerobic" if high < 3 % (my thresholds, approx)
  - `weekly_target(rows, d) -> tuple[float, float]` — mean weekly `wload` of the previous 4 weeks × (0.8, 1.3)
  - `running_tolerance(entries, d) -> float | None` — max weekly outdoor+indoor run km of the last 4 weeks × 1.15 (approx)
  - `session_level(entry, entries) -> str|None` — Polar bands vs mean `edwards` of same-kind sessions in 90 days (≥ 3 sessions)
  - `increments(entry_edwards, ctl, atl) -> tuple[float, float]` — `((load − ctl)/42, (load − atl)/7)`
  - `typical_z(entry, entries, field) -> float | None` — vs last 10 same-kind
  - `performance_condition(entry, entries) -> int | None` — `clamp(round(100·(ef − median28)/median28·2), −20, 20)` for steady outdoor runs (approx)
  - `hrv_status(rows, d) -> str|None` — 7-day mean `hrv_ln` vs 60-day median ± MAD band: "balanced" inside, "unbalanced" above/below by < 2 MAD, "low" below by ≥ 2 MAD (my rule)
  - `status(acwr, vo2_trend, recovery_avg7) -> str` (approx): Polar band, then "unproductive" when productive/overreaching load and VO2 trend down; "strained" when ratio > 1.3 and 7-day recovery mean < 34

- [ ] **Step 1: Write failing tests** `test_monotony_rest_days_zero`, `test_monotony_flat_week_capped`, `test_easy_cap_matches_derivation` (load applied → TSB stays ≥ 0 to 1e-6), `test_polarization_worked_example` (0.80/0.05/0.15 → 2.38), `test_polarization_none_when_no_high`, `test_bands_edges` (0.8, 1.0, 1.3, 1.5, 2.0), `test_srpe_ratio_needs_28_days`, `test_session_level_needs_three`, `test_hrv_status_low`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement (docstring: Foster 1998/2001, Runalyze, Polar Training Load Pro white paper, Garmin manual, Treff 2019, TrainingPeaks ramp). **Step 4:** Suite passes.
- [ ] **Step 5:** Commit `feat: daily and weekly training figures`.

### Task 8: Recovery Index

**Files:**
- Modify: `recovery_coach/sleep.py`
- Test: `tests/test_sleep.py`

**Interfaces:**
- Produces: `recovery_index(night_hr, night: Night, workouts: list[Workout]) -> dict` → `{"hours": float|None, "nadir": ISO|None, "late_nadir": bool, "late_workout": bool}`: nadir = middle of the lowest 5-minute rolling mean inside onset–wake; hours = (wake − nadir); `late_nadir` = nadir after the night's midpoint; `late_workout` = a workout ended within 3 h before onset; hours None with < 30 readings.

- [ ] **Step 1: Write failing tests** `test_recovery_index_early_nadir` (≥ 6 h), `test_recovery_index_late_nadir_flag`, `test_recovery_index_late_workout_flag`, `test_recovery_index_too_few_readings`.
- [ ] **Step 2–4:** Fail, implement (docstring: Oura support article), pass.
- [ ] **Step 5:** Commit `feat(sleep): Oura-style recovery index`.

### Task 9: Score a workout; the `workout` command; `run` integration

**Files:**
- Create: `recovery_coach/session.py`
- Modify: `recovery_coach/pipeline.py`, `recovery_coach/cli.py`
- Test: `tests/test_session.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–8; `strain.dedupe_workouts`, `strain.workout_strain`, `strain.strain`.
- Produces:
  - `score_workout(w: Workout, stats: WorkoutStats|None, hr, series, settings, hrmax, rest, vo2max, k, log, rows, now) -> tuple[dict, LogEntry]`. The dict (schema documented in the module docstring) = `{id, kind, start, end, minutes, indoor, provisional, hr: {avg, max, zones: [5], split: [3], trace: [[sec, bpm]] (15-s means to end+180 s, ≤ 400 points)}, load: {strain, edwards, banister, hrss, intensity_pct, azm, effort, effort_src: "you"|"apple"|null, srpe, level, d_fitness, d_fatigue, z_typical}, effect: {...Task 4}, recovery: {...Task 3, apple: cardio_recovery, hrrc}, run: {dist, pace_s_km, ef, dec, evo2, ri, perf_cond, vert_ratio, eff_index, muscle_kj, rtss, rss, best, pace_trace: [[sec, s/km]] 30-s, splits: [laps]} | null, notes: [str]}`
  - `score_new(inp: Inputs, t: date, now: datetime, only_day: date|None) -> tuple[list[dict], list[LogEntry]]` — dedupes `inp.workouts`, skips ids already in the log unless `provisional`, applies `inp.efforts` to matching entries (new or logged; effort_src "you")
  - `cli`: `workout` command → `result.json` `{"workouts", "card", "state_next"}` and `workout.html`; prints `"<n> workout(s) scored; wrote result.json, workout.html"`; `card` = the latest workout dict plus `{"context": {"recovery_today", "strain_so_far", "recovered_pct"}}`
  - `run_day`: calls `score_new(..., only_day=y)` when wk files are present; `result["workouts"]` added; yesterday's patch gains `wload, srpe, azm, mono, tstrain, form_pct, ramp, pi7`; today's row gains `ri_h`; `card["strain"]["workouts"][i]` gains `te, rec_h, hrr60, benefit, id`; `card["training"]` = `{form_pct, ramp, easy_strain, monotony, mono_flag, tstrain, srpe_ratio, polar_band, garmin_band, split7, split28, pi7, pi28, focus_short, target_week, run_tol_km, status, hrv_status, recovery_index}`; `state_next` carries the merged log
- [ ] **Step 1: Write failing tests** `test_duplicate_logs_scored_once` (Review Focus 1), `test_provisional_rescored_next_run` (Review Focus 2), `test_first_workout_without_history` (context fields null with "no history yet"; Review Focus 4), `test_indoor_run_skips_pace_fitness` (evo2/best None, te present; Review Focus 5), `test_effort_answer_applied_to_logged_workout`, `test_run_without_wk_files_unchanged` (existing fixtures: result identical to before except new null keys), `test_cli_workout_command_writes_files`, `test_trace_point_cap`.
- [ ] **Step 2–4:** Fail, implement, full suite passes.
- [ ] **Step 5:** Commit `feat: workout command and per-workout scoring in the morning run`.

### Task 10: Weekly additions

**Files:**
- Modify: `recovery_coach/weekly.py`, `recovery_coach/cli.py` (weekly reads `state.workouts`)
- Test: `tests/test_weekly.py`

**Interfaces:**
- Produces: `result["card"]["training"] = {week_wload, monotony, tstrain, split, pi, focus_short, status, race: {shape, marathon_shape, vdot, predictions: {"5k","10k","half","marathon"}, riegel: {"5k","10k"}, paces, cs, d_prime}, uth_vo2}`; `card` docstring updated; Body Age untouched.
- [ ] **Step 1: Write failing tests** `test_weekly_training_section_present`, `test_weekly_race_none_without_outdoor_runs`, `test_weekly_body_age_unchanged` (existing fixture's body_age identical).
- [ ] **Step 2–4:** Fail, implement, pass. **Step 5:** Commit `feat(weekly): training and race section`.

### Task 11: Courier helpers

**Files:**
- Modify: `recovery_coach/helper.py`
- Test: `tests/test_helper.py`

**Interfaces:**
- Produces:
  - `localize` also rewrites `wk_hr.csv time`, `wk_series.csv time`, `night_hr.csv time`, and `wk_stats.json` `start`, `end`, `laps[].start/end`
  - `new-workouts STATE FOLDER` — reads the localized `workouts.csv`, dedupes, prints JSON `[{"id", "kind", "from", "to"}]` for workouts not in the state's log (or provisional), `from` = start, `to` = end + 3 min
  - `emit` accepts keys `workouts` and `card` for `workout` results; `emit FILE workout-doc ID` prints that workout's dashboard document (the dict minus nothing; ≤ 60 KB, else trace downsampled until it fits)
- [ ] **Step 1: Write failing tests** `test_new_workouts_skips_logged_and_dupes`, `test_new_workouts_includes_provisional`, `test_localize_wk_stats_json`, `test_emit_workout_doc_under_60kb`.
- [ ] **Step 2–4:** Fail, implement (docstring updated), pass. **Step 5:** Commit `feat(helper): new-workouts, workout docs, localize new files`.

### Task 12: Chat cards

**Files:**
- Modify: `recovery_coach/card.py`, `tests/render_cards.py`
- Create: `tests/fixtures/card_workout.json` (synthetic)
- Test: `tests/test_card.py`

**Interfaces:**
- Produces: `workout_card(result: dict) -> str` — the latest workout: header (kind, time, duration, "indoor"), a training-effect dial (aerobic arc + anaerobic inner arc, 0–5, benefit label, "approx"), recovery-hours countdown, heart-rate trace with zone-coloured bands and the 2-minute recovery tail, zone bar, heart-rate recovery stat (with Apple's value beside it), load row (strain, HRSS, effort, level, fitness/fatigue increments); runs add pace + EF + decoupling, VO2 from this run, best efforts, mechanics. Daily card: the strain section's workout rows show `TE x.x · yy h · HRR zz`; a compact "Training" strip (form %, monotony, ratio bands). Weekly card: a "Training" section (split bar + polarization, monotony, status, race predictions and paces).
- [ ] **Step 1: Write failing tests** `test_workout_card_size_limit` (< 14 KB), `test_workout_card_prints_every_number` (each value in the fixture appears), `test_workout_card_nulls_render_dash_with_reason`, `test_workout_card_host_rules` (no `<script`, no `gradient`, no `xmlns`), `test_daily_card_workout_line`, `test_weekly_card_training_section`.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Load the `anthropic-skills:frontend-design` skill, then implement within card.py's tokens and host rules (new tokens allowed: one per zone colour Z1–Z5 from the existing palette family).
- [ ] **Step 4:** Tests pass; `python3 tests/render_cards.py` screenshots the three cards; view them and fix anything cramped at 380 px.
- [ ] **Step 5:** Commit `feat(card): workout card, daily and weekly training blocks`.

### Task 13: Release the engine and update the skill

**Files:**
- Modify: `README.md` (what it computes: new rows; `workout` command), `pyproject.toml` (version 0.4.0)
- Skill: proposed edit of the owner's `recovery-coach` skill (via `propose_skills`, `kind: improvement`, `edits`)

**Interfaces:**
- Skill edits (exact sections): Constants (new commit; new paths `data/rc/workouts/<id>`, `data/rc/workout/latest`, `data/effort_pending`); description gains "workout, how was my run/workout, workout history"; new **Workout check**, **Workout history**, **Effort answers** sections; Every morning steps 2–8 extended (per-workout queries from `new-workouts`, overnight minute HR across the main sleep, saves of workout docs, reply strain line); Writing the input files table gains the five files with their queries (`heartRate` samples per window; `runningSpeed`, `runningPower`, `distanceWalkingRunning` samples; workouts with `"detail": "statistics"` and `"breakdown"` for runs; `workoutEffortScore`, `estimatedWorkoutEffortScore`, `heartRateRecoveryOneMinute` samples).
- [ ] **Step 1:** `python3 -m pytest -q -W error` passes; README updated; commit `release: v0.4.0 post-workout metrics`.
- [ ] **Step 2:** Ask the owner to approve the push; push only after a yes (`add_repo` with push access).
- [ ] **Step 3:** Propose the skill edit pinned to the pushed commit; the owner saves it.

### Task 14: Dashboard Workouts tab

**Files:**
- Dashboard store (artifact `4ed5f8eb-868f-4db7-b7d3-c6a317cbc128`): new datasets `datasets/workouts` (`{kind: "store", path: "data/rc/workouts"}`), `datasets/workout` (`path: "data/rc/workout"`); `files/index.html` gains the tab; rules updated to allow the new paths.

**Interfaces:**
- Consumes: workout docs (Task 9 schema), day rows (Task 7 fields), latest morning card `training`.
- Produces: a Workouts tab in the dashboard's existing look: hero of the latest workout (TE dial, recovery countdown, HR trace with zone bands and recovery tail, HRR), recent workouts list, trends (HRR by type, EF of easy runs, run VO2 vs Apple VO2, weekly split + polarization, monotony, load ratio with both bands, race predictions and paces). Every value marked (`data-source`, `data-field`), tooltips on charts, readable at ~400 px.
- [ ] **Step 1:** Load `anthropic-skills:frontend-design`; read the type's `design-defaults.md` and `one-writer.md` (describe_type); `list_files` and read the current `files/index.html` and rules.
- [ ] **Step 2:** Build the tab against synthetic docs in a private preview copy (a new dashboard artifact the owner can open), check `data/users/me/check` = `passed` and `runs` clean.
- [ ] **Step 3:** Show the owner the preview; on a yes, write the datasets, rules and `files/index.html` to the live dashboard and confirm `check` passes.

### Task 15: End-to-end with real data, and the history back-fill

- [ ] **Step 1:** From the phone: run the "workout" check on today's run; confirm the reply and the dashboard doc; compare HRR with Apple's Cardio Recovery and the TE/recovery hours with how he felt.
- [ ] **Step 2:** Next morning: confirm the morning reuses the saved workout (no second heart-rate query) and the training figures appear.
- [ ] **Step 3:** Run "workout history" for the last 30 days (runs and strength); confirm trends draw.
- [ ] **Step 4:** Record any constant he wants tuned as a follow-up.
