"""
Adam — finance store tests (Phase F1).

Proves the SQLite finance store does what the dashboard depends on:

    init seeds the default taxonomy · account/category upsert + get ·
    txn-key normalization + dedup (a re-imported row never double-counts) ·
    list filters (month/category/reviewed/batch) · recategorize + review ·
    merchant-rule substring match (longest wins) · balance snapshots +
    latest-date selection + net-worth history · import-batch review counters ·
    settings round-trip (typed) + defaults · restart survival (no double-seed).

Self-contained: points the store at a throwaway temp DB; never touches the real
data/finance tree. Run:  python test_finance_store.py   (exit code 0 = passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import finance_store as fs

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def _reopen(db: Path) -> None:
    """Simulate a process restart: drop the connection, reopen the same file."""
    fs.close()
    config.FINANCE_DB = db
    fs.init(db)


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_fin_store_"))
    db = sandbox / "finance.db"
    config.FINANCE_DB = db

    print("\n[1] Init creates schema + seeds default categories")
    info = fs.init(db)
    check("db created on disk", db.exists())
    check("schema_version reported", info["schema_version"] == fs.SCHEMA_VERSION)
    seeded = fs.get_categories()
    check("default taxonomy seeded", len(seeded) == len(fs._DEFAULT_CATEGORIES))
    check("review category present", fs.get_category(fs.REVIEW_CATEGORY) is not None)
    groceries = fs.get_category("Groceries")
    check("Groceries seeded essential", groceries is not None and groceries["essential"] == 1)

    print("\n[2] Accounts upsert + get + update")
    fs.upsert_account("Checking", institution="SoFi", type="cash")
    fs.upsert_account("Card", institution="Chase", type="credit")
    check("account round-trips", fs.get_account("Checking")["institution"] == "SoFi")
    check("two accounts listed", len(fs.get_accounts()) == 2)
    fs.upsert_account("Checking", institution="SoFi", type="cash", expected_in_snapshot=False)
    check("account update sticks", fs.get_account("Checking")["expected_in_snapshot"] == 0)

    print("\n[3] Txn-key normalization")
    k1 = fs.make_txn_key("2030-01-05", "Card", "TRACTOR SUPPLY CO #177", -37.72)
    k2 = fs.make_txn_key("2030-01-05", "Card", "Tractor Supply Co  177", -37.720)
    check("normalization drops punctuation + case + spacing", k1 == k2)
    check("key has expected shape", k1 == "2030-01-05|Card|TRACTOR SUPPLY CO 177|-37.72")

    print("\n[4] Transaction add + dedup")
    r1 = fs.add_transaction("2030-01-05", "Card", "TRACTOR SUPPLY CO #177", -37.72,
                            category="Household", merchant="Tractor Supply",
                            source_batch="B1")
    check("first insert reports inserted", r1["inserted"] is True)
    r2 = fs.add_transaction("2030-01-05", "Card", "Tractor Supply Co 177", -37.72,
                            category="Household", source_batch="B1")
    check("duplicate insert reports NOT inserted", r2["inserted"] is False)
    check("duplicate did not double-count", fs.count_transactions() == 1)
    check("stored row keeps first values", fs.get_transaction(k1)["merchant"] == "Tractor Supply")

    print("\n[5] List filters")
    fs.add_transaction("2030-01-20", "Checking", "SAFEWAY", -14.09, category="Groceries",
                       source_batch="B1", reviewed=True)
    fs.add_transaction("2030-02-02", "Checking", "PAYCHECK", 2000.0, category="Paycheck",
                       source_batch="B2", reviewed=True)
    check("month filter", len(fs.list_transactions(month="2030-01")) == 2)
    check("category filter", len(fs.list_transactions(category="Groceries")) == 1)
    check("reviewed filter", len(fs.list_transactions(reviewed=False)) == 1)
    check("batch filter", len(fs.list_transactions(source_batch="B2")) == 1)
    check("limit honored", len(fs.list_transactions(limit=1)) == 1)
    listed = fs.list_transactions()
    check("newest first", listed[0]["date"] == "2030-02-02")

    print("\n[6] Recategorize + review")
    ok = fs.update_transaction(k1, category="Car / Truck", reviewed=True)
    check("update returns True", ok is True)
    upd = fs.get_transaction(k1)
    check("category changed", upd["category"] == "Car / Truck")
    check("reviewed flag set", upd["reviewed"] == 1)
    check("update of missing key returns False", fs.update_transaction("nope", category="X") is False)

    print("\n[7] Merchant rules (substring, longest wins)")
    fs.upsert_merchant_rule("SAFEWAY", "Safeway", "Groceries")
    fs.upsert_merchant_rule("SAFEWAY FUEL", "Safeway Fuel", "Gas")
    m1 = fs.match_merchant_rule("CARD PURCHASE SAFEWAY #1733")
    check("substring match", m1 is not None and m1["category"] == "Groceries")
    m2 = fs.match_merchant_rule("SAFEWAY FUEL CENTER")
    check("longest pattern wins", m2 is not None and m2["category"] == "Gas")
    check("no match returns None", fs.match_merchant_rule("RANDOM MERCHANT") is None)

    print("\n[8] Balance snapshots + latest date + history")
    fs.upsert_account("Savings", institution="SoFi", type="cash")
    fs.upsert_account("CarLoan", institution="Ally", type="loan")
    fs.set_balance("2029-12-31", "Checking", 2000)
    fs.set_balance("2029-12-31", "CarLoan", -4200)
    fs.set_balance("2030-01-31", "Checking", 3000)
    fs.set_balance("2030-01-31", "Savings", 5000)
    fs.set_balance("2030-01-31", "CarLoan", -4000)
    check("latest snapshot date is max", fs.latest_snapshot_date() == "2030-01-31")
    check("snapshot dates ordered", fs.snapshot_dates() == ["2029-12-31", "2030-01-31"])
    bals = fs.balances_at("2030-01-31")
    check("balances_at returns the row set", bals["Savings"] == 5000 and bals["CarLoan"] == -4000)
    fs.set_balance("2030-01-31", "Checking", 3100)  # overwrite same (date,account)
    check("set_balance overwrites, no dup", fs.balances_at("2030-01-31")["Checking"] == 3100)
    hist = fs.net_worth_history()
    latest_row = hist[-1]
    # assets = 3100 + 5000 = 8100 ; liabilities = 4000 ; net = 4100
    check("history net worth uses account types", latest_row["net_worth"] == 4100.0)

    print("\n[9] Import batches")
    fs.create_batch("B1", source="csv:chase", reviewed=False)
    fs.create_batch("B2", source="text", reviewed=True)
    check("one unreviewed batch", fs.count_unreviewed_batches() == 1)
    fs.mark_batch_reviewed("B1")
    check("marking reviewed clears the counter", fs.count_unreviewed_batches() == 0)
    check("reviewed filter on batches", len(fs.list_batches(reviewed=True)) == 2)

    print("\n[10] Settings round-trip + defaults")
    check("ef_months default", fs.get_setting("ef_months") == 3)
    check("income default is None (compute)", fs.get_setting("gross_monthly_income") is None)
    fs.set_setting("extra_buffer", 2000.0)
    fs.set_setting("gross_monthly_income", 4000)
    check("float round-trips", fs.get_setting("extra_buffer") == 2000.0)
    check("int round-trips", fs.get_setting("gross_monthly_income") == 4000)
    settings = fs.get_settings()
    check("get_settings overlays defaults", settings["ef_months"] == 3 and settings["extra_buffer"] == 2000.0)

    print("\n[11] Restart survival — data persists, no double-seed")
    cat_count_before = len(fs.get_categories())
    txn_before = fs.count_transactions()
    _reopen(db)
    check("categories not re-seeded on reopen", len(fs.get_categories()) == cat_count_before)
    check("transactions survived restart", fs.count_transactions() == txn_before)
    check("settings survived restart", fs.get_setting("extra_buffer") == 2000.0)
    check("balances survived restart", fs.balances_at("2030-01-31")["Checking"] == 3100)

    fs.close()
    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
