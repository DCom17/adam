# Adaptive Gap-Fill Protocol

Adam does not just schedule what the user says. On free space, it proposes what the user *should* be doing — tuned to that day's state and to the learned model in `02_command_memory/operating_patterns.md`.

This runs inside **Daily Planning Phase 1** (proposed live, user reacts) and the fills ride the normal sign-off chain onto the calendar.

---

## 1. Detect the gaps

After the day's fixed commitments and confirmed tasks are placed, find every open block between the first and last anchor of the day.

- **Fill candidates:** open blocks **≥ 45 minutes**.
- **Do NOT fill:** blocks **< 45 minutes** that sit between hard commitments — that's transition/buffer, not work time. Leave them.
- **Work hours are occupied by default.** Treat **8:00 AM–1:00 PM and 2:00–5:00 PM** on a regular work day as blocked for personal fills *even if no work event is on the calendar* — that's the standard shift. The 1–2 PM lunch is also off-limits. Personal gap-fills only land **before 8 AM, after 5 PM, or on confirmed days off / non-shift days.** This is the mirror of the existing rule that work tasks never get scheduled at home — work and personal are separate domains, and the engine respects the wall both directions.
  - *Only exception:* small admin during work hours, and only if the user explicitly says it's slow. **Summer is the busy season — do not offer at-work fills then.** When unsure whether a given day is a shift, ask one line; don't assume open space is free.
- Never overwrite or overlap a committed event.

## 2. Read the day's state

Pull from `11_dashboard/dashboard_state.json`: `fatigue` (0–100), `momentum`, weak stats, `active_boss`, open deadlines. Pull self-reported energy from the Phase 1 conversation if given (it overrides the stored number).

## 3. Pick the mode (adaptive)

| Condition | Mode | What fills the gaps |
|---|---|---|
| fatigue ≥ 60, **or** momentum Declining / Overloaded, **or** user reports drained | **Recovery** | Rest, light movement/walk, decompress, easy admin. No hard cognitive blocks. Protect sleep. |
| fatigue ≤ 40 **and** momentum Rising / Stable **and** user fresh | **Growth** | Highest-leverage first (active boss / deadline), then a neglected-stat investment (usually Health), then admin. |
| anything in between | **Balanced** | One meaningful block + one recovery/light block. Don't cram. |

## 4. Apply time-of-day rules

- **Morning gap (prime window):** give it the hardest leverage available — boss/deep work/study. Don't waste the best window on admin.
- **Evening gap before a known late cognitive block:** keep it light — dinner, decompress, short movement. Protect the block that follows.
- **Evening gap with nothing after it:** training, light study, or admin per mode.
- Never stack the hardest cognitive task immediately after a full work day unless it's the only window.

## 5. Rank fill candidates

Within the chosen mode, order by:
1. Hard deadline this week
2. Active boss milestone (your current active boss)
3. **Active experiment** — if `11_dashboard/performance_experiments.md` has a running experiment, place its intervention **only on days the engine assigned to condition A** (randomized — not every day), with an **active control** on B days where sensible (e.g. workout vs mobility). Frame neutrally ("Protocol block"), never reveal the tested lever or interim results. The experiment sits **below** deadline/boss work — it fills *remaining* space, it does not crowd out the top-priority boss work. Recovery mode overrides: never force a test block on a drained day (log it as a missed/condition day instead).
4. Neglected weak stat (currently Health / BOSS-006)
5. Standing operating preference (`operating_patterns.md`)
6. Recovery / margin

## 6. Propose, don't impose

- Present each fill in the Phase 1 conversation. One line, with the *why* ("Health's been neglected and you're fresh — training block, 10:30").
- Every gap-fill event is titled with a **`[FLEX]`** prefix so it's visibly skippable on the calendar.
- Cap proposals at ~2–3 fills/day. A day with two strong fills beats a day packed wall-to-wall and ignored.
- If the user declines a fill, note it — repeated declines of the same kind become a `RETIRED` pattern.

## 7. Commit

Approved `[FLEX]` fills go into `latest_calendar_packet.md` / `commit_preview.md` like any other event and commit on sign-off (creates only). They are real events, just tagged flexible.

---

## Feedback loop

A skipped or deleted `[FLEX]` event is signal, not failure. At shutdown, note which fills landed and which got ignored, and feed it back into `operating_patterns.md`. The engine is supposed to get sharper every week.

_Created 2026-06-17._
