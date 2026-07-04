"""
Jarvis Voice Local — SMS (inbound-poll) setup wizard + connector tests.

Covers the /setup-sms slice end-to-end with a TestClient + the static wizard HTML,
plus unit tests for the polling connector (twilio_sms.py), proving:

  * route auth — /integrations/sms/test requires the bearer token (403 without);
  * /integrations/sms/test — success path (creds probe ok) and failure paths
    (ok:false), and that the submitted Auth Token is REDACTED from any error;
  * the test route stores/enables NOTHING (SMS stays off, inbound-only);
  * connector: list_inbound keeps only inbound messages and parses them; poll_once
    dispatches owner-only, oldest-first, dedupes across polls, and never replays the
    historical inbox; safe_status leaks no secret; there is NO send/delete function;
  * wizard HTML loads, no remote assets/CDN, no token in a URL/query, the secret
    input is a password field, the test UI + route are present, and the page sells
    the private polling model (no public webhook required);
  * the doctor sms check stays PASS for a user who never enables SMS;
  * the registry exposes sms as write_capable:False with no secret;
  * the release stages the wizard + connector + test and the deny-guard passes.

Run:  python test_sms_setup.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

import config

# Import-time stand-ins so server.py's config.validate() passes off the real box.
if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "c" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_smssetup_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"
config.STATE_DIR = _SANDBOX            # connector cursor file lands here

import integration_registry          # noqa: E402
import job_store                     # noqa: E402
import onboarding                    # noqa: E402
import twilio_sms                    # noqa: E402
import server                        # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

ROOT = Path(__file__).resolve().parent
TOKEN = server.JARVIS_TOKEN
client = TestClient(server.app)
AUTH = {"Authorization": "Bearer " + TOKEN}
SECRET = "TWILIO_AUTH_SECRET_should_never_echo"

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


def _msg(sid, frm, body, direction, when):
    return {"sid": sid, "from": frm, "to": "+15550000000", "body": body,
            "direction": direction, "date_created": twilio_sms._parse_date(when)}


def main() -> int:
    print("\n[1] /setup-sms page loads (unauth shell, carries no secret)")
    page = client.get("/setup-sms")
    check("GET /setup-sms -> 200", page.status_code == 200)
    check("page does not contain the bearer token", TOKEN not in page.text)

    print("\n[2] Route auth — sms test route requires the token")
    check("test without token -> 403",
          client.post("/integrations/sms/test",
                      json={"account_sid": "AC", "auth_token": "x", "number": "+1"}).status_code == 403)

    _real_probe = twilio_sms.probe

    print("\n[3] /test success path (creds verified; no save, no enable, no secret)")
    twilio_sms.probe = lambda sid, tok, num, timeout=None: {"ok": True, "number_configured": True}
    ok = client.post("/integrations/sms/test", headers=AUTH,
                     json={"account_sid": "ACxxxx", "auth_token": "good-token", "number": "+15555550123"})
    check("success -> 200", ok.status_code == 200)
    check("success ok:true", ok.json().get("ok") is True)
    check("SMS still disabled after a successful test (no auto-enable)",
          getattr(config, "SMS_ENABLED", False) is False)

    print("\n[4] /test failure paths surface ok:false with a redacted, token-free error")

    def _raise(msg):
        def _p(sid, tok, num, timeout=None):
            raise twilio_sms.TwilioError(msg)
        return _p

    twilio_sms.probe = _raise("Twilio API HTTP 401: Unauthorized (check your Account SID and Auth Token)")
    bad = client.post("/integrations/sms/test", headers=AUTH,
                      json={"account_sid": "ACxxxx", "auth_token": SECRET, "number": "+15555550123"})
    check("bad creds -> ok:false", bad.json().get("ok") is False)
    check("bad creds error present + mentions 401", "401" in bad.json().get("error", ""))

    twilio_sms.probe = _raise("Twilio API unreachable: name resolution failed")
    unreach = client.post("/integrations/sms/test", headers=AUTH,
                          json={"account_sid": "ACxxxx", "auth_token": SECRET, "number": "+15555550123"})
    check("unreachable -> ok:false", unreach.json().get("ok") is False)

    # Even if a message somehow contained the token, the route must scrub it.
    twilio_sms.probe = _raise("leak attempt " + SECRET)
    leak = client.post("/integrations/sms/test", headers=AUTH,
                       json={"account_sid": "ACxxxx", "auth_token": SECRET, "number": "+15555550123"})
    check("submitted token is redacted from the error", SECRET not in leak.text)
    check("redaction leaves a marker", "***" in leak.json().get("error", ""))

    print("\n[4b] /integrations/sms/enable writes the app's own config (one-click, incl. OWNER_PHONE)")
    import integration_config as ic
    _ic_root = ic.ROOT
    _bkp = config.BACKUP_DIR
    ic.ROOT = _SANDBOX
    config.BACKUP_DIR = _SANDBOX / "backups"
    (_SANDBOX / "settings.json").write_text(
        '{\n  "port": 8010,\n  "integrations": {"hunter": {"enabled": false}}\n}\n', "utf-8")
    (_SANDBOX / ".env").write_text(
        "JARVIS_TOKEN=keep\n# --- Twilio inbound SMS ---\n"
        "TWILIO_AUTH_TOKEN=\nTWILIO_ACCOUNT_SID=\nTWILIO_NUMBER=\nOWNER_PHONE=\n", "utf-8")

    check("enable without token -> 403",
          client.post("/integrations/sms/enable",
                      json={"account_sid": "AC", "auth_token": "x", "number": "+1",
                            "owner_phone": "+1"}).status_code == 403)

    twilio_sms.probe = _raise("Twilio API HTTP 401: Unauthorized")
    before = (_SANDBOX / "settings.json").read_text("utf-8")
    bad_en = client.post("/integrations/sms/enable", headers=AUTH,
                         json={"account_sid": "ACxxxx", "auth_token": SECRET,
                               "number": "+15555550123", "owner_phone": "+15551112222"})
    check("enable on a failed probe -> ok:false", bad_en.json().get("ok") is False)
    check("failed enable wrote NOTHING to settings.json",
          (_SANDBOX / "settings.json").read_text("utf-8") == before)
    check("failed enable did not echo the token", SECRET not in bad_en.text)

    twilio_sms.probe = lambda sid, tok, num, timeout=None: {"ok": True, "number_configured": True}
    good_en = client.post("/integrations/sms/enable", headers=AUTH,
                          json={"account_sid": "ACgood", "auth_token": SECRET,
                                "number": "+15550000000", "owner_phone": "+15551112222"})
    ej = good_en.json()
    check("enable success -> ok:true", ej.get("ok") is True)
    check("enable success -> restart_required:true", ej.get("restart_required") is True)
    check("enable response does NOT echo the token", SECRET not in good_en.text)
    written = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("settings.json now enables sms (valid JSON)", written["integrations"]["sms"]["enabled"] is True)
    check("settings.json preserved the sibling integration",
          written["integrations"]["hunter"] == {"enabled": False})
    check("token is NOT written to settings.json",
          SECRET not in (_SANDBOX / "settings.json").read_text("utf-8"))
    envtxt = (_SANDBOX / ".env").read_text("utf-8")
    check("auth token written to .env", ("TWILIO_AUTH_TOKEN=" + SECRET) in envtxt)
    check("account sid written to .env", "TWILIO_ACCOUNT_SID=ACgood" in envtxt)
    check("twilio number written to .env", "TWILIO_NUMBER=+15550000000" in envtxt)
    check("OWNER_PHONE written to .env", "OWNER_PHONE=+15551112222" in envtxt)
    check(".env preserved JARVIS_TOKEN", "JARVIS_TOKEN=keep" in envtxt)

    ic.ROOT = _ic_root
    config.BACKUP_DIR = _bkp
    twilio_sms.probe = _real_probe

    print("\n[5] Connector: list_inbound keeps only inbound and parses; no send/delete")
    raw = json.dumps({"messages": [
        {"sid": "SM1", "from": "+15551112222", "to": "+15550000000", "body": "hi",
         "direction": "inbound", "date_created": "Tue, 18 Aug 2015 17:30:00 +0000"},
        {"sid": "SM2", "from": "+15550000000", "to": "+15551112222", "body": "reply",
         "direction": "outbound-api", "date_created": "Tue, 18 Aug 2015 17:31:00 +0000"},
    ]})

    class _Resp:
        def __init__(self, t): self._t = t
        def read(self): return self._t.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request as _ur
    _real_urlopen = _ur.urlopen
    _ur.urlopen = lambda req, timeout=None: _Resp(raw)
    msgs = twilio_sms.list_inbound("ACxxxx", SECRET, "+15550000000")
    _ur.urlopen = _real_urlopen
    check("list_inbound returns only the inbound message", len(msgs) == 1 and msgs[0]["sid"] == "SM1")
    check("list_inbound parses the body", msgs[0]["body"] == "hi")
    check("connector has NO send function (inbound only)",
          not hasattr(twilio_sms, "send_sms") and not hasattr(twilio_sms, "send"))
    check("connector has NO delete function", not hasattr(twilio_sms, "delete_message"))
    check("safe_status exposes no secret",
          SECRET not in json.dumps(twilio_sms.safe_status()))

    print("\n[6] Connector: poll_once is owner-only, oldest-first, deduped, no history replay")
    config.SMS_ENABLED = True
    config.TWILIO_ACCOUNT_SID = "ACxxxx"
    config.TWILIO_AUTH_TOKEN = SECRET
    config.TWILIO_NUMBER = "+15550000000"
    config.OWNER_PHONE = "+15551112222"
    (_SANDBOX / "twilio_sms_cursor.json").unlink(missing_ok=True)

    feed = [
        _msg("SM_B", "+15551112222", "second", "inbound", "Tue, 18 Aug 2015 17:31:00 +0000"),
        _msg("SM_A", "+15551112222", "first", "inbound", "Tue, 18 Aug 2015 17:30:00 +0000"),
        _msg("SM_S", "+19998887777", "stranger", "inbound", "Tue, 18 Aug 2015 17:32:00 +0000"),
    ]
    _real_list = twilio_sms.list_inbound
    twilio_sms.list_inbound = lambda *a, **k: feed
    # Force the cursor below all feed timestamps so they count as "new".
    twilio_sms._save_cursor("2000-01-01T00:00:00+00:00", [])

    got: list[str] = []

    async def on_msg(b):
        got.append(b)

    n1 = asyncio.run(twilio_sms.poll_once(on_msg, log=None))
    check("first poll dispatches owner-only, oldest-first", got == ["first", "second"])
    check("stranger message ignored (allowlist)", "stranger" not in got)
    n2 = asyncio.run(twilio_sms.poll_once(on_msg, log=None))
    check("second poll dispatches nothing (deduped)", n2 == 0 and got == ["first", "second"])

    # No-history-replay: a fresh cursor stamps 'now', so old feed is skipped.
    (_SANDBOX / "twilio_sms_cursor.json").unlink(missing_ok=True)
    got.clear()
    n3 = asyncio.run(twilio_sms.poll_once(on_msg, log=None))
    check("fresh cursor never replays the historical inbox", n3 == 0 and got == [])

    twilio_sms.list_inbound = _real_list
    config.SMS_ENABLED = False
    config.TWILIO_ACCOUNT_SID = config.TWILIO_AUTH_TOKEN = config.TWILIO_NUMBER = config.OWNER_PHONE = ""

    print("\n[7] Wizard HTML: no remote assets, no token in URL, secret is a password field")
    html = (ROOT / "web" / "setup-sms.html").read_text("utf-8")
    check("page loads as a file", "SMS Setup" in html)
    check("no remote script/img assets", 'src="http' not in html)
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        check(f"no CDN reference ({cdn})", cdn not in html)
    check("no token ever placed in a query string", "?token=" not in html)
    check("JARVIS_TOKEN input is a password field", 'id="tokenInput" type="password"' in html)
    check("Auth Token input is a password field", 'id="twilioAuthToken" type="password"' in html)
    check("Account SID + number inputs present",
          'id="twilioAccountSid"' in html and 'id="twilioNumber"' in html)
    check("test button + route present",
          'id="testBtn"' in html and "/integrations/sms/test" in html)
    check("owner phone input present", 'id="ownerPhone"' in html)
    check("one-click enable button + route present",
          'id="enableBtn"' in html and "/integrations/sms/enable" in html)
    check("done section present", 'id="doneSec"' in html)
    check("page sells the private polling model (no public webhook required)",
          "poll" in html.lower() and "internet" in html.lower())
    check("no literal secret token value baked into the page", TOKEN not in html and SECRET not in html)

    print("\n[8] Doctor: a user who never enables SMS passes (no WARN/FAIL)")
    off = onboarding._sms_check(types.SimpleNamespace(
        SMS_ENABLED=False, TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_NUMBER=""))
    check("sms check is PASS when disabled", off["status"] == "PASS")

    print("\n[9] Registry: sms is inbound-only (write_capable False) and secret-free")
    entry = integration_registry.get("sms")
    check("registry has an sms entry", entry is not None)
    if entry:
        check("sms status is 'available' when unset", entry["status"] == "available")
        check("sms is not write_capable (inbound only)", entry["write_capable"] is False)
        check("sms is not delete_capable", entry["delete_capable"] is False)
        check("registry snapshot carries no secret",
              SECRET not in json.dumps(integration_registry.snapshot()))

    print("\n[10] Release stages the new files and the deny-guard still passes")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_release
    staged = make_release.staged_files()
    check("wizard page is staged", "web/setup-sms.html" in staged)
    check("connector module is staged", "twilio_sms.py" in staged)
    check("test suite is staged", "test_sms_setup.py" in staged)
    try:
        make_release.check_no_excluded(staged)
        check("deny-guard passes (no secret slips in)", True)
    except RuntimeError:
        check("deny-guard passes (no secret slips in)", False)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
