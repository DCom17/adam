"""
Adam — finance metrics golden tests (Phase F1).

Every dashboard number is computed by finance_metrics in plain Python (the LLM
never does the arithmetic). This test builds a FULLY SYNTHETIC fixture — round,
obviously-fake numbers, no real personal data — hand-computes each figure in the
docstring, and asserts the engine reproduces it exactly.

Fixture (latest snapshot 2030-01-31):
    Checking 3000 (cash)   Savings 5000 (cash)   Broker 2000 (investment)
    Card -500 (credit)     CarLoan -4000 (loan)  OldAccount (cash, no latest bal)
  -> liquid_cash 8000 · investments 2000 · assets 10000
     card 500 · total_debt 4500 · liabilities 4500 · net_worth 5500
     investment_share 2000/10000 = 0.2

Transactions in 2030-01:
    Rent -1000*  Groceries -200*  Gas -100*   (*essential -> essentials 1300)
    Eating Out -150  Shopping -80  Entertainment -50
    Loan Payment -300 (Debt Service)   Paycheck +4000 (Income)
    Transfer -500/+500 (paired, nets 0)   Transfer -200 (broken pair -> unmatched)
    Invest -250 (Investment, excluded from spend)
  + a Dec 2029 Uncategorized -25 (unmapped)

Settings: ef_months 3, extra_buffer 1000; income/debt/card-reserve left UNSET
so the compute-from-transactions fallbacks are exercised.

Hand-computed targets:
    essentials 1300 · ef_target 3x1300=3900 · cash_safety 3900+1000=4900
    card_reserve 500 · investable 8000-3900-1000-500 = 2600 · ef_progress 1.0
    income 4000 · debt payments 300 · dti 300/4000 = 0.075 -> Manageable
    data health: unmapped 1, unmatched_transfers 1, missing 1, unreviewed 1

Run:  python test_finance_metrics.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import finance_store as fs
import finance_metrics as fm

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


def _seed() -> dict:
    """Build the fixture; return a few txn keys the tests need to mutate later."""
    # Categories — declared explicitly (exact flags) so the goldens don't depend
    # on the default-seed contents.
    fs.upsert_category("Rent", "Essentials", "Expense", essential=True, sort=100)
    fs.upsert_category("Groceries", "Essentials", "Expense", essential=True, sort=130)
    fs.upsert_category("Gas", "Essentials", "Expense", essential=True, sort=140)
    fs.upsert_category("Eating Out", "Lifestyle", "Expense", essential=False, sort=300)
    fs.upsert_category("Shopping", "Lifestyle", "Expense", essential=False, sort=350)
    fs.upsert_category("Entertainment", "Lifestyle", "Expense", essential=False, sort=320)
    fs.upsert_category("Loan Payment", "Debt", "Debt Service", essential=False, sort=600)
    fs.upsert_category("Paycheck", "Wealth", "Income", essential=False, sort=650)
    fs.upsert_category("Transfer", "Admin", "Transfer", essential=False, sort=710)
    fs.upsert_category("Invest", "Wealth", "Investment", essential=False, sort=800)

    # Accounts.
    fs.upsert_account("Checking", "SoFi", "cash")
    fs.upsert_account("Savings", "SoFi", "cash")
    fs.upsert_account("Broker", "Fidelity", "investment")
    fs.upsert_account("Card", "Chase", "credit")
    fs.upsert_account("CarLoan", "Ally", "loan")
    fs.upsert_account("OldAccount", "Legacy", "cash", expected_in_snapshot=True)

    # Snapshots.
    fs.set_balance("2029-12-31", "Checking", 2000)
    fs.set_balance("2029-12-31", "Broker", 2000)
    fs.set_balance("2029-12-31", "Card", -600)
    fs.set_balance("2029-12-31", "CarLoan", -4200)
    fs.set_balance("2030-01-31", "Checking", 3000)
    fs.set_balance("2030-01-31", "Savings", 5000)
    fs.set_balance("2030-01-31", "Broker", 2000)
    fs.set_balance("2030-01-31", "Card", -500)
    fs.set_balance("2030-01-31", "CarLoan", -4000)
    # OldAccount intentionally missing from the latest snapshot.

    # Transactions.
    fs.add_transaction("2030-01-02", "Checking", "RENT AGUILAR", -1000, category="Rent", merchant="Landlord")
    fs.add_transaction("2030-01-05", "Card", "BIGMART", -200, category="Groceries", merchant="BigMart")
    fs.add_transaction("2030-01-08", "Card", "SHELL OIL", -100, category="Gas", merchant="Shell")
    fs.add_transaction("2030-01-10", "Card", "THE DINER", -150, category="Eating Out", merchant="Diner")
    fs.add_transaction("2030-01-12", "Card", "MEGA MALL", -80, category="Shopping", merchant="Mall")
    fs.add_transaction("2030-01-14", "Card", "CINEMA 12", -50, category="Entertainment", merchant="Cinema")
    fs.add_transaction("2030-01-15", "Checking", "STUDENT LOAN", -300, category="Loan Payment", merchant="Lender")
    fs.add_transaction("2030-01-16", "Checking", "EMPLOYER PAY", 4000, category="Paycheck", merchant="Employer")
    fs.add_transaction("2030-01-18", "Checking", "TO SAVINGS", -500, category="Transfer",
                       transfer_pair_key="T1")
    fs.add_transaction("2030-01-18", "Savings", "FROM CHECKING", 500, category="Transfer",
                       transfer_pair_key="T1")
    fs.add_transaction("2030-01-20", "Checking", "BROKERAGE MOVE", -250, category="Invest", merchant="Broker")
    # A BROKEN pair: carries a pair key whose group doesn't net to zero (its other
    # leg is gone). That — not a leg with no pair key at all — is what counts as an
    # unmatched transfer; an unpaired leg is the normal result of importing one
    # account at a time and is deliberately not flagged.
    lonely = fs.add_transaction("2030-01-22", "Checking", "ATM CASH MOVE", -200, category="Transfer",
                                transfer_pair_key="T2")
    unmapped = fs.add_transaction("2029-12-15", "Card", "MYSTERY CHARGE", -25,
                                  category=fs.REVIEW_CATEGORY)

    # One unreviewed import batch.
    fs.create_batch("B1", source="csv:test", reviewed=False)

    # Settings: keep income/debt/reserve unset so the compute fallbacks run.
    fs.set_setting("ef_months", 3)
    fs.set_setting("extra_buffer", 1000)
    return {"lonely": lonely["txn_key"], "unmapped": unmapped["txn_key"]}


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_fin_metrics_"))
    db = sandbox / "finance.db"
    config.FINANCE_DB = db
    fs.init(db)
    keys = _seed()
    M = "2030-01"

    print("\n[1] Balance figures (latest snapshot)")
    bal = fm.balance_figures(fs)
    check("snapshot date is latest", bal["snapshot_date"] == "2030-01-31")
    check("liquid cash 8000", bal["liquid_cash"] == 8000.0)
    check("investments 2000", bal["total_investments"] == 2000.0)
    check("assets 10000", bal["assets"] == 10000.0)
    check("card balances 500", bal["card_balances"] == 500.0)
    check("total debt 4500", bal["total_debt"] == 4500.0)
    check("liabilities 4500", bal["liabilities"] == 4500.0)
    check("net worth 5500", bal["net_worth"] == 5500.0)
    check("investment share 0.2", bal["investment_share"] == 0.2)

    print("\n[2] Monthly transaction rollups")
    check("essentials 1300", fm.monthly_essentials(fs, M) == 1300.0)
    check("income 4000", fm.monthly_income(fs, M) == 4000.0)
    check("debt payments (from txns) 300", fm.monthly_debt_payments_from_txns(fs, M) == 300.0)

    print("\n[3] Spending by category (spend only; transfers/invest/income excluded)")
    spend = fm.monthly_spending_by_category(fs, M)
    cats = [r["category"] for r in spend]
    check("seven spend categories", len(spend) == 7)
    check("ranked largest first", cats[0] == "Rent" and spend[0]["spend"] == 1000.0)
    check("second is loan payment 300", cats[1] == "Loan Payment" and spend[1]["spend"] == 300.0)
    check("transfers excluded", "Transfer" not in cats)
    check("investment excluded", "Invest" not in cats)
    check("income excluded", "Paycheck" not in cats)

    print("\n[4] Top flex transactions + flags")
    top5 = fm.top_flex_transactions(fs, M, n=5)
    check("top 5 returned", len(top5) == 5)
    check("largest is Rent/Review", top5[0]["merchant"] == "Landlord" and top5[0]["flag"] == "Review")
    top_all = fm.top_flex_transactions(fs, M, n=10)
    by_amt = {r["amount"]: r["flag"] for r in top_all}
    check("$100 spend flags Review", by_amt.get(100.0) == "Review")
    check("$80 spend flags Watch", by_amt.get(80.0) == "Watch")
    check("$50 spend flags Watch", by_amt.get(50.0) == "Watch")

    print("\n[5] Cash safety system (compute fallbacks)")
    cs = fm.cash_safety(fs, M, balances=bal)
    check("ef target 3900", cs["ef_target"] == 3900.0)
    check("cash safety target 4900", cs["cash_safety_target"] == 4900.0)
    check("card reserve falls back to card balances 500", cs["card_payoff_reserve"] == 500.0)
    check("investable cash 2600", cs["investable_cash"] == 2600.0)
    check("ef progress capped at 1.0", cs["ef_progress"] == 1.0)

    print("\n[6] DTI (compute fallbacks)")
    d = fm.dti(fs, M, balances=bal)
    check("payments 300", d["monthly_debt_payments"] == 300.0)
    check("income 4000", d["gross_monthly_income"] == 4000.0)
    check("dti 0.075", d["dti"] == 0.075)
    check("status Manageable", d["debt_status"] == "Manageable")

    print("\n[7] Setting overrides win over computed values")
    fs.set_setting("monthly_debt_payments", 900)
    fs.set_setting("gross_monthly_income", 3000)
    fs.set_setting("card_payoff_reserve", 1500)
    d2 = fm.dti(fs, M, balances=bal)
    check("override debt payments 900", d2["monthly_debt_payments"] == 900.0)
    check("override income 3000", d2["gross_monthly_income"] == 3000.0)
    check("override dti 0.3", d2["dti"] == 0.3)
    check("dti 0.3 -> Watch", d2["debt_status"] == "Watch")
    cs2 = fm.cash_safety(fs, M, balances=bal)
    check("override card reserve 1500", cs2["card_payoff_reserve"] == 1500.0)
    check("investable recomputes to 1600", cs2["investable_cash"] == 1600.0)
    # Reset the overrides for the remaining checks.
    fs.set_setting("monthly_debt_payments", None)
    fs.set_setting("gross_monthly_income", None)
    fs.set_setting("card_payoff_reserve", None)

    print("\n[8] Data health counters")
    h = fm.data_health(fs)
    check("unmapped categories 1", h["unmapped_categories"] == 1)
    check("unmatched transfers 1 (broken pair)", h["unmatched_transfers"] == 1)
    check("missing snapshot accounts 1 (OldAccount)", h["missing_snapshot_accounts"] == 1)
    check("unreviewed batches 1", h["unreviewed_import_batches"] == 1)
    check("status Needs Review", h["review_status"] == "Needs Review")

    print("\n[9] Clearing every counter -> All Clear")
    fs.mark_batch_reviewed("B1")
    fs.delete_transaction(keys["lonely"])
    fs.delete_transaction(keys["unmapped"])
    fs.set_balance("2030-01-31", "OldAccount", 0)  # now present in latest snapshot
    h2 = fm.data_health(fs)
    check("all counters zero", all(h2[k] == 0 for k in (
        "unmapped_categories", "unmatched_transfers",
        "missing_snapshot_accounts", "unreviewed_import_batches")))
    check("status All Clear", h2["review_status"] == "All Clear")

    print("\n[10] Net worth history")
    hist = fm.net_worth_history() if hasattr(fm, "net_worth_history") else fs.net_worth_history()
    check("two snapshot rows", len(hist) == 2)
    check("oldest net worth -800", hist[0]["net_worth"] == -800.0)
    # Latest now includes OldAccount at 0, so net worth is unchanged at 5500.
    check("latest net worth 5500", hist[-1]["net_worth"] == 5500.0)

    print("\n[11] Advice thresholds (deterministic)")
    check("room to invest", fm.advice(2600, 1.0) == "You have room to invest.")
    check("small investment", fm.advice(500, 1.0) == "Consider a small investment.")
    check("hold when nothing investable", fm.advice(-10, 1.0).startswith("Hold"))
    check("build EF first when progress < 1", fm.advice(9999, 0.5).startswith("Build"))

    print("\n[12] Full summary payload")
    s = fm.summary(fs)  # month=None -> defaults to latest snapshot month
    check("default month is latest snapshot's", s["month"] == "2030-01")
    check("summary net worth 5500", s["net_worth"] == 5500.0)
    check("summary carries cash safety", s["cash_safety"]["ef_target"] == 3900.0)
    check("summary carries dti block", s["dti"]["dti"] == 0.075)
    check("summary advice present", bool(s["advice"]))
    check("summary spending list present", len(s["spending_by_category"]) == 7)

    fs.close()
    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
