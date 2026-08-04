"""
Adam — trackers planning-note tests (Phase H4).

Proves the per-turn snapshot server injects so daily planning + voice questions
use real Finance/Health numbers: empty (and cheap) when nothing is set up, and a
compact, accurate one-liner each once data exists. Best-effort/fail-soft.

Run:  python test_health_planning.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "n" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server            # noqa: E402
import health_store as hs  # noqa: E402
import finance_store as fs  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_planning_"))
    config.HEALTH_DB = sandbox / "health.db"
    config.FINANCE_DB = sandbox / "finance.db"
    hs.close(); hs.init(config.HEALTH_DB)
    fs.close(); fs.init(config.FINANCE_DB)
    today = time.strftime("%Y-%m-%d", time.localtime())

    print("\n[1] Empty when nothing is set up")
    check("empty trackers -> empty note", server._trackers_snapshot_note() == "")

    print("\n[2] Health snapshot appears once data is logged")
    hs.set_setting("target_kcal", 2200); hs.set_setting("target_protein_g", 160)
    hs.add_meal(today, "Chicken bowl", kcal=550, protein_g=45, carbs_g=40, fat_g=18)
    hs.add_meal(today, "Protein shake", kcal=180, protein_g=30, carbs_g=8, fat_g=3)
    hs.add_weight(today, 177.0, "lb")
    hs.set_daily_metric(today, steps=8200, sleep_min=430)
    note = server._trackers_snapshot_note()
    check("has health line", "Health today:" in note)
    check("kcal vs target", "730/2200 kcal" in note)
    check("protein vs target", "75/160g protein" in note)
    check("weight shown", "177" in note and "lb" in note)
    check("steps shown", "8200 steps" in note)

    print("\n[2b] Water reaches the per-turn snapshot")
    import health_metrics as hm  # noqa: E402
    hs.set_setting("target_water_ml", hm.to_ml(64, "oz"))
    hs.add_water(today, hm.to_ml(24, "oz"))
    note = server._trackers_snapshot_note()
    check("water vs target shown", "24/64 oz water" in note)

    print("\n[3] Finance snapshot appears once a balance exists")
    fs.upsert_account("Checking", "TestBank", "cash")
    fs.upsert_account("Card", "TestBank", "credit")
    fs.set_balance(today, "Checking", 5000)
    fs.set_balance(today, "Card", -1200)
    note = server._trackers_snapshot_note()
    check("has finance line", "Finance (" in note)
    check("net worth shown", "net worth $3,800" in note)
    check("debt shown", "debt $1,200" in note)
    check("labeled private + local", "private, on this machine" in note)

    print("\n[4] Fail-soft: a broken store never raises")
    _bad = fs.latest_snapshot_date
    fs.latest_snapshot_date = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        note = server._trackers_snapshot_note()
        check("still returns (health survives finance error)", "Health today:" in note)
    finally:
        fs.latest_snapshot_date = _bad

    hs.close(); fs.close()
    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {sandbox}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
