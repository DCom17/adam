"""
Adam — server-side chat-session store (cross-device sync).

Chats ("sessions") and their transcripts used to live only in each device's
browser localStorage, so a chat started on the PC never appeared on the phone.
This store gives the (single) user one authoritative copy on their OWN server, so
every signed-in device reads + writes the same list.

Model: dead-simple last-write-wins. Each session record carries an `updated`
millisecond timestamp; on conflict the higher `updated` wins. Deletes are
tombstones (`deleted=1`) so a delete on one device propagates instead of the row
silently coming back from another device. Single user (one bearer token), so
there is no per-user partitioning here — the token gate in server.py is the fence.

Delivery cursor: every applied write gets a server-assigned, monotonic `seq`, and
clients pull with `changed_since(seq)`. This is DELIBERATELY separate from `updated`.
`updated` is a *client* wall-clock (`Date.now()`) and clocks disagree across devices;
if the pull cursor keyed off `updated`, a delete stamped by a lagging-clock device
could land *below* another device's cursor and get filtered out forever — the chat
that "won't delete on the other device." `seq` comes from the server's single clock,
so "have I seen this change yet?" is skew-proof. `updated` still decides which
*version* wins a conflict (LWW); `seq` only decides *delivery*.

Storage is a standalone SQLite file (stdlib sqlite3, WAL) under the state dir,
separate from the job DB so the two stores can't step on each other.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import config

# Defensive server-side cap on a transcript blob (the client already caps ~120k).
_TX_MAX = 200_000

# Client-supplied columns (the wire record). `seq` is server-assigned and handled
# separately, so it is NOT in this list.
_COLS = "key,title,mode,sid,last_ts,last_spoken,tx,deleted,created,used,updated"
# Read projection: the wire columns plus the server-assigned delivery cursor.
_READ_COLS = _COLS + ",seq"

_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None
_LOCK = threading.Lock()
# Monotonic server-side delivery counter, seeded from MAX(seq) at init and bumped on
# every applied write. Guarded by _LOCK. This is the cursor clients page on.
_SEQ = 0


def _db_path() -> Path:
    return Path(getattr(config, "SESSIONS_DB", None) or (config.STATE_DIR / "sessions.db"))


def init(db_path: str | Path | None = None) -> dict:
    """Open the DB (idempotent) and ensure the schema. Safe to call repeatedly."""
    global _CONN, _DB_PATH, _SEQ
    with _LOCK:
        path = Path(db_path) if db_path is not None else _db_path()
        if _CONN is not None and _DB_PATH == path:
            return {"db": str(path)}
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                key         TEXT PRIMARY KEY,
                title       TEXT,
                mode        TEXT,
                sid         TEXT,
                last_ts     INTEGER DEFAULT 0,
                last_spoken TEXT,
                tx          TEXT,
                deleted     INTEGER DEFAULT 0,
                created     INTEGER DEFAULT 0,
                used        INTEGER DEFAULT 0,
                updated     INTEGER DEFAULT 0,
                seq         INTEGER DEFAULT 0
            )
            """
        )
        # Migrate a pre-seq DB: add the column and backfill a monotonic seq in
        # `updated` order so existing chats keep a stable, sensible delivery order.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "seq" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN seq INTEGER DEFAULT 0")
            keys = [r[0] for r in conn.execute(
                "SELECT key FROM sessions ORDER BY updated ASC, key ASC").fetchall()]
            for i, k in enumerate(keys, start=1):
                conn.execute("UPDATE sessions SET seq=? WHERE key=?", (i, k))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_seq ON sessions(seq)")
        # Ground truth: which mode (hence which cwd/workspace) each Claude session was
        # last run under. A Claude session only exists inside the cwd it was created in
        # — code chats run in the vault, safe (voice/work) chats in the throwaway
        # workspace. Resuming a session under a mode whose cwd differs from its origin
        # makes the CLI error out or silently start fresh, wiping context. This map lets
        # the server honor a session's TRUE origin mode when a stale client (e.g. a phone
        # that hasn't synced a mode change) sends the wrong one. Server-owned, NOT synced.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_modes (
                sid     TEXT PRIMARY KEY,
                mode    TEXT,
                updated INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        _SEQ = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM sessions").fetchone()[0] or 0
        _CONN = conn
        _DB_PATH = path
        return {"db": str(path)}


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        init()
    assert _CONN is not None
    return _CONN


def _row_to_dict(r) -> dict:
    return {
        "key": r[0],
        "title": r[1] or "",
        "mode": r[2] or "voice",
        "sid": r[3],
        "last_ts": r[4] or 0,
        "last_spoken": r[5] or "",
        "tx": r[6] or "",
        "deleted": bool(r[7]),
        "created": r[8] or 0,
        "used": r[9] or 0,
        "updated": r[10] or 0,
        "seq": r[11] or 0,
    }


def changed_since(since: int) -> list[dict]:
    """Every session whose server `seq` is strictly greater than `since`, in seq order
    (so a client advances its cursor to the last one it applied). `since` is a `seq`
    cursor, NOT a timestamp — this is what makes delivery immune to cross-device clock
    skew (see the module docstring)."""
    with _LOCK:
        cur = _conn().execute(
            f"SELECT {_READ_COLS} FROM sessions WHERE seq > ? ORDER BY seq ASC",
            (int(since or 0),),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def upsert(records: list[dict]) -> dict:
    """Merge client records by last-write-wins on `updated`. A record whose
    `updated` is not strictly newer than the stored copy is ignored, so a slow
    device can never clobber a fresher edit. Returns {applied, max_updated}."""
    global _SEQ
    applied = 0
    max_updated = 0
    with _LOCK:
        conn = _conn()
        for rec in records or []:
            key = str(rec.get("key") or "").strip()
            if not key:
                continue
            updated = int(rec.get("updated") or 0)
            if updated > max_updated:
                max_updated = updated
            row = conn.execute("SELECT updated FROM sessions WHERE key=?", (key,)).fetchone()
            if row is not None and (row[0] or 0) >= updated:
                continue  # stored copy is newer or equal — keep it
            tx = rec.get("tx") or ""
            if len(tx) > _TX_MAX:
                tx = tx[-_TX_MAX:]
            _SEQ += 1  # server-assigned monotonic delivery cursor for this write
            conn.execute(
                f"INSERT INTO sessions ({_COLS},seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "title=excluded.title, mode=excluded.mode, sid=excluded.sid, "
                "last_ts=excluded.last_ts, last_spoken=excluded.last_spoken, tx=excluded.tx, "
                "deleted=excluded.deleted, created=excluded.created, used=excluded.used, "
                "updated=excluded.updated, seq=excluded.seq",
                (
                    key,
                    str(rec.get("title") or ""),
                    str(rec.get("mode") or "voice"),
                    (str(rec["sid"]) if rec.get("sid") else None),
                    int(rec.get("last_ts") or 0),
                    str(rec.get("last_spoken") or ""),
                    tx,
                    1 if rec.get("deleted") else 0,
                    int(rec.get("created") or 0),
                    int(rec.get("used") or 0),
                    updated,
                    _SEQ,
                ),
            )
            applied += 1
        conn.commit()
    return {"applied": applied, "max_updated": max_updated}


def all_sessions() -> list[dict]:
    with _LOCK:
        cur = _conn().execute(f"SELECT {_READ_COLS} FROM sessions ORDER BY updated ASC")
        return [_row_to_dict(r) for r in cur.fetchall()]


def purge_expired(ttl_ms: int, now: int | None = None) -> int:
    """Hard-delete tombstoned sessions whose `updated` (the delete time) is older
    than `ttl_ms`. This is the retention expiry: a deleted chat stays recoverable
    from the Archived drawer until its window lapses, then the row (transcript and
    all) is swept. Only tombstones are ever purged — a live chat is untouchable.

    Note: purging a tombstone reopens the classic resurrection window — a device
    that's been offline longer than the retention period still holds the chat as
    *alive* and will re-push it on reconnect. For a single-user (phone + PC) setup
    the window is wide enough that this is a non-issue; it's the deliberate cost of
    not keeping tombstones forever. Returns the number of rows purged."""
    if ttl_ms <= 0:
        return 0
    now = now_ms() if now is None else int(now)
    cutoff = now - int(ttl_ms)
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM sessions WHERE deleted=1 AND updated < ?", (cutoff,)
        )
        conn.commit()
        return cur.rowcount or 0


def record_session_mode(sid: str, mode: str) -> None:
    """Remember the mode (hence cwd) a Claude session was last run under. Called after
    every completed turn with the session_id the CLI returned. Best-effort — a failure
    here must never break a finished turn."""
    sid = str(sid or "").strip()
    if not sid:
        return
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO session_modes (sid, mode, updated) VALUES (?,?,?) "
            "ON CONFLICT(sid) DO UPDATE SET mode=excluded.mode, updated=excluded.updated",
            (sid, str(mode or "voice"), now_ms()),
        )
        conn.commit()


def get_session_mode(sid: str) -> str | None:
    """The mode a Claude session was last run under, or None if we've never seen it
    (a brand-new session, or one predating this map). None means 'no ground truth —
    trust the client's requested mode.'"""
    sid = str(sid or "").strip()
    if not sid:
        return None
    with _LOCK:
        row = _conn().execute(
            "SELECT mode FROM session_modes WHERE sid=?", (sid,)
        ).fetchone()
        return (row[0] if row else None) or None


def now_ms() -> int:
    return int(time.time() * 1000)
