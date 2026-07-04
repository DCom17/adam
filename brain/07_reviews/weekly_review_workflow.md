# Weekly Review Workflow

This file defines the full procedure for running the [[Weekly Review|weekly review]].

Triggered by the command: "Run [[Weekly Review|weekly review]]"

> **Authoritative source:** CLAUDE.md (the "Big picture me" command) is the source of truth and may be newer than this doc. In particular, weekly review now includes the **performance gated step** (step 7): capture the weekly Spiritual score, approve any pending experiment-confirmed patterns into the EXPERIMENTAL tier with a 90-day re-check, and revalidate expired rules — see CLAUDE.md and `11_dashboard/performance_experiments.md`.

---

## Purpose

The [[Weekly Review|weekly review]] closes out the week:
- Surfaces wins, misses, stale tasks, and open loops
- Identifies repeated patterns worth noting
- Reviews memory candidates for promotion
- Sets next week's top priorities
- Updates `07_reviews/weekly_review.md`

---

## Step-by-Step

### 1. Read Last 7 Daily Logs

Read each available daily log from the past 7 days in `03_daily_logs/`.
Note which days have logs and which are missing.

### 2. Read Key Context Files

- `05_tasks/active_tasks.md`
- `05_tasks/waiting_on.md`
- `05_tasks/completed_log.md`
- `02_command_memory/memory_candidates.md`
- `02_command_memory/decisions.md`
- `02_command_memory/active_context.md`
- `11_dashboard/dashboard_state.json`
- `11_dashboard/boss_tracker.md`
- `11_dashboard/xp_log.csv`
- `11_dashboard/quest_log.csv`
- `11_dashboard/weakness_log.csv`

### 3. Compile Weekly Summary

Build a summary covering:

- **Wins** — What was completed or clearly moved forward
- **Misses** — What was planned but not done
- **Stale tasks** — Tasks with no logged progress in 7+ days
- **Open loops** — Unresolved follow-ups, pending decisions, waiting items
- **Patterns** — Repeated behaviors, recurring obstacles, or habits worth noting
- **Next week priorities** — Top 3–5 focus areas based on what remains and what matters most

### 4. Hunter Tracker Review

Review the [[Hunter Tracker]] state for the week.

**Boss Progress:**
- Check each active boss in `11_dashboard/boss_tracker.md`.
- Compare daily logs and completed tasks against boss milestone criteria.
- If a milestone is clearly confirmed, propose updating it — do not mark confirmed without user approval.

**Weakness Trends:**
- Review `11_dashboard/weakness_log.csv` for repeated patterns from the past 7 days.
- If a pattern appears 3 or more times, flag it as a recurring weakness.

**Weekly Quest Summary:**
- Review `11_dashboard/quest_log.csv` for quests from the past 7 days.
- Count quests completed, quests missed, and XP earned by stat.

**[[Rank Gate]] Check:**
- Compare current stat levels and [[Character Level|character level]] in `11_dashboard/dashboard_state.json` against rank requirements in `11_dashboard/rank_rules.md`.
- If the user is within range of a [[Rank Gate|rank gate]], note it.
- Do not promote rank without explicit user confirmation.

**Consistency Bonus:**
- Count the number of days with confirmed XP entries this week.
- Apply the correct consistency bonus tier from `11_dashboard/xp_rules.md`.
- Update `11_dashboard/dashboard_state.json` if the bonus has changed.

**Push the weekly update to the board:**
- If the **Hunter add-on is enabled**, build the weekly summary payload from `11_dashboard/dashboard_state.json` and push it with a `hunter.sync` block (see `INTEGRATION_EXECUTION.md`). This is the step that actually gets the weekly update onto the Sheet — don't skip it. Report it as staged for approval (or done if confirmed). If Hunter isn't connected, keep the local state updated and tell the user to add it in Settings → Add-ons.

Do not award retroactive XP without evidence.
Do not mark any boss cleared without explicit user confirmation.

### 5. Memory Promotion Review

- Read all entries in `02_command_memory/memory_candidates.md`.
- Identify entries that are clearly durable, confirmed, and stable.
- Propose specific promotions: which entry, which target file, why.
- Ask the user before promoting any uncertain or one-off items.
- Only promote after user confirms.

Promotion targets:
- Durable facts or preferences → `02_command_memory/long_term_memory.md`
- Behavioral preferences → `02_command_memory/preferences.md`
- Important decisions → `02_command_memory/decisions.md`
- People/relationship context → `02_command_memory/people_and_relationships.md`

### 6. Stale Task Review

- Flag tasks with no updates for 7+ days.
- Ask the user whether to keep, modify, defer, or remove each.
- Do not archive or delete without explicit user confirmation.

### 7. Write Weekly Review

Write the completed summary to `07_reviews/weekly_review.md`.

Use this heading format:

```
## Weekly Review: YYYY-MM-DD to YYYY-MM-DD
```

Add the new review as a dated section at the top of the file. Do not overwrite previous reviews unless the user explicitly asks.

---

## Safety Rules

- Never promote memory candidates without user confirmation for uncertain items.
- Never delete or archive tasks without explicit user approval.
- Do not access Google Calendar.
- Do not create, update, or delete external events.
- When uncertain whether something is a win, miss, or pattern — flag it as a question rather than asserting it.
- Do not award retroactive XP without evidence.
- Do not promote rank changes automatically. Flag for user confirmation.
- Do not mark a boss cleared without explicit user confirmation.
- Do not access Google Sheets.
