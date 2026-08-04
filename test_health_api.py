"""
Adam — Health Tracker API tests (Phase H1b), end to end over the real router.

Drives routers/health.py through TestClient: token gating · log weight + meals ·
water counter (unit conversion, undo, validation) · set targets · daily metric merge · /health/summary reflects logged data with
correct computed rings · trends · edit + delete meal/weight. DB redirected to a
temp sandbox; token stubbed; synthetic data. Run: python test_health_api.py.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "h" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import health_store as hs   # noqa: E402
import server               # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

AUTH = {"Authorization": "Bearer " + server.ADAM_TOKEN}
client = TestClient(server.app)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_health_api_"))
    db = sandbox / "health.db"
    config.HEALTH_DB = db
    hs.close(); hs.init(db)
    D = "2030-01-15"

    print("\n[1] Token gating")
    check("no token -> rejected", client.get("/health/summary").status_code in (401, 403))
    check("with token -> 200", client.get("/health/summary", headers=AUTH).status_code == 200)

    print("\n[2] Targets + weight + meals")
    r = client.post("/health/targets", headers=AUTH,
                    json={"target_kcal": 2200, "target_protein_g": 160, "weight_goal": 170})
    check("targets set", r.status_code == 200 and r.json()["target_kcal"] == 2200)
    client.post("/health/weight", headers=AUTH, json={"weight": 177.0, "date": D})
    r = client.post("/health/meal", headers=AUTH,
                    json={"name": "Chicken bowl", "date": D, "kcal": 550, "protein_g": 45, "carbs_g": 40, "fat_g": 18})
    meal_id = r.json()["id"]
    client.post("/health/meal", headers=AUTH,
                json={"name": "Protein shake", "date": D, "kcal": 180, "protein_g": 30, "carbs_g": 8, "fat_g": 3})
    check("meal logged", r.status_code == 200 and r.json()["name"] == "Chicken bowl")

    print("\n[3] Daily metric merge")
    client.post("/health/metric", headers=AUTH, json={"date": D, "steps": 8000})
    client.post("/health/metric", headers=AUTH, json={"date": D, "sleep_min": 430})
    dm = hs.get_daily_metric(D)
    check("metric fields merged", dm["steps"] == 8000 and dm["sleep_min"] == 430)

    print("\n[3b] Water counter (log in the user's unit, stored as ml)")
    client.post("/health/targets", headers=AUTH, json={"target_water_ml": 1892.7, "water_unit": "oz"})
    r = client.post("/health/water", headers=AUTH, json={"amount": 16, "unit": "oz", "date": D})
    wid = r.json()["id"]
    check("water logged", r.status_code == 200 and r.json()["water"]["amount"] == 16.0)
    client.post("/health/water", headers=AUTH, json={"amount": 500, "unit": "ml", "date": D})
    r = client.get("/health/waters", params={"date": D}, headers=AUTH)
    check("two drinks, newest first", len(r.json()["water"]) == 2 and r.json()["water"][0]["ml"] == 500)
    check("day total in oz (16 + 500ml)", r.json()["summary"]["amount"] == 32.9)
    check("target in oz ~64", r.json()["summary"]["target_amount"] == 64.0)
    check("zero amount rejected",
          client.post("/health/water", headers=AUTH, json={"amount": 0, "date": D}).status_code == 400)
    check("bad unit rejected",
          client.post("/health/water", headers=AUTH, json={"amount": 5, "unit": "gallon", "date": D}).status_code == 400)
    r = client.request("DELETE", "/health/water", params={"id": wid}, headers=AUTH)
    check("undo removes the drink", r.status_code == 200)
    check("delete unknown water -> 404",
          client.request("DELETE", "/health/water", params={"id": 99999}, headers=AUTH).status_code == 404)
    check("one drink left (500 ml)",
          len(client.get("/health/waters", params={"date": D}, headers=AUTH).json()["water"]) == 1)

    print("\n[4] Summary reflects logged data + computed rings")
    s = client.get("/health/summary", params={"date": D}, headers=AUTH).json()
    check("totals kcal 730", s["totals"]["kcal"] == 730)
    check("totals protein 75", s["totals"]["protein_g"] == 75)
    check("protein remaining 85", s["rings"]["protein_g"]["remaining"] == 85)
    check("kcal remaining 1470", s["rings"]["kcal"]["remaining"] == 1470)
    check("weight shown 177", s["weight"]["weight"] == 177.0)
    check("weight goal 170", s["weight_goal"] == 170)
    check("2 meals today", len(s["meals_today"]) == 2)
    check("water block in summary", s["water"]["ml"] == 500 and s["water"]["unit"] == "oz")
    check("water_today rows in summary", len(s["water_today"]) == 1)

    print("\n[5] Trends")
    t = client.get("/health/trends", headers=AUTH).json()
    check("weight trend 1 point", len(t["weight"]) == 1)
    check("intake trend 1 day kcal 730", len(t["intake"]) == 1 and t["intake"][0]["kcal"] == 730)
    check("water trend 1 day", len(t["water"]) == 1 and t["water"][0]["ml"] == 500)
    check("water target echoed in the user's unit", t["target_water_amount"] == 64.0)

    print("\n[6] Edit + delete meal")
    r = client.post(f"/health/meal/{meal_id}", headers=AUTH, json={"kcal": 600, "protein_g": 50})
    check("meal edited", r.status_code == 200 and r.json()["kcal"] == 600)
    s = client.get("/health/summary", params={"date": D}, headers=AUTH).json()
    check("totals reflect edit (kcal 780)", s["totals"]["kcal"] == 780)
    r = client.request("DELETE", "/health/meal", params={"id": meal_id}, headers=AUTH)
    check("meal deleted", r.status_code == 200)
    check("delete unknown meal -> 404",
          client.request("DELETE", "/health/meal", params={"id": 99999}, headers=AUTH).status_code == 404)
    s = client.get("/health/summary", params={"date": D}, headers=AUTH).json()
    check("one meal left (kcal 180)", s["totals"]["kcal"] == 180)

    hs.close()
    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {sandbox}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
