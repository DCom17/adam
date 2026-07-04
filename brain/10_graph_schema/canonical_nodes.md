# Canonical Nodes

This file defines all canonical node names for the [[JARVIS Command System|JARVIS vault]] knowledge graph.

The `graph_linker.py` script reads this file.

**Auto-Link Nodes** are automatically linked during a graph link run.
**Manual-Only Nodes** are listed here for reference but are NOT auto-linked. Add links to them manually when the context is clearly about the Hunter stat, not the general concept.

---

## Auto-Link Nodes

### Core System

- [[JARVIS Command System]]
- [[Shared Brain]]
- [[Claude Code]]
- [[Obsidian Vault]]
- [[Google Drive]]
- [[Google Calendar]]
- [[Phone Capture]]
- [[Daily Planning]]
- [[Daily Shutdown]]
- [[Weekly Review]]
- [[Calendar Packet Workflow]]
- [[Commit Preview]]
- [[Sign-Off Execution]]
- [[Google Calendar Bridge]]
- [[Hunter Progression Dashboard]]
- [[Hunter Tracker]]
- [[Dashboard Sync Bridge]]
- [[Apps Script Bridge]]

### Hunter System

- [[Character Level]]
- [[Rank Gate]]
- [[XP System]]
- [[Boss System]]
- [[Daily Quests]]
- [[Weekly Quests]]
- [[Main Quests]]
- [[Milestones]]
- [[Weakness Analysis]]
- [[Next Best Action]]

### Your Bosses

<!-- Bootstrap and weekly review add your long-arc goals ("bosses") here as
     canonical nodes, e.g. [[Finish My Degree]], [[Stabilize Finances]]. Add the
     same names you use in boss_tracker.md so they link across the vault. -->

### Your Projects and Domains

<!-- Add your own recurring projects/life areas, e.g. [[Work]], [[School]],
     [[Finances]], [[Fitness]]. Keep them specific enough to be unambiguous. -->

- [[School Planning]]
- [[Calendar Planning]]
- [[Financial Log]]
- [[Life Progression System]]

### Recurring Weaknesses

<!-- Weakness Analysis adds patterns it discovers about you. A few common
     starter examples are below; add/remove as your real patterns emerge. -->

- [[Overplanning]]
- [[Phone Distraction]]
- [[Task Overload]]
- [[Avoiding Hard Tasks]]

---

## Manual-Only Nodes

These nodes are NOT auto-linked because the words are too common in general English text.
Add links to these manually only when the context clearly refers to the [[Hunter Tracker]] stat.

### Stats

- [[Discipline]]
- [[Knowledge]]
- [[Health]]
- [[Finance]]
- [[Career]]
- [[Spiritual]]
- [[Social]]
- [[Execution]]

### General Hunter Concepts

- [[Fatigue]]
- [[Momentum]]

---

## How to Add a New Node

1. Add it to the correct section above.
2. If it should be auto-linked, add it under **Auto-Link Nodes**.
3. If it is a common English word, add it under **Manual-Only Nodes**.
4. If it needs alternate names, add entries to `aliases.md`.
5. Run a dry-run to confirm it links correctly before applying.
