"""
Jarvis Voice Local — Settings add-on cards (U2) + brain add-on awareness (U3).

Covers the two registry-consuming surfaces:

  U2 — the Settings cards page:
    * /settings serves the page (no secret baked in);
    * the page renders from /integrations/status, uses textContent (no innerHTML of
      server data), has no remote/CDN assets, no token in a URL, password token input;
    * the release stages settings.html and the deny-guard passes.

  U3 — brain add-on awareness (the system-prompt note):
    * lists ENABLED add-ons (the brain may use) and points disabled ones at Settings;
    * carries NO secret value, ever;
    * flips an add-on between the enabled/redirect sections as config changes.

Run:  python test_settings_addons.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "f" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_settings_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import job_store                    # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

ROOT = Path(__file__).resolve().parent
TOKEN = server.JARVIS_TOKEN
client = TestClient(server.app)
AUTH = {"Authorization": "Bearer " + TOKEN}

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


SECRET_TOKEN = "cal-bridge-secret-" + "z" * 40


def main() -> int:
    orig = (config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN)
    try:
        print("\n[1] /settings page")
        r = client.get("/settings")
        check("page serves (200)", r.status_code == 200)
        html = r.text
        check("page carries no bearer token", TOKEN not in html)
        check("page fetches /integrations/status", "/integrations/status" in html)
        check("no remote/CDN assets", ('src="http' not in html) and
              all(c not in html for c in ("cdnjs", "unpkg", "jsdelivr")))
        check("no token in a URL/query", "?token=" not in html)
        check("token input is a password field", 'id="tokenInput" type="password"' in html)
        check("renders server text via textContent (not innerHTML)",
              "textContent" in html and ".innerHTML" not in html)
        check("no-store (edits show without cache clear)",
              r.headers.get("cache-control", "").find("no-store") >= 0)

        print("\n[2] Brain add-on awareness note (U3)")
        # Disabled calendar -> appears in the redirect (not set up) section.
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = (False, "", "")
        note = server._addon_awareness_note()
        check("note mentions Settings -> Add-ons redirect", "Settings -> Add-ons" in note)
        check("disabled calendar is NOT in the enabled section",
              "Enabled: none." in note or "Calendar — " not in note.split("NOT set up")[0])
        check("disabled calendar listed as not set up", "Calendar" in note.split("NOT set up")[-1])

        # Enabled calendar -> moves to the 'you may use' section.
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = (
            True, "https://script.google.com/macros/s/AKfake/exec", SECRET_TOKEN)
        note2 = server._addon_awareness_note()
        check("enabled calendar appears in the 'may use' section",
              "Calendar — " in note2.split("NOT set up")[0])
        check("awareness note leaks no secret", SECRET_TOKEN not in note2)
        check("note reminds writes need approval", "approval" in note2.lower())

        print("\n[2b] Add-ons is reachable IN-APP from the main web app (index.html)")
        idx = (ROOT / "web" / "index.html").read_text("utf-8")
        check("an in-app Add-ons entry exists (gear menu, or legacy view menu)",
              'id="gearAddons"' in idx or 'id="viewAddons"' in idx)
        check("an in-app Add-ons overlay exists", 'id="addonsOverlay"' in idx and 'id="addonsFrame"' in idx)
        check("the overlay loads /settings in-app", '"/settings"' in idx)
        check("a Back control returns to the assistant", 'id="addonsBack"' in idx)

        print("\n[3] Release stages the page; deny-guard passes")
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_release
        staged = make_release.staged_files()
        check("web/settings.html is staged", "web/settings.html" in staged)
        try:
            make_release.check_no_excluded(staged)
            check("deny-guard passes", True)
        except RuntimeError:
            check("deny-guard passes", False)
    finally:
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = orig

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
