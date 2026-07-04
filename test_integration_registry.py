"""
Jarvis Voice Local — add-on registry + /integrations/status tests.

Proves the scaffold every parallel add-on build conforms to:

  * the registry exposes the contract shape (id/name/short_description/category/
    setup_path/requires/write_capable/delete_capable/status/enabled);
  * status is derived from live config and walks the available -> needs_setup ->
    enabled ladder for Calendar (the reference add-on);
  * a snapshot NEVER leaks a secret (bridge URL or token), even when configured;
  * enabled_ids() reflects only fully-configured add-ons;
  * GET /integrations/status requires the bearer token (403 without) and returns
    the snapshot with no secret in the payload;
  * the release stages the registry module and the deny-guard still passes.

Run:  python test_integration_registry.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

# Import-time stand-ins so server.py's config.validate() passes off the real box.
if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "d" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_addonreg_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import integration_registry as reg     # noqa: E402
import job_store                        # noqa: E402
import server                           # noqa: E402
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


# Secret-ish values we set on config to prove they never escape into a snapshot.
SECRET_URL = "https://script.google.com/macros/s/AKfakeDEPLOYMENTid/exec"
SECRET_TOKEN = "cal-bridge-secret-" + "z" * 40


def _set_calendar(enabled: bool, url: str, token: str) -> None:
    config.CALENDAR_ENABLED = enabled
    config.CALENDAR_BRIDGE_URL = url
    config.CALENDAR_TOKEN = token


def main() -> int:
    # Snapshot the originals so the test box's real config is restored at the end.
    orig = (config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN)
    try:
        print("\n[1] Registry contract shape")
        cal = reg.get("google_calendar")
        check("calendar entry exists", cal is not None)
        required_keys = {
            "id", "name", "short_description", "category", "setup_path",
            "requires", "write_capable", "delete_capable", "status", "enabled",
        }
        for entry in reg.snapshot():
            check(f"{entry['id']} has the full contract shape",
                  required_keys.issubset(entry.keys()))
            check(f"{entry['id']} status is a valid ladder value",
                  entry["status"] in (reg.STATUS_AVAILABLE, reg.STATUS_NEEDS_SETUP,
                                      reg.STATUS_ENABLED))
            check(f"{entry['id']} enabled flag matches status",
                  entry["enabled"] == (entry["status"] == reg.STATUS_ENABLED))
        check("calendar delete is impossible by contract", cal["delete_capable"] is False)
        check("calendar is write-capable", cal["write_capable"] is True)
        check("unknown add-on returns None", reg.get("nope") is None)

        print("\n[2] Status ladder derives from live config")
        _set_calendar(False, "", "")
        check("nothing configured -> available",
              reg.get("google_calendar")["status"] == reg.STATUS_AVAILABLE)
        check("available calendar not in enabled_ids",
              "google_calendar" not in reg.enabled_ids())

        _set_calendar(True, SECRET_URL, "")  # flag on, creds incomplete
        check("flag on but missing token -> needs_setup",
              reg.get("google_calendar")["status"] == reg.STATUS_NEEDS_SETUP)
        check("needs_setup calendar not enabled",
              reg.get("google_calendar")["enabled"] is False)

        _set_calendar(True, SECRET_URL, SECRET_TOKEN)  # fully configured
        check("flag on + url + token -> enabled",
              reg.get("google_calendar")["status"] == reg.STATUS_ENABLED)
        check("enabled calendar appears in enabled_ids",
              "google_calendar" in reg.enabled_ids())

        print("\n[3] A snapshot NEVER leaks a secret, even when configured")
        import json
        blob = json.dumps(reg.snapshot())
        check("bridge URL absent from snapshot", SECRET_URL not in blob)
        check("bridge token absent from snapshot", SECRET_TOKEN not in blob)
        check("requires names the cred, not its value",
              "GOOGLE_CALENDAR_TOKEN" in reg.get("google_calendar")["requires"])

        print("\n[4] GET /integrations/status")
        r_noauth = client.get("/integrations/status")
        check("requires the bearer token (403 without)", r_noauth.status_code == 403)
        r = client.get("/integrations/status", headers=AUTH)
        check("authed request is 200", r.status_code == 200)
        body = r.json()
        check("payload has an integrations list", isinstance(body.get("integrations"), list))
        ids = [i["id"] for i in body["integrations"]]
        check("calendar is listed", "google_calendar" in ids)
        check("endpoint payload leaks no secret URL", SECRET_URL not in r.text)
        check("endpoint payload leaks no secret token", SECRET_TOKEN not in r.text)

        print("\n[5] Release stages the registry; deny-guard still passes")
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_release
        staged = make_release.staged_files()
        check("integration_registry.py is staged", "integration_registry.py" in staged)
        try:
            make_release.check_no_excluded(staged)
            check("deny-guard passes (no secret slips in)", True)
        except RuntimeError:
            check("deny-guard passes (no secret slips in)", False)
    finally:
        _set_calendar(*orig)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
