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

```
BaseStats = 0.60 × AVERAGE(all 8 stat levels)
          + 0.40 × AVERAGE(lowest 4 stat levels)

BossBonus = MIN(10, SUM(cleared_boss_difficulty) × 0.8)

CandidateLevel = FLOOR(BaseStats + BossBonus + ConsistencyBonus)

CharacterLevel = MIN(
  CandidateLevel,
  FLOOR(AVERAGE(all 8 stats) + 12),
  MIN(all 8 stats) + 20
)
```

The third cap — `MIN(all 8 stats) + 20` — prevents a single neglected stat from hiding behind others.
A stat at level 1 with all others at 50 caps [[Character Level|character level]] at 21.

---

## Rank Review Process

1. Claude flags when all gate conditions appear met
2. User reviews the flag and confirms or defers
3. If confirmed, `dashboard_state.json` is updated with new rank and date
4. Weekly review records the rank change in `07_reviews/weekly_review.md`
5. Rank is never rolled back unless the user requests it
