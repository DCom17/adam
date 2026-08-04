"""
Adam — health metrics golden tests (Phase H1).

Synthetic fixture (reference date 2030-01-15), hand-computed:
  Meals 01-15: Chicken bowl 550/45/40/18 + Protein shake 180/30/8/3
             -> totals kcal 730, protein 75, carbs 48, fat 21, 2 meals
  Meal  01-14: 600/40/60/20   (for the intake trend's 2nd point)
  Targets: kcal 2200, protein 160 (carbs/fat deliberately unset)
  Weights: 01-01 180lb, 01-15 177lb ; goal 170lb
  Daily metric 01-15: 8500 steps, 420 min sleep

  kcal ring:    consumed 730, remaining 1470, progress 730/2200
  protein ring: consumed 75,  remaining 85,   progress 75/160
  carbs/fat rings: no target -> remaining None, progress None

No wall-clock use (date passed in). Run: python test_health_metrics.py (exit 0 = pass)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import health_store as hs
import health_metrics as hm

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def approx(a, b, tol=5e-4):
    return a is not None and abs(a - b) <= tol


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_health_metrics_"))
    db = sandbox / "health.db"
    config.HEALTH_DB = db
    hs.init(db)

    hs.add_weight("2030-01-01", 180, "lb")
    hs.add_weight("2030-01-15", 177, "lb")
    hs.add_meal("2030-01-14", "Prev day", kcal=600, protein_g=40, carbs_g=60, fat_g=20)
    hs.add_meal("2030-01-15", "Chicken bowl", kcal=550, protein_g=45, carbs_g=40, fat_g=18)
    hs.add_meal("2030-01-15", "Protein shake", kcal=180, protein_g=30, carbs_g=8, fat_g=3)
    hs.set_daily_metric("2030-01-15", steps=8500, sleep_min=420)
    hs.set_setting("target_kcal", 2200)
    hs.set_setting("target_protein_g", 160)
    hs.set_setting("weight_goal", 170)
    D = "2030-01-15"

    print("\n[1] Today totals")
    t = hm.today_totals(hs, D)
    check("kcal 730", t["kcal"] == 730)
    check("protein 75", t["protein_g"] == 75)
    check("carbs 48", t["carbs_g"] == 48)
    check("fat 21", t["fat_g"] == 21)
    check("2 meals", t["meals"] == 2)

    print("\n[2] Rings vs targets (+ no-target path)")
    ds = hm.day_summary(hs, D)
    r = ds["rings"]
    check("kcal remaining 1470", r["kcal"]["remaining"] == 1470)
    check("kcal progress ~0.3318", approx(r["kcal"]["progress"], 730 / 2200))
    check("protein remaining 85", r["protein_g"]["remaining"] == 85)
    check("protein progress ~0.4688", approx(r["protein_g"]["progress"], 75 / 160))
    check("carbs no target -> remaining None", r["carbs_g"]["remaining"] is None)
    check("carbs no target -> progress None", r["carbs_g"]["progress"] is None)

    print("\n[3] Weight (latest on/before) + goal")
    check("latest weight 177", ds["weight"]["weight"] == 177)
    check("weight date is the 15th", ds["weight"]["date"] == "2030-01-15")

    print("\n[4] Daily metric")
    check("steps 8500", ds["daily_metric"]["steps"] == 8500)
    check("sleep 420", ds["daily_metric"]["sleep_min"] == 420)

    print("\n[5] Trends")
    wt = hm.weight_trend(hs)
    check("weight trend 2 points, oldest first", len(wt) == 2 and wt[0]["date"] == "2030-01-01")
    it = hm.intake_trend(hs)
    check("intake trend 2 days", len(it) == 2 and it[0]["date"] == "2030-01-14")
    check("intake trend 15th kcal 730", it[1]["kcal"] == 730 and it[1]["protein_g"] == 75)

    print("\n[5b] Water: conversion, day total vs target, trend")
    check("8 oz -> ml", approx(hm.to_ml(8, "oz"), 236.5882365, 1e-6))
    check("1 cup -> ml", approx(hm.to_ml(1, "cup"), 236.5882365, 1e-6))
    check("ml passthrough", hm.to_ml(500, "ml") == 500)
    check("ml -> oz rounds to 1dp", hm.from_ml(946.35, "oz") == 32.0)
    check("no target -> progress None", hm.water_summary(hs, D)["progress"] is None)
    hs.add_water(D, hm.to_ml(16, "oz"))
    hs.add_water(D, hm.to_ml(8, "oz"))
    hs.add_water("2030-01-14", hm.to_ml(32, "oz"))
    hs.set_setting("target_water_ml", hm.to_ml(64, "oz"))
    w = hm.water_summary(hs, D)
    check("day total 24 oz", w["amount"] == 24.0 and w["logs"] == 2)
    check("target shown in oz", w["target_amount"] == 64.0)
    check("remaining 40 oz", approx(hm.from_ml(w["remaining_ml"], "oz"), 40.0, 0.05))
    check("progress 24/64", approx(w["progress"], 24 / 64, 1e-3))
    check("unit defaults to oz", w["unit"] == "oz")
    wt = hm.water_trend(hs)
    check("water trend 2 days, oldest first", len(wt) == 2 and wt[0]["date"] == "2030-01-14")
    check("trend carries display amount", wt[1]["amount"] == 24.0)
    check("rows carry amount + unit", hm.water_rows(hs, date=D)[0]["unit"] == "oz")
    hs.set_setting("water_unit", "ml")
    check("unit switch re-expresses the same ml", hm.water_summary(hs, D)["amount"] == 710.0)
    hs.set_setting("water_unit", "oz")

    print("\n[6] Full summary payload")
    s = hm.summary(hs, D)
    check("summary date", s["date"] == D)
    check("summary totals", s["totals"]["kcal"] == 730)
    check("summary weight goal 170", s["weight_goal"] == 170)
    check("summary weight unit lb", s["weight_unit"] == "lb")
    check("summary meals_today 2", len(s["meals_today"]) == 2)
    check("summary carries rings", s["rings"]["protein_g"]["remaining"] == 85)
    check("summary trends present", len(s["weight_trend"]) == 2 and len(s["intake_trend"]) == 2)
    check("summary carries water", s["water"]["amount"] == 24.0)
    check("summary water_today rows", len(s["water_today"]) == 2)

    print("\n[7] Empty day is safe (no meals, no targets)")
    s2 = hm.summary(hs, "2030-02-01")
    check("empty totals zero", s2["totals"]["kcal"] == 0 and s2["totals"]["meals"] == 0)
    check("empty rings target-None safe", s2["rings"]["kcal"]["progress"] in (None, 0.0))
    check("empty water zero", s2["water"]["ml"] == 0 and s2["water"]["logs"] == 0)

    hs.close()
    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {sandbox}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
