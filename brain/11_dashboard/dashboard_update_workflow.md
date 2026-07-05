# Dashboard Update Workflow

This file defines how to update the [[Hunter Tracker]] during daily and weekly workflows.

---

## When to Update

| Trigger | Update Type |
|---|---|
| Daily shutdown | XP log, quest log, weakness log, dashboard state, [[Dashboard Sync Bridge|sheet sync]] |
| Weekly review | Boss progress, weakness trends, [[Weekly Quests|weekly quests]], [[Rank Gate|rank gates]], dashboard state |
| Daily planning | Read state only — recommend quests, no writes unless quests are confirmed |
| Phone inbox | Route captures to quest or weakness log if relevant |

---

## Daily Shutdown Update Procedure

### 1. Read Current State
- Read `11_dashboard/dashboard_state.json`
- Note current XP, levels, fatigue, momentum, active quests

### 2. Evaluate Evidence

Before evaluating any action for XP, apply the [[Daily Quests|Quest Eligibility Filter]]:
- **Stat gate:** Does the action meet qualifying evidence criteria for at least one stat in `stat_definitions.md`?
- **Boss gate:** Does the action directly advance a confirmed milestone for an active boss?
- Fails both gates → skip it. Do not award XP regardless of completion.

For each action that passes at least one gate and is confirmed completed:
- Identify the relevant stat(s)
- Confirm the action qualifies per `stat_definitions.md`
- Determine XP category (routine, milestone, session, micro)
- Check against daily caps in `xp_rules.md`
- Do not award XP without confirmed evidence

### 3. Log XP
For each confirmed XP award, add a row to `xp_log.csv`:
```
timestamp,source_type,stat,xp,category,evidence_ref,approved,notes
```
- `evidence_ref`: daily log date, task name, or confirmation note
- `approved`: true (only log after evidence confirmed)

### 4. Update Quest Log
For each daily quest:
- Mark completed quests as `status: completed` with evidence
- Mark incomplete quests as `status: missed` or `status: partial`
- Add a row to `quest_log.csv`

### 5. Check for Weakness Patterns
If a stat is repeatedly low, a daily quest is repeatedly missed, or the user reports a recurring obstacle:
- Add a row to `weakness_log.csv`
- Note the pattern, stat, severity, and a proposed countermeasure

### 6. Update Dashboard State
Recalculate and update `dashboard_state.json`:
- Add XP to relevant stats
- Check if any stat leveled up (XP ≥ xp_to_next)
- Recalculate character_level using the formula in `rank_rules.md`
- Update fatigue (increment if high-XP day; decrement if rest day)
- Update momentum (increment if XP earned; reset if missed)
- Update next_best_action based on weakest stat and active boss
- **Replace** the `daily_quests` array entirely — remove all prior-dated quests before writing new ones. Prior quests are already archived in `quest_log.csv`. The array should contain only today's quests when done.
- **CANONICAL QUEST FIELD NAMES — do not rename.** The Sheet writes each quest field into the Daily_Quests tab by matching the JSON key to the column header. Use these exact keys (they mirror `quest_log.csv` headers) in `dashboard_state.json` AND the `hunter.sync` payload you emit:
  `date`, `quest_id`, `quest`, `type`, `linked_stat`, `linked_boss`, `xp_value`, `status`, `evidence`, `notes`.
  Do NOT use `id`, `title`, `stat`, `xp`, `category`, or `boss` — those keys silently fail the column match, leaving Quest ID / Quest / Type / Stat / XP blank on the board, which makes the Mobile_Dashboard report "no daily quests loaded" even though the rows synced. (Broke this way 2026-06-17.)
- **`xp_value` is the quest's planned BOUNTY, assigned at staging — never 0.** Staging a value is not awarding it. The bounty is informational feedback (what the quest is worth if completed); whether it's actually collected is decided at shutdown review against evidence — completed / partial / missed. The "never award XP for plans or intentions" rule in `xp_rules.md` governs the AWARD, not the displayed bounty. Pick the bounty from the action class and `quest_log.csv` precedent (micro ≈ 3, routine ≈ 4–6, boss_step ≈ 8, milestones per the tier table). A quest staged at 0 shows the user a board full of worthless quests. (Broke this way 2026-07-01.)
- **`type` uses the `quest_log.csv` vocabulary:** `micro` / `routine` / `boss_step` / `milestone` — not "daily" (every row in Daily_Quests is daily; the type column is the action class that sets the bounty).

### 7. Push to the Sheet (if Hunter is connected)
`dashboard_state.json` is the single source of truth. There is **no `sheet_sync.json`
file** anymore — when it's time to sync, you build the payload from
`dashboard_state.json` and deliver it in a `hunter.sync` ACTION block (see
`INTEGRATION_EXECUTION.md` and the Dashboard Sync Workflow below). The payload carries:
- character_level, rank, total_xp, fatigue, momentum
- All stat levels and XP
- Active boss
- next_best_action / system_directive
- today's `daily_quests` only, with the canonical keys above

First confirm the Hunter add-on is enabled (the live ADD-ONS note). If it isn't,
`dashboard_state.json` is still updated — just tell the user the board won't reflect it
until they connect Hunter in Settings → Add-ons, and don't emit a block.

---

## Weekly Review Update Procedure

### 1. Review Boss Progress
For each active boss in `boss_tracker.md`:
- Identify confirmed milestones from the week's daily logs
- Update milestone checkboxes only for confirmed completions
- Update progress_percent estimate

### 2. Review Weakness Trends
- Read `weakness_log.csv`
- Identify recurring patterns across the week
- Propose countermeasures for persistent weaknesses
- Update severity if pattern is worsening or improving

### 3. Set Weekly Quests
Based on weak stats, active bosses, and open loops, define 3–5 [[Weekly Quests|weekly quests]]:
- Add to `dashboard_state.json` under `weekly_quests`
- Each quest should have a clear definition of done

### 4. Review Rank Gate
Run the [[Character Level|character level]] formula against current stat levels.
If [[Rank Gate|rank gate]] conditions are met, flag for user review.
Do not promote rank without explicit confirmation.

### 5. Update Consistency Bonus
Based on active days in the past 7 days, set `consistency_bonus` in dashboard state.

---

## Dashboard Sync Workflow

Triggered by: "Sync dashboard"

Purpose:
Push the current dashboard state to the user's Google Sheet by emitting a `hunter.sync`
ACTION block. You build the payload from `dashboard_state.json` and emit the block; the
server sends it to the user's own [[Apps Script Bridge|Apps Script]] bridge after the
user approves (or immediately, only if the action is reported as auto-run). You never run
a script. Full format: `INTEGRATION_EXECUTION.md`.

Rules:
- Build the payload from `dashboard_state.json` — it's the source of truth.
- Confirm the **Hunter add-on is enabled** first (live ADD-ONS note). If not, skip the block, keep `dashboard_state.json` updated, and tell the user to connect Hunter in Settings → Add-ons.
- Do not emit `hunter.sync` with a malformed/empty payload.
- Do not award XP without evidence. Do not change rank without user confirmation.
- Only emit the block when the user says "Sync dashboard" or as part of daily shutdown / "Give me credit" / weekly review / sign-off — not on every turn.
- The payload is structured dashboard data only, never the full vault.
- It's a proposal: report it as staged-for-approval, never claim the board updated until the server confirms it ran.

### Protected Fields — DO NOT OVERWRITE

These State tab cells are formula-driven. Never include them in the sync payload `state` block:

| Cell | Field | Source |
|---|---|---|
| State!B2 | character_level | Calculated from XP_Log + Level_Curve |
| State!D2 | total_xp | SUM of XP_Log confirmed entries |
| State!E2 | xp_to_next | Calculated from Level_Curve |

Claude/Adam writes ONLY these State fields:

| Cell | Field | Who sets it |
|---|---|---|
| State!A2 | date | Claude — current date |
| State!C2 | rank | Claude — from rank_rules |
| State!F2 | fatigue | Claude — 0–100 assessed score |
| State!G2 | momentum | Claude — readable label |
| State!H2 | next_best_action / system_directive | Claude — strong directive |
| State!I2 | active_boss | Claude — current primary boss |
| State!J2 | main_quest | Claude — current main quest |
| State!K2 | last_updated | Claude — timestamp |

XP architecture:
- XP_Log is the source of truth for earned XP.
- Level_Curve is the source of truth for level thresholds.
- State!D2 and State!E2 are Sheet formulas — do not manually calculate or inject these.
- Only append to XP_Log when evidence is confirmed (approved = TRUE).

### System Directive Rule

Every sync must include a `system_directive` in the `state` block. Also mirror the same text into `next_best_action` for compatibility.

Requirements:
- Write `system_directive` as a direct instruction from the system — what matters now and why.
- 1–2 sentences. Mobile-readable. No vague labels.
- Mirror into `next_best_action` (same text).
- Never leave either field blank.
- Priority logic for content: most urgent open quest → active weakness → active boss → current stat bottleneck.

Good examples:
- "Complete the study block at 2:00 PM — this is the second consecutive miss. Knowledge Level 2 is 15 XP away; do not defer again."
- "Send the team message at 8:05 AM. Hit all 4 quests today — Knowledge level-up is on the line."
- "Finance review is the smallest winning move tonight. Complete the log and prevent the streak from breaking."

Bad examples (do not write these):
- "Do school." / "Daily quest active." / "Work on stuff." / "Pending."

How to emit it (build the payload from `dashboard_state.json`):
```
<<ACTION type="hunter.sync" summary="Sync today's quests and state">>
{ "payload": { "state": { ... }, "daily_quests": [ ... ], "stats": [ ... ] } }
<<END_ACTION>>
```

---

## Daily Planning Read Procedure

During [[Daily Planning|daily planning]], read but do not write to Hunter files unless quests are confirmed:

1. Read `dashboard_state.json` — check levels, fatigue, momentum, active boss
2. Read active and [[Weekly Quests|weekly quests]]
3. Read `boss_tracker.md` — note which boss is active and nearest milestone
4. Identify the 2 weakest stats
5. Recommend 3–5 [[Daily Quests|daily quests]] weighted toward weak stats and active boss
6. Present quests to user before staging the [[Calendar Packet Workflow|calendar packet]]

---

## Hunter Dashboard Visual Preservation Rule

The Google Sheets dashboard has polished visual tabs:
- **Dashboard** — desktop/full command view
- **Mobile_Dashboard** — iPhone status view

Raw data tabs are hidden in Google Sheets:
- State, Stats, Daily_Quests, Weekly_Quests, Main_Quests, Bosses, Boss_Milestones, Weaknesses, XP_Log, Rank_Rules, Config, Level_Curve, System_Log, Progress_History

[[Claude Code]] writes to vault files only:
- `dashboard_state.json`
- `xp_log.csv`
- `quest_log.csv`
- `weakness_log.csv`

Do not redesign the Google Sheets visual tabs unless the user explicitly asks for a visual upgrade.

[[Apps Script Bridge|Apps Script]] sync updates data tabs and state values only — it does not overwrite the visual layout.

Dashboard aesthetic (reference for any future visual proposals):
- Dark system interface, cyan/purple glow
- Stat panels, boss detail card, [[Rank Gate|rank gate]] panel
- Daily quest board, [[Weakness Analysis|weakness analysis]], system notifications
- Strict progression feel, readable on iPhone and Windows
