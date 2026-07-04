"""
Jarvis Voice Local — Voicemail (missed-call message-taker) wizard + connector tests.

Covers the /setup-voicemail slice end-to-end with a TestClient + the static wizard
HTML, plus unit tests for the polling connector (twilio_voicemail.py) and the answer
provisioning (voicemail_provision.py), proving:

  * route auth — every /integrations/voicemail/* route needs the bearer token (403);
  * /test — success (creds probe ok) and failure (ok:false) with the submitted Auth
    Token REDACTED from any error; the test route stores/enables NOTHING;
  * /enable — re-probes, then the SERVER writes its own settings.json voicemail block
    + the three Twilio .env values; a failed probe writes nothing; the token never
    lands in settings.json or the response;
  * /wire — points the number's Voice webhook via the API; bad number -> ok:false;
  * connector: list_recordings parses; poll_once dispatches new recordings with a
    ready transcript oldest-first, dedupes across polls, holds a pending transcript
    until it completes, and never replays the historical mailbox; safe_status leaks
    no secret; there is NO send/originate/delete function;
  * provisioning: build_answer_twiml is a one-shot record (British <Say> + <Record
    transcribe> + <Hangup>, no statusCallback) and <Play>s a hosted clip when given;
    set_number_voice_url POSTs VoiceUrl and never echoes the token;
  * wizard HTML loads, no remote assets/CDN, no token in a URL, the secret is a
    password field, the test/wire/enable UI + routes are present, and it sells the
    private polling model;
  * the doctor voicemail check stays PASS for a user who never enables it;
  * the registry exposes voicemail as write_capable:False with no secret;
  * the release stages the wizard + connectors + test and the deny-guard passes.

Run:  python test_voicemail_setup.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

# Import-time stand-ins so server.py's config.validate() passes off the real box.
if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "c" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_vmsetup_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"
config.STATE_DIR = _SANDBOX            # connector cursor + voicemail store land here

import integration_registry          # noqa: E402
import job_store                     # noqa: E402
import onboarding                    # noqa: E402
import twilio_voicemail              # noqa: E402
import voicemail_provision          # noqa: E402
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


def _rec(sid, call_sid, when, duration=8):
    return {"sid": sid, "call_sid": call_sid, "date_created": when, "duration": duration}


def main() -> int:
    print("\n[1] /setup-voicemail page loads (unauth shell, carries no secret)")
    page = client.get("/setup-voicemail")
    check("GET /setup-voicemail -> 200", page.status_code == 200)
    check("page does not contain the bearer token", TOKEN not in page.text)

    print("\n[2] Route auth — every voicemail route requires the token")
    for path, body in [
        ("/integrations/voicemail/test", {"account_sid": "AC", "auth_token": "x", "number": "+1"}),
        ("/integrations/voicemail/enable", {"account_sid": "AC", "auth_token": "x", "number": "+1"}),
        ("/integrations/voicemail/wire", {"account_sid": "AC", "auth_token": "x", "number": "+1", "voice_url": "https://x"}),
    ]:
        check(f"{path} without token -> 403", client.post(path, json=body).status_code == 403)
    check("GET /voicemails without token -> 403", client.get("/voicemails").status_code == 403)

    _real_probe = twilio_voicemail.probe

    print("\n[3] /test success path (creds verified; no save, no enable, no secret)")
    twilio_voicemail.probe = lambda sid, tok, num, timeout=None: {"ok": True, "number_owned": True, "number_configured": True}
    ok = client.post("/integrations/voicemail/test", headers=AUTH,
                     json={"account_sid": "ACxxxx", "auth_token": "good-token", "number": "+15555550123"})
    check("success -> 200", ok.status_code == 200)
    check("success ok:true", ok.json().get("ok") is True)
    check("success reports number_owned", ok.json().get("number_owned") is True)
    check("voicemail still disabled after a successful test (no auto-enable)",
          getattr(config, "VOICEMAIL_ENABLED", False) is False)

    print("\n[4] /test failure paths surface ok:false with a redacted, token-free error")

    def _raise(msg):
        def _p(sid, tok, num, timeout=None):
            raise twilio_voicemail.TwilioError(msg)
        return _p

    twilio_voicemail.probe = _raise("Twilio API HTTP 401: Unauthorized (check your Account SID and Auth Token)")
    bad = client.post("/integrations/voicemail/test", headers=AUTH,
                      json={"account_sid": "ACxxxx", "auth_token": SECRET, "number": "+15555550123"})
    check("bad creds -> ok:false", bad.json().get("ok") is False)
    check("bad creds error mentions 401", "401" in bad.json().get("error", ""))

    twilio_voicemail.probe = _raise("leak attempt " + SECRET)
    leak = client.post("/integrations/voicemail/test", headers=AUTH,
                       json={"account_sid": "ACxxxx", "auth_token": SECRET, "number": "+15555550123"})
    check("submitted token is redacted from the error", SECRET not in leak.text)
    check("redaction leaves a marker", "***" in leak.json().get("error", ""))

    print("\n[5] /enable writes the app's own config (one-click); failed probe writes nothing")
    import integration_config as ic
    _ic_root = ic.ROOT
    _bkp = config.BACKUP_DIR
    ic.ROOT = _SANDBOX
    config.BACKUP_DIR = _SANDBOX / "backups"
    (_SANDBOX / "settings.json").write_text(
        '{\n  "port": 8010,\n  "integrations": {"sms": {"enabled": false}}\n}\n', "utf-8")
    (_SANDBOX / ".env").write_text(
        "JARVIS_TOKEN=keep\nTWILIO_AUTH_TOKEN=\nTWILIO_ACCOUNT_SID=\nTWILIO_NUMBER=\n", "utf-8")

    twilio_voicemail.probe = _raise("Twilio API HTTP 401: Unauthorized")
    before = (_SANDBOX / "settings.json").read_text("utf-8")
    bad_en = client.post("/integrations/voicemail/enable", headers=AUTH,
                         json={"account_sid": "ACxxxx", "auth_token": SECRET, "number": "+15555550123"})
    check("enable on a failed probe -> ok:false", bad_en.json().get("ok") is False)
    check("failed enable wrote NOTHING to settings.json",
          (_SANDBOX / "settings.json").read_text("utf-8") == before)
    check("failed enable did not echo the token", SECRET not in bad_en.text)

    twilio_voicemail.probe = lambda sid, tok, num, timeout=None: {"ok": True, "number_owned": True, "number_configured": True}
    good_en = client.post("/integrations/voicemail/enable", headers=AUTH,
                          json={"account_sid": "ACgood", "auth_token": SECRET,
                                "number": "+15550000000", "owner_name": "Diego",
                                "transcribe_mode": "twilio"})
    ej = good_en.json()
    check("enable success -> ok:true", ej.get("ok") is True)
    check("enable success -> restart_required:true", ej.get("restart_required") is True)
    check("enable response does NOT echo the token", SECRET not in good_en.text)
    written = json.loads((_SANDBOX / "settings.json").read_text("utf-8"))
    check("settings.json now enables voicemail (valid JSON)",
          written["integrations"]["voicemail"]["enabled"] is True)
    check("settings.json stored the owner name + transcribe mode",
          written["integrations"]["voicemail"]["owner_name"] == "Diego"
          and written["integrations"]["voicemail"]["transcribe_mode"] == "twilio")
    check("settings.json preserved the sibling integration",
          written["integrations"]["sms"] == {"enabled": False})
    check("token is NOT written to settings.json",
          SECRET not in (_SANDBOX / "settings.json").read_text("utf-8"))
    envtxt = (_SANDBOX / ".env").read_text("utf-8")
    check("auth token written to .env", ("TWILIO_AUTH_TOKEN=" + SECRET) in envtxt)
    check("account sid written to .env", "TWILIO_ACCOUNT_SID=ACgood" in envtxt)
    check("twilio number written to .env", "TWILIO_NUMBER=+15550000000" in envtxt)
    check(".env preserved JARVIS_TOKEN", "JARVIS_TOKEN=keep" in envtxt)

    print("\n[6] /wire points the number's Voice webhook (bad number -> ok:false, token-safe)")
    _real_isid = twilio_voicemail.incoming_number_sid
    _real_seturl = voicemail_provision.set_number_voice_url

    twilio_voicemail.incoming_number_sid = lambda sid, tok, num, timeout=None: ""
    nowire = client.post("/integrations/voicemail/wire", headers=AUTH,
                         json={"account_sid": "AC", "auth_token": SECRET, "number": "+1",
                               "voice_url": "https://handler.twilio.com/twiml/EH1"})
    check("number not on account -> ok:false", nowire.json().get("ok") is False)

    twilio_voicemail.incoming_number_sid = lambda sid, tok, num, timeout=None: "PN123"
    voicemail_provision.set_number_voice_url = lambda sid, tok, pn, url, timeout=20: {"ok": True, "voice_url": url}
    yeswire = client.post("/integrations/voicemail/wire", headers=AUTH,
                          json={"account_sid": "AC", "auth_token": SECRET, "number": "+15550000000",
                                "voice_url": "https://handler.twilio.com/twiml/EH1"})
    check("wire success -> ok:true", yeswire.json().get("ok") is True)
    check("wire response does not echo the token", SECRET not in yeswire.text)

    twilio_voicemail.incoming_number_sid = _real_isid
    voicemail_provision.set_number_voice_url = _real_seturl

    ic.ROOT = _ic_root
    config.BACKUP_DIR = _bkp
    twilio_voicemail.probe = _real_probe

    print("\n[7] Connector: list_recordings parses; no send/originate/delete")
    raw = json.dumps({"recordings": [
        {"sid": "RE1", "call_sid": "CA1", "duration": "12",
         "date_created": "Tue, 18 Aug 2015 17:30:00 +0000"},
        {"sid": "RE2", "call_sid": "CA2", "duration": "5",
         "date_created": "Tue, 18 Aug 2015 17:31:00 +0000"},
    ]})

    class _Resp:
        def __init__(self, t): self._t = t
        def read(self): return self._t.encode("utf-8")
        @property
        def status(self): return 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request as _ur
    _real_urlopen = _ur.urlopen
    _ur.urlopen = lambda req, timeout=None: _Resp(raw)
    recs = twilio_voicemail.list_recordings("ACxxxx", SECRET)
    _ur.urlopen = _real_urlopen
    check("list_recordings parses both recordings", len(recs) == 2 and recs[0]["sid"] == "RE1")
    check("list_recordings parses call_sid + duration",
          recs[0]["call_sid"] == "CA1" and recs[0]["duration"] == 12)
    check("connector has NO send/originate function",
          not hasattr(twilio_voicemail, "send") and not hasattr(twilio_voicemail, "place_call"))
    check("connector has NO delete function", not hasattr(twilio_voicemail, "delete_recording"))
    check("safe_status exposes no secret", SECRET not in json.dumps(twilio_voicemail.safe_status()))

    print("\n[8] Connector: poll_once dispatches ready transcripts, deduped, oldest-first")
    config.VOICEMAIL_ENABLED = True
    config.TWILIO_ACCOUNT_SID = "ACxxxx"
    config.TWILIO_AUTH_TOKEN = SECRET
    config.TWILIO_NUMBER = "+15550000000"
    config.VOICEMAIL_TRANSCRIBE_MODE = "twilio"
    (_SANDBOX / "twilio_voicemail_cursor.json").unlink(missing_ok=True)

    d = twilio_voicemail._parse_date
    feed = [
        _rec("RE_B", "CA_B", d("Tue, 18 Aug 2015 17:31:00 +0000")),
        _rec("RE_A", "CA_A", d("Tue, 18 Aug 2015 17:30:00 +0000")),
    ]
    _real_list = twilio_voicemail.list_recordings
    _real_tr = twilio_voicemail.recording_transcript
    _real_from = twilio_voicemail.call_from
    twilio_voicemail.list_recordings = lambda *a, **k: feed
    twilio_voicemail.recording_transcript = lambda sid, tok, rsid, timeout=None: {
        "status": "completed", "text": f"message for {rsid}"}
    twilio_voicemail.call_from = lambda sid, tok, csid, timeout=None: "+15551112222"
    twilio_voicemail._save_cursor("2000-01-01T00:00:00+00:00", [])

    got: list[str] = []

    async def on_vm(vm):
        got.append(vm["recording_sid"])

    n1 = asyncio.run(twilio_voicemail.poll_once(on_vm, log=None))
    check("first poll dispatches all ready recordings, oldest-first", got == ["RE_A", "RE_B"])
    n2 = asyncio.run(twilio_voicemail.poll_once(on_vm, log=None))
    check("second poll dispatches nothing (deduped)", n2 == 0 and got == ["RE_A", "RE_B"])

    print("\n[9] Connector: a pending transcript is held, then delivered when it completes")
    (_SANDBOX / "twilio_voicemail_cursor.json").unlink(missing_ok=True)
    twilio_voicemail._save_cursor("2000-01-01T00:00:00+00:00", [])
    recent = datetime.now(timezone.utc) - timedelta(seconds=5)
    pending_feed = [_rec("RE_P", "CA_P", recent)]
    twilio_voicemail.list_recordings = lambda *a, **k: pending_feed
    state = {"status": "in-progress"}
    twilio_voicemail.recording_transcript = lambda sid, tok, rsid, timeout=None: (
        {"status": "completed", "text": "ready now"} if state["status"] == "completed"
        else {"status": "in-progress", "text": ""})
    got.clear()
    held = asyncio.run(twilio_voicemail.poll_once(on_vm, log=None))
    check("pending transcript is NOT dispatched yet", held == 0 and got == [])
    state["status"] = "completed"
    done = asyncio.run(twilio_voicemail.poll_once(on_vm, log=None))
    check("once the transcript completes, it is delivered", done == 1 and got == ["RE_P"])

    print("\n[10] Connector: a fresh cursor never replays the historical mailbox")
    (_SANDBOX / "twilio_voicemail_cursor.json").unlink(missing_ok=True)
    twilio_voicemail.list_recordings = lambda *a, **k: feed  # all dated 2015 (old)
    got.clear()
    n3 = asyncio.run(twilio_voicemail.poll_once(on_vm, log=None))
    check("fresh cursor skips recordings older than first-run stamp", n3 == 0 and got == [])

    twilio_voicemail.list_recordings = _real_list
    twilio_voicemail.recording_transcript = _real_tr
    twilio_voicemail.call_from = _real_from
    config.VOICEMAIL_ENABLED = False
    config.TWILIO_ACCOUNT_SID = config.TWILIO_AUTH_TOKEN = config.TWILIO_NUMBER = ""

    print("\n[11] Provisioning: answer TwiML is a one-shot record; <Play> when a clip is given")
    twiml = voicemail_provision.build_answer_twiml("Hello, leave a message.")
    check("greeting uses the British Polly voice by default",
          voicemail_provision.POLLY_BRITISH_MALE in twiml and "<Say" in twiml)
    check("records the caller with Twilio transcription on",
          "<Record" in twiml and 'transcribe="true"' in twiml)
    check("hangs up after recording (one-shot, no Gather loop)",
          "<Hangup/>" in twiml and "<Gather" not in twiml)
    check("no statusCallback (recordings are polled, not pushed)",
          "statusCallback" not in twiml.lower())
    played = voicemail_provision.build_answer_twiml(play_url="https://example.com/greet.wav")
    check("a hosted greeting clip is <Play>ed instead of spoken",
          "<Play>https://example.com/greet.wav</Play>" in played and "<Say" not in played)

    print("\n[12] Provisioning: set_number_voice_url POSTs VoiceUrl and hides the token")
    captured = {}

    class _WResp:
        def read(self): return json.dumps({"voice_url": "https://handler.twilio.com/twiml/EH1"}).encode()
        @property
        def status(self): return 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_open(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = (req.data or b"").decode("utf-8")
        captured["auth"] = req.headers.get("Authorization", "")
        return _WResp()

    _ur.urlopen = _fake_open
    res = voicemail_provision.set_number_voice_url("ACx", SECRET, "PN1",
                                                   "https://handler.twilio.com/twiml/EH1")
    _ur.urlopen = _real_urlopen
    check("set_number_voice_url returns ok", res.get("ok") is True)
    check("it targets the IncomingPhoneNumber PN SID", "/IncomingPhoneNumbers/PN1.json" in captured.get("url", ""))
    check("it POSTs the VoiceUrl", "VoiceUrl=" in captured.get("data", ""))
    check("the token rode only in the auth header, not the URL/body",
          SECRET not in captured.get("url", "") and SECRET not in captured.get("data", ""))
    check("rejects a non-http voice url", _voice_url_rejects())

    print("\n[13] Wizard HTML: no remote assets, no token in URL, secret is a password field")
    html = (ROOT / "web" / "setup-voicemail.html").read_text("utf-8")
    check("page loads as a file", "Voicemail Setup" in html)
    check("no remote script/img assets", 'src="http' not in html)
    for cdn in ("cdnjs", "unpkg", "jsdelivr", "googleapis.com/ajax"):
        check(f"no CDN reference ({cdn})", cdn not in html)
    check("no token ever placed in a query string", "?token=" not in html)
    check("JARVIS_TOKEN input is a password field", 'id="tokenInput" type="password"' in html)
    check("Auth Token input is a password field", 'id="twilioAuthToken" type="password"' in html)
    check("test button + route present",
          'id="testBtn"' in html and "/integrations/voicemail/test" in html)
    check("wire button + route present",
          'id="wireBtn"' in html and "/integrations/voicemail/wire" in html)
    check("one-click enable button + route present",
          'id="enableBtn"' in html and "/integrations/voicemail/enable" in html)
    check("done section present", 'id="doneSec"' in html)
    check("page explains carrier call-forwarding", "forward" in html.lower())
    check("page sells the private polling model",
          "poll" in html.lower() and "internet" in html.lower())
    check("no literal secret token value baked into the page", TOKEN not in html and SECRET not in html)

    print("\n[14] Doctor: a user who never enables voicemail passes (no WARN/FAIL)")
    off = onboarding._voicemail_check(types.SimpleNamespace(
        VOICEMAIL_ENABLED=False, TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_NUMBER=""))
    check("voicemail check is PASS when disabled", off["status"] == "PASS")
    warn = onboarding._voicemail_check(types.SimpleNamespace(
        VOICEMAIL_ENABLED=True, TWILIO_ACCOUNT_SID="", TWILIO_AUTH_TOKEN="", TWILIO_NUMBER=""))
    check("voicemail check WARNs when on but unconfigured (never FAIL)", warn["status"] == "WARN")

    print("\n[15] Registry: voicemail is read-only (write_capable False) and secret-free")
    entry = integration_registry.get("voicemail")
    check("registry has a voicemail entry", entry is not None)
    if entry:
        check("voicemail status is 'available' when unset", entry["status"] == "available")
        check("voicemail is not write_capable", entry["write_capable"] is False)
        check("voicemail is not delete_capable", entry["delete_capable"] is False)
        check("registry snapshot carries no secret",
              SECRET not in json.dumps(integration_registry.snapshot()))

    print("\n[16] Release stages the new files and the deny-guard still passes")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_release
    staged = make_release.staged_files()
    check("wizard page is staged", "web/setup-voicemail.html" in staged)
    check("connector module is staged", "twilio_voicemail.py" in staged)
    check("provisioning module is staged", "voicemail_provision.py" in staged)
    check("store + contacts modules staged",
          "voicemail_store.py" in staged and "voicemail_contacts.py" in staged)
    check("test suite is staged", "test_voicemail_setup.py" in staged)
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


def _voice_url_rejects() -> bool:
    """set_number_voice_url must refuse a non-http(s) URL before any network call."""
    try:
        voicemail_provision.set_number_voice_url("ACx", "tok", "PN1", "ftp://nope")
        return False
    except voicemail_provision.ProvisionError:
        return True


if __name__ == "__main__":
    sys.exit(main())
