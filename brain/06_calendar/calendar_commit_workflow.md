# Calendar Commit Workflow

Triggered by: **user sign-off** at the end of a planning conversation (any natural closer — no fixed phrase). See CLAUDE.md → Sign-Off Execution Rule.

Purpose:
Create Google Calendar events from the approved [[JARVIS Command System|JARVIS]] [[Calendar Packet Workflow|calendar packet]] by emitting a `calendar.create` ACTION block. The server sends it to the user's own [[Google Calendar Bridge|Calendar add-on]]. **You never run a script and never touch Google Calendar directly — read `INTEGRATION_EXECUTION.md` for the exact mechanism.**

Mode:
Create-only. The product's connector has **no delete** and does not auto-update at sign-off — there is nothing to delete-gate. A one-off edit is a deliberate `calendar.update` block you stage only when the user asks.

Required files:
- `06_calendar/latest_calendar_packet.md`
- `06_calendar/commit_preview.md`
- `06_calendar/calendar_lessons.md`
- `06_calendar/commit_log.md`

(There is no `calendar_sync.json` / `calendar_sync_result.json` anymore — that was the old script path. The events go straight into the ACTION block.)

Rules:
- Never commit from messy planning — validate the packet first.
- Read `06_calendar/calendar_lessons.md` and apply every standing lesson before building events.
- A validated packet and commit preview must exist before committing.
- **Confirm the Calendar add-on is enabled** (the live ADD-ONS note). If it isn't, do not emit a block — keep the day staged in `latest_calendar_packet.md` and tell the user to connect Calendar in Settings → Add-ons.
- Commit only events listed in the approved preview.
- Create events only. Do not update existing events. Do not delete events. Do not invite guests. Do not create recurring events.
- Do not create events longer than 12 hours. Do not create more than 25 events in one block.
- If the date, time, timezone, title, or duration is genuinely unclear, hold that event out and note it in the summary rather than guessing — do not block the whole commit.
- Log created event titles and start/end times in `06_calendar/commit_log.md`. (Event IDs come back from the server only after the action executes; record them if reported.)
- **Honesty:** report the create as **staged for approval** by default (it waits for the user's on-screen tap). Only if the live EXTERNAL ACTIONS note says calendar auto-run is ON may you confirm it as done. Never claim it committed otherwise.
- Leave the user a summary of what was staged/committed and anything held out.

## Emit the events like this

Each marker on its own line; the JSON body on one line. The server extracts it automatically.

```
<<ACTION type="calendar.create" summary="Add today's planned events">>
{ "events": [
  { "title": "Study block", "start": "2026-07-04T14:00:00", "end": "2026-07-04T15:30:00", "location": "", "description": "Created from JARVIS calendar packet." }
] }
<<END_ACTION>>
```

Per-event fields: `title`, `start`, `end` (required), plus optional `location`,
`description`, `calendar_id`. Use the user's local date/time; assume the current year if
unstated.
