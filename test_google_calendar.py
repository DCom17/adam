"""
Jarvis Voice Local — Google Calendar connector tests.

Self-contained (no network, no Google): monkeypatches config flags and the
connector's transport to prove:

    opt-in default OFF · is_configured gating · token sent in body only and
    NEVER in safe_status/logs · reads call the right actions · writes call the
    right actions · NO delete capability exists · bridge errors surface as
    CalendarError · disabled connector refuses with CalendarNotConfigured.

Run:  python test_google_calendar.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import io
import json
import sys

import config
import google_calendar as gc

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


class _FakeResponse(io.BytesIO):
    """Minimal context-manager stand-in for urlopen()'s return value."""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        self.close()
        return False


def main() -> int:
    TOKEN = "secrettoken_should_never_leak_1234567890"

    # --- [1] opt-in default OFF -------------------------------------------
    print("\n[1] Opt-in: disabled by default")
    config.CALENDAR_ENABLED = False
    config.CALENDAR_BRIDGE_URL = ""
    config.CALENDAR_TOKEN = ""
    config.CALENDAR_DEFAULT_ID = "primary"
    check("is_configured() False when disabled/unset", gc.is_configured() is False)
    check("safe_status ready False when disabled", gc.safe_status()["ready"] is False)
    check("delete never advertised as supported", gc.safe_status()["delete_supported"] is False)

    print("\n[2] Disabled connector refuses calls (fail-closed)")
    try:
        gc.list_events("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        check("disabled list raises", False)
    except gc.CalendarNotConfigured:
        check("disabled list raises CalendarNotConfigured", True)

    # --- enable with a URL + token ----------------------------------------
    config.CALENDAR_ENABLED = True
    config.CALENDAR_BRIDGE_URL = "https://script.google.com/macros/s/EXAMPLE/exec"
    config.CALENDAR_TOKEN = TOKEN

    print("\n[3] Configured state")
    check("is_configured() True with enabled+url+token", gc.is_configured() is True)
    check("safe_status ready True when configured", gc.safe_status()["ready"] is True)

    print("\n[4] safe_status NEVER exposes the token or URL contents")
    blob = json.dumps(gc.safe_status())
    check("token absent from safe_status", TOKEN not in blob)
    check("url string absent from safe_status", "script.google.com" not in blob)
    check("safe_status reports booleans only",
          gc.safe_status()["bridge_configured"] is True and gc.safe_status()["token_configured"] is True)

    # --- capture what the transport sends, without hitting the network ----
    sent: list[dict] = []

    def fake_urlopen(req, timeout=None):
        # req is a urllib.request.Request; capture its body + url.
        body = json.loads(req.data.decode("utf-8"))
        sent.append({"url": req.full_url, "body": body})
        action = body.get("action")
        # Build a plausible ok response per action.
        if action == "list":
            payload = {"ok": True, "action": "list",
                       "result": {"calendar_id": body.get("calendar_id"), "count": 1,
                                  "events": [{"event_id": "evt1", "title": "Standup",
                                              "start": "2026-06-26T16:00:00Z",
                                              "end": "2026-06-26T16:15:00Z"}]}}
        elif action == "get":
            payload = {"ok": True, "action": "get",
                       "result": {"event": {"event_id": body.get("event_id"), "title": "Standup"}}}
        elif action == "create":
            payload = {"ok": True, "action": "create",
                       "result": {"created_count": len(body.get("events", [])), "created": [{"event_id": "new1"}]}}
        elif action == "update":
            payload = {"ok": True, "action": "update",
                       "result": {"updated": True, "event": {"event_id": body.get("event_id")}}}
        else:
            payload = {"ok": False, "error": "unknown action"}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    import urllib.request as _u
    _real = _u.urlopen
    _u.urlopen = fake_urlopen
    try:
        print("\n[5] Reads call the right actions and carry the token in the body")
        evts = gc.list_events("2026-06-26T00:00:00Z", "2026-06-27T00:00:00Z")
        check("list returns events", len(evts) == 1 and evts[0]["event_id"] == "evt1")
        check("list used action=list", sent[-1]["body"]["action"] == "list")
        check("token sent in POST body", sent[-1]["body"]["token"] == TOKEN)
        check("token NOT in the request URL", TOKEN not in sent[-1]["url"])

        one = gc.get_event("evt1")
        check("get returns the event", one["event_id"] == "evt1")
        check("get used action=get", sent[-1]["body"]["action"] == "get")

        print("\n[6] test_connection lists a tiny window, never creates")
        tc = gc.test_connection()
        check("test_connection ok", tc["ok"] is True)
        check("test_connection used a read (list), not a write", sent[-1]["body"]["action"] == "list")

        print("\n[7] Writes call the right actions")
        cr = gc.create_events([{"title": "X", "start": "2026-06-26T18:00:00Z", "end": "2026-06-26T18:30:00Z"}])
        check("create returns result", cr.get("created_count") == 1)
        check("create used action=create", sent[-1]["body"]["action"] == "create")
        up = gc.update_event("evt1", {"title": "Renamed"})
        check("update returns updated", up.get("updated") is True)
        check("update used action=update", sent[-1]["body"]["action"] == "update")
        check("update never sends a delete action",
              all(s["body"].get("action") != "delete" for s in sent))

        print("\n[8] Bridge error (ok:false) surfaces as CalendarError")
        def err_urlopen(req, timeout=None):
            return _FakeResponse(json.dumps({"ok": False, "error": "Unauthorized: invalid token."}).encode())
        _u.urlopen = err_urlopen
        try:
            gc.list_events("2026-06-26T00:00:00Z", "2026-06-27T00:00:00Z")
            check("bridge ok:false raises", False)
        except gc.CalendarError as e:
            check("bridge ok:false raises CalendarError", True)
            check("error message has no token", TOKEN not in str(e))
    finally:
        _u.urlopen = _real

    print("\n[9] No delete capability exists on the module")
    check("connector has no delete_event function", not hasattr(gc, "delete_event"))
    check("connector source defines no delete action",
          'action="delete"' not in open(gc.__file__, encoding="utf-8").read())

    print("\n[10] Doctor calendar check is advisory (PASS off / configured, WARN unfinished, never FAIL)")
    import types
    import onboarding
    off = onboarding._calendar_check(types.SimpleNamespace(
        CALENDAR_ENABLED=False, CALENDAR_BRIDGE_URL="", CALENDAR_TOKEN=""))
    check("disabled connector -> PASS", off["status"] == "PASS")
    ready = onboarding._calendar_check(types.SimpleNamespace(
        CALENDAR_ENABLED=True, CALENDAR_BRIDGE_URL="https://x/exec", CALENDAR_TOKEN="t"))
    check("enabled+configured -> PASS", ready["status"] == "PASS")
    no_token = onboarding._calendar_check(types.SimpleNamespace(
        CALENDAR_ENABLED=True, CALENDAR_BRIDGE_URL="https://x/exec", CALENDAR_TOKEN=""))
    check("enabled but missing token -> WARN (not FAIL)", no_token["status"] == "WARN")
    no_url = onboarding._calendar_check(types.SimpleNamespace(
        CALENDAR_ENABLED=True, CALENDAR_BRIDGE_URL="", CALENDAR_TOKEN="t"))
    check("enabled but missing url -> WARN (not FAIL)", no_url["status"] == "WARN")
    check("calendar check NEVER returns FAIL",
          all(c["status"] != "FAIL" for c in (off, ready, no_token, no_url)))

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
