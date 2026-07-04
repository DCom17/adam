"""
Jarvis Voice Local — Google Calendar connector (opt-in, off by default).

This is the server-side client for the user's own Google Apps Script calendar
bridge (calendar_bridge.gs). It is a thin, dependency-free HTTP client over the
stdlib `urllib` — no Google SDK, no OAuth client, no service account.

Trust model (mirrors the Twilio/secret pattern):
  * The bridge runs in the USER's Google account. We never hold a Google
    credential — only the random bridge token the user generated, which lives in
    .env as GOOGLE_CALENDAR_TOKEN and is NEVER logged or returned in /health.
  * The token is sent only inside the POST body to the user's own bridge URL.

Capabilities, on purpose:
  * read  — list_events(), get_event()        (safe; the server may call these
            directly, with or without Claude in the loop)
  * write — create_events(), update_event()   (these MUST be gated by the
            approval / confirmation flow at the call site; this module is just
            the transport and does NOT itself decide policy)
  * delete — NOT IMPLEMENTED. There is deliberately no delete function and the
            bridge has no delete handler, so calendar deletion is impossible
            through this product by construction.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import config


class CalendarError(RuntimeError):
    """A calendar bridge call failed. The message never contains the token."""


class CalendarNotConfigured(CalendarError):
    """The calendar connector is disabled or missing its URL/token."""


# --- configuration access (tolerant of config not yet wiring these in) ------

def _enabled() -> bool:
    return bool(getattr(config, "CALENDAR_ENABLED", False))


def _bridge_url() -> str:
    return str(getattr(config, "CALENDAR_BRIDGE_URL", "") or "").strip()


def _token() -> str:
    return str(getattr(config, "CALENDAR_TOKEN", "") or "").strip()


def _default_calendar_id() -> str:
    return str(getattr(config, "CALENDAR_DEFAULT_ID", "primary") or "primary").strip()


def _timeout() -> int:
    return int(getattr(config, "CALENDAR_TIMEOUT_SECONDS", 20))


def is_configured() -> bool:
    """True only when the connector is enabled AND has both a URL and a token."""
    return bool(_enabled() and _bridge_url() and _token())


def safe_status() -> dict:
    """Non-secret status for /health and the setup wizard. Booleans only — never
    the URL contents or the token value."""
    return {
        "enabled": _enabled(),
        "bridge_configured": bool(_bridge_url()),
        "token_configured": bool(_token()),
        "ready": is_configured(),
        "delete_supported": False,
    }


# --- transport --------------------------------------------------------------

def _request(bridge_url: str, token: str, action: str,
             payload: dict[str, Any] | None = None, timeout: int | None = None) -> dict:
    """Low-level POST of one action to an EXPLICIT bridge url + token. Raises
    CalendarError on any failure. The token is placed in the body only — never in
    the URL, a log line, or an exception message (bridge error text is the bridge's
    own and contains no token). Used by both the config-based _post() and the
    wizard's probe_bridge()."""
    if not bridge_url or not token:
        raise CalendarNotConfigured("calendar bridge url and token are required.")
    body = dict(payload or {})
    body["action"] = action
    body["token"] = token
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        bridge_url, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=(timeout or _timeout())) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # e.reason / status carry no token; do not echo the request body.
        raise CalendarError(f"calendar bridge HTTP {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise CalendarError(f"calendar bridge unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        # malformed URL, socket timeout, etc. — surface without the token.
        raise CalendarError(f"calendar bridge request failed: {e}") from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise CalendarError("calendar bridge returned a non-JSON response.") from None
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        msg = (parsed.get("error") if isinstance(parsed, dict) else None) or "calendar bridge error"
        raise CalendarError(str(msg))
    return parsed


def _post(action: str, payload: dict[str, Any] | None = None) -> dict:
    """POST one action using the CONFIGURED bridge url + token. Requires the
    connector to be enabled and configured."""
    if not is_configured():
        raise CalendarNotConfigured(
            "Google Calendar connector is not configured "
            "(set integrations.google_calendar.enabled + bridge_url and GOOGLE_CALENDAR_TOKEN)."
        )
    return _request(_bridge_url(), _token(), action, payload)


def probe_bridge(bridge_url: str, token: str, calendar_id: str | None = None,
                 timeout: int | None = None) -> dict:
    """Validate an arbitrary bridge url + token WITHOUT saving anything or
    touching global config — the wizard's 'Test connection' green-check. Lists a
    1-minute window (a read; never creates) so success proves the deployment is
    live and the token is accepted. Returns booleans/counts only; raises
    CalendarError (token-free message) on failure."""
    from datetime import datetime, timedelta, timezone
    bridge_url = (bridge_url or "").strip()
    token = (token or "").strip()
    cal = (calendar_id or _default_calendar_id() or "primary").strip()
    if not bridge_url:
        raise CalendarError("bridge_url is required.")
    if not token:
        raise CalendarError("token is required.")
    now = datetime.now(timezone.utc)
    res = _request(bridge_url, token, "list", {
        "time_min": now.isoformat(),
        "time_max": (now + timedelta(minutes=1)).isoformat(),
        "calendar_id": cal,
    }, timeout=timeout)
    result = res.get("result", {}) or {}
    return {"ok": True, "calendar_id": result.get("calendar_id", cal)}


# --- reads (safe; no approval needed) ---------------------------------------

def test_connection() -> dict:
    """Prove the bridge URL AND token both work, without creating anything.
    Lists a 1-minute window (almost always empty) so a green result means the
    deployment is live and the token is accepted. Returns booleans/counts only."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    res = _post("list", {
        "time_min": now.isoformat(),
        "time_max": (now + timedelta(minutes=1)).isoformat(),
        "calendar_id": _default_calendar_id(),
    })
    result = res.get("result", {}) or {}
    return {"ok": True, "calendar_id": result.get("calendar_id", _default_calendar_id())}


def list_events(time_min: str, time_max: str, calendar_id: str | None = None) -> list[dict]:
    """Events between two ISO datetimes. Read-only."""
    res = _post("list", {
        "time_min": time_min, "time_max": time_max,
        "calendar_id": (calendar_id or _default_calendar_id()),
    })
    return list((res.get("result", {}) or {}).get("events", []))


def get_event(event_id: str, calendar_id: str | None = None) -> dict:
    """A single event by id. Read-only — used to show the 'before' state when
    proposing an edit."""
    res = _post("get", {
        "event_id": event_id,
        "calendar_id": (calendar_id or _default_calendar_id()),
    })
    return (res.get("result", {}) or {}).get("event", {})


# --- writes (MUST be gated by approval / confirmation at the call site) ------

def create_events(events: list[dict]) -> dict:
    """Create one or more events. WRITE: the caller is responsible for routing
    this through the approval flow — this function is only the transport."""
    if not events:
        raise CalendarError("create_events requires a non-empty events list.")
    res = _post("create", {"events": events})
    return res.get("result", {}) or {}


def update_event(event_id: str, changes: dict, calendar_id: str | None = None) -> dict:
    """Edit an existing event's title/time/location/description. WRITE: the
    caller MUST obtain explicit confirmation before calling this. Cannot delete."""
    if not event_id:
        raise CalendarError("update_event requires an event_id.")
    if not changes:
        raise CalendarError("update_event requires a non-empty changes dict.")
    res = _post("update", {
        "event_id": event_id, "changes": changes,
        "calendar_id": (calendar_id or _default_calendar_id()),
    })
    return res.get("result", {}) or {}

# NOTE: there is intentionally no delete_event(). Deletion is unsupported by
# design — both here and in the bridge — so it cannot happen through Jarvis.
