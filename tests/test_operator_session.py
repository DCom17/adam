"""Operator mode — the live Claude Code session behind an Operator chat.

Drives operator_session against tests/fake_claude_operator.py (a real subprocess
speaking the CLI's stream-json host protocol), then the whole HTTP path
(/ask_async → /poll ask card → /answer, /steer, interrupt stop, /events) through
the real app with the jobs DB in a temp dir and every live-data writer stubbed.
No real claude.exe is spawned; nothing under data/ is touched.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADAM_TOKEN", "test-token-operator-000000000000000000")

import pytest  # noqa: E402

import config  # noqa: E402
import operator_session as ops  # noqa: E402
import server  # noqa: E402,F401 — imported FIRST: its module code points ops.LOG_DIR at the
               # real data dir, which the fixture below must then override

FAKE = str(Path(__file__).with_name("fake_claude_operator.py"))
TMP = Path(tempfile.mkdtemp(prefix="adam-operator-test-"))
LIVE_LOG_DIR = Path(config.DATA_DIR) / "operator_logs"


def _live_snapshot():
    return sorted(p.name for p in LIVE_LOG_DIR.glob("*")) if LIVE_LOG_DIR.exists() else []


@pytest.fixture(autouse=True)
def _isolated_operator_logs(monkeypatch):
    """Every test writes event logs + the slash-command cache to a temp dir, never the
    install's real data/operator_logs (v0.9.79 test runs polluted it with the fake
    CLI's commands) — and each test proves it left the real dir untouched."""
    before = _live_snapshot()
    monkeypatch.setattr(ops, "LOG_DIR", TMP / "operator_logs")
    monkeypatch.setattr(ops, "_COMMANDS", [])
    yield
    assert _live_snapshot() == before, "a test wrote into the real data/operator_logs"


def _argv():
    return [sys.executable, FAKE, "--output-format", "stream-json"]


async def _session(sid=None):
    s, _ = await ops.get_session(sid, _argv(), str(TMP), dict(os.environ), "sig-1")
    return s


async def _wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


# --- module level -------------------------------------------------------------------

def test_plain_turn_returns_result_and_logs_full_output():
    async def go():
        s = await _session()
        r = await s.run_turn("job-plain", "hello", timeout=20)
        assert r["result"] == "echo: hello"
        assert s.session_id == "fake-sid-1"
        assert ops.SESSIONS["fake-sid-1"] is s
        evs = ops.events_since("job-plain", 0)
        assert [e["t"] for e in evs] == ["text"]
        assert evs[0]["text"] == "echo: hello"
        # Second turn reuses the SAME live process.
        pid = s.proc.pid
        s2, reused = await ops.get_session("fake-sid-1", _argv(), str(TMP), {}, "sig-1")
        assert reused and s2 is s and s2.proc.pid == pid
        assert (await s.run_turn("job-plain2", "again", timeout=20))["result"] == "echo: again"
        await ops.shutdown_all()
    asyncio.run(go())


def test_slash_command_catalog_from_handshake():
    async def go():
        await _session()
        names = [c["name"] for c in ops.commands()]
        assert names == ["compact", "save"]
        assert ops.commands()[1]["hint"] == "[note]"
        await ops.shutdown_all()
    asyncio.run(go())
    # Cached to disk for the next server run.
    assert json.loads((ops.LOG_DIR / "_commands.json").read_text())[0]["name"] == "compact"


def test_question_parks_until_answered():
    async def go():
        s = await _session()
        t = asyncio.create_task(s.run_turn("job-ask", "ASK", timeout=20))
        assert await _wait_for(lambda: ops.pending_ask("job-ask") is not None)
        ask = ops.pending_ask("job-ask")
        assert ask["kind"] == "question"
        assert ask["questions"][0]["question"] == "Red or blue?"
        assert [o["label"] for o in ask["questions"][0]["options"]] == ["Red", "Blue"]
        assert not t.done()   # the turn really waits on the user
        assert ops.answer("job-ask", ask["id"], {"answers": {"Red or blue?": "Blue"}})
        r = await t
        assert r["result"] == "You picked Blue."
        kinds = [e["t"] for e in ops.events_since("job-ask", 0)]
        assert "ask" in kinds and "answer" in kinds
        assert ops.answer("job-ask", ask["id"], {"answers": {}}) is False  # closed now
        await ops.shutdown_all()
    asyncio.run(go())


def test_plan_reject_carries_feedback():
    async def go():
        s = await _session()
        t = asyncio.create_task(s.run_turn("job-plan", "PLAN", timeout=20))
        assert await _wait_for(lambda: ops.pending_ask("job-plan") is not None)
        ask = ops.pending_ask("job-plan")
        assert ask["kind"] == "plan" and ask["plan"] == "1. Do the thing"
        ops.answer("job-plan", ask["id"], {"approve": False, "feedback": "add tests"})
        r = await t
        assert r["result"].startswith("Still planning:") and "add tests" in r["result"]
        await ops.shutdown_all()
    asyncio.run(go())


def test_steer_mid_turn_lands_in_same_turn():
    async def go():
        s = await _session()
        t = asyncio.create_task(s.run_turn("job-steer", "SLOW", timeout=20))
        assert await _wait_for(lambda: any(e["t"] == "tool" for e in
                                           ops.events_since("job-steer", 0) or []))
        assert await ops.steer("job-steer", "do the other thing")
        r = await t
        assert r["result"] == "Steered: do the other thing"
        kinds = [e["t"] for e in ops.events_since("job-steer", 0)]
        assert "steer" in kinds
        assert await ops.steer("job-steer", "too late") is False
        await ops.shutdown_all()
    asyncio.run(go())


def test_interrupt_stops_turn_but_keeps_session():
    async def go():
        s = await _session()
        t = asyncio.create_task(s.run_turn("job-int", "SLOW", timeout=20))
        assert await _wait_for(lambda: any(e["t"] == "tool" for e in
                                           ops.events_since("job-int", 0) or []))
        assert await ops.interrupt("job-int")
        try:
            await t
            raise AssertionError("expected OperatorStopped")
        except ops.OperatorStopped:
            pass
        assert s.alive
        assert (await s.run_turn("job-int2", "still here?", timeout=20))["result"] == "echo: still here?"
        await ops.shutdown_all()
    asyncio.run(go())


def test_big_tool_output_is_capped_in_the_log():
    async def go():
        s = await _session()
        await s.run_turn("job-big", "SLOW", timeout=20)
        await ops.shutdown_all()
    asyncio.run(go())
    out = [e for e in ops.events_since("job-big", 0) if e["t"] == "out"][0]
    assert out["text"].startswith("slow done")
    assert "more characters not shown" in out["text"]   # 100,000 chars > 64 KiB cap
    assert len(out["text"]) < ops.TOOL_OUTPUT_CAP + 200


def test_clear_moves_session_to_new_id():
    async def go():
        s = await _session()
        r = await s.run_turn("job-clear", "/clear", timeout=20)
        assert r["session_id"] == "fake-sid-cleared"
        assert s.session_id == "fake-sid-cleared"
        assert ops.SESSIONS.get("fake-sid-cleared") is s
        assert "fake-sid-1" not in ops.SESSIONS
        await ops.shutdown_all()
    asyncio.run(go())


def test_process_death_fails_the_turn_with_its_words():
    async def go():
        s = await _session()
        try:
            await s.run_turn("job-die", "DIE", timeout=20)
            raise AssertionError("expected OperatorDied")
        except ops.OperatorDied as e:
            assert "something broke" in str(e)
        assert not s.alive
        assert "fake-sid-1" not in ops.SESSIONS
    asyncio.run(go())


def test_busy_session_refuses_a_second_turn():
    async def go():
        s = await _session()
        t = asyncio.create_task(s.run_turn("job-b1", "SLOW", timeout=20))
        await asyncio.sleep(0.3)
        try:
            await s.run_turn("job-b2", "hello", timeout=5)
            raise AssertionError("expected OperatorBusy")
        except ops.OperatorBusy:
            pass
        await t
        await ops.shutdown_all()
    asyncio.run(go())


def test_event_log_persists_after_the_turn():
    async def go():
        s = await _session()
        await s.run_turn("job-persist", "hello", timeout=20)
        await ops.shutdown_all()
    asyncio.run(go())
    ops.JOB_EVENTS.pop("job-persist", None)   # as after a server restart
    evs = ops.events_since("job-persist", 0)
    assert evs and evs[0]["text"] == "echo: hello"
    assert ops.events_since("../../etc", 0) is None   # path-safe job ids only


def test_idle_reap_and_session_cap():
    async def go():
        old_cap = ops.MAX_SESSIONS
        ops.MAX_SESSIONS = 2
        try:
            a = await _session("sid-a")
            b = await _session("sid-b")
            c = await _session("sid-c")   # evicts the least-recently-used idle one
            live = [s for s in ops.live_sessions() if s.alive]
            assert len(live) == 2 and a not in live and c in live
            c.last_used -= ops.IDLE_SECONDS + 5
            assert await ops.reap_idle() == 1
            assert not c.alive and b.alive
        finally:
            ops.MAX_SESSIONS = old_cap
            await ops.shutdown_all()
    asyncio.run(go())


def test_transcript_carry_over_between_folders(monkeypatch):
    home = TMP / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    src_cwd, dst_cwd = TMP / "workspace", TMP / "vault"
    src_cwd.mkdir(exist_ok=True)
    dst_cwd.mkdir(exist_ok=True)
    src = ops.project_dir_for(str(src_cwd))
    src.mkdir(parents=True)
    (src / "abc-123.jsonl").write_text('{"x":1}\n')
    assert ops.migrate_transcript("abc-123", str(dst_cwd)) is True
    dst = ops.project_dir_for(str(dst_cwd)) / "abc-123.jsonl"
    assert dst.read_text() == '{"x":1}\n'
    assert ops.migrate_transcript("abc-123", str(dst_cwd)) is True   # idempotent
    assert ops.migrate_transcript("../evil", str(dst_cwd)) is False
    assert ops.migrate_transcript("nope", str(dst_cwd)) is False
    # Munging matches Claude Code's own folder naming (verified on this machine).
    assert ops.project_dir_for(r"C:\Users\x\Jarvis Voice").name.endswith("Users-x-Jarvis-Voice")


# --- the whole HTTP path through the app -------------------------------------------

def test_http_operator_flow(monkeypatch):
    import job_store
    import permissions
    import server
    from fastapi.testclient import TestClient

    job_store.close()
    monkeypatch.setattr(config, "JOBS_DB", TMP / "adam.db")
    job_store.init(config.JOBS_DB)
    monkeypatch.setattr(config, "AGENT_ALLOW_CODE_MODE", True)
    monkeypatch.setattr(permissions, "record_audit_event", lambda ev: None)
    monkeypatch.setattr(server, "_store_last_result", lambda *a, **k: None)
    monkeypatch.setattr(server, "_send_push", lambda *a, **k: None)
    monkeypatch.setattr(server, "_proposal_outcome_note", lambda: "")   # writes ui_prefs
    pushes = []
    monkeypatch.setattr(server, "_send_ask_push", lambda q, chat: pushes.append((q, chat)))
    monkeypatch.setattr(server, "_last_seen", 0.0)   # app "in the background" -> question push fires
    prefs = {}
    monkeypatch.setattr(server, "_load_ui_prefs", lambda: dict(prefs))
    monkeypatch.setattr(server, "_save_ui_prefs", lambda p: prefs.update(p))
    if server.session_store is not None:
        monkeypatch.setattr(server.session_store, "record_session_mode", lambda *a, **k: None)
        monkeypatch.setattr(server.session_store, "get_session_mode", lambda *a, **k: None)
    monkeypatch.setattr(server, "VAULT_PATH", str(TMP))
    monkeypatch.setattr(server, "_PROMPT_FILE_SUPPORT", {server.CLAUDE_EXE: False})

    # The `with TestClient` context (needed so the live session's tasks share one
    # event loop across requests) would run the app's startup hooks: SMS/voicemail
    # pollers, the TTS supervisor. None of that belongs in a test — strip them.
    monkeypatch.setattr(server.app.router, "on_startup", [])
    monkeypatch.setattr(server.app.router, "on_shutdown", [])

    real_start = ops.OperatorSession.start

    async def fake_start(self):
        self.argv = [sys.executable, FAKE] + self.argv[1:]
        await real_start(self)
    monkeypatch.setattr(ops.OperatorSession, "start", fake_start)

    auth = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}
    with TestClient(server.app) as c:
        def ask(msg, sid=None, chat="chat-A"):
            r = c.post("/ask_async", headers=auth,
                       json={"message": msg, "session_id": sid, "mode": "code", "chat": chat})
            assert r.status_code == 200, r.text
            return r.json()["job_id"]

        def poll_until(jid, pred, timeout=15):
            end = time.time() + timeout
            while time.time() < end:
                p = c.get(f"/poll/{jid}", headers=auth).json()
                if pred(p):
                    return p
                time.sleep(0.1)
            raise AssertionError(f"poll timed out: {p}")

        # 1. a question card round-trip
        jid = ask("ASK")
        p = poll_until(jid, lambda p: p.get("ask"))
        assert p["status"] == "running" and p["ask"]["kind"] == "question"
        # A reloaded app can find this turn (and its open question) again.
        run = c.get("/operator/running", headers=auth).json()["running"]
        assert [(x["job_id"], x["chat"]) for x in run] == [(jid, "chat-A")]
        assert run[0]["ask"]["id"] == p["ask"]["id"]
        # App in the background -> one question push, routed to the chat.
        end = time.time() + 5
        while not pushes and time.time() < end:
            time.sleep(0.05)
        assert pushes == [("Red or blue?", "chat-A")]
        r = c.post(f"/jobs/{jid}/answer", headers=auth,
                   json={"ask_id": p["ask"]["id"], "answers": {"Red or blue?": "Red"}})
        assert r.status_code == 200, r.text
        p = poll_until(jid, lambda p: p["status"] == "done")
        assert p["result"].startswith("You picked Red") and p["session_id"] == "fake-sid-1"
        assert c.get("/operator/running", headers=auth).json()["running"] == []
        assert p["events_n"] >= 3
        evs = c.get(f"/jobs/{jid}/events?since=0", headers=auth).json()["events"]
        assert {"tool", "ask", "answer", "out", "text"} <= {e["t"] for e in evs}
        assert c.post(f"/jobs/{jid}/answer", headers=auth,
                      json={"ask_id": p.get("ask", {}).get("id", "x"),
                            "answers": {"a": "b"}}).status_code == 409

        # 2. steer, same live session
        jid = ask("SLOW", "fake-sid-1")
        poll_until(jid, lambda p: p.get("events_n"))
        r = c.post(f"/jobs/{jid}/steer", headers=auth, json={"message": "go left"})
        assert r.status_code == 200, r.text
        p = poll_until(jid, lambda p: p["status"] == "done")
        assert "Steered: go left" in p["result"]

        # 3. stop = interrupt; the chat keeps its session
        jid = ask("SLOW", "fake-sid-1")
        poll_until(jid, lambda p: p.get("events_n"))
        assert c.post(f"/jobs/{jid}/stop", headers=auth).status_code == 200
        p = poll_until(jid, lambda p: p["status"] != "running")
        assert p["status"] == "error" and "Stopped" in (p["error"] or "")
        assert ops.SESSIONS["fake-sid-1"].alive
        jid = ask("after stop", "fake-sid-1")
        p = poll_until(jid, lambda p: p["status"] == "done")
        assert p["result"] == "echo: after stop"

        # 4. one-time consent: stored server-side, reported by /ui-prefs
        u = c.get("/ui-prefs", headers=auth).json()
        assert u["operator_live"] is True and u["operator_consent"] is False
        assert c.post("/operator/consent", headers=auth).status_code == 200
        assert c.get("/ui-prefs", headers=auth).json()["operator_consent"] is True
        assert c.post("/operator/consent").status_code in (401, 403)
        assert c.get("/operator/running").status_code in (401, 403)

        # 5. slash-command catalog + auth
        cmds = c.get("/operator/commands", headers=auth).json()["commands"]
        assert [x["name"] for x in cmds] == ["compact", "save"]
        assert c.get("/operator/commands").status_code in (401, 403)
        assert c.post(f"/jobs/{jid}/steer", json={"message": "x"}).status_code in (401, 403)
        c.portal.call(ops.shutdown_all)   # on the loop that owns the sessions
    job_store.close()


def test_legacy_work_mode_folds_to_normal():
    import server
    assert server._normalize_mode("work") == "voice"
    assert server._normalize_mode("code") == "code"
    assert server._normalize_mode(None) == "voice"


def test_question_push_skipped_while_app_is_on_screen(monkeypatch):
    import server
    sent = []
    monkeypatch.setattr(server, "_send_ask_push", lambda q, chat: sent.append(q))
    monkeypatch.setattr(server, "_last_seen", time.time())   # app on screen

    async def go():
        server._operator_ask_hook("job-x", {"kind": "question", "questions": [{"question": "Q?"}]})
        await asyncio.sleep(0.2)
    asyncio.run(go())
    assert sent == []


def test_question_push_payload_is_its_own_kind(monkeypatch):
    import server
    payloads = []
    monkeypatch.setattr(server, "webpush", object())
    monkeypatch.setattr(server, "VAPID_PUBLIC_KEY", "k")
    monkeypatch.setattr(server, "VAPID_PRIVATE_PEM", Path(__file__))   # any existing file
    monkeypatch.setattr(server, "_load_subs", lambda: [{"endpoint": "https://x"}])
    monkeypatch.setattr(server, "_deliver_push", lambda payload, subs, **kw: payloads.append(json.loads(payload)))
    server._send_ask_push("Ship   it" + chr(10) + "now?", "chat-9")
    assert payloads == [{"kind": "ask", "title": "Adam",
                         "body": "Operator is asking: Ship it now?", "chat": "chat-9"}]
