"""
Adam — local-first checklist store.

The storage behind the Checklists view: named lists of ordered, checkable steps
that either the user writes by hand or Adam builds during a conversation ("turn
that into a checklist"). Everything lives on-device in a small SQLite database
(`data/checklists/checklists.db`, stdlib sqlite3, no new deps).

Design rules:
  - **Archive over delete.** Deleting a checklist sets `archived_at` and hides it
    from the active list; the Archive tab restores it. A row only truly leaves
    the database through `purge()`, which the UI reaches solely from the archive
    (and Adam can never propose — see external_actions). This is what makes it
    safe to let the assistant delete a list it created.
  - **Provenance is recorded.** `source` is 'user' or 'adam', so the UI can badge
    what the assistant made and the owner can tell at a glance.
  - **Positions are dense integers** maintained on write, so drag-reordering and
    "insert at top" are cheap and deterministic rather than float-midpoint games.

Same local-first shape as health_store / finance_store: one shared WAL connection
behind a re-entrant lock, path taken from config so tests can redirect it. No
secrets and no message content — just the user's own lists, which live only under
data/ (gitignored, never shipped).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import config

SCHEMA_VERSION = 1

# Provenance values. Anything else is coerced to 'user' on write.
SOURCE_USER = "user"
SOURCE_ADAM = "adam"
_SOURCES = {SOURCE_USER, SOURCE_ADAM}

# Bounds — a checklist is a checklist, not a document store. Enforced here as
# well as in the request models so the assistant's ACTION path is bounded too.
MAX_TITLE = 200
MAX_TEXT = 2000
MAX_NOTE = 4000
MAX_ITEMS_PER_LIST = 500

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


def _now() -> str:
    """UTC ISO-8601 seconds, the same stamp format the rest of the app writes."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

        -- One row per checklist. `archived_at` NULL means active; a timestamp
        -- means it sits in the Archive tab, restorable, until purged.
        CREATE TABLE IF NOT EXISTS checklists (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT 'user',   -- user | adam
            position    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT '',
            updated_at  TEXT NOT NULL DEFAULT '',
            archived_at TEXT                            -- NULL = active
        );
        CREATE INDEX IF NOT EXISTS ix_checklist_archived ON checklists(archived_at, position);

        -- Ordered steps. ON DELETE CASCADE so purging a list takes its items;
        -- archiving does not touch them, which is why restore is lossless.
        CREATE TABLE IF NOT EXISTS items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            checklist_id INTEGER NOT NULL REFERENCES checklists(id) ON DELETE CASCADE,
            text         TEXT NOT NULL DEFAULT '',
            note         TEXT NOT NULL DEFAULT '',
            done         INTEGER NOT NULL DEFAULT 0,
            done_at      TEXT,
            position     INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT '',
            updated_at   TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_item_list ON items(checklist_id, position);
        """
    )
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif int(row["version"]) < SCHEMA_VERSION:
        # Additive-only so far: the CREATE ... IF NOT EXISTS script above already
        # brought an older DB current; just stamp the version.
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


def init(db_path: Path | str | None = None) -> dict:
    """Open (or create) the checklist DB. Idempotent; safe after
    config.CHECKLIST_DB is redirected (tests do this)."""
    global _CONN, _DB_PATH
    with _LOCK:
        target = Path(db_path) if db_path is not None else Path(config.CHECKLIST_DB)
        if _CONN is not None and _DB_PATH == target:
            return {"db": str(target), "reused": True, "schema_version": SCHEMA_VERSION}
        if _CONN is not None:
            _CONN.close()
        _CONN = _connect(target)
        _create_schema(_CONN)
        _DB_PATH = target
        return {"db": str(target), "reused": False, "schema_version": SCHEMA_VERSION}


def close() -> None:
    global _CONN, _DB_PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None
        _DB_PATH = None


def _conn() -> sqlite3.Connection:
    with _LOCK:
        if _CONN is None or _DB_PATH != Path(config.CHECKLIST_DB):
            init()
        assert _CONN is not None
        return _CONN


def _clip(s: str | None, n: int) -> str:
    return (s or "").strip()[:n]


# --- Checklists -------------------------------------------------------------

def create_checklist(
    title: str,
    description: str = "",
    source: str = SOURCE_USER,
    items: list | None = None,
) -> int:
    """Create a checklist, optionally with its items in one call (how the
    assistant creates one). Returns the new checklist id. New lists go to the top
    of the active list, which is what you want when Adam just made one."""
    title = _clip(title, MAX_TITLE) or "Untitled checklist"
    src = source if source in _SOURCES else SOURCE_USER
    now = _now()
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT COALESCE(MIN(position), 0) - 1 AS p FROM checklists WHERE archived_at IS NULL"
        ).fetchone()
        cur = c.execute(
            "INSERT INTO checklists (title, description, source, position, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title, _clip(description, MAX_NOTE), src, int(row["p"]), now, now),
        )
        cid = int(cur.lastrowid)
        c.commit()
    for it in (items or [])[:MAX_ITEMS_PER_LIST]:
        if isinstance(it, dict):
            add_item(cid, it.get("text", ""), note=it.get("note", ""), done=bool(it.get("done")))
        else:
            add_item(cid, str(it))
    return cid


def list_checklists(archived: bool = False) -> list[dict]:
    """Active lists (archived=False) or the archive (archived=True), each with a
    done/total progress count so the index can render without N+1 queries."""
    where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
    order = "archived_at DESC, id DESC" if archived else "position, id DESC"
    with _LOCK:
        rows = _conn().execute(
            f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM items i WHERE i.checklist_id = c.id) AS total,
                   (SELECT COUNT(*) FROM items i WHERE i.checklist_id = c.id AND i.done = 1) AS done
            FROM checklists c WHERE {where} ORDER BY {order}
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_checklist(checklist_id: int) -> dict | None:
    """One checklist with its ordered items, or None if it does not exist."""
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT * FROM checklists WHERE id = ?", (int(checklist_id),)).fetchone()
        if row is None:
            return None
        items = c.execute(
            "SELECT * FROM items WHERE checklist_id = ? ORDER BY position, id",
            (int(checklist_id),),
        ).fetchall()
    out = dict(row)
    out["items"] = [dict(i) for i in items]
    out["total"] = len(out["items"])
    out["done"] = sum(1 for i in out["items"] if i["done"])
    return out


def update_checklist(checklist_id: int, title: str | None = None,
                     description: str | None = None) -> bool:
    sets, params = [], []
    if title is not None:
        sets.append("title = ?"); params.append(_clip(title, MAX_TITLE) or "Untitled checklist")
    if description is not None:
        sets.append("description = ?"); params.append(_clip(description, MAX_NOTE))
    if not sets:
        return False
    sets.append("updated_at = ?"); params.append(_now())
    params.append(int(checklist_id))
    with _LOCK:
        c = _conn()
        cur = c.execute(f"UPDATE checklists SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()
        return cur.rowcount > 0


def archive_checklist(checklist_id: int) -> bool:
    """The delete path. Reversible by restore_checklist; items are untouched."""
    now = _now()
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "UPDATE checklists SET archived_at = ?, updated_at = ? WHERE id = ? AND archived_at IS NULL",
            (now, now, int(checklist_id)),
        )
        c.commit()
        return cur.rowcount > 0


def restore_checklist(checklist_id: int) -> bool:
    """Bring an archived list back to the top of the active list."""
    now = _now()
    with _LOCK:
        c = _conn()
        row = c.execute(
            "SELECT COALESCE(MIN(position), 0) - 1 AS p FROM checklists WHERE archived_at IS NULL"
        ).fetchone()
        cur = c.execute(
            "UPDATE checklists SET archived_at = NULL, position = ?, updated_at = ?"
            " WHERE id = ? AND archived_at IS NOT NULL",
            (int(row["p"]), now, int(checklist_id)),
        )
        c.commit()
        return cur.rowcount > 0


def purge_checklist(checklist_id: int) -> bool:
    """Permanent delete — only ever reached from the Archive tab, and never
    proposable by the assistant. Items cascade."""
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM checklists WHERE id = ?", (int(checklist_id),))
        c.commit()
        return cur.rowcount > 0


def reorder_checklists(order: list[int]) -> int:
    """Persist a drag-reorder of the active list. Ids not named keep their
    relative order after the ones that were."""
    now = _now()
    with _LOCK:
        c = _conn()
        n = 0
        for pos, cid in enumerate(order):
            cur = c.execute(
                "UPDATE checklists SET position = ?, updated_at = ? WHERE id = ? AND archived_at IS NULL",
                (pos, now, int(cid)),
            )
            n += cur.rowcount
        c.commit()
        return n


# --- Items ------------------------------------------------------------------

def _touch(conn: sqlite3.Connection, checklist_id: int) -> None:
    conn.execute("UPDATE checklists SET updated_at = ? WHERE id = ?", (_now(), int(checklist_id)))


def add_item(checklist_id: int, text: str, note: str = "", done: bool = False) -> int | None:
    """Append a step. Returns None if the list does not exist or is already at
    MAX_ITEMS_PER_LIST."""
    now = _now()
    with _LOCK:
        c = _conn()
        if c.execute("SELECT 1 FROM checklists WHERE id = ?", (int(checklist_id),)).fetchone() is None:
            return None
        cnt = c.execute(
            "SELECT COUNT(*) AS n FROM items WHERE checklist_id = ?", (int(checklist_id),)
        ).fetchone()["n"]
        if int(cnt) >= MAX_ITEMS_PER_LIST:
            return None
        row = c.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM items WHERE checklist_id = ?",
            (int(checklist_id),),
        ).fetchone()
        cur = c.execute(
            "INSERT INTO items (checklist_id, text, note, done, done_at, position, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(checklist_id), _clip(text, MAX_TEXT), _clip(note, MAX_NOTE),
             1 if done else 0, now if done else None, int(row["p"]), now, now),
        )
        _touch(c, checklist_id)
        c.commit()
        return int(cur.lastrowid)


def update_item(item_id: int, text: str | None = None, note: str | None = None,
                done: bool | None = None) -> bool:
    sets, params = [], []
    if text is not None:
        sets.append("text = ?"); params.append(_clip(text, MAX_TEXT))
    if note is not None:
        sets.append("note = ?"); params.append(_clip(note, MAX_NOTE))
    if done is not None:
        sets.append("done = ?"); params.append(1 if done else 0)
        sets.append("done_at = ?"); params.append(_now() if done else None)
    if not sets:
        return False
    sets.append("updated_at = ?"); params.append(_now())
    params.append(int(item_id))
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT checklist_id FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if row is None:
            return False
        c.execute(f"UPDATE items SET {', '.join(sets)} WHERE id = ?", params)
        _touch(c, int(row["checklist_id"]))
        c.commit()
        return True


def toggle_item(item_id: int) -> dict | None:
    """Flip one step's done state. Returns the new state, or None if unknown."""
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT checklist_id, done FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if row is None:
            return None
        new = 0 if int(row["done"]) else 1
        now = _now()
        c.execute(
            "UPDATE items SET done = ?, done_at = ?, updated_at = ? WHERE id = ?",
            (new, now if new else None, now, int(item_id)),
        )
        _touch(c, int(row["checklist_id"]))
        c.commit()
        return {"id": int(item_id), "done": bool(new)}


def delete_item(item_id: int) -> bool:
    """Remove one step. The list-level archive is the recovery path for a whole
    list; a single mis-typed step does not need a tombstone."""
    with _LOCK:
        c = _conn()
        row = c.execute("SELECT checklist_id FROM items WHERE id = ?", (int(item_id),)).fetchone()
        if row is None:
            return False
        c.execute("DELETE FROM items WHERE id = ?", (int(item_id),))
        _touch(c, int(row["checklist_id"]))
        c.commit()
        return True


def reorder_items(checklist_id: int, order: list[int]) -> int:
    with _LOCK:
        c = _conn()
        n = 0
        for pos, iid in enumerate(order):
            cur = c.execute(
                "UPDATE items SET position = ?, updated_at = ? WHERE id = ? AND checklist_id = ?",
                (pos, _now(), int(iid), int(checklist_id)),
            )
            n += cur.rowcount
        _touch(c, checklist_id)
        c.commit()
        return n


def set_all_done(checklist_id: int, done: bool) -> int:
    """Check or clear every step at once."""
    now = _now()
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "UPDATE items SET done = ?, done_at = ?, updated_at = ? WHERE checklist_id = ?",
            (1 if done else 0, now if done else None, now, int(checklist_id)),
        )
        _touch(c, checklist_id)
        c.commit()
        return cur.rowcount


# --- Summary ----------------------------------------------------------------

def summary() -> dict:
    """Counts for the view header and for Adam's own situational awareness."""
    with _LOCK:
        c = _conn()
        active = c.execute("SELECT COUNT(*) AS n FROM checklists WHERE archived_at IS NULL").fetchone()["n"]
        archived = c.execute("SELECT COUNT(*) AS n FROM checklists WHERE archived_at IS NOT NULL").fetchone()["n"]
        row = c.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(i.done), 0) AS done FROM items i"
            " JOIN checklists c ON c.id = i.checklist_id WHERE c.archived_at IS NULL"
        ).fetchone()
    return {
        "active": int(active),
        "archived": int(archived),
        "items": int(row["total"]),
        "items_done": int(row["done"]),
    }
