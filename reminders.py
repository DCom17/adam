"""Proactive reminders — Adam notices what slipped and nudges, in character.

The easy-to-miss chores of the day (water, meals, the evening weigh-in, the
morning plan, the weekly bank CSVs) each get a check that looks at the REAL
data, not the clock alone: a reminder only goes out when the thing genuinely
hasn't happened yet. Each one fires at most once a day (water: a few spaced
nudges), one banner at a time at least 20 minutes apart, never inside quiet
hours, and — when Google Calendar is connected — never while a calendar event
is in progress (it waits for the event to end).

A reminder for a tracker the user has never touched stays silent: a new user
who doesn't log meals is never asked about meals. That is the "active" gate in
`gather_facts`. It is deliberately "ever used", not "used recently": a lapsed
habit (no weigh-in for two months) is exactly what these reminders are for.

Layout:
  * CATALOG / prefs     — what exists, what the user turned on, at what time
  * gather_facts()      — read-only snapshot of the trackers (all failure-safe)
  * evaluate()          — PURE: (now, prefs, state, facts) -> what's due
  * build_message()     — the banner text, front-loaded for a phone lock screen
  * tick() / run_loop() — the background loop server.py starts

Banner copy rule: an iPhone lock screen shows the title in full and only the
first couple of body lines. So the TITLE is the whole ask ("Log dinner, sir?")
and the body is the one-line why. Titles stay <= 40 chars, bodies <= 110.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import config

# --- Catalog -----------------------------------------------------------------
# `time` is the default "from" time (local, HH:MM). `until` bounds the window:
# past it, a missed reminder is dropped for the day rather than sent late.
# `action` is what a tap opens (index.html handles each).
CATALOG: list[dict] = [
    {"id": "plan", "label": "Daily plan",
     "desc": "If the day hasn't been planned by this time, Adam asks what you want to get done.",
     "time": "09:00", "until": "12:30", "action": "plan", "group": "Day"},
    {"id": "water", "label": "Water pace",
     "desc": "When you fall behind the pace for your daily water goal. Up to 3 spaced nudges a day.",
     "time": "10:00", "until": "20:30", "action": "health", "group": "Health"},
    {"id": "meal_am", "label": "Breakfast / lunch check",
     "desc": "If nothing's been logged yet today, Adam asks whether there's a meal to log.",
     "time": "11:30", "until": "15:00", "action": "meal", "group": "Health"},
    {"id": "meal_pm", "label": "Dinner check",
     "desc": "If nothing's been logged since mid-afternoon, Adam asks about dinner.",
     "time": "19:30", "until": "22:00", "action": "meal", "group": "Health"},
    {"id": "weigh_in", "label": "End-of-day weigh-in",
     "desc": "If no weight is logged today by this time.",
     "time": "20:30", "until": "23:30", "action": "health", "group": "Health"},
    {"id": "finance_csv", "label": "Weekly bank statements",
     "desc": "On your chosen day, if no bank CSV has been imported in the last 5 days.",
     "time": "17:00", "until": "22:00", "action": "finance", "group": "Money"},
]
KIND_IDS = [k["id"] for k in CATALOG]
_BY_ID = {k["id"]: k for k in CATALOG}

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DEFAULT_PREFS: dict = {
    "enabled": True,
    "quiet_start": "22:00",
    "quiet_end": "07:00",
    "hold_for_calendar": True,
    "paused_until": 0,          # epoch seconds; 0 = not paused
    "finance_day": 6,           # 0=Mon .. 6=Sun
    "kinds": {k["id"]: {"on": True, "time": k["time"]} for k in CATALOG},
}

WATER_MAX_PER_DAY = 3
WATER_MIN_GAP_S = 150 * 60      # 2.5 h between water nudges
GLOBAL_GAP_S = 20 * 60          # at least 20 min between ANY two reminders
WATER_DEFAULT_TARGET_ML = 2000  # used only when the user never set a goal
FINANCE_STALE_DAYS = 5
MEAL_SNAPSHOT_AT = "15:00"      # dinner check compares against this count

TITLE_MAX = 40
BODY_MAX = 110

_LOCK = threading.Lock()        # prefs read-modify-write
_STATE_LOCK = threading.Lock()  # state read-modify-write (the loop vs. a test send)


def _prefs_file() -> Path:
    return Path(config.STATE_DIR) / "reminder_prefs.json"


def _state_file() -> Path:
    return Path(config.STATE_DIR) / "reminder_state.json"


# --- Time helpers ------------------------------------------------------------

def _hm(s: str, default: str = "00:00") -> tuple[int, int]:
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return _hm(default) if default != s else (0, 0)


def _mins(s: str) -> int:
    h, m = _hm(s)
    return h * 60 + m


def _valid_hm(s: Any) -> bool:
    try:
        h, m = str(s).split(":")
        return len(m) == 2 and 0 <= int(h) < 24 and 0 <= int(m) < 60
    except (ValueError, AttributeError):
        return False


def in_quiet_hours(now: datetime, prefs: dict) -> bool:
    """Quiet hours may wrap midnight (22:00 -> 07:00) or not (13:00 -> 14:00)."""
    q0, q1 = _mins(prefs.get("quiet_start", "22:00")), _mins(prefs.get("quiet_end", "07:00"))
    cur = now.hour * 60 + now.minute
    if q0 == q1:
        return False
    if q0 < q1:
        return q0 <= cur < q1
    return cur >= q0 or cur < q1


def fmt_time(hm: str) -> str:
    """'19:30' -> '7:30 PM' (for copy and the panel)."""
    h, m = _hm(hm)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


# --- Prefs + state persistence ---------------------------------------------------

def _merge_prefs(raw: dict | None) -> dict:
    p = json.loads(json.dumps(DEFAULT_PREFS))
    if not isinstance(raw, dict):
        return p
    for k in ("enabled", "hold_for_calendar"):
        if isinstance(raw.get(k), bool):
            p[k] = raw[k]
    for k in ("quiet_start", "quiet_end"):
        if _valid_hm(raw.get(k)):
            p[k] = raw[k]
    if isinstance(raw.get("paused_until"), (int, float)):
        p["paused_until"] = float(raw["paused_until"])
    if isinstance(raw.get("finance_day"), int) and 0 <= raw["finance_day"] <= 6:
        p["finance_day"] = raw["finance_day"]
    kinds = raw.get("kinds")
    if isinstance(kinds, dict):
        for kid, v in kinds.items():
            if kid not in p["kinds"] or not isinstance(v, dict):
                continue
            if isinstance(v.get("on"), bool):
                p["kinds"][kid]["on"] = v["on"]
            if _valid_hm(v.get("time")):
                p["kinds"][kid]["time"] = v["time"]
    return p


def load_prefs() -> dict:
    try:
        return _merge_prefs(json.loads(_prefs_file().read_text("utf-8")))
    except Exception:
        return _merge_prefs(None)


def save_prefs(patch: dict) -> dict:
    """Merge a partial update onto the current prefs (unknown keys ignored,
    invalid values dropped) and persist. Returns the resulting prefs."""
    with _LOCK:
        cur = load_prefs()
        merged = dict(cur)
        for k, v in (patch or {}).items():
            if k == "kinds" and isinstance(v, dict):
                kinds = {kid: dict(cur["kinds"][kid]) for kid in cur["kinds"]}
                for kid, kv in v.items():
                    if kid in kinds and isinstance(kv, dict):
                        kinds[kid].update(kv)
                merged["kinds"] = kinds
            else:
                merged[k] = v
        out = _merge_prefs(merged)
        f = _prefs_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out


def load_state() -> dict:
    try:
        s = json.loads(_state_file().read_text("utf-8"))
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _day(state: dict, date: str) -> dict:
    """Today's record; older days are dropped so the file never grows."""
    days = state.setdefault("days", {})
    for d in [d for d in days if d != date]:
        days.pop(d, None)
    return days.setdefault(date, {"fired": {}, "water_ts": []})


# --- Facts (read-only, every source failure-safe) -------------------------------

def _plan_packet() -> Path | None:
    try:
        vp = str(config.VAULT_PATH or "").strip()
        return Path(vp) / "06_calendar" / "latest_calendar_packet.md" if vp else None
    except Exception:
        return None


def _plan_used(text: str) -> bool:
    """The shipped packet template has an empty '## Date:' and no year anywhere;
    a packet daily planning has written carries a real date. (Its mtime alone
    can't tell: an install stamps the template with a fresh one.)"""
    return bool(re.search(r"(?<![0-9])20[0-9][0-9](?![0-9])", text))


def gather_facts(now: datetime) -> dict:
    """Snapshot of what's been logged. Never raises; a source that can't be read
    reports itself inactive so its reminders stay silent rather than guess."""
    today = now.strftime("%Y-%m-%d")
    f: dict = {"date": today}

    # Health — only if the user has a health DB at all (a fresh install that
    # never opened the tracker must not get one created by a background loop).
    if Path(config.HEALTH_DB).exists():
        try:
            import health_store as hs
            s = hs.get_settings()
            f["water_active"] = bool(hs.list_water(limit=1))
            f["water_ml"] = sum(float(r.get("ml") or 0) for r in hs.list_water(date=today))
            f["water_target_ml"] = float(s.get("target_water_ml") or 0) or None
            f["water_unit"] = s.get("water_unit") or "oz"
            f["meal_active"] = bool(hs.list_meals(limit=1))
            f["meals_today"] = len(hs.list_meals(date=today))
            f["weight_active"] = bool(hs.list_weights(limit=1))
            f["weighed_today"] = bool(hs.list_weights(since=today))
        except Exception:
            pass

    if Path(config.FINANCE_DB).exists():
        try:
            import finance_store as fs
            batches = fs.list_batches()
            f["finance_active"] = bool(batches)
            f["finance_last_import_ts"] = max((float(b.get("created_at") or 0) for b in batches), default=0)
            f["finance_unreviewed"] = sum(1 for b in batches if not b.get("reviewed"))
        except Exception:
            pass

    pk = _plan_packet()
    try:
        if pk and pk.is_file():
            mt = datetime.fromtimestamp(pk.stat().st_mtime)
            with open(pk, encoding="utf-8", errors="replace") as fh:
                f["plan_active"] = _plan_used(fh.read(8192))
            f["planned_today"] = mt.strftime("%Y-%m-%d") == today
    except Exception:
        pass
    return f


# --- Calendar hold -------------------------------------------------------------------

_CAL_CACHE: dict = {"ts": 0.0, "events": None}
_CAL_TTL_S = 600


def calendar_busy(now: datetime) -> bool:
    """True while a timed Google Calendar event is in progress. False when the
    calendar isn't connected or can't be read — a calendar hiccup never blocks
    a reminder for the whole day."""
    try:
        import google_calendar
        if not google_calendar.is_configured():
            return False
        if _CAL_CACHE["events"] is None or time.monotonic() - _CAL_CACHE["ts"] > _CAL_TTL_S:
            loc = now.astimezone()
            start = loc.replace(hour=0, minute=0, second=0, microsecond=0)
            _CAL_CACHE["events"] = google_calendar.list_events(
                start.isoformat(), (start + timedelta(days=1)).isoformat(), timeout=8)
            _CAL_CACHE["ts"] = time.monotonic()
        loc = now.astimezone()
        for e in _CAL_CACHE["events"] or []:
            if e.get("all_day"):
                continue
            try:
                s = datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00"))
                en = datetime.fromisoformat(str(e.get("end")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if s <= loc < en:
                return True
    except Exception:
        return False
    return False


# --- Evaluation (pure) ---------------------------------------------------------------

def _water_behind(now: datetime, prefs: dict, facts: dict) -> tuple[float, float, float] | None:
    """(logged_ml, target_ml, behind_ml) when behind pace by a meaningful amount."""
    target = facts.get("water_target_ml") or WATER_DEFAULT_TARGET_ML
    start = _mins(prefs.get("quiet_end", "07:00"))
    end = _mins(_BY_ID["water"]["until"])
    cur = now.hour * 60 + now.minute
    if end <= start:
        return None
    frac = min(1.0, max(0.0, (cur - start) / (end - start)))
    expected = target * frac
    logged = float(facts.get("water_ml") or 0)
    behind = expected - logged
    # Meaningful = a real glass behind AND at least 15% of the day's goal.
    if behind >= max(240.0, 0.15 * target):
        return logged, target, behind
    return None


def evaluate(now: datetime, prefs: dict, state: dict, facts: dict) -> list[str]:
    """Which reminder kinds are due right now. Pure — no I/O, no clock reads.
    Gates, in order: master switch, pause, quiet hours, then each kind's own
    toggle / window / already-sent / still-not-done check."""
    if not prefs.get("enabled", True):
        return []
    if float(prefs.get("paused_until") or 0) > now.timestamp():
        return []
    if in_quiet_hours(now, prefs):
        return []
    date = now.strftime("%Y-%m-%d")
    day = (state.get("days") or {}).get(date) or {"fired": {}, "water_ts": []}
    fired = day.get("fired") or {}
    cur = now.hour * 60 + now.minute
    due: list[str] = []
    for k in CATALOG:
        kid = k["id"]
        kp = prefs["kinds"].get(kid) or {}
        if not kp.get("on", True):
            continue
        t0 = _mins(kp.get("time", k["time"]))
        t1 = max(_mins(k["until"]), t0 + 60)  # a user time past `until` still gets an hour
        if not (t0 <= cur < t1):
            continue
        if kid == "water":
            if not facts.get("water_active"):
                continue
            sent = day.get("water_ts") or []
            if len(sent) >= WATER_MAX_PER_DAY:
                continue
            if sent and now.timestamp() - max(sent) < WATER_MIN_GAP_S:
                continue
            if _water_behind(now, prefs, facts):
                due.append(kid)
            continue
        if fired.get(kid):
            continue
        if kid == "plan":
            if facts.get("plan_active") and not facts.get("planned_today"):
                due.append(kid)
        elif kid == "meal_am":
            if facts.get("meal_active") and int(facts.get("meals_today") or 0) == 0:
                due.append(kid)
        elif kid == "meal_pm":
            if not facts.get("meal_active"):
                continue
            n = int(facts.get("meals_today") or 0)
            snap = day.get("meals_at_snapshot")
            # Meals carry a date but no time, so "nothing since this afternoon"
            # is measured against the count recorded at MEAL_SNAPSHOT_AT. If the
            # server wasn't up then, fall back to "fewer than two today".
            if n == 0 or (snap is not None and n <= int(snap)) or (snap is None and n < 2):
                due.append(kid)
        elif kid == "weigh_in":
            if facts.get("weight_active") and not facts.get("weighed_today"):
                due.append(kid)
        elif kid == "finance_csv":
            if now.weekday() != int(prefs.get("finance_day", 6)):
                continue
            if not facts.get("finance_active"):
                continue
            last = float(facts.get("finance_last_import_ts") or 0)
            if now.timestamp() - last >= FINANCE_STALE_DAYS * 86400:
                due.append(kid)
    return due


# --- Copy ------------------------------------------------------------------------

def _amount(ml: float, unit: str) -> str:
    if unit == "ml":
        return f"{round(ml):,} ml"
    if unit == "l" or unit == "L":
        return f"{ml / 1000:.1f} L"
    if unit == "cup" or unit == "cups":
        return f"{ml / 236.6:.1f} cups"
    return f"{round(ml / 29.5735)} oz"


def _amount_pair(logged: float, target: float, unit: str) -> str:
    if unit in ("ml", "l", "L", "cup", "cups"):
        return f"{_amount(logged, unit)} of {_amount(target, unit)}"
    return f"{round(logged / 29.5735)} of {round(target / 29.5735)} oz"


def build_message(kind: str, facts: dict, prefs: dict | None = None,
                  now: datetime | None = None) -> dict:
    """Title = the whole ask (survives lock-screen truncation); body = one line
    of why. Always returns something sendable — a test send with no data still
    reads naturally."""
    prefs = prefs or load_prefs()
    now = now or datetime.now()
    unit = facts.get("water_unit") or "oz"
    if kind == "plan":
        title, body = "What's the plan today, sir?", "Nothing's planned for today yet. Tap and tell me what we're getting done."
    elif kind == "water":
        wb = _water_behind(now, prefs, facts)
        if wb:
            logged, target, behind = wb
            title = f"Water check, sir: {_amount_pair(logged, target, unit)}"
            body = f"About {_amount(behind, unit)} behind pace. Drink a glass, then tap to log it."
        else:
            title, body = "Water check, sir", "Time for a glass. Tap to log it."
    elif kind == "meal_am":
        title, body = "Any meal to log, sir?", "Nothing's logged today yet. Tap and tell me what you ate."
    elif kind == "meal_pm":
        title, body = "Log dinner, sir?", "Nothing's logged since this afternoon. Tap and tell me what you had."
    elif kind == "weigh_in":
        title, body = "Weigh-in, sir?", "No weight logged today. Step on the scale, then tap to log it."
    elif kind == "finance_csv":
        last = float(facts.get("finance_last_import_ts") or 0)
        title = "Bank statements are due, sir"
        if last:
            days = max(1, int((now.timestamp() - last) // 86400))
            body = f"Last import was {days} day{'s' if days != 1 else ''} ago. Tap to drop in this week's CSVs."
        else:
            body = "Tap to drop in this week's CSVs."
        n = int(facts.get("finance_unreviewed") or 0)
        if n:
            body = f"{n} import{'s' if n != 1 else ''} still waiting on review, too. " + body
    else:
        title, body = "A reminder, sir", "Tap to open Adam."
    return {"kind": kind, "title": title[:TITLE_MAX], "body": body[:BODY_MAX],
            "action": (_BY_ID.get(kind) or {}).get("action", "open")}


# --- The loop ----------------------------------------------------------------------

def _record_sent(state: dict, now: datetime, msg: dict, test: bool = False) -> None:
    day = _day(state, now.strftime("%Y-%m-%d"))
    if not test:
        if msg["kind"] == "water":
            day.setdefault("water_ts", []).append(now.timestamp())
        else:
            day.setdefault("fired", {})[msg["kind"]] = now.timestamp()
    log = state.setdefault("log", [])
    log.insert(0, {"ts": now.timestamp(), "kind": msg["kind"], "title": msg["title"],
                   "body": msg["body"], "test": bool(test)})
    del log[20:]


def tick(send: Callable[[dict], None], now: datetime | None = None,
         facts: dict | None = None, busy: Callable[[datetime], bool] | None = None) -> list[dict]:
    """One pass: record the afternoon meal snapshot, evaluate, and send what's
    due. `send` delivers one message (server passes the Web Push sender).
    Returns the messages sent. Tests inject now/facts/busy."""
    now = now or datetime.now()
    prefs = load_prefs()
    facts = facts if facts is not None else gather_facts(now)
    with _STATE_LOCK:
        return _tick_locked(send, now, prefs, facts, busy)


def _tick_locked(send, now, prefs, facts, busy) -> list[dict]:
    state = load_state()
    day = _day(state, now.strftime("%Y-%m-%d"))
    if "meals_at_snapshot" not in day and now.hour * 60 + now.minute >= _mins(MEAL_SNAPSHOT_AT) \
            and "meals_today" in facts:
        day["meals_at_snapshot"] = int(facts.get("meals_today") or 0)
    due = evaluate(now, prefs, state, facts)
    sent: list[dict] = []
    if due and now.timestamp() - float(state.get("last_sent_ts") or 0) < GLOBAL_GAP_S:
        due = []  # spaced out: the rest wait their turn
    if due and prefs.get("hold_for_calendar", True) and (busy or calendar_busy)(now):
        due = []  # in a meeting — every due reminder simply waits for the next tick
    # One banner per tick, never a burst. Whichever window closes soonest goes
    # first, so the rest still have time left when their turn comes.
    due = sorted(due, key=lambda k: _mins(_BY_ID[k]["until"]))[:1]
    for kid in due:
        msg = build_message(kid, facts, prefs, now)
        try:
            send(msg)
        except Exception:
            continue
        _record_sent(state, now, msg)
        state["last_sent_ts"] = now.timestamp()
        sent.append(msg)
    save_state(state)
    return sent


def send_test(kind: str, send: Callable[[dict], None], now: datetime | None = None) -> dict:
    """Send one reminder right now regardless of its conditions (the panel's
    'Send test'). Logged as a test; never counts toward the daily limit."""
    now = now or datetime.now()
    msg = build_message(kind, gather_facts(now), load_prefs(), now)
    send(msg)
    with _STATE_LOCK:
        state = load_state()
        _record_sent(state, now, msg, test=True)
        save_state(state)
    return msg


def overview(now: datetime | None = None) -> dict:
    """Everything the Notifications panel shows, in one read."""
    now = now or datetime.now()
    prefs = load_prefs()
    state = load_state()
    facts = gather_facts(now)
    day = (state.get("days") or {}).get(now.strftime("%Y-%m-%d")) or {}
    kinds = []
    for k in CATALOG:
        kid = k["id"]
        active_key = {"plan": "plan_active", "water": "water_active", "meal_am": "meal_active",
                      "meal_pm": "meal_active", "weigh_in": "weight_active",
                      "finance_csv": "finance_active"}[kid]
        sent_today = (len(day.get("water_ts") or []) if kid == "water"
                      else (1 if (day.get("fired") or {}).get(kid) else 0))
        kinds.append({
            "id": kid, "label": k["label"], "desc": k["desc"], "group": k["group"],
            "on": prefs["kinds"][kid]["on"], "time": prefs["kinds"][kid]["time"],
            "default_time": k["time"], "until": k["until"],
            "tracking": bool(facts.get(active_key)), "sent_today": sent_today,
            "preview": build_message(kid, facts, prefs, now),
        })
    return {
        "prefs": prefs, "kinds": kinds, "weekdays": WEEKDAYS,
        "quiet_now": in_quiet_hours(now, prefs),
        "paused": float(prefs.get("paused_until") or 0) > now.timestamp(),
        "water_goal_set": bool(facts.get("water_target_ml")),
        "log": (state.get("log") or [])[:8],
    }


async def run_loop(send: Callable[[dict], None], log=None, interval: float = 60.0) -> None:
    """Background loop (server startup). Never raises; one bad tick is logged
    and the next minute tries again."""
    if os.environ.get("ADAM_NO_PUSH"):
        return  # test mode: never read the install's real trackers or send anything
    await asyncio.sleep(20)  # let startup finish before the first read
    while True:
        try:
            sent = await asyncio.to_thread(tick, send)
            if sent and log:
                log.info("reminders: sent %s", ", ".join(m["kind"] for m in sent))
        except Exception as e:  # noqa: BLE001
            if log:
                log.warning("reminders: tick failed: %s", e)
        await asyncio.sleep(interval)
