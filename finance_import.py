"""
Adam — finance import + review pipeline (Phase F2).

This is Adam's private, in-app replacement for the owner's weekly ChatGPT loop
("export CSVs → send to ChatGPT → paste into the sheet → review"). A statement
(CSV, pasted text, later a photo) comes in; the ONE non-deterministic step — turn
messy bank rows into the canonical transaction schema — is done by Claude behind
an injectable seam (`parse_text`), and everything after that is deterministic,
testable Python:

    normalize → auto-categorize via merchant rules → dedup (vs the ledger AND
    within the batch) → pair internal transfers → stage for review

Nothing lands in the real `transactions` ledger on import. Rows sit in
`staged_transactions` until the user approves the batch, at which point the
non-duplicate rows are committed and the batch is marked reviewed. Approving an
edited categorization teaches a merchant rule, so the next import categorizes
itself — the learning loop the sheet's "Merchant Rules" tab modeled.

Design rules (from docs/PLAN-FINANCE-HEALTH.md):
  - The LLM parses; Python does everything numeric and every state change.
  - The parse runner is injected (`runner=`), so tests exercise the whole
    pipeline with canned model output and never spawn a real claude.exe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time

import config
import finance_store as fs

# Transfer legs match if their dates are within this many days of each other.
_TRANSFER_WINDOW_DAYS = 3


# --- Row normalization ------------------------------------------------------

def _to_float(v) -> float:
    """Parse an amount that may arrive as a number or a string like '$1,234.56'
    or '(45.00)' (parenthetary negative). Returns 0.0 on anything unparseable."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").strip()
    try:
        val = float(s)
    except ValueError:
        return 0.0
    return -val if neg else val


def _iso_date(v) -> str:
    """Best-effort normalize a date to YYYY-MM-DD. Accepts already-ISO strings and
    common US M/D/Y forms; otherwise returns the trimmed input unchanged (the LLM
    is asked for ISO, so this is a safety net, not the primary parser)."""
    s = str(v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", s)
    if m:
        mo, day, yr = m.groups()
        yr = ("20" + yr) if len(yr) == 2 else yr
        return f"{int(yr):04d}-{int(mo):02d}-{int(day):02d}"
    return s


def normalize_row(raw: dict, default_account: str = "") -> dict:
    """Turn one loosely-shaped parsed row into the canonical staging shape. Accepts
    a few common key spellings the model might emit (description/desc/memo,
    amount/value)."""
    def pick(*keys, default=""):
        for k in keys:
            if k in raw and raw[k] not in (None, ""):
                return raw[k]
        return default

    account = str(pick("account", "account_name", default=default_account)).strip()
    return {
        "date": _iso_date(pick("date", "posted", "transaction_date")),
        "account": account or default_account,
        "institution": str(pick("institution", "bank")).strip(),
        "account_type": str(pick("account_type", "type")).strip(),
        "raw_desc": str(pick("raw_desc", "description", "desc", "memo", "merchant")).strip(),
        "merchant": str(pick("merchant", "clean_merchant")).strip(),
        "amount": _to_float(pick("amount", "value", default=0)),
        "category": str(pick("category")).strip(),
        "transfer_pair_key": str(pick("transfer_pair_key")).strip(),
        "notes": str(pick("notes")).strip(),
    }


# --- Auto-categorization ----------------------------------------------------

def apply_merchant_rule(row: dict) -> dict:
    """If the row has no confident category, try the merchant-rule memory against
    its raw description. Mutates and returns the row. A rule fills in BOTH the
    clean merchant name and the category; a row that already names a real category
    is left alone."""
    cat = row.get("category") or ""
    if cat and cat != fs.REVIEW_CATEGORY:
        return row
    hit = fs.match_merchant_rule(row.get("raw_desc", ""))
    if hit:
        if hit.get("merchant") and not row.get("merchant"):
            row["merchant"] = hit["merchant"]
        row["category"] = hit["category"] or fs.REVIEW_CATEGORY
    else:
        row["category"] = cat or fs.REVIEW_CATEGORY
    return row


# --- Transfer pairing -------------------------------------------------------

def _days_apart(d1: str, d2: str) -> int | None:
    """Whole-day distance between two ISO dates, or None if either isn't ISO."""
    try:
        a = time.strptime(d1, "%Y-%m-%d")
        b = time.strptime(d2, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return abs(int((time.mktime(a) - time.mktime(b)) / 86400))


def pair_transfers(rows: list[dict], category_types: dict[str, str]) -> None:
    """Assign a shared transfer_pair_key to opposite-sign, equal-magnitude legs of
    an internal transfer that land within the window on different accounts. Only
    considers rows whose category is a Transfer-type and that aren't already
    paired. Deterministic: rows are matched in list order, first fit wins.

    Mutates rows in place. Keys look like TRF-<date>-<index> so they're stable and
    readable (no wall-clock/random component that would break test reproducibility)."""
    def is_transfer(r):
        return category_types.get(r.get("category", ""), "") == "Transfer"

    n = len(rows)
    counter = 0
    for i in range(n):
        ri = rows[i]
        if not is_transfer(ri) or ri.get("transfer_pair_key"):
            continue
        for j in range(i + 1, n):
            rj = rows[j]
            if not is_transfer(rj) or rj.get("transfer_pair_key"):
                continue
            if not _accounts_may_pair(ri.get("account", ""), rj.get("account", "")):
                continue
            if abs(ri["amount"] + rj["amount"]) > 0.01:  # must net to ~0
                continue
            if abs(ri["amount"]) < 0.01:
                continue
            dist = _days_apart(ri.get("date", ""), rj.get("date", ""))
            if dist is None or dist > _TRANSFER_WINDOW_DAYS:
                continue
            counter += 1
            key = f"TRF-{ri.get('date', '')}-{counter}"
            ri["transfer_pair_key"] = key
            rj["transfer_pair_key"] = key
            break


# Ledger-wide rematching is more permissive than within-batch pairing: legs of one
# transfer often post several days apart (a card payment leaves checking on the 11th
# and lands on the card on the 17th), and they arrive in SEPARATE imports, so the
# tight in-batch window would never see them together.
_LEDGER_TRANSFER_WINDOW_DAYS = 7


def _accounts_may_pair(a: str, b: str) -> bool:
    """Two legs of an internal transfer must sit on DIFFERENT accounts — but only
    when we actually know both account names. A statement parsed without an account
    column leaves the field blank, and refusing to pair two blanks (they compare
    equal) is what stranded every transfer in an account-less import. Unknown means
    unknown, not 'same'."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return True
    return a != b


def rematch_transfers(store=fs) -> dict:
    """Pair up unlinked Transfer-type transactions across the WHOLE ledger.

    `pair_transfers` only ever sees one import at a time, so the two halves of a
    transfer that arrived in different statements (the normal case — you export one
    account at a time) stay unlinked forever and pile up in the review queue. This
    pass runs over everything already committed: opposite sign, equal magnitude,
    different account (when known), within the window. Matches are made
    closest-date-first so a repeated round number pairs with its nearest
    counterpart instead of an arbitrary one, and every leg is used at most once.

    Returns {'paired': <pairs made>, 'scanned': <unlinked legs considered>}.
    """
    cat_types = {c["name"]: c["type"] for c in store.get_categories()}
    legs = [t for t in store.list_transactions()
            if cat_types.get(t["category"]) == "Transfer"
            and not t["transfer_pair_key"]
            and abs(t["amount"]) >= 0.01]

    # Candidate pairs, cheapest (closest in date) first.
    candidates = []
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            a, b = legs[i], legs[j]
            if abs(a["amount"] + b["amount"]) > 0.01:      # must net to ~zero
                continue
            if not _accounts_may_pair(a["account"], b["account"]):
                continue
            dist = _days_apart(a["date"], b["date"])
            if dist is None or dist > _LEDGER_TRANSFER_WINDOW_DAYS:
                continue
            candidates.append((dist, i, j))
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    used: set[int] = set()
    paired = 0
    for dist, i, j in candidates:
        if i in used or j in used:
            continue
        a, b = legs[i], legs[j]
        paired += 1
        key = f"TRF-{min(a['date'], b['date'])}-{paired}"
        store.update_transaction(a["txn_key"], transfer_pair_key=key)
        store.update_transaction(b["txn_key"], transfer_pair_key=key)
        used.add(i); used.add(j)
    return {"paired": paired, "scanned": len(legs)}


# --- Review-reason classification -------------------------------------------

def classify_row(row: dict, *, is_duplicate: bool, category_types: dict[str, str],
                 known_categories: set[str]) -> tuple[str, str]:
    """Return (status, review_reason) for a staged row.
        status:        'duplicate' | 'pending'
        review_reason: '' when clean, else a short human reason.
    """
    if is_duplicate:
        return "duplicate", "Duplicate of a transaction already recorded"
    cat = row.get("category", "")
    if not cat or cat == fs.REVIEW_CATEGORY or cat not in known_categories:
        return "pending", "Needs a category"
    # NOTE: a Transfer-type leg with no matching pair (a credit-card payment, a
    # move to savings) is NOT flagged for review. You almost never import both
    # accounts at once, so the other leg is simply absent — that's normal, not an
    # error, and there's nothing for the user to act on (no leg to match to). The
    # category is already correct and the metrics engine excludes Transfer-type
    # rows from spend/income, so a lonely leg is handled correctly whether or not
    # it's paired. Pairing still runs (pair_transfers) to LINK legs when both are
    # present; it just doesn't gate the review queue. Only genuinely
    # uncategorizable rows land in review.
    return "pending", ""


# --- Staging ----------------------------------------------------------------

def _default_batch_id(source: str) -> str:
    """A readable, unique-enough batch id. Uses local time — fine for a real
    import; tests always pass an explicit id, so this is never hit under test."""
    return time.strftime("import-%Y%m%d-%H%M%S", time.localtime())


def stage_rows(rows: list[dict], *, batch_id: str | None = None, source: str = "",
               default_account: str = "") -> dict:
    """Run the deterministic pipeline over already-parsed rows and land them in the
    staging area under a new (unreviewed) batch. Returns a summary the review UI
    shows. Nothing touches the `transactions` ledger here."""
    bid = batch_id or _default_batch_id(source)
    fs.create_batch(bid, source=source, reviewed=False)

    cats = {c["name"]: c for c in fs.get_categories()}
    category_types = {name: c["type"] for name, c in cats.items()}
    known_categories = set(cats.keys())

    norm = [normalize_row(r, default_account) for r in rows]
    for r in norm:
        apply_merchant_rule(r)
    # Canonicalize category spelling/case so a model reply of "groceries" or
    # "eating out" maps to the taxonomy's exact "Groceries" / "Eating Out" instead
    # of being falsely flagged "Needs a category". Only exact-after-normalization
    # matches are accepted; a truly unknown category is left as-is (and flagged).
    canon = {name.lower(): name for name in known_categories}
    for r in norm:
        c = (r.get("category") or "").strip()
        if c and c not in known_categories and c.lower() in canon:
            r["category"] = canon[c.lower()]
    pair_transfers(norm, category_types)

    seen_keys: set[str] = set()
    summary = {
        "batch_id": bid, "source": source, "total": len(norm),
        "staged": 0, "duplicates": 0, "needs_review": 0, "ready": 0,
        "transfers_paired": 0,
    }
    paired_keys: set[str] = set()

    for r in norm:
        key = fs.make_txn_key(r["date"], r["account"], r["raw_desc"], r["amount"])
        # Duplicate if it already exists in the ledger, or repeats within this batch.
        is_dup = (fs.get_transaction(key) is not None) or (key in seen_keys)
        seen_keys.add(key)
        status, reason = classify_row(
            r, is_duplicate=is_dup, category_types=category_types,
            known_categories=known_categories,
        )
        fs.add_staged(
            bid, r["date"], r["account"], r["raw_desc"], r["amount"], key,
            category=r["category"], merchant=r["merchant"],
            institution=r["institution"], account_type=r["account_type"],
            transfer_pair_key=r["transfer_pair_key"], notes=r["notes"],
            status=status, review_reason=reason,
        )
        summary["staged"] += 1
        if status == "duplicate":
            summary["duplicates"] += 1
        elif reason:
            summary["needs_review"] += 1
        else:
            summary["ready"] += 1
        if r["transfer_pair_key"]:
            paired_keys.add(r["transfer_pair_key"])
    summary["transfers_paired"] = len(paired_keys)
    return summary


def review_summary(batch_id: str) -> dict:
    """The review-queue payload for one batch: its rows split into ready / needs-
    review / duplicates, plus counts."""
    rows = fs.list_staged(batch_id=batch_id)
    ready, needs, dupes = [], [], []
    for r in rows:
        if r["status"] == "duplicate":
            dupes.append(r)
        elif r["review_reason"]:
            needs.append(r)
        else:
            ready.append(r)
    batch = next((b for b in fs.list_batches() if b["id"] == batch_id), None)
    return {
        "batch_id": batch_id,
        "batch": batch,
        "counts": {"ready": len(ready), "needs_review": len(needs),
                   "duplicates": len(dupes), "total": len(rows)},
        "ready": ready, "needs_review": needs, "duplicates": dupes,
    }


def _reclassify(staged_id: int) -> dict | None:
    """Recompute status/review_reason for one staged row after an edit, and (when
    the user has just given an uncategorized row a real category) teach a merchant
    rule so the next import maps it automatically."""
    row = fs.get_staged(staged_id)
    if not row:
        return None
    cats = {c["name"]: c for c in fs.get_categories()}
    category_types = {name: c["type"] for name, c in cats.items()}
    is_dup = row["status"] == "duplicate"
    status, reason = classify_row(
        row, is_duplicate=is_dup, category_types=category_types,
        known_categories=set(cats.keys()),
    )
    fs.update_staged(staged_id, status=status, review_reason=reason)
    return fs.get_staged(staged_id)


def edit_staged(staged_id: int, *, category: str | None = None,
                merchant: str | None = None, notes: str | None = None,
                transfer_pair_key: str | None = None,
                teach_rule: bool = True) -> dict | None:
    """Apply a user edit to a staged row, re-run its classification, and (by
    default) remember the merchant→category mapping so future imports auto-map it.
    Returns the updated row, or None if the id is unknown."""
    row = fs.get_staged(staged_id)
    if not row:
        return None
    patch = {}
    if category is not None:
        patch["category"] = category
    if merchant is not None:
        patch["merchant"] = merchant
    if notes is not None:
        patch["notes"] = notes
    if transfer_pair_key is not None:
        patch["transfer_pair_key"] = transfer_pair_key
    if patch:
        fs.update_staged(staged_id, **patch)

    # Teach a rule when a real category was set on a row that has a description to
    # key off of. A short, distinctive slice of the raw description is the pattern.
    if teach_rule and category and category != fs.REVIEW_CATEGORY:
        raw = (row.get("raw_desc") or "").strip()
        if raw:
            pattern = raw.upper()[:40].strip()
            merch = merchant if merchant is not None else row.get("merchant", "")
            fs.upsert_merchant_rule(pattern, merch or "", category)
    return _reclassify(staged_id)


def approve_batch(batch_id: str) -> dict:
    """Commit the batch: every non-duplicate staged row is written into the real
    `transactions` ledger (already reviewed), the batch is marked reviewed, and the
    staging rows for it are cleared. Duplicates are dropped, not committed. Returns
    a summary of what happened."""
    rows = fs.list_staged(batch_id=batch_id)
    committed = skipped_dupes = 0
    for r in rows:
        if r["status"] == "duplicate":
            skipped_dupes += 1
            continue
        res = fs.add_transaction(
            r["date"], r["account"], r["raw_desc"], r["amount"],
            category=r["category"], merchant=r["merchant"],
            institution=r["institution"], account_type=r["account_type"],
            transfer_pair_key=r["transfer_pair_key"], notes=r["notes"],
            source_batch=batch_id, reviewed=True, txn_key=r["txn_key"],
        )
        if res["inserted"]:
            committed += 1
        else:
            # Raced or re-approved: the ledger already had it. Count as a dupe skip.
            skipped_dupes += 1
    fs.mark_batch_reviewed(batch_id, True)
    fs.delete_staged_for_batch(batch_id)
    # The other half of a transfer usually arrives in a DIFFERENT statement, so the
    # moment new rows land is exactly when previously-lonely legs become matchable.
    # Run the ledger-wide pass here so the review queue settles itself instead of
    # asking the user to hand-confirm every leg.
    rematched = rematch_transfers()
    return {"batch_id": batch_id, "committed": committed,
            "skipped_duplicates": skipped_dupes,
            "transfers_paired": rematched["paired"]}


def discard_batch(batch_id: str) -> dict:
    """Throw away an unreviewed batch: drop its staged rows and the batch record.
    Nothing was ever in the ledger, so this is a clean undo of an import."""
    dropped = fs.delete_staged_for_batch(batch_id)
    # Remove the batch row itself so it stops showing as unreviewed.
    with fs._LOCK:
        c = fs._conn()
        c.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))
        c.commit()
    return {"batch_id": batch_id, "discarded_rows": dropped}


# --- LLM parse adapter (the one non-deterministic step) ---------------------

_PARSE_SYSTEM_PROMPT = (
    "You convert a raw bank/credit-card statement into structured transactions. "
    "Output ONLY a JSON array — no prose, no markdown fences. Each element is an "
    "object with these keys:\n"
    '  "date"        (string, YYYY-MM-DD)\n'
    '  "description" (string, the raw merchant/description text)\n'
    '  "amount"      (number; NEGATIVE for money leaving the account/spending, '
    "POSITIVE for money coming in/income/credits)\n"
    '  "account"     (string, optional — the account name if the statement names it)\n'
    '  "category"    (string — the single best-fit category, using the EXACT '
    "spelling from the provided list). Assign one to EVERY transaction you can "
    "reasonably recognize — that is almost all of them. You know these merchants: a "
    "grocery/supermarket store → Groceries; a gas/fuel station → Gas; a "
    "restaurant, cafe, fast food, or food-delivery → Eating Out; an electric / "
    "water / gas / internet / trash bill → Utilities; a phone carrier → Phone; a "
    "streaming, software, or membership subscription → Subscriptions; a retail / "
    "online store → Shopping; a paycheck or direct deposit → Paycheck; a PAYMENT "
    "TO a credit card (e.g. 'AUTOMATIC PAYMENT', 'PAYMENT THANK YOU', 'CARDMEMBER "
    "PMT') → Credit Card Payment; a move between the person's own accounts → "
    "Transfer; a loan or financing payment → Debt Payment; interest, service "
    "charges, or late fees → Fees / Interest; an ATM withdrawal → Cash / ATM. Use "
    "your judgment for the rest. OMIT the category key ONLY for a genuinely opaque "
    "code with no recognizable merchant — this should be rare, a few rows at most, "
    "not the default.\n"
    "Do not invent transactions. Do not include running balances or summary rows. "
    "Never guess the date or amount — read them exactly as shown."
)


def _extract_json_array(text: str) -> list[dict]:
    """Pull a JSON array of row objects out of a model reply that may be wrapped in
    prose or ```json fences. Returns [] if nothing parseable is found."""
    if not text:
        return []
    t = text.strip()
    # Strip a leading/trailing markdown fence if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    # Fall back to the outermost [ ... ] span.
    if not t.startswith("["):
        start, end = t.find("["), t.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        t = t[start:end + 1]
    try:
        data = json.loads(t)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _default_runner(system_prompt: str, message: str,
                    timeout: int = config.CLAUDE_TIMEOUT_SECONDS) -> str:
    """Invoke claude.exe for a single, tool-less, read-only extraction turn and
    return its raw text `result`. Mirrors run_claude's spawn conventions (model
    selection, auth isolation, long-input-on-stdin) but deliberately carries none
    of the voice brain/system prompt — this is a pure text->JSON transform.

    Kept synchronous; the endpoint runs it via asyncio.to_thread. Not exercised in
    tests (they inject a fake runner)."""
    exe = config.CLAUDE_EXE
    if not exe:
        raise RuntimeError("Claude executable not configured (config.CLAUDE_EXE).")
    cmd = [exe, "-p", "--output-format", "json"]
    if config.VOICE_MODEL and config.VOICE_MODEL.lower() != "default":
        cmd += ["--model", config.VOICE_MODEL]
    # No tools at all: parsing needs only the text handed in on the prompt.
    cmd += ["--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash",
            "Read", "WebFetch", "WebSearch"]
    cmd += ["--append-system-prompt", system_prompt]

    stdin_payload = None
    argv_chars = sum(len(a) + 1 for a in cmd) + len(message)
    if argv_chars > 28000:
        stdin_payload = message.encode("utf-8")
    else:
        cmd += [message]

    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)
    if config.AUTH_MODE == "api_key" and config.ANTHROPIC_API_KEY:
        child_env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    proc = subprocess.run(
        cmd, input=stdin_payload, capture_output=True, timeout=timeout,
        env=child_env,
    )
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    try:
        payload = json.loads(out)
        return payload.get("result", "") if isinstance(payload, dict) else out
    except ValueError:
        return out


_BALANCE_SYSTEM_PROMPT = (
    "You read account balances from a screenshot of a banking or brokerage app. Use "
    "the Read tool to view the image at the path given, then output ONLY a JSON array "
    "— no prose, no markdown fences. One object per account balance you can see:\n"
    '  "name"    (string, the account name/label shown)\n'
    '  "balance" (number; POSITIVE for cash/checking/savings/investment balances, '
    "NEGATIVE for money OWED on a credit card or loan)\n"
    '  "last4"   (string, the last 4 digits of the account if shown, else "")\n'
    "If only one balance is visible, return a single-element array. Read the numbers "
    "exactly as shown; do not invent accounts."
)


def _default_image_runner(image_path: str, timeout: int = config.CLAUDE_TIMEOUT_SECONDS) -> str:
    """Drive claude.exe for a single vision turn that Reads a balance screenshot and
    returns the JSON. Read stays enabled (it must view the file); the image's folder
    is granted via --add-dir; write/shell tools are denied. Not exercised in tests."""
    exe = config.CLAUDE_EXE
    if not exe:
        raise RuntimeError("Claude executable not configured (config.CLAUDE_EXE).")
    folder = os.path.dirname(os.path.abspath(image_path))
    cmd = [exe, "-p", "--output-format", "json"]
    if config.VOICE_MODEL and config.VOICE_MODEL.lower() != "default":
        cmd += ["--model", config.VOICE_MODEL]
    cmd += ["--add-dir", folder]
    cmd += ["--disallowedTools", "Write", "Edit", "NotebookEdit", "Bash", "WebFetch", "WebSearch"]
    cmd += ["--append-system-prompt", _BALANCE_SYSTEM_PROMPT,
            f"Read the account balance(s) in this image: {image_path}"]
    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)
    if config.AUTH_MODE == "api_key" and config.ANTHROPIC_API_KEY:
        child_env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=child_env)
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    try:
        payload = json.loads(out)
        return payload.get("result", "") if isinstance(payload, dict) else out
    except ValueError:
        return out


def read_balances_from_image(image_path: str, *, runner=_default_image_runner) -> list[dict]:
    """Read one or more account balances from a screenshot via the injected runner.
    Returns [{name, balance, last4}], numbers normalized. Empty if none read."""
    rows = _extract_json_array(runner(image_path))
    out = []
    for r in rows:
        name = str(r.get("name") or r.get("account") or r.get("label") or "").strip()
        bal = _to_float(r.get("balance", r.get("amount")))
        if not name and bal == 0.0:
            continue
        out.append({"name": name, "balance": round(bal, 2), "last4": str(r.get("last4") or "").strip()})
    return out


def parse_text(raw: str, *, accounts: list[str] | None = None,
               categories: list[str] | None = None, default_account: str = "",
               runner=_default_runner) -> list[dict]:
    """Parse a raw statement (CSV/text) into canonical rows via the injected runner
    (Claude by default). The runner returns raw model text; this builds the prompt,
    extracts the JSON array, and normalizes each row. `runner` is swapped for a
    fake in tests, so the whole pipeline is exercised without a real model."""
    if not (raw or "").strip():
        return []
    context = []
    if accounts:
        context.append("Known account names: " + ", ".join(accounts) + ".")
    if categories:
        context.append("Allowed categories (use EXACT spelling, or omit): "
                       + ", ".join(categories) + ".")
    if default_account:
        context.append(f"If a row names no account, assume: {default_account}.")
    message = ("\n".join(context) + "\n\nStatement:\n" + raw) if context else raw
    text = runner(_PARSE_SYSTEM_PROMPT, message)
    rows = _extract_json_array(text)
    return [normalize_row(r, default_account) for r in rows]
