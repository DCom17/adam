# Inbox Processing Rules

This file defines how Claude Code processes entries in `00_inbox/phone_capture.md`.

Triggered by the command: "Process phone inbox"

---

## Core Rule

Phone capture is raw input, not confirmed truth.

Do not promote phone notes directly to long-term memory.
Do not commit calendar changes from phone captures without staging and validation.
Do not store passwords, legal documents, full financial details, or sensitive account data.

---

## Voice Capture Rule

Captures with `Source: iphone-shortcut-voice` or `Capture_Type: voice_capture` are raw dictated input.

- Voice captures may contain transcription errors (misspellings, wrong words, dropped words).
- Preserve the original text exactly as captured — do not silently correct it.
- Infer likely intent only when it is obvious (e.g., a phonetic near-miss with a clear meaning).
- If the capture is ambiguous or intent is unclear, ask for clarification. Do not guess.
- Do not promote voice captures directly to `long_term_memory.md`.
- If a voice capture implies a calendar item, stage it in `06_calendar/latest_calendar_packet.md` only. Do not commit externally.

---

## Processing Steps

When "Process phone inbox" is triggered:

1. Read `00_inbox/phone_capture.md`.
2. Identify all entries with `Status: Unprocessed`.
3. For each unprocessed entry:
   a. Read and understand the raw input.
   b. Determine the correct triage destination (see routing table below).
   c. Route the entry to the correct file.
   d. Mark the entry as `Status: Processed` with a short routing note.
4. Do not delete any capture without explicit user approval.
5. Do not process entries already marked `Status: Processed`.

---

## Triage Routing Table

| Entry type | Route to |
|---|---|
| Task or to-do | `05_tasks/active_tasks.md` |
| Waiting on someone | `05_tasks/waiting_on.md` |
| Calendar or scheduling | `06_calendar/latest_calendar_packet.md` (stage only — do not commit) |
| Calendar feedback / complaint about a committed event | `06_calendar/calendar_lessons.md` (as a durable LESSON rule — see Calendar Feedback Rule) |
| Durable fact or preference | `02_command_memory/memory_candidates.md` (not directly to long-term memory) |
| Current context or situation | `02_command_memory/active_context.md` |
| Today's event or note | Today's daily log in `03_daily_logs/` |
| Project-specific update | Relevant file in `04_projects/` |
| Structured knowledge (LLM Wiki) | `09_llm_wiki/` only if genuinely durable and encyclopedic |
| People or relationship info | `02_command_memory/people_and_relationships.md` |
| Important decision | `02_command_memory/decisions.md` |
| Unclear or ambiguous | Flag for user review — do not guess |

---

## Calendar Safety Rule

If a capture implies a *new* calendar change:
- Stage it in `06_calendar/latest_calendar_packet.md` only.
- Do not commit automatically during inbox processing.
- Notify the user that a calendar staging is ready for review.

---

## Calendar Feedback Rule

If a capture is the user complaining about a calendar event that already happened — wrong time, too tight a buffer, bad block, wrong title, missed reminder — it is **feedback, not a new event.**

- Route it to `06_calendar/calendar_lessons.md` as a durable LESSON rule, using the format in that file.
- Do not just drop it in a daily log, where it would evaporate. The whole point is that every future plan reads `calendar_lessons.md` and applies the rule, so the same mistake is not made twice.
- If the correction is ambiguous, capture the user's exact words and flag it for confirmation before writing a hard rule.

This closes the autonomous-calendar feedback loop (see CLAUDE.md → Calendar Feedback Loop Rule).

---

## Memory Safety Rule

Do not promote unconfirmed or one-off information directly into `long_term_memory.md`.

Use `memory_candidates.md` as the holding area.
The user or a future session will promote confirmed entries.

---

## Sensitive Data Rule

Refuse to store or route:
- Passwords or credentials
- Legal documents
- Full financial account details
- Private health information beyond general context

If a capture contains sensitive data, flag it and ask the user how to handle it.

---

## Hunter Evidence Rule

If a phone capture implies a completed action that may qualify for Hunter Tracker XP:
- Do not award XP during inbox processing.
- Route the capture to its normal destination (task log, daily log, etc.) as usual.
- Note in the routing summary that this capture may be evaluated for XP at daily shutdown.
- Hunter Tracker evaluation happens at shutdown, not during inbox processing.
- Only confirmed completions with documented evidence count toward XP.

---

## After Processing

After all unprocessed entries are routed:
- Report a summary: how many entries were processed, where each was routed.
- Note any entries that were flagged for user review.
- Update today's daily log with a processing summary if meaningful work was done.
