# Operating Rules

## System Priority

This vault is the durable shared-brain system. If chat memory conflicts with this vault, ask which source is current before making important decisions.

## Default Workflow

1. Read relevant context.
2. Identify the actual objective.
3. Separate fixed facts from assumptions.
4. Propose the cleanest next move.
5. Edit files only when asked or clearly appropriate.
6. Log meaningful work after completion.

## Planning Workflow

When the user gives messy planning input:

1. Extract fixed commitments.
2. Extract flexible tasks.
3. Estimate durations.
4. Add buffers.
5. Identify risks and conflicts.
6. Create or update a [[Calendar Packet Workflow|calendar packet]] if requested.

## Calendar Commands

"Stage this for input"
- Create or revise `06_calendar/latest_calendar_packet.md`
- Do not commit anything externally.

"INPUT"
- Validate the latest [[Calendar Packet Workflow|calendar packet]].
- Identify overlaps, missing buffers, unrealistic durations, vague tasks, and open questions.
- Do not commit anything externally.

"COMMIT"
- Only after approval, create or update Google Calendar events if connector access is available.
- Log committed changes in `06_calendar/commit_log.md`.
- Update the daily log.

"HOLD"
- Do not write or commit anything externally.

## Memory Promotion

Use this path:

Daily log → memory candidates → long-term memory

Do not promote unconfirmed assumptions directly into long-term memory.

Promote only:
- durable preferences
- recurring routines
- stable roles
- active long-term projects
- important decisions
- repeated constraints
- lessons likely to matter later

Do not promote:
- one-off emotions
- random brainstorming
- temporary frustrations
- stale plans
- unverified claims

## File Editing Rules

Allowed:
- Edit markdown files inside this vault.
- Create new files only when they have a clear purpose.
- Prefer updating existing files over creating duplicates.

Not allowed without confirmation:
- Delete files
- Move files
- Edit outside this vault
- Access private folders
- Send external messages
- Commit calendar changes
- Make financial/legal/account actions

## Safety Rule

When uncertain, stage the change and ask for confirmation.