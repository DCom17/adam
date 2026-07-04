# XP Rules

---

## XP Formula

XP required to advance from level L to level L+1:

```
XP_to_next(L) = 50 × (L + 1)
```

Each level costs 50 more XP than the previous. Early levels are achievable but not trivial. High levels require sustained long-term evidence.

Cumulative XP to reach level L:
```
Cumulative(L) = 50 × (L(L+1)/2 − 1)    [for L ≥ 2]
Cumulative(1) = 0
```

Reference table:

| Level | XP to Next | Cumulative XP to Reach This Level |
|---|---|---|
| 1 | 100 | 0 |
| 2 | 150 | 100 |
| 3 | 200 | 250 |
| 4 | 250 | 450 |
| 5 | 300 | 700 |
| 6 | 350 | 1,000 |
| 7 | 400 | 1,350 |
| 8 | 450 | 1,750 |
| 9 | 500 | 2,200 |
| 10 | 550 | 2,700 |
| 11 | 600 | 3,250 |
| 12 | 650 | 3,850 |
| 13 | 700 | 4,500 |
| 14 | 750 | 5,200 |
| 15 | 800 | 5,950 |
| 20 | 1,050 | 10,450 |
| 25 | 1,300 | 16,200 |
| 30 | 1,550 | 23,200 |
| 40 | 2,050 | 41,150 |
| 50 | 2,550 | 64,900 |

---

## Level Meaning

| Level Range | Meaning |
|---|---|
| 1–9 | Beginner |
| 10–24 | Developing |
| 25–39 | Consistent novice / early intermediate |
| 40–59 | Competent |
| 60–74 | Advanced |
| 75–89 | Highly capable |
| 90+ | True mastery / high-level evidence required |

---

## XP Caps

| Category | Cap |
|---|---|
| Max routine XP per day (all stats combined) | 60 |
| Max routine XP per stat per day | 18 |
| Max repeatable micro-action XP per day | 6 |
| Milestone XP | Uncapped — requires evidence |
| Boss reward XP | Uncapped — requires explicit confirmation |

---

## XP Categories

| Category | Description | Cap |
|---|---|---|
| routine | Daily habits, consistent behaviors | 18/stat/day |
| milestone | Completing a defined checkpoint | None — evidence required |
| boss_reward | Clearing or hitting a boss milestone | None — confirmation required |
| project | Meaningful output on a project | Moderate — logged per event |
| session | Single focused work or study session | Moderate — logged per event |
| micro | Small supportive actions | 6/day |

---

## Milestone XP Tiers

Reference values for major life milestones (revamp 2026-06-13). These bypass the
routine daily caps and require strong evidence. They are intentionally larger than
routine awards so real achievements move the character level meaningfully — but
they stay calibrated to the existing curve (a degree should feel like a boss kill,
not catapult you ten levels in one event).

| Milestone | XP | Notes |
|---|---:|---|
| Exam / gate assessment passed | 100 | Allocated mostly Knowledge, some Execution |
| Course completed | 150 | Per course, as completed |
| Major certification earned | 400 | External credential |
| Full degree / program completed | 1,200 | Boss-clear bonus for the relevant education boss, **on top of** the per-course/exam XP earned along the way |

These accumulate: finishing the degree pays per-course (150 each) and per-OA (100)
XP as you go, then a 1,200 clear bonus at the end. Over the full degree that totals
into the thousands organically — without any single event except the final clear
causing a large spike. Other major life milestones (job offer, promotion, debt
payoff, property) are sized by judgment in the same hundreds-to-low-thousands band
and logged as `milestone` with evidence.

---

## Additivity Invariant

The XP log is the single source of truth. Two equalities must always hold:

```
character total_xp = SUM(approved xp_log rows)
character total_xp = SUM(cumulative stat XP across all 8 stats)
```

Note the dashboard displays each stat bar as **progress within its current level**,
not cumulative XP. A stat at level 2 has already spent 100 XP reaching L2, so its
cumulative XP = 100 + the displayed bar value. The `hunter.sync` payload should carry
cumulative `xp_total` per stat; `dashboard_state.json` stores the per-level bar value.
When building the payload, convert to cumulative before summing.

If these equalities break, XP is being double-counted or dropped — investigate the
log before trusting the total. (Baseline reconciled 2026-06-13: stored total had
drifted to 645; the log summed to 651, so 651 is correct.)

---

## Evidence Requirements

Claude must have at least one of the following before awarding XP:

1. User explicitly confirms the action was completed
2. Daily log contains a clear record of completion
3. Task moved from active_tasks.md to completed_log.md
4. Boss milestone was explicitly confirmed by the user

Claude must NOT:
- Award XP based on plans or intentions
- Assume XP from a previous session's context
- Award milestone or boss XP without direct confirmation
- Invent evidence to justify a level-up

---

## Fatigue Mechanic

Fatigue (0–100): Builds when daily XP consistently hits the routine cap for multiple days.
Above 70 fatigue: routine XP awards are halved until rest or low-activity day occurs.
Fatigue resets by 20 per confirmed rest day.

---

## Momentum Mechanic

Momentum is a readable label assessed during daily planning and shutdown. Use one of:

| Label | Meaning |
|---|---|
| Rising | Completed quests, streak improvement, or meaningful recent progress |
| Stable | Some progress, no major drift |
| Declining | Missed important blocks, repeated deferrals, weak follow-through |
| Recovery | High fatigue or intentional reset day |
| Overloaded | Too many active demands for available time/energy |

Never use a number for momentum.

---

## Consistency Bonus (for Character Level formula)

| Active Days in Past 7 | Consistency Bonus |
|---|---|
| 0–2 | 0 |
| 3–5 | 1 |
| 6–7 | 2 |
| 14+ consecutive active days | 3 |

Reviewed and set during [[Weekly Review|weekly review]]. Not auto-calculated.
