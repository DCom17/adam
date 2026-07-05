# Hunter Rules

The [[Hunter Tracker]] is the gamified progression layer of the [[Adam Command System|Adam system]].
It tracks real-world personal development using stats, levels, ranks, and bosses.

## Core Principle

Progression must reflect real-world evidence, not just activity.
XP is a signal of genuine effort and results — not a reward for showing up.

---

## What Claude Can Do

- Read `dashboard_state.json` and recommend 3–5 [[Daily Quests|daily quests]]
- Log XP entries when evidence is confirmed
- Flag weak stats and detected patterns
- Track boss milestone progress
- Recommend rank review when all gate conditions are met
- Update `xp_log.csv`, `quest_log.csv`, `weakness_log.csv`
- Update `dashboard_state.json` (the single source of truth)
- Push the board with a `hunter.sync` ACTION block when the Hunter add-on is connected

## What Claude Cannot Do

- Award XP without confirmed evidence from the user or daily log
- Promote rank without explicit user confirmation
- Clear a boss without confirmed milestone completion
- Invent progress that was not stated or confirmed
- Write to Google Sheets directly or run a script — pushes go ONLY through a `hunter.sync` block the user approves (see `INTEGRATION_EXECUTION.md`); if the Hunter add-on isn't connected, the board simply isn't updated
- Access Google Calendar except through a `calendar.create` / `calendar.update` block

---

## Source of Truth

The vault is the reasoning source of truth (`dashboard_state.json` specifically).
Google Sheets is the visual/mobile dashboard layer, updated via a `hunter.sync` ACTION
block through the user's own [[Apps Script Bridge|Apps Script]] bridge — only when the
Hunter add-on is connected, and only after the user approves the push.

All updates flow through structured files in `11_dashboard/` first, then out via the block.

---

## Integration Points

| Workflow | Hunter Action |
|---|---|
| Daily planning | Read state, recommend 3–5 quests before staging the packet |
| Daily shutdown | Evaluate evidence, log XP, update quests and state |
| Weekly review | Update boss progress, weakness trends, [[Rank Gate|rank gates]], [[Weekly Quests|weekly quests]] |
| Phone inbox | Route captures to quest_log or weakness_log when relevant |

---

## File Map

| File | Purpose |
|---|---|
| `hunter_rules.md` | System rules and operating guide (this file) |
| `stat_definitions.md` | What each stat means and what counts as evidence |
| `xp_rules.md` | XP formula, caps, categories, evidence requirements |
| `rank_rules.md` | Rank tiers, gates, and promotion requirements |
| `boss_tracker.md` | Active and cleared bosses with milestone progress |
| `dashboard_state.json` | Current character state — primary read/write target |
| `xp_log.csv` | Running log of all awarded XP entries |
| `quest_log.csv` | Daily and weekly quest records |
| `weakness_log.csv` | Detected weaknesses and countermeasures |
| `dashboard_update_workflow.md` | Step-by-step update procedure (incl. the `hunter.sync` block) |
| `monthly_summaries/` | Monthly snapshot archives |
