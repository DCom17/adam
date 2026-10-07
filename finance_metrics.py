"""
Adam — deterministic finance metrics (Phase F1).

Every number the Finance dashboard shows is computed HERE, in plain Python, from
the records in finance_store. The LLM never does the arithmetic — it only parses
and categorizes imports upstream. That split is the whole trust model: a finance
feature that let a language model add up your net worth would be unshippable.

These are the formulas ported from the owner's "Finance" sheet Metrics tab
(docs/PLAN-FINANCE-HEALTH.md §3). Balances come from the LATEST balance snapshot;
spending/income come from transactions in a given month. Account TYPE decides
asset-vs-liability rollup:
    cash, investment  -> assets       (investment excluded from liquid cash)
    credit, loan      -> liabilities / total debt

Sign convention (from the sheet's README): income/assets positive, spending/
liabilities negative.

Design notes:
  - Pure over the store: pass a `store` module (defaults to finance_store) so a
    test can seed a throwaway DB and assert exact figures.
  - No wall-clock dependency. `month` is always explicit or derived from the data
    (latest snapshot / newest transaction), never from time.now — so results are
    reproducible and tests are deterministic.
  - Currency rounded to cents on output; ratios (DTI, investment share) to 4 dp;
    EF progress is a 0..1 fraction capped at 1.
"""

from __future__ import annotations

import finance_store as _default_store

# Category `type` values that are NOT real spending (they move money between the
# user's own buckets or into assets) and so are excluded from spend totals.
_NON_SPEND_TYPES = {"Transfer", "Investment"}
_INCOME_TYPES = {"Income"}
_DEBT_SERVICE_TYPE = "Debt Service"

# A transaction at or above this magnitude is flagged "Review" in the top-flex
# list; smaller ones are "Watch". Matches the sheet's high-cost highlighting.
_FLEX_REVIEW_THRESHOLD = 100.0


def _round2(x: float) -> float:
    return round(float(x), 2)


def _category_index(store) -> dict[str, dict]:
    return {c["name"]: c for c in store.get_categories()}


def _account_types(store) -> dict[str, str]:
    return {a["name"]: a["type"] for a in store.get_accounts()}


# --- Balance-derived figures (from the latest snapshot) ---------------------

def balance_figures(store=_default_store, date: str | None = None) -> dict:
    """Assets / liabilities / net worth / liquid cash / investments / total debt
    from a snapshot date (default: the latest). An account with no recorded type
    is classified by the sign of its balance (positive=asset, negative=liability)
    so a partially-configured install still returns sane numbers."""
    d = date or store.latest_snapshot_date()
    if not d:
        return {
            "snapshot_date": None, "assets": 0.0, "liabilities": 0.0,
            "net_worth": 0.0, "liquid_cash": 0.0, "total_investments": 0.0,
            "total_debt": 0.0, "card_balances": 0.0, "investment_share": 0.0,
        }
    types = _account_types(store)
    assets = liabilities = liquid = investments = debt = card = 0.0
    for acct, bal in store.balances_at(d).items():
        typ = types.get(acct)
        if typ == "cash":
            liquid += bal; assets += bal
        elif typ == "investment":
            investments += bal; assets += bal
        elif typ == "credit":
            card += abs(bal); debt += abs(bal); liabilities += abs(bal)
        elif typ == "loan":
            debt += abs(bal); liabilities += abs(bal)
        else:
            # Unknown type: infer from sign.
            if bal < 0:
                debt += abs(bal); liabilities += abs(bal)
            else:
                liquid += bal; assets += bal
    total_assets = assets
    share = (investments / total_assets) if total_assets else 0.0
    return {
        "snapshot_date": d,
        "assets": _round2(assets),
        "liabilities": _round2(liabilities),
        "net_worth": _round2(assets - liabilities),
        "liquid_cash": _round2(liquid),
        "total_investments": _round2(investments),
        "total_debt": _round2(debt),
        "card_balances": _round2(card),
        "investment_share": round(share, 4),
    }


# --- Transaction-derived monthly figures ------------------------------------

def monthly_spending_by_category(store=_default_store, month: str = "") -> list[dict]:
    """Real spending in `month` grouped by category, largest first. Excludes
    transfers, investments, and income — only money that actually left."""
    cats = _category_index(store)
    totals: dict[str, float] = {}
    for t in store.list_transactions(month=month):
        cat = t["category"]
        meta = cats.get(cat)
        ctype = meta["type"] if meta else "Expense"
        if ctype in _NON_SPEND_TYPES or ctype in _INCOME_TYPES:
            continue
        if t["amount"] < 0:  # spending only
            totals[cat] = totals.get(cat, 0.0) + abs(t["amount"])
    out = [{"category": k, "spend": _round2(v)} for k, v in totals.items()]
    out.sort(key=lambda r: r["spend"], reverse=True)
    return out


def monthly_essentials(store=_default_store, month: str = "") -> float:
    """Total spend in `month` on categories flagged essential — the base for the
    emergency-fund target."""
    cats = _category_index(store)
    total = 0.0
    for t in store.list_transactions(month=month):
        meta = cats.get(t["category"])
        if meta and meta["essential"] and t["amount"] < 0:
            total += abs(t["amount"])
    return _round2(total)


def monthly_income(store=_default_store, month: str = "") -> float:
    """Gross income recorded in `month` (positive Income-type transactions)."""
    cats = _category_index(store)
    total = 0.0
    for t in store.list_transactions(month=month):
        meta = cats.get(t["category"])
        if meta and meta["type"] in _INCOME_TYPES and t["amount"] > 0:
            total += t["amount"]
    return _round2(total)


def monthly_debt_payments_from_txns(store=_default_store, month: str = "") -> float:
    """Debt-service spending recorded in `month` (fallback when the fixed
    monthly_debt_payments setting is unset)."""
    cats = _category_index(store)
    total = 0.0
    for t in store.list_transactions(month=month):
        meta = cats.get(t["category"])
        if meta and meta["type"] == _DEBT_SERVICE_TYPE and t["amount"] < 0:
            total += abs(t["amount"])
    return _round2(total)


def top_flex_transactions(store=_default_store, month: str = "", n: int = 5) -> list[dict]:
    """The `n` largest single spends in `month` (excluding transfers/investments/
    income), each flagged Review (>= $100) or Watch. Mirrors the sheet's
    'top high-cost flex transactions' highlight."""
    cats = _category_index(store)
    spends = []
    for t in store.list_transactions(month=month):
        meta = cats.get(t["category"])
        ctype = meta["type"] if meta else "Expense"
        if ctype in _NON_SPEND_TYPES or ctype in _INCOME_TYPES:
            continue
        if t["amount"] < 0:
            amt = abs(t["amount"])
            spends.append({
                "date": t["date"],
                "category": t["category"],
                "merchant": t["merchant"] or t["raw_desc"],
                "account": t["account"],
                "amount": _round2(amt),
                "flag": "Review" if amt >= _FLEX_REVIEW_THRESHOLD else "Watch",
            })
    spends.sort(key=lambda r: r["amount"], reverse=True)
    return spends[:n]


# --- Cash-safety system + DTI -----------------------------------------------

def cash_safety(store=_default_store, month: str = "", *,
                balances: dict | None = None, essentials: float | None = None) -> dict:
    """Emergency-fund target, buffer, cash-safety target, card reserve, and the
    resulting investable cash — the sheet's 'Cash Safety System' block.

    EF target      = ef_months x monthly essentials(month)
    Cash safety    = EF target + extra buffer
    Card reserve   = setting, else the sum of credit-card balances
    Investable     = liquid cash - EF target - buffer - card reserve
    EF progress    = min(1, liquid cash / EF target)

    `essentials` overrides the one-month figure with a steadier baseline (the
    dashboard passes finance_history.baseline()'s average of recent complete
    months, so a half-finished month can't shrink the emergency-fund target).
    """
    bal = balances or balance_figures(store)
    settings = store.get_settings()
    ef_months = float(settings.get("ef_months") or 0)
    buffer = float(settings.get("extra_buffer") or 0)

    essentials = (_round2(essentials) if essentials is not None
                  else monthly_essentials(store, month))
    ef_target = _round2(ef_months * essentials)
    cash_safety_target = _round2(ef_target + buffer)

    reserve_setting = settings.get("card_payoff_reserve")
    card_reserve = (_round2(reserve_setting) if reserve_setting is not None
                    else bal["card_balances"])

    liquid = bal["liquid_cash"]
    investable = _round2(liquid - ef_target - buffer - card_reserve)
    ef_progress = round(min(1.0, liquid / ef_target), 4) if ef_target > 0 else 0.0
    return {
        "monthly_essentials": essentials,
        "ef_months": ef_months,
        "ef_target": ef_target,
        "extra_buffer": _round2(buffer),
        "cash_safety_target": cash_safety_target,
        "card_payoff_reserve": _round2(card_reserve),
        "investable_cash": investable,
        "ef_progress": ef_progress,
    }


def dti(store=_default_store, month: str = "", *, balances: dict | None = None,
        income_fallback: float | None = None,
        payments_fallback: float | None = None) -> dict:
    """Debt-to-income ratio and a plain-language status label.

    Monthly debt payments = setting, else summed Debt-Service spending in `month`.
    Gross monthly income  = setting, else summed Income in `month`.
    DTI = monthly debt payments / gross monthly income (None if income is 0).
    """
    settings = store.get_settings()
    dp_setting = settings.get("monthly_debt_payments")
    if dp_setting is not None:
        payments = _round2(dp_setting)
    elif payments_fallback is not None:
        payments = _round2(payments_fallback)
    else:
        payments = monthly_debt_payments_from_txns(store, month)
    inc_setting = settings.get("gross_monthly_income")
    if inc_setting is not None:
        income = _round2(inc_setting)
    elif income_fallback is not None:
        income = _round2(income_fallback)
    else:
        income = monthly_income(store, month)
    ratio = round(payments / income, 4) if income > 0 else None
    if ratio is None:
        status = "Unknown"
    elif ratio < 0.20:
        status = "Manageable"
    elif ratio < 0.36:
        status = "Watch"
    else:
        status = "High"
    return {
        "monthly_debt_payments": payments,
        "gross_monthly_income": income,
        "dti": ratio,
        "debt_status": status,
    }


# --- Data health ------------------------------------------------------------

def needs_review(store=_default_store) -> dict:
    """The actual committed transactions that need attention (the data-health
    counters, but the rows themselves so the UI can fix them):
      - unmapped: category is the review bucket or names a category that no longer
        exists in the taxonomy;
      - unmatched_transfers: a Transfer-type transaction whose pair-key group
        doesn't net to ~zero — a LINK that's broken, which is a real data error.

    A transfer leg with no pair key at all is NOT flagged. You almost never import
    both sides of a transfer (statements are exported one account at a time), so a
    lonely leg is the normal case, not a mistake — and there is nothing the user can
    do about it, since the matching row simply isn't in the ledger. It also cannot
    skew any figure: Transfer-type rows are excluded from spend and income, and
    balances come from snapshots, never from transactions. finance_import.classify_row
    has always taken this position on import; this is the dashboard agreeing with it
    instead of dumping every card payment into the review queue.
    """
    cats = _category_index(store)
    txns = store.list_transactions()

    unmapped = [t for t in txns
                if t["category"] == store.REVIEW_CATEGORY or t["category"] not in cats]

    pair_groups: dict[str, list] = {}
    transfers = []
    for t in txns:
        if t.get("transfer_ack"):
            continue  # user confirmed it's a real transfer; stop flagging it
        meta = cats.get(t["category"])
        if meta and meta["type"] == "Transfer":
            transfers.append(t)
            if t["transfer_pair_key"]:
                pair_groups.setdefault(t["transfer_pair_key"], []).append(t)
    unmatched = []
    for t in transfers:
        key = t["transfer_pair_key"]
        if not key:
            continue  # lonely leg — normal, not actionable (see the docstring)
        if abs(sum(x["amount"] for x in pair_groups.get(key, []))) > 0.01:
            unmatched.append(t)

    return {"unmapped": unmapped, "unmatched_transfers": unmatched}


def data_health(store=_default_store) -> dict:
    """The review counters the dashboard surfaces. Overall status reads
    'All Clear' only when every counter is zero."""
    cats = _category_index(store)

    # Unmapped: category is the review bucket, or names a category that no longer
    # exists in the taxonomy.
    unmapped = 0
    for t in store.list_transactions():
        if t["category"] == store.REVIEW_CATEGORY or t["category"] not in cats:
            unmapped += 1

    # Unmatched transfers: a Transfer-type transaction whose pair-key group doesn't
    # net to ~zero across its legs (a broken link). An UNPAIRED leg is not counted —
    # see needs_review()'s docstring for why a lonely leg is normal and harmless.
    pair_sums: dict[str, float] = {}
    for t in store.list_transactions():
        if t.get("transfer_ack"):
            continue  # acknowledged as a real transfer — matches needs_review()
        meta = cats.get(t["category"])
        if not (meta and meta["type"] == "Transfer"):
            continue
        key = t["transfer_pair_key"]
        if key:
            pair_sums[key] = pair_sums.get(key, 0.0) + t["amount"]
    unmatched_transfers = sum(1 for s in pair_sums.values() if abs(s) > 0.01)

    # Missing snapshot accounts: accounts expected in every snapshot that are
    # absent from the latest one.
    missing = 0
    latest = store.latest_snapshot_date()
    if latest:
        present = set(store.balances_at(latest).keys())
        for a in store.get_accounts():
            if a["expected_in_snapshot"] and a["name"] not in present:
                missing += 1

    unreviewed_batches = store.count_unreviewed_batches()

    counters = {
        "unmapped_categories": unmapped,
        "unmatched_transfers": unmatched_transfers,
        "missing_snapshot_accounts": missing,
        "unreviewed_import_batches": unreviewed_batches,
    }
    # The status comes from the SAME list the Review tab renders
    # (finance_history.checks). It used to be "any counter non-zero", which
    # counted accounts missing from the snapshot that the Review tab never
    # listed — the page said "Needs Review" over an empty queue.
    import finance_history as _fh  # local: finance_history imports this module
    ck = _fh.checks(store)
    counters["open_items"] = ck["open"]
    counters["dismissed_items"] = ck["dismissed"]
    counters["by_kind"] = ck["by_kind"]
    counters["review_status"] = ck["status"]
    return counters


# --- Advice -----------------------------------------------------------------

def advice(investable_cash: float, ef_progress: float) -> str:
    """A deterministic one-line recommendation from the cash-safety numbers.
    Thresholds are fixed and documented — no model judgment."""
    if ef_progress < 1.0:
        return "Build your emergency fund before investing."
    if investable_cash <= 0:
        return "Hold — no investable cash above your safety target."
    if investable_cash < 1000:
        return "Consider a small investment."
    return "You have room to invest."


# --- Full dashboard summary -------------------------------------------------

def _default_month(store) -> str:
    """The month the dashboard shows by default: the NEWEST transaction's month,
    else the latest snapshot's month, else empty (no data yet). Never reads the
    wall clock, so it's reproducible.

    Transactions lead, snapshots follow. Balance snapshots are entered by hand and
    go stale the moment you stop entering them, so keying the view off the snapshot
    date parks the dashboard in an old month and makes a fresh import look like it
    did nothing — the whole point of importing is to see the new activity."""
    txns = store.list_transactions(limit=1)
    if txns:
        return txns[0]["date"][:7]
    latest = store.latest_snapshot_date()
    if latest:
        return latest[:7]
    return ""


def _account_balances(store, as_of: str | None) -> dict:
    """Per-account balances from the newest snapshot on or before `as_of` (else the
    latest), for the expanded cards. Liabilities reported as positive amounts owed."""
    dates = store.snapshot_dates()
    if not dates:
        return {"date": None, "accounts": []}
    use = [d for d in dates if not as_of or d <= as_of]
    d = use[-1] if use else dates[0]
    types = _account_types(store)
    out = []
    for acct, bal in sorted(store.balances_at(d).items()):
        typ = types.get(acct) or ("credit" if bal < 0 else "cash")
        owed = typ in ("credit", "loan")
        out.append({"account": acct, "type": typ,
                    "balance": _round2(abs(bal) if owed else bal)})
    return {"date": d, "accounts": out}


def summary(store=_default_store, month: str | None = None, *,
            start: str | None = None, end: str | None = None) -> dict:
    """Assemble the whole Finance dashboard payload for a month (default: the
    newest month with data) or an explicit [start, end] date range.

    Balances are for the END of the period: the hand-entered snapshot when one
    falls on that day, otherwise reconstructed from the transactions between
    snapshots (finance_history). Spending / income / top transactions cover the
    whole period. Cash safety and debt-to-income use monthly baselines from recent
    complete months so a part-month never distorts them."""
    import finance_history as fh  # local: finance_history imports this module
    from datetime import date as _dt, timedelta as _td

    if start and end:
        mode = "range"
        if start > end:
            start, end = end, start
        m = end[:7]
    else:
        mode = "month"
        m = month if month is not None else _default_month(store)
        if m:
            start = m + "-01"
            end = fh._iso(fh._month_end(m))
    ser = fh.series(store)
    data_end = ser["end"]
    as_of = min(end, data_end) if (end and data_end) else (end or data_end)

    if ser["points"]:
        bal = fh.figures_on(store, as_of, _series=ser)
        before = (_dt.fromisoformat(start) - _td(days=1)).isoformat() if start else None
        prev = (fh.figures_on(store, before, _series=ser)
                if before and before >= ser["start"] else None)
    else:
        latest = balance_figures(store)
        bal = {"date": latest["snapshot_date"], "basis": "actual" if latest["snapshot_date"] else "none",
               "thin": False, "net_worth": latest["net_worth"], "liquid_cash": latest["liquid_cash"],
               "total_debt": latest["total_debt"], "card_balances": latest["card_balances"],
               "total_investments": latest["total_investments"]}
        prev = None
    assets = _round2(bal["liquid_cash"] + bal["total_investments"])
    bal_block = {"liquid_cash": bal["liquid_cash"], "card_balances": bal["card_balances"]}

    base = fh.baseline(store, end)
    cs = cash_safety(store, m or "", balances=bal_block,
                     essentials=base["essentials"] if base["essentials_months"] else None)
    cs["essentials_months"] = base["essentials_months"]
    d = dti(store, m or "", balances=bal_block,
            income_fallback=base["income"] if base["income_months"] else None,
            payments_fallback=base["debt_payments"] if base["debt_months"] else None)
    d["income_months"] = base["income_months"]
    health = data_health(store)
    rep = fh.period_report(store, start, end) if (start and end) else None

    newest_txn = store.list_transactions(limit=1)
    newest_txn_date = newest_txn[0]["date"] if newest_txn else None
    snap = store.latest_snapshot_date()
    change = None
    if prev:
        change = {"from": prev["date"],
                  **{k: _round2(bal[k] - prev[k]) for k in
                     ("net_worth", "liquid_cash", "total_debt", "total_investments")}}
    return {
        "mode": mode,
        "month": m,
        "start": start,
        "end": end,
        "as_of": bal["date"],
        "balances_basis": bal["basis"],
        "balances_thin": bool(bal.get("thin")),
        "snapshot_date": snap,
        "newest_transaction_date": newest_txn_date,
        "data_start": ser["start"],
        "data_end": data_end,
        # Past the last snapshot the figures are rolled forward from transactions
        # alone — say so instead of presenting them as typed-in balances.
        "balances_stale": bool(snap and bal["date"] and bal["date"] > snap),
        "net_worth": bal["net_worth"],
        "assets": assets,
        "liabilities": bal["total_debt"],
        "liquid_cash": bal["liquid_cash"],
        "total_debt": bal["total_debt"],
        "card_balances": bal["card_balances"],
        "total_investments": bal["total_investments"],
        "investment_share": round(bal["total_investments"] / assets, 4) if assets > 0 else 0.0,
        "change": change,
        "account_balances": _account_balances(store, bal["date"]),
        "cash_safety": cs,
        "dti": d,
        "data_health": health,
        "spending_by_category": rep["spending_by_category"] if rep else [],
        "top_flex_transactions": rep["top_transactions"][:5] if rep else [],
        "report": rep,
        "advice": advice(cs["investable_cash"], cs["ef_progress"]),
    }
