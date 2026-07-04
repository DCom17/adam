"""
Jarvis Voice Local — calendar setup wizard + routes tests.

Covers the visible /setup-calendar slice end-to-end with a TestClient and the
static wizard HTML, proving:

  * route auth — bridge-code + test require the bearer token (403 without);
  * bridge-code returns the .gs template and exposes NO secret;
  * /integrations/calendar/test — success path, bad url, bad token, bridge
    timeout/error, and that any submitted token is REDACTED from the response;
  * the test route stores/enables NOTHING (calendar stays off);
  * wizard HTML loads, has no remote assets/CDN, no token in a URL/query, the
    secret inputs are password fields, and the copy-code + test UI are present;
  * the doctor calendar check stays PASS for a user who never enables calendar;
  * the release stages the wizard + connector + bridge and the deny-guard passes.

Run:  python test_calendar_setup.py   (exit code 0 = all passed)
"""

from __future__ import annotations

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

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_calsetup_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import google_calendar              # noqa: E402
import job_store                    # noqa: E402
import onboarding                   # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

ROOT = Path(__file__).resolve().parent
TOKEN = server.JARVIS_TOKEN
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
    print("\n[1] /setup-calendar page loads (unauth shell, carries no secret)")
    page = client.get("/setup-calendar")
    check("GET /setup-calendar -> 200", page.status_code == 200)
    check("page does not contain the bearer token", TOKEN not in page.text)

    print("\n[2] Route auth — calendar API routes require the token")
    check("bridge-code without token -> 403",
          client.get("/integrations/calendar/bridge-code").status_code == 403)
    check("test without token -> 403",
          client.post("/integrations/calendar/test",
                      json={"bridge_url": "x", "token": "y"}).status_code == 403)

    print("\n[3] bridge-code returns the .gs template and no secret")
    bc = client.get("/integrations/calendar/bridge-code", headers=AUTH)
    check("bridge-code -> 200", bc.status_code == 200)
    body = bc.json()
    check("filename is calendar_bridge.gs", body.get("filename") == "calendar_bridge.gs")
    check("code includes the bridge entrypoints",
          "function doPost" in body["code"] and "setCalendarSyncToken" in body["code"])
    check("code has no delete handler (no delete action/function)",
          "case 'delete'" not in body["code"]
          and "deleteEvent" not in body["code"]
          and "deleteCalendarEvent" not in body["code"])
    check("bridge-code exposes no bearer token", TOKEN not in bc.text)

    # Patch the connector probe so no real network is touched.
    _real_probe = google_calendar.probe_bridge

    print("\n[4] /test success path (no save, no enable)")
    google_calendar.probe_bridge = lambda url, tok, cal="primary", timeout=None: {"ok": True, "calendar_id": "primary"}
    ok = client.post("/integrations/calendar/test", headers=AUTH,
                     json={"bridge_url": "https://script.google.com/macros/s/X/exec",
                           "token": "good-token", "calendar_id": "primary"})
    check("success -> 200", ok.status_code == 200)
    check("success ok:true + calendar_id", ok.json().get("ok") is True and ok.json().get("calendar_id") == "primary")
    check("calendar still disabled after a successful test (no auto-enable)",
          config.safe_summary()["calendar_enabled"] is False)

    print("\n[5] /test failure paths surface ok:false with a redacted, token-free error")
    SECRET = "BRIDGE_SECRET_TOKEN_should_never_echo"

    def _raise(msg):
        def _p(url, tok, cal="primary", timeout=None):
            raise google_calendar.CalendarError(msg)
        return _p

    google_calendar.probe_bridge = _raise("calendar bridge unreachable: name resolution failed")
    bad_url = client.post("/integrations/calendar/test", headers=AUTH,
                          json={"bridge_url": "https://bogus.invalid/exec", "token": SECRET})
    check("bad url -> ok:false", bad_url.json().get("ok") is False)
    check("bad url error present", bool(bad_url.json().get("error")))

    google_calendar.probe_bridge = _raise("Unauthorized: invalid token.")
    bad_tok = client.post("/integrations/calendar/test", headers=AUTH,
                          json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("bad token -> ok:false with bridge message", bad_tok.json().get("ok") is False
          and "Unauthorized" in bad_tok.json().get("error", ""))

    google_calendar.probe_bridge = _raise("calendar bridge request failed: timed out")
    timed = client.post("/integrations/calendar/test", headers=AUTH,
                        json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("timeout -> ok:false", timed.json().get("ok") is False)

    # Even if a message somehow contained the token, the route must scrub it.
    google_calendar.probe_bridge = _raise("leak attempt " + SECRET)
    leak = client.post("/integrations/calendar/test", headers=AUTH,
                       json={"bridge_url": "https://script.google.com/macros/s/X/exec", "token": SECRET})
    check("submitted token is redacted from the error", SECRET not in leak.text)
    check("redaction leaves a marker", "***" in leak.json().get("error", ""))

    google_calendar.probe_bridge = _real_probe

    print("\n[6] Wizard HTML: no remote assets, no token in URL, secrets are password fields")
    html = (ROOT / "web" / "setup-calendar.html").read_text("utf-8")
    check("page loads as a file", "Calendar Setup" in html)
    check("no remote script/img assets", 'src="http' not in html)
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        check(f"no CDN reference ({cdn})", cdn not in html)
    check("no token ever placed in a query string", "?token=" not in html)
    check("JARVIS_TOKEN input is a password field", 'id="tokenInput" type="password"' in html)
    check("bridge token input is a password field", 'id="bridgeToken" type="password"' in html)
    check("copy-code button + route present",
          'id="copyCode"' in html and "/integrations/calendar/bridge-code" in html)
    check("test-connection button + route present",
          'id="testBtn"' in html and "/integrations/calendar/test" in html)
    check("one-click enable button + route present",
          'id="enableBtn"' in html and "/integrations/calendar/enable" in html)
    check("done section shows a try-it example prompt",
          'id="doneSec"' in html and "to my calendar" in html)
    check("detailed auth-flow guidance present (Review permissions / Advanced)",
          "Review permissions" in html and "Advanced" in html)
    check("no literal secret token value baked into the page", TOKEN not in html and SECRET not in html)

    print("\n[7] Doctor: a user who never enables calendar passes (no WARN/FAIL)")
    off = onboarding._calendar_check(types.SimpleNamespace(
        CALENDAR_ENABLED=False, CALENDAR_BRIDGE_URL="", CALENDAR_TOKEN=""))
    check("calendar check is PASS when disabled", off["status"] == "PASS")

    print("\n[8] /integrations/calendar/enable writes the app's own config (one-click)")
    import integration_config as ic
    # Redirect ALL config writes + backups into the test sandbox so the real
    # settings.json/.env are never touched. The endpoint calls ic with default
    # paths, so patch the module root + the backup dir (per the isolate-destructive-
    # tests rule: redirect every data file, not just one).
    _ic_root = ic.ROOT
    _bkp = config.BACKUP_DIR
    ic.ROOT = _SANDBOX
    config.BACKUP_DIR = _SANDBOX / "backups"
    (_SANDBOX / "settings.json").write_text(
        '{\n  "port": 8010,\n  "integrations": {"hunter": {"enabled": false}}\n}\n', "utf-8")
    (_SANDBOX / ".env").write_text(
        "JARVIS_TOKEN=keep\n# --- Google Calendar bridge ---\nGOOGLE_CALENDAR_TOKEN=\n", "utf-8")

    check("enable without token -> 403",
          client.post("/integrations/calendar/enable",
                      json={"bridge_url": "x", "token": "y"}).status_code == 403)

    ENABLE_SECRET = "ENABLE_TOKEN_should_never_echo_or_land_in_settings"

    # A failed probe must enable NOTHING and write NOTHING, with the token redacted.
    google_calendar.probe_bridge = _raise("Unauthorized: invalid token.")
    before = (_SANDBOX / "settings.json").read_text("utf-8")
    bad_en = client.post("/integrations/calendar/enable", headers=AUTH,
                         json={"bridge_url": "https://script.google.com/macros/s/X/exec",
                               "token": ENABLE_SECRET})
    check("enable on a failed probe -> ok:false", bad_en.json().get("ok") is False)
    check("failed enable wrote NOTHING to settings.json",
          (_SANDBOX / "settings.json").read_text("utf-8") == before)
    check("failed enable did not echo the token", ENABLE_SECRET not in bad_en.text)

    # A good probe writes valid config to both files and asks for a restart.
    google_calendar.probe_bridge = lambda url, tok, cal="primary", timeout=None: {"ok": True, "calendar_id": "primary"}
    good_en = client.post("/integrations/calendar/enable", headers=AUTH,
                          json={"bridge_url": "https://script.google.com/macros/s/Y/exec",
                                "token": ENABLE_SECRET, "calendar_id": "primary"})
    ej = good_en.json()
    check("enable success -> ok:true", ej.get("ok") is True)
    check("enable success -> applied live (no restart needed)", ej.get("restart_required") is False)
    check("enable applied live -> calendar shows enabled without a restart",
          config.safe_summary()["calendar_enabled"] is True)
    check("enable response does NOT echo the token", ENABLE_SECRET not in good_en.text)
    written = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("settings.json now enables calendar (valid JSON)",
          written["integrations"]["google_calendar"]["enabled"] is True)
    check("settings.json preserved the sibling integration",
          written["integrations"]["hunter"] == {"enabled": False})
    check("token is NOT written to settings.json",
          ENABLE_SECRET not in (_SANDBOX / "settings.json").read_text("utf-8"))
    envtxt = (_SANDBOX / ".env").read_text("utf-8")
    check("token written to .env", ("GOOGLE_CALENDAR_TOKEN=" + ENABLE_SECRET) in envtxt)
    check(".env preserved JARVIS_TOKEN", "JARVIS_TOKEN=keep" in envtxt)

    google_calendar.probe_bridge = _real_probe
    ic.ROOT = _ic_root
    config.BACKUP_DIR = _bkp

    print("\n[9] Release stages the new files and the deny-guard still passes")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_release
    staged = make_release.staged_files()
    check("wizard page is staged", "web/setup-calendar.html" in staged)
    check("connector module is staged", "google_calendar.py" in staged)
    check("config writer module is staged", "integration_config.py" in staged)
    check("bridge template is staged", "calendar_bridge.gs" in staged)
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
