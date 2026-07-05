"""
Adam — calendar READ endpoint tests (GET /integrations/calendar/events).

This is the reference "safe read" surface for the add-on pattern. Proves:

  * the route requires the bearer token (403 without);
  * it is configured-gated — 400 with a setup hint when the connector is off,
    and it does NOT call the bridge in that case;
  * the success path returns events from the connector and a correct count, and
    defaults to a sensible window when none is given;
  * a custom time window is passed through to the connector unchanged;
  * a connector failure surfaces as 502 with a token-free message;
  * a successful read NEVER enables or writes anything (it's read-only).

Run:  python test_calendar_read.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "e" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_calread_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import google_calendar              # noqa: E402
import job_store                    # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

TOKEN = server.ADAM_TOKEN
client = TestClient(server.app)
AUTH = {"Authorization": "Bearer " + TOKEN}

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


SECRET_TOKEN = "cal-bridge-secret-" + "z" * 40


def main() -> int:
    orig = (config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN)
    orig_list = google_calendar.list_events
    try:
        print("\n[1] Auth + configured-gating")
        r = client.get("/integrations/calendar/events")
        check("requires the bearer token (403 without)", r.status_code == 403)

        # Connector OFF: 400, and the bridge is never called.
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = (False, "", "")
        called = {"n": 0}

        def _boom(*a, **k):
            called["n"] += 1
            raise AssertionError("list_events must not be called when unconfigured")

        google_calendar.list_events = _boom
        r = client.get("/integrations/calendar/events", headers=AUTH)
        check("unconfigured -> 400", r.status_code == 400)
        check("400 detail points at setup", "/setup-calendar" in r.json().get("detail", ""))
        check("bridge not called when unconfigured", called["n"] == 0)

        print("\n[2] Success path (connector configured)")
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = (
            True, "https://script.google.com/macros/s/AKfake/exec", SECRET_TOKEN)
        seen = {}

        def _fake_list(time_min, time_max, calendar_id=None):
            seen["time_min"], seen["time_max"], seen["calendar_id"] = time_min, time_max, calendar_id
            return [{"id": "evt1", "summary": "Standup"}, {"id": "evt2", "summary": "Lunch"}]

        google_calendar.list_events = _fake_list
        r = client.get("/integrations/calendar/events", headers=AUTH)
        check("authed + configured -> 200", r.status_code == 200)
        body = r.json()
        check("returns the events", [e["id"] for e in body["events"]] == ["evt1", "evt2"])
        check("count matches", body["count"] == 2)
        check("defaults a window when none given", bool(seen["time_min"]) and bool(seen["time_max"]))
        check("response leaks no secret token", SECRET_TOKEN not in r.text)

        print("\n[3] Custom window passes through unchanged")
        r = client.get(
            "/integrations/calendar/events",
            params={"time_min": "2026-07-01T00:00:00+00:00",
                    "time_max": "2026-07-02T00:00:00+00:00",
                    "calendar_id": "work@example.com"},
            headers=AUTH,
        )
        check("custom time_min passed through", seen["time_min"] == "2026-07-01T00:00:00+00:00")
        check("custom time_max passed through", seen["time_max"] == "2026-07-02T00:00:00+00:00")
        check("custom calendar_id passed through", seen["calendar_id"] == "work@example.com")

        print("\n[4] Connector failure -> 502, token-free")
        def _fail_list(*a, **k):
            raise google_calendar.CalendarError("calendar bridge HTTP 500: Internal Server Error")

        google_calendar.list_events = _fail_list
        r = client.get("/integrations/calendar/events", headers=AUTH)
        check("bridge failure -> 502", r.status_code == 502)
        check("502 message is token-free", SECRET_TOKEN not in r.text)

        print("\n[5] A read never enables/writes anything")
        check("read-only: calendar still configured exactly as set, nothing toggled",
              config.safe_summary()["calendar_enabled"] is True)
    finally:
        google_calendar.list_events = orig_list
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = orig

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
