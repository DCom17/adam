"""
Adam — external-action execution lane (the shared write path).

Calendar is the reference; the lane is generic. Proves:

  registry (external_actions.py):
    * is_known / available gate correctly off live config;
    * brain_proposable is True for calendar.create, False for email.send/linkedin.post;
    * execute() dispatches to the connector, and raises UnknownAction / ActionNotAvailable
      / ActionError (bad payload) as appropriate — never executing a disabled add-on.

  POST /integrations/actions/propose:
    * 403 without token; 400 unknown type; 409 when the add-on is disabled;
    * success parks a pending approval carrying the payload (nothing executes yet).

  POST /approvals/{id}/approve (execute-on-approve):
    * approving a calendar.create runs google_calendar.create_events and records
      execution=executed with the result;
    * a connector failure records execution=failed (approval stays approved, 200);
    * a plain (no-payload) approval still just approves — no execution;
    * no secret/token ever appears in a parked record or an approve response.

Run:  python test_external_actions.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "g" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_extact_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import google_calendar              # noqa: E402
import hunter                       # noqa: E402
import external_actions             # noqa: E402
import approvals                    # noqa: E402
import job_store                    # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

TOKEN = server.ADAM_TOKEN
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


def _enable_calendar(on: bool) -> None:
    config.CALENDAR_ENABLED = on
    config.CALENDAR_BRIDGE_URL = "https://script.google.com/macros/s/AKfake/exec" if on else ""
    config.CALENDAR_TOKEN = SECRET_TOKEN if on else ""


def _enable_hunter(on: bool) -> None:
    config.HUNTER_ENABLED = on
    config.HUNTER_BRIDGE_URL = "https://script.google.com/macros/s/AKfakehunter/exec" if on else ""
    config.HUNTER_TOKEN = SECRET_TOKEN if on else ""


def main() -> int:
    orig_cal = (config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN)
    orig_hunter = (config.HUNTER_ENABLED, config.HUNTER_BRIDGE_URL, config.HUNTER_TOKEN)
    orig_create = google_calendar.create_events
    try:
        print("\n[1] Registry gating")
        _enable_calendar(False)
        check("calendar.create is a known action", external_actions.is_known("calendar.create"))
        check("unknown action is not known", not external_actions.is_known("bogus.x"))
        check("calendar.create unavailable when calendar is off",
              not external_actions.available("calendar.create"))
        _enable_calendar(True)
        check("calendar.create available when configured",
              external_actions.available("calendar.create"))
        check("calendar.create is brain-proposable", external_actions.brain_proposable("calendar.create"))
        check("email.send is NOT brain-proposable", not external_actions.brain_proposable("email.send"))
        check("linkedin.post is NOT brain-proposable", not external_actions.brain_proposable("linkedin.post"))

        print("\n[2] execute() dispatch + guards")
        calls = {}
        def _fake_create(events):
            calls["events"] = events
            return {"created": [{"id": "evt_new", "summary": events[0].get("summary")}]}
        google_calendar.create_events = _fake_create

        res = external_actions.execute("calendar.create", {"events": [{"summary": "Dentist"}]})
        check("execute dispatched to the connector", calls.get("events") == [{"summary": "Dentist"}])
        check("execute returned the connector result", res["created"][0]["id"] == "evt_new")
        try:
            external_actions.execute("bogus.x", {})
            check("unknown -> UnknownAction", False)
        except external_actions.UnknownAction:
            check("unknown -> UnknownAction", True)
        try:
            external_actions.execute("calendar.create", {"events": []})
            check("bad payload -> ActionError", False)
        except external_actions.ActionError:
            check("bad payload -> ActionError", True)
        _enable_calendar(False)
        try:
            external_actions.execute("calendar.create", {"events": [{"summary": "x"}]})
            check("disabled add-on -> ActionNotAvailable", False)
        except external_actions.ActionNotAvailable:
            check("disabled add-on -> ActionNotAvailable", True)
        _enable_calendar(True)

        print("\n[3] POST /integrations/actions/propose")
        r = client.post("/integrations/actions/propose",
                        json={"action_type": "calendar.create", "payload": {"events": [{"summary": "x"}]}})
        check("requires token (403)", r.status_code == 403)
        r = client.post("/integrations/actions/propose", headers=AUTH,
                        json={"action_type": "bogus.x", "payload": {}})
        check("unknown type -> 400", r.status_code == 400)
        _enable_calendar(False)
        r = client.post("/integrations/actions/propose", headers=AUTH,
                        json={"action_type": "calendar.create", "payload": {"events": [{"summary": "x"}]}})
        check("disabled add-on -> 409", r.status_code == 409)
        _enable_calendar(True)
        r = client.post("/integrations/actions/propose", headers=AUTH,
                        json={"action_type": "calendar.create",
                              "payload": {"events": [{"summary": "Dentist", "start": "2026-07-01T09:00:00"}]},
                              "summary": "Add: Dentist"})
        check("valid proposal -> 200", r.status_code == 200)
        rec = r.json()
        check("parked as pending", rec["status"] == "pending")
        check("payload is stored on the record", rec["payload"]["events"][0]["summary"] == "Dentist")
        check("nothing executed yet", rec.get("execution") is None)
        check("propose response leaks no secret", SECRET_TOKEN not in r.text)
        approval_id = rec["id"]

        print("\n[4] Approve executes the action")
        r = client.post(f"/approvals/{approval_id}/approve", headers=AUTH)
        check("approve -> 200", r.status_code == 200)
        out = r.json()
        check("status approved", out["status"] == "approved")
        check("execution recorded as executed", out["execution"]["status"] == "executed")
        check("connector was actually called", calls.get("events", [{}])[0].get("summary") == "Dentist")
        check("execution result carried back", out["execution"]["result"]["created"][0]["id"] == "evt_new")
        check("approve response leaks no secret", SECRET_TOKEN not in r.text)

        print("\n[5] A connector failure records failed (approval stays approved)")
        def _boom(events):
            raise google_calendar.CalendarError("calendar bridge HTTP 500: Internal Server Error")
        google_calendar.create_events = _boom
        r = client.post("/integrations/actions/propose", headers=AUTH,
                        json={"action_type": "calendar.create", "payload": {"events": [{"summary": "y"}]}})
        aid2 = r.json()["id"]
        r = client.post(f"/approvals/{aid2}/approve", headers=AUTH)
        check("approve still 200 on connector failure", r.status_code == 200)
        out = r.json()
        check("status still approved", out["status"] == "approved")
        check("execution recorded as failed", out["execution"]["status"] == "failed")
        check("failure message is token-free", SECRET_TOKEN not in r.text)

        print("\n[6] A plain (no-payload) approval still just approves")
        plain = approvals.create(action_summary="manual yes/no", action_type="write")
        r = client.post(f"/approvals/{plain['id']}/approve", headers=AUTH)
        check("plain approve -> 200", r.status_code == 200)
        check("plain approval not executed", r.json().get("execution") is None)

        print("\n[7] Brain hook: <<ACTION>> blocks parse into pending proposals")
        _enable_calendar(True)
        reply = (
            "I'll add that for you.\n"
            '<<ACTION type="calendar.create" summary="Add Dentist">>\n'
            '{"events":[{"summary":"Dentist","start":"2026-07-04T09:00:00","end":"2026-07-04T10:00:00"}]}\n'
            "<<END_ACTION>>\nDone — staged for your approval."
        )
        cleaned, recs = server._extract_actions(reply)
        check("one action parked", len(recs) == 1)
        check("parked action is pending calendar.create",
              recs[0]["status"] == "pending" and recs[0]["action_type"] == "calendar.create")
        check("payload captured from the block", recs[0]["payload"]["events"][0]["summary"] == "Dentist")
        check("block stripped from the visible reply", "<<ACTION" not in cleaned and "END_ACTION" not in cleaned)

        # A non-proposable / disabled action is dropped, never parked.
        bad = '<<ACTION type="email.send" summary="x">>{"to":"a@b.c","subject":"s","body":"b"}<<END_ACTION>>'
        cleaned2, recs2 = server._extract_actions(bad)
        check("email.send is NOT auto-staged by the agent", len(recs2) == 0)
        check("non-proposable block still stripped", "<<ACTION" not in cleaned2)

        _enable_calendar(False)
        cleaned3, recs3 = server._extract_actions(
            '<<ACTION type="calendar.create" summary="x">>{"events":[{"summary":"y"}]}<<END_ACTION>>')
        check("disabled add-on -> agent block not parked", len(recs3) == 0)
        _enable_calendar(True)

        print("\n[8] Auto-run calendar (opt-in) + prompt-note wording + self-edit offer")
        import asyncio as _asyncio
        _orig_prefs_file = server.UI_PREFS_FILE
        server.UI_PREFS_FILE = _SANDBOX / "ui_prefs.json"  # never touch the real prefs
        try:
            check("auto_run_calendar defaults off", server._get_auto_run_calendar() is False)

            # /ui-prefs round-trips the new pref; a partial update preserves the other.
            client.post("/ui-prefs", headers=AUTH, json={"auto_apply_proposed": True})
            client.post("/ui-prefs", headers=AUTH, json={"auto_run_calendar": True})
            gp = client.get("/ui-prefs", headers=AUTH).json()
            check("/ui-prefs reports auto_run_calendar on", gp.get("auto_run_calendar") is True)
            check("/ui-prefs partial update did NOT reset auto_apply", gp.get("auto_apply_proposed") is True)
            client.post("/ui-prefs", headers=AUTH, json={"auto_apply_proposed": False, "auto_run_calendar": False})

            # The action note advertises calendar and uses the right wording for each posture.
            _enable_calendar(True)
            note_on = server._action_proposal_note(auto_run_calendar=True)
            note_off = server._action_proposal_note(auto_run_calendar=False)
            check("action note advertises calendar.create", "calendar.create" in note_on)
            check("auto-run note says it runs immediately", "IMMEDIATELY" in note_on)
            check("non-auto note waits for approval (no immediate-run claim)",
                  "approve" in note_off.lower() and "IMMEDIATELY" not in note_off)

            # The self-edit OFFER points at raising the capability tier to Unrestricted
            # (via the gear menu), not a phantom prompt or a removed standalone toggle.
            offer = server._self_edit_offer_note()
            check("self-edit offer points at the Unrestricted tier in the gear menu",
                  "Unrestricted" in offer and "gear" in offer.lower())
            check("self-edit offer disavows a folder/permission pop-up",
                  "folder access" in offer and "pop-up" in offer)

            # Auto-run executes a staged calendar action in place (status -> executed).
            ran = {}
            def _fake_create2(events):
                ran["events"] = events
                return {"created": [{"id": "evt_auto"}]}
            google_calendar.create_events = _fake_create2
            _cleaned8, recs8 = server._extract_actions(
                '<<ACTION type="calendar.create" summary="Add Fortnite">>'
                '{"events":[{"title":"Fortnite","start":"2026-07-04T17:00:00","end":"2026-07-04T18:00:00"}]}'
                '<<END_ACTION>>')
            check("staged one calendar action (pending)",
                  len(recs8) == 1 and recs8[0]["status"] == "pending")
            _asyncio.run(server._auto_run_calendar_actions(recs8))
            check("auto-run executed the calendar action",
                  ran.get("events", [{}])[0].get("title") == "Fortnite")
            check("auto-run marked the record executed", recs8[0]["status"] == "executed")

            # Auto-run never touches a non-calendar action (email draft/hunter sync stay pending).
            other = {"id": "x1", "action_type": "email.draft", "status": "pending", "payload": {}}
            _asyncio.run(server._auto_run_calendar_actions([other]))
            check("auto-run leaves non-calendar actions pending", other["status"] == "pending")
            # ...and the calendar auto-run must NOT sweep up a hunter.sync either.
            hpending = {"id": "h0", "action_type": "hunter.sync", "status": "pending", "payload": {"state": {}}}
            _asyncio.run(server._auto_run_calendar_actions([hpending]))
            check("calendar auto-run leaves hunter.sync pending", hpending["status"] == "pending")

            print("\n[8b] Auto-sync Hunter dashboard (opt-in, independent of calendar)")
            _enable_hunter(True)
            check("auto_run_hunter defaults off", server._get_auto_run_hunter() is False)
            # /ui-prefs round-trips auto_run_hunter without disturbing the calendar pref.
            client.post("/ui-prefs", headers=AUTH, json={"auto_run_calendar": True})
            client.post("/ui-prefs", headers=AUTH, json={"auto_run_hunter": True})
            gph = client.get("/ui-prefs", headers=AUTH).json()
            check("/ui-prefs reports auto_run_hunter on", gph.get("auto_run_hunter") is True)
            check("/ui-prefs partial update did NOT reset auto_run_calendar", gph.get("auto_run_calendar") is True)
            client.post("/ui-prefs", headers=AUTH, json={"auto_run_calendar": False, "auto_run_hunter": False})

            # The action note advertises hunter.sync and states the immediate-sync posture.
            hnote_on = server._action_proposal_note(auto_run_hunter=True)
            check("action note gives hunter.sync guidance", "SYNCING THE HUNTER DASHBOARD" in hnote_on)
            check("action note points at the dashboard workflow", "dashboard_update_workflow.md" in hnote_on)
            check("auto-sync note says it runs immediately", "IMMEDIATELY" in hnote_on)
            # Quests are staged with their planned bounty, never zeroed (broke 2026-07-01).
            check("action note says xp_value is the bounty, never 0", "NEVER 0" in hnote_on and "xp_value" in hnote_on)
            check("action note separates bounty from award", "awarding XP" in hnote_on)

            # Auto-run executes a staged hunter.sync in place (status -> executed), via the connector.
            hran = {}
            def _fake_hsync(payload):
                hran["payload"] = payload
                return {"ok": True}
            _orig_hsync = hunter.sync
            hunter.sync = _fake_hsync
            try:
                _cleaned9, recs9 = server._extract_actions(
                    '<<ACTION type="hunter.sync" summary="Sync board">>'
                    '{"state":{"total_xp":900},"daily_quests":[{"title":"D469"}]}'
                    '<<END_ACTION>>')
                check("staged one hunter.sync action (pending)",
                      len(recs9) == 1 and recs9[0]["status"] == "pending")
                _asyncio.run(server._auto_run_hunter_actions(recs9))
                check("auto-sync executed the hunter action",
                      hran.get("payload", {}).get("state", {}).get("total_xp") == 900)
                check("auto-sync marked the record executed", recs9[0]["status"] == "executed")
                # ...and hunter auto-run must NOT sweep up a calendar action.
                cpending = {"id": "c9", "action_type": "calendar.create", "status": "pending", "payload": {}}
                _asyncio.run(server._auto_run_hunter_actions([cpending]))
                check("hunter auto-run leaves calendar.create pending", cpending["status"] == "pending")
            finally:
                hunter.sync = _orig_hsync

            # TTS must never read a machine block aloud (voice mode speaks the WHOLE reply).
            spoken_in = (
                "Done, that's on your calendar.\n"
                '<<ACTION type="calendar.create" summary="x">>\n'
                '{"events":[{"title":"Fortnite","start":"2026-07-04T17:00:00","end":"2026-07-04T18:00:00"}]}\n'
                "<<END_ACTION>>"
            )
            spoken_out = server._clean_spoken_blocks(spoken_in)
            check("spoken strips the ACTION markers",
                  "ACTION" not in spoken_out and "END_ACTION" not in spoken_out)
            check("spoken strips the action JSON", "Fortnite" not in spoken_out and "{" not in spoken_out)
            check("spoken keeps the human sentence", "on your calendar" in spoken_out)
            prop_out = server._clean_spoken_blocks(
                'Sure.\n<<PROPOSE path="x.py" action="edit" risk="low" summary="s">>\ncode\n<<END_PROPOSE>>')
            check("spoken strips a PROPOSE/self-edit block",
                  "PROPOSE" not in prop_out and "code" not in prop_out and "Sure." in prop_out)

            # Integration: a PROPOSE block emitted in VOICE mode must be extracted
            # (stripped from the reply), not leaked into the chat. Mock the Claude
            # subprocess; the block targets an OUT-OF-ALLOWLIST path so it is refused
            # at create() and NOTHING is written anywhere (safe, no real vault write).
            import asyncio as __aio
            _orig_exec = __aio.create_subprocess_exec
            VOICE_REPLY = (
                "Saved that for you.\n"
                '<<PROPOSE path="../../outside_the_sandbox.txt" action="create" risk="low" summary="x">>\n'
                "content that must never reach the screen\n"
                "<<END_PROPOSE>>"
            )

            class _FakeProc:
                returncode = 0
                async def communicate(self):
                    return (json.dumps({"result": VOICE_REPLY, "session_id": "sV"}).encode(), b"")

            async def _fake_exec(*a, **k):
                return _FakeProc()

            __aio.create_subprocess_exec = _fake_exec
            try:
                out = _asyncio.run(server.run_claude("remember this", None, mode="voice"))
            finally:
                __aio.create_subprocess_exec = _orig_exec
            check("voice-mode PROPOSE block is stripped from the visible reply",
                  "<<PROPOSE" not in out["result"] and "END_PROPOSE" not in out["result"])
            check("voice-mode PROPOSE body never leaks to the screen",
                  "must never reach the screen" not in out["result"])
            check("voice-mode reply keeps the human sentence", "Saved that for you." in out["result"])
            check("voice-mode spoken text carries no block residue",
                  "PROPOSE" not in out["spoken"])
        finally:
            server.UI_PREFS_FILE = _orig_prefs_file
    finally:
        google_calendar.create_events = orig_create
        config.CALENDAR_ENABLED, config.CALENDAR_BRIDGE_URL, config.CALENDAR_TOKEN = orig_cal
        config.HUNTER_ENABLED, config.HUNTER_BRIDGE_URL, config.HUNTER_TOKEN = orig_hunter

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
