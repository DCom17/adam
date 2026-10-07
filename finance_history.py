"""
Adam — finance history: balances for ANY date, period reports, and data checks.

finance_metrics answers "what does the latest snapshot say". This module answers
the questions a ledger with months of imported activity can answer too:

  * What was my net worth / cash / debt at the end of August, when I never
    typed in an August snapshot?            -> figures_on(), series()
  * What did I spend over the last 3 / 6 / 12 months, by category and month?
                                            -> period_report()
  * Is anything in the data inconsistent?   -> checks()

Same trust model as the rest of the Finance lane: plain deterministic Python over
finance_store, no model judgment, no wall clock (the "end" of the data is the
newest transaction or snapshot, never time.now()).

How balances between snapshots are reconstructed
------------------------------------------------
Hand-entered snapshots are ANCHORS (exact). Every other day is estimated from the
transactions:

  * Net worth only moves on real money in/out: income, spending, refunds, fees,
    interest. Transfers between your own accounts, investment contributions and
    debt-principal payments shuffle money between buckets, so they are excluded.
    That makes the net-worth estimate independent of which account a row was
    imported under — the blank-account imports still count.
  * Between two snapshots A and B the estimate starts at A, adds each day's flow,
    and spreads whatever the transactions DON'T explain (the residual: market
    moves, interest, statements never imported) evenly across the gap, so the
    curve lands exactly on B. That residual is also reported by checks() — a big
    one means missing data.
  * Before the first snapshot the estimate rolls BACKWARD from it; after the last
    one it rolls FORWARD. Nothing checks those stretches, so they are labelled
    'projected' rather than 'bridged'.
  * Debt and investments are bridged the same way from the rows on credit/loan and
    investment accounts; liquid cash is derived (net worth - investments + debt) so
    the three always add up.

Sign convention: snapshots store liabilities as negatives, but a card balance
typed as a positive number is read as an amount owed (a credit/loan account's
balance is always taken as abs()) — the same rule balance_figures() applies.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date as _date, timedelta

import finance_store as _default_store
import finance_metrics as fm

# Category types that move money between the user's own buckets (or pay down
# principal) — they never change net worth.
_NW_NEUTRAL_TYPES = {"Transfer", "Investment", "Debt Service"}
_SPEND_EXCLUDED_TYPES = {"Transfer", "Investment", "Income"}
_LIABILITY_TYPES = {"credit", "loan"}

# Duplicate detection: same amount, dates within this many days (statement
# exports disagree on transaction vs post date), different import batch.
_DUP_WINDOW_DAYS = 2
# A gap between consecutive snapshots that transactions don't explain is flagged
# once it is bigger than this many dollars AND this share of net worth.
_GAP_MIN_DOLLARS = 150.0
_GAP_MIN_SHARE = 0.03
# Per-account version of the same check.
_ACCT_GAP_MIN_DOLLARS = 50.0


def _r2(x: float) -> float:
    return round(float(x) + 0.0, 2)


def _d(s: str) -> _date:
    return _date.fromisoformat(s[:10])


def _iso(d: _date) -> str:
    return d.isoformat()


def _month_end(month: str) -> _date:
    y, m = int(month[:4]), int(month[5:7])
    nxt = _date(y + (m // 12), (m % 12) + 1, 1)
    return nxt - timedelta(days=1)


def _months_between(start: _date, end: _date) -> list[str]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _fid(kind: str, *parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:14]
    return f"{kind}:{h}"


# --- Duplicates + the effective ledger --------------------------------------

def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _desc_similar(a: str, b: str) -> bool:
    """Two statement descriptions name the same thing. Banks pad, truncate and
    re-case descriptions differently across exports ('Corner Market Store'
    vs 'CORNER MARKET STOR CITYNAME'), so compare the alphanumeric skeleton."""
    na, nb = _alnum(a), _alnum(b)
    if not na or not nb:
        return False
    if na[:8] == nb[:8]:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short) >= 5 and short in long_


def _accounts_compatible(a: str, b: str) -> bool:
    # Blank = unknown, so it can match anything; two DIFFERENT known accounts
    # are two different statements, not a duplicate.
    return (not a) or (not b) or a == b


def duplicate_groups(store=_default_store, txns: list[dict] | None = None) -> list[dict]:
    """Pairs of ledger rows that look like the same real transaction imported
    twice — same amount, within _DUP_WINDOW_DAYS, similar description, from
    DIFFERENT import batches, on compatible accounts. (Identical rows inside one
    batch are left alone: two same-price coffees on one statement are real.)

    Each group: {id, keep, extra, rows}. `keep` is the copy calculations use —
    the one carrying an account, else the earlier import — and `extra` the one
    they ignore until the user resolves it."""
    txns = txns if txns is not None else store.list_transactions()
    by_amt: dict[float, list[dict]] = {}
    for t in txns:
        by_amt.setdefault(round(t["amount"], 2), []).append(t)
    used: set[str] = set()
    groups = []
    for amt, rows in by_amt.items():
        if len(rows) < 2 or abs(amt) < 0.01:
            continue
        rows = sorted(rows, key=lambda r: (r["date"], r["txn_key"]))
        for i, a in enumerate(rows):
            if a["txn_key"] in used:
                continue
            for b in rows[i + 1:]:
                if b["txn_key"] in used:
                    continue
                if (a.get("source_batch") or "") == (b.get("source_batch") or ""):
                    continue
                if abs((_d(b["date"]) - _d(a["date"])).days) > _DUP_WINDOW_DAYS:
                    continue
                if not _accounts_compatible(a["account"], b["account"]):
                    continue
                if not _desc_similar(a["raw_desc"], b["raw_desc"]):
                    continue
                if bool(a["account"]) != bool(b["account"]):
                    keep, extra = (a, b) if a["account"] else (b, a)
                else:
                    keep, extra = a, b
                gid = _fid("dup", *sorted([a["txn_key"], b["txn_key"]]))
                groups.append({"id": gid, "keep": keep, "extra": extra, "rows": [keep, extra]})
                used.add(a["txn_key"]); used.add(b["txn_key"])
                break
    groups.sort(key=lambda g: g["keep"]["date"], reverse=True)
    return groups


def dup_ack_ids(group: dict) -> dict:
    """The two ways to resolve a duplicate, as acknowledgement ids:
      confirm  — 'yes, same transaction': the extra copy is excluded for good. Keyed
                 by the extra row itself so it STAYS excluded even if a later
                 account relabel means the pair is no longer detected.
      separate — 'two real transactions': both count and the flag goes away."""
    return {"confirm": "dupx:" + group["extra"]["txn_key"], "separate": "sep:" + group["id"]}


def effective_transactions(store=_default_store) -> list[dict]:
    """The ledger every calculation should use: all transactions, minus the extra
    copy of each likely duplicate — unresolved ones (counted once until the user
    decides) and confirmed ones. A pair the user marked 'separate' counts twice,
    as they said. Nothing is deleted; this is a view."""
    txns = store.list_transactions()
    acked = store.acked_flags()
    drop = {k[5:] for k in acked if k.startswith("dupx:")}
    for g in duplicate_groups(store, [t for t in txns if t["txn_key"] not in drop]):
        if dup_ack_ids(g)["separate"] not in acked:
            drop.add(g["extra"]["txn_key"])
    return [t for t in txns if t["txn_key"] not in drop]


# --- Flows + bridging --------------------------------------------------------

def _context(store) -> dict:
    """Everything the reconstructions share, computed once per call."""
    cats = {c["name"]: c for c in store.get_categories()}
    types = {a["name"]: a["type"] for a in store.get_accounts()}
    txns = effective_transactions(store)
    snaps = store.snapshot_dates()
    dates = [t["date"] for t in txns] + snaps
    start = min(dates) if dates else None
    end = max(dates) if dates else None
    return {"cats": cats, "types": types, "txns": txns, "snaps": snaps,
            "start": start, "end": end}


def _ctype(cats: dict, category: str) -> str:
    meta = cats.get(category)
    return meta["type"] if meta else "Expense"


def _snapshot_parts(store, d: str, types: dict) -> dict:
    """Net worth / debt / investments on a snapshot date (abs() for liabilities,
    same as finance_metrics.balance_figures)."""
    f = fm.balance_figures(store, d)
    return {"net_worth": f["net_worth"], "debt": f["total_debt"],
            "card_debt": f["card_balances"], "investments": f["total_investments"]}


def _bridge(days: list[_date], anchors: list[tuple[_date, float]],
            flow: dict[_date, float]) -> tuple[list[float], list[str]]:
    """Value at the END of each day: anchors are exact; days between two anchors
    walk the flows forward from the earlier one plus a linear share of the
    unexplained residual; days outside the anchors roll the flows from the
    nearest one. Returns (values, kinds) with kind in actual|bridged|projected."""
    cum, run = {}, 0.0
    for d in days:
        run += flow.get(d, 0.0)
        cum[d] = run
    if not anchors:
        return [0.0 for _ in days], ["none" for _ in days]

    def C(d: _date) -> float:
        # Cumulative flow through end of day d (anchors may sit outside `days`).
        if d in cum:
            return cum[d]
        if d < days[0]:
            return 0.0
        return cum[days[-1]]

    anchors = sorted(anchors)
    adates = {a for a, _ in anchors}
    vals, kinds = [], []
    first_d, first_v = anchors[0]
    last_d, last_v = anchors[-1]
    seg = 0
    for d in days:
        if d in adates:
            vals.append(dict(anchors)[d]); kinds.append("actual"); continue
        if d < first_d:
            vals.append(first_v - (C(first_d) - C(d))); kinds.append("projected"); continue
        if d > last_d:
            vals.append(last_v + (C(d) - C(last_d))); kinds.append("projected"); continue
        while not (anchors[seg][0] < d < anchors[seg + 1][0]):
            seg += 1
        (a, va), (b, vb) = anchors[seg], anchors[seg + 1]
        resid = vb - va - (C(b) - C(a))
        frac = (d - a).days / max(1, (b - a).days)
        vals.append(va + (C(d) - C(a)) + resid * frac); kinds.append("bridged")
    return vals, kinds


def _coverage(ctx: dict) -> dict[str, dict]:
    """Per month: which accounts have imported rows. A month with far fewer
    sources than the best-covered month is 'thin' — its estimates mostly reflect
    what was imported, not what happened."""
    per: dict[str, set] = {}
    for t in ctx["txns"]:
        per.setdefault(t["date"][:7], set()).add(t["account"] or "(no account)")
    best = max((len(v) for v in per.values()), default=0)
    out = {}
    for m, accts in per.items():
        out[m] = {"sources": sorted(accts), "count": len(accts),
                  "thin": best >= 3 and len(accts) == 1}
    return out


def series(store=_default_store) -> dict:
    """Daily net worth, liquid cash, debt and investments from the first day of
    data to the last. Each point: {date, net_worth, liquid_cash, debt,
    investments, kind, thin}."""
    ctx = _context(store)
    if not ctx["start"]:
        return {"points": [], "start": None, "end": None, "snapshots": [], "coverage": {}}
    cats, types = ctx["cats"], ctx["types"]
    d0, d1 = _d(ctx["start"]), _d(ctx["end"])
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]

    nw_flow: dict[_date, float] = {}
    debt_flow: dict[_date, float] = {}
    card_flow: dict[_date, float] = {}
    inv_flow: dict[_date, float] = {}
    for t in ctx["txns"]:
        d, amt = _d(t["date"]), float(t["amount"])
        if _ctype(cats, t["category"]) not in _NW_NEUTRAL_TYPES:
            nw_flow[d] = nw_flow.get(d, 0.0) + amt
        at = types.get(t["account"])
        if at in _LIABILITY_TYPES:
            debt_flow[d] = debt_flow.get(d, 0.0) - amt   # a purchase (-) grows debt
            if at == "credit":
                card_flow[d] = card_flow.get(d, 0.0) - amt
        elif at == "investment":
            inv_flow[d] = inv_flow.get(d, 0.0) + amt

    parts = {s: _snapshot_parts(store, s, types) for s in ctx["snaps"]}
    nw, k = _bridge(days, [(_d(s), p["net_worth"]) for s, p in parts.items()], nw_flow)
    debt, _ = _bridge(days, [(_d(s), p["debt"]) for s, p in parts.items()], debt_flow)
    inv, _ = _bridge(days, [(_d(s), p["investments"]) for s, p in parts.items()], inv_flow)
    card, _ = _bridge(days, [(_d(s), p["card_debt"]) for s, p in parts.items()], card_flow)

    cov = _coverage(ctx)
    pts = []
    for i, d in enumerate(days):
        dv = max(0.0, debt[i])
        iv = max(0.0, inv[i])
        pts.append({
            "date": _iso(d), "net_worth": _r2(nw[i]), "debt": _r2(dv),
            "investments": _r2(iv), "liquid_cash": _r2(nw[i] - iv + dv),
            "card_debt": _r2(min(dv, max(0.0, card[i]))),
            "kind": k[i], "thin": bool(cov.get(_iso(d)[:7], {}).get("thin")),
        })
    return {"points": pts, "start": ctx["start"], "end": ctx["end"],
            "snapshots": [{"date": s, **{kk: _r2(v) for kk, v in p.items()}}
                          for s, p in parts.items()],
            "coverage": cov}


def figures_on(store=_default_store, on: str | None = None, *, _series: dict | None = None) -> dict:
    """Balance figures for one date (default: the last day of data), from the
    snapshot if one exists that day, else the reconstruction. Dates past the data
    clamp to its last day; dates before it clamp to its first."""
    s = _series or series(store)
    if not s["points"]:
        return {"date": on, "basis": "none", "thin": False, "net_worth": 0.0, "liquid_cash": 0.0,
                "total_debt": 0.0, "card_balances": 0.0, "total_investments": 0.0}
    target = on or s["end"]
    target = min(max(target, s["start"]), s["end"])
    idx = (_d(target) - _d(s["start"])).days
    p = s["points"][idx]
    return {"date": p["date"], "basis": p["kind"], "thin": p["thin"],
            "net_worth": p["net_worth"], "liquid_cash": p["liquid_cash"],
            "total_debt": p["debt"], "card_balances": p["card_debt"],
            "total_investments": p["investments"]}


# --- Period report -----------------------------------------------------------

def _merchant_label(t: dict) -> str:
    m = (t.get("merchant") or "").strip()
    if m:
        return m
    raw = (t.get("raw_desc") or "").strip()
    pre = re.match(r"^(?:SQ|TST|SP|PP|PAYPAL|DD|IC|GOOGLE|APPLE PAY|BT)\s*\*\s*(.+)$", raw, re.I)
    if pre:
        raw = pre.group(1)
    raw = re.sub(r"[#*].*$", "", raw)
    raw = re.sub(r"\s+\d[\d\s-]*$", "", raw).strip()
    words = raw.split()
    label = " ".join(words[:3]).title() if words else "?"
    return re.sub(r"[\s\-:/.,]+$", "", label) or "?"


def complete_months(store=_default_store, end: str | None = None) -> list[str]:
    """Months (oldest first) that are fully covered by the data: every month
    before the newest one, plus the newest if its data reaches its last ~4 days."""
    newest = None
    txns = store.list_transactions(limit=1)
    if txns:
        newest = txns[0]["date"]
    if not newest:
        return []
    first = store.list_transactions()[-1]["date"]
    months = _months_between(_d(first), _d(newest))
    last = months[-1]
    if (_month_end(last) - _d(newest)).days > 3:
        months = months[:-1]
    if end:
        months = [m for m in months if m <= end[:7]]
    return months


def baseline(store=_default_store, through: str | None = None, *, n: int = 3) -> dict:
    """Monthly averages for cash-safety and DTI, from up to `n` recent COMPLETE
    months (through the given month). A month with no essential spend recorded is
    skipped for essentials — it means 'not imported', not 'spent nothing' — and
    likewise for income. A part-month (the newest, still in progress) is never
    averaged in: six days of October would make the emergency fund look tiny.
    Falls back to the given month alone when nothing complete is available."""
    cats = {c["name"]: c for c in store.get_categories()}
    txns = effective_transactions(store)
    by_m: dict[str, dict] = {}
    for t in txns:
        m = t["date"][:7]
        b = by_m.setdefault(m, {"ess": 0.0, "inc": 0.0, "debt": 0.0})
        meta = cats.get(t["category"])
        amt = float(t["amount"])
        if meta and meta["essential"] and meta["type"] not in _SPEND_EXCLUDED_TYPES:
            b["ess"] -= amt                      # refunds (+) net against spend
        if meta and meta["type"] == "Income":
            b["inc"] += amt
        if meta and meta["type"] == "Debt Service":
            b["debt"] -= amt
    months = complete_months(store, through)

    def avg(key: str) -> tuple[float, list[str]]:
        use = [m for m in reversed(months) if by_m.get(m, {}).get(key, 0) > 0.005][:n]
        if not use and through and by_m.get(through[:7], {}).get(key, 0) > 0:
            use = [through[:7]]
        if not use:
            return 0.0, []
        return _r2(sum(by_m[m][key] for m in use) / len(use)), sorted(use)

    ess, ess_m = avg("ess")
    inc, inc_m = avg("inc")
    debt, debt_m = avg("debt")
    return {"essentials": ess, "essentials_months": ess_m,
            "income": inc, "income_months": inc_m,
            "debt_payments": debt, "debt_months": debt_m}


def period_report(store=_default_store, start: str = "", end: str = "") -> dict:
    """Everything about money in/out over [start, end] (inclusive ISO dates):
    spending by category NET of refunds, income, cash flow, month-by-month
    breakdown, biggest transactions and merchants, and the rows themselves so the
    page can drill into a category without another round trip."""
    cats = {c["name"]: c for c in store.get_categories()}
    rows = [t for t in effective_transactions(store) if start <= t["date"] <= end]
    months = _months_between(_d(start), _d(end)) if start and end else []

    cat_tot: dict[str, dict] = {}
    month_tot = {m: {"month": m, "income": 0.0, "spending": 0.0, "essentials": 0.0,
                     "debt_service": 0.0, "by_category": {}} for m in months}
    income = debt_service = 0.0
    spends = []
    merchants: dict[str, dict] = {}
    out_rows = []
    for t in rows:
        ctype = _ctype(cats, t["category"])
        amt = float(t["amount"])
        m = t["date"][:7]
        mt = month_tot.get(m)
        out_rows.append({"date": t["date"], "merchant": _merchant_label(t),
                         "category": t["category"], "account": t["account"],
                         "amount": _r2(amt), "type": ctype, "txn_key": t["txn_key"]})
        if ctype == "Income":
            income += amt
            if mt: mt["income"] += amt
            continue
        if ctype in _SPEND_EXCLUDED_TYPES:
            continue
        c = cat_tot.setdefault(t["category"], {"category": t["category"], "gross": 0.0,
                                               "refunds": 0.0, "count": 0,
                                               "essential": bool(cats.get(t["category"], {}).get("essential")),
                                               "type": ctype})
        c["count"] += 1
        if amt < 0:
            c["gross"] += -amt
            spends.append(t)
            lab = _merchant_label(t)
            mm = merchants.setdefault(lab, {"merchant": lab, "total": 0.0, "count": 0})
            mm["total"] += -amt; mm["count"] += 1
        else:
            c["refunds"] += amt
        if mt:
            mt["spending"] -= amt
            mt["by_category"][t["category"]] = mt["by_category"].get(t["category"], 0.0) - amt
            if c["essential"]:
                mt["essentials"] -= amt
            if ctype == "Debt Service":
                mt["debt_service"] -= amt
        if ctype == "Debt Service":
            debt_service -= amt

    cat_list = []
    total_spend = 0.0
    for c in cat_tot.values():
        net = max(0.0, c["gross"] - c["refunds"])
        total_spend += net
        cat_list.append({**c, "spend": _r2(net), "gross": _r2(c["gross"]),
                         "refunds": _r2(c["refunds"])})
    cat_list = [c for c in cat_list if c["spend"] > 0 or c["refunds"] > 0]
    for c in cat_list:
        c["share"] = round(c["spend"] / total_spend, 4) if total_spend else 0.0
    cat_list.sort(key=lambda c: c["spend"], reverse=True)

    spends.sort(key=lambda t: t["amount"])
    top = [{"date": t["date"], "merchant": _merchant_label(t), "category": t["category"],
            "account": t["account"], "amount": _r2(-t["amount"]), "txn_key": t["txn_key"],
            "flag": "Review" if -t["amount"] >= fm._FLEX_REVIEW_THRESHOLD else "Watch"}
           for t in spends[:25]]
    merch = sorted(merchants.values(), key=lambda m: m["total"], reverse=True)[:12]
    for m in merch:
        m["total"] = _r2(m["total"])

    by_month = []
    for m in months:
        mt = month_tot[m]
        by_month.append({"month": m, "income": _r2(mt["income"]),
                         "spending": _r2(max(0.0, mt["spending"])),
                         "net": _r2(mt["income"] - max(0.0, mt["spending"])),
                         "essentials": _r2(max(0.0, mt["essentials"])),
                         "debt_service": _r2(max(0.0, mt["debt_service"])),
                         "by_category": {k: _r2(v) for k, v in mt["by_category"].items() if v > 0.005}})
    days = (_d(end) - _d(start)).days + 1 if start and end else 0
    n_months = max(1e-9, days / 30.4375)
    net = income - total_spend
    out_rows.sort(key=lambda r: (r["date"], r["txn_key"]), reverse=True)
    return {
        "start": start, "end": end, "days": days, "months": months,
        "income": _r2(income), "spending": _r2(total_spend), "net": _r2(net),
        "savings_rate": round(net / income, 4) if income > 0 else None,
        "avg_monthly_spending": _r2(total_spend / n_months) if days >= 28 else _r2(total_spend),
        "avg_monthly_income": _r2(income / n_months) if days >= 28 else _r2(income),
        "debt_service": _r2(debt_service),
        "transaction_count": len(rows),
        "spending_by_category": cat_list,
        "by_month": by_month,
        "top_transactions": top,
        "top_merchants": merch,
        "transactions": out_rows,
    }


# --- Data checks -------------------------------------------------------------

_GENERIC_ACCOUNT_WORDS = {"payment", "plan", "loan", "card", "chase", "freedom", "unlimited",
                         "flex", "the", "and", "promo", "credit", "checking", "savings", "bank",
                         "account", "personal", "investment"}


def _name_words(acct: str) -> list[str]:
    """Distinctive words of an account name ('ACME Payment Plan' -> ['ACME']), used
    to spot payments to it in statement descriptions."""
    return [w.upper() for w in re.findall(r"[A-Za-z]{3,}", acct)
            if w.lower() not in _GENERIC_ACCOUNT_WORDS]


def _paid_off_hint(store, acct: str, since: str, prior_balance: float,
                   txns: list[dict]) -> dict | None:
    """For a debt missing from the newest snapshot: do payments mentioning it
    since the previous snapshot add up to what was owed? Then it was likely paid
    off and the right fix is to record $0, not to type a balance."""
    words = _name_words(acct)
    if not words:
        return None
    paid = 0.0
    hits = []
    for t in txns:
        if t["date"] <= since or t["amount"] >= 0:
            continue
        desc = (t["raw_desc"] + " " + (t.get("merchant") or "")).upper()
        if any(w in desc for w in words):
            paid += -t["amount"]; hits.append(t)
    owed = abs(prior_balance)
    if not hits or owed <= 0:
        return None
    return {"paid": _r2(paid), "owed": _r2(owed), "payments": len(hits),
            "looks_paid_off": abs(paid - owed) <= max(5.0, owed * 0.02)}


def transaction_detail(store=_default_store, txn_key: str = "") -> dict | None:
    """Everything worth knowing about one transaction, for the tap-to-open window:
    the row itself, where it came from, its transfer partner, the rule that
    categorizes it, whether it's an uncounted duplicate copy, and its history at
    the same merchant (matched by the same pattern a rule would use)."""
    t = store.get_transaction(txn_key)
    if t is None:
        return None
    cats = {c["name"]: c for c in store.get_categories()}
    meta = cats.get(t["category"]) or {}
    batch = next((b for b in store.list_batches() if b["id"] == t.get("source_batch")), None)
    partner = []
    if t.get("transfer_pair_key"):
        partner = [x for x in store.list_transactions()
                   if x["transfer_pair_key"] == t["transfer_pair_key"] and x["txn_key"] != txn_key]
    eff_keys = {x["txn_key"] for x in effective_transactions(store)}
    pattern = store.rule_pattern(t["raw_desc"])
    rule = store.match_merchant_rule(t["raw_desc"])
    same = [x for x in store.list_transactions()
            if pattern and pattern in (x["raw_desc"] or "").upper() and x["txn_key"] in eff_keys]
    spent = [-x["amount"] for x in same if x["amount"] < 0]
    by_month: dict[str, float] = {}
    for x in same:
        if x["amount"] < 0:
            by_month[x["date"][:7]] = by_month.get(x["date"][:7], 0.0) - x["amount"]
    return {
        "txn": {**t, "label": _merchant_label(t)},
        "category_type": meta.get("type") or "Expense",
        "essential": bool(meta.get("essential")),
        "counted": txn_key in eff_keys,
        "source": (batch or {}).get("source") or "",
        "partner": [{"date": x["date"], "account": x["account"], "amount": x["amount"],
                     "raw_desc": x["raw_desc"], "txn_key": x["txn_key"]} for x in partner],
        "pattern": pattern,
        "rule": rule,
        "merchant": {"count": len(same), "spent": _r2(sum(spent)),
                     "average": _r2(sum(spent) / len(spent)) if spent else 0.0,
                     "first": min((x["date"] for x in same), default=None),
                     "last": max((x["date"] for x in same), default=None),
                     "by_month": [{"month": k, "spent": _r2(v)} for k, v in sorted(by_month.items())],
                     "recent": [{"date": x["date"], "amount": x["amount"], "category": x["category"],
                                 "account": x["account"], "txn_key": x["txn_key"]} for x in same[:12]]},
    }


def checks(store=_default_store, *, include_dismissed: bool = False) -> dict:
    """Every data-health finding, as review items the page can act on. Each item:
      {id, kind, severity: 'warn'|'info', title, detail, ...kind-specific fields}
    'warn' items make Data Health read 'Needs Review'; 'info' items are worth
    knowing but never block 'All Clear'. Dismissed items are omitted unless asked
    for (and then carry dismissed=True). The Review tab renders exactly this list,
    so the status and the tab can never disagree again."""
    cats = {c["name"]: c for c in store.get_categories()}
    accts = {a["name"]: a for a in store.get_accounts()}
    all_txns = store.list_transactions()
    acked = store.acked_flags()
    eff = effective_transactions(store)
    items: list[dict] = []

    # 1. Uncategorized + broken transfer pairs (the original review queue).
    nr = fm.needs_review(store)
    for t in nr["unmapped"]:
        items.append({"id": "unmapped:" + t["txn_key"], "kind": "unmapped", "severity": "warn",
                      "title": "Needs a category", "txn": t,
                      "detail": "Adam couldn't tell what this was. Pick a category — it remembers the merchant next time."})
    for t in nr["unmatched_transfers"]:
        items.append({"id": "transfer:" + t["txn_key"], "kind": "broken_transfer", "severity": "warn",
                      "title": "Transfer whose other side is missing", "txn": t,
                      "detail": "This was linked to a matching transfer that no longer nets to zero."})

    # 2. Likely duplicates across imports.
    for g in duplicate_groups(store, all_txns):
        k, x = g["keep"], g["extra"]
        ids = dup_ack_ids(g)
        acked_by = ids["confirm"] if ids["confirm"] in acked else (
            ids["separate"] if ids["separate"] in acked else None)
        items.append({"id": g["id"], "kind": "duplicate", "severity": "warn",
                      "ack_ids": ids, "acked_by": acked_by,
                      "title": "Possible duplicate",
                      "detail": ("The same %s %s on %s shows up in two different imports. Until you "
                                 "decide, Adam counts it once." % (_money(abs(k["amount"])),
                                 "deposit" if k["amount"] > 0 else "charge", k["date"])),
                      "keep": k, "extra": x})

    # 3. Accounts missing from the newest snapshot (the 'status says review but
    #    the tab is empty' case — it was counted but never listed).
    snaps = store.snapshot_dates()
    if snaps:
        latest = snaps[-1]
        present = store.balances_at(latest)
        for name, a in accts.items():
            if not a["expected_in_snapshot"] or name in present:
                continue
            prior = next((d for d in reversed(snaps[:-1]) if name in store.balances_at(d)), None)
            prior_bal = store.balances_at(prior)[name] if prior else None
            hint = (_paid_off_hint(store, name, prior, prior_bal, eff)
                    if prior and a["type"] in _LIABILITY_TYPES else None)
            if hint and hint["looks_paid_off"]:
                detail = ("Not in your %s snapshot. Payments since %s total %s against %s owed — "
                          "it looks paid off." % (latest, prior, _money(hint["paid"]), _money(hint["owed"])))
            elif prior:
                detail = ("Not in your %s snapshot (last recorded %s on %s). Record its balance, "
                          "or $0 if it's paid off or closed." % (latest, _money(prior_bal), prior))
            else:
                detail = "Not in your %s snapshot. Record its balance, or stop tracking it." % latest
            items.append({"id": _fid("missing", name, latest), "kind": "missing_snapshot",
                          "severity": "warn", "title": "%s has no current balance" % name,
                          "detail": detail, "account": name, "account_type": a["type"],
                          "institution": a.get("institution") or "",
                          "snapshot_date": latest, "prior_balance": prior_bal,
                          "prior_date": prior, "paid_off": bool(hint and hint["looks_paid_off"])})

    # 4. Imports with no account on their rows.
    batches = {b["id"]: b for b in store.list_batches()}
    blank: dict[str, list[dict]] = {}
    for t in all_txns:
        if not t["account"]:
            blank.setdefault(t.get("source_batch") or "", []).append(t)
    for bid, rows in blank.items():
        rows.sort(key=lambda r: r["date"])
        sample = []
        for r in sorted(rows, key=lambda r: abs(r["amount"]), reverse=True):
            lab = _merchant_label(r)
            if lab not in sample:
                sample.append(lab)
            if len(sample) >= 4:
                break
        src = (batches.get(bid) or {}).get("source") or ""
        items.append({"id": _fid("noacct", bid, str(len(rows))), "kind": "no_account",
                      "severity": "warn",
                      "title": "%d transactions with no account" % len(rows),
                      "detail": ("Imported%s with the Account field blank (%s → %s). Tell Adam which "
                                 "account it was so balances by account and duplicate checks work."
                                 % ((" from " + src) if src else "", rows[0]["date"], rows[-1]["date"])),
                      "batch_id": bid, "count": len(rows), "first": rows[0]["date"],
                      "last": rows[-1]["date"], "sample": sample, "source": src})

    # 5. Transactions on account names that aren't set up.
    unknown: dict[str, list[dict]] = {}
    for t in all_txns:
        if t["account"] and t["account"] not in accts:
            unknown.setdefault(t["account"], []).append(t)
    for name, rows in unknown.items():
        rows.sort(key=lambda r: r["date"])
        items.append({"id": _fid("unknownacct", name), "kind": "unknown_account", "severity": "warn",
                      "title": "“%s” isn't one of your accounts" % name,
                      "detail": ("%d transactions (%s → %s) use this name. Point them at the right "
                                 "account, or add it in Setup." % (len(rows), rows[0]["date"], rows[-1]["date"])),
                      "account": name, "count": len(rows)})

    # 6. Snapshot-to-snapshot reconciliation: does the activity explain the change?
    for a, b in zip(snaps, snaps[1:]):
        fa, fb = fm.balance_figures(store, a), fm.balance_figures(store, b)
        flow = sum(t["amount"] for t in eff if a < t["date"] <= b
                   and _ctype(cats, t["category"]) not in _NW_NEUTRAL_TYPES)
        actual = fb["net_worth"] - fa["net_worth"]
        gap = actual - flow
        scale = max(abs(fa["net_worth"]), abs(fb["net_worth"]), 1.0)
        if abs(gap) > _GAP_MIN_DOLLARS and abs(gap) > _GAP_MIN_SHARE * scale:
            items.append({"id": _fid("gap", a, b, "%.0f" % gap), "kind": "balance_gap",
                          "severity": "warn",
                          "title": "%s unexplained between snapshots" % _money(abs(gap)),
                          "detail": ("Net worth moved %s from %s to %s, but the imported transactions "
                                     "account for %s. Usually a statement that was never imported, a "
                                     "duplicate, or investment growth. Adam spreads the difference evenly "
                                     "across that stretch of the chart."
                                     % (_signed(actual), a, b, _signed(flow))),
                          "from": a, "to": b, "actual": _r2(actual), "explained": _r2(flow),
                          "gap": _r2(gap)})
        # Per-account version — catches an import labelled with the wrong account.
        ba, bb = store.balances_at(a), store.balances_at(b)
        for name in sorted(set(ba) & set(bb)):
            rows = [t for t in eff if t["account"] == name and a < t["date"] <= b]
            if len(rows) < 3:
                continue
            typ = (accts.get(name) or {}).get("type")
            sa = -abs(ba[name]) if typ in _LIABILITY_TYPES else ba[name]
            sb = -abs(bb[name]) if typ in _LIABILITY_TYPES else bb[name]
            moved, explained = sb - sa, sum(t["amount"] for t in rows)
            g = moved - explained
            if abs(g) > _ACCT_GAP_MIN_DOLLARS and abs(g) > 0.05 * max(abs(sa), abs(sb), 1.0):
                by_batch: dict[str, int] = {}
                for t in rows:
                    by_batch[t.get("source_batch") or ""] = by_batch.get(t.get("source_batch") or "", 0) + 1
                items.append({"id": _fid("acctgap", name, a, b, "%.0f" % g), "kind": "account_gap",
                              "severity": "warn",
                              "title": "%s doesn't add up" % name,
                              "detail": ("Its balance went %s → %s (%s), but the %d transactions labelled "
                                         "with it add up to %s. A statement may be missing, or an import "
                                         "was labelled with the wrong account."
                                         % (_money(sa), _money(sb), _signed(moved), len(rows), _signed(explained))),
                              "account": name, "from": a, "to": b, "gap": _r2(g),
                              "batches": [{"batch_id": k, "count": v} for k, v in
                                          sorted(by_batch.items(), key=lambda kv: -kv[1])]})

    # 7. Payments to a tracked loan filed as everyday spending. Paying down a loan
    #    moves money from cash to the debt — it isn't spending, and counting it as
    #    spending makes both the spending totals and the net-worth history wrong.
    debt_cats = [c["name"] for c in cats.values() if c["type"] == "Debt Service"]
    target = "Debt Payment" if "Debt Payment" in debt_cats else (debt_cats[0] if debt_cats else None)
    if target:
        for name, a in accts.items():
            if a["type"] != "loan":
                continue
            words = _name_words(name)
            if not words:
                continue
            rows = [t for t in eff if t["amount"] < 0
                    and _ctype(cats, t["category"]) == "Expense"
                    and any(w in (t["raw_desc"] + " " + (t.get("merchant") or "")).upper() for w in words)]
            if not rows:
                continue
            rows.sort(key=lambda r: r["date"])
            items.append({"id": _fid("loanspend", name, *[r["txn_key"] for r in rows]),
                          "kind": "loan_as_spending", "severity": "warn",
                          "title": "Loan payments counted as spending",
                          "detail": ("%d payment%s to %s (%s total) are filed under “%s”. Paying down a "
                                     "loan isn't spending — moving them to “%s” keeps spending and net "
                                     "worth accurate."
                                     % (len(rows), "" if len(rows) == 1 else "s", name,
                                        _money(sum(-r["amount"] for r in rows)),
                                        rows[0]["category"], target)),
                          "account": name, "suggest_category": target,
                          "txn_keys": [r["txn_key"] for r in rows],
                          "rows": [{"date": r["date"], "amount": r["amount"], "category": r["category"],
                                    "desc": r["raw_desc"]} for r in rows]})

    # 7b. "Spending" that is really money moving between your own accounts: an
    #     outflow filed as an expense whose exact amount lands as a Transfer deposit
    #     in another account within 3 days (checking -> your credit-union savings, filed
    #     as Insurance because that's what the money is FOR). Counting it as
    #     spending double-counts the real bill paid from the other account.
    #     Grouped by rule pattern so one tap fixes every month and teaches imports.
    # Only deposits not already matched to their own outgoing leg: a +$1,000
    # savings->checking move that pairs with its -$1,000 is accounted for, and a
    # same-day $1,000 payment to someone is just a coincidence.
    inflows = [t for t in eff if t["amount"] > 0 and _ctype(cats, t["category"]) == "Transfer"
               and not t.get("transfer_pair_key")]
    groups: dict[str, list] = {}
    for t in eff:
        if t["amount"] >= 0 or _ctype(cats, t["category"]) != "Expense":
            continue
        for d in inflows:
            if (abs(d["amount"] + t["amount"]) < 0.005
                    and abs((_d(d["date"]) - _d(t["date"])).days) <= 3
                    and (not t["account"] or not d["account"] or t["account"] != d["account"])):
                groups.setdefault(store.rule_pattern(t["raw_desc"]), []).append((t, d))
                break
    for pat, pairs in groups.items():
        pairs.sort(key=lambda p: p[0]["date"])
        cat = pairs[0][0]["category"]
        dest = pairs[0][1]["account"] or "another account"
        items.append({"id": _fid("xferspend", pat, *[p[0]["txn_key"] for p in pairs]),
                      "kind": "transfer_as_spending", "severity": "warn",
                      "title": "Transfers counted as %s" % cat,
                      "detail": ("%d “%s” payment%s (%s each) filed as %s land as a deposit in %s "
                                 "within days — that's money moving between your own accounts, not "
                                 "spending. Fixing it makes them transfers and remembers the rule for "
                                 "future imports."
                                 % (len(pairs), pat.title(), "" if len(pairs) == 1 else "s",
                                    _money(abs(pairs[0][0]["amount"])), cat, dest)),
                      "pattern": pat, "suggest_category": "Transfer",
                      "rows": [{"date": t["date"], "amount": t["amount"], "category": t["category"],
                                "desc": t["raw_desc"], "to": d["account"] or "", "to_date": d["date"]}
                               for t, d in pairs]})

    # 8. Info: liabilities typed with both signs; thin early months.
    mixed = []
    for name, a in accts.items():
        if a["type"] not in _LIABILITY_TYPES:
            continue
        signs = {(bal > 0) for d in snaps for acc, bal in store.balances_at(d).items()
                 if acc == name and abs(bal) > 0.005}
        if len(signs) == 2:
            mixed.append(name)
    if mixed:
        items.append({"id": _fid("sign", *mixed), "kind": "mixed_sign", "severity": "info",
                      "title": "Debts entered as both + and −",
                      "detail": ("%s: some snapshots have the balance as a negative number and some as "
                                 "a positive one. Adam reads any balance on a credit or loan account as "
                                 "money owed, so the math is right either way." % ", ".join(mixed)),
                      "accounts": mixed})
    ctx_cov = _coverage({"txns": eff})
    thin = sorted(m for m, c in ctx_cov.items() if c["thin"])
    if thin:
        items.append({"id": _fid("thin", *thin), "kind": "thin_months", "severity": "info",
                      "title": "Sparse data for %d month%s" % (len(thin), "" if len(thin) == 1 else "s"),
                      "detail": ("%s: only %s imported. Balances shown for those months are rough — "
                                 "import the other accounts' statements to sharpen them."
                                 % (_month_span(thin), ", ".join(ctx_cov[thin[0]]["sources"]))),
                      "months": thin})

    # 9. Imports waiting for approval.
    for b in store.list_batches(reviewed=False):
        items.append({"id": "batch:" + b["id"], "kind": "pending_import", "severity": "warn",
                      "title": "Import waiting for approval",
                      "detail": "%s has staged rows that aren't in your ledger yet." % b["id"],
                      "batch_id": b["id"]})

    out = []
    dismissed = 0
    for it in items:
        is_acked = it["id"] in acked or bool(it.get("acked_by"))
        # Uncategorized rows, broken pairs and pending imports have a real fix,
        # not a dismiss — acks never hide them.
        if is_acked and it["kind"] not in ("unmapped", "broken_transfer", "pending_import"):
            dismissed += 1
            if not include_dismissed:
                continue
            it = {**it, "dismissed": True}
        out.append(it)
    sev = {"warn": 0, "info": 1}
    out.sort(key=lambda i: (i.get("dismissed", False), sev.get(i["severity"], 2)))
    open_warn = sum(1 for i in out if i["severity"] == "warn" and not i.get("dismissed"))
    by_kind: dict[str, int] = {}
    for i in out:
        if not i.get("dismissed"):
            by_kind[i["kind"]] = by_kind.get(i["kind"], 0) + 1
    return {"items": out, "open": open_warn, "dismissed": dismissed, "by_kind": by_kind,
            "status": "All Clear" if open_warn == 0 else "Needs Review"}


def _money(x: float) -> str:
    return ("-$" if x < 0 else "$") + f"{abs(x):,.2f}"


def _signed(x: float) -> str:
    return ("+" if x >= 0 else "−") + f"${abs(x):,.2f}"


def _month_span(months: list[str]) -> str:
    def nice(m: str) -> str:
        return _date(int(m[:4]), int(m[5:7]), 1).strftime("%b %Y")
    return nice(months[0]) if len(months) == 1 else "%s – %s" % (nice(months[0]), nice(months[-1]))
