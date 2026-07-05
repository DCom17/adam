# Daily Shutdown Workflow

This file defines the full procedure for running [[Daily Shutdown|daily shutdown]].

Triggered by the command: "Run [[Daily Shutdown|daily shutdown]]"

> **Authoritative source:** CLAUDE.md (the "How'd we do" command) is the source of truth and may be newer than this doc. In particular, shutdown now includes **performance capture** (step 16): five 7-pt scores + sleep + adherence, levers inferred — see CLAUDE.md and `11_dashboard/performance_experiments.md`.

---

## Purpose

Daily shutdown closes out the day cleanly:
- Captures what actually happened
- Processes any unprocessed phone inbox entries
- Moves confirmed completed tasks to completed_log.md
- Evaluates completed actions for [[Hunter Tracker]] XP
- Updates waiting items
- Flags durable information for memory review
- Sets a starting point for tomorrow

---

## Step-by-Step

### 1. Read Context

- Read startup files per CLAUDE.md Startup Protocol
- Read today's daily log: `03_daily_logs/YYYY-MM-DD.md`
- Read `00_inbox/phone_capture.md`
- Read `05_tasks/active_tasks.md`
- Read `05_tasks/waiting_on.md`
- Read `02_command_memory/memory_candidates.md`
- Read `06_calendar/commit_preview.md` only — do not read `latest_calendar_packet.md` during shutdown (large file, not needed unless a calendar action is actively pending)

### 2. Check Daily Log Completeness

If today's daily log is missing a "What Actually Happened" section or looks incomplete:
- Ask: "What actually got done today?"
- Update the daily log based on the user's response.
- Do not assume tasks are complete. Only mark things done when confirmed.

### 3. Process Phone Inbox

If `00_inbox/phone_capture.md` has unprocessed entries:
- Run the "Process phone inbox" routine.
- Route entries per `00_inbox/inbox_processing_rules.md`.
- Do not promote captures directly to long-term memory.

After processing, archive old captures: append all processed entries older than 7 days to `00_inbox/phone_capture_archive.md` and remove them from `phone_capture.md`. Keeps the active inbox file lean. Never permanently delete — always archive.

### 4. Commit Preview Check

Read `06_calendar/commit_preview.md`.

If a preview exists that has not been resolved:
- Ask: "Were these events committed, skipped, or still pending?"
- If committed: confirm the events are logged in `06_calendar/commit_log.md`. If not logged, flag for the user to log them.
- If skipped: note the preview as stale and ask whether to clear it.
- If still pending: leave it marked as pending and note it in the "Tomorrow Starting Point."
- Do not access Google Calendar.
- Do not create, update, or delete external events.

If no active preview exists, skip this step.

After reviewing, archive ALL Resolved entries: move every `Resolved` section from `commit_preview.md` to `06_calendar/packet_archive/commit_archive.md` and remove them from `commit_preview.md`. Resolved entries have no future operational value. Only pending/active previews stay in the main file.

### 5. Move Completed Tasks

For each task confirmed complete today:
- Copy it to `05_tasks/completed_log.md` with the completion date and a brief result note.
- Remove it from `05_tasks/active_tasks.md`.
- Do not move a task unless the user confirms it is complete or the daily log clearly confirms it.

### 6. Hunter Tracker Evaluation

Read `11_dashboard/dashboard_state.json` for current XP totals. Do NOT re-read `xp_log.csv` or `quest_log.csv` in full — append new entries only. Only read `11_dashboard/xp_rules.md` and `11_dashboard/stat_definitions.md` when there is a novel or uncertain XP eligibility question — skip for routine shutdowns where quest types are familiar.

**Before evaluating any action for XP, apply the [[Daily Quests|Quest Eligibility Filter]]:**

- **Stat gate:** Does the action meet qualifying evidence criteria for at least one stat in `stat_definitions.md`?
- **Boss gate:** Does the action directly advance a confirmed milestone for an active boss?
- Fails both gates → skip it entirely. Do not award XP regardless of whether the task was completed.

For each action that passes at least one gate and has confirmed evidence:
- Check whether evidence exists in today's daily log or via user confirmation.
- Identify the linked stat and XP value.
- Verify XP caps are not exceeded: 60 routine XP/day total, 18/stat/day, 6 micro-action XP/day.
- If evidence is confirmed, add an entry to `11_dashboard/xp_log.csv`.
- If a daily quest was completed, update its status in `11_dashboard/quest_log.csv`.
- If no evidence exists, do not award XP.

After evaluating all eligible actions:
- Update `11_dashboard/dashboard_state.json` with new XP totals, stat level-ups, **and updated quest statuses** (completed/partial/missed) with evidence strings.
- **If any boss milestone was confirmed today:** immediately update `11_dashboard/boss_tracker.md` — check the completed milestone box (`- [x]`), update the `Progress:` line to the new percent, update notes with the confirmation date. Then update `progress_percent` and `milestones_confirmed` in `dashboard_state.json` bosses array to match.
- If the **Hunter add-on is enabled**, push the board with a `hunter.sync` block built from `dashboard_state.json` (see `INTEGRATION_EXECUTION.md`) — same quest statuses, same XP totals. **Always include the full `bosses` array** in the payload so boss progress syncs (`bosses_updated` should be > 0). Update quest statuses in `dashboard_state.json` BEFORE building the payload, or the board still shows "pending." If Hunter isn't connected, skip the block — local state is still updated — and tell the user to add it in Settings → Add-ons.
- Report the sync as staged for approval (or done if the server confirms it ran) — don't claim the board updated until told.
- If a repeated weakness pattern is visible, add an entry to `11_dashboard/weakness_log.csv`.

Do not award XP without evidence.
Do not promote rank changes — flag for user confirmation.
Do not mark a boss cleared without explicit user confirmation.
Do not access Google Sheets.

### 7. Update Waiting Items

- Read `05_tasks/waiting_on.md`.
- Add anything newly blocked or waiting on someone else.
- Remove resolved items.
- Note date added for new waiting items.

### 8. Flag Memory Candidates

If anything durable was learned or confirmed today:
- Add it to `02_command_memory/memory_candidates.md`.
- Label clearly as a candidate.
- Do not promote directly to `long_term_memory.md`, `preferences.md`, or `decisions.md` without confirmation.

### 9. Add Tomorrow Starting Point

Add a section to today's daily log:

```
## Tomorrow Starting Point

Top priorities:
- [priority 1]
- [priority 2]
- [priority 3]

Carrying over:
- [unfinished item]

Open loops:
- [pending follow-up]

Known time blocks:
- [any confirmed commitments]
```

### 10. Build Tomorrow's Morning Briefing

Overwrite the "Briefing for" section of `06_calendar/morning_briefing.md` with tomorrow's date and shape. This is the single file the next "Run daily planning" pulls so the morning conversation is fast — confirmation and deltas, not interrogation.

Populate from what you just closed out plus known context:
- **Known fixed commitments** — work shift (per work schedule), classes, appointments, with times
- **Time blocks to remember** — time-sensitive action items (e.g. "Send team message 8:05 AM")
- **Approaching deadlines** — with date and days-out
- **Carry-over tasks** — unfinished items, flagged critical / flexible / deferred
- **Open weaknesses / boss context** — active weakness ID + day count, nearest boss milestone
- **Questions Adam should ask** — only the gaps the briefing can't already answer

Pull most of this straight from the "Tomorrow Starting Point" you just wrote, active tasks, waiting items, and dashboard state. Update the `Last built` timestamp.

### 11. Capture the Performance Day

Append one row to `11_dashboard/performance_log.csv`.

Header columns: `date, condition, adherence, sleep_quality, sleep_hours, workout, workout_type, mindset_block, nutrition, caffeine, late_screens, outdoors, social, alcohol, energy, focus, follow_through, mood, stress, energy_mid, focus_mid, deep_work_pct, output_xp, active_protocol, top_win, limiter, notes`

**Ask the user only for (one short exchange):**
- Five **7-point** self-reported scores: **Energy / Focus / Follow-through / Mood / Stress** (Stress is inverted: 7 = most overwhelmed; never computed from quests)
- **Sleep** quality (7-pt) + hours
- **Adherence** (yes / partial / no) — only if an experiment was assigned today

**Adam infers everything else:**
- Levers (workout, mindset_block, nutrition, caffeine, late_screens, outdoors, social, alcohol) — infer from daily log + calendar. Mark "unknown" if genuinely undeterminable. Never guess.
- `deep_work_pct` — planned focus/deep-work blocks completed today (percentage)
- `output_xp` — XP earned today
- `condition` / `active_protocol` — carry from the active experiment if one is running
- Note which `[FLEX]` fills landed vs. skipped

Do NOT ask for Spiritual here — it's weekly (in `weekly_checkin.csv`).
Do NOT compute a day_score — dimensions are analyzed separately.
Append-only — never read the CSV in full.
Capture only — no analysis, no verdict. Pattern analysis runs separately; writes to the performance model happen only at weekly review on approval.

---

## Safety Rules

- Do not delete tasks or captures without explicit user approval.
- Do not mark tasks complete unless the user confirms or the daily log clearly confirms it.
- Do not promote uncertain information to long_term_memory.md. Use memory_candidates.md.
- Do not access Google Calendar.
- Do not create, update, or delete external events.
- Do not award XP without evidence.
- Do not promote rank changes automatically. Flag for user confirmation.
- Do not mark a boss cleared without explicit user confirmation.
- Do not access Google Sheets.
