# JARVIS System Map

**Type:** Map of Content
**Status:** Active
**Last reviewed:** 2026-05-26

This MOC links the core operating components of the [[JARVIS Command System]].

Use this map to navigate how the system's pieces connect.

---

## What JARVIS Is

[[JARVIS Command System]] is a personal AI command center built on a [[Shared Brain]] vault.

The [[Shared Brain]] is a set of markdown files that persist memory, context, tasks, and plans across AI sessions. The AI model ([[Claude Code]]) may be stateless, but the vault is not.

Storage: [[Google Drive]] (synced to Windows and iPhone)
Interface: [[Obsidian Vault]] (markdown vault viewer)
Agent: [[Claude Code]] (Windows terminal agent)

---

## Core Workflows

### Capture

[[Phone Capture]] → `00_inbox/phone_capture.md` → "Process phone inbox" → routed to correct vault file

### Planning

[[Daily Planning]] → [[Calendar Packet Workflow|calendar packet]] staged → `INPUT` validation → `COMMIT PREVIEW` → "Approved" → live Google Calendar via MCP

Full workflow: [[Calendar Packet Workflow]]

### Review

[[Daily Shutdown]] → tasks closed, XP evaluated, context updated, memory candidates reviewed

[[Weekly Review]] → 7-day summary, boss progress, weakness trends, memory promotions

### Calendar Commits

[[Calendar Packet Workflow]] → [[Commit Preview]] → [[Sign-Off Execution]] → [[Google Calendar Bridge]]

Calendar **creates** commit autonomously when the user signs off a planning conversation (any natural closer). Deletions/updates never go autonomous. The [[Google Calendar Bridge]] (Google Calendar MCP) handles live event creation.
The [[Apps Script Bridge]] handles [[Dashboard Sync Bridge|dashboard sync]].

---

## Memory Architecture

| Memory Type | Location |
|---|---|
| Durable facts | `02_command_memory/long_term_memory.md` |
| Preferences | `02_command_memory/preferences.md` |
| Active context | `02_command_memory/active_context.md` |
| Decisions | `02_command_memory/decisions.md` |
| People | `02_command_memory/people_and_relationships.md` |
| Memory candidates | `02_command_memory/memory_candidates.md` |
| Daily events | `03_daily_logs/YYYY-MM-DD.md` |
| Active tasks | `05_tasks/active_tasks.md` |
| Calendar plans | `06_calendar/latest_calendar_packet.md` |

---

## Knowledge Layer

Raw sources → `08_sources/` → compiled wiki → `09_llm_wiki/`

See: [[LLM Wiki Rules]] (in `10_graph_schema/wiki_rules.md`)

Graph linking: `10_graph_schema/` → `scripts/graph_linker.py`

---

## Hunter Tracker

The gamification layer of [[JARVIS Command System|JARVIS]]. See: [[hunter_dashboard_map]]

[[Hunter Progression Dashboard]] integrates with [[Daily Planning]], [[Daily Shutdown]], and [[Weekly Review]].

---

## Relationship Triples

- [[JARVIS Command System]] -- runs_on --> [[Claude Code]]
- [[Claude Code]] -- reads_and_writes --> [[Shared Brain]]
- [[Shared Brain]] -- stored_in --> [[Google Drive]]
- [[Shared Brain]] -- viewed_in --> [[Obsidian Vault]]
- [[Phone Capture]] -- feeds --> [[Shared Brain]]
- [[Daily Planning]] -- produces --> [[Calendar Packet Workflow]]
- [[Calendar Packet Workflow]] -- commits_via --> [[Google Calendar Bridge]]
- [[Daily Shutdown]] -- updates --> [[Hunter Progression Dashboard]]
- [[Weekly Review]] -- synthesizes --> [[Shared Brain]]

---

## Related Maps

- [[hunter_dashboard_map]] — [[Hunter Tracker]] system
- [[life_operations_map]] — Life domains and boss system
