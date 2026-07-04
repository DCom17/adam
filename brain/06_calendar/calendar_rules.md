# Calendar Rules

Google Calendar is the schedule source of truth.

The markdown vault is the planning and validation layer.

## Core Rule

Never commit calendar changes directly from messy planning notes.

All [[Calendar Planning|calendar planning]] must pass through:

Messy input → "Stage this for input" → "INPUT" → "[[Commit Preview|COMMIT PREVIEW]]" → "COMMIT" after approval

## Live Commit Approval Phrase

The only phrase that authorizes live Google Calendar creation or update is:

"Approved"

The only phrase that authorizes live Google Calendar deletion is:

"Live Google Calendar deletion is approved."

If the user says "commit" without the live approval phrase, create a dry-run [[Commit Preview|commit preview]] only.

## Command Definitions

### Stage this for input

When the user says "Stage this for input":

1. Extract fixed commitments.
2. Extract flexible tasks.
3. Identify dates and timezone.
4. Estimate durations if not provided.
5. Add buffers and transitions.
6. Create or update `06_calendar/latest_calendar_packet.md`.
7. Do not touch Google Calendar.
8. Do not create a commit log entry yet.

### INPUT

When the user says "INPUT":

1. Read `06_calendar/latest_calendar_packet.md`.
2. Validate the schedule.
3. Check for overlaps.
4. Check whether durations are realistic.
5. Check whether fixed commitments are preserved.
6. Check for missing travel, meals, rest, and transition time.
7. Identify unclear tasks or missing information.
8. Update the validation checklist.
9. Set commit readiness to one of:
   - Not ready
   - Ready for preview
   - Ready for commit after approval
10. Do not touch Google Calendar.

### COMMIT PREVIEW

When the user says "[[Commit Preview|COMMIT PREVIEW]]":

1. Read the latest validated packet.
2. Write the proposed external calendar actions to `06_calendar/commit_preview.md`.
3. Do not touch Google Calendar.
4. Make the preview clear enough for the user to approve or reject.

### COMMIT

When the user says "COMMIT":

1. Confirm that the packet says `Ready for commit after approval`.
2. Confirm that user approval is explicit.
3. If Google Calendar access is available, perform the approved calendar actions.
4. If Google Calendar access is not available, provide copy-ready event blocks.
5. Log committed actions in `06_calendar/commit_log.md`.
6. Update today's daily log.
7. Archive the committed packet in `06_calendar/packet_archive/`.

## Integration Model

JARVIS handles staging and validation in the vault, then pushes calendar **creates**
through the product's action lane — it never touches Google Calendar directly and never
runs a script. Full mechanism: `INTEGRATION_EXECUTION.md`.

- Staging: creates and updates `latest_calendar_packet.md`
- Validation: runs INPUT checks
- Preview: writes proposed actions to `commit_preview.md`
- Commit: emits a `calendar.create` ACTION block; the server sends it to the user's own
  Calendar add-on after the user approves it on screen (or immediately, if they've turned
  calendar auto-run on). There is no MCP and no `.ps1` here.

## Calendar Commit Workflow

JARVIS commits to Google Calendar only through a `calendar.create` block, and only when
the **Calendar add-on is connected** (Settings → Add-ons). If it isn't connected, the
plan stays staged in the packet and JARVIS tells the user to set it up.

1. JARVIS stages the plan in `latest_calendar_packet.md`.
2. JARVIS validates the plan with `INPUT`.
3. JARVIS writes the proposed actions to `commit_preview.md`.
4. On sign-off, JARVIS emits a `calendar.create` block for the approved events.
5. The user approves it on screen (default) and the server creates the events — or, with
   auto-run on, it runs immediately.
6. JARVIS records the result in `commit_log.md`, updates today's daily log, and archives the packet.

No event is considered committed until the server confirms it (or it auto-ran). Until
then it is staged for approval, not done.

## Validation Requirements

Before any external calendar action, verify:

- Correct date
- Correct timezone
- No overlaps
- Fixed commitments preserved
- Realistic task durations
- Travel/buffer included
- Reminder settings included
- Specific event titles
- Calendar destination listed
- User approval received

## Default Reminder Rules

Default reminder: 10 minutes before.

Use stronger reminders when appropriate:

- Work shift: 30-60 minutes before
- Appointment: 30-60 minutes before
- Travel event: reminder before departure
- School test/deadline: 1 day before and 1 hour before if useful
- Important personal task: 30 minutes before

## Full Day Staging Rule

Every daily plan must be staged as a complete day in Google Calendar. Do not assume any block is already on the calendar or too small to include.

Always include:
- Work shifts (full blocks, both halves if split)
- All time-specific action items — even short tasks like "Send team message at 8:05 AM" if they are time-sensitive
- Admin windows (calls, banking, ordering)
- Lunch and meal blocks
- Errands
- Evening tasks (dishes, chores)
- Study or project blocks
- Any personal obligation with a defined time

**Time-sensitive action items must be calendar events with popup reminders.** If a task has a specific required time (e.g., "Send team message at 8:05 AM"), create it as a calendar event with a reminder so the user is notified. Do not leave these as notes inside other events.

## Safety Rules

Never create vague calendar events like:
- Work on stuff
- Do things
- Study
- Tasks

Prefer specific titles:
- Study: Chapter 4 Notes + Recall Drill
- Weekly Inventory Count
- Draft Staff Reminder
- Assignment Review Block

Do not schedule every minute of the day unless the user asks.

Protect sleep, meals, transition time, and realistic shutdown time.