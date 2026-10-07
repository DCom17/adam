"""
Finance Tracker routes (Phase F2) — the private, local money engine's JSON API.

The flow this serves: import a statement into a review staging area, let the user
fix categories / confirm transfers, approve the batch (which commits it to the
ledger and teaches merchant rules), and read back the computed dashboard. Every
number returned by /finance/summary is computed deterministically by
finance_metrics — the LLM only parses imports (finance_import), it never does the
arithmetic. All routes are token-gated. The HTML dashboard + view-menu entry are
Phase F3; this phase is the API + engine underneath it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

import config
import finance_import as fimport
import finance_store as fs
import finance_metrics as fm
import finance_history as fh
from models import (
    FinanceAccountBody,
    FinanceAckBody,
    FinanceBulkCategoryBody,
    FinanceReassignBody,
    FinanceRuleBody,
    FinanceBalancePhotoBody,
    FinanceCategoryBody,
    FinanceImportBody,
    FinanceRowEdit,
    FinanceSettingsBody,
    FinanceSnapshotBody,
)
from security import require_token

router = APIRouter()

_PARSEABLE_SUFFIXES = {".csv", ".txt", ".tsv", ".text", ".md"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}


def _read_upload_text(path: str) -> str:
    """Read a text/CSV file the user uploaded via /upload. Only files that actually
    live under UPLOAD_DIR are honored (same containment rule as chat attachments),
    so a client can't point this at an arbitrary filesystem path."""
    try:
        rp = Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file_path.")
    if not (rp.is_file() and config.UPLOAD_DIR.resolve() in rp.parents):
        raise HTTPException(status_code=400, detail="file_path must be an uploaded file.")
    if rp.suffix.lower() not in _PARSEABLE_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail="Only text/CSV files can be parsed here yet (images/PDF are a later phase).",
        )
    try:
        return rp.read_text("utf-8", errors="replace")
    except OSError:
        raise HTTPException(status_code=400, detail="Could not read that file.")


def _upload_display_name(path: str) -> str:
    name = Path(path).name
    head, sep, rest = name.partition("_")
    if sep and len(head) == 12 and all(c in "0123456789abcdef" for c in head):
        return rest
    return name


# --- Import + review --------------------------------------------------------

@router.post("/finance/import", dependencies=[Depends(require_token)])
async def finance_import(body: FinanceImportBody):
    """Import a statement into the review staging area. Provide structured `rows`
    (no LLM), `raw` text, a single `file_path`, or several `file_paths` (uploaded
    CSVs) to have Claude parse. Multiple files are parsed and combined into ONE
    batch so duplicates across the files (and against the ledger) are caught
    together. Nothing hits the ledger — returns a staging summary; the user
    approves the batch afterward."""
    rows = body.rows
    source = body.source or ""
    if rows is None:
        paths = list(body.file_paths or [])
        if body.file_path:
            paths.append(body.file_path)
        if paths and not source:
            # Remember which statement(s) an import came from (minus the /upload
            # id prefix) so the Review tab can say "the card-activity file", not just
            # a batch timestamp.
            source = ", ".join(_upload_display_name(p) for p in paths)[:200]
        if paths:
            accounts = [a["name"] for a in fs.get_accounts()]
            categories = [c["name"] for c in fs.get_categories()]
            rows = []
            for p in paths:
                raw = _read_upload_text(p)
                if not raw.strip():
                    continue
                parsed = await asyncio.to_thread(
                    fimport.parse_text, raw, accounts=accounts, categories=categories,
                    default_account=(body.account or ""),
                )
                rows.extend(parsed)
            if not rows:
                raise HTTPException(status_code=422,
                                    detail="Couldn't parse any transactions from those files.")
        else:
            raw = body.raw or ""
            if not raw.strip():
                raise HTTPException(status_code=400,
                                    detail="Provide rows, raw text, or file(s) to import.")
            accounts = [a["name"] for a in fs.get_accounts()]
            categories = [c["name"] for c in fs.get_categories()]
            rows = await asyncio.to_thread(
                fimport.parse_text, raw, accounts=accounts, categories=categories,
                default_account=(body.account or ""),
            )
            if not rows:
                raise HTTPException(status_code=422,
                                    detail="Could not parse any transactions from that input.")
    summary = fimport.stage_rows(
        rows, batch_id=body.batch_id, source=source,
        default_account=(body.account or ""),
    )
    return summary


@router.post("/finance/balance-photo", dependencies=[Depends(require_token)])
async def finance_balance_photo(body: FinanceBalancePhotoBody):
    """Read account balances from an uploaded screenshot (a bank/brokerage view) and
    match them to the user's accounts. Returns the detected balances for the user to
    confirm before saving a snapshot — nothing is written here. Vision runs off the
    event loop."""
    try:
        rp = Path(body.file_path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file_path.")
    if not (rp.is_file() and config.UPLOAD_DIR.resolve() in rp.parents):
        raise HTTPException(status_code=400, detail="file_path must be an uploaded image.")
    if rp.suffix.lower() not in _IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="That file isn't an image.")
    detected = await asyncio.to_thread(fimport.read_balances_from_image, str(rp))
    if not detected:
        raise HTTPException(status_code=422, detail="Couldn't read a balance from that screenshot.")
    # Best-effort match each detected balance to an existing account.
    accts = fs.get_accounts()
    for d in detected:
        dn = (d.get("name") or "").lower().strip()
        l4 = (d.get("last4") or "").strip()
        match = None
        for a in accts:
            an = a["name"].lower()
            if an == dn or (dn and (dn in an or an in dn)) or (l4 and l4 in a["name"]):
                match = a["name"]; break
        d["account"] = match
    return {"detected": detected}


@router.get("/finance/review", dependencies=[Depends(require_token)])
async def finance_review(batch_id: str | None = None):
    """The review queue. With ?batch_id=, the rows for that batch split into
    ready / needs-review / duplicates. Without it, the list of unreviewed batches
    so the UI can pick one."""
    if batch_id:
        return fimport.review_summary(batch_id)
    batches = fs.list_batches(reviewed=False)
    for b in batches:
        b["staged_count"] = fs.count_staged(batch_id=b["id"])
        b["needs_review"] = sum(
            1 for r in fs.list_staged(batch_id=b["id"])
            if r["status"] != "duplicate" and r["review_reason"]
        )
    return {"unreviewed_batches": batches, "count": len(batches)}


@router.get("/finance/batches", dependencies=[Depends(require_token)])
async def finance_batches(reviewed: bool | None = None):
    """All import batches (optionally filtered by reviewed), newest first."""
    batches = fs.list_batches(reviewed=reviewed)
    return {"batches": batches, "count": len(batches)}


@router.post("/finance/staged/{staged_id}", dependencies=[Depends(require_token)])
async def finance_edit_staged(staged_id: int, body: FinanceRowEdit):
    """Edit one staged row (recategorize / rename merchant / note / pair a
    transfer). Setting a real category teaches a merchant rule for next time."""
    row = fimport.edit_staged(
        staged_id, category=body.category, merchant=body.merchant,
        notes=body.notes, transfer_pair_key=body.transfer_pair_key,
        teach_rule=body.teach_rule,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown staged row.")
    return row


@router.delete("/finance/staged/{staged_id}", dependencies=[Depends(require_token)])
async def finance_delete_staged(staged_id: int):
    """Drop a single staged row (e.g. a bad parse) before approving the batch."""
    if not fs.delete_staged(staged_id):
        raise HTTPException(status_code=404, detail="Unknown staged row.")
    return {"deleted": staged_id}


@router.post("/finance/batches/{batch_id}/approve", dependencies=[Depends(require_token)])
async def finance_approve_batch(batch_id: str):
    """Commit a batch: non-duplicate staged rows enter the ledger (reviewed), the
    batch is marked reviewed, and staging is cleared. Duplicates are dropped."""
    if fs.count_staged(batch_id=batch_id) == 0 and not any(
        b["id"] == batch_id for b in fs.list_batches()
    ):
        raise HTTPException(status_code=404, detail="Unknown or empty batch.")
    return fimport.approve_batch(batch_id)


@router.post("/finance/batches/{batch_id}/discard", dependencies=[Depends(require_token)])
async def finance_discard_batch(batch_id: str):
    """Discard an unreviewed batch: drop its staged rows + the batch record.
    Nothing was in the ledger, so this cleanly undoes the import."""
    return fimport.discard_batch(batch_id)


# --- Dashboard reads (the payloads Phase F3's HTML renders) -----------------

def _iso_or_400(v: str | None, name: str) -> str | None:
    if v is None or v == "":
        return None
    from datetime import date as _d
    try:
        return _d.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be YYYY-MM-DD.")


@router.get("/finance/summary", dependencies=[Depends(require_token)])
async def finance_summary(month: str | None = None, start: str | None = None,
                          end: str | None = None):
    """The whole dashboard payload for a month (?month=YYYY-MM, default newest) or
    a date range (?start=&end=) — balances at the period's end (snapshot or
    reconstructed), spending/income over the period, every tile. Deterministic."""
    s, e = _iso_or_400(start, "start"), _iso_or_400(end, "end")
    if (s is None) != (e is None):
        raise HTTPException(status_code=400, detail="Give both start and end.")
    if month is not None and month and not (len(month) == 7 and month[4] == "-"):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM.")
    return await asyncio.to_thread(fm.summary, fs, month=month or None, start=s, end=e)


@router.get("/finance/history", dependencies=[Depends(require_token)])
async def finance_history():
    """Daily net worth / cash / debt / investments from the first day of data to
    the last: snapshots exact, days between them reconstructed from transactions.
    Each point carries kind = actual | bridged | projected."""
    return await asyncio.to_thread(fh.series, fs)


@router.get("/finance/months", dependencies=[Depends(require_token)])
async def finance_months():
    """Every month that has data, with transaction counts, plus the data's span —
    what the period picker offers."""
    counts: dict[str, int] = {}
    for t in fs.list_transactions():
        counts[t["date"][:7]] = counts.get(t["date"][:7], 0) + 1
    snaps = fs.snapshot_dates()
    for d in snaps:
        counts.setdefault(d[:7], 0)
    months = [{"month": m, "transactions": counts[m]} for m in sorted(counts)]
    dates = [t["date"] for t in fs.list_transactions(limit=1)] + snaps
    first = fs.list_transactions()
    start = min([first[-1]["date"]] + snaps) if first else (min(snaps) if snaps else None)
    return {"months": months, "start": start, "end": max(dates) if dates else None,
            "snapshots": snaps}


@router.get("/finance/checks", dependencies=[Depends(require_token)])
async def finance_checks(include_dismissed: bool = False):
    """Every data-health finding as an actionable review item — the list the
    Review tab renders and the Data Health status is computed from."""
    return await asyncio.to_thread(fh.checks, fs, include_dismissed=include_dismissed)


@router.post("/finance/checks/ack", dependencies=[Depends(require_token)])
async def finance_ack(body: FinanceAckBody):
    """Dismiss (or restore, with undo) findings. Calculations honour duplicate
    resolutions immediately."""
    ids = [i for i in body.ids if isinstance(i, str) and 0 < len(i) <= 400][:500]
    if not ids:
        raise HTTPException(status_code=400, detail="No ids.")
    for i in ids:
        if body.undo:
            fs.unack_flag(i)
        else:
            fs.ack_flag(i, body.kind)
    return {"ok": True, "count": len(ids), "undo": body.undo}


@router.post("/finance/reassign-account", dependencies=[Depends(require_token)])
async def finance_reassign_account(body: FinanceReassignBody):
    """Fix the account on an import made with the Account field blank (or wrong),
    then re-run transfer matching since pairing depends on accounts."""
    if body.from_account is None and not body.batch_id:
        raise HTTPException(status_code=400, detail="Give batch_id and/or from_account.")
    if fs.get_account(body.to_account) is None:
        raise HTTPException(status_code=400, detail="Unknown account — add it in Setup first.")
    changed = fs.reassign_account(body.to_account, from_account=body.from_account,
                                  batch_id=body.batch_id or None)
    rm = fimport.rematch_transfers(fs) if changed else {"paired": 0}
    return {"changed": changed, "transfers_paired": rm.get("paired", 0)}


def _matching(pattern: str) -> list[dict]:
    pat = pattern.upper().strip()
    return [t for t in fs.list_transactions() if pat and pat in (t["raw_desc"] or "").upper()]


@router.get("/finance/transaction", dependencies=[Depends(require_token)])
async def finance_transaction_detail(txn_key: str):
    """One transaction with its context (source, transfer partner, rule, merchant
    history) — what the tap-to-open window shows."""
    d = await asyncio.to_thread(fh.transaction_detail, fs, txn_key)
    if d is None:
        raise HTTPException(status_code=404, detail="Unknown transaction.")
    return d


@router.get("/finance/rules", dependencies=[Depends(require_token)])
async def finance_rules():
    """Every merchant rule Adam has learned, with how many ledger rows each matches."""
    txns = fs.list_transactions()
    out = []
    for r in fs.get_merchant_rules():
        n = sum(1 for t in txns if r["pattern"] in (t["raw_desc"] or "").upper())
        out.append({**r, "matches": n})
    return {"rules": out}


@router.get("/finance/rules/preview", dependencies=[Depends(require_token)])
async def finance_rule_preview(pattern: str | None = None, txn_key: str | None = None):
    """What a rule would touch. Give a pattern, or a txn_key to get the suggested
    pattern for that transaction's description (reference numbers stripped so it
    matches next month's statement too)."""
    if not pattern and txn_key:
        t = fs.get_transaction(txn_key)
        if t is None:
            raise HTTPException(status_code=404, detail="Unknown transaction.")
        pattern = fs.rule_pattern(t["raw_desc"])
    pattern = (pattern or "").upper().strip()
    if len(pattern) < 3:
        raise HTTPException(status_code=400, detail="Pattern must be at least 3 characters.")
    rows = _matching(pattern)
    cats: dict[str, int] = {}
    for t in rows:
        cats[t["category"]] = cats.get(t["category"], 0) + 1
    existing = next((r for r in fs.get_merchant_rules() if r["pattern"] == pattern), None)
    return {"pattern": pattern, "matches": len(rows), "by_category": cats,
            "existing_rule": existing,
            "sample": [{"date": t["date"], "desc": t["raw_desc"], "amount": t["amount"],
                        "category": t["category"]} for t in rows[:6]]}


@router.post("/finance/rules", dependencies=[Depends(require_token)])
async def finance_set_rule(body: FinanceRuleBody):
    """Teach a rule; optionally apply it to every matching transaction already in
    the ledger, then re-run transfer matching (a row that just became a Transfer
    can now pair with its other leg)."""
    pattern = body.pattern.upper().strip()
    if len(pattern) < 3:
        raise HTTPException(status_code=400, detail="Pattern must be at least 3 characters.")
    if fs.get_category(body.category) is None:
        raise HTTPException(status_code=400, detail="Unknown category.")
    fs.upsert_merchant_rule(pattern, body.merchant or "", body.category)
    changed = 0
    paired = 0
    if body.apply_existing:
        for t in _matching(pattern):
            if t["category"] != body.category and fs.update_transaction(t["txn_key"], category=body.category):
                changed += 1
        if changed:
            paired = fimport.rematch_transfers(fs).get("paired", 0)
    return {"pattern": pattern, "category": body.category, "changed": changed,
            "transfers_paired": paired}


@router.delete("/finance/rules", dependencies=[Depends(require_token)])
async def finance_delete_rule(pattern: str):
    """Forget a rule. Transactions it already categorized keep their category."""
    if not fs.delete_merchant_rule(pattern):
        raise HTTPException(status_code=404, detail="No such rule.")
    return {"deleted": pattern.upper().strip()}


@router.post("/finance/recategorize", dependencies=[Depends(require_token)])
async def finance_recategorize(body: FinanceBulkCategoryBody):
    """Move several committed transactions to one category."""
    if fs.get_category(body.category) is None:
        raise HTTPException(status_code=400, detail="Unknown category.")
    changed = 0
    for k in body.txn_keys[:1000]:
        existing = fs.get_transaction(k)
        if existing is None:
            continue
        if fs.update_transaction(k, category=body.category):
            changed += 1
        if body.teach_rule and body.category != fs.REVIEW_CATEGORY:
            raw = (existing.get("raw_desc") or "").strip()
            if raw:
                fs.upsert_merchant_rule(raw.upper()[:40].strip(),
                                        existing.get("merchant") or "", body.category)
    return {"changed": changed}


@router.get("/finance/needs-review", dependencies=[Depends(require_token)])
async def finance_needs_review():
    """The committed transactions that need attention — uncategorized rows and
    unmatched transfers — so the Review tab can list and fix them."""
    nr = fm.needs_review(fs)
    return {"unmapped": nr["unmapped"], "unmatched_transfers": nr["unmatched_transfers"],
            "counts": {"unmapped": len(nr["unmapped"]),
                       "unmatched_transfers": len(nr["unmatched_transfers"])}}


@router.post("/finance/rematch-transfers", dependencies=[Depends(require_token)])
async def finance_rematch_transfers():
    """Re-run internal-transfer matching across the whole ledger. Approving an import
    does this automatically; this endpoint re-runs it over transactions imported
    before that existed (or after balances/categories were corrected by hand)."""
    return fimport.rematch_transfers(fs)


@router.get("/finance/transactions", dependencies=[Depends(require_token)])
async def finance_transactions(month: str | None = None, account: str | None = None,
                               category: str | None = None, limit: int | None = None):
    """Committed ledger transactions matching the filters, newest first."""
    txns = fs.list_transactions(month=month, account=account, category=category,
                                limit=limit)
    return {"transactions": txns, "count": len(txns)}


@router.post("/finance/transactions/{txn_key}", dependencies=[Depends(require_token)])
async def finance_edit_transaction(txn_key: str, body: FinanceRowEdit):
    """Recategorize / annotate a committed transaction. Setting a real category
    teaches a merchant rule (same learning loop as the review flow)."""
    existing = fs.get_transaction(txn_key)
    if existing is None:
        raise HTTPException(status_code=404, detail="Unknown transaction.")
    ok = fs.update_transaction(
        txn_key, category=body.category, merchant=body.merchant,
        notes=body.notes, transfer_pair_key=body.transfer_pair_key,
        transfer_ack=body.transfer_ack,
    )
    if body.teach_rule and body.category and body.category != fs.REVIEW_CATEGORY:
        raw = (existing.get("raw_desc") or "").strip()
        if raw:
            merch = body.merchant if body.merchant is not None else existing.get("merchant", "")
            fs.upsert_merchant_rule(raw.upper()[:40].strip(), merch or "", body.category)
    return fs.get_transaction(txn_key) if ok else existing


# --- Manage / remove data (fix mistakes) ------------------------------------
# Deletes take the key/name as a QUERY param (not a path segment) so values that
# contain spaces or '|' — a txn_key does — never trip path routing/encoding.

@router.delete("/finance/transaction", dependencies=[Depends(require_token)])
async def finance_delete_transaction(txn_key: str):
    """Remove one committed transaction from the ledger."""
    if not fs.delete_transaction(txn_key):
        raise HTTPException(status_code=404, detail="Unknown transaction.")
    return {"deleted": txn_key}


@router.get("/finance/imports", dependencies=[Depends(require_token)])
async def finance_imports():
    """Committed import batches with how many ledger transactions each added — the
    Manage view's 'undo an import' list."""
    counts = fs.batch_transaction_counts()
    out = [{"batch_id": bid, "transactions": n} for bid, n in counts.items()]
    out.sort(key=lambda r: r["batch_id"], reverse=True)
    return {"imports": out, "count": len(out)}


@router.delete("/finance/import", dependencies=[Depends(require_token)])
async def finance_delete_import(batch_id: str):
    """Undo a whole import: remove every committed transaction that came from it."""
    removed = fs.delete_transactions_for_batch(batch_id)
    return {"batch_id": batch_id, "removed": removed}


@router.get("/finance/snapshots", dependencies=[Depends(require_token)])
async def finance_snapshots():
    """All balance-snapshot dates with their computed net worth, for the Manage
    list (each row deletable)."""
    return {"snapshots": fs.net_worth_history()}


@router.delete("/finance/snapshot", dependencies=[Depends(require_token)])
async def finance_delete_snapshot(date: str):
    """Remove a whole balance snapshot (all accounts on that date)."""
    removed = fs.delete_snapshot(date)
    if not removed:
        raise HTTPException(status_code=404, detail="No snapshot on that date.")
    return {"date": date, "removed": removed}


@router.delete("/finance/account", dependencies=[Depends(require_token)])
async def finance_delete_account(name: str):
    """Remove an account definition (history is left intact)."""
    if not fs.delete_account(name):
        raise HTTPException(status_code=404, detail="Unknown account.")
    return {"deleted": name}


# --- Accounts / categories / settings / snapshots ---------------------------

@router.get("/finance/accounts", dependencies=[Depends(require_token)])
async def finance_accounts():
    return {"accounts": fs.get_accounts()}


@router.post("/finance/accounts", dependencies=[Depends(require_token)])
async def finance_upsert_account(body: FinanceAccountBody):
    if body.type not in ("cash", "credit", "investment", "loan"):
        raise HTTPException(status_code=400,
                            detail="type must be cash, credit, investment, or loan.")
    fs.upsert_account(body.name, institution=body.institution, type=body.type,
                      expected_in_snapshot=body.expected_in_snapshot)
    return fs.get_account(body.name)


@router.get("/finance/categories", dependencies=[Depends(require_token)])
async def finance_categories():
    return {"categories": fs.get_categories()}


@router.post("/finance/categories", dependencies=[Depends(require_token)])
async def finance_upsert_category(body: FinanceCategoryBody):
    fs.upsert_category(body.name, grp=body.grp, type=body.type,
                       essential=body.essential, recurring=body.recurring,
                       sort=body.sort)
    return fs.get_category(body.name)


@router.get("/finance/settings", dependencies=[Depends(require_token)])
async def finance_get_settings():
    return fs.get_settings()


@router.post("/finance/settings", dependencies=[Depends(require_token)])
async def finance_set_settings(body: FinanceSettingsBody):
    """Update settings the metrics engine reads. Only fields present in the request
    are written; the model's other fields stay None and are skipped — EXCEPT that
    income/debt/reserve are explicitly nullable to clear an override, so we use the
    raw request to tell 'omitted' from 'set to null'."""
    sent = body.model_dump(exclude_unset=True)
    for key in ("ef_months", "extra_buffer", "gross_monthly_income",
                "monthly_debt_payments", "card_payoff_reserve"):
        if key in sent:
            fs.set_setting(key, sent[key])
    return fs.get_settings()


@router.post("/finance/snapshot", dependencies=[Depends(require_token)])
async def finance_snapshot(body: FinanceSnapshotBody):
    """Record a weekly balance snapshot (per-account balances on a date)."""
    if not body.balances:
        raise HTTPException(status_code=400, detail="No balances provided.")
    for account, balance in body.balances.items():
        fs.set_balance(body.date, account, balance)
    return {"date": body.date, "accounts": len(body.balances),
            "balances": fs.balances_at(body.date)}
