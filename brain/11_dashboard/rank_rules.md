# Rank Rules

Rank reflects overall mastery across all stats and bosses.
Rank advancement requires meeting all gate conditions AND explicit user confirmation.
Claude may flag when gates are met, but never promotes rank automatically.

---

## Rank Tiers

| Rank | [[Character Level]] | Meaning |
|---|---|---|
| E | 1–9 | Unawakened — system installed, habits not yet proven |
| D | 10–24 | Awakening — early consistent progress visible |
| C | 25–39 | Hunter — real competence emerging across stats |
| B | 40–59 | Advanced Hunter — serious and sustained progress |
| A | 60–74 | Elite Hunter — high-level real-world capability |
| S | 75–89 | Shadow-Class Hunter — mastery across most domains |
| National | 90+ | National Level — exceptional, evidence-backed dominance |

---

## Rank Gates

### E → D (Rank Up to D)
- [[Character Level]] ≥ 10
- No stat below level 7
- At least 1 boss milestone confirmed
- User confirms rank-up

### D → C (Rank Up to C)
- [[Character Level]] ≥ 25
- No stat below level 18
- At least 3 [[Milestones|boss milestones]] confirmed OR 1 boss cleared
- User confirms rank-up

### C → B (Rank Up to B)
- [[Character Level]] ≥ 40
- No stat below level 32
- At least 1 boss cleared + 3 additional confirmed milestones, OR 2 bosses cleared
- User confirms rank-up

### B → A (Rank Up to A)
- [[Character Level]] ≥ 60
- No stat below level 48
- At least 2 bosses cleared
- Strong evidence of sustained real-world performance
- User confirms rank-up

### A → S (Rank Up to S)
- [[Character Level]] ≥ 75
- No stat below level 62
- At least 3 bosses cleared
- Demonstrated sustained performance for multiple months
- User confirms rank-up

### S → National (Rank Up to National)
- [[Character Level]] ≥ 90
- No stat below level 78
- At least 5 bosses cleared
- Exceptional real-world evidence across all major domains
- User confirms rank-up

---

## Character Level Formula

[[Character Level|Character level]] is derived from `total_xp` against the single
curve in `xp_rules.md` — the same curve the stats use. There is no second formula:

```
total_xp      = SUM(cumulative XP across all 8 stats)
CharacterLevel = the largest L where Cumulative(L) ≤ total_xp
                 Cumulative(1) = 0,  Cumulative(L) = 50 × (L(L+1)/2 − 1)
xp_to_next     = Cumulative(CharacterLevel + 1) − total_xp
```

Recompute both on every update, straight from `total_xp`. A single large award —
a boss clear, a milestone — can cross several levels at once; resolve all of them.
Never carry the previous level forward and never advance it by one per award.

**Balance is enforced by the rank gates above, not by the level.** Level says how
much confirmed evidence has accumulated; the "no stat below N" gate is what stops a
neglected stat from riding along. (An older revision computed character level as a
capped composite of stat levels. That produced a level the board and the assistant
disagreed about — the composite on the Sheet, the curve in the vault. The curve wins.)

---

## Rank Review Process

1. Claude flags when all gate conditions appear met
2. User reviews the flag and confirms or defers
3. If confirmed, `dashboard_state.json` is updated with new rank and date
4. Weekly review records the rank change in `07_reviews/weekly_review.md`
5. Rank is never rolled back unless the user requests it
