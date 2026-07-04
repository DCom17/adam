"""
Jarvis Voice Local — persistent job store (Phase 5: Persistent Runtime State).

Before Phase 5 the async-job table lived in a plain in-process dict, so a server
restart / crash / machine sleep vanished every in-flight and finished job: the
phone's `/poll` got a 404 and the turn was lost. This module replaces that dict
with a small SQLite database (`data/state/jarvis.db`, stdlib `sqlite3`, no new
deps) so jobs — and their history — survive the process.

Design:
  * One table, `jobs`, plus a `schema_version` table for forward migrations.
  * Canonical statuses: queued · running · complete · failed · interrupted ·
    cancelled. The wire (`/poll`) still speaks the original running/done/error
    vocabulary via `to_wire()`, so the PWA is untouched.
  * Startup recovery (`recover_interrupted`) marks any job left queued/running by
    a previous process as `interrupted` — never silently complete — preserving
    its last-known info and making it visible through `/poll` and `/jobs`.

Local-first and single-user: a shared connection guarded by a lock, in WAL mode,
is plenty. No secrets are stored here — no tokens, no push keys. The user's input
is kept only as a short truncated summary; the full result is stored because the
product already persists it (last_result.json) and job history needs it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import config

# Bump when the schema changes; add a branch in `_migrate`.
SCHEMA_VERSION = 2

# Canonical lifecycle statuses.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES = (
    STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETE,
    STATUS_FAILED, STATUS_INTERRUPTED, STATUS_CANCELLED,
)
# Statuses that mean "still owned by a live process" — recovery targets these.
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
# Terminal statuses — safe to sweep once old enough.
TERMINAL_STATUSES = (STATUS_COMPLETE, STATUS_FAILED, STATUS_INTERRUPTED, STATUS_CANCELLED)

# Map canonical status -> the wire vocabulary the existing PWA poll loop expects:
#   "running" keeps polling, "done" delivers, "error" resets the session.
_WIRE_STATUS = {
    STATUS_QUEUED: "running",
    STATUS_RUNNING: "running",
    STATUS_COMPLETE: "done",
    STATUS_FAILED: "error",
    STATUS_INTERRUPTED: "error",
    STATUS_CANCELLED: "error",
}

# Columns, in declaration order — also the order we read rows back.
_COLUMNS = (
    "job_id", "created_at", "created_at_ts", "updated_at", "updated_at_ts",
    "started_at", "completed_at", "interrupted_at",
    "status", "mode", "session_id", "input_summary",
    "result", "spoken", "error", "message", "ts",
    "pid", "proposed_changes", "chat_control", "notification_status", "delivered",
)

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts if ts is not None else _now()))


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL: durable across a crash and tolerant of a reader during a write.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            job_id              TEXT PRIMARY KEY,
            created_at          TEXT,
            created_at_ts       REAL,
            updated_at          TEXT,
            updated_at_ts       REAL,
            started_at          TEXT,
            completed_at        TEXT,
            interrupted_at      TEXT,
            status              TEXT,
            mode                TEXT,
            session_id          TEXT,
            input_summary       TEXT,
            result              TEXT,
            spoken              TEXT,
            error               TEXT,
            message             TEXT,
            ts                  INTEGER,
            pid                 INTEGER,
            proposed_changes    TEXT,
            chat_control        TEXT,
            notification_status TEXT,
            delivered           INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at_ts);
        CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
        """
    )


def _get_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def _migrate(conn: sqlite3.Connection, from_version: int) -> int:
    """Minimal forward migration. v0 (fresh) -> SCHEMA_VERSION. Future schema
    changes add an `if version < N:` block here. Kept deliberately small."""
    version = from_version
    if version < 1:
        # v1 is the initial schema; tables are already created by _create_schema.
        version = 1
    if version < 2:
        # v2 adds jobs.chat_control (JSON chat-management directive relayed to the
        # client so JARVIS can rename the current chat / open a new one hands-free).
        # CREATE TABLE IF NOT EXISTS won't add it to an existing DB, so alter in place.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "chat_control" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN chat_control TEXT")
        version = 2
    return version


def init(db_path: str | Path | None = None) -> dict:
    """Open (or create) the job database and ensure the schema/version exist.

    Idempotent: safe to call once at startup. Returns a small summary dict for
    startup logging. `db_path` overrides config (used by tests)."""
    global _CONN, _DB_PATH
    with _LOCK:
        path = Path(db_path) if db_path is not None else Path(config.JOBS_DB)
        if _CONN is not None and _DB_PATH == path:
            return {"db": str(path), "schema_version": SCHEMA_VERSION, "reused": True}
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
        _CONN = _connect(path)
        _DB_PATH = path
        _create_schema(_CONN)
        current = _get_version(_CONN)
        migrated_to = _migrate(_CONN, current)
        if migrated_to != current:
            _set_version(_CONN, migrated_to)
        _CONN.commit()
        return {
            "db": str(path),
            "schema_version": migrated_to,
            "from_version": current,
            "migrated": migrated_to != current,
            "reused": False,
        }


def close() -> None:
    """Close the connection (tests use this to simulate a process restart)."""
    global _CONN, _DB_PATH
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
        _CONN = None
        _DB_PATH = None


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        init()
    assert _CONN is not None
    return _CONN


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = {k: row[k] for k in row.keys()}
    # proposed_changes is stored as a JSON string; hand it back as a list.
    pc = d.get("proposed_changes")
    if isinstance(pc, str) and pc:
        try:
            d["proposed_changes"] = json.loads(pc)
        except Exception:
            d["proposed_changes"] = []
    else:
        d["proposed_changes"] = []
    d["proposed_change_ids"] = [
        p.get("id") for p in d["proposed_changes"] if isinstance(p, dict) and p.get("id")
    ]
    # chat_control is stored as a JSON string (or NULL); hand it back as a dict/None.
    cc = d.get("chat_control")
    if isinstance(cc, str) and cc:
        try:
            d["chat_control"] = json.loads(cc)
        except Exception:
            d["chat_control"] = None
    else:
        d["chat_control"] = None
    d["delivered"] = bool(d.get("delivered"))
    return d


# --- Writes -----------------------------------------------------------------

def create_job(
    job_id: str,
    *,
    mode: str = "voice",
    session_id: str | None = None,
    input_summary: str | None = None,
    pid: int | None = None,
    status: str = STATUS_RUNNING,
) -> dict:
    """Insert a new job. Defaults to `running` because `/ask_async` starts the
    background task immediately (no real queue yet); `queued` is available for a
    future real queue. The user's input is stored only as a short summary."""
    now = _now()
    iso = _iso(now)
    started = iso if status == STATUS_RUNNING else None
    with _LOCK:
        c = _conn()
        c.execute(
            """INSERT INTO jobs
               (job_id, created_at, created_at_ts, updated_at, updated_at_ts,
                started_at, status, mode, session_id, input_summary, pid,
                proposed_changes, delivered)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (job_id, iso, now, iso, now, started, status, mode, session_id,
             input_summary, pid, json.dumps([])),
        )
        c.commit()
    return get_job(job_id)  # type: ignore[return-value]


def _update(job_id: str, **fields) -> dict | None:
    fields.setdefault("updated_at", _iso())
    fields.setdefault("updated_at_ts", _now())
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [job_id]
    with _LOCK:
        c = _conn()
        cur = c.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", vals)
        c.commit()
        if cur.rowcount == 0:
            return None
    return get_job(job_id)


def complete_job(
    job_id: str,
    *,
    result: str,
    spoken: str | None,
    mode: str | None,
    session_id: str | None,
    ts: int | None,
    proposed_changes: list[dict] | None = None,
    chat_control: dict | None = None,
    notification_status: str | None = None,
) -> dict | None:
    """Mark a job complete and store its outcome (status -> 'complete')."""
    return _update(
        job_id,
        status=STATUS_COMPLETE,
        completed_at=_iso(),
        result=result,
        spoken=spoken,
        mode=mode,
        session_id=session_id,
        ts=ts,
        proposed_changes=json.dumps(proposed_changes or []),
        chat_control=json.dumps(chat_control) if chat_control else None,
        notification_status=notification_status,
        error=None,
    )


def fail_job(job_id: str, error: str) -> dict | None:
    """Mark a job failed and store the error (status -> 'failed')."""
    return _update(
        job_id, status=STATUS_FAILED, completed_at=_iso(), error=str(error)[:2000],
    )


def cancel_job(job_id: str, message: str = "Stopped by user.") -> dict | None:
    """Mark a job cancelled — the user hit stop mid-turn. Wire-maps to 'error'
    with the message, so the phone shows a clean 'Stopped' instead of a crash."""
    return _update(
        job_id, status=STATUS_CANCELLED, completed_at=_iso(), error=str(message)[:200],
    )


def set_notification_status(job_id: str, notification_status: str) -> dict | None:
    return _update(job_id, notification_status=notification_status)


def mark_delivered(job_id: str) -> dict | None:
    """Record that `/poll` has handed a terminal result back to the client. The
    job is kept (for history); this is bookkeeping, not deletion."""
    return _update(job_id, delivered=1)


# --- Reads ------------------------------------------------------------------

def get_job(job_id: str) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return _row_to_dict(row)


def list_jobs(*, limit: int = 50, status: str | None = None) -> list[dict]:
    """Recent jobs, newest first. Optional exact-status filter."""
    limit = max(1, min(int(limit), 500))
    with _LOCK:
        c = _conn()
        if status:
            rows = c.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at_ts DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at_ts DESC LIMIT ?", (limit,),
            ).fetchall()
    return [d for d in (_row_to_dict(r) for r in rows) if d is not None]


# --- Startup recovery + maintenance -----------------------------------------

def recover_interrupted() -> list[dict]:
    """Mark every job a previous process left queued/running as `interrupted`.

    Called once at startup, BEFORE the app serves traffic. Such a job's worker
    died with the old process, so it can never finish — we never silently mark it
    complete. We preserve its last-known info, stamp `interrupted_at`, and set a
    message so `/poll` and `/jobs` show it clearly. Returns the recovered records
    so the caller can audit/log them."""
    now_iso = _iso()
    msg = "Interrupted — the server process restarted while this job was running."
    with _LOCK:
        c = _conn()
        rows = c.execute(
            "SELECT * FROM jobs WHERE status IN (?,?)", ACTIVE_STATUSES,
        ).fetchall()
        recovered = [d for d in (_row_to_dict(r) for r in rows) if d is not None]
        if recovered:
            c.execute(
                """UPDATE jobs
                   SET status=?, interrupted_at=?, message=?, error=COALESCE(error,?),
                       updated_at=?, updated_at_ts=?
                   WHERE status IN (?,?)""",
                (STATUS_INTERRUPTED, now_iso, msg, msg, now_iso, _now(),
                 *ACTIVE_STATUSES),
            )
            c.commit()
    # Re-read so the returned records reflect the new status.
    return [get_job(r["job_id"]) for r in recovered if get_job(r["job_id"])]  # type: ignore


def sweep(ttl_seconds: int) -> int:
    """Delete terminal jobs whose last activity is older than the TTL, so history
    can't grow unbounded. Active (queued/running) jobs are never swept. Returns
    the number deleted."""
    cutoff = _now() - max(0, int(ttl_seconds))
    qmarks = ",".join("?" for _ in TERMINAL_STATUSES)
    with _LOCK:
        c = _conn()
        cur = c.execute(
            f"""DELETE FROM jobs
                WHERE status IN ({qmarks})
                  AND COALESCE(updated_at_ts, created_at_ts, 0) < ?""",
            (*TERMINAL_STATUSES, cutoff),
        )
        c.commit()
        return cur.rowcount


# --- Wire mapping -----------------------------------------------------------

def to_wire(job: dict) -> dict:
    """Map a stored job to the exact `/poll` response shape the existing PWA
    expects: status in {running, done, error}, plus result/spoken/mode/
    session_id/error/ts/proposed_changes. An interrupted/cancelled job maps to
    `error` and surfaces its message so the phone shows a real reason, not a 404."""
    status = job.get("status")
    wire = _WIRE_STATUS.get(status, status)
    err = job.get("error")
    if wire == "error" and not err:
        err = job.get("message") or "Job ended without a result."
    return {
        "status": wire,
        "result": job.get("result"),
        "spoken": job.get("spoken"),
        "mode": job.get("mode"),
        "session_id": job.get("session_id"),
        "error": err,
        "ts": job.get("ts"),
        "proposed_changes": job.get("proposed_changes") or [],
        "chat_control": job.get("chat_control"),
    }
