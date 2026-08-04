"""
Adam — local-first personal finance store (Phase F1).

Adam's private replacement for the owner's hand-run "Finance" Google Sheet: a
small SQLite database (`data/finance/finance.db`, stdlib sqlite3, no new deps)
that holds accounts, transactions, categories, merchant-categorization rules,
weekly balance snapshots, and import batches. The sheet is the SPEC; this module
is the storage. See docs/PLAN-FINANCE-HEALTH.md.

Design rules carried from the plan:
  - The LLM only parses/categorizes imported statements. This module stores; it
    never guesses numbers. Every displayed figure is computed deterministically
    by finance_metrics.py over these tables.
  - `txn_key` (`date|account|normalized-raw-desc|amount`) is the natural dedup
    key — the same transaction re-imported from an overlapping CSV collapses to
    one row instead of double-counting. It is the PRIMARY KEY.
  - Transfers between the user's own accounts carry a shared `transfer_pair_key`
    so the two legs cancel and never read as spending.
  - Sign convention (from the sheet's README): income/assets positive, spending/
    liabilities negative.

Same local-first shape as usage_store / job_store: one shared WAL connection
behind a re-entrant lock, path taken from config so tests can redirect it.
No secrets, no message content — just the user's own financial records, which
live only under data/ (gitignored, never shipped).
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import config

SCHEMA_VERSION = 1

# The category name reserved for anything the importer couldn't confidently map.
# finance_metrics counts rows in this category as "unmapped" for data health.
REVIEW_CATEGORY = "Uncategorized / Review"

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


# --- Connection / schema ----------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );

        -- The user's accounts. `type` drives how balances roll up:
        --   cash / investment -> assets ; credit / loan -> liabilities.
        -- expected_in_snapshot: this account SHOULD appear in every weekly
        -- balance snapshot; a missing one is a data-health warning.
        CREATE TABLE IF NOT EXISTS accounts (
            name                 TEXT PRIMARY KEY,
            institution          TEXT NOT NULL DEFAULT '',
            type                 TEXT NOT NULL DEFAULT 'cash',
            expected_in_snapshot INTEGER NOT NULL DEFAULT 1
        );

        -- The categorization taxonomy (Category | Group | Type | Essential |
        -- Recurring | Sort), ported from the sheet's Categories tab.
        --   grp:  Essentials | Lifestyle | Admin | Debt | Wealth
        --   type: Expense | Debt Service | Transfer | Investment | Adjustment
        -- `essential` drives the emergency-fund target; `sort` drives display order.
        CREATE TABLE IF NOT EXISTS categories (
            name      TEXT PRIMARY KEY,
            grp       TEXT NOT NULL DEFAULT 'Lifestyle',
            type      TEXT NOT NULL DEFAULT 'Expense',
            essential INTEGER NOT NULL DEFAULT 0,
            recurring INTEGER NOT NULL DEFAULT 0,
            sort      INTEGER NOT NULL DEFAULT 500
        );

        -- One row per transaction. txn_key is the natural dedup PRIMARY KEY.
        CREATE TABLE IF NOT EXISTS transactions (
            txn_key           TEXT PRIMARY KEY,
            date              TEXT NOT NULL,           -- YYYY-MM-DD
            account           TEXT NOT NULL DEFAULT '',
            institution       TEXT NOT NULL DEFAULT '',
            account_type      TEXT NOT NULL DEFAULT '',
            raw_desc          TEXT NOT NULL DEFAULT '',
            merchant          TEXT NOT NULL DEFAULT '',
            amount            REAL NOT NULL DEFAULT 0, -- + income, - spending
            category          TEXT NOT NULL DEFAULT '',
            transfer_pair_key TEXT NOT NULL DEFAULT '',
            notes             TEXT NOT NULL DEFAULT '',
            source_batch      TEXT NOT NULL DEFAULT '',
            reviewed          INTEGER NOT NULL DEFAULT 0,
            -- User acknowledged this Transfer-type row as a real internal transfer
            -- whose matching leg simply isn't imported, so stop flagging it as an
            -- 'unmatched transfer'. Set from the review tab's "It's a transfer" action.
            transfer_ack      INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS ix_txn_date     ON transactions(date);
        CREATE INDEX IF NOT EXISTS ix_txn_category ON transactions(category);
        CREATE INDEX IF NOT EXISTS ix_txn_batch    ON transactions(source_batch);

        -- Merchant memory: a raw-description substring -> clean name + category,
        -- so a recurring merchant categorizes itself on future imports. This is
        -- the learning loop the review flow feeds.
        CREATE TABLE IF NOT EXISTS merchant_rules (
            pattern  TEXT PRIMARY KEY,   -- stored uppercased; substring match
            merchant TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT ''
        );

        -- Weekly per-account balance snapshots. Net worth / liquid cash / debt
        -- are all computed from the LATEST snapshot date. (date, account) unique.
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            date    TEXT NOT NULL,   -- YYYY-MM-DD
            account TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (date, account)
        );

        -- One row per import (e.g. a weekly '2026-W18' batch or a named
        -- backfill), so a batch can be reviewed/undone as a unit.
        CREATE TABLE IF NOT EXISTS import_batches (
            id         TEXT PRIMARY KEY,
            created_at REAL NOT NULL DEFAULT 0,
            source     TEXT NOT NULL DEFAULT '',
            reviewed   INTEGER NOT NULL DEFAULT 0
        );

        -- Small key/value settings (extra_buffer, gross_monthly_income,
        -- ef_months, card_payoff_reserve, monthly_debt_payments). Values are
        -- JSON-encoded so numbers round-trip as numbers.
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- The review staging area. An import lands here FIRST (never straight
        -- into transactions) so nothing is counted until the user approves the
        -- batch. Approval moves the non-duplicate rows into `transactions`.
        --   status:        'pending' (will commit) | 'duplicate' (won't)
        --   review_reason: '' when clean, else why it needs a look (needs a
        --                  category / unmatched transfer / duplicate).
        CREATE TABLE IF NOT EXISTS staged_transactions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id          TEXT NOT NULL DEFAULT '',
            txn_key           TEXT NOT NULL DEFAULT '',
            date              TEXT NOT NULL DEFAULT '',
            account           TEXT NOT NULL DEFAULT '',
            institution       TEXT NOT NULL DEFAULT '',
            account_type      TEXT NOT NULL DEFAULT '',
            raw_desc          TEXT NOT NULL DEFAULT '',
            merchant          TEXT NOT NULL DEFAULT '',
            amount            REAL NOT NULL DEFAULT 0,
            category          TEXT NOT NULL DEFAULT '',
            transfer_pair_key TEXT NOT NULL DEFAULT '',
            notes             TEXT NOT NULL DEFAULT '',
            status            TEXT NOT NULL DEFAULT 'pending',
            review_reason     TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_staged_batch ON staged_transactions(batch_id);
        """
    )
    # Additive migrations for DBs created before a column existed. `CREATE TABLE
    # IF NOT EXISTS` above never alters an existing table, so add missing columns
    # here. Idempotent: guarded by table_info so it's a no-op once present.
    _add_column_if_missing(conn, "transactions", "transfer_ack",
                           "INTEGER NOT NULL DEFAULT 0")

    cur = conn.execute("SELECT version FROM schema_version")
    if cur.fetchone() is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str,
                           decl: str) -> None:
    """ADD COLUMN only if it isn't already there (SQLite can't do IF NOT EXISTS on
    columns). `decl` is the type + constraints, e.g. 'INTEGER NOT NULL DEFAULT 0'."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init(db_path: Path | str | None = None) -> dict:
    """Open (or create) the finance DB. Idempotent; safe to call again after
    config.FINANCE_DB is redirected (tests do this). Seeds the default category
    taxonomy on a fresh database."""
    global _CONN, _DB_PATH
    with _LOCK:
        target = Path(db_path) if db_path is not None else Path(config.FINANCE_DB)
        if _CONN is not None and _DB_PATH == target:
            return {"db": str(target), "reused": True, "schema_version": SCHEMA_VERSION}
        if _CONN is not None:
            _CONN.close()
        _CONN = _connect(target)
        _create_schema(_CONN)
        _DB_PATH = target
        seed_default_categories()
        return {"db": str(target), "reused": False, "schema_version": SCHEMA_VERSION}


def close() -> None:
    """Drop the connection (used by tests to simulate a restart)."""
    global _CONN, _DB_PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None
        _DB_PATH = None


def _conn() -> sqlite3.Connection:
    with _LOCK:
        if _CONN is None or _DB_PATH != Path(config.FINANCE_DB):
            init()
        assert _CONN is not None
        return _CONN


# --- Txn-key + normalization ------------------------------------------------

def normalize_desc(raw: str) -> str:
    """Canonicalize a raw bank description for the dedup key: uppercase, drop
    punctuation, collapse whitespace. So 'TRACTOR SUPPLY CO #177' and
    'Tractor Supply Co  177' produce the same key fragment."""
    up = (raw or "").upper()
    up = re.sub(r"[^A-Z0-9 ]+", " ", up)
    return re.sub(r"\s+", " ", up).strip()


def make_txn_key(date: str, account: str, raw_desc: str, amount: float) -> str:
    """The natural dedup key: date|account|normalized-desc|amount. Amount is
    formatted to 2 decimals so 37.7 and 37.70 don't split a row."""
    return f"{date}|{account}|{normalize_desc(raw_desc)}|{float(amount):.2f}"


# --- Accounts ---------------------------------------------------------------

def upsert_account(name: str, institution: str = "", type: str = "cash",
                   expected_in_snapshot: bool = True) -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO accounts (name, institution, type, expected_in_snapshot)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                institution = excluded.institution,
                type = excluded.type,
                expected_in_snapshot = excluded.expected_in_snapshot
            """,
            (name, institution, type, 1 if expected_in_snapshot else 0),
        )
        c.commit()


def get_accounts() -> list[dict]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM accounts ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_account(name: str) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def delete_account(name: str) -> bool:
    """Remove an account definition. Its historical transactions/snapshot rows are
    left intact (deleting them is a separate, deliberate action) — this just stops
    the account being expected in future snapshots."""
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM accounts WHERE name = ?", (name,))
        c.commit()
        return cur.rowcount > 0


# --- Categories -------------------------------------------------------------

def upsert_category(name: str, grp: str = "Lifestyle", type: str = "Expense",
                    essential: bool = False, recurring: bool = False,
                    sort: int = 500) -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO categories (name, grp, type, essential, recurring, sort)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                grp = excluded.grp, type = excluded.type,
                essential = excluded.essential, recurring = excluded.recurring,
                sort = excluded.sort
            """,
            (name, grp, type, 1 if essential else 0, 1 if recurring else 0, int(sort)),
        )
        c.commit()


def get_categories() -> list[dict]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM categories ORDER BY sort, name").fetchall()
    return [dict(r) for r in rows]


def get_category(name: str) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


# The default taxonomy. These are generic category names (not personal data) and
# match the groups/types the sheet's Metrics formulas assume, so a fresh install
# computes sensible numbers out of the box. (name, grp, type, essential, recurring, sort)
_DEFAULT_CATEGORIES = [
    ("Rent",                    "Essentials", "Expense",      True,  True,  100),
    ("Utilities",               "Essentials", "Expense",      True,  True,  110),
    ("Phone",                   "Essentials", "Expense",      True,  True,  120),
    ("Groceries",               "Essentials", "Expense",      True,  True,  130),
    ("Gas",                     "Essentials", "Expense",      True,  True,  140),
    ("Car Insurance",           "Essentials", "Expense",      True,  True,  150),
    ("Car / Truck",             "Essentials", "Expense",      True,  True,  160),
    ("Household",               "Essentials", "Expense",      True,  True,  170),
    ("School",                  "Essentials", "Expense",      True,  True,  180),
    ("Health",                  "Essentials", "Expense",      True,  True,  190),
    ("Pets",                    "Essentials", "Expense",      True,  True,  200),
    ("Eating Out",              "Lifestyle",  "Expense",      False, True,  300),
    ("Girlfriend / Date",       "Lifestyle",  "Expense",      False, True,  310),
    ("Entertainment",           "Lifestyle",  "Expense",      False, True,  320),
    ("Subscriptions",           "Lifestyle",  "Expense",      False, True,  330),
    ("Clothing",                "Lifestyle",  "Expense",      False, True,  340),
    ("Shopping",                "Lifestyle",  "Expense",      False, True,  350),
    ("Personal / Misc",         "Lifestyle",  "Expense",      False, True,  360),
    ("Fees / Interest",         "Admin",      "Expense",      False, True,  500),
    ("Cash / ATM",              "Admin",      "Expense",      False, True,  510),
    ("Taxes",                   "Admin",      "Expense",      True,  True,  520),
    ("Student Loan Payment",    "Debt",       "Debt Service", True,  False, 600),
    ("Mattress Promo Payment",  "Debt",       "Debt Service", True,  False, 610),
    ("Debt Payment",            "Debt",       "Debt Service", True,  False, 620),
    ("Paycheck",                "Wealth",     "Income",       False, True,  650),
    ("Reimbursement",           "Admin",      "Income",       False, False, 660),
    ("Credit Card Payment",     "Admin",      "Transfer",     False, False, 700),
    ("Transfer",                "Admin",      "Transfer",     False, False, 710),
    ("Emergency Savings Transfer", "Wealth",  "Transfer",     False, False, 720),
    ("Brokerage Contribution",  "Wealth",     "Investment",   False, False, 800),
    ("Roth IRA Contribution",   "Wealth",     "Investment",   False, False, 810),
    ("Internal Trade",          "Wealth",     "Investment",   False, False, 820),
    (REVIEW_CATEGORY,           "Admin",      "Adjustment",   False, False, 900),
]


def seed_default_categories() -> int:
    """Insert the default taxonomy for any category not already present. Returns
    the number inserted. Existing rows are never overwritten, so a user's edits
    and their own seeded categories are preserved across restarts."""
    inserted = 0
    with _LOCK:
        c = _conn()
        existing = {r["name"] for r in c.execute("SELECT name FROM categories").fetchall()}
        for name, grp, type_, essential, recurring, sort in _DEFAULT_CATEGORIES:
            if name in existing:
                continue
            c.execute(
                "INSERT INTO categories (name, grp, type, essential, recurring, sort) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, grp, type_, 1 if essential else 0, 1 if recurring else 0, sort),
            )
            inserted += 1
        c.commit()
    return inserted


# --- Merchant rules ---------------------------------------------------------

def upsert_merchant_rule(pattern: str, merchant: str, category: str) -> None:
    """Remember that any raw description containing `pattern` maps to `merchant`
    + `category`. Pattern is stored uppercased for case-insensitive matching."""
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO merchant_rules (pattern, merchant, category)
            VALUES (?, ?, ?)
            ON CONFLICT(pattern) DO UPDATE SET
                merchant = excluded.merchant, category = excluded.category
            """,
            (pattern.upper().strip(), merchant, category),
        )
        c.commit()


def get_merchant_rules() -> list[dict]:
    with _LOCK:
        rows = _conn().execute("SELECT * FROM merchant_rules ORDER BY pattern").fetchall()
    return [dict(r) for r in rows]


def match_merchant_rule(raw_desc: str) -> dict | None:
    """Return {'merchant', 'category', 'pattern'} for the first rule whose
    pattern is a substring of raw_desc, else None. Longest pattern wins so a
    specific rule beats a generic one."""
    up = (raw_desc or "").upper()
    with _LOCK:
        rows = _conn().execute("SELECT * FROM merchant_rules").fetchall()
    best = None
    for r in rows:
        if r["pattern"] and r["pattern"] in up:
            if best is None or len(r["pattern"]) > len(best["pattern"]):
                best = r
    return dict(best) if best else None


# --- Transactions -----------------------------------------------------------

def add_transaction(date: str, account: str, raw_desc: str, amount: float,
                    category: str = REVIEW_CATEGORY, merchant: str = "",
                    institution: str = "", account_type: str = "",
                    transfer_pair_key: str = "", notes: str = "",
                    source_batch: str = "", reviewed: bool = False,
                    txn_key: str | None = None) -> dict:
    """Insert one transaction, deduped on txn_key. Returns
    {'txn_key', 'inserted': bool}. A re-imported duplicate leaves the stored row
    untouched and reports inserted=False (so imports are idempotent)."""
    key = txn_key or make_txn_key(date, account, raw_desc, amount)
    with _LOCK:
        c = _conn()
        cur = c.execute(
            """
            INSERT OR IGNORE INTO transactions
                (txn_key, date, account, institution, account_type, raw_desc,
                 merchant, amount, category, transfer_pair_key, notes,
                 source_batch, reviewed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, date, account, institution, account_type, raw_desc, merchant,
             float(amount), category, transfer_pair_key, notes, source_batch,
             1 if reviewed else 0),
        )
        c.commit()
        inserted = cur.rowcount > 0
    return {"txn_key": key, "inserted": inserted}


def get_transaction(txn_key: str) -> dict | None:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM transactions WHERE txn_key = ?", (txn_key,)
        ).fetchone()
    return dict(row) if row else None


def list_transactions(month: str | None = None, account: str | None = None,
                      category: str | None = None, reviewed: bool | None = None,
                      source_batch: str | None = None,
                      limit: int | None = None) -> list[dict]:
    """Transactions matching the given filters, newest first. `month` is a
    'YYYY-MM' prefix match on the date."""
    clauses, params = [], []
    if month:
        clauses.append("date LIKE ?")
        params.append(month + "-%")
    if account is not None:
        clauses.append("account = ?")
        params.append(account)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if reviewed is not None:
        clauses.append("reviewed = ?")
        params.append(1 if reviewed else 0)
    if source_batch is not None:
        clauses.append("source_batch = ?")
        params.append(source_batch)
    sql = "SELECT * FROM transactions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, txn_key DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_transaction(txn_key: str, *, category: str | None = None,
                       merchant: str | None = None, notes: str | None = None,
                       transfer_pair_key: str | None = None,
                       reviewed: bool | None = None,
                       transfer_ack: bool | None = None) -> bool:
    """Patch the mutable fields of a transaction (used by the review flow).
    Returns True if a row was updated."""
    sets, params = [], []
    if category is not None:
        sets.append("category = ?"); params.append(category)
    if merchant is not None:
        sets.append("merchant = ?"); params.append(merchant)
    if notes is not None:
        sets.append("notes = ?"); params.append(notes)
    if transfer_pair_key is not None:
        sets.append("transfer_pair_key = ?"); params.append(transfer_pair_key)
    if reviewed is not None:
        sets.append("reviewed = ?"); params.append(1 if reviewed else 0)
    if transfer_ack is not None:
        sets.append("transfer_ack = ?"); params.append(1 if transfer_ack else 0)
    if not sets:
        return False
    params.append(txn_key)
    with _LOCK:
        c = _conn()
        cur = c.execute(
            f"UPDATE transactions SET {', '.join(sets)} WHERE txn_key = ?", params
        )
        c.commit()
        return cur.rowcount > 0


def delete_transaction(txn_key: str) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM transactions WHERE txn_key = ?", (txn_key,))
        c.commit()
        return cur.rowcount > 0


def count_transactions() -> int:
    with _LOCK:
        row = _conn().execute("SELECT COUNT(*) AS n FROM transactions").fetchone()
    return int(row["n"]) if row else 0


def delete_transactions_for_batch(source_batch: str) -> int:
    """Remove every committed transaction that came from one import batch — the
    'undo this import' action after a mistaken file. Returns rows removed."""
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM transactions WHERE source_batch = ?", (source_batch,))
        c.commit()
        return cur.rowcount


def batch_transaction_counts() -> dict[str, int]:
    """{source_batch: committed-transaction count} across the ledger, so the
    Manage view can show how much each import added."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT source_batch, COUNT(*) AS n FROM transactions "
            "WHERE source_batch != '' GROUP BY source_batch"
        ).fetchall()
    return {r["source_batch"]: int(r["n"]) for r in rows}


# --- Balance snapshots ------------------------------------------------------

def set_balance(date: str, account: str, balance: float) -> None:
    """Record (or overwrite) one account's balance on a snapshot date. Sign
    convention: assets positive, liabilities negative."""
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO balance_snapshots (date, account, balance)
            VALUES (?, ?, ?)
            ON CONFLICT(date, account) DO UPDATE SET balance = excluded.balance
            """,
            (date, account, float(balance)),
        )
        c.commit()


def snapshot_dates() -> list[str]:
    """All snapshot dates, oldest first."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT DISTINCT date FROM balance_snapshots ORDER BY date"
        ).fetchall()
    return [r["date"] for r in rows]


def latest_snapshot_date() -> str | None:
    with _LOCK:
        row = _conn().execute(
            "SELECT MAX(date) AS d FROM balance_snapshots"
        ).fetchone()
    return row["d"] if row and row["d"] else None


def balances_at(date: str) -> dict[str, float]:
    """{account: balance} for a given snapshot date."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT account, balance FROM balance_snapshots WHERE date = ?", (date,)
        ).fetchall()
    return {r["account"]: float(r["balance"]) for r in rows}


def delete_snapshot(date: str) -> int:
    """Remove an entire balance snapshot (all accounts on that date) — for fixing
    a mistaken weekly entry. Returns rows removed."""
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM balance_snapshots WHERE date = ?", (date,))
        c.commit()
        return cur.rowcount


def net_worth_history() -> list[dict]:
    """Per-snapshot-date assets/liabilities/net-worth, oldest first. Rolls up
    by the account TYPE recorded in `accounts` (unknown accounts are treated by
    the sign of their balance: positive = asset, negative = liability)."""
    accounts = {a["name"]: a["type"] for a in get_accounts()}
    out = []
    for d in snapshot_dates():
        assets = liabilities = 0.0
        for acct, bal in balances_at(d).items():
            typ = accounts.get(acct)
            is_liability = typ in ("credit", "loan") or (typ is None and bal < 0)
            if is_liability:
                liabilities += abs(bal)
            else:
                assets += bal
        out.append({
            "date": d,
            "assets": round(assets, 2),
            "liabilities": round(liabilities, 2),
            "net_worth": round(assets - liabilities, 2),
        })
    return out


# --- Import batches ---------------------------------------------------------

def create_batch(id: str, source: str = "", created_at: float | None = None,
                 reviewed: bool = False) -> None:
    ts = time.time() if created_at is None else float(created_at)
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO import_batches (id, created_at, source, reviewed)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source, reviewed = excluded.reviewed
            """,
            (id, ts, source, 1 if reviewed else 0),
        )
        c.commit()


def mark_batch_reviewed(id: str, reviewed: bool = True) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "UPDATE import_batches SET reviewed = ? WHERE id = ?",
            (1 if reviewed else 0, id),
        )
        c.commit()
        return cur.rowcount > 0


def list_batches(reviewed: bool | None = None) -> list[dict]:
    sql = "SELECT * FROM import_batches"
    params: list = []
    if reviewed is not None:
        sql += " WHERE reviewed = ?"
        params.append(1 if reviewed else 0)
    sql += " ORDER BY created_at DESC, id DESC"
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_unreviewed_batches() -> int:
    with _LOCK:
        row = _conn().execute(
            "SELECT COUNT(*) AS n FROM import_batches WHERE reviewed = 0"
        ).fetchone()
    return int(row["n"]) if row else 0


# --- Staged transactions (review area) --------------------------------------

# Columns a staged row carries beyond its auto id (kept in one place so add /
# update / the row->dict shape never drift apart).
_STAGED_FIELDS = (
    "batch_id", "txn_key", "date", "account", "institution", "account_type",
    "raw_desc", "merchant", "amount", "category", "transfer_pair_key", "notes",
    "status", "review_reason",
)


def add_staged(batch_id: str, date: str, account: str, raw_desc: str,
               amount: float, txn_key: str, *, category: str = REVIEW_CATEGORY,
               merchant: str = "", institution: str = "", account_type: str = "",
               transfer_pair_key: str = "", notes: str = "",
               status: str = "pending", review_reason: str = "") -> int:
    """Insert one row into the staging area. Returns its new id."""
    with _LOCK:
        c = _conn()
        cur = c.execute(
            """
            INSERT INTO staged_transactions
                (batch_id, txn_key, date, account, institution, account_type,
                 raw_desc, merchant, amount, category, transfer_pair_key, notes,
                 status, review_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (batch_id, txn_key, date, account, institution, account_type, raw_desc,
             merchant, float(amount), category, transfer_pair_key, notes, status,
             review_reason),
        )
        c.commit()
        return int(cur.lastrowid)


def get_staged(staged_id: int) -> dict | None:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM staged_transactions WHERE id = ?", (staged_id,)
        ).fetchone()
    return dict(row) if row else None


def list_staged(batch_id: str | None = None, status: str | None = None) -> list[dict]:
    clauses, params = [], []
    if batch_id is not None:
        clauses.append("batch_id = ?"); params.append(batch_id)
    if status is not None:
        clauses.append("status = ?"); params.append(status)
    sql = "SELECT * FROM staged_transactions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_staged(staged_id: int, **fields) -> bool:
    """Patch any staged-row column in _STAGED_FIELDS. Returns True if a row changed."""
    sets, params = [], []
    for k, v in fields.items():
        if k not in _STAGED_FIELDS:
            continue
        sets.append(f"{k} = ?")
        params.append(float(v) if k == "amount" else v)
    if not sets:
        return False
    params.append(staged_id)
    with _LOCK:
        c = _conn()
        cur = c.execute(
            f"UPDATE staged_transactions SET {', '.join(sets)} WHERE id = ?", params
        )
        c.commit()
        return cur.rowcount > 0


def delete_staged(staged_id: int) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM staged_transactions WHERE id = ?", (staged_id,))
        c.commit()
        return cur.rowcount > 0


def delete_staged_for_batch(batch_id: str) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM staged_transactions WHERE batch_id = ?", (batch_id,))
        c.commit()
        return cur.rowcount


def count_staged(batch_id: str | None = None, status: str | None = None) -> int:
    clauses, params = [], []
    if batch_id is not None:
        clauses.append("batch_id = ?"); params.append(batch_id)
    if status is not None:
        clauses.append("status = ?"); params.append(status)
    sql = "SELECT COUNT(*) AS n FROM staged_transactions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    with _LOCK:
        row = _conn().execute(sql, params).fetchone()
    return int(row["n"]) if row else 0


# --- Settings ---------------------------------------------------------------

# Sensible defaults for the settings finance_metrics reads. None means "not set
# -> compute from transactions" (income, debt payments) or "treat as absent"
# (card reserve). ef_months and extra_buffer have real numeric defaults.
_SETTING_DEFAULTS = {
    "ef_months": 3,
    "extra_buffer": 0.0,
    "gross_monthly_income": None,   # None -> compute from Income-type categories
    "monthly_debt_payments": None,  # None -> compute from Debt Service categories
    "card_payoff_reserve": None,    # None -> sum of credit-account balances
}


def set_setting(key: str, value) -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        c.commit()


def get_setting(key: str, default=None):
    with _LOCK:
        row = _conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default if key not in _SETTING_DEFAULTS else _SETTING_DEFAULTS[key]
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return row["value"]


def get_settings() -> dict:
    """All effective settings (stored values overlaid on the defaults)."""
    out = dict(_SETTING_DEFAULTS)
    with _LOCK:
        rows = _conn().execute("SELECT key, value FROM settings").fetchall()
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            out[r["key"]] = r["value"]
    return out
