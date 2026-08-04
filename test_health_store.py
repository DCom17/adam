"""
Adam — health store tests (Phase H1).

Proves the SQLite health store:
    schema init · weigh-ins (add / list / latest-on-or-before / delete) ·
    meals (add / list by day+month / update / delete) · water log (add / list
    newest-first / delete) + v1→v2 migration · daily-metrics upsert
    that merges fields (a manual step entry doesn't wipe synced sleep) ·
    settings/targets round-trip (typed) + defaults · corrections memory ·
    restart survival.

Self-contained temp DB; never touches real data/health. Fully synthetic data.
Run:  python test_health_store.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import health_store as hs

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_health_store_"))
    db = sandbox / "health.db"
    config.HEALTH_DB = db

    print("\n[1] Init")
    info = hs.init(db)
    check("db created", db.exists())
    check("schema version", info["schema_version"] == hs.SCHEMA_VERSION)

    print("\n[2] Weights + latest-on-or-before")
    hs.add_weight("2030-01-01", 180.0, "lb")
    hs.add_weight("2030-01-08", 178.5, "lb")
    wid = hs.add_weight("2030-01-15", 177.0, "lb", note="morning")
    check("three weigh-ins", len(hs.list_weights()) == 3)
    check("latest overall", hs.latest_weight()["weight"] == 177.0)
    check("latest on-or-before a past date", hs.latest_weight(on_or_before="2030-01-10")["weight"] == 178.5)
    check("since filter", len(hs.list_weights(since="2030-01-08")) == 2)
    check("delete weight", hs.delete_weight(wid) and len(hs.list_weights()) == 2)

    print("\n[3] Meals by day + month")
    hs.add_meal("2030-01-15", "Chicken bowl", qty="1 bowl", kcal=550, protein_g=45, carbs_g=40, fat_g=18)
    hs.add_meal("2030-01-15", "Protein shake", kcal=180, protein_g=30, carbs_g=8, fat_g=3)
    mid = hs.add_meal("2030-01-20", "Oatmeal", kcal=300, protein_g=10, carbs_g=54, fat_g=6)
    check("two meals on the 15th", len(hs.list_meals(date="2030-01-15")) == 2)
    check("three meals in the month", len(hs.list_meals(month="2030-01")) == 3)
    check("update meal macros", hs.update_meal(mid, kcal=320, protein_g=12))
    check("update stuck", hs.get_meal(mid)["kcal"] == 320)
    check("delete meal", hs.delete_meal(mid) and len(hs.list_meals(month="2030-01")) == 2)

    print("\n[3b] Water log (ml is canonical)")
    hs.add_water("2030-01-15", 236.588, note="glass")
    wtr_id = hs.add_water("2030-01-15", 473.176)
    hs.add_water("2030-01-16", 500.0)
    day = hs.list_water(date="2030-01-15")
    check("two drinks on the 15th", len(day) == 2)
    check("newest first (undo target)", day[0]["id"] == wtr_id)
    check("note kept", day[1]["note"] == "glass")
    check("since filter spans days", len(hs.list_water(since="2030-01-15")) == 3)
    check("delete water", hs.delete_water(wtr_id) and len(hs.list_water(date="2030-01-15")) == 1)
    check("delete unknown water -> False", hs.delete_water(99999) is False)

    print("\n[4] Daily metrics merge (partial upsert)")
    hs.set_daily_metric("2030-01-15", steps=8000)
    hs.set_daily_metric("2030-01-15", sleep_min=430)   # must NOT wipe steps
    dm = hs.get_daily_metric("2030-01-15")
    check("steps kept after a later sleep write", dm["steps"] == 8000 and dm["sleep_min"] == 430)
    hs.set_daily_metric("2030-01-15", steps=8200)       # update one field
    check("field update", hs.get_daily_metric("2030-01-15")["steps"] == 8200)
    check("metrics since filter", len(hs.list_daily_metrics(since="2030-01-01")) == 1)

    print("\n[5] Settings/targets round-trip + defaults")
    check("weight_unit default", hs.get_setting("weight_unit") == "lb")
    check("water_unit default", hs.get_setting("water_unit") == "oz")
    check("water target default None", hs.get_setting("target_water_ml") is None)
    check("target default None", hs.get_setting("target_kcal") is None)
    hs.set_setting("target_kcal", 2200)
    hs.set_setting("target_protein_g", 160)
    hs.set_setting("weight_goal", 170.0)
    check("target round-trips", hs.get_setting("target_kcal") == 2200)
    s = hs.get_settings()
    check("get_settings overlays defaults", s["target_protein_g"] == 160 and s["weight_unit"] == "lb")

    print("\n[6] Corrections memory")
    hs.upsert_correction("Chicken Bowl", 560, 46, 41, 19)
    hit = hs.match_correction("chicken bowl")   # case-insensitive
    check("correction matched case-insensitively", hit is not None and hit["protein_g"] == 46)
    check("unknown correction -> None", hs.match_correction("nope") is None)

    print("\n[7] Restart survival")
    hs.close(); config.HEALTH_DB = db; hs.init(db)
    check("weights survived", len(hs.list_weights()) == 2)
    check("meals survived", len(hs.list_meals(month="2030-01")) == 2)
    check("target survived", hs.get_setting("target_kcal") == 2200)
    check("daily metric survived", hs.get_daily_metric("2030-01-15")["steps"] == 8200)
    check("water survived", len(hs.list_water(date="2030-01-15")) == 1)

    print("\n[8] Migration: a v1 DB (no water table) opens, keeps data, gains water")
    import sqlite3
    old = sandbox / "old_v1.db"
    raw = sqlite3.connect(str(old))
    raw.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (1);"
        "CREATE TABLE weights (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,"
        " weight REAL NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'lb', note TEXT NOT NULL DEFAULT '');"
        "INSERT INTO weights (date, weight) VALUES ('2030-02-01', 175.0);"
    )
    raw.commit(); raw.close()
    hs.close(); config.HEALTH_DB = old; hs.init(old)
    check("existing weigh-in preserved", len(hs.list_weights()) == 1)
    hs.add_water("2030-02-01", 500.0)
    check("water table created on upgrade", len(hs.list_water(date="2030-02-01")) == 1)
    hs.close()
    raw = sqlite3.connect(str(old))
    ver = raw.execute("SELECT version FROM schema_version").fetchone()[0]
    raw.close()
    check("schema_version stamped forward", ver == hs.SCHEMA_VERSION)

    hs.close()
    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {sandbox}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
