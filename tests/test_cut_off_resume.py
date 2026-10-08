"""A turn cut off by an Adam restart must be resumable with a plain "continue".

2026-10-07: a day-planning turn was interrupted by a restart while the phone app was
closed; the app then dropped the chat's resume id and "Continue" landed in a blank
new session. The app now keeps the session on a cut-off turn; these cover the server
half — the next turn on that session is told it was cut off — and /turns/running,
which lets a reopened app re-attach to a Normal turn that's still working.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ADAM_TOKEN", "test-token-cutoff-0000000000000000000")

import config  # noqa: E402
import job_store  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app, raise_server_exceptions=False)
AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}


def _fresh_store():
    job_store.close()
    d = tempfile.mkdtemp(prefix="jvl_cutoff_test_")
    job_store.init(os.path.join(d, "adam.db"))


def teardown_module(_m):
    job_store.close()


def test_note_after_an_interrupted_turn():
    _fresh_store()
    job_store.create_job("j1", session_id="sid-a", input_summary="Also today I fixed the truck")
    job_store.recover_interrupted()          # what a restart does to a running job
    job_store.create_job("j2", session_id="sid-a", input_summary="Continue")
    note = server._cut_off_turn_note("sid-a", "j2")
    assert "CUT-OFF TURN" in note
    assert "Also today I fixed the truck" in note
    assert "continue" in note.lower()


def test_no_note_when_the_last_turn_finished():
    _fresh_store()
    job_store.create_job("j1", session_id="sid-b", input_summary="x")
    job_store.recover_interrupted()
    job_store.create_job("j2", session_id="sid-b", input_summary="y")
    job_store.complete_job("j2", result="ok", spoken="ok", mode="voice", session_id="sid-b", ts=1)
    job_store.create_job("j3", session_id="sid-b", input_summary="z")
    # The cut-off turn is no longer the latest one before j3 — nothing to resume.
    assert server._cut_off_turn_note("sid-b", "j3") == ""


def test_no_note_for_a_stopped_turn_or_no_session():
    _fresh_store()
    job_store.create_job("j1", session_id="sid-c", input_summary="x")
    job_store.cancel_job("j1")               # the user hit Stop — not a cut-off
    job_store.create_job("j2", session_id="sid-c", input_summary="y")
    assert server._cut_off_turn_note("sid-c", "j2") == ""
    assert server._cut_off_turn_note(None, "j2") == ""


def test_no_note_once_the_cut_off_is_stale():
    _fresh_store()
    job_store.create_job("j1", session_id="sid-d", input_summary="x")
    job_store.recover_interrupted()
    old = time.time() - server._CUT_OFF_WINDOW_S - 60
    job_store._conn().execute("UPDATE jobs SET created_at_ts=? WHERE job_id='j1'", (old,))
    job_store.create_job("j2", session_id="sid-d", input_summary="y")
    assert server._cut_off_turn_note("sid-d", "j2") == ""


def test_turns_running_lists_normal_turns():
    server.RUNNING_TURNS.clear()
    server.RUNNING_TURNS["jobN"] = {"chat": "chat-1", "session_id": "sid-n", "mode": "voice"}
    try:
        r = client.get("/turns/running", headers=AUTH)
        assert r.status_code == 200
        rows = [j for j in r.json()["running"] if j["job_id"] == "jobN"]
        assert rows == [{"job_id": "jobN", "operator": False, "chat": "chat-1",
                         "session_id": "sid-n", "mode": "voice"}]
        assert client.get("/turns/running").status_code in (401, 403)   # token required
    finally:
        server.RUNNING_TURNS.clear()


def test_recovery_leaves_a_live_servers_jobs_alone():
    """2026-10-07: a pytest run's startup recovery flipped two live phone turns to
    'interrupted' while the real server was still answering them."""
    import subprocess
    _fresh_store()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        job_store.create_job("live", session_id="s", input_summary="x", pid=other.pid)
        job_store.create_job("dead", session_id="s", input_summary="y", pid=999999)
        job_store.create_job("mine", session_id="s", input_summary="z", pid=os.getpid())
        got = {j["job_id"] for j in job_store.recover_interrupted()}
        assert got == {"dead", "mine"}
        assert job_store.get_job("live")["status"] == job_store.STATUS_RUNNING
    finally:
        other.kill()


def test_suite_never_opens_the_live_job_db():
    live = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "state", "adam.db")
    assert os.path.normcase(str(config.JOBS_DB)) != os.path.normcase(live)
    assert os.path.normcase(str(config.SESSIONS_DB)) != os.path.normcase(live.replace("adam.db", "sessions.db"))


# --- A reply nobody collected still reaches every device ---------------------
import asyncio  # noqa: E402
import session_store  # noqa: E402


def _fresh_sessions():
    session_store.init(os.path.join(tempfile.mkdtemp(prefix="jvl_sess_test_"), "sessions.db"))


def _tx(key):
    return [r for r in session_store.all_sessions() if r["key"] == key][0]


def test_append_turn_adds_question_and_reply_once():
    _fresh_sessions()
    session_store.upsert([{"key": "c1", "title": "t", "tx": '<div class="you">“earlier”</div>', "updated": 100}])
    assert session_store.append_turn("c1", "Remind me <8pm>", "Done, sir.", ts=500, sid="s1")
    r = _tx("c1")
    assert r["tx"].count('<div class="you">') == 2 and "Remind me &lt;8pm&gt;" in r["tx"]
    assert r["tx"].endswith('<div class="adam" style="white-space:pre-wrap">Done, sir.</div>')
    assert r["last_ts"] == 500 and r["sid"] == "s1" and r["updated"] > 100
    assert not session_store.append_turn("c1", "Remind me <8pm>", "Done, sir.", ts=500)   # same ts: no dup


def test_append_turn_skips_question_already_synced():
    _fresh_sessions()
    session_store.upsert([{"key": "c2", "tx": '<div class="you">“Hi there”</div>', "updated": 100}])
    assert session_store.append_turn("c2", "Hi there", "Hello.", ts=10)
    assert _tx("c2")["tx"].count('<div class="you">') == 1


def test_unclaimed_reply_written_only_when_no_device_collected_it():
    _fresh_store(); _fresh_sessions()
    old_wait = server.UNCLAIMED_REPLY_WAIT_S
    server.UNCLAIMED_REPLY_WAIT_S = 0
    try:
        session_store.upsert([{"key": "c3", "tx": "", "updated": 1}, {"key": "c4", "tx": "", "updated": 1}])
        job_store.create_job("u1", session_id="s"); job_store.complete_job("u1", result="R1", spoken="R1", mode="voice", session_id="s", ts=11)
        job_store.create_job("u2", session_id="s"); job_store.complete_job("u2", result="R2", spoken="R2", mode="voice", session_id="s", ts=12)
        job_store.mark_delivered("u2")            # the phone was watching and got it
        carried = "[My previous message never reached you — x]\n\nResume"
        asyncio.run(server._sync_unclaimed_reply("u1", "c3", carried, "R1", "R1", "s", 11))
        asyncio.run(server._sync_unclaimed_reply("u2", "c4", "Q2", "R2", "R2", "s", 12))
        assert "R1" in _tx("c3")["tx"] and "“Resume”" in _tx("c3")["tx"]
        assert _tx("c4")["tx"] == ""
    finally:
        server.UNCLAIMED_REPLY_WAIT_S = old_wait
