"""Where will the user be today? — the schedule picture the reminders plan around.

Three sources, merged into one list of time blocks for today:

  * "Your usual week" — recurring blocks the user sets in the Notifications
    panel (e.g. Work, Mon & Wed 1:30–9:30 PM). Always counts as OUT.
  * Google Calendar — today's events. An event counts as OUT when it has a
    location (that isn't home) or its title sounds like leaving the house
    (work, appointment, trip, gym, dinner at…). It counts as BUSY — every
    reminder waits it out — when it's a short out-of-the-house event or a
    call/meeting. A plain at-home task block ("Wash the car") is neither, so a
    planning-heavy calendar doesn't silence the reminders all day.
  * Adam's daily plan (06_calendar/latest_calendar_packet.md) — today's planned
    items with a time range, classified the same way, so something planned with
    Adam counts even before it's committed to the calendar.

A block is a dict: {start, end (minutes since midnight), label, out, busy,
source}. An all-day OUT event (vacation, trip) is a 0–1440 block.

Everything here is read-only and failure-safe: a source that can't be read
contributes nothing, so a calendar hiccup never silences reminders.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import config

# Words that mean "not at home". Matched as whole words / phrases, lowercase.
_OUT_WORDS = [
    "work", "shift", "office", "job", "trip", "travel", "flight", "airport",
    "drive to", "driving", "commute", "appointment", "appt", "dentist", "doctor",
    "clinic", "hospital", "church", "mass", "gym", "practice", "game", "school",
    "class", "store", "shopping", "errand", "errands", "groceries", "grocery",
    "costco", "walmart", "pick up", "pickup", "drop off", "haircut", "barber",
    "visit", "party", "wedding", "funeral", "dinner at", "lunch at",
    "breakfast at", "restaurant", "date night", "movie", "movies", "concert",
    "camping", "hike", "hiking", "vacation", "out of town", "meet at",
    "meeting at", "lake", "beach", "park", "tournament", "bank", "post office",
]
# Words that pin an item to the house even if an OUT word also appears
# ("work on the garage", "clean the yard").
_HOME_WORDS = ["home", "house", "yard", "garage", "laundry", "clean", "kitchen",
               "driveway", "work on", "homework"]
# Calls/meetings: the user is occupied wherever they are.
_BUSY_WORDS = ["call", "meeting", "interview", "zoom", "teams", "appointment",
               "appt", "dentist", "doctor", "church", "mass", "funeral", "wedding",
               "flight", "class", "game", "tournament"]

BUSY_MAX_MIN = 180      # an out-of-the-house event this short = busy (hold all)


def _has(text: str, words: list[str]) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text) for w in words)


def classify(title: str, location: str = "", minutes: int = 60) -> tuple[bool, bool]:
    """(out, busy) for one calendar/plan item."""
    t = (title or "").lower()
    loc = (location or "").strip().lower()
    out = (bool(loc) and "home" not in loc) or _has(t, _OUT_WORDS)
    if out and _has(t, _HOME_WORDS) and not loc:
        out = False
    busy = _has(t, _BUSY_WORDS) or (out and minutes <= BUSY_MAX_MIN)
    return out, busy


# --- Usual week (prefs) ----------------------------------------------------------

def _hm_min(s: str) -> int | None:
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, AttributeError):
        pass
    return None


def routine_blocks(routine: list[dict], weekday: int) -> list[dict]:
    out = []
    for r in routine or []:
        if weekday not in (r.get("days") or []):
            continue
        s, e = _hm_min(r.get("start")), _hm_min(r.get("end"))
        if s is None or e is None or e <= s:
            continue
        out.append({"start": s, "end": e, "label": str(r.get("label") or "Away")[:40],
                    "out": True, "busy": False, "source": "routine"})
    return out


# --- Google Calendar ---------------------------------------------------------------

_CAL_CACHE: dict = {"key": "", "ts": 0.0, "events": []}
_CAL_TTL_S = 600


def _calendar_events(now: datetime) -> list[dict]:
    try:
        import google_calendar
        if not google_calendar.is_configured():
            return []
        key = now.strftime("%Y-%m-%d")
        if _CAL_CACHE["key"] != key or time.monotonic() - _CAL_CACHE["ts"] > _CAL_TTL_S:
            loc = now.astimezone()
            start = loc.replace(hour=0, minute=0, second=0, microsecond=0)
            evs = google_calendar.list_events(
                start.isoformat(), (start + timedelta(days=1)).isoformat(), timeout=8)
            _CAL_CACHE.update(key=key, ts=time.monotonic(), events=list(evs or []))
        return list(_CAL_CACHE["events"])
    except Exception:
        return []


def calendar_blocks(events: list[dict], now: datetime) -> list[dict]:
    day0 = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for e in events or []:
        title = str(e.get("title") or "")
        if e.get("all_day"):
            o, _ = classify(title, str(e.get("location") or ""), 24 * 60)
            # Only a clearly-away all-day event counts (vacation, trip); an
            # all-day reminder note like "Fam meeting 8pm" doesn't empty the day.
            if o and _has(title.lower(), ["vacation", "trip", "travel", "out of town",
                                          "camping", "away"]):
                out.append({"start": 0, "end": 1440, "label": title[:40] or "Away",
                            "out": True, "busy": False, "source": "calendar"})
            continue
        try:
            s = datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00")).astimezone()
            en = datetime.fromisoformat(str(e.get("end")).replace("Z", "+00:00")).astimezone()
        except ValueError:
            continue
        sm = max(0, int((s - day0).total_seconds() // 60))
        em = min(1440, int((en - day0).total_seconds() // 60))
        if em <= 0 or sm >= 1440 or em <= sm:
            continue
        o, b = classify(title, str(e.get("location") or ""), em - sm)
        if not (o or b):
            continue
        out.append({"start": sm, "end": em, "label": title[:40] or "Event",
                    "out": o, "busy": b, "source": "calendar"})
    return out


# --- Adam's daily plan (the packet) -------------------------------------------------

_RANGE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?",
    re.I)


def _to_min(h: int, m: int, mer: str | None) -> int:
    if mer:
        h = h % 12 + (12 if mer.lower().startswith("p") else 0)
    return h * 60 + m


def parse_range(text: str) -> tuple[int, int, str] | None:
    """'2:30–2:45 PM — Front yard' -> (870, 885, 'Front yard'). Meridiem is
    shared ('2:30–4 PM'); a start that would land after its end flips to AM
    ('11–1 PM' = 11 AM–1 PM). Bare 24h ranges ('14:30-15:00') also parse."""
    m = _RANGE.search(text)
    if not m:
        return None
    h1, m1 = int(m.group(1)), int(m.group(2) or 0)
    h2, m2 = int(m.group(4)), int(m.group(5) or 0)
    mer1, mer2 = m.group(3), m.group(6)
    if not (mer1 or mer2) and not (m.group(2) or m.group(5)):
        return None  # "3-4" with no clock marks is too ambiguous (could be "3-4 sets")
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    end = _to_min(h2, m2, mer2 or mer1)
    start = _to_min(h1, m1, mer1 or mer2)
    if not mer1 and mer2 and start > end:
        start = _to_min(h1, m1, "am")
    if end <= start:
        return None
    rest = text[m.end():]
    rest = re.sub(r"^[\s*_|:—–-]+", "", rest)
    label = re.split(r"\s[|—–]\s|\.\s", rest.replace("**", "").strip())[0].strip()
    return start, end, label[:40]


def _today_section(text: str, now: datetime) -> str:
    """Today's part of the packet: the '##' section whose heading names today
    (weekday + 'Oct 4', 'October 4', '10/4' or the ISO date). If the packet has
    no per-day headings but is dated today, the whole packet."""
    iso = now.strftime("%Y-%m-%d")
    names = [f"{now:%b} {now.day}", f"{now:%B} {now.day}", f"{now.month}/{now.day}", iso]
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    for n, i in enumerate(heads):
        h = lines[i].lower()
        if any(x.lower() in h for x in names):
            j = heads[n + 1] if n + 1 < len(heads) else len(lines)
            return "\n".join(lines[i:j])
    if iso in text[:400]:
        return text
    return ""


def plan_blocks(text: str, now: datetime) -> list[dict]:
    out = []
    for ln in _today_section(text, now).splitlines():
        if not ln.lstrip().startswith(("-", "*")):
            continue
        r = parse_range(ln)
        if not r:
            continue
        s, e, label = r
        o, b = classify(label, "", e - s)
        if o or b:
            out.append({"start": s, "end": e, "label": label or "Planned",
                        "out": o, "busy": b, "source": "plan"})
    return out


def _packet_text() -> str:
    try:
        vp = str(config.VAULT_PATH or "").strip()
        p = Path(vp) / "06_calendar" / "latest_calendar_packet.md" if vp else None
        if p and p.is_file():
            return p.read_text("utf-8", errors="replace")[:60000]
    except Exception:
        pass
    return ""


# --- Merge ------------------------------------------------------------------------------

def gather(now: datetime, routine: list[dict]) -> list[dict]:
    """Today's blocks from every source, sorted by start. Calendar and plan items
    that duplicate each other (same start and end) collapse to one."""
    blocks = routine_blocks(routine, now.weekday())
    blocks += calendar_blocks(_calendar_events(now), now)
    blocks += plan_blocks(_packet_text(), now)
    seen, out = set(), []
    for b in sorted(blocks, key=lambda b: (b["start"], b["end"])):
        k = (b["start"], b["end"])
        if k in seen and b["source"] == "plan":
            continue
        seen.add(k)
        out.append(b)
    return out
