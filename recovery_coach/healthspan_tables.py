"""Body-age parameter tables, openbodyscore healthspan v1.2 (hs-v1.2-2026-10-23).

Ported verbatim (values, knots, anchors and citations; comments only trimmed where noted) from:
  Source repo : https://github.com/SongyuQi-Francisco/Watch2Whoop
  Commit      : 003882464e197d42fd4b3163bcc47219d6e1e40e
  Files       : packages/openbodyscore/src/openbodyscore/healthspan_params.py      (v1.1: everything not overridden)
                packages/openbodyscore/src/openbodyscore/healthspan_params_v12.py  (v1.2: activity domain, Ding 2025 step curve)
Licence     : Apache License 2.0 (packages/openbodyscore/LICENSE; Copyright 2026 Watch2Whoop contributors,
              see packages/openbodyscore/NOTICE). Licensed under the Apache License, Version 2.0; you may obtain a copy at
              http://www.apache.org/licenses/LICENSE-2.0. Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS
              OF ANY KIND.

Changes made when porting: the two modules are merged into one flat module (v1.1 values, with the v1.2 overrides of
ALGO_VERSION, STEPS_KNOTS_UNDER60 and STEPS_ANCHOR_UNDER60 applied in place of the v1.1 under-60 step curve); the
`override()` helper and the `_override` import are dropped; the v1.2 module docstring is kept as the comment block below.
Constants this engine does not use (WHTR, VO2_DECILES, SparkyFitness minute estimate, MVPA curve) are kept so the table stays
a faithful port. Values are unchanged.
"""

# ================================================================ v1.1 base (healthspan_params.py)

# ---------------------------------------------------------------- window and conversion
WINDOW_DAYS = 180
RECENT_DAYS, RECENT_WEIGHT = 30, 2.0      # day weight of the last 30 days x 2
MIN_DAYS = 30                             # at least 30 days per item (regularity = 30 pairs of adjacent nights)
# Gompertz: adult (after age 40) all-cause mortality roughly doubles every 8 years. Kirkwood TBL. Deciphering death: a commentary on Gompertz (1825).
# Philos Trans R Soc B 2015;370:20140379(doi:10.1098/rstb.2014.0379), citing Jones HB 1956 "doubling time consistently around 8 years".
MORTALITY_DOUBLING_YEARS = 8.0
ITEM_CAP_YEARS = 4.0
SHRINKAGE = 0.6                           # items are correlated (e.g. steps, exercise, VO₂max, resting heart rate), so the total is scaled to 60% to avoid double counting
TOTAL_CAP_YEARS = 10.0
# Pace of aging (decided 2026-10-17 by the maintainer): each week's Δ (body age - chronological age) is first smoothed with a 4-week moving average, then least-squares fitted over the last 26 weeks,
# pace = 1 + dΔ/dt (years / year). Before 26 weeks exist, all available weeks are used, and at least 8 weeks are needed. v1.0 used 8 weeks without smoothing, and the owner's value was pinned at -1.
PACE_WEEKS, PACE_MIN_WEEKS, PACE_SMOOTH_WEEKS = 26, 8, 4
PACE_RANGE = (-1.0, 3.0)

# ---------------------------------------------------------------- sleep duration
# Yin J et al. J Am Heart Assoc 2017;6:e005947(doi:10.1161/JAHA.117.005947) dose-response meta-analysis: nadir around 7h,
# below 7h each 1h less gives RR 1.06 (1.04–1.07), above 7h each 1h more gives RR 1.13 (1.11–1.15). The U shape is also seen in Cappuccio 2010 Sleep 33:585
# (short sleep RR 1.12, long sleep 1.30) and Shen 2016 Sci Rep 6:21480 (nadir 7h).
SLEEP_NADIR_H = 7.0                       # anchor = nadir (self-reported 7h is also the most common duration in the population)
SLEEP_RR_PER_H_SHORT, SLEEP_RR_PER_H_LONG = 1.06, 1.13
# Durations in the literature are self-reported in questionnaires, while the watch measures actual time asleep. Lauderdale DS et al. Epidemiology 2008;19:838 (doi:10.1097/EDE.0b013e318187a7b0):
# self-report averages 0.8h more than actigraphy and correlates with the measured value: each extra 1h measured gives 34 more minutes self-reported; measured 5h -> self-report +1.2h, 7h -> +0.4h.
# Conversion: self-report ≈ 3.365 + 0.567 × measured (the line through those two points).
SLEEP_SELF_REPORT = (3.365, 0.567)

# ---------------------------------------------------------------- sleep regularity (SRI)
# Windred DP et al. Sleep 2024;47:zsad253 (doi:10.1093/sleep/zsad253), UK Biobank, 60,000 people, about 1 week of accelerometry:
# SRI quintile medians 65.1 / 75.6 / 81.0 / 85.2 / 89.8, all-cause mortality HR (vs the most irregular Q1, full model) 1 / 0.80 / 0.75 / 0.72 / 0.70.
SRI_KNOTS = [(65.1, 1.00), (75.6, 0.80), (81.0, 0.75), (85.2, 0.72), (89.8, 0.70)]
SRI_ANCHOR = 81.0                         # anchor = cohort median (Q3)

# ---------------------------------------------------------------- steps
# Paluch AE et al. Lancet Public Health 2022;7:e219 (doi:10.1016/S2468-2667(21)00302-9), 15 cohorts: quartile medians and HR (model 2, reference Q1).
# >= 60 years (v1.1 and v1.2 both): 2841 / 5217 / 7116 / 10501 -> 1 / 0.62 / 0.52 / 0.43 (plateau at 6000-8000).
# (The < 60 curve of v1.1 is superseded by Ding 2025 below, see the v1.2 block.)
STEPS_KNOTS_60PLUS = [(2841, 1.00), (5217, 0.62), (7116, 0.52), (10501, 0.43)]
STEPS_ANCHOR_60PLUS = (5217 + 7116) / 2   # ≈ 6167

# ---------------------------------------------------------------- moderate-to-vigorous exercise (moderate-equivalent minutes / week)
# Lee DH et al. Circulation 2022;146:523 (doi:10.1161/CIRCULATIONAHA.121.058162), NHS / HPFS, 116,000 people:
# moderate intensity (reference 0–19 min / week) 20–74: 0.91, 75–149: 0.84, 150–224: 0.80, 225–299: 0.79, 300–374: 0.74, 375–449: 0.74,
# 450–599: 0.69, ≥ 600: 0.68 (bracket midpoints; ≥ 600 taken as 700). 1 vigorous minute counts as 2 moderate minutes (the guideline equivalence; Lee's vigorous curve is roughly
# the moderate curve at half the minutes: vigorous 150–224 = 0.79 ≈ moderate 300–374 = 0.74-0.79).
MVPA_KNOTS = [(10, 1.00), (47, 0.91), (112, 0.84), (187, 0.80), (262, 0.79), (337, 0.74), (412, 0.74), (525, 0.69),
              (700, 0.68)]
MVPA_ANCHOR = 150                         # anchor = guideline minimum of 150 min / week
VIGOROUS_FACTOR = 2.0
# Our minutes = minutes of all-day heart rate in Z2–Z3 (60–80% HRmax) + 2 × (Z4–Z5, ≥ 80%) (daily_scores.details.load.zone_minutes),
# not only the workout windows; the questionnaire covers leisure exercise, so the definitions do not match exactly.

# ---------------------------------------------------------------- strength training (minutes / week)
# Momma H et al. Br J Sports Med 2022;56:755 (doi:10.1136/bjsports-2021-105061) meta-analysis: J-shaped, lowest RR 0.83 (0.79–0.86) at 40 min / week,
# RR stays < 1 up to about 140 min / week, the effect of more is unclear (not penalized, treated as 140). The paper gives no point estimate at 140; 0.92 is used (read off the curve plot, flagged as inferred).
STRENGTH_KNOTS = [(0, 1.00), (40, 0.83), (140, 0.92)]
STRENGTH_ANCHOR = 0                       # anchor = no training (about 70% of adults do not reach 2 strength sessions a week)
STRENGTH_MIN_PER_SET = 3.0                # when SparkyFitness has no duration: estimate 3 minutes per working set (about 45 s of work + 2 min rest)

# ---------------------------------------------------------------- VO₂max
# Kodama S et al. JAMA 2009;301:2024 (doi:10.1001/jama.2009.681): each 1 MET (3.5 mL/kg/min) higher cardiorespiratory fitness gives all-cause mortality RR 0.87 (0.84–0.90).
# Mandsager 2018 JAMA Netw Open 1:e183605: the elite group has the lowest risk with no ceiling effect (low vs elite HR 5.04).
VO2_RR_PER_MET = 0.87
VO2_MAX_DELTA_MET = 3.0                   # gap to the norm counted at most ±3 MET (Kodama's linear relation holds in this range)
VO2_MIN_READINGS = 2                      # VO₂max readings are sporadic (estimated only on outdoor walks / runs), so the "30 days" rule does not apply: >= 2 readings in 180 days,
VO2_MAX_AGE_DAYS = 120                    # and the latest within 120 days
# Anchor = same-age same-sex median: FRIEND 2022 (Kaminsky LA et al. Mayo Clin Proc 2022;97:285, doi:10.1016/j.mayocp.2021.08.020)
# treadmill VO₂peak 50th percentile; placed at the midpoint of each age band (20–29 -> age 25), linearly interpolated between. The Apple Watch estimate runs a few units low, with no correction.
VO2_MEDIAN = {
    "male": {25: 46.5, 35: 39.7, 45: 35.3, 55: 29.2, 65: 24.6, 75: 20.6, 85: 17.6},
    "female": {25: 36.6, 35: 28.3, 45: 25.7, 55: 22.9, 65: 19.6, 75: 17.2, 85: 15.4},
}
# Deciles used for the fitness level (fitness.py) (same table): P10 … P90
VO2_DECILES = {
    "male": {25: [28.6, 35.2, 40.0, 43.6, 46.5, 49.0, 51.9, 54.5, 58.6],
             35: [24.9, 29.8, 33.5, 37.0, 39.7, 43.4, 46.4, 50.0, 55.5],
             45: [22.1, 26.7, 29.7, 32.4, 35.3, 37.9, 40.9, 45.2, 50.8],
             55: [18.6, 22.2, 24.5, 26.9, 29.2, 31.8, 34.3, 38.3, 43.4],
             65: [15.8, 18.5, 20.7, 22.8, 24.6, 26.5, 28.7, 32.0, 37.1],
             75: [13.6, 15.9, 17.3, 19.1, 20.6, 22.2, 23.8, 25.9, 29.4],
             85: [12.9, 14.8, 16.1, 16.6, 17.6, 18.4, 20.0, 21.4, 22.8]},
    "female": {25: [22.5, 27.2, 30.8, 34.0, 36.6, 39.0, 41.8, 44.8, 49.0],
               35: [18.6, 21.9, 24.2, 26.4, 28.3, 31.0, 33.6, 37.0, 42.1],
               45: [17.2, 19.7, 21.8, 23.9, 25.7, 27.7, 30.0, 33.0, 37.8],
               55: [16.5, 18.5, 20.1, 21.5, 22.9, 24.6, 26.3, 28.4, 32.4],
               65: [13.4, 15.4, 17.0, 18.3, 19.6, 20.9, 22.4, 24.3, 27.3],
               75: [12.3, 14.0, 15.2, 16.2, 17.2, 18.3, 19.6, 20.8, 22.8],
               85: [11.4, 12.6, 13.7, 14.7, 15.4, 16.0, 17.3, 18.4, 20.8]},
}

# ---------------------------------------------------------------- resting heart rate
# Jensen MT et al. Heart 2013;99:882 (doi:10.1136/heartjnl-2012-303375), Copenhagen Male Study: HR 1.16 (1.10–1.22) per 10 bpm higher,
# adjusted for VO₂max (so it overlaps less with the VO₂max item; chosen over Zhang 2016 CMAJ's 1.09).
# Shape: Zhang D et al. CMAJ 2016;188:E53 meta-analysis, all-cause mortality rises linearly from 45 bpm with no threshold -> flat below 45.
RHR_RR_PER_10 = 1.16
RHR_FLOOR = 45.0
RHR_ANCHOR = 65.0                         # anchor = average resting heart rate of wearable users, about 65 (Quer G et al. PLoS One 2020;15:e0227709, 90,000 Fitbit users)

# ---------------------------------------------------------------- body composition
# BMI: Global BMI Mortality Collaboration. Lancet 2016;388:776 (doi:10.1016/S0140-6736(16)30175-1), never-smokers without chronic disease:
# 15–18.5: 1.51, 18.5–20: 1.13, 20–22.5: 1.00, 22.5–25: reference, 25–27.5: 1.07, 27.5–30: 1.20, 30–35: 1.45, 35–40: 1.94, 40–60: 2.76 (bracket midpoints).
BMI_KNOTS = [(17.0, 1.51), (19.25, 1.13), (21.25, 1.00), (23.75, 1.00), (26.25, 1.07), (28.75, 1.20), (32.5, 1.45),
             (37.5, 1.94), (45.0, 2.76)]
BMI_ANCHOR = 25.0                         # anchor = healthy / overweight boundary (upper edge of the reference bracket)
# Waist-to-height ratio: Jayedi A et al. BMJ 2020;370:m3324 (doi:10.1136/bmj.m3324): HR 1.24 (1.12–1.36) per 0.1 higher, J-shaped, nadir 0.50.
WHTR_KNOTS = [(0.50, 1.00), (0.60, 1.24), (0.70, 1.24 ** 2), (0.80, 1.24 ** 3)]
WHTR_ANCHOR = 0.50
WEIGHT_MAX_AGE_DAYS = 90                  # weight / waist are sporadic measurements: one within the last 90 days counts (the "30 days" rule does not apply)


# ================================================================ v1.2 overrides (healthspan_params_v12.py)
#
# Body-age parameters, version 1.2: steps, exercise and strength training become one "activity" domain (issue #118).
# Everything not set here is inherited unchanged from healthspan_params (v1.1): sleep, regularity, VO₂max, resting heart
# rate, body composition, the window, the 8-year doubling time, × 0.6 and the ±10-year total cap.
# Sources checked against the full texts / tables on 2026-10-23. Labels as in docs/scoring-methodology.md 0.1:
# [literature] / [design choice] / [inferred] (a number derived from a paper, not printed in it).
#
# Why (v1.1 → v1.2):
# 1. v1.1 scored steps, MVPA and strength as three items, each capped at ±4 years, and relied on × 0.6 to undo the overlap.
#    Lee 2022 counts walking and weightlifting inside its MVPA, and steps are mostly walking, so the same activity was counted
#    up to three times. v1.2 scores one hazard ratio for the domain and caps it once.
# 2. The under-60 step curve (Paluch 2022 quartiles) falls from HR 1.00 to 0.59 between 4849 and 7245 steps — the least
#    precise segment (Q2 CI 0.39–0.88) and the one most open to reverse causation (people who walk little include the ill).
#    The larger 2025 meta-analysis is almost flat between 5000 and 10000 steps; v1.2 uses it for adults under 60.
# 3. Strength training, swimming, cycling and rowing give the wrist few steps; v1.2 converts the non-walking aerobic ones to
#    step equivalents and keeps strength on its own curve.

ALGO_VERSION = "hs-v1.2-2026-10-23"   # v1.2: activity domain (steps + non-step exercise + strength, one cap)
ACTIVITY_MODEL = "domain"              # healthspan_store: "items" (v1.1, default when absent) or "domain"

# ---------------------------------------------------------------- domain cap [design choice]
# One cap for the whole domain = what one item could reach in v1.1, so activity can no longer add up to ±12 years before × 0.6.
ACTIVITY_CAP_YEARS = 4.0

# ---------------------------------------------------------------- steps, adults under 60 [literature]
# Ding D et al. Daily steps and health outcomes in adults: a systematic review and dose-response meta-analysis.
# Lancet Public Health 2025;10:e668–e681 (doi:10.1016/S2468-2667(25)00164-1), Table 2, all-cause mortality (14 studies,
# 161,176 people), HR vs 2000 steps/day: 3000 0.77, 4000 0.64, 5000 0.57, 6000 0.54, 7000 0.53 (0.46–0.60), 8000 0.52,
# 9000 0.52, 10000 0.52 (0.45–0.59), 11000 0.49, 12000 0.45 (0.39–0.53). Inflection ~5400 steps (also in the younger
# subgroup); the curve was "very similar" when studies without adequate confounder adjustment were excluded.
# Flat beyond the table's ends (no extrapolation), as for every curve here.
# Reverse causation: Hansen BH et al. Scand J Med Sci Sports 2020;30:1705 (doi:10.1111/sms.13726), waist accelerometer,
# quartiles 4651 / 6862 / 8670 / 11467 steps: HR 0.45 / 0.49 / 0.42 vs Q1, 0.52 / 0.50 / 0.43 after excluding the first
# 2 years — the lowest segment shrinks most. Lee IM et al. JAMA Intern Med 2019;179:1105 (doi:10.1001/jamainternmed.2019.0899):
# Q4 0.42 → 0.50 after excluding year 1 and baseline CVD / cancer / diabetes. Ding's flatter curve already sits where
# these corrected estimates point; the ±20% slope sensitivity covers the rest.
STEPS_KNOTS_UNDER60 = [(2000, 1.00), (3000, 0.77), (4000, 0.64), (5000, 0.57), (6000, 0.54), (7000, 0.53), (8000, 0.52),
                       (9000, 0.52), (10000, 0.52), (11000, 0.49), (12000, 0.45)]
# Anchor = median of the under-60 cohorts in Paluch 2022 (text: 7803, IQR 5377–10352) [literature]; v1.1 used 8078, the
# midpoint of the Q2 / Q3 medians.
STEPS_ANCHOR_UNDER60 = 7803
# Adults 60+: Paluch 2022's 60+ curve and anchor stay as in v1.1 (STEPS_KNOTS_60PLUS, STEPS_ANCHOR_60PLUS). Ding reports a
# linear curve in its older subgroup (HRs only in the appendix, not checked), so the 60+ curve is not flattened.

# ---------------------------------------------------------------- wrist steps [literature, no correction]
# The step literature uses waist / hip devices; the Apple Watch counts at the wrist. A raw ActiGraph moved from waist to wrist
# counts ~2500 more steps/day (Tudor-Locke C et al. Med Sci Sports Exerc 2015;47:839, doi:10.1249/MSS.0000000000000476),
# but that is one device's raw signal, not the Apple Watch's algorithm. Apple Watch vs hip / waist research devices in free
# living: about 4–5% fewer over 72 h (Breteler MJM et al. JMIR Form Res 2019, doi:10.2196/11489, n = 30);
# medians 7060 vs 7064 steps/day (Spartano NL et al. JMIR Biomed Eng 2024, doi:10.2196/54631, n = 523), with
# wide day-to-day limits of agreement. No consistent bias → factor 1.0 (the sensitivity analysis varies it ±20%).
WRIST_STEPS_FACTOR = 1.0

# ---------------------------------------------------------------- exercise without steps → step equivalents
# Minutes × the steps a walk / run of the same MET takes. CADENCE-Adults (Tudor-Locke C et al. Int J Behav Nutr Phys Act
# 2019;16:8, doi:10.1186/s12966-019-0769-6, ages 21–40; 2020;17:137, doi:10.1186/s12966-020-01045-z, ages 41–60):
# 100 steps/min ≈ 3 MET (moderate), 130 steps/min ≈ 6 MET (vigorous) [literature]; linear in between and flat above 6 MET
# [design choice — no extra credit for intensity beyond the step count, which is what Lee 2019 and Saint-Maurice 2020 found
# for cadence once steps are adjusted for]. Below 3 MET (yoga, pilates, stretching) = light activity, 0.
# Tudor-Locke C et al. How many steps/day are enough? for adults. IJBNPA 2011;8:79 (doi:10.1186/1479-5868-8-79): 30 min of
# MVPA ≈ 3000–4000 steps, the same 100 steps / moderate minute.
MODERATE_MET, VIGOROUS_MET = 3.0, 6.0
CADENCE_MET_KNOTS = [(3.0, 100.0), (6.0, 130.0)]   # (MET, steps per minute)
# MET per workout type: 2024 Adult Compendium of Physical Activities (Herrmann SD et al. J Sport Health Sci 2024;13:6,
# doi:10.1016/j.jshs.2023.10.010; pacompendium.com codes in brackets), the general / moderate entry of each [literature]:
# swimming laps freestyle, slow, recreational 5.8 [18240]; bicycling, general 7.0 [01014]; stationary bicycling, general
# 6.8 [01200]; rowing ergometer, < 100 W, moderate 5.0 [02071].
WORKOUT_MET = {"swimming": 5.8, "pool_swimming": 5.8, "open_water_swimming": 5.8, "cycling": 7.0, "indoor_cycling": 6.8,
               "rowing_machine": 5.0}
# Only these types are converted [design choice]: the wrist barely counts steps for them. Walking, running, hiking, stairs,
# basketball, tennis, football, dance, jump rope, elliptical (arms move with the handles) already show up as steps; adding
# them would count them twice. Unrecorded exercise is not converted (record a workout).
NONSTEP_TYPES = frozenset(WORKOUT_MET)

# ---------------------------------------------------------------- strength training inside the domain
# Momma 2022's curve (STRENGTH_KNOTS, unchanged from v1.1) multiplies the volume hazard ratio. Its included studies all
# considered aerobic or other activity, so the muscle-strengthening association is the part beyond aerobic activity
# [literature; GRADE "very low", heterogeneous]; combined with aerobic activity it was RR 0.60 (0.54–0.67) vs neither.
# Step studies are not adjusted for strength training, so multiplying the two is an approximation [design choice].
# Strength minutes are NOT also converted to step equivalents [design choice]: Momma's association already includes what the
# sessions themselves burn, so doing both would count strength twice. Lee 2022 instead puts weightlifting inside moderate
# activity; True gives that alternative (shown in the comparison report, not the default).
STRENGTH_IN_VOLUME = False
STRENGTH_MET = 3.5   # resistance (weight) training, multiple exercises, 8–15 reps at varied resistance [02054], 2024 Compendium

# ---------------------------------------------------------------- MVPA (reference only, not counted)
# The heart-rate MVPA of v1.1 (Z2–Z3 + 2 × Z4–Z5, ≥ 60% HRmax) reads low for strength training: at a given %HRmax
# resistance exercise uses less oxygen than running (Collins MA et al. Med Sci Sports Exerc 1991;23:636,
# doi:10.1249/00005768-199105000-00018), and much of a session (rests) sits below 60% HRmax. Questionnaires such as Lee 2022
# class activities by absolute MET (weightlifting = moderate, < 6 MET). The reference part therefore counts strength and
# non-step workouts at their MET (moderate × 1, ≥ 6 MET × 2) and everything else by heart rate. Apple's exercise minutes are
# not used: their ring starts at about 30% VO₂ reserve, below the 40% moderate threshold (Abt G et al. JMIR Cardio
# 2018;2:e6, doi:10.2196/cardio.8574), so they overestimate.
