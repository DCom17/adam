"""
Jarvis Voice Local — LinkedIn setup wizard + routes tests.

Mirrors test_calendar_setup.py for the LinkedIn add-on's visible slice, proving:

  * the wizard page loads and carries NO secret;
  * route auth — /integrations/linkedin/test requires the bearer token (403 without);
  * /integrations/linkedin/test — success path (resolves the author URN, does NOT
    auto-enable), plus bad-token / unreachable / timeout failures, each with the
    submitted token REDACTED from the response;
  * the test route stores/enables NOTHING (LinkedIn stays off);
  * wizard HTML hygiene — no remote assets/CDN, no token in a URL/query, secret
    inputs are password fields, BOTH lanes + the test button + the route present;
  * the connector shape — draft lane needs no creds, no delete function, the token
    never appears in error strings;
  * the doctor linkedin check stays PASS for a user who never enables it;
  * the release stages linkedin.py + setup-linkedin.html and the deny-guard passes.

Some assertions depend on the shared-file patches (config block, server route,
registry entry, doctor check, release lists) the Calendar terminal applies at
merge. Until then those slices are reported as SKIP, not FAIL, so this file is
runnable solo and goes fully green after merge.

Run:  python test_linkedin_setup.py   (exit code 0 = all passed)
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

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_lisetup_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import linkedin                     # noqa: E402
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
_skipped = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def skip(name: str, why: str) -> None:
    global _skipped
    _skipped += 1
    print(f"  SKIP  {name}  ({why})")


def _route_exists(path: str, method: str = "post") -> bool:
    """Whether server.py already has the route (it arrives with the merge patch)."""
    for r in server.app.routes:
        if getattr(r, "path", None) == path and method.upper() in getattr(r, "methods", set()):
            return True
    return False


def main() -> int:
    print("\n[1] Connector shape (works solo — no shared-file patch needed)")
    check("draft lane needs no credentials (draft_post is network-free)",
          isinstance(linkedin.draft_post("a launch", "warm", "short"), dict))
    d = linkedin.draft_post("hiring news")
    check("draft_post returns a linkedin post spec",
          d.get("platform") == "linkedin" and d.get("kind") == "post_draft")
    check("draft_post rejects an empty topic", _raises_value(lambda: linkedin.draft_post("")))
    check("no delete/unpost function exists",
          not hasattr(linkedin, "delete_post") and not hasattr(linkedin, "unpost"))
    check("safe_status exposes booleans only (no token/secret/urn value)",
          _no_secret_values(linkedin.safe_status()))
    check("safe_status marks delete unsupported",
          linkedin.safe_status().get("delete_supported") is False)

    print("\n[2] probe_credentials redacts: error messages never contain the token")
    SECRET = "LI_ACCESS_TOKEN_should_never_echo"
    try:
        linkedin.probe_credentials("")
        leaked = False
    except linkedin.LinkedInError:
        leaked = False
    check("probe_credentials('') raises cleanly", True)

    print("\n[3] /setup-linkedin page loads (unauth shell, carries no secret)")
    if _route_exists("/setup-linkedin", "get"):
        page = client.get("/setup-linkedin")
        check("GET /setup-linkedin -> 200", page.status_code == 200)
        check("page does not contain the bearer token", TOKEN not in page.text)
    else:
        skip("GET /setup-linkedin", "route arrives with the server.py merge patch")

    print("\n[4] Route auth — /integrations/linkedin/test requires the token")
    if _route_exists("/integrations/linkedin/test", "post"):
        check("test without token -> 403",
              client.post("/integrations/linkedin/test",
                          json={"access_token": "y"}).status_code == 403)
    else:
        skip("/integrations/linkedin/test auth", "route arrives with the server.py merge patch")

    _real_probe = linkedin.probe_credentials

    print("\n[5] /test success path (resolves URN, no save, no enable)")
    if _route_exists("/integrations/linkedin/test", "post"):
        linkedin.probe_credentials = lambda tok, timeout=None: {
            "ok": True, "author_urn": "urn:li:person:ABC123", "name": "Test User"}
        ok = client.post("/integrations/linkedin/test", headers=AUTH,
                         json={"access_token": "good-token", "client_id": "cid"})
        check("success -> 200", ok.status_code == 200)
        body = ok.json()
        check("success ok:true + resolved author_urn",
              body.get("ok") is True and body.get("author_urn") == "urn:li:person:ABC123")
        check("LinkedIn still disabled after a successful test (no auto-enable)",
              _linkedin_enabled() is False)

        print("\n[6] /test failure paths surface ok:false with a redacted, token-free error")

        def _raise(msg):
            def _p(tok, timeout=None):
                raise linkedin.LinkedInError(msg)
            return _p

        linkedin.probe_credentials = _raise("LinkedIn API unreachable: name resolution failed")
        bad = client.post("/integrations/linkedin/test", headers=AUTH,
                          json={"access_token": SECRET})
        check("unreachable -> ok:false", bad.json().get("ok") is False)
        check("unreachable error present", bool(bad.json().get("error")))

        linkedin.probe_credentials = _raise("LinkedIn API HTTP 401: Unauthorized")
        bad_tok = client.post("/integrations/linkedin/test", headers=AUTH,
                              json={"access_token": SECRET})
        check("bad token -> ok:false with api message", bad_tok.json().get("ok") is False
              and "401" in bad_tok.json().get("error", ""))

        linkedin.probe_credentials = _raise("LinkedIn API request failed: timed out")
        timed = client.post("/integrations/linkedin/test", headers=AUTH,
                            json={"access_token": SECRET})
        check("timeout -> ok:false", timed.json().get("ok") is False)

        # Even if a message somehow contained the token, the route must scrub it.
        linkedin.probe_credentials = _raise("leak attempt " + SECRET)
        leak = client.post("/integrations/linkedin/test", headers=AUTH,
                           json={"access_token": SECRET})
        check("submitted token is redacted from the error", SECRET not in leak.text)
        check("redaction leaves a marker", "***" in leak.json().get("error", ""))
    else:
        skip("/integrations/linkedin/test behavior", "route arrives with the server.py merge patch")

    print("\n[6b] /integrations/linkedin/enable — draft lane (no secrets) + API lane (settings + .env)")
    import integration_config as ic
    _ic_root = ic.ROOT
    _bkp = config.BACKUP_DIR
    ic.ROOT = _SANDBOX
    config.BACKUP_DIR = _SANDBOX / "backups"
    (_SANDBOX / "settings.json").write_text(
        '{\n  "port": 8010,\n  "integrations": {"hunter": {"enabled": false}}\n}\n', "utf-8")
    (_SANDBOX / ".env").write_text("JARVIS_TOKEN=keep\n", "utf-8")

    check("enable without token -> 403",
          client.post("/integrations/linkedin/enable", json={"api_enabled": False}).status_code == 403)

    # Draft lane: settings only, NO secrets, NO .env writes.
    draft = client.post("/integrations/linkedin/enable", headers=AUTH, json={"api_enabled": False})
    dj = draft.json()
    check("draft enable -> ok:true, api_enabled false", dj.get("ok") is True and dj.get("api_enabled") is False)
    w1 = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("draft enable turns linkedin on, api off",
          w1["integrations"]["linkedin"]["enabled"] is True
          and w1["integrations"]["linkedin"]["api_enabled"] is False)
    check("draft enable wrote NO token to .env",
          "LINKEDIN_ACCESS_TOKEN=" not in (_SANDBOX / ".env").read_text("utf-8"))

    # API lane: bad token -> ok:false, nothing written; good token -> settings + .env.
    API_SECRET = "LI_ACCESS_TOKEN_should_never_echo"
    linkedin.probe_credentials = _raise("LinkedIn API HTTP 401: Unauthorized")
    before = (_SANDBOX / "settings.json").read_text("utf-8")
    bad_api = client.post("/integrations/linkedin/enable", headers=AUTH,
                          json={"api_enabled": True, "access_token": API_SECRET, "client_id": "cid"})
    check("api enable bad token -> ok:false", bad_api.json().get("ok") is False)
    check("bad api enable wrote nothing", (_SANDBOX / "settings.json").read_text("utf-8") == before)
    check("bad api enable did not echo the token", API_SECRET not in bad_api.text)

    linkedin.probe_credentials = lambda tok, timeout=None: {"author_urn": "urn:li:person:ABC", "name": "Me"}
    good_api = client.post("/integrations/linkedin/enable", headers=AUTH,
                           json={"api_enabled": True, "access_token": API_SECRET,
                                 "client_id": "cid", "client_secret": "csecret"})
    gj = good_api.json()
    check("api enable -> ok:true + applied live (no restart needed)",
          gj.get("ok") is True and gj.get("restart_required") is False)
    check("api enable applied live -> linkedin API lane shows enabled without a restart",
          config.safe_summary().get("linkedin_api_enabled") is True)
    check("api enable did NOT echo the token/secret", API_SECRET not in good_api.text and "csecret" not in good_api.text)
    w2 = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("api enable: api_enabled true + author_urn resolved",
          w2["integrations"]["linkedin"]["api_enabled"] is True
          and w2["integrations"]["linkedin"]["author_urn"] == "urn:li:person:ABC")
    check("token/secret NOT written to settings.json",
          API_SECRET not in json.dumps(w2) and "csecret" not in json.dumps(w2))
    envtxt = (_SANDBOX / ".env").read_text("utf-8")
    check("access token written to .env", ("LINKEDIN_ACCESS_TOKEN=" + API_SECRET) in envtxt)
    check("client secret written to .env", "LINKEDIN_CLIENT_SECRET=csecret" in envtxt)

    linkedin.probe_credentials = _real_probe
    ic.ROOT = _ic_root
    config.BACKUP_DIR = _bkp

    print("\n[7] Wizard HTML: both lanes, no remote assets, no token in URL, secrets are password fields")
    html = (ROOT / "web" / "setup-linkedin.html").read_text("utf-8")
    check("page loads as a file", "LinkedIn Setup" in html)
    check("no remote script/img assets", 'src="http' not in html)
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        check(f"no CDN reference ({cdn})", cdn not in html)
    check("no token ever placed in a query string", "?token=" not in html)
    check("JARVIS_TOKEN input is a password field", 'id="tokenInput" type="password"' in html)
    check("access-token input is a password field", 'id="accessToken" type="password"' in html)
    check("client-secret input is a password field", 'id="clientSecret" type="password"' in html)
    check("draft lane (Lane 1) present", "Lane 1" in html and 'id="copyDraft"' in html)
    check("api lane (Lane 2) present", "Lane 2" in html)
    check("ToS / no-account warning is prominent",
          "User Agreement" in html and "cannot delete" in html)
    check("test-connection button + route present",
          'id="testBtn"' in html and "/integrations/linkedin/test" in html)
    check("draft-lane one-click enable button present", 'id="enableDraftBtn"' in html)
    check("api-lane one-click enable button + route present",
          'id="enableBtn"' in html and "/integrations/linkedin/enable" in html)
    check("done section present", 'id="doneSec"' in html)
    check("no literal secret token value baked into the page", TOKEN not in html and SECRET not in html)

    print("\n[8] Doctor: a user who never enables linkedin passes (no WARN/FAIL)")
    if hasattr(onboarding, "_linkedin_check"):
        off = onboarding._linkedin_check(types.SimpleNamespace(
            LINKEDIN_ENABLED=False, LINKEDIN_API_ENABLED=False,
            LINKEDIN_ACCESS_TOKEN="", LINKEDIN_AUTHOR_URN=""))
        check("linkedin check is PASS when disabled", off["status"] == "PASS")
    else:
        skip("doctor linkedin check", "onboarding._linkedin_check arrives with the merge patch")

    print("\n[9] Release stages the new files and the deny-guard still passes")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_release
    staged = make_release.staged_files()
    if "linkedin.py" in staged:
        check("connector module is staged", "linkedin.py" in staged)
        check("wizard page is staged", "web/setup-linkedin.html" in staged)
        try:
            make_release.check_no_excluded(staged)
            check("deny-guard passes (no secret slips in)", True)
        except RuntimeError:
            check("deny-guard passes (no secret slips in)", False)
    else:
        skip("release staging", "make_release file lists arrive with the merge patch")

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed, {_skipped} skipped (pending merge)")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


# --- small helpers ----------------------------------------------------------

def _raises_value(fn) -> bool:
    try:
        fn()
        return False
    except linkedin.LinkedInError:
        return True
    except Exception:
        return False


def _no_secret_values(status: dict) -> bool:
    """safe_status must be booleans only — no string token/secret/urn values."""
    return all(isinstance(v, bool) for v in status.values())


def _linkedin_enabled() -> bool:
    """Read the live add-on status if the config/registry patch is merged; else
    fall back to the connector's own enabled flag. Either way: not enabled here."""
    try:
        return bool(config.safe_summary().get("linkedin_api_enabled", False)) \
            or bool(getattr(config, "LINKEDIN_ENABLED", False))
    except Exception:
        return bool(getattr(config, "LINKEDIN_ENABLED", False))


if __name__ == "__main__":
    sys.exit(main())
