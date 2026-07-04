# Performance Experiments — Methodology

The active half of the performance system. The model in `02_command_memory/operating_patterns.md` holds conclusions; this file defines **how a conclusion is allowed to be reached.** Rebuilt 2026-06-17 on an n-of-1 / single-case methodology review (see `08_sources/` if archived). The governing principle: **stop calling observation experiment, randomize what can be randomized, verify what actually happened, defer the verdict, and let rules expire.**

Tier in force: **Moderate.** Framing: **neutral labels + deferred verdicts.**

---

## 1. The data (per `11_dashboard/performance_log.csv`)

**Outcomes — self-rated nightly, 7-point labeled scales (analyzed separately, never as one average):**
- **Energy** (1 depleted → 7 fully charged)
- **Focus** (1 scattered → 7 locked in)
- **Follow-through** (1 drifted off plan → 7 executed the day I intended) — replaces "Discipline," which blends identity/guilt/effort and drifts semantically.
- **Mood** (1 low → 7 great)
- **Stress / Overwhelm** (1 calm → 7 overwhelmed) — note the inverted direction; high is bad.
- *Moderate-tier optional:* `energy_mid` / `focus_mid`, one ultra-brief midday probe on some days. Blank when not taken.

**Objective co-primary (harder to game):** `deep_work_pct` — % of planned deep-work / focus blocks actually completed. Plus `output_xp` as a secondary signal. Every experiment names one subjective primary **and** this objective co-primary, predeclared.

**Adherence — self-reported nightly, NOT inferred:** `adherence` = yes / partial / no / na. *A scheduled workout is not a workout.* This is the single most important field; inference from the calendar is too error-prone to trust for the variable we're correlating.

**Levers (inferred by Night Watch from log + calendar):** workout, mindset_block, nutrition, caffeine, late_screens, outdoors, social, alcohol. Sleep (quality 7-pt + hours) is self-reported. Undeterminable = "unknown," never guessed.

**Assignment:** `condition` (A/B for that day) and `active_protocol` (neutral id, e.g. PROT-2026-07-A).

**Spiritual** is now a **weekly** measure (`weekly_checkin.csv`), not daily — its validated scales are long and daily signal is low.

---

## 2. Classify every lever before testing it

| Class | Definition | Method allowed |
|---|---|---|
| **Interventional** | The scheduler can assign it and you can verify it happened (workout, outdoor walk, reflection/mindset block, morning ritual, caffeine-cutoff *reminder*). | Randomized crossover experiment → can reach **Confirmed (experimental)**. |
| **Support-randomizable** | Can't force the behavior, but can randomize a *support* (sleep-hygiene routine, caffeine-cutoff prompt, friction reducers). | Randomize the support package; conclusion is about the *protocol*, not the raw behavior. |
| **Observational only** | Can't be assigned (sleep quality, actual caffeine/alcohol intake, spontaneous mood). | Association analysis only → ceiling is **Suggestive (observational)**, never "confirmed causal." |

The engine must never run an "experiment" on an observational lever or label its result causal.

---

## 3. Two analysis tracks

**A. Manipulated / randomized levers**
- **Randomize assignment.** Acute reversible lever → randomize A/B **by day**. Slow/lingering lever → randomized **3–7 day blocks** with explicit wash-in/washout. Use **active controls** where possible (workout vs matched mobility; reflection vs reading), not empty controls.
- **Inference:** a **randomization test** (depends on the assignment mechanism, not parametric assumptions) **plus** a model-based effect estimate adjusting for prior-day outcome, day-of-week, and time trend. Computed by `scripts/performance_analysis.py`.

**B. Observational levers**
- Rolling **association dashboard** over the last 60–90 valid days. Lagged regression with prior-day outcome, day-of-week, time trend, and a **small, pre-chosen** confounder set.
- Output is **association strength, ranked as a hypothesis** — and the pipeline is: a strong observational signal becomes a *candidate* for a future randomized test if/when it becomes manipulable. Never promoted straight to Confirmed.

---

## 4. The decision rule (precommitted — replaces "2 weeks vs baseline")

**Minimum analyzable data**
- Acute day-level lever: **≥24 scheduled days, ≥10 valid A days and ≥10 valid B days, ≥80% adherence-known days.**
- Block lever: **≥4 crossovers** plus enough days for within-block stabilization.
- Never finalize early because a graph looked exciting.

**Confirmed (experimental)** — all four:
1. Truly randomized / experimentally assigned.
2. Adherence known and acceptable.
3. Effect exceeds a **minimally important within-person difference**: ~**0.3 within-person SD**, or ~**0.5 pt on the 7-pt scale**, or **≥10% on the objective co-primary**.
4. Strong inferential support: randomization-test **p < .05** **and** a model effect of practical size, **sign consistent across replications.**

**Irrelevant** — minimum data met, probability of material benefit low **and** material harm low.

**Inconclusive** — everything else (default when missingness is notable, adherence uncertain, or effect small vs day-to-day variance). The engine must have and use this state.

**Autocorrelation/trend:** check residual autocorrelation, carry a lagged-outcome term; if strong trend/seasonality and too little data, restrict the window — don't pretend the model corrected what it couldn't.

**Missing data:** no carry-forward, ever. Missing outcome → drop the day from primary analysis. Intervention done but adherence unknown → exclude from per-protocol, keep for assignment-based intention-to-treat if randomized. **>10% missing → mandatory sensitivity note. >20% missing or condition-imbalanced missingness → default Inconclusive** unless assignment-based and model-based results agree.

---

## 5. Bias control (damage reduction, not rescue)

You know the intervention and rate yourself, so expectation/demand effects can't be fully removed. Mitigations, all in force:
- **Neutral labels** — test days are "Protocol A / Protocol B" in any report; the lever under test and interim results are withheld until the analysis point.
- **Active controls** over empty ones.
- **Objective co-primary** every time, so you can't just train yourself to *report* the tested lever "felt productive."
- **No rolling verdicts.** The engine logs and assigns quietly; interpretation surfaces only after the block/study ends.
- **Predeclared** primary + co-primary outcome per experiment.
- Honest interpretation: a confirmed effect here is "the intervention **as experienced by me, open-label**," not a clean mechanistic estimate. Useful for self-management; not pure science.

---

## 6. One experiment at a time, and don't free-associate

- One variable per experiment, or the result can't be attributed.
- **Interactions** (e.g. workout helps only when well-slept): only test after a main effect is at least *suggestive*, and require ~60–90 valid days or a blocked design that intentionally varies the moderator.
- Rotation: prefer an unmeasured **interventional** lever over re-testing one already measured — but signal strength can override; if an observational dashboard flags a strong, newly-manipulable candidate, it jumps the queue.

---

## 7. Evidence tiers & expiry (how conclusions leave this file)

Verdicts feed `operating_patterns.md` under three tiers — **Experimental personal effect**, **Suggestive observational signal**, **Retired / drifted**. Every confirmed rule is **time-stamped and time-limited: re-check at ~90 days** or sooner if trend/residual checks suggest drift. The write into the operating model is the **one** action that requires user approval (weekly review).

---

## How a cycle runs

1. **Nightly (Night Watch, autonomous):** infer levers; ingest the self-reported scores + adherence; set tomorrow's randomized `condition`; run integrity/missingness checks. **No interim verdict.**
2. **At the planned endpoint (min data met):** Night Watch calls `scripts/performance_analysis.py`, gets verdict + effect + diagnostics, stages any Confirmed result as **"CONFIRMED — pending weekly approval"** here, and proposes the next experiment. Announces *that* an experiment concluded — in neutral terms — without showing the tested lever mid-run.
3. **Weekly (user present):** approve/reject the pending pattern → only then written to `operating_patterns.md`. Capture the weekly Spiritual score. Revalidate any rule past its 90-day mark.

---

## Active Experiment

_None yet. Night Watch starts the first once `performance_log.csv` has enough valid days to establish a baseline (~5+), and runs it to the ≥24-day / ≥10-per-arm threshold before any verdict._

## Experiment Log

<!--
### PROT-XXXX — <neutral id>  (lever revealed only after verdict)
- Class: interventional / support-randomizable / observational
- Design: day-randomized A/B | N-day blocks ×K | observational
- Primary outcome / Objective co-primary:
- Assignment window: YYYY-MM-DD to YYYY-MM-DD
- Valid days (A/B), adherence-known %, missing %:
- Randomization-test p / model effect (within-SD) / sign consistency:
- Verdict: Confirmed (experimental) / Suggestive (observational) / Irrelevant / Inconclusive
- Written to operating model: y/n + date approved
-->

_Rebuilt 2026-06-17 on n-of-1 methodology review._
