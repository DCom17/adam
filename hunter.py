"""
Adam — Hunter Tracker connector (opt-in, off by default).

This is the server-side client for the user's OWN Hunter dashboard: a Google
Sheet they copied from a template, with a bound Apps Script that (a) accepts a
sheet_sync payload over POST to update the board, and (b) exposes a read-only
verify endpoint over GET (hunter_verify.gs) so a sync can be confirmed. It is a
thin, dependency-free HTTP client over the stdlib `urllib` — no Google SDK, no
OAuth client, no service account.

Trust model (mirrors the Calendar/Twilio secret pattern):
  * The Sheet + its bound script live in the USER's own Google account. We never
    hold a Google credential — only the random bridge token the user generated,
    which lives in .env as HUNTER_TOKEN and is NEVER logged or returned in
    /health. It is sent only to the user's own bridge URL.

A note on the read path: the verify endpoint is a doGet, so the token rides as a
GET query parameter (this mirrors the dashboard's verify_dashboard.ps1). The
connector never logs the URL, and error messages are scrubbed of the token; the
write path (sync) carries the token in the POST body, never the URL.

Capabilities, on purpose:
  * read  — verify()            (safe; reads the board back through the verify
            endpoint to confirm a sync landed)
  * write — sync(payload)       (MUST be gated by the approval/confirmation flow
            at the call site; this module is only the transport and does NOT
            itself decide policy. The payload passes through UNCHANGED — the
            canonical daily_quests keys are never remapped, because renaming
            them silently blanks the board.)
  * delete — NOT IMPLEMENTED. There is deliberately no delete function and the
            verify endpoint has no delete handler, so the Hunter board cannot be
            cleared through this product by construction.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import config


class HunterError(RuntimeError):
    """A Hunter bridge call failed. The message never contains the token."""


class HunterNotConfigured(HunterError):
    """The Hunter connector is disabled or missing its URL/token."""


# --- configuration access (tolerant of config not yet wiring these in) ------

def _enabled() -> bool:
    return bool(getattr(config, "HUNTER_ENABLED", False))


def _bridge_url() -> str:
    return str(getattr(config, "HUNTER_BRIDGE_URL", "") or "").strip()


def _token() -> str:
    return str(getattr(config, "HUNTER_TOKEN", "") or "").strip()


def _timeout() -> int:
    return int(getattr(config, "HUNTER_TIMEOUT_SECONDS", 20))


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


def _scrub(msg: str, token: str) -> str:
    """Defense in depth: never let the token ride out in an error string."""
    return msg.replace(token, "***") if token and token in msg else msg


# --- transport --------------------------------------------------------------

def _parse(raw: str) -> dict:
    """Parse a bridge response, raising HunterError (token-free) on any non-ok."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HunterError("hunter bridge returned a non-JSON response.") from None
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        msg = (parsed.get("error") if isinstance(parsed, dict) else None) or "hunter bridge error"
        raise HunterError(str(msg))
    return parsed


def _request(bridge_url: str, token: str, action: str, timeout: int | None = None) -> dict:
    """Low-level GET of a READ action (verify) to an EXPLICIT bridge url + token.
    The token rides as a query parameter to the user's own Apps Script doGet
    (mirrors verify_dashboard.ps1). It is never logged; any exception message is
    scrubbed of the token. Used by both the config-based verify() and the
    wizard's probe_bridge()."""
    if not bridge_url or not token:
        raise HunterNotConfigured("hunter bridge url and token are required.")
    query = urllib.parse.urlencode({"token": token, "action": action})
    url = bridge_url + ("&" if "?" in bridge_url else "?") + query
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=(timeout or _timeout())) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # e.code / e.reason carry no token.
        raise HunterError(f"hunter bridge HTTP {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise HunterError(f"hunter bridge unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        # malformed URL / socket timeout — could echo the url, so scrub the token.
        raise HunterError(f"hunter bridge request failed: {_scrub(str(e), token)}") from None
    return _parse(raw)


def _post(bridge_url: str, token: str, payload: dict[str, Any], timeout: int | None = None) -> dict:
    """Low-level POST of a sheet_sync payload to an EXPLICIT bridge url + token.
    Mirrors sync_dashboard.ps1: `token` + `source` are added to the JSON body; the
    payload's own keys (state / stats / daily_quests / ...) PASS THROUGH UNCHANGED
    — the canonical quest keys are never remapped. The token is in the body only,
    never the URL or a log line."""
    if not bridge_url or not token:
        raise HunterNotConfigured("hunter bridge url and token are required.")
    body = dict(payload or {})
    body["token"] = token
    body["source"] = "adam-local"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        bridge_url, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=(timeout or _timeout())) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise HunterError(f"hunter bridge HTTP {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise HunterError(f"hunter bridge unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        raise HunterError(f"hunter bridge request failed: {_scrub(str(e), token)}") from None
    return _parse(raw)


def probe_bridge(bridge_url: str, token: str, timeout: int | None = None) -> dict:
    """Validate an arbitrary bridge url + token WITHOUT saving anything or touching
    global config — the wizard's 'Test connection' green-check. Calls the
    read-only `verify` action so success proves the deployment is live and the
    token is accepted. Returns booleans/counts only; raises HunterError (token-free
    message) on failure."""
    bridge_url = (bridge_url or "").strip()
    token = (token or "").strip()
    if not bridge_url:
        raise HunterError("bridge_url is required.")
    if not token:
        raise HunterError("token is required.")
    res = _request(bridge_url, token, "verify", timeout=timeout)
    result = res.get("result", {}) or {}
    return {
        "ok": True,
        "quest_count": len(result.get("daily_quests", []) or []),
        "stat_count": len(result.get("stats", []) or []),
    }


# --- read (safe; no approval needed) ----------------------------------------

def verify() -> dict:
    """Read the live board back through the CONFIGURED bridge's verify endpoint.
    Read-only — used to confirm a sync landed. Returns the bridge's result dict
    (daily_quests, stats, timestamp)."""
    if not is_configured():
        raise HunterNotConfigured(
            "Hunter connector is not configured "
            "(set integrations.hunter.enabled + bridge_url and HUNTER_TOKEN)."
        )
    res = _request(_bridge_url(), _token(), "verify")
    return res.get("result", {}) or {}


def board() -> dict:
    """Read the full live board for the in-app Hunter dashboard view. Same
    read-only verify call as verify(); returns the bridge's result dict
    (state, stats, daily_quests, bosses, weaknesses, rank_rules, timestamp).
    Read-only — never writes."""
    return verify()


# --- write (MUST be gated by approval / confirmation at the call site) -------

def sync(payload: dict) -> dict:
    """Push a sheet_sync payload to the user's Hunter dashboard. WRITE: the caller
    is responsible for routing this through the approval flow — this function is
    only the transport. The payload is passed through WITHOUT remapping quest keys
    (renaming them silently blanks the board). There is intentionally no delete."""
    if not is_configured():
        raise HunterNotConfigured(
            "Hunter connector is not configured "
            "(set integrations.hunter.enabled + bridge_url and HUNTER_TOKEN)."
        )
    if not isinstance(payload, dict) or not payload:
        raise HunterError("sync requires a non-empty payload dict.")
    res = _post(_bridge_url(), _token(), payload)
    return res.get("result", {}) or res

# NOTE: there is intentionally no delete()/clear(). Deletion is unsupported by
# design — both here and in the verify endpoint — so it cannot happen through Adam.
