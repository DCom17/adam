"""
Adam — Finance Tracker API tests (Phase F2), end to end over the real router.

Drives routers/finance.py through FastAPI's TestClient:

    token gating · account + snapshot + settings round-trips · import (structured
    rows) into staging without touching the ledger · review queue grouping · edit
    a staged row (teaches a rule) · approve the batch (commits + clears staging) ·
    /finance/summary reflects the committed data with correct computed numbers ·
    the raw-text import path via a monkeypatched parser (no real claude.exe) ·
    discard an unreviewed batch.

DB redirected to a temp sandbox; token stubbed; no real model is ever spawned.
Fully synthetic data. Run:  python test_finance_api.py   (exit code 0 = passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "f" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import finance_store as fs          # noqa: E402
import finance_import as fi         # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = server.ADAM_TOKEN
AUTH = {"Authorization": "Bearer " + TOKEN}
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


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_fin_api_"))
    db = sandbox / "finance.db"
    config.FINANCE_DB = db
    fs.close()
    fs.init(db)  # fresh DB + default taxonomy (has Groceries, Paycheck, Transfer)

    print("\n[1] Token gating")
    check("no token -> rejected", client.get("/finance/summary").status_code in (401, 403))
    check("with token -> 200", client.get("/finance/summary", headers=AUTH).status_code == 200)

    print("\n[2] Accounts + snapshot + settings round-trips")
    client.post("/finance/accounts", headers=AUTH,
                json={"name": "Checking", "institution": "TestBank", "type": "cash"})
    r = client.post("/finance/accounts", headers=AUTH,
                    json={"name": "Card", "institution": "TestBank", "type": "credit"})
    check("account created", r.status_code == 200 and r.json()["type"] == "credit")
    r = client.post("/finance/accounts", headers=AUTH, json={"name": "X", "type": "bogus"})
    check("bad account type -> 400", r.status_code == 400)

    r = client.post("/finance/snapshot", headers=AUTH,
                    json={"date": "2030-01-31", "balances": {"Checking": 4000, "Card": -600}})
    check("snapshot stored", r.status_code == 200 and r.json()["accounts"] == 2)

    r = client.post("/finance/settings", headers=AUTH, json={"extra_buffer": 1500})
    check("setting written", r.status_code == 200 and r.json()["extra_buffer"] == 1500)
    r = client.post("/finance/settings", headers=AUTH, json={"gross_monthly_income": None})
    check("null clears override", r.json()["gross_monthly_income"] is None)

    print("\n[3] Import structured rows into staging (ledger untouched)")
    rows = [
        {"date": "2030-01-05", "account": "Checking", "description": "BIGMART", "amount": -50.0, "category": "Groceries"},
        {"date": "2030-01-16", "account": "Checking", "description": "EMPLOYER", "amount": 4000.0, "category": "Paycheck"},
        {"date": "2030-01-20", "account": "Checking", "description": "MYSTERY", "amount": -25.0},  # needs category
    ]
    r = client.post("/finance/import", headers=AUTH,
                    json={"rows": rows, "batch_id": "APIB1", "source": "test", "account": "Checking"})
    check("import staged all rows", r.status_code == 200 and r.json()["staged"] == 3)
    check("one row needs review", r.json()["needs_review"] == 1)
    check("ledger still empty after import", fs.count_transactions() == 0)

    print("\n[4] Review queue + edit staged row")
    r = client.get("/finance/review", params={"batch_id": "APIB1"}, headers=AUTH)
    body = r.json()
    check("review groups returned", body["counts"]["total"] == 3)
    mystery = body["needs_review"][0]
    r = client.post(f"/finance/staged/{mystery['id']}", headers=AUTH,
                    json={"category": "Groceries", "merchant": "Mystery Mart"})
    check("edit categorized the row", r.status_code == 200 and r.json()["review_reason"] == "")
    check("edit taught a merchant rule", fs.match_merchant_rule("MYSTERY") is not None)

    print("\n[5] Approve the batch -> commits + clears staging")
    r = client.post("/finance/batches/APIB1/approve", headers=AUTH)
    check("committed 3 rows", r.status_code == 200 and r.json()["committed"] == 3)
    check("ledger now has 3", fs.count_transactions() == 3)
    check("staging cleared", fs.count_staged(batch_id="APIB1") == 0)
    check("approve unknown batch -> 404",
          client.post("/finance/batches/NOPE/approve", headers=AUTH).status_code == 404)

    print("\n[6] Summary reflects the committed, computed data")
    s = client.get("/finance/summary", headers=AUTH).json()
    # snapshot: Checking 4000 (cash), Card -600 (credit) -> assets 4000, debt 600, net 3400
    check("net worth 3400 from snapshot", s["net_worth"] == 3400.0)
    check("liquid cash 4000", s["liquid_cash"] == 4000.0)
    check("total debt 600", s["total_debt"] == 600.0)
    check("month is snapshot month", s["month"] == "2030-01")
    spend_cats = {row["category"]: row["spend"] for row in s["spending_by_category"]}
    check("groceries spend 75 (50 + 25)", spend_cats.get("Groceries") == 75.0)
    check("income excluded from spending", "Paycheck" not in spend_cats)
    check("data health all clear (batch reviewed)",
          s["data_health"]["review_status"] == "All Clear")

    print("\n[7] Transactions list")
    r = client.get("/finance/transactions", params={"month": "2030-01"}, headers=AUTH)
    check("three committed txns listed", r.json()["count"] == 3)

    print("\n[8] Raw-text import path (parser monkeypatched — no real LLM)")
    _orig = fi.parse_text
    def fake_parse(raw, *, accounts=None, categories=None, default_account=""):
        return [fi.normalize_row({"date": "2030-02-02", "description": "COFFEE HUT",
                                  "amount": -6.0, "category": "Eating Out"}, default_account)]
    fi.parse_text = fake_parse
    try:
        r = client.post("/finance/import", headers=AUTH,
                        json={"raw": "some,csv,text", "batch_id": "APIB2", "account": "Checking"})
        check("raw import staged the parsed row", r.status_code == 200 and r.json()["staged"] == 1)
    finally:
        fi.parse_text = _orig
    check("empty import request -> 400",
          client.post("/finance/import", headers=AUTH, json={"account": "Checking"}).status_code == 400)

    print("\n[9] Discard an unreviewed batch")
    r = client.post("/finance/batches/APIB2/discard", headers=AUTH)
    check("discard drops staged rows", r.status_code == 200 and r.json()["discarded_rows"] == 1)
    check("ledger unchanged by discard", fs.count_transactions() == 3)

    print("\n[10] Manage: delete a single committed transaction")
    key = client.get("/finance/transactions", params={"month": "2030-01"}, headers=AUTH).json()["transactions"][0]["txn_key"]
    r = client.request("DELETE", "/finance/transaction", params={"txn_key": key}, headers=AUTH)
    check("delete txn 200", r.status_code == 200)
    check("ledger down to 2", fs.count_transactions() == 2)
    check("delete unknown txn -> 404",
          client.request("DELETE", "/finance/transaction", params={"txn_key": "nope"}, headers=AUTH).status_code == 404)

    print("\n[11] Manage: undo a whole import (by batch)")
    r = client.get("/finance/imports", headers=AUTH)
    check("imports listed with counts", any(i["batch_id"] == "APIB1" for i in r.json()["imports"]))
    r = client.request("DELETE", "/finance/import", params={"batch_id": "APIB1"}, headers=AUTH)
    check("import undo removed remaining rows", r.status_code == 200 and r.json()["removed"] == 2)
    check("ledger now empty", fs.count_transactions() == 0)

    print("\n[12] Manage: snapshots + account deletes")
    r = client.get("/finance/snapshots", headers=AUTH)
    check("snapshot listed with net worth", r.json()["snapshots"] and r.json()["snapshots"][-1]["net_worth"] == 3400.0)
    r = client.request("DELETE", "/finance/snapshot", params={"date": "2030-01-31"}, headers=AUTH)
    check("snapshot deleted", r.status_code == 200 and r.json()["removed"] == 2)
    check("delete missing snapshot -> 404",
          client.request("DELETE", "/finance/snapshot", params={"date": "1999-01-01"}, headers=AUTH).status_code == 404)
    r = client.request("DELETE", "/finance/account", params={"name": "Card"}, headers=AUTH)
    check("account deleted", r.status_code == 200)
    check("delete unknown account -> 404",
          client.request("DELETE", "/finance/account", params={"name": "ghost"}, headers=AUTH).status_code == 404)

    print("\n[13] Needs-review surface + fix via the edit endpoint")
    import urllib.parse as _up
    fs.add_transaction("2030-03-03", "Checking", "MYSTERYX", -5, category=fs.REVIEW_CATEGORY, reviewed=True)
    # Broken pair (key set, other leg absent) -> flagged. A leg with NO pair key is
    # normal (you import one account at a time) and is deliberately not flagged.
    fs.add_transaction("2030-03-04", "Checking", "LONEXFER", -40, category="Transfer", reviewed=True,
                       transfer_pair_key="BROKEN1")
    nr = client.get("/finance/needs-review", headers=AUTH).json()
    check("uncategorized surfaced", nr["counts"]["unmapped"] >= 1 and any(t["raw_desc"] == "MYSTERYX" for t in nr["unmapped"]))
    check("unmatched transfer surfaced", nr["counts"]["unmatched_transfers"] >= 1 and any(t["raw_desc"] == "LONEXFER" for t in nr["unmatched_transfers"]))
    key = fs.make_txn_key("2030-03-03", "Checking", "MYSTERYX", -5)
    r = client.post("/finance/transactions/" + _up.quote(key, safe=""), headers=AUTH, json={"category": "Groceries"})
    check("edit committed txn via path endpoint (encoded key) 200", r.status_code == 200 and r.json()["category"] == "Groceries")
    nr2 = client.get("/finance/needs-review", headers=AUTH).json()
    check("fixed item cleared from needs-review", not any(t["raw_desc"] == "MYSTERYX" for t in nr2["unmapped"]))

    print("\n[14] Multiple CSV files -> one combined batch (cross-file dedup)")
    config.UPLOAD_DIR = sandbox / "uploads"; config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (config.UPLOAD_DIR / "a.csv").write_text("MARKER_ALPHA", encoding="utf-8")
    (config.UPLOAD_DIR / "b.csv").write_text("MARKER_BETA", encoding="utf-8")
    _op = fi.parse_text
    def fake_parse2(raw, *, accounts=None, categories=None, default_account=""):
        shared = fi.normalize_row({"date": "2030-06-01", "account": "Checking", "description": "SHARED", "amount": -10, "category": "Groceries"})
        uniq = {"date": "2030-06-02", "account": "Checking", "description": "ONLY_A", "amount": -11, "category": "Groceries"} if "ALPHA" in raw \
            else {"date": "2030-06-03", "account": "Checking", "description": "ONLY_B", "amount": -12, "category": "Groceries"}
        return [shared, fi.normalize_row(uniq)]
    fi.parse_text = fake_parse2
    try:
        r = client.post("/finance/import", headers=AUTH, json={
            "file_paths": [str(config.UPLOAD_DIR / "a.csv"), str(config.UPLOAD_DIR / "b.csv")],
            "batch_id": "MULTI", "account": "Checking"})
        s = r.json()
        check("multi-file staged all 4 rows into one batch", r.status_code == 200 and s["staged"] == 4)
        check("cross-file duplicate caught", s["duplicates"] == 1)
        check("3 unique rows ready", s["ready"] == 3)
    finally:
        fi.parse_text = _op

    print("\n[15] Balance-photo endpoint + account matching")
    img = config.UPLOAD_DIR / "bal.png"; img.write_bytes(b"\xff\xd8\xff fake png")
    _ob = fi.read_balances_from_image
    fi.read_balances_from_image = lambda p: [{"name": "Checking", "balance": 4000.0, "last4": ""},
                                             {"name": "Nonexistent Bank", "balance": 99.0, "last4": ""}]
    try:
        r = client.post("/finance/balance-photo", headers=AUTH, json={"file_path": str(img)})
        d = r.json()
        check("balance-photo 200 + detected", r.status_code == 200 and len(d["detected"]) == 2)
        check("known account matched", any(x.get("account") == "Checking" for x in d["detected"]))
        check("unknown account left unmatched", any(x.get("account") is None for x in d["detected"]))
        check("non-image file -> 415",
              client.post("/finance/balance-photo", headers=AUTH, json={"file_path": str(config.UPLOAD_DIR / "a.csv")}).status_code == 415)
    finally:
        fi.read_balances_from_image = _ob

    fs.close()
    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
