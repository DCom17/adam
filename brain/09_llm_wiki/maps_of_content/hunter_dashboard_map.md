# Hunter Dashboard Map

**Type:** Map of Content
**Status:** Active
**Last reviewed:** 2026-05-26

This MOC links all components of the [[Hunter Progression Dashboard]].

Use this map to navigate the gamification layer of the [[JARVIS Command System]].

---

## What the Hunter System Is

The [[Hunter Progression Dashboard]] is a gamified progression tracker layered on top of [[JARVIS Command System|JARVIS]] operations.

It turns real-world actions into stats, XP, quests, and [[Milestones|boss milestones]]. The core idea: doing the actual work gives measurable, visible progress.

Vault layer: `11_dashboard/`
Live view: Google Sheets (visual Dashboard and Mobile_Dashboard tabs)
Sync: [[Dashboard Sync Bridge]] via [[Apps Script Bridge]]

---

## Progression Architecture

### Character Progression

[[Character Level]] — overall level derived from total XP across all stats

[[Rank Gate]] — threshold gates between ranks (E → D → C → B → A → S)
Rank promotion requires explicit user confirmation. Never auto-promoted.

[[XP System]] — XP earned by completing quests and milestones
- Routine XP: up to 60/day total, 18/stat/day
- Micro-action XP: up to 6/day
- Milestone XP: varies by boss milestone
- XP is only awarded with confirmed evidence

---

## Stats

Eight stats track real-world domains:

| Stat | Domain |
|---|---|
| [[Discipline]] | Routines, habits, consistency |
| [[Knowledge]] | Study, learning, coursework |
| [[Health]] | Exercise, sleep, physical wellbeing |
| [[Finance]] | Budgeting, saving, financial tracking |
| [[Career]] | Work performance, professional growth |
| [[Spiritual]] | Reflection, mindfulness, values |
| [[Social]] | Relationships, communication |
| [[Execution]] | Project completion, follow-through |

Stats level up independently based on XP in that domain.
See: `11_dashboard/stat_definitions.md` for evidence criteria.

---

## Quest System

### Quest Eligibility Filter

Before any action becomes a quest, it must pass at least one gate:
- **Stat gate:** action meets qualifying evidence in `stat_definitions.md`
- **Boss gate:** action directly advances a confirmed boss milestone

Failing both gates → task only. No quest ID, no XP potential.

### Quest Types

[[Daily Quests]] — 3–5 recommended during [[Daily Planning]]. Reset each day.

[[Weekly Quests]] — longer-scope objectives spanning the full week.

[[Main Quests]] — tied to active [[Boss System|boss]] battles. Long-term objectives.

---

## Boss System

Bosses are major life objectives. Each has milestones, difficulty ratings, and reward XP.

### Active Bosses

<!-- Bootstrap adds your long-arc goals here as bosses, e.g.:
- [[Finish My Degree]] — complete the program
- [[Stabilize Finances]] — establish financial stability and savings
- [[Build Daily Discipline System]] — establish consistent daily routines
- [[Improve Health/Fitness]] — reach and maintain fitness goals
-->

See: `11_dashboard/boss_tracker.md` for milestones and progress.

---

## Analysis and Tracking

[[Weakness Analysis]] — patterns of repeated failure or avoidance

Common starter weaknesses (yours emerge from real evidence over time):
- [[Overplanning]]
- [[Phone Distraction]]
- [[Task Overload]]
- [[Avoiding Hard Tasks]]

[[Fatigue]] — high fatigue flag reduces quest recommendations

[[Momentum]] — streak bonuses for consistent action

[[Next Best Action]] — highest-leverage action recommended by the system

---

## Data Files

| File | Purpose |
|---|---|
| `dashboard_state.json` | Current level, XP totals, stat levels |
| `xp_log.csv` | All XP transactions with evidence |
| `quest_log.csv` | Quest completions with IDs |
| `weakness_log.csv` | Weakness entries with dates |
| (sync payload) | Built from `dashboard_state.json` at sync time, delivered via a `hunter.sync` ACTION block |

---

## Integration Points

[[Daily Planning]] → reads dashboard state → generates [[Daily Quests]]

[[Daily Shutdown]] → evaluates completed actions → awards XP → updates dashboard state

[[Weekly Review]] → reviews boss progress, weakness trends, [[Rank Gate|rank gate]] status

---

## Relationship Triples

- [[Hunter Progression Dashboard]] -- is_part_of --> [[JARVIS Command System]]
- [[Hunter Tracker]] -- tracks --> [[Character Level]]
- [[XP System]] -- advances --> [[Character Level]]
- [[Rank Gate]] -- gates --> [[Character Level]]
- [[Daily Quests]] -- earns --> [[XP System]]
- [[Boss System]] -- defines --> [[Main Quests]]
- [[Weakness Analysis]] -- informs --> [[Next Best Action]]
- [[Dashboard Sync Bridge]] -- syncs --> [[Hunter Progression Dashboard]]

---

## Related Maps

- [[jarvis_system_map]] — [[JARVIS Command System|JARVIS]] core system
