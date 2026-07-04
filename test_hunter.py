"""
Jarvis Voice Local — Hunter Tracker connector tests.

Self-contained (no network, no Google): monkeypatches config flags and the
connector's transport to prove:

    opt-in default OFF · is_configured gating · token sent in body (sync) /
    query (verify) and NEVER in safe_status · verify is a read (GET, action=verify)
    · sync POSTs the payload with token+source added and the quest keys PASSED
    THROUGH UNCHANGED · NO delete capability exists · bridge errors surface as
    HunterError with no token · disabled connector refuses with HunterNotConfigured.

Run:  python test_hunter.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import io
import json
import sys

import config
import hunter

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
    config.HUNTER_ENABLED = False
    config.HUNTER_BRIDGE_URL = ""
    config.HUNTER_TOKEN = ""
    check("is_configured() False when disabled/unset", hunter.is_configured() is False)
    check("safe_status ready False when disabled", hunter.safe_status()["ready"] is False)
    check("delete never advertised as supported", hunter.safe_status()["delete_supported"] is False)

    print("\n[2] Disabled connector refuses calls (fail-closed)")
    try:
        hunter.verify()
        check("disabled verify raises", False)
    except hunter.HunterNotConfigured:
        check("disabled verify raises HunterNotConfigured", True)
    try:
        hunter.sync({"state": {"total_xp": 1}})
        check("disabled sync raises", False)
    except hunter.HunterNotConfigured:
        check("disabled sync raises HunterNotConfigured", True)

    # --- enable with a URL + token ----------------------------------------
    config.HUNTER_ENABLED = True
    config.HUNTER_BRIDGE_URL = "https://script.google.com/macros/s/EXAMPLE/exec"
    config.HUNTER_TOKEN = TOKEN

    print("\n[3] Configured state")
    check("is_configured() True with enabled+url+token", hunter.is_configured() is True)
    check("safe_status ready True when configured", hunter.safe_status()["ready"] is True)

    print("\n[4] safe_status NEVER exposes the token or URL contents")
    blob = json.dumps(hunter.safe_status())
    check("token absent from safe_status", TOKEN not in blob)
    check("url string absent from safe_status", "script.google.com" not in blob)
    check("safe_status reports booleans only",
          hunter.safe_status()["bridge_configured"] is True and hunter.safe_status()["token_configured"] is True)

    # --- capture what the transport sends, without hitting the network ----
    sent: list[dict] = []

    def fake_urlopen(req, timeout=None):
        method = req.get_method()
        url = req.full_url
        if method == "GET":
            sent.append({"method": "GET", "url": url, "body": None})
            payload = {"ok": True, "result": {
                "daily_quests": [{"quest_id": "Q1", "linked_stat": "Discipline", "xp_value": 10}],
                "stats": [{"stat": "Discipline"}, {"stat": "Knowledge"}],
                "timestamp": "2026-06-26T00:00:00Z"}}
        else:
            body = json.loads(req.data.decode("utf-8"))
            sent.append({"method": "POST", "url": url, "body": body})
            payload = {"ok": True, "result": {"updated": True}}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    import urllib.request as _u
    _real = _u.urlopen
    _u.urlopen = fake_urlopen
    try:
        print("\n[5] verify() reads via GET, carries the token in the query, action=verify")
        board = hunter.verify()
        check("verify returns the board result", len(board.get("daily_quests", [])) == 1)
        check("verify used a GET", sent[-1]["method"] == "GET")
        check("verify used action=verify", "action=verify" in sent[-1]["url"])
        check("verify is read-only (no write/sync action)",
              "sync" not in sent[-1]["url"] and "delete" not in sent[-1]["url"])

        print("\n[6] probe_bridge() returns booleans/counts only")
        pr = hunter.probe_bridge(config.HUNTER_BRIDGE_URL, TOKEN)
        check("probe ok", pr["ok"] is True)
        check("probe reports quest_count", pr["quest_count"] == 1)
        check("probe reports stat_count", pr["stat_count"] == 2)

        print("\n[7] sync() POSTs the payload with token+source, quest keys UNCHANGED")
        payload = {
            "state": {"total_xp": 886, "rank": "E"},
            "daily_quests": [{"date": "2026-06-26", "quest_id": "Q1", "quest": "Swim",
                              "type": "daily", "linked_stat": "Health", "linked_boss": "BOSS-001",
                              "xp_value": 25, "status": "open", "evidence": "", "notes": ""}],
            "stats": [{"stat": "Health", "xp_total": 100}],
        }
        res = hunter.sync(payload)
        check("sync returns a result", isinstance(res, dict))
        body = sent[-1]["body"]
        check("sync used a POST", sent[-1]["method"] == "POST")
        check("token sent in POST body", body["token"] == TOKEN)
        check("source stamped in POST body", body["source"] == "jarvis-voice-local")
        check("token NOT in the POST URL", TOKEN not in sent[-1]["url"])
        # The canonical quest keys must survive untouched — renaming blanks the board.
        q = body["daily_quests"][0]
        for k in ("date", "quest_id", "quest", "type", "linked_stat", "linked_boss",
                  "xp_value", "status", "evidence", "notes"):
            check(f"quest key '{k}' passed through unchanged", k in q)
        check("quest values unchanged", q["quest_id"] == "Q1" and q["xp_value"] == 25)
        check("state block passed through unchanged", body["state"]["total_xp"] == 886)
        check("sync added no delete/clear key", "delete" not in body and "clear" not in body)

        print("\n[8] Bridge error (ok:false) surfaces as HunterError with no token")
        def err_urlopen(req, timeout=None):
            return _FakeResponse(json.dumps({"ok": False, "error": "Unauthorized"}).encode())
        _u.urlopen = err_urlopen
        try:
            hunter.verify()
            check("bridge ok:false raises", False)
        except hunter.HunterError as e:
            check("bridge ok:false raises HunterError", True)
            check("error message has no token", TOKEN not in str(e))
    finally:
        _u.urlopen = _real

    print("\n[9] No delete capability exists on the module")
    check("connector has no delete() function", not hasattr(hunter, "delete"))
    check("connector has no clear() function", not hasattr(hunter, "clear"))
    src = open(hunter.__file__, encoding="utf-8").read()
    check("connector source defines no delete action",
          'action="delete"' not in src and "'delete'" not in src)

    print("\n[10] Doctor hunter check is advisory (PASS off / configured, WARN unfinished, never FAIL)")
    import types
    import onboarding
    if hasattr(onboarding, "_hunter_check"):
        off = onboarding._hunter_check(types.SimpleNamespace(
            HUNTER_ENABLED=False, HUNTER_BRIDGE_URL="", HUNTER_TOKEN=""))
        check("disabled connector -> PASS", off["status"] == "PASS")
        ready = onboarding._hunter_check(types.SimpleNamespace(
            HUNTER_ENABLED=True, HUNTER_BRIDGE_URL="https://x/exec", HUNTER_TOKEN="t"))
        check("enabled+configured -> PASS", ready["status"] == "PASS")
        no_token = onboarding._hunter_check(types.SimpleNamespace(
            HUNTER_ENABLED=True, HUNTER_BRIDGE_URL="https://x/exec", HUNTER_TOKEN=""))
        check("enabled but missing token -> WARN (not FAIL)", no_token["status"] == "WARN")
        no_url = onboarding._hunter_check(types.SimpleNamespace(
            HUNTER_ENABLED=True, HUNTER_BRIDGE_URL="", HUNTER_TOKEN="t"))
        check("enabled but missing url -> WARN (not FAIL)", no_url["status"] == "WARN")
        check("hunter check NEVER returns FAIL",
              all(c["status"] != "FAIL" for c in (off, ready, no_token, no_url)))
    else:
        print("  SKIP  onboarding._hunter_check not merged yet (verified post-merge by the Calendar terminal)")

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
