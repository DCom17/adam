"""
Jarvis Voice Local — Gmail connector (opt-in, off by default).

Server-side client for the user's OWN Google Apps Script Gmail bridge
(gmail_bridge.gs), which runs inside the user's Google account. A thin,
dependency-free HTTP client over the stdlib `urllib` — no Google SDK, no OAuth
client, no service account. This is the email sibling of google_calendar.py and
follows the exact same trust model.

Trust model (mirrors the calendar / Twilio / secret pattern):
  * The bridge runs in the USER's Google account. We never hold a Google
    credential — only the random bridge token the user generated, which lives in
    .env as GMAIL_BRIDGE_TOKEN and is NEVER logged or returned in /health.
  * The token is sent only inside the POST body to the user's own bridge URL.

Capabilities, on purpose:
  * read  — list_messages(), get_message(), audit_inbox()   (safe; the server
            may call these directly, with or without Claude in the loop.
            audit_inbox aggregates promotional senders so Jarvis can SUGGEST
            Gmail filters — it counts, it never archives/deletes/filters.)
  * write — create_draft()                     (a DRAFT only; nothing is sent.
            The caller MUST route this through the approval flow — this module
            is just the transport and does NOT itself decide policy.)
  * send  — send_message()                     (DOUBLE-LOCKED. It is transport
            only AND it refuses to run unless config.GMAIL_ALLOW_SEND is true.
            Out of the box GMAIL_ALLOW_SEND is false, so the product physically
            cannot send. Even when enabled, the caller must route through the
            approval flow before calling.)
  * delete / archive / trash — NOT IMPLEMENTED. There is deliberately no delete
            (or archive/trash) function and the bridge has no such handler, so
            destroying or hiding mail is impossible through this product by
            construction. Mirrors the calendar connector's delete omission.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import config


class GmailError(RuntimeError):
    """A Gmail bridge call failed. The message never contains the token."""


class GmailNotConfigured(GmailError):
    """The Gmail connector is disabled or missing its URL/token."""


class GmailSendDisabled(GmailError):
    """A send was attempted while GMAIL_ALLOW_SEND is false (the default). Sending
    is physically off out of the box; this is the first of the two send locks."""


# --- configuration access (tolerant of config not yet wiring these in) ------

def _enabled() -> bool:
    return bool(getattr(config, "GMAIL_ENABLED", False))


def _bridge_url() -> str:
    return str(getattr(config, "GMAIL_BRIDGE_URL", "") or "").strip()


def _token() -> str:
    return str(getattr(config, "GMAIL_TOKEN", "") or "").strip()


def _allow_send() -> bool:
    """Lock #1 of two on sending. Default false — out of the box send_message()
    raises GmailSendDisabled regardless of the call site."""
    return bool(getattr(config, "GMAIL_ALLOW_SEND", False))


def _timeout() -> int:
    return int(getattr(config, "GMAIL_TIMEOUT_SECONDS", 20))


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
        "send_allowed": _allow_send(),
        "delete_supported": False,
    }


# --- transport --------------------------------------------------------------

def _request(bridge_url: str, token: str, action: str,
             payload: dict[str, Any] | None = None, timeout: int | None = None) -> dict:
    """Low-level POST of one action to an EXPLICIT bridge url + token. Raises
    GmailError on any failure. The token is placed in the body only — never in
    the URL, a log line, or an exception message (bridge error text is the
    bridge's own and contains no token). Used by both the config-based _post()
    and the wizard's probe_bridge()."""
    if not bridge_url or not token:
        raise GmailNotConfigured("gmail bridge url and token are required.")
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
        raise GmailError(f"gmail bridge HTTP {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise GmailError(f"gmail bridge unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        # malformed URL, socket timeout, etc. — surface without the token.
        raise GmailError(f"gmail bridge request failed: {e}") from None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise GmailError("gmail bridge returned a non-JSON response.") from None
    if not isinstance(parsed, dict) or not parsed.get("ok"):
        msg = (parsed.get("error") if isinstance(parsed, dict) else None) or "gmail bridge error"
        raise GmailError(str(msg))
    return parsed


def _post(action: str, payload: dict[str, Any] | None = None) -> dict:
    """POST one action using the CONFIGURED bridge url + token. Requires the
    connector to be enabled and configured."""
    if not is_configured():
        raise GmailNotConfigured(
            "Gmail connector is not configured "
            "(set integrations.gmail.enabled + bridge_url and GMAIL_BRIDGE_TOKEN)."
        )
    return _request(_bridge_url(), _token(), action, payload)


def probe_bridge(bridge_url: str, token: str, timeout: int | None = None) -> dict:
    """Validate an arbitrary bridge url + token WITHOUT saving anything or
    touching global config — the wizard's 'Test connection' green-check. Does a
    single-message read (a read; never drafts or sends) so success proves the
    deployment is live and the token is accepted. Returns the account email and
    booleans/counts only — NEVER any message contents. Raises GmailError
    (token-free message) on failure."""
    bridge_url = (bridge_url or "").strip()
    token = (token or "").strip()
    if not bridge_url:
        raise GmailError("bridge_url is required.")
    if not token:
        raise GmailError("token is required.")
    res = _request(bridge_url, token, "list", {"query": "in:inbox", "max": 1}, timeout=timeout)
    result = res.get("result", {}) or {}
    # Account email only — deliberately drop the messages array so a probe never
    # returns mail contents.
    return {"ok": True, "account": result.get("account", "")}


# --- reads (safe; no approval needed) ---------------------------------------

def test_connection() -> dict:
    """Prove the bridge URL AND token both work, without drafting or sending.
    Reads a single inbox message header so a green result means the deployment is
    live and the token is accepted. Returns the account + booleans only — no
    message contents."""
    res = _post("list", {"query": "in:inbox", "max": 1})
    result = res.get("result", {}) or {}
    return {"ok": True, "account": result.get("account", "")}


def list_messages(query: str = "in:inbox", max: int = 20) -> list[dict]:
    """Recent message summaries matching a Gmail search `query` (sender, subject,
    date, snippet, ids — never the full body). Read-only."""
    res = _post("list", {"query": str(query or "in:inbox"), "max": int(max)})
    return list((res.get("result", {}) or {}).get("messages", []))


def get_message(message_id: str) -> dict:
    """A single message by id, including its plain-text body. Read-only — used to
    show the message Jarvis is drafting a reply to."""
    if not message_id:
        raise GmailError("get_message requires a message_id.")
    res = _post("get", {"message_id": str(message_id)})
    return (res.get("result", {}) or {}).get("message", {})


def audit_inbox(days: int = 90, query: str | None = None,
                max_threads: int = 200, offset: int = 0) -> dict:
    """Read-only inbox audit for FILTER SUGGESTIONS. Aggregates promotional /
    marketing senders over a window (sender, domain, message count, an
    unsubscribe-header marketing flag) so Jarvis can PROPOSE Gmail filters you
    apply yourself. It NEVER archives, deletes, marks, or creates a filter — it
    only reads and counts.

    Paginated via `offset`: a busy multi-month inbox can exceed the Apps Script
    run limit in a single pass, so when the returned dict's `next_offset` is not
    None, call again with that offset to continue scanning. Returns the aggregate
    (account, query, scanned_threads, offset, next_offset, more_likely,
    unique_senders, senders[...]) — never full message bodies."""
    payload: dict[str, Any] = {
        "days": int(days),
        "max_threads": int(max_threads),
        "offset": int(offset),
    }
    if query:
        payload["query"] = str(query)
    res = _post("audit", payload)
    return res.get("result", {}) or {}


# --- write: create a DRAFT (MUST be gated by approval at the call site) ------

def create_draft(to: str, subject: str, body: str,
                 cc: str | None = None, bcc: str | None = None,
                 reply_to_message_id: str | None = None) -> dict:
    """Create a Gmail DRAFT (nothing is sent — the draft waits in the user's
    Drafts for them to review/send). WRITE: the caller is responsible for routing
    this through the approval flow; this function is only the transport. When
    reply_to_message_id is given the bridge threads the draft as a reply."""
    if not to or not str(to).strip():
        raise GmailError("create_draft requires at least one recipient (to).")
    if subject is None:
        raise GmailError("create_draft requires a subject.")
    payload: dict[str, Any] = {
        "to": str(to).strip(),
        "subject": str(subject),
        "body": str(body or ""),
    }
    if cc:
        payload["cc"] = str(cc).strip()
    if bcc:
        payload["bcc"] = str(bcc).strip()
    if reply_to_message_id:
        payload["reply_to_message_id"] = str(reply_to_message_id).strip()
    res = _post("create_draft", payload)
    return res.get("result", {}) or {}


# --- send (DOUBLE-LOCKED: the flag here + the approval flow at the call site) -

def send_message(to: str, subject: str, body: str,
                 cc: str | None = None, bcc: str | None = None,
                 reply_to_message_id: str | None = None) -> dict:
    """Send an email. DOUBLE-LOCKED:
      1. This function refuses (raises GmailSendDisabled) unless
         config.GMAIL_ALLOW_SEND is true. Out of the box it is false, so the
         product cannot send at all.
      2. It is transport only — the caller MUST obtain an explicit send approval
         before calling. Claude is never handed a send tool.
    There is intentionally no way to send through this module without flipping
    the deliberate GMAIL_ALLOW_SEND lock first."""
    if not _allow_send():
        raise GmailSendDisabled(
            "Sending is disabled. Set integrations.gmail.allow_send=true to enable "
            "it; out of the box Jarvis can only draft, never send."
        )
    if not to or not str(to).strip():
        raise GmailError("send_message requires at least one recipient (to).")
    if subject is None:
        raise GmailError("send_message requires a subject.")
    payload: dict[str, Any] = {
        "to": str(to).strip(),
        "subject": str(subject),
        "body": str(body or ""),
    }
    if cc:
        payload["cc"] = str(cc).strip()
    if bcc:
        payload["bcc"] = str(bcc).strip()
    if reply_to_message_id:
        payload["reply_to_message_id"] = str(reply_to_message_id).strip()
    res = _post("send", payload)
    return res.get("result", {}) or {}

# NOTE: there is intentionally no delete_message()/archive()/trash(). Removing or
# hiding mail is unsupported by design — both here and in the bridge — so it
# cannot happen through Jarvis.
