"""
Jarvis Voice Local — Gmail connector tests.

Self-contained (no network, no Google): monkeypatches config flags and the
connector's transport to prove:

    opt-in default OFF · is_configured gating · token sent in body only and
    NEVER in safe_status/logs · reads call the right actions · create_draft is a
    write (DRAFT, never sends) · send_message is DOUBLE-LOCKED (raises when
    GMAIL_ALLOW_SEND is false; only sends through the bridge when the flag is on)
    · NO delete capability exists · bridge errors surface as GmailError ·
    disabled connector refuses with GmailNotConfigured.

Run:  python test_gmail_bridge.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import io
import json
import sys

import config
import gmail as gm

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
    config.GMAIL_ENABLED = False
    config.GMAIL_BRIDGE_URL = ""
    config.GMAIL_TOKEN = ""
    config.GMAIL_ALLOW_SEND = False
    check("is_configured() False when disabled/unset", gm.is_configured() is False)
    check("safe_status ready False when disabled", gm.safe_status()["ready"] is False)
    check("send not allowed by default", gm.safe_status()["send_allowed"] is False)
    check("delete never advertised as supported", gm.safe_status()["delete_supported"] is False)

    print("\n[2] Disabled connector refuses calls (fail-closed)")
    try:
        gm.list_messages("in:inbox", 5)
        check("disabled list raises", False)
    except gm.GmailNotConfigured:
        check("disabled list raises GmailNotConfigured", True)

    # --- enable with a URL + token ----------------------------------------
    config.GMAIL_ENABLED = True
    config.GMAIL_BRIDGE_URL = "https://script.google.com/macros/s/EXAMPLE/exec"
    config.GMAIL_TOKEN = TOKEN

    print("\n[3] Configured state")
    check("is_configured() True with enabled+url+token", gm.is_configured() is True)
    check("safe_status ready True when configured", gm.safe_status()["ready"] is True)

    print("\n[4] safe_status NEVER exposes the token or URL contents")
    blob = json.dumps(gm.safe_status())
    check("token absent from safe_status", TOKEN not in blob)
    check("url string absent from safe_status", "script.google.com" not in blob)
    check("safe_status reports booleans only",
          gm.safe_status()["bridge_configured"] is True and gm.safe_status()["token_configured"] is True)

    # --- capture what the transport sends, without hitting the network ----
    sent: list[dict] = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        sent.append({"url": req.full_url, "body": body})
        action = body.get("action")
        if action == "list":
            payload = {"ok": True, "action": "list",
                       "result": {"account": "me@example.com", "query": body.get("query"),
                                  "count": 1, "messages": [{"message_id": "m1", "from": "a@b.com",
                                                            "subject": "Hi", "snippet": "hello"}]}}
        elif action == "get":
            payload = {"ok": True, "action": "get",
                       "result": {"account": "me@example.com",
                                  "message": {"message_id": body.get("message_id"),
                                              "subject": "Hi", "body": "full body text"}}}
        elif action == "audit":
            off = body.get("offset", 0)
            mx = body.get("max_threads")
            # Emulate a full page (-> more, next_offset set) when max_threads==2,
            # else a short page (-> done, next_offset None).
            scanned = 2 if mx == 2 else 1
            nxt = (off + scanned) if scanned == mx else None
            payload = {"ok": True, "action": "audit",
                       "result": {"account": "me@example.com",
                                  "query": body.get("query") or "default",
                                  "offset": off, "scanned_threads": scanned,
                                  "next_offset": nxt, "more_likely": nxt is not None,
                                  "unique_senders": 1,
                                  "senders": [{"from_email": "deals@x.com", "domain": "x.com",
                                               "count": scanned, "marketing": True}]}}
        elif action == "create_draft":
            payload = {"ok": True, "action": "create_draft",
                       "result": {"mode": "draft", "draft_id": "d1", "to": body.get("to"), "sent": False}}
        elif action == "send":
            payload = {"ok": True, "action": "send",
                       "result": {"mode": "send", "to": body.get("to"), "sent": True}}
        else:
            payload = {"ok": False, "error": "unknown action"}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    import urllib.request as _u
    _real = _u.urlopen
    _u.urlopen = fake_urlopen
    try:
        print("\n[5] Reads call the right actions and carry the token in the body")
        msgs = gm.list_messages("in:inbox", 5)
        check("list returns messages", len(msgs) == 1 and msgs[0]["message_id"] == "m1")
        check("list used action=list", sent[-1]["body"]["action"] == "list")
        check("token sent in POST body", sent[-1]["body"]["token"] == TOKEN)
        check("token NOT in the request URL", TOKEN not in sent[-1]["url"])

        one = gm.get_message("m1")
        check("get returns the message body", one["message_id"] == "m1" and one["body"] == "full body text")
        check("get used action=get", sent[-1]["body"]["action"] == "get")

        print("\n[6] test_connection reads one message, never drafts/sends; returns account only")
        tc = gm.test_connection()
        check("test_connection ok + account", tc["ok"] is True and tc["account"] == "me@example.com")
        check("test_connection used a read (list)", sent[-1]["body"]["action"] == "list")

        print("\n[7] probe_bridge returns the account ONLY (never message contents)")
        pb = gm.probe_bridge(config.GMAIL_BRIDGE_URL, TOKEN)
        check("probe returns account", pb["account"] == "me@example.com")
        check("probe never returns messages", "messages" not in pb and "message" not in pb)

        print("\n[7b] audit_inbox is read-only, aggregates senders, and paginates")
        a1 = gm.audit_inbox(days=90, max_threads=2, offset=0)
        check("audit used action=audit", sent[-1]["body"]["action"] == "audit")
        check("audit carries the token in the body", sent[-1]["body"]["token"] == TOKEN)
        check("audit returns ranked senders", a1["senders"][0]["from_email"] == "deals@x.com")
        check("audit flags marketing", a1["senders"][0]["marketing"] is True)
        check("audit hands back next_offset when a page is full (paginates)", a1["next_offset"] == 2)
        a2 = gm.audit_inbox(days=90, offset=2)  # short page -> done
        check("audit reports done (next_offset None) on a short page", a2["next_offset"] is None)
        check("audit never returns message bodies",
              all("body" not in s for s in a2.get("senders", [])))

        print("\n[8] create_draft is a DRAFT write (never sends)")
        dr = gm.create_draft("x@y.com", "Subject", "Body text")
        check("create_draft returns a draft", dr.get("mode") == "draft" and dr.get("sent") is False)
        check("create_draft used action=create_draft", sent[-1]["body"]["action"] == "create_draft")

        print("\n[9] send_message is DOUBLE-LOCKED")
        config.GMAIL_ALLOW_SEND = False
        try:
            gm.send_message("x@y.com", "S", "B")
            check("send raises when GMAIL_ALLOW_SEND is false", False)
        except gm.GmailSendDisabled:
            check("send raises GmailSendDisabled when flag off (no bridge call)", True)
        check("no send action ever reached the bridge while flag off",
              all(s["body"].get("action") != "send" for s in sent))

        config.GMAIL_ALLOW_SEND = True
        sr = gm.send_message("x@y.com", "S", "B")
        check("send works only once the flag is on", sr.get("sent") is True)
        check("send used action=send", sent[-1]["body"]["action"] == "send")
        config.GMAIL_ALLOW_SEND = False

        check("no delete/archive/trash action was ever sent",
              all(s["body"].get("action") not in ("delete", "archive", "trash") for s in sent))

        print("\n[10] Bridge error (ok:false) surfaces as GmailError")
        def err_urlopen(req, timeout=None):
            return _FakeResponse(json.dumps({"ok": False, "error": "Unauthorized: invalid token."}).encode())
        _u.urlopen = err_urlopen
        try:
            gm.list_messages("in:inbox", 5)
            check("bridge ok:false raises", False)
        except gm.GmailError as e:
            check("bridge ok:false raises GmailError", True)
            check("error message has no token", TOKEN not in str(e))
    finally:
        _u.urlopen = _real

    print("\n[11] No delete/archive capability exists on the module")
    check("connector has no delete_message function", not hasattr(gm, "delete_message"))
    check("connector has no archive/trash function",
          not hasattr(gm, "archive_message") and not hasattr(gm, "trash_message"))
    src = open(gm.__file__, encoding="utf-8").read()
    check("connector source defines no delete/archive/trash action",
          '"delete"' not in src and '"archive"' not in src and '"trash"' not in src)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
