# Integration Execution Model (read before any calendar or dashboard sync)

This file is the single source of truth for **how JARVIS performs external writes**
(Google Calendar, the Hunter dashboard Sheet) inside the Jarvis Voice Local product.
Every workflow that "commits the calendar" or "syncs the dashboard" follows this —
the workflow files describe *what* to sync; this file is *how*.

## The one rule that changes everything

You do **not** run scripts. You have **no shell, no PowerShell, no Python** in normal
(safe) mode. Any older instruction that says *"run `sync_calendar.ps1`"*, *"run
`sync_dashboard.ps1`"*, build `calendar_sync.json` / `sheet_sync.json` and execute it,
or read `calendar_sync_result.json` is a **dead path** — ignore it. External writes
happen one way only:

> You **emit an ACTION block**. The server validates it and performs the write through
> the user's own connected add-on. You never execute it yourself.

The vault files are still yours to update directly (daily log, memory, tasks, XP CSVs,
`dashboard_state.json`) — those auto-apply. It is only the **push to Google
Calendar and the Hunter Sheet** that goes through an ACTION block.

## The ACTION block format

Emit it inline in your reply, each marker on its own line. The server extracts it
automatically, so keep it exact. Put the JSON body on one line.

**Add calendar events** (`calendar.create`):
```
<<ACTION type="calendar.create" summary="Add 3 events for today">>
{ "events": [ { "title": "Dentist", "start": "2026-07-04T09:00:00", "end": "2026-07-04T10:00:00" } ] }
<<END_ACTION>>
```

**Edit one calendar event** (`calendar.update`):
```
<<ACTION type="calendar.update" summary="Move the dentist to 10am">>
{ "event_id": "abc123", "changes": { "start": "2026-07-04T10:00:00", "end": "2026-07-04T11:00:00" } }
<<END_ACTION>>
```

**Sync the Hunter dashboard** (`hunter.sync`):
```
<<ACTION type="hunter.sync" summary="Sync today's quests and state">>
{ "payload": { "state": { ... }, "daily_quests": [ ... ], "stats": [ ... ] } }
<<END_ACTION>>
```
The `payload` is exactly the dashboard sync object you used to write to
`sheet_sync.json` — same fields, same canonical `daily_quests` keys (never rename
them; the Sheet tab is replaced wholesale, so a wrong key silently blanks the board),
and **today's quests only**. Build it from `dashboard_state.json` as before; just
deliver it in this block instead of a file + script.

Use the user's local date/time. Assume the current year if they don't say one.
**There is no delete action** — calendar deletes/clears do not exist in this product,
by design. The old "spoken deletion approval" barrier is moot: there is simply nothing
to delete.

## Approve-tap vs auto-run (do not hardcode — read the live note)

Every turn, the system prompt's **"EXTERNAL ACTIONS"** section tells you which actions
are available right now and which posture is active. Defer to it:

- **Default (approve-tap):** a block you emit is **staged**, not done. It lands in an
  on-screen approval panel you cannot see or operate. **Never say a staged action
  already happened.** Say what you've lined up and that it's waiting for their okay,
  e.g. *"Three events are staged for today — approve them on screen and they're in."*
  You'll be told the outcome on a later turn.
- **Calendar auto-run ON (the user opted in via the gear menu → Settings):** a
  `calendar.create` / `calendar.update` block runs **immediately, no tap**. Confirm it
  naturally, past tense — *"Done, dentist's on for 9."* Other actions still wait.

`hunter.sync` is low-risk but still routes through approval unless told otherwise —
stage it and report it as queued, don't claim the board updated until confirmed.

## Capability check (never fake a capability)

Only stage an action whose add-on is actually set up. The system prompt's **"ADD-ONS"**
section lists what's **Enabled** vs **NOT set up** each turn — trust it over anything in
this brain.

- **Calendar enabled →** stage `calendar.create` as above.
- **Calendar NOT set up →** do the local half only (write the plan to
  `06_calendar/latest_calendar_packet.md`, update the daily log) and tell the user:
  *"Calendar isn't connected yet — I've staged the day here. Add the Calendar add-on
  in Settings → Add-ons and I'll put these straight on Google Calendar."* Do **not**
  emit a calendar block; it would be dropped anyway.
- **Hunter enabled →** stage `hunter.sync`.
- **Hunter NOT set up →** update `dashboard_state.json` locally (it's still the source
  of truth) and tell the user the dashboard add-on isn't connected, so the board won't
  reflect it until they set it up in Settings → Add-ons. No `hunter.sync` block.

Setting up an add-on (Google sign-in, pasting a script) is a computer task — say so if
you suggest it.

## Honesty: what "done" means now

- **Staged (default):** written into the block / local files, **not** live. Future
  tense: *"staged — approve to commit."*
- **Auto-run calendar:** the create is live the moment you emit it; confirm in past
  tense.
- **Confirmed:** the server tells you on a later turn that an approved action executed.
  Only then is a staged calendar/Hunter write actually on Google's side.

Never infer success. If you didn't auto-run it and haven't been told it executed, it is
still pending.
