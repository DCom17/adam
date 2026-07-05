"""
Adam — Hunter setup wizard + routes tests.

Covers the visible /setup-hunter slice end-to-end with a TestClient and the
static wizard HTML, proving:

  * route auth — bridge-code + test require the bearer token (403 without);
  * bridge-code returns the verify .gs template and exposes NO secret and NO
    delete/destructive handler;
  * /integrations/hunter/test — success path, bad url, bad token, bridge
    timeout/error, and that any submitted token is REDACTED from the response;
  * the test route stores/enables NOTHING (hunter stays off);
  * wizard HTML loads, has no remote assets/CDN, no token in a URL/query, the
    secret inputs are password fields, and the copy-code + test UI are present;
  * the doctor hunter check stays PASS for a user who never enables hunter;
  * the release stages the wizard + connector + verify template and the
    deny-guard passes.

NOTE: this exercises the integrated server, so it passes only after the Calendar
terminal merges the hunter shared-file patch (registry/config/server/release/
onboarding). Solo, the routes/config it depends on do not exist yet.

Run:  python test_hunter_setup.py   (exit code 0 = all passed)
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

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_hunsetup_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import hunter                       # noqa: E402
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
    print("\n[1] /setup-hunter page loads (unauth shell, carries no secret)")
    page = client.get("/setup-hunter")
    check("GET /setup-hunter -> 200", page.status_code == 200)
    check("page does not contain the bearer token", TOKEN not in page.text)

    print("\n[2] Route auth — hunter API routes require the token")
    check("bridge-code without token -> 403",
          client.get("/integrations/hunter/bridge-code").status_code == 403)
    check("test without token -> 403",
          client.post("/integrations/hunter/test",
                      json={"bridge_url": "x", "token": "y"}).status_code == 403)

    print("\n[3] bridge-code returns the full Hunter script, no secret, no row/tab deletion")
    bc = client.get("/integrations/hunter/bridge-code", headers=AUTH)
    check("bridge-code -> 200", bc.status_code == 200)
    body = bc.json()
    check("filename is hunter_dashboard.gs", body.get("filename") == "hunter_dashboard.gs")
    check("code includes setup + sync + verify entrypoints",
          "function setupHunterDashboard" in body["code"]
          and "function doPost" in body["code"]
          and "function doGet" in body["code"]
          and 'action === "verify"' in body["code"])
    # The web endpoints (doGet/doPost) must never DELETE rows or tabs. setup() may
    # clear a blank Sheet, but no row/sheet deletion is ever reachable.
    check("script has no row/tab deletion",
          "deleteRow" not in body["code"]
          and "deleteSheet" not in body["code"]
          and "removeSheet" not in body["code"]
          and "deleteColumn" not in body["code"])
    check("bridge-code exposes no bearer token", TOKEN not in bc.text)
    check("script hardcodes no token (uses Script Properties)",
          "PASTE_YOUR" not in body["code"])

    _real_probe = hunter.probe_bridge

    print("\n[4] /test success path (no save, no enable)")
    hunter.probe_bridge = lambda url, tok, timeout=None: {"ok": True, "quest_count": 3, "stat_count": 8}
    ok = client.post("/integrations/hunter/test", headers=AUTH,
                     json={"bridge_url": "https://script.google.com/macros/s/X/exec",
                           "token": "good-token"})
    check("success -> 200", ok.status_code == 200)
    check("success ok:true + counts", ok.json().get("ok") is True
          and ok.json().get("quest_count") == 3 and ok.json().get("stat_count") == 8)
    check("hunter still disabled after a successful test (no auto-enable)",
          config.safe_summary()["hunter_enabled"] is False)

    print("\n[5] /test failure paths surface ok:false with a redacted, token-free error")
    SECRET = "BRIDGE_SECRET_TOKEN_should_never_echo"

    def _raise(msg):
        def _p(url, tok, timeout=None):
            raise hunter.HunterError(msg)
        return _p

    hunter.probe_bridge = _raise("hunter bridge unreachable: name resolution failed")
    bad_url = client.post("/integrations/hunter/test", headers=AUTH,
                          json={"bridge_url": "https://bogus.invalid/exec", "token": SECRET})
    check("bad url -> ok:false", bad_url.json().get("ok") is False)
    check("bad url error present", bool(bad_url.json().get("error")))

    hunter.probe_bridge = _raise("Unauthorized")
    bad_tok = client.post("/integrations/hunter/test", headers=AUTH,
                          json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("bad token -> ok:false with bridge message", bad_tok.json().get("ok") is False
          and "Unauthorized" in bad_tok.json().get("error", ""))

    hunter.probe_bridge = _raise("hunter bridge request failed: timed out")
    timed = client.post("/integrations/hunter/test", headers=AUTH,
                        json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("timeout -> ok:false", timed.json().get("ok") is False)

    # Even if a message somehow contained the token, the route must scrub it.
    hunter.probe_bridge = _raise("leak attempt " + SECRET)
    leak = client.post("/integrations/hunter/test", headers=AUTH,
                       json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("submitted token is redacted from the error", SECRET not in leak.text)
    check("redaction leaves a marker", "***" in leak.json().get("error", ""))

    hunter.probe_bridge = _real_probe

    print("\n[6] Wizard HTML: no remote assets, no token in URL, secrets are password fields")
    html = (ROOT / "web" / "setup-hunter.html").read_text("utf-8")
    check("page loads as a file", "Operating System Setup" in html)
    check("no remote script/img assets", 'src="http' not in html)
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        check(f"no CDN reference ({cdn})", cdn not in html)
    check("no token ever placed in a query string", "?token=" not in html)
    check("ADAM_TOKEN input is a password field", 'id="tokenInput" type="password"' in html)
    check("bridge token input is a password field", 'id="bridgeToken" type="password"' in html)
    check("copy-code button + route present",
          'id="copyCode"' in html and "/integrations/hunter/bridge-code" in html)
    check("test-connection button + route present",
          'id="testBtn"' in html and "/integrations/hunter/test" in html)
    check("one-click enable button + route present",
          'id="enableBtn"' in html and "/integrations/hunter/enable" in html)
    check("done section shows the restart step",
          'id="doneSec"' in html and "restart Adam" in html)
    check("detailed auth-flow guidance present (Review permissions / Advanced)",
          "Review permissions" in html and "Advanced" in html)
    check("guidance to copy everything after the = sign",
          "after the" in html and "=" in html)
    check("no literal secret token value baked into the page", TOKEN not in html and SECRET not in html)

    print("\n[6b] /integrations/hunter/enable writes the app's own config (one-click)")
    import integration_config as ic
    _ic_root = ic.ROOT
    _bkp = config.BACKUP_DIR
    ic.ROOT = _SANDBOX
    config.BACKUP_DIR = _SANDBOX / "backups"
    (_SANDBOX / "settings.json").write_text(
        '{\n  "port": 8010,\n  "integrations": {"google_calendar": {"enabled": false}}\n}\n', "utf-8")
    (_SANDBOX / ".env").write_text(
        "ADAM_TOKEN=keep\n# --- Hunter dashboard bridge ---\nHUNTER_TOKEN=\n", "utf-8")

    check("enable without token -> 403",
          client.post("/integrations/hunter/enable",
                      json={"bridge_url": "x", "token": "y"}).status_code == 403)

    ENABLE_SECRET = "HUNTER_ENABLE_TOKEN_should_never_echo_or_land_in_settings"

    hunter.probe_bridge = _raise("Unauthorized")
    before = (_SANDBOX / "settings.json").read_text("utf-8")
    bad_en = client.post("/integrations/hunter/enable", headers=AUTH,
                         json={"bridge_url": "https://script.google.com/macros/s/X/exec",
                               "token": ENABLE_SECRET})
    check("enable on a failed probe -> ok:false", bad_en.json().get("ok") is False)
    check("failed enable wrote NOTHING to settings.json",
          (_SANDBOX / "settings.json").read_text("utf-8") == before)
    check("failed enable did not echo the token", ENABLE_SECRET not in bad_en.text)

    hunter.probe_bridge = lambda url, tok, timeout=None: {"ok": True, "quest_count": 1, "stat_count": 2}
    good_en = client.post("/integrations/hunter/enable", headers=AUTH,
                          json={"bridge_url": "https://script.google.com/macros/s/Y/exec",
                                "token": ENABLE_SECRET})
    ej = good_en.json()
    check("enable success -> ok:true", ej.get("ok") is True)
    check("enable success -> applied live (no restart needed)", ej.get("restart_required") is False)
    check("enable applied live -> hunter shows enabled without a restart",
          config.safe_summary()["hunter_enabled"] is True)
    check("enable response does NOT echo the token", ENABLE_SECRET not in good_en.text)
    written = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("settings.json now enables hunter (valid JSON)",
          written["integrations"]["hunter"]["enabled"] is True)
    check("settings.json preserved the sibling integration",
          written["integrations"]["google_calendar"] == {"enabled": False})
    check("token is NOT written to settings.json",
          ENABLE_SECRET not in (_SANDBOX / "settings.json").read_text("utf-8"))
    envtxt = (_SANDBOX / ".env").read_text("utf-8")
    check("token written to .env", ("HUNTER_TOKEN=" + ENABLE_SECRET) in envtxt)
    check(".env preserved ADAM_TOKEN", "ADAM_TOKEN=keep" in envtxt)

    hunter.probe_bridge = _real_probe
    ic.ROOT = _ic_root
    config.BACKUP_DIR = _bkp

    print("\n[7] Doctor: a user who never enables hunter passes (no WARN/FAIL)")
    off = onboarding._hunter_check(types.SimpleNamespace(
        HUNTER_ENABLED=False, HUNTER_BRIDGE_URL="", HUNTER_TOKEN=""))
    check("hunter check is PASS when disabled", off["status"] == "PASS")

    print("\n[8] Release stages the new files and the deny-guard still passes")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_release
    staged = make_release.staged_files()
    check("wizard page is staged", "web/setup-hunter.html" in staged)
    check("connector module is staged", "hunter.py" in staged)
    check("full Hunter script is staged", "hunter_dashboard.gs" in staged)
    check("verify fragment is staged", "hunter_verify.gs" in staged)
    try:
        make_release.check_no_excluded(staged)
        check("deny-guard passes (no secret slips in)", True)
    except RuntimeError:
        check("deny-guard passes (no secret slips in)", False)

    print("\n[9] In-app dashboard page: loads, has the tutorial + the mobile zoom knob")
    dash = client.get("/hunter-dashboard")
    check("GET /hunter-dashboard -> 200", dash.status_code == 200)
    dhtml = (ROOT / "web" / "hunter-dashboard.html").read_text("utf-8")
    check("dashboard carries no bearer token", TOKEN not in dash.text)
    check("tutorial section present", "How To Use This" in dhtml)
    check("tutorial lists the daily-flow commands",
          "Good morning" in dhtml and "Run daily planning" in dhtml
          and "Give me credit" in dhtml and "Big picture me" in dhtml)
    check("tutorial is tap-to-expand (reuses expand-card)", "help-card" in dhtml and "toggleCardV4" in dhtml)
    check("mobile sizing is intrinsic (no CSS zoom hack)",
          "intrinsic compact sizing" in dhtml and "zoom:" not in dhtml)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
