"""
Adam — per-day AI spend store.

Every claude.exe turn's JSON result carries `total_cost_usd` (what the turn
would cost at API rates). This module accumulates those numbers per calendar
day in a small SQLite database (`data/state/usage.db`, stdlib sqlite3, no new
deps) so the app can show an honest cost meter and enforce the monthly budget
in api_key mode — the two features that make pay-as-you-go feel bounded
instead of like a running taxi meter.

On a subscription install the numbers are still recorded (the CLI reports them
either way); there they read as "what your plan saved you", and nothing gates.

Same local-first shape as job_store: one shared WAL connection behind a lock.
No secrets, no message content — just day, dollars, turn count.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import config

SCHEMA_VERSION = 1

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _this_month() -> str:
    return time.strftime("%Y-%m", time.localtime())


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_daily (
            day      TEXT PRIMARY KEY,   -- YYYY-MM-DD (local time)
            cost_usd REAL NOT NULL DEFAULT 0,
            turns    INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    cur = conn.execute("SELECT version FROM schema_version")
    if cur.fetchone() is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


def init() -> dict:
    """Open (or create) the usage DB at config.USAGE_DB. Idempotent; safe to call
    again after config.USAGE_DB is redirected (tests do this)."""
    global _CONN, _DB_PATH
    with _LOCK:
        target = Path(config.USAGE_DB)
        if _CONN is not None and _DB_PATH == target:
            return {"db": str(target), "reused": True}
        if _CONN is not None:
            _CONN.close()
        _CONN = _connect(target)
        _create_schema(_CONN)
        _DB_PATH = target
        return {"db": str(target), "reused": False}


def _conn() -> sqlite3.Connection:
    with _LOCK:
        if _CONN is None or _DB_PATH != Path(config.USAGE_DB):
            init()
        assert _CONN is not None
        return _CONN


def record_turn(cost_usd: float, day: str | None = None) -> None:
    """Add one turn's cost to the day's row (creating it if needed)."""
    if not isinstance(cost_usd, (int, float)) or cost_usd < 0:
        return
    d = day or _today()
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO usage_daily (day, cost_usd, turns) VALUES (?, ?, 1)
            ON CONFLICT(day) DO UPDATE SET
                cost_usd = cost_usd + excluded.cost_usd,
                turns    = turns + 1
            """,
            (d, float(cost_usd)),
        )
        c.commit()


def day_total(day: str | None = None) -> float:
    d = day or _today()
    with _LOCK:
        row = _conn().execute(
            "SELECT cost_usd FROM usage_daily WHERE day = ?", (d,)
        ).fetchone()
    return float(row["cost_usd"]) if row else 0.0


def month_total(month: str | None = None) -> float:
    """Total spend for a 'YYYY-MM' month (default: the current local month)."""
    m = month or _this_month()
    with _LOCK:
        row = _conn().execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage_daily WHERE day LIKE ?",
            (m + "-%",),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def summary() -> dict:
    """The numbers the cost meter shows: today, this month, and turn counts."""
    m = _this_month()
    with _LOCK:
        c = _conn()
        month_row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(turns), 0) AS turns "
            "FROM usage_daily WHERE day LIKE ?",
            (m + "-%",),
        ).fetchone()
        today_row = c.execute(
            "SELECT cost_usd, turns FROM usage_daily WHERE day = ?", (_today(),)
        ).fetchone()
    return {
        "month": m,
        "month_usd": round(float(month_row["cost"]), 4) if month_row else 0.0,
        "month_turns": int(month_row["turns"]) if month_row else 0,
        "today_usd": round(float(today_row["cost_usd"]), 4) if today_row else 0.0,
        "today_turns": int(today_row["turns"]) if today_row else 0,
    }
