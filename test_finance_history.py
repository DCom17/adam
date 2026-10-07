"""
Adam — finance history golden tests: balances for any date, period reports, and
data-health checks (finance_history.py + the routes that serve it).

FULLY SYNTHETIC fixture — round, obviously fake numbers. Every expected figure is
hand-computed below so the reconstruction math is pinned, not just "runs".

Accounts: Checking (cash) · Card (credit) · ACME Loan (loan) · Broker (investment)
Snapshots:
    2030-01-31  Checking 1000, Card -200, ACME Loan -900, Broker 500  -> NW 400
    2030-03-31  Checking 1500, Card +300 (typed positive!), Broker 500 -> NW 1700
                (ACME Loan absent — paid off)
Transactions (batch B1 unless noted):
    01-20 Checking Groceries      -100      (before the first snapshot)
    02-01 Checking Paycheck      +2000
    02-05 Card     Groceries      -300
    02-10 Card     Groceries       +50      (refund)
    02-15 Checking ACME LOAN PMT  -450  Tuition (Expense — should be Debt Service)
    03-01 Checking Card payment   -200  Transfer
    03-15 Checking ACME LOAN PMT  -450  Tuition
    03-20 Card     THE DINER #12  -100  Eating Out
    03-21 (blank)  THE DINER 12   -100  Eating Out   batch B2 -> duplicate of 03-20
    03-05 Mystery  COFFEE           -5  Eating Out   (account not set up)

Net-worth flows Jan31..Mar31 (transfers excluded, duplicate counted once):
    2000 - 300 + 50 - 450 - 450 - 100 = 750 ; actual change 1300 ; gap 550 - 5 = see below
    (the Mystery -5 also counts: flows = 745, gap = 555)
Bridged NW on 02-28: 400 + (2000-300+50-450) + 555 * 28/59 = 1700 + 263.39 = 1963.39
Projected NW on 01-20 (first day of data): 400 - 0 = 400 ; on 01-19 it doesn't exist.
Feb report: Groceries net 250 (300 - 50 refund), income 2000, Tuition 450.
Baseline (complete months Jan, Feb): essentials (Groceries) Jan 100, Feb 250 -> 175.

Run:  python test_finance_history.py   (exit code 0 = all passed)
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

import finance_store as fs          # noqa: E402
import finance_metrics as fm        # noqa: E402
import finance_history as fh        # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

AUTH = {"Authorization": "Bearer " + server.ADAM_TOKEN}
client = TestClient(server.app)
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


def near(a, b, tol=0.011) -> bool:
    return a is not None and abs(a - b) <= tol


def _seed() -> None:
    fs.upsert_category("Groceries", "Essentials", "Expense", essential=True)
    fs.upsert_category("Tuition", "Essentials", "Expense", essential=False)
    fs.upsert_category("Eating Out", "Lifestyle", "Expense", essential=False)
    fs.upsert_category("Paycheck", "Wealth", "Income")
    fs.upsert_category("Transfer", "Admin", "Transfer")
    fs.upsert_category("Debt Payment", "Debt", "Debt Service")
    fs.upsert_account("Checking", "Bank", "cash")
    fs.upsert_account("Card", "Bank", "credit")
    fs.upsert_account("ACME Loan", "ACME", "loan")
    fs.upsert_account("Broker", "Broker", "investment")
    for acct, bal in {"Checking": 1000, "Card": -200, "ACME Loan": -900, "Broker": 500}.items():
        fs.set_balance("2030-01-31", acct, bal)
    for acct, bal in {"Checking": 1500, "Card": 300, "Broker": 500}.items():
        fs.set_balance("2030-03-31", acct, bal)
    add = fs.add_transaction
    add("2030-01-20", "Checking", "SUPERMART", -100, category="Groceries", source_batch="B1")
    add("2030-02-01", "Checking", "EMPLOYER PAY", 2000, category="Paycheck", source_batch="B1")
    add("2030-02-05", "Card", "SUPERMART", -300, category="Groceries", source_batch="B1")
    add("2030-02-10", "Card", "SUPERMART RETURN", 50, category="Groceries", source_batch="B1")
    add("2030-02-15", "Checking", "ACME LOAN PMT", -450, category="Tuition", source_batch="B1")
    add("2030-03-01", "Checking", "CARD PAYMENT", -200, category="Transfer", source_batch="B1")
    add("2030-03-15", "Checking", "ACME LOAN PMT", -450, category="Tuition", source_batch="B1")
    add("2030-03-20", "Card", "THE DINER #12", -100, category="Eating Out", source_batch="B1")
    add("2030-03-21", "", "THE DINER 12 PHOENIX", -100, category="Eating Out", source_batch="B2")
    add("2030-03-05", "Mystery", "COFFEE", -5, category="Eating Out", source_batch="B1")
    fs.create_batch("B1", source="b1.csv", reviewed=True)
    fs.create_batch("B2", source="b2.csv", reviewed=True)
    fs.set_setting("ef_months", 3)
    fs.set_setting("extra_buffer", 0)


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_fin_hist_"))
    db = sandbox / "finance.db"
    config.FINANCE_DB = db
    fs.close()
    fs.init(db)
    assert fs._DB_PATH == db
    _seed()

    print("\n[1] Duplicates + effective ledger")
    groups = fh.duplicate_groups(fs)
    check("one duplicate pair found", len(groups) == 1)
    check("keeps the row that has an account", groups[0]["keep"]["account"] == "Card")
    eff = fh.effective_transactions(fs)
    check("effective ledger drops the extra copy", len(eff) == 9)

    print("\n[2] Daily series — anchors exact, bridge + projection hand-computed")
    s = fh.series(fs)
    pts = {p["date"]: p for p in s["points"]}
    check("series spans first txn to last snapshot", s["start"] == "2030-01-20" and s["end"] == "2030-03-31")
    check("snapshot day is exact (400)", pts["2030-01-31"]["net_worth"] == 400.0 and pts["2030-01-31"]["kind"] == "actual")
    check("second snapshot exact (1700)", pts["2030-03-31"]["net_worth"] == 1700.0)
    check("bridged Feb 28 = 1963.39", near(pts["2030-02-28"]["net_worth"], 1963.39) and pts["2030-02-28"]["kind"] == "bridged")
    check("projected before first snapshot", pts["2030-01-20"]["kind"] == "projected" and near(pts["2030-01-20"]["net_worth"], 400.0))
    check("card typed positive still read as owed (debt 300 on 03-31)", pts["2030-03-31"]["debt"] == 300.0)
    check("cash = NW - investments + debt", near(pts["2030-02-28"]["liquid_cash"],
          pts["2030-02-28"]["net_worth"] - pts["2030-02-28"]["investments"] + pts["2030-02-28"]["debt"]))
    f = fh.figures_on(fs, "2030-02-28")
    check("figures_on returns the bridged day", f["basis"] == "bridged" and near(f["net_worth"], 1963.39))
    check("figures_on clamps past the data", fh.figures_on(fs, "2031-01-01")["date"] == "2030-03-31")

    print("\n[3] Period report — refunds net, income, months")
    r = fh.period_report(fs, "2030-02-01", "2030-02-28")
    cats = {c["category"]: c for c in r["spending_by_category"]}
    check("groceries net of refund = 250", cats["Groceries"]["spend"] == 250.0 and cats["Groceries"]["refunds"] == 50.0)
    check("income 2000", r["income"] == 2000.0)
    check("spending 700 (250 + 450)", r["spending"] == 700.0)
    check("net +1300", r["net"] == 1300.0)
    r3 = fh.period_report(fs, "2030-01-01", "2030-03-31")
    check("three months broken out", [m["month"] for m in r3["by_month"]] == ["2030-01", "2030-02", "2030-03"])
    check("duplicate counted once in March eating out (105)",
          {c["category"]: c["spend"] for c in r3["spending_by_category"]}["Eating Out"] == 105.0)

    print("\n[4] Baseline uses complete months only")
    b = fh.baseline(fs, "2030-03")
    check("essentials avg of Jan+Feb = 175", b["essentials"] == 175.0 and b["essentials_months"] == ["2030-01", "2030-02"])

    print("\n[5] Checks — every finding listed, status agrees")
    ck = fh.checks(fs)
    kinds = ck["by_kind"]
    check("duplicate flagged", kinds.get("duplicate") == 1)
    check("loan missing from snapshot flagged", kinds.get("missing_snapshot") == 1)
    miss = [i for i in ck["items"] if i["kind"] == "missing_snapshot"][0]
    check("…and recognised as paid off (900 paid vs 900 owed)", miss["paid_off"] is True)
    check("loan payments filed as spending flagged", kinds.get("loan_as_spending") == 1)
    check("blank-account import flagged", kinds.get("no_account") == 1)
    check("unknown account flagged", kinds.get("unknown_account") == 1)
    check("unexplained gap flagged", kinds.get("balance_gap") == 1)
    check("mixed +/- debt noted as info", any(i["kind"] == "mixed_sign" and i["severity"] == "info" for i in ck["items"]))
    h = fm.data_health(fs)
    check("data_health status == checks status", h["review_status"] == ck["status"] == "Needs Review")
    check("open count matches warn items", h["open_items"] == sum(1 for i in ck["items"] if i["severity"] == "warn"))

    print("\n[6] Summary for a month and for a range")
    sm = fm.summary(fs, month="2030-02")
    check("month summary balances are end-of-Feb", sm["as_of"] == "2030-02-28" and sm["balances_basis"] == "bridged")
    check("month summary net worth 1963.39", near(sm["net_worth"], 1963.39))
    check("month summary spending from report", {c["category"] for c in sm["spending_by_category"]} == {"Groceries", "Tuition"})
    check("ef target uses baseline (3 x 175)", sm["cash_safety"]["ef_target"] == 525.0)
    rg = fm.summary(fs, start="2030-02-01", end="2030-03-31")
    check("range summary change since Jan 31 = +1300", rg["change"]["net_worth"] == 1300.0 and rg["change"]["from"] == "2030-01-31")
    check("range summary basis actual on a snapshot day", rg["balances_basis"] == "actual")

    print("\n[7] API routes")
    check("history route", client.get("/finance/history", headers=AUTH).json()["end"] == "2030-03-31")
    check("checks route", client.get("/finance/checks", headers=AUTH).json()["status"] == "Needs Review")
    check("months route", [m["month"] for m in client.get("/finance/months", headers=AUTH).json()["months"]] == ["2030-01", "2030-02", "2030-03"])
    check("summary range route", client.get("/finance/summary", params={"start": "2030-02-01", "end": "2030-03-31"}, headers=AUTH).status_code == 200)
    check("summary needs both ends", client.get("/finance/summary", params={"start": "2030-02-01"}, headers=AUTH).status_code == 400)
    check("summary rejects a bad date", client.get("/finance/summary", params={"start": "x", "end": "y"}, headers=AUTH).status_code == 400)
    check("routes are token-gated", client.get("/finance/checks").status_code in (401, 403))

    # Resolve the duplicate as two separate charges -> both count again.
    ids = groups[0]
    dup_ids = fh.dup_ack_ids(ids)
    client.post("/finance/checks/ack", headers=AUTH, json={"ids": [dup_ids["separate"]]})
    check("'separate' keeps both rows", len(fh.effective_transactions(fs)) == 10)
    client.post("/finance/checks/ack", headers=AUTH, json={"ids": [dup_ids["separate"]], "undo": True})
    client.post("/finance/checks/ack", headers=AUTH, json={"ids": [dup_ids["confirm"]]})
    check("'confirm' excludes the extra for good", len(fh.effective_transactions(fs)) == 9
          and fh.checks(fs)["by_kind"].get("duplicate") is None)

    # Assign the blank import to Card; relabel the unknown account.
    r = client.post("/finance/reassign-account", headers=AUTH, json={"to_account": "Card", "batch_id": "B2", "from_account": ""}).json()
    check("blank import assigned", r["changed"] == 1 and fh.checks(fs)["by_kind"].get("no_account") is None)
    check("confirmed duplicate stays excluded after relabel", len(fh.effective_transactions(fs)) == 9)
    check("reassign to unknown account refused",
          client.post("/finance/reassign-account", headers=AUTH, json={"to_account": "Nope", "batch_id": "B1"}).status_code == 400)
    client.post("/finance/reassign-account", headers=AUTH, json={"to_account": "Checking", "from_account": "Mystery"})
    check("unknown account cleared", fh.checks(fs)["by_kind"].get("unknown_account") is None)

    # Fix the loan payments; mark the loan paid off.
    item = [i for i in fh.checks(fs)["items"] if i["kind"] == "loan_as_spending"][0]
    r = client.post("/finance/recategorize", headers=AUTH, json={"txn_keys": item["txn_keys"], "category": item["suggest_category"]}).json()
    check("loan payments recategorized", r["changed"] == 2 and fh.checks(fs)["by_kind"].get("loan_as_spending") is None)
    client.post("/finance/snapshot", headers=AUTH, json={"date": "2030-03-31", "balances": {"ACME Loan": 0}})
    check("paid-off loan no longer missing", fh.checks(fs)["by_kind"].get("missing_snapshot") is None)

    # Dismiss what's left; status turns All Clear and the dismissal can be undone.
    left = [i for i in fh.checks(fs)["items"] if i["severity"] == "warn"]
    client.post("/finance/checks/ack", headers=AUTH, json={"ids": [i["id"] for i in left]})
    check("all dismissed -> All Clear", fm.data_health(fs)["review_status"] == "All Clear")
    check("dismissed still listable", len(client.get("/finance/checks", params={"include_dismissed": True}, headers=AUTH).json()["items"]) >= len(left))

    print("\n[8] Transfers filed as spending + rules + transaction window")
    # A -130 'MOVE TO CU' filed as Insurance whose +130 lands as an (unpaired)
    # Transfer deposit in another account two days later = a move, not spending.
    fs.upsert_category("Insurance", "Essentials", "Expense", essential=True)
    fs.upsert_account("CU Savings", "CU", "cash")
    for m in ("04", "05"):
        fs.add_transaction(f"2030-{m}-14", "Checking", f"CREDIT UNION XFER 99887766{m}", -130, category="Insurance", source_batch="B3")
        fs.add_transaction(f"2030-{m}-16", "CU Savings", "ACH DEPOSIT FROM BANK", 130, category="Transfer", source_batch="B4")
        fs.add_transaction(f"2030-{m}-17", "CU Savings", "WITHDRAWAL TO AGENT INSURANCE", -133, category="Insurance", source_batch="B4")
    ck = fh.checks(fs)
    xs = [i for i in ck["items"] if i["kind"] == "transfer_as_spending"]
    check("transfer filed as spending detected once (grouped)", len(xs) == 1 and len(xs[0]["rows"]) == 2)
    check("real insurance payment (-133) not flagged", all(r["amount"] == -130 for r in xs[0]["rows"]))
    check("suggested pattern drops the reference number", xs[0]["pattern"] == "CREDIT UNION XFER")
    pv = client.get("/finance/rules/preview", params={"pattern": xs[0]["pattern"]}, headers=AUTH).json()
    check("rule preview counts matches", pv["matches"] == 2 and pv["by_category"] == {"Insurance": 2})
    r = client.post("/finance/rules", headers=AUTH, json={"pattern": xs[0]["pattern"], "category": "Transfer", "apply_existing": True}).json()
    check("rule applied to existing rows", r["changed"] == 2)
    check("…and they pair with their deposits", r["transfers_paired"] == 2)
    check("flag cleared after fix", not any(i["kind"] == "transfer_as_spending" for i in fh.checks(fs)["items"]))
    import finance_import as fi
    row = fi.apply_merchant_rule({"raw_desc": "CREDIT UNION XFER 1234567806", "category": "Insurance", "merchant": ""})
    check("future import: the taught rule beats the model's guess", row["category"] == "Transfer")
    check("rules route lists it with matches", any(x["pattern"] == "CREDIT UNION XFER" and x["matches"] == 2
          for x in client.get("/finance/rules", headers=AUTH).json()["rules"]))
    check("rule delete", client.delete("/finance/rules", params={"pattern": "credit union xfer"}, headers=AUTH).status_code == 200)
    check("short pattern refused", client.post("/finance/rules", headers=AUTH, json={"pattern": "ab", "category": "Transfer"}).status_code == 400)
    k = [t for t in fs.list_transactions() if t["raw_desc"] == "WITHDRAWAL TO AGENT INSURANCE"][0]["txn_key"]
    d = client.get("/finance/transaction", params={"txn_key": k}, headers=AUTH).json()
    check("transaction window: merchant history", d["merchant"]["count"] == 2 and d["merchant"]["spent"] == 266.0)
    check("transaction window: counted + type", d["counted"] is True and d["category_type"] == "Expense")
    check("transaction window: unknown key 404", client.get("/finance/transaction", params={"txn_key": "nope"}, headers=AUTH).status_code == 404)
    check("card-reader prefix stripped from label", fh._merchant_label({"merchant": "", "raw_desc": "SQ *CORNER AUTO GLASS"}) == "Corner Auto Glass")

    print(f"\n  {_passed} passed, {_failed} failed")
    fs.close()
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
