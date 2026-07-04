"""
Jarvis Voice Local — Twilio inbound-SMS connector (opt-in, off by default).

This is the *polling* transport for inbound texts. Instead of Twilio POSTing to a
public webhook (which would force a public HTTPS ingress and break the product's
private-by-default / Tailscale posture), the server reaches OUT to Twilio's REST
API on an interval and pulls new inbound messages to your Twilio number. No public
URL, no tunnel, no inbound exposure at all — just an authenticated outbound call.

Trust model (mirrors google_calendar.py):
  * BYO Twilio credentials. The Auth Token is a secret and lives in .env only
    (TWILIO_AUTH_TOKEN) — never settings.json, never logged, never in /health or
    any error string this module raises.
  * The Account SID and the Twilio number are not secret but also live in .env for
    one-place config (TWILIO_ACCOUNT_SID, TWILIO_NUMBER).

Capabilities, on purpose:
  * read  — list_inbound(), probe()   (safe; pulls inbound messages / verifies creds)
  * send  — NOT IMPLEMENTED. v1 is inbound-only (write_capable: False). There is
            deliberately no send_sms(); Jarvis replies via Web Push, never SMS.
  * delete — NOT IMPLEMENTED.

This module is transport + the poll loop's bookkeeping (cursor/dedup). It does not
decide vault policy: each new message body is handed to a caller-supplied callback
(the server's _run_sms_job), which routes it through the normal brain/approval flow.
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

import config

_API_ROOT = "https://api.twilio.com/2010-04-01"


class TwilioError(RuntimeError):
    """A Twilio API call failed. The message never contains the Auth Token."""


class TwilioNotConfigured(TwilioError):
    """The SMS connector is disabled or missing its Account SID / Auth Token / number."""


# --- configuration access (tolerant of config not yet wiring these in) ------

def _enabled() -> bool:
    return bool(getattr(config, "SMS_ENABLED", False))


def _account_sid() -> str:
    return str(getattr(config, "TWILIO_ACCOUNT_SID", "") or "").strip()


def _auth_token() -> str:
    return str(getattr(config, "TWILIO_AUTH_TOKEN", "") or "").strip()


def _number() -> str:
    return str(getattr(config, "TWILIO_NUMBER", "") or "").strip()


def _owner_phone() -> str:
    return str(getattr(config, "OWNER_PHONE", "") or "").strip()


def _poll_interval() -> int:
    # Floor at 5s so a misconfig can't hammer the API.
    return max(5, int(getattr(config, "SMS_POLL_INTERVAL_SECONDS", 15) or 15))


def _timeout() -> int:
    return int(getattr(config, "SMS_POLL_TIMEOUT_SECONDS", 15) or 15)


def _state_file():
    from pathlib import Path
    base = getattr(config, "STATE_DIR", None) or "."
    return Path(base) / "twilio_sms_cursor.json"


def is_configured() -> bool:
    """True only when enabled AND it has an Account SID, Auth Token, and number."""
    return bool(_enabled() and _account_sid() and _auth_token() and _number())


def safe_status() -> dict:
    """Non-secret status for /health and the setup wizard. Booleans only — never
    the SID, token, or number value."""
    return {
        "enabled": _enabled(),
        "account_sid_configured": bool(_account_sid()),
        "auth_token_configured": bool(_auth_token()),
        "number_configured": bool(_number()),
        "ready": is_configured(),
        "send_supported": False,
        "delete_supported": False,
    }


# --- transport --------------------------------------------------------------

def _basic_auth(account_sid: str, auth_token: str) -> str:
    raw = f"{account_sid}:{auth_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def list_inbound(account_sid: str, auth_token: str, to_number: str,
                 *, page_size: int = 20, timeout: int | None = None) -> list[dict]:
    """List recent INBOUND messages sent to `to_number`, newest first. Read-only.

    Calls the Twilio Messages list endpoint with HTTP Basic auth. The Auth Token
    rides only in the Authorization header — never the URL, a log line, or an
    exception message (Twilio's own error text carries no token). Returns a
    normalized list of {sid, from, to, body, direction, date_created(datetime)}.
    Raises TwilioError (token-free) on any failure."""
    account_sid = (account_sid or "").strip()
    auth_token = (auth_token or "").strip()
    to_number = (to_number or "").strip()
    if not account_sid or not auth_token:
        raise TwilioNotConfigured("Twilio Account SID and Auth Token are required.")
    if not to_number:
        raise TwilioError("a Twilio number (To) is required.")

    qs = urllib.parse.urlencode({"To": to_number, "PageSize": int(page_size)})
    url = f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}/Messages.json?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": _basic_auth(account_sid, auth_token),
                 "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=(timeout or _timeout())) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 401 = bad SID/token; others carry no token in reason.
        hint = " (check your Account SID and Auth Token)" if e.code in (401, 403) else ""
        raise TwilioError(f"Twilio API HTTP {e.code}: {e.reason}{hint}") from None
    except urllib.error.URLError as e:
        raise TwilioError(f"Twilio API unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        raise TwilioError(f"Twilio API request failed: {e}") from None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise TwilioError("Twilio API returned a non-JSON response.") from None
    if not isinstance(parsed, dict):
        raise TwilioError("Twilio API returned an unexpected response.")

    out: list[dict] = []
    for m in parsed.get("messages", []) or []:
        if not isinstance(m, dict):
            continue
        direction = str(m.get("direction", "") or "")
        if not direction.startswith("inbound"):
            continue
        out.append({
            "sid": str(m.get("sid", "") or ""),
            "from": str(m.get("from", "") or ""),
            "to": str(m.get("to", "") or ""),
            "body": (m.get("body") or ""),
            "direction": direction,
            "date_created": _parse_date(m.get("date_created")),
        })
    return out


def _parse_date(value) -> datetime:
    """Twilio dates are RFC-2822 ('Tue, 18 Aug 2015 17:30:00 +0000'). Fall back to
    epoch (UTC) so an unparseable date sorts oldest and is never treated as 'new'."""
    if value:
        try:
            dt = parsedate_to_datetime(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def probe(account_sid: str, auth_token: str, to_number: str,
          *, timeout: int | None = None) -> dict:
    """Verify credentials + number WITHOUT saving or enabling anything — the
    wizard's 'Test' green-check. Does one authenticated read (PageSize=1); a 200
    proves the Account SID + Auth Token are valid and the account is reachable.
    Returns booleans only; raises TwilioError (token-free message) on failure."""
    account_sid = (account_sid or "").strip()
    auth_token = (auth_token or "").strip()
    to_number = (to_number or "").strip()
    if not account_sid:
        raise TwilioError("Account SID is required.")
    if not auth_token:
        raise TwilioError("Auth Token is required.")
    if not to_number:
        raise TwilioError("Twilio number is required.")
    list_inbound(account_sid, auth_token, to_number, page_size=1, timeout=timeout)
    return {"ok": True, "number_configured": True}


# --- poll loop --------------------------------------------------------------

def _load_cursor() -> dict:
    try:
        data = json.loads(_state_file().read_text("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_cursor(last_iso: str, seen: list[str]) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bound the seen-set so the cursor file can't grow without limit.
        path.write_text(json.dumps({"last_iso": last_iso, "seen": seen[-200:]}),
                        encoding="utf-8")
    except Exception:
        pass


def _high_water(cursor: dict) -> datetime:
    raw = cursor.get("last_iso")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    # No cursor yet → start from "now" so we never replay the historical inbox.
    return datetime.now(timezone.utc)


async def poll_once(on_message: Callable[[str], Awaitable[None]], *, log=None) -> int:
    """One poll tick. Pulls inbound messages, filters to the owner + anything newer
    than the cursor and not already seen, and dispatches each (oldest first) to
    `on_message`. Advances and persists the cursor. Returns the number dispatched.
    Best-effort: a transport error is logged and swallowed (the loop retries next
    tick) so a flaky network never kills the poller."""
    cursor = _load_cursor()
    if "last_iso" not in cursor:
        # First run: stamp the high-water now and persist so a slow first tick
        # (or a crash) can't later replay old messages.
        hw = datetime.now(timezone.utc)
        _save_cursor(hw.isoformat(), [])
        cursor = {"last_iso": hw.isoformat(), "seen": []}

    hw = _high_water(cursor)
    seen = list(cursor.get("seen", []))
    seen_set = set(seen)
    owner = _owner_phone()

    try:
        msgs = await asyncio.to_thread(
            list_inbound, _account_sid(), _auth_token(), _number(),
            page_size=20, timeout=_timeout(),
        )
    except TwilioError as e:
        if log:
            log.warning("sms poll: %s", e)  # token-free by construction
        return 0

    fresh = [
        m for m in msgs
        if m["sid"] and m["sid"] not in seen_set
        and m["date_created"] > hw
        and (not owner or m["from"] == owner)
    ]
    fresh.sort(key=lambda m: m["date_created"])

    dispatched = 0
    for m in fresh:
        body = (m["body"] or "").strip()
        if body:
            try:
                await on_message(body)
                dispatched += 1
            except Exception:  # noqa: BLE001 — one bad message must not stall the loop
                if log:
                    log.exception("sms poll: on_message failed for %s", m["sid"])
        # Advance the cursor even for an empty/failed body so we never re-dispatch it.
        seen.append(m["sid"])
        if m["date_created"] > hw:
            hw = m["date_created"]

    if fresh:
        _save_cursor(hw.isoformat(), seen)
    return dispatched


async def run_poller(on_message: Callable[[str], Awaitable[None]], *,
                     log=None, stop_event: asyncio.Event | None = None) -> None:
    """Long-running inbound-SMS poll loop. Re-reads config each tick, so toggling
    the add-on off (or pulling creds) makes it go idle without a restart. Runs only
    while configured; sleeps the poll interval between ticks. Never raises — this is
    a fire-and-forget background task."""
    if log:
        log.info("sms poller started (interval=%ss)", _poll_interval())
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            if is_configured():
                await poll_once(on_message, log=log)
        except Exception:  # noqa: BLE001 — the loop must outlive any single failure
            if log:
                log.exception("sms poller tick crashed; continuing")
        try:
            await asyncio.sleep(_poll_interval())
        except asyncio.CancelledError:
            break
