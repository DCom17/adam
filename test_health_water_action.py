"""
Adam — logging water and food by voice (health.water / health.meal actions).

Regression for the 2026-09-29 owner report: "log 16 ounces of water" was
confirmed by Adam but the Health tracker still showed 0. There was no write
path from chat into the Health tracker at all; the agent reached for
hunter.sync and put "Water intake: 600 mL" into the quest board's XP log, then
claimed a correction it never made. Proves:

  * health.water is registered, local (always available) and brain-proposable,
    and the prompt tells the agent to use it (and never hunter.sync) for water;
  * the executor converts oz / ml / cup (and spoken aliases) to ml, writes the
    SAME table the Health page reads, and returns the day's total;
  * replace_last corrects the latest drink instead of doubling it;
  * bad input (no amount, unknown unit, absurd amount, bad date) is refused;
  * end to end: an <<ACTION type="health.water">> block in a reply is staged
    and auto-run (no approval tap), and the Health API then shows the water.

Run:  python test_health_water_action.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "w" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_water_action_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"
config.HEALTH_DB = _SANDBOX / "health.db"
config.AUDIT_LOG_FILE = _SANDBOX / "audit.jsonl"   # never append test rows to the live audit log

import approvals                    # noqa: E402
import external_actions             # noqa: E402
import health_metrics               # noqa: E402
import health_store                 # noqa: E402
import job_store                    # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")
health_store.init(config.HEALTH_DB)
client = TestClient(server.app)
AUTH = {"Authorization": "Bearer " + server.ADAM_TOKEN}
DAY = "2026-09-29"

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def refused(payload: dict) -> bool:
    try:
        external_actions.execute("health.water", payload)
    except external_actions.ActionError:
        return True
    return False


def main() -> int:
    assert str(Path(health_store._DB_PATH)).startswith(str(_SANDBOX)), "not sandboxed"

    print("registry + prompt")
    check("health.water is known", external_actions.is_known("health.water"))
    check("health.water is brain-proposable", external_actions.brain_proposable("health.water"))
    note = server._action_proposal_note()
    check("prompt advertises health.water", 'type="health.water"' in note)
    check("prompt forbids water via hunter.sync", "never into hunter.sync" in note.replace("\n", " ")
          or "never into \"hunter.sync\"" in note)
    check("prompt explains replace_last", '"replace_last": true' in note)

    print("executor")
    r = external_actions.execute("health.water", {"amount": 16, "unit": "oz", "date": DAY})
    check("16 oz stored as ml", abs(r["logged_ml"] - health_metrics.to_ml(16, "oz")) < 0.2, str(r))
    rows = health_store.list_water(date=DAY)
    check("one row in the Health water table", len(rows) == 1, str(rows))
    r = external_actions.execute("health.water", {"amount": 20, "unit": "ounces", "date": DAY,
                                                  "replace_last": True})
    rows = health_store.list_water(date=DAY)
    check("replace_last corrects instead of adding", len(rows) == 1, str(rows))
    check("replace_last wrote the new amount",
          abs(rows[0]["ml"] - health_metrics.to_ml(20, "oz")) < 0.2, str(rows))
    check("replace_last reports the old amount", r["replaced_ml"] is not None, str(r))
    external_actions.execute("health.water", {"amount": 250, "unit": "milliliters", "date": DAY})
    external_actions.execute("health.water", {"amount": 1, "unit": "cups", "date": DAY})
    check("ml + cup aliases add rows", len(health_store.list_water(date=DAY)) == 3)
    check("replace_last with nothing today just adds",
          external_actions.execute("health.water", {"amount": 8, "unit": "oz", "date": "2026-09-01",
                                                    "replace_last": True})["replaced_ml"] is None)
    check("no amount refused", refused({"unit": "oz"}))
    check("unknown unit refused", refused({"amount": 2, "unit": "gallons"}))
    check("zero refused", refused({"amount": 0, "unit": "oz"}))
    check("absurd amount refused", refused({"amount": 900, "unit": "oz"}))
    check("bad date refused", refused({"amount": 8, "unit": "oz", "date": "yesterday"}))

    print("end to end: reply block -> auto-run -> Health API")
    reply = ('Logged 16 ounces.\n<<ACTION type="health.water" summary="Log 16 oz water">>\n'
             '{ "amount": 16, "unit": "oz", "date": "2026-09-30" }\n<<END_ACTION>>')
    display, actions = server._extract_actions(reply)
    check("block is stripped from the reply", "<<ACTION" not in display and "Logged 16" in display)
    check("one action staged", len(actions) == 1 and actions[0]["action_type"] == "health.water")
    asyncio.run(server._auto_run_health_actions(actions))
    check("auto-run executed it (no tap)", actions[0].get("status") == "executed", str(actions[0]))
    check("approval recorded as executed",
          (approvals.get(actions[0]["id"]) or {}).get("execution", {}).get("status") == "executed")
    j = client.get("/health/waters", params={"date": "2026-09-30"}, headers=AUTH).json()
    check("Health API shows the logged water", len(j["water"]) == 1
          and abs(j["water"][0]["ml"] - health_metrics.to_ml(16, "oz")) < 0.2, str(j)[:300])

    hunter_actions = [dict(a, action_type="hunter.sync", status="pending") for a in actions]
    asyncio.run(server._auto_run_health_actions(hunter_actions))
    check("health auto-run never runs hunter.sync", hunter_actions[0]["status"] == "pending")

    print("meals: executor")
    MD = "2026-09-28"

    def meal_refused(payload: dict) -> bool:
        try:
            external_actions.execute("health.meal", payload)
        except external_actions.ActionError:
            return True
        return False

    check("health.meal is brain-proposable", external_actions.brain_proposable("health.meal"))
    check("prompt advertises health.meal + portion rule",
          'type="health.meal"' in note and "ONE standard serving" in note.replace("\n", " "))
    r = external_actions.execute("health.meal", {"date": MD, "items": [
        {"name": "Scrambled eggs", "qty": "2 large", "kcal": 180, "protein_g": 12, "carbs_g": 1, "fat_g": 14},
        {"name": "Flour tortilla", "qty": "2 small", "kcal": 190, "protein_g": 5, "carbs_g": 32, "fat_g": 5}]})
    meals = health_store.list_meals(date=MD)
    check("two items -> two meal rows", len(meals) == 2, str(meals))
    check("rows are tagged source=voice", all(m["source"] == "voice" for m in meals))
    check("day totals come back", abs(r["day_totals"]["kcal"] - 370) < 1, str(r["day_totals"]))
    r = external_actions.execute("health.meal", {"date": MD, "name": "Banana", "qty": "1 medium",
                                                 "kcal": 900, "protein_g": 1, "carbs_g": 27, "fat_g": 0})
    check("kcal that contradicts the macros is reconciled (4/4/9)",
          abs(r["logged"][0]["kcal"] - 112) < 1 and r["logged"][0]["kcal_adjusted_from"] == 900, str(r))
    health_store.upsert_correction("protein shake", 160, 30, 5, 2)
    r = external_actions.execute("health.meal", {"date": MD, "name": "Protein shake", "qty": "1 scoop",
                                                 "kcal": 120, "protein_g": 24, "carbs_g": 3, "fat_g": 1})
    check("the user's saved correction wins", r["logged"][0]["kcal"] == 160
          and r["logged"][0]["corrected"], str(r))
    n = len(health_store.list_meals(date=MD))
    r = external_actions.execute("health.meal", {"date": MD, "name": "Protein shake", "qty": "2 scoops",
                                                 "kcal": 320, "protein_g": 60, "carbs_g": 10, "fat_g": 4,
                                                 "replace_last": True})
    check("replace_last corrects the latest meal, no duplicate",
          len(health_store.list_meals(date=MD)) == n and r["replaced"] == "Protein shake", str(r))
    check("no name refused", meal_refused({"kcal": 100}))
    check("all-zero numbers refused", meal_refused({"name": "Air"}))
    check("negative macros refused", meal_refused({"name": "X", "kcal": 100, "protein_g": -5}))
    check("absurd kcal refused", meal_refused({"name": "X", "kcal": 99999}))
    check("replace_last with several items refused", meal_refused(
        {"replace_last": True, "items": [{"name": "A", "kcal": 1}, {"name": "B", "kcal": 1}]}))

    print("meals: end to end")
    reply = ('Logged - about 105 calories.\n<<ACTION type="health.meal" summary="Log a banana">>\n'
             '{ "name": "Banana", "qty": "1 medium", "kcal": 105, "protein_g": 1.3, "carbs_g": 27,'
             ' "fat_g": 0.4, "date": "2026-09-27" }\n<<END_ACTION>>')
    display, actions = server._extract_actions(reply)
    asyncio.run(server._auto_run_health_actions(actions))
    check("meal block auto-ran (no tap)", actions and actions[0].get("status") == "executed", str(actions))
    j = client.get("/health/meals", params={"date": "2026-09-27"}, headers=AUTH).json()
    rows = j if isinstance(j, list) else j.get("meals", [])
    check("Health API shows the logged meal", len(rows) == 1 and rows[0]["name"] == "Banana", str(j)[:300])

    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {_SANDBOX}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
