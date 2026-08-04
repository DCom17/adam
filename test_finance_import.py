"""
Adam — finance import + review pipeline tests (Phase F2).

Proves the deterministic pipeline that turns parsed rows into a reviewed ledger:

    normalize (money/date coercion) · merchant-rule auto-categorize · dedup vs the
    ledger AND within a batch · transfer pairing (±3d, opposite sign, equal
    magnitude, different accounts) · review-reason classification · edit_staged
    teaches a merchant rule · approve_batch commits non-dupes, marks reviewed,
    clears staging · discard_batch · the LLM-parse adapter via an INJECTED fake
    runner (no real claude.exe) + the JSON extractor's fence/prose tolerance.

Fully synthetic data (no real people/places — the shipped-test content guard).
Run:  python test_finance_import.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import finance_store as fs
import finance_import as fi

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
    sandbox = Path(tempfile.mkdtemp(prefix="adam_fin_import_"))
    db = sandbox / "finance.db"
    config.FINANCE_DB = db
    fs.init(db)

    # Accounts + a couple explicit categories the fixture relies on.
    fs.upsert_account("Checking", "TestBank", "cash")
    fs.upsert_account("Savings", "TestBank", "cash")
    fs.upsert_category("Groceries", "Essentials", "Expense", essential=True, sort=130)
    fs.upsert_category("Transfer", "Admin", "Transfer", sort=710)

    print("\n[1] Row normalization (money + date coercion)")
    r = fi.normalize_row({"date": "1/5/2030", "description": "BIGMART #5",
                          "amount": "($42.50)"}, default_account="Checking")
    check("US date -> ISO", r["date"] == "2030-01-05")
    check("parenthetary amount -> negative", r["amount"] == -42.5)
    check("default account applied", r["account"] == "Checking")
    check("raw_desc mapped from 'description'", r["raw_desc"] == "BIGMART #5")
    r2 = fi.normalize_row({"date": "2030-02-01", "desc": "PAYDAY", "value": "1,250.00"})
    check("comma amount parses", r2["amount"] == 1250.0)
    check("alt keys (desc/value) map", r2["raw_desc"] == "PAYDAY")

    print("\n[2] Merchant-rule auto-categorization")
    fs.upsert_merchant_rule("BIGMART", "BigMart", "Groceries")
    row = fi.apply_merchant_rule({"raw_desc": "POS BIGMART #5 CITY", "category": "", "merchant": ""})
    check("rule fills category", row["category"] == "Groceries")
    check("rule fills clean merchant", row["merchant"] == "BigMart")
    row2 = fi.apply_merchant_rule({"raw_desc": "UNKNOWN CO", "category": "", "merchant": ""})
    check("no rule -> review category", row2["category"] == fs.REVIEW_CATEGORY)

    print("\n[3] Transfer pairing (±3d, opposite sign, equal magnitude)")
    rows = [
        {"date": "2030-01-10", "account": "Checking", "amount": -500.0, "category": "Transfer",
         "raw_desc": "TO SAVINGS", "merchant": "", "transfer_pair_key": "", "notes": "", "institution": "", "account_type": ""},
        {"date": "2030-01-12", "account": "Savings", "amount": 500.0, "category": "Transfer",
         "raw_desc": "FROM CHECKING", "merchant": "", "transfer_pair_key": "", "notes": "", "institution": "", "account_type": ""},
        {"date": "2030-01-20", "account": "Checking", "amount": -75.0, "category": "Transfer",
         "raw_desc": "ATM", "merchant": "", "transfer_pair_key": "", "notes": "", "institution": "", "account_type": ""},
    ]
    fi.pair_transfers(rows, {"Transfer": "Transfer"})
    check("matched legs share a key", rows[0]["transfer_pair_key"] and
          rows[0]["transfer_pair_key"] == rows[1]["transfer_pair_key"])
    check("lonely transfer stays unpaired", rows[2]["transfer_pair_key"] == "")

    print("\n[4] Stage a batch — dedup + classification")
    # Pre-seed the ledger with one existing txn so a re-import dedups against it.
    existing_key = fs.make_txn_key("2030-01-05", "Checking", "BIGMART #5", -42.50)
    fs.add_transaction("2030-01-05", "Checking", "BIGMART #5", -42.50,
                       category="Groceries", reviewed=True, txn_key=existing_key)
    import_rows = [
        {"date": "2030-01-05", "description": "BIGMART #5", "amount": -42.50},   # dup of ledger
        {"date": "2030-01-06", "description": "BIGMART #5", "amount": -18.00},   # auto Groceries (rule)
        {"date": "2030-01-06", "description": "BIGMART #5", "amount": -18.00},   # dup within batch
        {"date": "2030-01-07", "description": "MYSTERY VENDOR", "amount": -30.00},  # needs category
        {"date": "2030-01-10", "account": "Checking", "description": "TO SAVINGS", "amount": -500.00, "category": "Transfer"},
        {"date": "2030-01-12", "account": "Savings", "description": "FROM CHECKING", "amount": 500.00, "category": "Transfer"},
    ]
    summary = fi.stage_rows(import_rows, batch_id="BATCH1", source="test",
                            default_account="Checking")
    check("all rows staged", summary["staged"] == 6)
    check("two duplicates flagged", summary["duplicates"] == 2)
    check("transfers paired (1 pair)", summary["transfers_paired"] == 1)
    # ready = the -18 auto-Groceries row + the two paired transfer legs = 3
    check("ready count = 3", summary["ready"] == 3)
    check("needs_review = 1 (MYSTERY VENDOR)", summary["needs_review"] == 1)
    check("nothing hit the ledger yet (only the pre-seed)", fs.count_transactions() == 1)

    print("\n[5] Review summary grouping")
    rev = fi.review_summary("BATCH1")
    check("grouping counts add up", rev["counts"]["total"] == 6)
    check("duplicates grouped", rev["counts"]["duplicates"] == 2)
    check("needs-review grouped", rev["counts"]["needs_review"] == 1)
    mystery = rev["needs_review"][0]
    check("mystery row reason is 'needs a category'", "category" in mystery["review_reason"].lower())

    print("\n[6] Edit a staged row -> teaches a merchant rule")
    fi.edit_staged(mystery["id"], category="Groceries", merchant="Mystery Vendor")
    reclassed = fs.get_staged(mystery["id"])
    check("edited row now categorized", reclassed["category"] == "Groceries")
    check("edited row clears its review reason", reclassed["review_reason"] == "")
    rule = fs.match_merchant_rule("MYSTERY VENDOR")
    check("merchant rule was taught", rule is not None and rule["category"] == "Groceries")

    print("\n[7] Approve the batch -> commits non-dupes, clears staging")
    result = fi.approve_batch("BATCH1")
    # committed = -18 Groceries + MYSTERY(now Groceries) + 2 transfer legs = 4
    check("committed 4 non-duplicate rows", result["committed"] == 4)
    check("skipped 2 duplicates", result["skipped_duplicates"] == 2)
    check("ledger now has pre-seed + 4 = 5", fs.count_transactions() == 5)
    check("staging cleared for the batch", fs.count_staged(batch_id="BATCH1") == 0)
    check("batch marked reviewed", fs.list_batches(reviewed=True) and
          any(b["id"] == "BATCH1" for b in fs.list_batches(reviewed=True)))
    check("no unreviewed batches remain", fs.count_unreviewed_batches() == 0)

    print("\n[8] Discard a batch -> nothing committed, batch gone")
    fi.stage_rows([{"date": "2030-03-01", "description": "TEMP", "amount": -9.0}],
                  batch_id="BATCH2", source="test", default_account="Checking")
    check("batch2 staged", fs.count_staged(batch_id="BATCH2") == 1)
    fi.discard_batch("BATCH2")
    check("batch2 staging gone", fs.count_staged(batch_id="BATCH2") == 0)
    check("batch2 record gone", not any(b["id"] == "BATCH2" for b in fs.list_batches()))
    check("ledger unchanged by discard", fs.count_transactions() == 5)

    print("\n[9] JSON extractor tolerates fences + prose")
    plain = fi._extract_json_array('[{"date":"2030-01-01","description":"A","amount":-1}]')
    check("plain array parses", len(plain) == 1)
    fenced = fi._extract_json_array('Here you go:\n```json\n[{"description":"B","amount":2}]\n```\nDone.')
    check("fenced array parses", len(fenced) == 1 and fenced[0]["amount"] == 2)
    check("garbage -> empty list", fi._extract_json_array("no json here") == [])

    print("\n[10] parse_text via an injected fake runner (no real LLM)")
    def fake_runner(system_prompt, message):
        # A stand-in Claude: ignores input, returns a canned JSON array.
        return '```json\n[{"date":"2030-04-01","description":"COFFEE HUT","amount":-4.25,"account":"Checking"}]\n```'
    parsed = fi.parse_text("any,csv,text", accounts=["Checking"],
                           categories=["Groceries"], default_account="Checking",
                           runner=fake_runner)
    check("parsed one row via fake runner", len(parsed) == 1)
    check("parsed row normalized", parsed[0]["amount"] == -4.25 and parsed[0]["raw_desc"] == "COFFEE HUT")
    check("empty raw -> no rows", fi.parse_text("", runner=fake_runner) == [])

    print("\n[11] Balance-photo reader via fake vision runner")
    def fake_bal(path):
        return 'Balances:\n```json\n[{"name":"SoFi Checking","balance":784.99,"last4":"0702"},' \
               '{"name":"Chase Freedom Flex","balance":-2144.68}]\n```'
    bals = fi.read_balances_from_image("shot.png", runner=fake_bal)
    check("two balances read", len(bals) == 2)
    check("positive cash balance", bals[0]["name"] == "SoFi Checking" and bals[0]["balance"] == 784.99)
    check("negative debt balance kept", bals[1]["balance"] == -2144.68)
    check("last4 captured", bals[0]["last4"] == "0702")
    check("no balances -> empty", fi.read_balances_from_image("x.png", runner=lambda p: "no json") == [])

    print("\n[12] An UNMATCHED transfer leg is READY, not review "
          "(credit-card payments no longer flood the queue)")
    # Import ONE account's statement: a credit-card payment + a lonely transfer,
    # neither with a matching leg present. Both are correctly categorized.
    lonely = fi.stage_rows([
        {"date": "2030-05-01", "account": "Visa", "description": "AUTOMATIC PAYMENT - THANK YOU",
         "amount": 900.00, "category": "Credit Card Payment"},
        {"date": "2030-05-02", "account": "Checking", "description": "TO SAVINGS",
         "amount": -500.00, "category": "Transfer"},
        {"date": "2030-05-03", "account": "Checking", "description": "SOME MYSTERY LLC",
         "amount": -12.00},  # genuinely uncategorized -> the ONLY review item
    ], batch_id="LONELY", default_account="Checking")
    check("lonely transfers did NOT pair", lonely["transfers_paired"] == 0)
    check("only the uncategorized row needs review", lonely["needs_review"] == 1)
    check("both unmatched transfers are ready", lonely["ready"] == 2)

    print("\n[13] Category case/spelling drift canonicalizes (no false 'needs category')")
    drift = fi.stage_rows([
        {"date": "2030-06-01", "account": "Checking", "description": "TRADER X", "amount": -20.00, "category": "groceries"},
        {"date": "2030-06-02", "account": "Checking", "description": "DINER Y", "amount": -15.00, "category": "eating out"},
    ], batch_id="DRIFT", default_account="Checking")
    check("case-drift categories accepted (0 review)", drift["needs_review"] == 0)
    check("both drift rows ready", drift["ready"] == 2)
    drows = fs.list_staged(batch_id="DRIFT")
    check("category canonicalized to 'Groceries'",
          any(d["category"] == "Groceries" for d in drows))

    fs.close()
    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
