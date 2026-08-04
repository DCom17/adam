"""
Adam — local-first health store (Phase H1).

The private, on-device counterpart to the Finance engine: weight, meals with
macros, water intake, and daily wearable metrics (steps/sleep/heart-rate, for the
Garmin add-on in a later phase). Same shape and rules as finance_store — one shared WAL
connection behind a re-entrant lock, path from config, stdlib sqlite3, and the
data lives ONLY under data/health/ (gitignored, never shipped). Keeping health
data on the user's machine instead of a cloud tracker is the whole point.

Design carried from the plan (docs/PLAN-FINANCE-HEALTH.md §5):
  - This module stores; health_metrics computes every displayed number.
  - Meal macros come from manual entry now; a later phase adds photo estimation
    (grounded against a nutrition table) and edit-corrections — the `corrections`
    table is created now so that loop has a home.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import config

SCHEMA_VERSION = 2   # 2: water log

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


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
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

        -- Weigh-ins. Multiple per day allowed; metrics use the latest on/before
        -- a date. Unit stored per row so lb/kg installs both round-trip.
        CREATE TABLE IF NOT EXISTS weights (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            date   TEXT NOT NULL,            -- YYYY-MM-DD
            weight REAL NOT NULL DEFAULT 0,
            unit   TEXT NOT NULL DEFAULT 'lb',
            note   TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_weight_date ON weights(date);

        -- Logged meals/foods with macros. `source` = manual | text | photo.
        CREATE TABLE IF NOT EXISTS meals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            date      TEXT NOT NULL,          -- YYYY-MM-DD
            name      TEXT NOT NULL DEFAULT '',
            qty       TEXT NOT NULL DEFAULT '',   -- free-text portion ("1 bowl", "200 g")
            kcal      REAL NOT NULL DEFAULT 0,
            protein_g REAL NOT NULL DEFAULT 0,
            carbs_g   REAL NOT NULL DEFAULT 0,
            fat_g     REAL NOT NULL DEFAULT 0,
            source    TEXT NOT NULL DEFAULT 'manual',
            note      TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_meal_date ON meals(date);

        -- Water intake. One row per drink logged; stored in millilitres (the
        -- canonical unit) so oz/ml installs sum the same. The user's display
        -- unit lives in settings (water_unit).
        CREATE TABLE IF NOT EXISTS water (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,              -- YYYY-MM-DD
            ml   REAL NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_water_date ON water(date);

        -- One row per day of wearable/daily metrics. Manual now; the Garmin
        -- add-on (later) writes the same row. Nullable-by-default via 0/NULL.
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date         TEXT PRIMARY KEY,     -- YYYY-MM-DD
            steps        INTEGER,
            sleep_min    INTEGER,
            resting_hr   INTEGER,
            stress       INTEGER,
            body_battery INTEGER,
            active_kcal  INTEGER,
            source       TEXT NOT NULL DEFAULT 'manual'
        );

        -- Daily targets + profile, as a JSON-typed key/value store (mirrors
        -- finance settings): target_kcal, target_protein_g, target_carbs_g,
        -- target_fat_g, target_water_ml, weight_goal, weight_unit, water_unit.
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- Food-name -> user-corrected macros, so a photo/text estimate the user
        -- fixed is reused next time (the learning loop, used in the photo phase).
        CREATE TABLE IF NOT EXISTS corrections (
            name      TEXT PRIMARY KEY,      -- lowercased food name
            kcal      REAL NOT NULL DEFAULT 0,
            protein_g REAL NOT NULL DEFAULT 0,
            carbs_g   REAL NOT NULL DEFAULT 0,
            fat_g     REAL NOT NULL DEFAULT 0
        );
        """
    )
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif int(row["version"]) < SCHEMA_VERSION:
        # Every migration so far is purely additive — the CREATE TABLE/INDEX
        # IF NOT EXISTS script above already brought an older DB current, so the
        # only thing left is to stamp the new version.
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


def init(db_path: Path | str | None = None) -> dict:
    """Open (or create) the health DB. Idempotent; safe after config.HEALTH_DB is
    redirected (tests do this)."""
    global _CONN, _DB_PATH
    with _LOCK:
        target = Path(db_path) if db_path is not None else Path(config.HEALTH_DB)
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
        if _CONN is None or _DB_PATH != Path(config.HEALTH_DB):
            init()
        assert _CONN is not None
        return _CONN


# --- Weights ----------------------------------------------------------------

def add_weight(date: str, weight: float, unit: str = "lb", note: str = "") -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "INSERT INTO weights (date, weight, unit, note) VALUES (?, ?, ?, ?)",
            (date, float(weight), unit, note),
        )
        c.commit()
        return int(cur.lastrowid)


def replace_weight_by_note(date: str, weight: float, unit: str = "lb", note: str = "") -> int:
    """Insert a weigh-in, first removing any same-date row that carries the same
    note. Synced sources (e.g. the Garmin add-on, note='Garmin sync') use this so
    re-syncing a day updates in place instead of stacking a duplicate each refresh;
    manual weigh-ins go through add_weight and can still stack as before."""
    with _LOCK:
        c = _conn()
        if note:
            c.execute("DELETE FROM weights WHERE date = ? AND note = ?", (date, note))
        cur = c.execute(
            "INSERT INTO weights (date, weight, unit, note) VALUES (?, ?, ?, ?)",
            (date, float(weight), unit, note),
        )
        c.commit()
        return int(cur.lastrowid)


def list_weights(since: str | None = None, limit: int | None = None) -> list[dict]:
    sql = "SELECT * FROM weights"
    params: list = []
    if since:
        sql += " WHERE date >= ?"
        params.append(since)
    sql += " ORDER BY date, id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def latest_weight(on_or_before: str | None = None) -> dict | None:
    sql = "SELECT * FROM weights"
    params: list = []
    if on_or_before:
        sql += " WHERE date <= ?"
        params.append(on_or_before)
    sql += " ORDER BY date DESC, id DESC LIMIT 1"
    with _LOCK:
        row = _conn().execute(sql, params).fetchone()
    return dict(row) if row else None


def get_weight(weight_id: int) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM weights WHERE id = ?", (weight_id,)).fetchone()
    return dict(row) if row else None


_WEIGHT_FIELDS = ("date", "weight", "unit", "note")


def update_weight(weight_id: int, **fields) -> bool:
    """Patch a weigh-in in place. Mirrors update_meal: unknown keys are ignored,
    and an empty patch is a no-op rather than an error."""
    sets, params = [], []
    for k, v in fields.items():
        if k not in _WEIGHT_FIELDS:
            continue
        sets.append(f"{k} = ?")
        params.append(float(v) if k == "weight" else v)
    if not sets:
        return False
    params.append(weight_id)
    with _LOCK:
        c = _conn()
        cur = c.execute(f"UPDATE weights SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()
        return cur.rowcount > 0


def delete_weight(weight_id: int) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM weights WHERE id = ?", (weight_id,))
        c.commit()
        return cur.rowcount > 0


# --- Meals ------------------------------------------------------------------

def add_meal(date: str, name: str, *, qty: str = "", kcal: float = 0,
             protein_g: float = 0, carbs_g: float = 0, fat_g: float = 0,
             source: str = "manual", note: str = "") -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            """INSERT INTO meals (date, name, qty, kcal, protein_g, carbs_g, fat_g, source, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, name, qty, float(kcal), float(protein_g), float(carbs_g),
             float(fat_g), source, note),
        )
        c.commit()
        return int(cur.lastrowid)


def get_meal(meal_id: int) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
    return dict(row) if row else None


def list_meals(date: str | None = None, month: str | None = None,
               limit: int | None = None) -> list[dict]:
    clauses, params = [], []
    if date:
        clauses.append("date = ?"); params.append(date)
    if month:
        clauses.append("date LIKE ?"); params.append(month + "-%")
    sql = "SELECT * FROM meals"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"; params.append(int(limit))
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


_MEAL_FIELDS = ("name", "date", "qty", "kcal", "protein_g", "carbs_g", "fat_g", "source", "note")


def update_meal(meal_id: int, **fields) -> bool:
    sets, params = [], []
    for k, v in fields.items():
        if k not in _MEAL_FIELDS:
            continue
        sets.append(f"{k} = ?")
        params.append(float(v) if k in ("kcal", "protein_g", "carbs_g", "fat_g") else v)
    if not sets:
        return False
    params.append(meal_id)
    with _LOCK:
        c = _conn()
        cur = c.execute(f"UPDATE meals SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()
        return cur.rowcount > 0


def delete_meal(meal_id: int) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        c.commit()
        return cur.rowcount > 0


# --- Water ------------------------------------------------------------------

def add_water(date: str, ml: float, note: str = "") -> int:
    """Log a drink. `ml` is always millilitres — callers convert from the user's
    unit first (health_metrics.to_ml), so every row sums cleanly."""
    with _LOCK:
        c = _conn()
        cur = c.execute("INSERT INTO water (date, ml, note) VALUES (?, ?, ?)",
                        (date, float(ml), note))
        c.commit()
        return int(cur.lastrowid)


def list_water(date: str | None = None, since: str | None = None,
               limit: int | None = None) -> list[dict]:
    """Water rows, newest first within a day (so the UI's 'undo last' is row 0)."""
    clauses, params = [], []
    if date:
        clauses.append("date = ?"); params.append(date)
    if since:
        clauses.append("date >= ?"); params.append(since)
    sql = "SELECT * FROM water"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"; params.append(int(limit))
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_water(water_id: int) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM water WHERE id = ?", (water_id,)).fetchone()
    return dict(row) if row else None


_WATER_FIELDS = ("date", "ml", "note")


def update_water(water_id: int, **fields) -> bool:
    """Patch a drink in place. `ml` is canonical — callers convert from the
    user's unit first, exactly as add_water requires."""
    sets, params = [], []
    for k, v in fields.items():
        if k not in _WATER_FIELDS:
            continue
        sets.append(f"{k} = ?")
        params.append(float(v) if k == "ml" else v)
    if not sets:
        return False
    params.append(water_id)
    with _LOCK:
        c = _conn()
        cur = c.execute(f"UPDATE water SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()
        return cur.rowcount > 0


def delete_water(water_id: int) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM water WHERE id = ?", (water_id,))
        c.commit()
        return cur.rowcount > 0


# --- Daily metrics ----------------------------------------------------------

_METRIC_FIELDS = ("steps", "sleep_min", "resting_hr", "stress", "body_battery",
                  "active_kcal", "source")


def set_daily_metric(date: str, **fields) -> None:
    """Upsert the day's wearable metrics. Only the fields provided are written,
    so a manual step entry doesn't wipe a synced sleep value."""
    provided = {k: v for k, v in fields.items() if k in _METRIC_FIELDS}
    with _LOCK:
        c = _conn()
        c.execute("INSERT OR IGNORE INTO daily_metrics (date) VALUES (?)", (date,))
        if provided:
            sets = ", ".join(f"{k} = ?" for k in provided)
            c.execute(f"UPDATE daily_metrics SET {sets} WHERE date = ?",
                      list(provided.values()) + [date])
        c.commit()


def get_daily_metric(date: str) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM daily_metrics WHERE date = ?", (date,)).fetchone()
    return dict(row) if row else None


def list_daily_metrics(since: str | None = None) -> list[dict]:
    sql = "SELECT * FROM daily_metrics"
    params: list = []
    if since:
        sql += " WHERE date >= ?"; params.append(since)
    sql += " ORDER BY date"
    with _LOCK:
        rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --- Settings (targets + profile) -------------------------------------------

_SETTING_DEFAULTS = {
    "target_kcal": None,        # None -> no target set yet
    "target_protein_g": None,
    "target_carbs_g": None,
    "target_fat_g": None,
    "target_water_ml": None,
    "weight_goal": None,
    "weight_unit": "lb",
    "water_unit": "oz",         # display unit only — rows are always ml
}


def set_setting(key: str, value) -> None:
    with _LOCK:
        c = _conn()
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, json.dumps(value)))
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
    out = dict(_SETTING_DEFAULTS)
    with _LOCK:
        rows = _conn().execute("SELECT key, value FROM settings").fetchall()
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            out[r["key"]] = r["value"]
    return out


# --- Corrections (photo/text macro memory; used in the photo phase) ---------

def upsert_correction(name: str, kcal: float, protein_g: float,
                      carbs_g: float, fat_g: float) -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            """INSERT INTO corrections (name, kcal, protein_g, carbs_g, fat_g)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET kcal=excluded.kcal,
                 protein_g=excluded.protein_g, carbs_g=excluded.carbs_g, fat_g=excluded.fat_g""",
            (name.lower().strip(), float(kcal), float(protein_g), float(carbs_g), float(fat_g)),
        )
        c.commit()


def match_correction(name: str) -> dict | None:
    with _LOCK:
        row = _conn().execute("SELECT * FROM corrections WHERE name = ?",
                              (name.lower().strip(),)).fetchone()
    return dict(row) if row else None
