# Health metrics: what MyCoach tracks and how it reads them

Status: research basis for #191 (Nov 2026). This is the reference the daily briefing email and the coaching prompt are built from. When a prompt rule or email block changes, check it against this doc, and update the doc if the evidence moved.

How this was produced: four parallel research passes (Huberman Lab core metrics, daily steps, HRV, weight and waist), then a grilling session that turned the findings into decisions. Paywalls or captchas blocked some full texts, so some figures come from abstracts or secondary summaries. Those are marked *(unverified)*. Every number is observational and population-level unless stated otherwise: "associated with", never "causes".

## 1. Ranked metrics for a self-tracking coach

Ranked by strength of the outcome evidence × whether a Garmin watch (or a tape measure) can measure it well enough.

| # | Metric | Source | Why | In MyCoach today |
|---|---|---|---|---|
| 1 | VO2max trend | Garmin estimate | Strongest modifiable mortality predictor. Low vs elite fitness: adjusted HR 5.04 (Mandsager 2018) | Stored, in prompt; not trended |
| 2 | Weekly cardio volume / HR zones | Garmin | The lever that moves VO2max. Huberman: ≥150–200 min/wk Zone 2 + 1 hard session | Partly (training load) |
| 3 | Resting HR, 7-day avg | Garmin | RR 1.09 per +10 bpm (Zhang 2016 CMAJ). Reliable on the wrist | Stored, in prompt (3 days) |
| 4 | Waist / waist-to-height | Manual | Best self-measured visceral-fat proxy. HR ~1.11 per +10 cm independent of BMI (Jayedi 2020 BMJ) | **#191** |
| 5 | Sleep regularity (bed/wake times) | Garmin | Predicted mortality better than duration (Windred 2024 *Sleep*) | No |
| 6 | Daily steps | Garmin | Solid dose-response, plateau ~8–10k under 60 | **#191** |
| 7 | Strength sessions + grip/hang tests | Manual | Grip −5 kg → HR 1.16 (Leong 2015 *Lancet*) | Workouts only |
| 8 | Sleep duration | Garmin | U-shaped risk (Cappuccio 2010). Garmin overestimates; stages unreliable | Stored, in prompt |
| 9 | HRV vs personal baseline | Garmin | Good daily readiness signal; weak long-term evidence for wearables | **#191** (baseline) |
| 10 | Morning outdoor light | Manual habit | Plausible circadian benefit; minute targets are extrapolated | No |
| 11 | Weight | Manual | Useful only next to waist: can't tell fat from muscle | **#191** |
| 12 | Body Battery / Stress | Garmin | Proprietary, no outcome validation. Context only | Stored, in prompt |

Takeaway: of the four metrics #191 adds, waist and steps rank high, HRV earns its place as the daily readiness signal, and weight is a supporting signal for waist. The biggest gaps after #191 are VO2max trend (#200), RHR trend (#201), sleep regularity (#202) and weekly Zone 2 volume (#203). Strength benchmarks are #207.

## 2. Daily steps

### Evidence

| Study | Finding |
|---|---|
| Paluch 2022, *Lancet Public Health* ([doi](https://doi.org/10.1016/S2468-2667(21)00302-9)), 15 cohorts, n=47,471 | Risk levels off at **8–10k/day under 60** and **6–8k at 60+**. vs ~3.5k: HR 0.60 at ~5.8k, 0.55 at ~7.8k, 0.47 at ~10.9k |
| Saint-Maurice 2020, *JAMA* ([PubMed](https://pubmed.ncbi.nlm.nih.gov/32207799/)) | vs 4k: HR 0.49 at 8k, 0.35 at 12k. Cadence not significant after adjusting for volume |
| Lee 2019, *JAMA Intern Med*, older women | Risk levels off at ~7.5k |
| Banach 2023, *Eur J Prev Cardiol* ([doi](https://doi.org/10.1093/eurjpc/zwad229)), n=226,889 | Benefit from ~4k/day; each +1,000 steps ≈ 15% lower all-cause mortality |
| Ding 2025, *Lancet Public Health* ([doi](https://doi.org/10.1016/S2468-2667(25)00164-1)), 57 studies | Curves bend at 5–7k. 7k vs 2k: ~47% lower mortality; gains above 7k are modest |
| del Pozo Cruz 2022, *JAMA Intern Med* ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC9468953)), wrist device | Benefit up to ~10k. Peak-30 cadence adds a modest independent signal |
| Inoue 2023, *JAMA Netw Open* ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10051082)) | Reaching 8k on just 1–2 days/week captured most of the benefit. **Weekly volume matters more than daily regularity**, so a 7-day average is a valid summary |

- **Accuracy:** expect about ±10% from wrist devices in free living (Fuller 2020 JMIR review; Garmin validation studies). Slow gait and pushing a cart undercount; arm movement overcounts. Trust trends, not single values.
- **Huberman / Attia:** neither sets a step target. Steps are a baseline, not a training programme; extra effort beyond the plateau is better spent on Zone 2, VO2max work and strength.
- **Coaching:** adaptive and self-chosen goals lift steps more than a fixed 10k (Adams 2013; Patel 2019 STEP UP). In this case the user already averages around 10.9k, so a fixed target that reads as "maintain" is the honest framing.

### Decisions (#191)

- **Target:** a fixed setting, default **10,000**. A 7-day average at or above target means "maintain" and the coach redirects extra effort to Zone 2 and strength, not more steps.
- **Email:** a 7-day strip ending yesterday, drawn with HTML table-cell bars (Gmail strips SVG), with a ✓ on days at goal and yesterday's count as the headline. Chips: 7-day average, change vs the previous 7 days (blue when within ±10%, the device error), and days at goal out of 7. Inspired by the Google Health weekly report in #167.
- **"Yesterday"** is computed in `settings.timezone`, not server-local time.

### Coach rules

- Ignore single low days.
- Comment only if the 7-day average drops >15% below the previous 7 days, or there are ≥3 consecutive days below 50% of the average. In either case, ask about the cause (illness, injury, travel, schedule) before prescribing anything.
- Treat changes within ±10% as noise.
- A weekend-heavy pattern that still meets weekly volume is fine.

## 3. HRV

### What Garmin reports

- **Measurement:** overnight RMSSD in 5-minute readings, averaged per night (`lastNightAvg`).
- **HRV Status** compares the **7-day average** (`weeklyAvg`) to a personal baseline band (`baseline.balancedLow/balancedUpper`; `baseline.lowUpper` is the "Low" threshold). The band needs about 3 weeks of wear.
- **Statuses:** Balanced / Unbalanced (above *or* below the band) / Low / Poor / No status. "Unbalanced-high" is not automatically good; it can follow heavy training or illness.
- **Sources:** [Garmin manual](https://www8.garmin.com/manuals/webhelp/GUID-4462687B-8DF5-4719-872E-E16754F9D4B0/EN-US/GUID-5247CEC7-AF06-44FB-B6AD-87C53D4F7941.html), [garth](https://github.com/matin/garth).

### Evidence

- **Use rolling averages, not single nights:** Plews 2012, Buchheit 2014 ([doi](https://doi.org/10.3389/fphys.2014.00073)) and the [AIS guide](https://www.ais.gov.au/__data/assets/pdf_file/0013/1130260/HRV-BPG_FINAL.pdf) all use a 7-day rolling lnRMSSD against a smallest worthwhile change of about 0.5 × baseline SD *(convention, unverified from full text)*. Single nights are too noisy.
- **HRV-guided training:**
  - Kiviniemi 2007 and Vesterinen 2016 (3000 m time +2.1% vs +1.1%) favour it.
  - Javaloyes 2019 found no significant group difference.
  - Meta-analyses (Manresa-Rocamora 2021, Düking 2021) find small, non-significant performance effects but **fewer non-responders**.
  - Net: it mainly helps avoid bad days rather than boost performance.
- **Long-term health:**
  - Low HRV is associated with first-CVD-event risk (Hillebrand 2013 *Europace*, RR ~1.35), but that evidence comes from short clinical ECGs, not wearables.
  - Alcohol lowers overnight HRV dose-dependently (Pietilä 2018).
  - Fitness raises HRV; age lowers it.
  - Healthy people range from about 15 to over 200 ms, so **never compare between people**.
- **Accuracy:** Garmin Fenix 6 vs ECG overnight: CCC 0.87, MAPE ~10.5% ([PMC12367097](https://pmc.ncbi.nlm.nih.gov/articles/PMC12367097)). Good enough for within-person trends.
- **Huberman / Attia:** use HRV as a recovery signal measured against yourself. Attia treats a sustained downward trend as the warning sign.

### Decisions (#191)

- **New snapshot columns** `hrv_baseline_low`, `hrv_baseline_high` and `hrv_baseline_low_upper`, backfilled from stored `raw_data`. (`hrv_status` actually holds last night's ms; renaming it is #205.)
- **Email line:** `HRV 52 ms 7-day avg · your range 47–58 · Balanced · last night 48 ms`. Before Garmin has a band it shows "Building baseline".

### Coach rules

1. The primary signal is the 7-day average vs the band. Never prescribe from one night.
2. Treat changes under about 10% as noise.
3. A drop counts only when it agrees with RHR ≥ +5 bpm over baseline or with poor sleep.
4. A drop under high training load is expected fatigue. A drop without load suggests illness, stress or alcohol: ask, don't prescribe.
5. **Low** status, or below `lowUpper` for 3+ days, means a recovery block; ask about illness.
6. Unbalanced-high together with elevated RHR or a recent heavy block means possible overreaching.
7. No baseline yet, or missing data: say so and don't infer.

## 4. Weight and waist

### Evidence

- **Waist matters beyond BMI:** Ross 2020 IAS/ICCR consensus, *Nat Rev Endocrinol* ([PubMed](https://pubmed.ncbi.nlm.nih.gov/32020062/)), treats waist as a vital sign that adds risk information at every BMI. Waist reduction is a treatment target even when weight barely moves.
- **Waist thresholds:**
  - Male waist: >94 cm increased risk, >102 cm substantially increased (WHO 2008).
  - **Waist-to-height:** NICE 2022 bands are 0.40–0.49 healthy, 0.50–0.59 increased, ≥0.60 high. "Keep your waist under half your height."
  - Ashwell 2012 meta-analysis: waist-to-height beats waist and BMI for discriminating risk *(note: author conflict of interest)*.
- **Measuring the waist:**
  - The site doesn't change predictive value (Ross 2008), but it must be **the same every time**. The navel is fine for self-measurement.
  - End of a normal exhale, morning, fasted, 2 readings averaged.
  - Self-measurement error is 1–2 cm, so **≥2–3 cm sustained is real; ≤1 cm is noise**.
- **Weight:**
  - Frequent self-weighing helps weight management with no psychological harm (Zheng 2015; Steinberg 2015).
  - Day-to-day noise is 1–2 kg (water, glycogen, sodium). Weekends run about 0.35% higher (Turicchi 2020).
  - Smooth with an exponential moving average (Hacker's Diet alpha 0.1; MacroFactor-style time-aware).
  - Fat loss that preserves muscle: **0.5–1% of bodyweight per week** (Helms 2014).
- **Huberman / Attia:** Attia prefers DEXA and lean-mass indices and treats scale weight as a weak signal. No substantive Huberman guidance on weight or waist was found, so **don't attribute any to him**.

### Decisions (#191)

- **MyCoach owns body data.** A Body page form collects date/time, weight in kg and/or waist in cm, and an optional note, with a protocol hint under the inputs. Height is entered once on the Athlete record. The page lists the last 10 entries with delete. Data lives in a `body_measurements` health table, `source='manual'`.
  - Why: owning the data keeps MyCoach portable if the wearable changes (e.g. Garmin to Whoop).
- **Weight is pushed to Garmin Connect** one-way, in the background, with status/id/error tracked, retries on sync, and delete propagation. Garmin weigh-ins are never imported back. Waist stays local, since Garmin has no field for it.
- **Email:**
  - Latest reading plus how long ago it was taken.
  - Weight trend (EMA) and kg/week once there are ≥4 readings in 14 days.
  - Waist shows waist-to-height (target <0.50) and a 4-week change only when it's ≥2 cm.
- **Nudges:**
  - Weight: after 7 days. The trend is marked stale after 14 days.
  - Waist: after 30 days.

### Coach rules

1. Never comment on a single day's weight. Use the ≥2-week trend.
2. Treat a ±1–2 kg overnight jump as water: look for notes (salt, carbs, alcohol, travel, hard session).
3. Weight change under 0.3 kg/week and waist change ≤1 cm mean "no detectable change".
4. Weekly loss above 1% of bodyweight is too aggressive.
5. Waist down with weight flat is a win (recomposition). Waist up with weight flat is worth raising gently.
6. Frame risk with waist-to-height, not BMI. Never diagnose; suggest DEXA if the user wants composition detail.
7. Mention weight and waist only when something changed meaningfully or a nudge is due.

## 5. Cross-cutting decisions

- **Code renders the numbers; the LLM interprets them.** Email blocks are computed from the database. The LLM gets the same figures as context and never echoes numbers back. (Migrating existing `key_metrics` the same way is #204.)
- **New prompt variables** `steps_summary` and `body_summary`, plus the HRV band in `health_data`. This needs new bundled prompt versions and golden files (ADR 0003). Body data is health context and goes only to the zero-data-retention provider (ADR 0004).
- **Wording:** "associated with", never diagnose, never compare to other people.

## 6. Other metrics in the Huberman framework (brief)

- **VO2max:** Mandsager 2018 *JAMA Netw Open* ([PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6324439)) found no upper limit of benefit. Galpin: ~18 ml/kg/min is the "line of independence". Garmin error is ~5–8%, so trend it over 4–8 weeks. *Caveat:* clinical cohort, estimated from METs.
- **Resting HR:** Galpin uses <60 bpm as the benchmark and ~75 as a red flag. Use the 7-day average.
- **Sleep:**
  - Huberman's Sleep Toolkit: consistent wake time, caffeine cut-off 8–10 h before bed.
  - Regularity beats duration (Windred 2024).
  - Garmin: ~89% sleep/wake agreement with polysomnography, poor sleep stages (kappa ~0.25). Trust bed/wake times, not deep/REM minutes.
- **Zone 2:** Huberman's Foundational Fitness Protocol is ≥150–200 min/wk plus 1 VO2max session plus 2–4 strength sessions. Storoschuk 2025 (*Sports Med*) disputes that Zone 2 is the optimal intensity; mixed intensities work. Garmin's default zones rarely match lactate-defined Zone 2.
- **Strength:** grip strength predicts mortality better than systolic BP (Leong 2015). Galpin/Attia benchmarks (broad jump ≈ height, dead hang ~2 min, bodyweight carry) need manual tests.
- **Morning light:** Windred 2024 *PNAS*: daytime light is protective and night light harmful. Huberman's minute counts are extrapolated. Treat it as a habit, not a metric.
- **Body Battery / Stress:** proprietary Firstbeat composites with no outcome validation. Use as conversational context only.
