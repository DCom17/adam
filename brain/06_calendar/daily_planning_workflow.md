# Daily Planning Workflow

## Purpose

This workflow turns a messy day plan into a realistic [[Calendar Packet Workflow|calendar packet]].

## Command

When the user says:

"Run [[Daily Planning|daily planning]]" (or "Start my day")

This is a **conversation-first** command. Phase 1 gathers everything from the user in one fluid conversation. Phase 2 runs the slow mechanical work *after* the user signs off and walks away — so they are never waiting on processing. See CLAUDE.md → Daily Planning Command and Sign-Off Execution Rule.

The assistant should:

1. Read startup files from `CLAUDE.md`.

2. Read context files:
   - `06_calendar/morning_briefing.md` — **read first.** Built last night at shutdown; already describes today's shape. The conversation is confirmation and deltas, not interrogation.
   - `06_calendar/calendar_lessons.md` — apply every standing lesson when building the day.
   - `05_tasks/active_tasks.md`
   - `05_tasks/waiting_on.md`
   - `06_calendar/latest_calendar_packet.md`
   - `06_calendar/calendar_rules.md`
   - today's daily log
   - yesterday's daily log if useful
   - `02_command_memory/active_context.md`

3. Read [[Hunter Tracker]] files:
   - `11_dashboard/dashboard_state.json` — check level, fatigue, momentum, active quests, active boss
   - `11_dashboard/boss_tracker.md` — identify nearest milestone for active boss
   - `11_dashboard/stat_definitions.md` — reference for quest evidence requirements
   - `11_dashboard/xp_rules.md` — reference for XP caps and categories

4. Have a real conversation — ask only the gaps the briefing can't already answer. The briefing should already cover most of the day, so confirm it and probe changes rather than asking the user to recite everything. Cover anything the briefing flags under "Questions JARVIS should ask," plus:
   - any new or changed fixed commitments
   - flexible tasks not already known
   - deadlines that shifted
   - errands/travel
   - energy level
   - wake/sleep constraints
   - any meals, breaks, or personal obligations

5. Apply [[Daily Quests|Quest Eligibility Filter]].

   From the user's input, list every item on the day — all tasks, errands, commitments, and calendar blocks.

   For each item, apply two gates:

   **Stat gate:** Does this action meet the qualifying evidence criteria for at least one stat in `11_dashboard/stat_definitions.md`?
   **Boss gate:** Does this action directly advance a confirmed milestone for an active boss in `11_dashboard/boss_tracker.md`?

   - Passes either gate → quest-eligible
   - Fails both gates → task only (no quest ID, no XP potential)

   Common failures (both gates fail):
   - Errands with no stat or boss linkage (ordering supplies, running errands)
   - Passive scheduled time with no intentional investment
   - Admin tasks that don't advance a boss milestone
   - Planning or intention without execution
   - Passive entertainment or routine co-presence

   Do not assign quest IDs to ineligible items.

6. Generate Daily Quest Recommendations from eligible items only.

   Use this template for each quest:

   ```
   Quest ID: DQ-YYYY-MM-DD-N
   Quest: [clear action with defined completion criteria]
   Linked Stat: [stat name]
   Linked Boss: [boss name or "none"]
   Estimated XP: [number]
   Evidence Required: [what must be true for XP to be awarded at shutdown]
   Suggested Time Block: [time range or "flexible"]
   ```

   **Quest count rule:** Generate one quest for every item that passes the eligibility filter. Do not pad to hit a minimum. Do not cut to hit a maximum. If only 2 items pass, the quest board has 2 quests. If 9 pass, it has 9. Use judgment above ~8 — if the list is getting unmanageable, flag it and ask the user to prioritize rather than silently dropping quests.

   Present two outputs to the user before proceeding to step 7:
   - **Full schedule** — every item including non-quest tasks
   - **Quest board** — eligible quests only (exact count, no artificial cap)

   Do not write XP during planning. Only create planned quests.

7. Build a realistic plan incorporating confirmed quests as time blocks.

   **Full day staging rule:** Every item with a defined time belongs on the calendar. Do not exclude work shifts, admin windows, meals, errands, chores, or short tasks. Stage the complete day.

   **Time-sensitive action items:** If a task has a required time (e.g., "Send team message at 8:05 AM"), create it as its own calendar event with a popup reminder — not just a note inside a larger block. The user relies on calendar notifications to execute these on time.

8. Update:
   - `06_calendar/latest_calendar_packet.md`

9. **Sign-off triggers execution.** When the user ends the conversation with any natural closer ("that's it buddy," "see you later," "thanks have a good one"), run the full chain — validate, build the commit preview, emit a `calendar.create` block for today's events, emit a `hunter.sync` block for the dashboard, and Save. See CLAUDE.md → Sign-Off Execution Rule and `INTEGRATION_EXECUTION.md`. There is no fixed trigger phrase and no separate `INPUT`/`Do it` step required from the user. By default the calendar block is **staged for the user's approval tap** (or runs immediately if they've turned auto-run on) — report it accordingly, never as already done. Then leave a summary.

   The manual staged path (`Preflight this` → review → sign-off) still works if the user wants to stage and review explicitly before walking away.

## Post-Commit Dashboard Sync (Required After Every Calendar Commit)

After sign-off fires and calendar events are committed, immediately:

1. Update `11_dashboard/dashboard_state.json`:
   - Set `date` to today
   - Set `daily_quests` to today's planned quests (exact count from the quest board — do not assume 5). Set status to "pending" for all. Do not fill XP, evidence, or notes at planning time — those are written at shutdown only.
   - Update `system_directive` and `next_best_action` to reflect today's priority
   - Update `fatigue` and `momentum` based on current context
   - Update `last_updated` to current timestamp

2. Build the `hunter.sync` payload from `dashboard_state.json` — same date, same quests, same state block.
   - State block **MUST include** `character_level`, `total_xp`, and `xp_to_next` — copy directly from `dashboard_state.json`. These are the source of truth; do not rely on Sheets formulas.
   - Set `xp_log` to `[]` (empty — XP is only written at shutdown)

3. If the **Hunter add-on is enabled**, emit the block (see `INTEGRATION_EXECUTION.md`):
   ```
   <<ACTION type="hunter.sync" summary="Sync today's planned quests and state">>
   { "payload": { "state": { ... }, "daily_quests": [ ... ], "stats": [ ... ] } }
   <<END_ACTION>>
   ```
   If Hunter isn't connected, skip the block — `dashboard_state.json` is still updated — and tell the user to add it in Settings → Add-ons.

4. Report it as staged for approval (or done only if the server confirms / auto-run). Don't claim the board updated until told.

**Why this step exists:** `dashboard_state.json` is the source of truth, but the Sheet only reflects it once a `hunter.sync` block is pushed and approved. Build the payload from today's state every time so the board always shows today's planned quests after a commit.

## INPUT Validation

When the user says `INPUT`, validate the packet for:

- correct date
- correct timezone
- no overlaps
- fixed commitments preserved
- realistic durations
- buffers
- travel time
- meals/rest
- clear event titles
- reminder settings
- commit readiness

Then update the packet checklist.

## Commit Preview

When the user asks for [[Commit Preview|commit preview]]:

1. Read the validated packet.
2. Write proposed calendar events into:
   - `06_calendar/commit_preview.md`

Do not touch Google Calendar.

## Calendar Commit (Creates via ACTION block)

On user sign-off, [[Claude Code]] commits calendar **creates** by emitting a `calendar.create` block built from the approved preview (see `INTEGRATION_EXECUTION.md` and `calendar_commit_workflow.md`). The server sends it to the user's Calendar add-on. By default it waits for the user's approval tap; with auto-run on it's immediate. No script, no `calendar_sync.json`.

Creates only. The product has no calendar delete, and updates are deliberate one-off `calendar.update` blocks — never part of the sign-off chain.

After the commit, [[Claude Code]] logs the result in:
- `06_calendar/commit_log.md`
- today's daily log

## Safety Rule

No calendar event is considered committed unless it appears in `commit_log.md`.