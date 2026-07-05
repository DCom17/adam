"""
Adam — email setup wizard + routes tests.

Covers the visible /setup-email slice end-to-end with a TestClient and the
static wizard HTML, proving:

  * route auth — bridge-code + test require the bearer token (403 without);
  * bridge-code returns the .gs template, exposes NO secret, and has NO
    delete/archive/trash handler;
  * /integrations/email/test — success path (returns the account), bad url, bad
    token, bridge timeout/error, and that any submitted token is REDACTED from
    the response;
  * the test route stores/enables NOTHING (email stays off; send stays off);
  * wizard HTML loads, has no remote assets/CDN, no token in a URL/query, the
    secret inputs are password fields, the copy-code + test UI are present, and
    the warnbox states drafts-for-approval / never-sends / cannot-delete;
  * the doctor email check stays PASS for a user who never enables email;
  * the release stages the wizard + connector + bridge and the deny-guard passes.

NOTE: the email routes + onboarding._email_check are added by the shared-file
patch (_addon_patches/email.md). This suite passes once that patch is merged;
solo, the connector + HTML-hygiene checks still hold (see test_gmail_bridge.py).

Run:  python test_email_setup.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

import config

# Import-time stand-ins so server.py's config.validate() passes off the real box.
if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "c" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_emailsetup_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import gmail                        # noqa: E402
import job_store                    # noqa: E402
import onboarding                   # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

ROOT = Path(__file__).resolve().parent
TOKEN = server.ADAM_TOKEN
client = TestClient(server.app)
AUTH = {"Authorization": "Bearer " + TOKEN}

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


def main() -> int:
    print("\n[1] /setup-email page loads (unauth shell, carries no secret)")
    page = client.get("/setup-email")
    check("GET /setup-email -> 200", page.status_code == 200)
    check("page does not contain the bearer token", TOKEN not in page.text)

    print("\n[2] Route auth — email API routes require the token")
    check("bridge-code without token -> 403",
          client.get("/integrations/email/bridge-code").status_code == 403)
    check("test without token -> 403",
          client.post("/integrations/email/test",
                      json={"bridge_url": "x", "token": "y"}).status_code == 403)

    print("\n[3] bridge-code returns the .gs template, no secret, no delete handler")
    bc = client.get("/integrations/email/bridge-code", headers=AUTH)
    check("bridge-code -> 200", bc.status_code == 200)
    body = bc.json()
    check("filename is gmail_bridge.gs", body.get("filename") == "gmail_bridge.gs")
    check("code includes the bridge entrypoints",
          "function doPost" in body["code"] and "setGmailBridgeToken" in body["code"])
    check("code dispatches send behind an explicit action (no send-without-guard)",
          "case 'send'" in body["code"] and "case 'create_draft'" in body["code"])
    check("code includes the read-only audit handler (filter suggestions)",
          "case 'audit'" in body["code"] and "auditMarketing_" in body["code"])
    check("code has no delete/archive/trash handler",
          "case 'delete'" not in body["code"]
          and "case 'archive'" not in body["code"]
          and "case 'trash'" not in body["code"]
          and "moveToTrash" not in body["code"]
          and "moveThreadToTrash" not in body["code"]
          and "deleteMessage" not in body["code"])
    check("bridge-code exposes no bearer token", TOKEN not in bc.text)

    _real_probe = gmail.probe_bridge

    print("\n[4] /test success path (no save, no enable, returns account)")
    gmail.probe_bridge = lambda url, tok, timeout=None: {"ok": True, "account": "me@example.com"}
    ok = client.post("/integrations/email/test", headers=AUTH,
                     json={"bridge_url": "https://script.google.com/macros/s/X/exec",
                           "token": "good-token"})
    check("success -> 200", ok.status_code == 200)
    check("success ok:true + account", ok.json().get("ok") is True and ok.json().get("account") == "me@example.com")
    check("email still disabled after a successful test (no auto-enable)",
          config.safe_summary().get("email_enabled") is False)

    print("\n[5] /test failure paths surface ok:false with a redacted, token-free error")
    SECRET = "BRIDGE_SECRET_TOKEN_should_never_echo"

    def _raise(msg):
        def _p(url, tok, timeout=None):
            raise gmail.GmailError(msg)
        return _p

    gmail.probe_bridge = _raise("gmail bridge unreachable: name resolution failed")
    bad_url = client.post("/integrations/email/test", headers=AUTH,
                          json={"bridge_url": "https://bogus.invalid/exec", "token": SECRET})
    check("bad url -> ok:false", bad_url.json().get("ok") is False)
    check("bad url error present", bool(bad_url.json().get("error")))

    gmail.probe_bridge = _raise("Unauthorized: invalid token.")
    bad_tok = client.post("/integrations/email/test", headers=AUTH,
                          json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("bad token -> ok:false with bridge message", bad_tok.json().get("ok") is False
          and "Unauthorized" in bad_tok.json().get("error", ""))

    gmail.probe_bridge = _raise("gmail bridge request failed: timed out")
    timed = client.post("/integrations/email/test", headers=AUTH,
                        json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("timeout -> ok:false", timed.json().get("ok") is False)

    gmail.probe_bridge = _raise("leak attempt " + SECRET)
    leak = client.post("/integrations/email/test", headers=AUTH,
                       json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("submitted token is redacted from the error", SECRET not in leak.text)
    check("redaction leaves a marker", "***" in leak.json().get("error", ""))

    print("\n[5b] /integrations/email/enable writes the app's own config (one-click)")
    import integration_config as ic
    _ic_root = ic.ROOT
    _bkp = config.BACKUP_DIR
    ic.ROOT = _SANDBOX
    config.BACKUP_DIR = _SANDBOX / "backups"
    (_SANDBOX / "settings.json").write_text(
        '{\n  "port": 8010,\n  "integrations": {"hunter": {"enabled": false}}\n}\n', "utf-8")
    (_SANDBOX / ".env").write_text(
        "ADAM_TOKEN=keep\n# --- Gmail bridge ---\nGMAIL_BRIDGE_TOKEN=\n", "utf-8")

    check("enable without token -> 403",
          client.post("/integrations/email/enable",
                      json={"bridge_url": "x", "token": "y"}).status_code == 403)

    ENABLE_SECRET = "EMAIL_ENABLE_TOKEN_should_never_echo"
    gmail.probe_bridge = _raise("Unauthorized: invalid token.")
    before = (_SANDBOX / "settings.json").read_text("utf-8")
    bad_en = client.post("/integrations/email/enable", headers=AUTH,
                         json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": ENABLE_SECRET})
    check("enable on a failed probe -> ok:false", bad_en.json().get("ok") is False)
    check("failed enable wrote NOTHING to settings.json",
          (_SANDBOX / "settings.json").read_text("utf-8") == before)
    check("failed enable did not echo the token", ENABLE_SECRET not in bad_en.text)

    gmail.probe_bridge = lambda url, tok, timeout=None: {"ok": True, "account": "me@example.com"}
    good_en = client.post("/integrations/email/enable", headers=AUTH,
                          json={"bridge_url": "https://script.google.com/macros/s/Y/exec", "token": ENABLE_SECRET})
    ej = good_en.json()
    check("enable success -> ok:true + applied live (no restart needed)",
          ej.get("ok") is True and ej.get("restart_required") is False)
    check("enable applied live -> email shows enabled without a restart",
          config.safe_summary().get("email_enabled") is True)
    check("enable response does NOT echo the token", ENABLE_SECRET not in good_en.text)
    written = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("settings.json enables gmail, allow_send stays false",
          written["integrations"]["gmail"]["enabled"] is True
          and written["integrations"]["gmail"]["allow_send"] is False)
    check("settings.json preserved the sibling integration",
          written["integrations"]["hunter"] == {"enabled": False})
    check("token is NOT written to settings.json",
          ENABLE_SECRET not in (_SANDBOX / "settings.json").read_text("utf-8"))
    check("token written to .env",
          ("GMAIL_BRIDGE_TOKEN=" + ENABLE_SECRET) in (_SANDBOX / ".env").read_text("utf-8"))

    gmail.probe_bridge = _real_probe
    ic.ROOT = _ic_root
    config.BACKUP_DIR = _bkp

    print("\n[6] Wizard HTML: no remote assets, no token in URL, secrets are password fields")
    html = (ROOT / "web" / "setup-email.html").read_text("utf-8")
    check("page loads as a file", "Email Setup" in html)
    check("no remote script/img assets", 'src="http' not in html)
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        check(f"no CDN reference ({cdn})", cdn not in html)
    check("no token ever placed in a query string", "?token=" not in html)
    check("ADAM_TOKEN input is a password field", 'id="tokenInput" type="password"' in html)
    check("bridge token input is a password field", 'id="bridgeToken" type="password"' in html)
    check("copy-code button + route present",
          'id="copyCode"' in html and "/integrations/email/bridge-code" in html)
    check("test-connection button + route present",
          'id="testBtn"' in html and "/integrations/email/test" in html)
    check("one-click enable button + route present",
          'id="enableBtn"' in html and "/integrations/email/enable" in html)
    check("done section present", 'id="doneSec"' in html)
    check("detailed Apps Script walkthrough present (save + auth flow)",
          "Save the project" in html and "Review permissions" in html and "Advanced" in html)
    check("guidance to copy everything after the = sign", "after the" in html and "=" in html)
    check("warnbox states the safety stance (drafts / never sends / cannot delete)",
          "draft" in html.lower() and "never sends without your approval" in html.lower()
          and "cannot delete" in html.lower())
    check("finish snippet defaults allow_send to false", '"allow_send": false' in html)
    check("no literal secret token value baked into the page", TOKEN not in html and SECRET not in html)

    print("\n[7] Doctor: a user who never enables email passes (no WARN/FAIL)")
    off = onboarding._email_check(types.SimpleNamespace(
        GMAIL_ENABLED=False, GMAIL_BRIDGE_URL="", GMAIL_TOKEN=""))
    check("email check is PASS when disabled", off["status"] == "PASS")

    print("\n[8] Release stages the new files and the deny-guard still passes")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_release
    staged = make_release.staged_files()
    check("wizard page is staged", "web/setup-email.html" in staged)
    check("connector module is staged", "gmail.py" in staged)
    check("bridge template is staged", "gmail_bridge.gs" in staged)
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
