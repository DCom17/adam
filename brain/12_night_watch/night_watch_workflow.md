# Night Watch Workflow

> **Advanced / not available in the standard safe build.** Night Watch needs a shell and
> a Windows scheduler, neither of which the assistant has in safe mode. The steps below
> (including any `.ps1` / `python` / `Get-Date` commands and `sheet_sync.json` handling)
> are the **legacy always-on spec** and do not run in this product — if the user asks for
> Night Watch, explain it's an advanced feature that isn't in this build. See CLAUDE.md →
> Night Watch Command.

The autonomous overnight maintenance and evaluation run. Fires nightly via Windows
Task Scheduler while the user sleeps, or manually with "Run Night Watch".

## Governing Principle

The user is not present. So this run **reads, reports, and makes only safe,
reversible, unambiguous fixes** — every fix logged in the packet. It does not make
judgment calls on the user's behalf. The morning packet is a desk report, not a
record of changes made to the system's meaning.

### Hard prohibitions (never, regardless of what looks fixable)
- Never touch Google Calendar — no creates, updates, or deletes.
- Never delete or complete tasks.
- Never promote anything to `long_term_memory.md` or out of `memory_candidates.md`.
- Never apply graph links (`graph_linker.py --apply`). Dry-run only.
- Never delete any file or capture.
- Never process the phone inbox (voice captures need waking judgment).
- Never promote rank or mark a boss cleared.
- **Never write to `02_command_memory/operating_patterns.md` or otherwise change the operating model.** Committing a CONFIRMED pattern into the model is a weekly, user-approved action. (This is the one hard line on performance — the experiment engine itself is user-authorized to run autonomously; see step 7.)

### The archive invariant (absolute)

Self-cleaning **never permanently deletes anything.** Every sweep moves content to
an `_archive/` location where it stays recoverable — the same principle the phone
inbox and commit-preview already follow. If a fix cannot be done by archiving, it is
not a safe fix: flag it instead.

### Safe auto-fix allowlist (these only — fix and log each in the packet)
1. **Stale quest prune:** remove entries from `sheet_sync.json` `daily_quests`
   whose `date` is not today, *only* if that quest already exists in
   `quest_log.csv`. (The 6/15 bug. See [[Dashboard Quest Pruning]].)
2. **State mirror drift:** if `sheet_sync.json` state fields disagree with
   `dashboard_state.json`, rewrite `sheet_sync.json` from `dashboard_state.json`
   (state is source of truth). Required fields: character_level, total_xp,
   xp_to_next, date, rank, fatigue, momentum, system_directive, next_best_action,
   active_boss (ID only — see [[Boss ID Format]]), main_quest, last_updated.
   Then re-run `sync_dashboard.ps1`.
3. **Malformed alias lines** in `10_graph_schema/aliases.md` — fix only when the
   intended canonical mapping is unambiguous; otherwise flag.
4. **JSON/CSV formatting** that still parses — trailing commas, whitespace. Never
   change a value, only formatting.
5. **Daily-log consolidation (merge, then archive):** canonical naming is
   `YYYY-MM-DD.md`. If a `daily_log_YYYY-MM-DD.md` twin exists, **first merge any
   content the canonical lacks** (twins often hold the shutdown results the canonical
   was never filled with — never assume the canonical is complete), then move the
   twin to `03_daily_logs/_archive/`. If only the `daily_log_` version exists, rename
   it to canonical. Fix an obviously wrong weekday if the date proves it.
6. **Active-task hygiene (archive, never delete):**
   - Move `[x]` completed tasks to `completed_log.md`.
   - Move one-time tasks whose explicit date is firmly past to
     `05_tasks/_archive/active_tasks_archive.md` with a one-line reason.
   - De-duplicate identical task lines.
   - **Keep** recurring tasks, standing procedures, future-dated items, and anything
     undated or ambiguous. When unsure whether a task is dead, KEEP it and flag —
     never archive on a guess.

Anything outside this list → **flag under "Needs your call," do not touch.**

## Procedure

1. **Stamp.** Date = today (`Get-Date -Format yyyy-MM-dd`).
2. **Backup first.** Copy `dashboard_state.json`, `sheet_sync.json`, `xp_log.csv`,
   `quest_log.csv`, `weakness_log.csv` into
   `12_night_watch/backups/<date>/`. This run never modifies a dashboard file
   without a snapshot existing first.
3. **Integrity / self-check:**
   - `dashboard_state.json` and `sheet_sync.json` parse as valid JSON.
   - All three dashboard CSVs parse (consistent column counts, no truncated final row).
     Also parse-check `performance_log.csv` (27 columns) and `weekly_checkin.csv` — the
     wide performance log is the most fragile to a misaligned manual append.
   - `sheet_sync.json` state block has every required field, none blank;
     `system_directive` is 1–2 sentences; `fatigue` 0–100; `momentum` a valid label.
   - `sheet_sync.json` mirrors `dashboard_state.json` (→ safe-fix #2 if not).
   - `daily_quests` contains only today's quests (→ safe-fix #1 if not).
   - Stat XP totals in state are internally consistent with the latest `xp_log.csv`
     tail (spot-check, do not full-read history).
4. **Drift / contradiction check:**
   - Any task present in BOTH `active_tasks.md` and `completed_log.md`.
   - `waiting_on.md` items that the logs show are resolved.
   - A daily log dated in the future, or today's missing.
5. **Self-clean (apply allowlist #1–6).** Run the safe auto-fixes now, recording
   each in the packet's "Fixes Applied" section: prune stale quests; mirror
   state→`sheet_sync.json`; fix unambiguous malformed aliases; consolidate duplicate
   daily logs (merge → archive); run active-task hygiene (completed → `completed_log.md`,
   expired one-time → `_archive/`, de-dupe). Archive only — never delete. When a call
   is ambiguous, leave it and flag under "Needs your call."
6. **Self-evaluation** (the part the user reads first):
   - Read the last ~3 daily logs, plus `active_tasks.md`, `waiting_on.md`, and the
     tails of `xp_log.csv` / `quest_log.csv`.
   - Report, honestly and briefly: momentum and fatigue trajectory, which stats are
     being neglected, how many days any open weakness (e.g. WK-001) has persisted,
     tasks that have gone stale, waiting-on items gone cold. What's slipping,
     what's consistent, what to cut. No motivational filler.
7. **Performance & experiment engine (user-authorized to run autonomously; full method in `11_dashboard/performance_experiments.md`):**
   - **Infer the day's levers.** From the daily log + calendar, fill the latest
     `performance_log.csv` row's inferable levers (workout, mindset_block, social,
     outdoors, caffeine, nutrition, late_screens, alcohol) and compute `deep_work_pct`.
     Mark "unknown" if undeterminable — never guess. (The five 7-pt scores, sleep, and
     **adherence** come from the user at shutdown; never infer adherence.)
   - **Manage assignment, don't interpret.** If an experiment is active, set **tomorrow's
     randomized `condition`** (A/B) for it and record the protocol id. Run integrity +
     **missingness** checks (per the decision rule). **Do NOT render a rolling verdict and
     do NOT reveal the tested lever** — the engine runs quietly until the endpoint.
   - **Evaluate only at the planned endpoint** (min data met: ≥24 valid days, ≥10/arm,
     ≥80% adherence-known). Then run `python scripts/performance_analysis.py`, read its
     verdict + effect + diagnostics, and update `performance_experiments.md`. On a
     **Confirmed** result, stage the pattern **"CONFIRMED — pending weekly approval"**
     there (NOT in `operating_patterns.md`). If no experiment is active and a baseline
     exists, **start the next one** — one variable; prefer an unmeasured *interventional*
     lever; observational levers get the association dashboard, never an "experiment."
   - **Announce in neutral terms** at the top of the "Performance & Experiments" section:
     that an experiment is running / started / concluded — without naming the tested lever
     or showing interim results mid-run. Reveal specifics only once a verdict is staged.
   - The one thing this step never does: write to `02_command_memory/operating_patterns.md`.
8. **Morning briefing verification:** confirm `06_calendar/morning_briefing.md`
   exists and its "Briefing for" date is tomorrow. If stale/empty (shutdown was
   skipped), flag it at the TOP of the packet.
9. **Graph linker dry-run:** `python scripts/graph_linker.py --dry-run`, then report
   candidate count and anything noisy from `10_graph_schema/link_candidates.md`.
   Never apply.
10. **Write the packet** to `12_night_watch/packets/<date>_night_watch.md` using the
   structure below.
11. **If a safe-fix touched `sheet_sync.json` or `dashboard_state.json`:** run
   `powershell -ExecutionPolicy Bypass -File .\11_dashboard\sync_dashboard.ps1`
   so the board reflects the correction, and record the sync result in the packet.
   If no dashboard file changed, do not sync.
12. **Log the run:** append one line to `12_night_watch/night_watch_log.md`
    (date, fixes applied count, flags count, sync run y/n).

## Packet Structure

```
# Night Watch — <date>

## ⚠ Top Flags
(anything urgent: stale briefing, failed parse, contradictions — or "None.")

## Self-Evaluation
(momentum, fatigue, neglected stats, weakness day-count, stalls — the honest read)

## Performance & Experiments
(Neutral framing — no rolling verdicts, don't name the tested lever mid-run. Report:
 whether an experiment is running/started/concluded and its protocol id + day-count
 toward the threshold; data health (valid days, adherence-known %, missingness);
 coverage gaps marked "unknown." Reveal specifics + effect only once a verdict is
 staged "CONFIRMED — pending weekly approval." Engine self-drives; the operating-model
 write waits for weekly approval.)

## Integrity & Drift
(checks run, pass/fail each)

## Fixes Applied (auto)
(each safe-fix made, with before→after — or "None.")

## Needs Your Call
(everything flagged but not touched — stale tasks, memory candidates ready,
 judgment items)

## Graph Linker
(dry-run candidate count, noise notes)

## Run Meta
(backup location, sync run y/n, errors)
```

Keep the packet skimmable. The user reads it with coffee, not at a desk.
