"""
Adam — health insights tests (the expanded Today-card detail).

Synthetic fixture, reference date 2030-03-01 (so "the last 14 days" is
02-15..02-28 and today, 03-01, is never averaged):

  Targets: 2000 kcal, 150 g protein, 3000 ml water. Goal 170 lb.
  Food, 02-15..02-28: every day 2400 kcal / 100 g protein, except
        02-20 = 600 kcal (a PARTIAL day: under half the target -> skipped)
        02-21, 02-22 = nothing logged
  Today 03-01: 5000 kcal  -> must NOT move any average or status.
  Water: 1500 ml on 10 days.
  Weights: 200 lb on 02-01, 199 on 02-08, 198 on 02-15, 197 on 02-22
           -> -1.0 lb/wk, toward goal (0.5% of body weight: on pace).
  Watch: last 7 days 4000 steps, 5h sleep; 0 steps on 02-27 (not worn);
         resting HR 60 for the baseline weeks, 66 the last week.

Expected (hand-computed):
  kcal: 11 full days in the last 14 -> avg 2400 = 20% over -> lose -> "bad"
  protein: avg 100 vs 150 -> ratio .667 -> "bad", short 50 g
  water: avg 1500 vs 3000 -> 50% -> "bad"
  weight: rate -1.0/wk -> "good", projected date exists
  steps: avg 4000 (the 0-step day ignored) -> "bad"
  sleep: 300 min -> "bad"; resting HR +6 over baseline -> "watch"

Run: python test_health_insights.py (exit 0 = pass)
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import config
import health_store as hs

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def days(start: str, end: str):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_health_insights_"))
    db = sandbox / "health.db"
    config.HEALTH_DB = db
    hs.init(db)
    assert str(hs._DB_PATH).startswith(str(sandbox)), hs._DB_PATH
    import health_insights as hi

    hs.set_setting("target_kcal", 2000)
    hs.set_setting("target_protein_g", 150)
    hs.set_setting("target_water_ml", 3000)
    hs.set_setting("weight_goal", 170)
    hs.set_setting("weight_unit", "lb")
    for d in days("2030-02-15", "2030-02-28"):
        if d in ("2030-02-21", "2030-02-22"):
            continue
        if d == "2030-02-20":
            hs.add_meal(d, "Snack", kcal=600, protein_g=20)
            continue
        hs.add_meal(d, "Burrito", kcal=1400, protein_g=50, carbs_g=150, fat_g=50)
        hs.add_meal(d, "Pasta", kcal=1000, protein_g=50, carbs_g=120, fat_g=30)
    hs.add_meal("2030-03-01", "Feast", kcal=5000, protein_g=10)
    for d in list(days("2030-02-19", "2030-02-28")):
        hs.add_water(d, 1500)
    for d, w in (("2030-02-01", 200), ("2030-02-08", 199), ("2030-02-15", 198), ("2030-02-22", 197)):
        hs.add_weight(d, w, "lb")
    for d in days("2030-02-01", "2030-02-21"):
        hs.set_daily_metric(d, steps=9000, sleep_min=450, resting_hr=60, stress=30, source="garmin")
    for d in days("2030-02-22", "2030-02-28"):
        hs.set_daily_metric(d, steps=4000, sleep_min=300, resting_hr=66, stress=30, source="garmin")
    hs.set_daily_metric("2030-02-27", steps=0)

    r = hi.insights(hs, "2030-03-01")

    print("\n[1] Calories: partial + unlogged days skipped, today never averaged")
    k = r["kcal"]
    check("status bad (20% over while losing)", k["status"] == "bad")
    check("headline says 2,400 and 20% over", "2,400" in k["headline"] and "20% over" in k["headline"])
    check("partial day called out", any("partial" in f for f in k["findings"]))
    check("over-target tips present", len(k["tips"]) >= 2)
    check("series is 14 days ending today", len(k["series"]) == 14 and k["series"][-1]["date"] == "2030-03-01")
    check("today flagged in series", k["series"][-1]["today"] and k["series"][-1]["v"] == 5000)
    check("unlogged day is None, not 0", next(p for p in k["series"] if p["date"] == "2030-02-21")["v"] is None)
    check("partial day flagged", next(p for p in k["series"] if p["date"] == "2030-02-20")["partial"])

    print("\n[2] Protein")
    p = r["protein"]
    check("status bad (67% of target)", p["status"] == "bad")
    check("says 50 g short", "50 g a day short" in p["headline"])
    check("top sources listed", set(t["name"] for t in p["top"][:2]) == {"Burrito", "Pasta"})

    print("\n[3] Macros split")
    m = r["macros"]
    check("split sums ~100", abs(sum(m["split"].values()) - 100) <= 1)
    check("top foods by kcal", m["top"][0]["name"] == "Feast" or m["top"][0]["name"] == "Burrito")

    print("\n[4] Water")
    w = r["water"]
    check("status bad (50%)", w["status"] == "bad")
    check("water tips", len(w["tips"]) >= 2)

    print("\n[5] Weight")
    wt = r["weight"]
    check("rate -1.0 lb/wk", wt["rate_per_week"] == -1.0)
    check("status good (on pace toward goal)", wt["status"] == "good")
    check("projected date set", bool(wt["projected_date"]))
    check("direction lose", wt["direction"] == "lose")

    print("\n[6] Watch")
    wm = r["watch"]["metrics"]
    check("steps bad, 0-step day ignored", wm["steps"]["status"] == "bad" and "4,000" in wm["steps"]["headline"])
    check("sleep bad", wm["sleep"]["status"] == "bad")
    check("top-level sleep = the watch sleep metric", r["sleep"] is wm["sleep"])
    check("resting HR watch (+6 over baseline)", wm["resting_hr"]["status"] == "watch")
    check("body battery never graded", wm["body_battery"]["status"] in ("info", "none"))
    check("overall watch status is bad", r["watch"]["status"] == "bad")
    check("sleep no longer named in the watch headline", "sleep" not in r["watch"]["headline"])

    print("\n[7] Empty store -> 'none' everywhere, no crash")
    db2 = sandbox / "empty.db"
    config.HEALTH_DB = db2
    hs.init(db2)
    e = hi.insights(hs, "2030-03-01")
    check("all none", all(e[x]["status"] == "none" for x in ("kcal", "protein", "macros", "water", "weight"))
          and e["watch"]["status"] == "none")

    print(f"\n{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
