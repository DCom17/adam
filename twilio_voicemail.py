"""
Jarvis Voice Local — Twilio AI-voicemail connector (opt-in, off by default).

When a call to the user goes unanswered, their carrier forwards it to a Twilio
number. Twilio answers it *itself* (a greeting + <Record>, hosted on Twilio — see
voicemail_provision.py), stores the recording, and hangs up. THIS module is the
*polling* transport that pulls those recordings back: instead of Twilio POSTing to
a public webhook (which would force a public HTTPS ingress and break the product's
private-by-default / Tailscale posture), the server reaches OUT to Twilio's REST
API on an interval and pulls new recordings to the user's number. No public URL,
no tunnel, no inbound exposure — just an authenticated outbound call. This is the
exact transport the inbound-SMS add-on uses (twilio_sms.py), for the same reason.

Trust model (mirrors twilio_sms.py):
  * BYO Twilio credentials. The Auth Token is a secret and lives in .env only
    (TWILIO_AUTH_TOKEN) — never settings.json, never logged, never in /health or
    any error string this module raises.
  * The Account SID and the Twilio number are not secret but also live in .env for
    one-place config (TWILIO_ACCOUNT_SID, TWILIO_NUMBER). Shared with the SMS
    add-on: one Twilio number can serve both Voice and Messaging.

Capabilities, on purpose:
  * read  — list_recordings(), recording_transcript(), call_from(), probe()
            (safe; pulls recordings / transcripts / verifies creds)
  * download — recording_media() (for the optional local-Whisper transcription path)
  * send  — NONE. The connector never originates a call or a text.
  * delete — NONE. Recordings are left on Twilio; the app never deletes them.

This module is transport + the poll loop's bookkeeping (a seen-set + a first-run
stamp for dedup). It does not decide vault policy: each finalized voicemail is
handed to a caller-supplied callback (the server's _run_voicemail_job), which
builds the message record and pushes it to the phone.
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

# A recording whose transcription still isn't ready after this long is delivered
# WITHOUT a transcript (a "you have a voicemail, couldn't transcribe it" notice)
# rather than waiting forever. Twilio transcription is async and usually lands in
# under a minute; this is the give-up bound.
_MAX_TRANSCRIBE_WAIT_SECONDS = 240


class TwilioError(RuntimeError):
    """A Twilio API call failed. The message never contains the Auth Token."""


class TwilioNotConfigured(TwilioError):
    """The voicemail connector is disabled or missing its Account SID / Auth Token / number."""


# --- configuration access (tolerant of config not yet wiring these in) ------

def _enabled() -> bool:
    return bool(getattr(config, "VOICEMAIL_ENABLED", False))


def _account_sid() -> str:
    return str(getattr(config, "TWILIO_ACCOUNT_SID", "") or "").strip()


def _auth_token() -> str:
    return str(getattr(config, "TWILIO_AUTH_TOKEN", "") or "").strip()


def _number() -> str:
    return str(getattr(config, "TWILIO_NUMBER", "") or "").strip()


def _poll_interval() -> int:
    # Floor at 5s so a misconfig can't hammer the API.
    return max(5, int(getattr(config, "VOICEMAIL_POLL_INTERVAL_SECONDS", 20) or 20))


def _timeout() -> int:
    return int(getattr(config, "VOICEMAIL_POLL_TIMEOUT_SECONDS", 20) or 20)


def _transcribe_mode() -> str:
    """'twilio' (default; Twilio's built-in transcription) or 'whisper' (local
    faster-whisper on the downloaded audio, for users who have it installed)."""
    return str(getattr(config, "VOICEMAIL_TRANSCRIBE_MODE", "twilio") or "twilio").strip().lower()


def _state_file():
    from pathlib import Path
    base = getattr(config, "STATE_DIR", None) or "."
    return Path(base) / "twilio_voicemail_cursor.json"


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
        "transcribe_mode": _transcribe_mode(),
        "send_supported": False,
        "delete_supported": False,
    }


# --- transport --------------------------------------------------------------

def _basic_auth(account_sid: str, auth_token: str) -> str:
    raw = f"{account_sid}:{auth_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _get_json(url: str, account_sid: str, auth_token: str, *, timeout: int | None = None) -> dict:
    """Authenticated GET returning parsed JSON. The Auth Token rides only in the
    Authorization header — never the URL, a log line, or an exception message.
    Raises TwilioError (token-free) on any failure."""
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
    return parsed


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


def list_recordings(account_sid: str, auth_token: str, *, page_size: int = 20,
                    timeout: int | None = None) -> list[dict]:
    """List recent recordings on the account, newest first. Read-only.

    Twilio's Recordings list is account-wide (not per-number), so callers filter to
    the voicemail box by other means (the answer flow only records calls to the
    voicemail number). Returns normalized dicts:
    {sid, call_sid, date_created(datetime), duration(int)}.
    Raises TwilioError (token-free) on failure."""
    account_sid = (account_sid or "").strip()
    auth_token = (auth_token or "").strip()
    if not account_sid or not auth_token:
        raise TwilioNotConfigured("Twilio Account SID and Auth Token are required.")

    qs = urllib.parse.urlencode({"PageSize": int(page_size)})
    url = f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}/Recordings.json?{qs}"
    parsed = _get_json(url, account_sid, auth_token, timeout=timeout)

    out: list[dict] = []
    for r in parsed.get("recordings", []) or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "sid": str(r.get("sid", "") or ""),
            "call_sid": str(r.get("call_sid", "") or ""),
            "date_created": _parse_date(r.get("date_created")),
            "duration": int(str(r.get("duration", "0") or "0") or 0),
        })
    return out


def recording_transcript(account_sid: str, auth_token: str, recording_sid: str,
                         *, timeout: int | None = None) -> dict:
    """Fetch the transcription for one recording. Returns
    {status, text}: status is 'completed' | 'in-progress' | 'failed' | 'none'
    (none = Twilio has not created a transcription resource yet). text is '' unless
    completed. Read-only; raises TwilioError (token-free) on transport failure."""
    account_sid = (account_sid or "").strip()
    recording_sid = (recording_sid or "").strip()
    if not recording_sid:
        return {"status": "none", "text": ""}
    url = (f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}"
           f"/Recordings/{urllib.parse.quote(recording_sid)}/Transcriptions.json")
    parsed = _get_json(url, account_sid, auth_token, timeout=timeout)
    items = parsed.get("transcriptions", []) or []
    if not items or not isinstance(items[0], dict):
        return {"status": "none", "text": ""}
    t = items[0]
    status = str(t.get("status", "") or "").strip().lower() or "none"
    text = (t.get("transcription_text") or "") if status == "completed" else ""
    return {"status": status, "text": text.strip()}


def call_from(account_sid: str, auth_token: str, call_sid: str,
              *, timeout: int | None = None) -> str:
    """The caller's number (the call's 'From') for a recording's call, or '' if it
    can't be resolved. Read-only; never raises on a missing call — returns ''."""
    call_sid = (call_sid or "").strip()
    if not call_sid:
        return ""
    url = (f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}"
           f"/Calls/{urllib.parse.quote(call_sid)}.json")
    try:
        parsed = _get_json(url, account_sid, auth_token, timeout=timeout)
    except TwilioError:
        return ""
    return str(parsed.get("from", "") or "").strip()


def recording_media(account_sid: str, auth_token: str, recording_sid: str,
                    *, timeout: int | None = None) -> bytes | None:
    """Download the recording audio (WAV) for the local-Whisper path. Returns the
    bytes, or None on any failure (the caller falls back to a no-transcript notice).
    The Auth Token rides only in the Authorization header."""
    account_sid = (account_sid or "").strip()
    recording_sid = (recording_sid or "").strip()
    if not recording_sid:
        return None
    url = (f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}"
           f"/Recordings/{urllib.parse.quote(recording_sid)}.wav")
    req = urllib.request.Request(
        url, headers={"Authorization": _basic_auth(account_sid, auth_token)}, method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=(timeout or _timeout())) as resp:
            data = resp.read()
            return data or None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def incoming_number_sid(account_sid: str, auth_token: str, number: str,
                        *, timeout: int | None = None) -> str:
    """The IncomingPhoneNumber SID (PN…) for `number` on this account, or '' if the
    number isn't owned by the account. Used by provisioning to set the Voice URL,
    and by probe() to confirm the user really owns the number they typed."""
    account_sid = (account_sid or "").strip()
    number = (number or "").strip()
    if not number:
        return ""
    qs = urllib.parse.urlencode({"PhoneNumber": number})
    url = (f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}"
           f"/IncomingPhoneNumbers.json?{qs}")
    parsed = _get_json(url, account_sid, auth_token, timeout=timeout)
    for n in parsed.get("incoming_phone_numbers", []) or []:
        if isinstance(n, dict) and n.get("sid"):
            return str(n["sid"]).strip()
    return ""


def probe(account_sid: str, auth_token: str, number: str,
          *, timeout: int | None = None) -> dict:
    """Verify credentials + that the account owns the number, WITHOUT saving or
    enabling anything — the wizard's 'Test' green-check. One authenticated read of
    the account's numbers; a 200 proves the Account SID + Auth Token are valid, and
    finding the number proves it's owned (and gives provisioning its PN SID).
    Returns booleans only; raises TwilioError (token-free) on failure."""
    account_sid = (account_sid or "").strip()
    auth_token = (auth_token or "").strip()
    number = (number or "").strip()
    if not account_sid:
        raise TwilioError("Account SID is required.")
    if not auth_token:
        raise TwilioError("Auth Token is required.")
    if not number:
        raise TwilioError("Twilio number is required.")
    pn_sid = incoming_number_sid(account_sid, auth_token, number, timeout=timeout)
    return {"ok": True, "number_owned": bool(pn_sid), "number_configured": bool(pn_sid)}


# --- poll loop --------------------------------------------------------------
# Dedup model: a bounded seen-set of finalized recording SIDs, plus a first-run
# stamp so we never replay the historical mailbox. We do NOT advance a date high-
# water on discovery, because a recording's transcription may not be ready yet —
# the seen-set is only added to once a recording is actually finalized (dispatched).

def _load_cursor() -> dict:
    try:
        data = json.loads(_state_file().read_text("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_cursor(start_iso: str, seen: list[str]) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bound the seen-set so the cursor file can't grow without limit. 500 >> the
        # 20-newest page we ever look at, so trimming never drops a still-visible sid.
        path.write_text(json.dumps({"start_iso": start_iso, "seen": seen[-500:]}),
                        encoding="utf-8")
    except Exception:
        pass


def _started_at(cursor: dict) -> datetime:
    raw = cursor.get("start_iso")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def _finalize_recording(rec: dict, *, log=None) -> dict | None:
    """Resolve a discovered recording into a deliverable voicemail dict, or None if
    it isn't ready yet (transcription still pending and not past the wait bound).

    Returns {recording_sid, call_sid, from_number, duration, transcript,
             transcribed(bool), date_created(iso)}.
    """
    sid = rec["sid"]
    age = (datetime.now(timezone.utc) - rec["date_created"]).total_seconds()
    transcript = ""
    transcribed = False

    mode = _transcribe_mode()
    if mode == "whisper":
        audio = recording_media(_account_sid(), _auth_token(), sid)
        text = _whisper_transcribe(audio, log=log) if audio else None
        if text is None and age < _MAX_TRANSCRIBE_WAIT_SECONDS:
            return None  # transient; retry next tick
        transcript = (text or "").strip()
        transcribed = bool(transcript)
    else:
        try:
            t = recording_transcript(_account_sid(), _auth_token(), sid)
        except TwilioError as e:
            if log:
                log.warning("voicemail: transcript fetch failed for %s: %s", sid, e)
            t = {"status": "none", "text": ""}
        if t["status"] == "completed":
            transcript = t["text"]
            transcribed = True
        elif t["status"] in ("in-progress", "none") and age < _MAX_TRANSCRIBE_WAIT_SECONDS:
            return None  # not ready yet; retry next tick
        # else: failed, or timed out waiting → deliver without a transcript.

    from_number = call_from(_account_sid(), _auth_token(), rec["call_sid"])
    return {
        "recording_sid": sid,
        "call_sid": rec["call_sid"],
        "from_number": from_number,
        "duration": rec["duration"],
        "transcript": transcript,
        "transcribed": transcribed,
        "date_created": rec["date_created"].isoformat(),
    }


def _whisper_transcribe(audio: bytes | None, *, log=None) -> str | None:
    """Local transcription via faster-whisper, if installed. Returns the text, or
    None on any failure (missing dependency, decode error) so the caller can retry
    or fall back. Kept optional — Voice Local does not ship Whisper by default."""
    if not audio:
        return None
    try:
        import voicemail_whisper
        return voicemail_whisper.transcribe(audio)
    except Exception as e:  # noqa: BLE001 — optional dependency / any failure is non-fatal
        if log:
            log.warning("voicemail: local whisper unavailable (%s)", type(e).__name__)
        return None


async def poll_once(on_voicemail: Callable[[dict], Awaitable[None]], *, log=None) -> int:
    """One poll tick. Lists recent recordings, finalizes any that are new (after the
    first-run stamp), not already delivered, and have a ready transcript (or have
    waited past the bound). Dispatches each (oldest first) to `on_voicemail` and
    records its SID as seen. Returns the number dispatched. Best-effort: a transport
    error is logged and swallowed so a flaky network never kills the poller."""
    cursor = _load_cursor()
    if "start_iso" not in cursor:
        start = datetime.now(timezone.utc)
        _save_cursor(start.isoformat(), [])
        cursor = {"start_iso": start.isoformat(), "seen": []}

    started = _started_at(cursor)
    seen = list(cursor.get("seen", []))
    seen_set = set(seen)

    try:
        recs = await asyncio.to_thread(
            list_recordings, _account_sid(), _auth_token(),
            page_size=20, timeout=_timeout(),
        )
    except TwilioError as e:
        if log:
            log.warning("voicemail poll: %s", e)  # token-free by construction
        return 0

    # Oldest-first among the fresh, undelivered, in-window recordings.
    fresh = [r for r in recs
             if r["sid"] and r["sid"] not in seen_set and r["date_created"] >= started]
    fresh.sort(key=lambda r: r["date_created"])

    dispatched = 0
    changed = False
    for r in fresh:
        vm = await asyncio.to_thread(_finalize_recording, r, log=log)
        if vm is None:
            continue  # not ready yet; leave un-seen so we re-check next tick
        try:
            await on_voicemail(vm)
            dispatched += 1
        except Exception:  # noqa: BLE001 — one bad voicemail must not stall the loop
            if log:
                log.exception("voicemail poll: on_voicemail failed for %s", r["sid"])
        seen.append(r["sid"])
        changed = True

    if changed:
        _save_cursor(started.isoformat(), seen)
    return dispatched


async def run_poller(on_voicemail: Callable[[dict], Awaitable[None]], *,
                     log=None, stop_event: asyncio.Event | None = None) -> None:
    """Long-running voicemail poll loop. Re-reads config each tick, so toggling the
    add-on off (or pulling creds) makes it go idle without a restart. Runs only
    while configured; sleeps the poll interval between ticks. Never raises — this is
    a fire-and-forget background task."""
    if log:
        log.info("voicemail poller started (interval=%ss)", _poll_interval())
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            if is_configured():
                await poll_once(on_voicemail, log=log)
        except Exception:  # noqa: BLE001 — the loop must outlive any single failure
            if log:
                log.exception("voicemail poller tick crashed; continuing")
        try:
            await asyncio.sleep(_poll_interval())
        except asyncio.CancelledError:
            break
