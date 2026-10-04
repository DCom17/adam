"""
Adam — Claude Code mode (opt-in, long-press escalation) tests.

Covers the flag-gated 'code' chat mode and its supervision rig:
  * config: agent_safety.operator_mode default-ON (D23) + code_mode_dirs +
    code_claude_timeout_seconds, surfaced in agent_safety_summary;
  * _normalize_mode accepts 'code' (and still folds junk to 'voice');
  * _extract_spoken treats code like work (<<SPEAK>> summary / first-sentence fallback);
  * run_claude REFUSES mode='code' when the flag is off (403, no spawn);
  * flag on -> the spawn is raw Claude Code: cwd=vault, NO --disallowedTools,
    --permission-mode bypassPermissions, stream-json + --verbose, code_mode_dirs
    as --add-dir, CODE_SYSTEM_PROMPT (no SAFETY MODE note), turn audited;
  * the stream reader turns tool_use events into a live activity feed and returns
    the terminal result event; a user stop raises TurnStopped;
  * POST /jobs/{id}/stop: 404 unknown, 409 not-running, 200 kills a real process,
    audited, token-gated; cancelled jobs wire-map to error "Stopped by user.";
  * /poll merges steps + the last 8 progress lines while running;
  * _run_job gives code turns CODE_CLAUDE_TIMEOUT_SECONDS, passes job_id, and
    records a user stop as cancelled (not failed);
  * a work-mode spawn is UNCHANGED (still sandboxed + tool-denied under restrict);
  * GET /ui-prefs exposes read-only code_mode_allowed; POST cannot flip it;
  * static UI + template contracts (hold bar CSS, stop button, caps, example defaults).

No real claude.exe is spawned (subprocess stubbed); the jobs DB is redirected to a
temp sandbox before any job rows are written; audit + ui-prefs writers are stubbed
so the suite leaves dev data untouched.

Run:  python test_code_mode.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
# These checks read the prompt off argv; pin the argv path (the prompt-file path
# is covered in tests/test_restore_and_checklist_ids.py).
server._PROMPT_FILE_SUPPORT[str(config.CLAUDE_EXE)] = False
import job_store  # noqa: E402
import permissions  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = server.ADAM_TOKEN
AUTH = {"Authorization": "Bearer " + TOKEN}
ROOT = Path(__file__).resolve().parent

client = TestClient(server.app)

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


# --- Spawn fakes ----------------------------------------------------------------

_RESULT_TEXT = "Edited the file. <<SPEAK>>Done, sir.<<SPEAK>>"


class _FakeStdin:
    def write(self, _data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _FakeStreamProc:
    """--output-format stream-json path: NDJSON lines via stdout.readline().
    The only spawn shape left — every mode streams now, so there is no json fake."""

    def __init__(self, lines: list[bytes], returncode: int = 0):
        self._lines = list(lines)
        self._rc = returncode
        self.returncode: int | None = None
        self.pid = 4242
        self.stdout = self
        self.stderr = self
        self.stdin = _FakeStdin()   # a long message rides stdin, like the real proc

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""

    async def read(self):
        return b""   # stderr

    async def wait(self):
        self.returncode = self._rc
        return self._rc


def _stream_lines(events) -> list[bytes]:
    out = []
    for e in events:
        out.append((json.dumps(e) if isinstance(e, dict) else e).encode("utf-8") + b"\n")
    return out


_DEFAULT_STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "python test_hunter.py"}}]}},
    {"type": "result", "subtype": "success", "result": _RESULT_TEXT, "session_id": "sid-test-1"},
]


FAKE_OPERATOR_CLI = str(ROOT / "tests" / "fake_claude_operator.py")


def _run(mode: str, job_id: str | None = None) -> tuple[dict, dict]:
    """Run one run_claude turn against a stubbed subprocess; return (captured, out).
    Stubs the audit + ui-prefs writers so dev data/logs stay untouched.

    An Operator ('code') turn rides a LIVE session (operator_session.py) that talks
    the CLI's stream-json host protocol — so instead of a canned stream it spawns
    tests/fake_claude_operator.py (a real subprocess speaking that protocol), with
    the argv the server built captured for the checks."""
    import operator_session
    captured: dict = {"audit": []}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["cwd"] = kw.get("cwd")
        return _FakeStreamProc(_stream_lines(_DEFAULT_STREAM))

    real_start = operator_session.OperatorSession.start

    async def fake_start(self):
        captured["cmd"] = list(self.argv)
        captured["cwd"] = self.cwd
        self.argv = [sys.executable, FAKE_OPERATOR_CLI] + self.argv[1:]
        await real_start(self)

    async def go():
        try:
            return await server.run_claude("hello there", None, mode=mode, job_id=job_id)
        finally:
            await operator_session.shutdown_all()

    real_exec = asyncio.create_subprocess_exec
    real_audit = permissions.record_audit_event
    real_note = server._proposal_outcome_note
    real_log_dir = operator_session.LOG_DIR
    if mode != "code":
        asyncio.create_subprocess_exec = fake_exec
    operator_session.OperatorSession.start = fake_start
    operator_session.LOG_DIR = Path(tempfile.mkdtemp(prefix="jvl_op_log_"))
    permissions.record_audit_event = lambda ev: captured["audit"].append(ev)
    server._proposal_outcome_note = lambda: ""   # writer — keep dev ui_prefs.json clean
    try:
        out = asyncio.run(go())
    finally:
        asyncio.create_subprocess_exec = real_exec
        operator_session.OperatorSession.start = real_start
        operator_session.LOG_DIR = real_log_dir
        permissions.record_audit_event = real_audit
        server._proposal_outcome_note = real_note
    return captured, out


def _flag(v: bool):
    config.AGENT_ALLOW_CODE_MODE = v


def main() -> int:
    real_flag = config.AGENT_ALLOW_CODE_MODE
    real_dirs = config.AGENT_CODE_MODE_DIRS

    # Redirect the jobs DB to a throwaway sandbox BEFORE any job rows are written
    # (per the isolate-data-in-destructive-tests rule).
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_codemode_test_"))
    job_store.close()
    config.JOBS_DB = sandbox / "adam.db"
    job_store.init(config.JOBS_DB)

    print("\n[1] config surface")
    check("AGENT_ALLOW_CODE_MODE is a bool", isinstance(config.AGENT_ALLOW_CODE_MODE, bool))
    check("AGENT_CODE_MODE_DIRS is a list", isinstance(config.AGENT_CODE_MODE_DIRS, list))
    check("code timeout default 3600", config.CODE_CLAUDE_TIMEOUT_SECONDS == 3600)
    summ = config.agent_safety_summary()
    check("summary exposes code_mode_allowed", "code_mode_allowed" in summ)
    check("summary exposes code_mode_dirs", "code_mode_dirs" in summ)
    ex = json.loads((ROOT / "settings.example.json").read_text(encoding="utf-8-sig"))
    check("example ships operator_mode: true (D23: Operator for all users)",
          ex.get("agent_safety", {}).get("operator_mode") is True)
    check("retired allow_code_mode key no longer shipped",
          "allow_code_mode" not in ex.get("agent_safety", {}))
    src = (ROOT / "config.py").read_text(encoding="utf-8")
    check("config reads operator_mode, default True",
          'bool(_as("operator_mode", True))' in src)
    check("config ignores the retired allow_code_mode key",
          '_as("allow_code_mode"' not in src)
    check("example ships code_mode_dirs: []",
          ex.get("agent_safety", {}).get("code_mode_dirs") == [])
    check("example ships code_claude_timeout_seconds",
          ex.get("code_claude_timeout_seconds") == 3600)

    print("\n[2] mode normalization")
    check("'code' -> code", server._normalize_mode("code") == "code")
    check("' CODE ' -> code", server._normalize_mode(" CODE ") == "code")
    check("legacy 'work' -> voice (folded into Normal)", server._normalize_mode("work") == "voice")
    check("junk -> voice", server._normalize_mode("root") == "voice")
    check("None -> voice", server._normalize_mode(None) == "voice")

    print("\n[3] spoken extraction in code mode (work-style summary)")
    d, s = server._extract_spoken("Long detail here. <<SPEAK>>Short summary.<<SPEAK>>", "code")
    check("SPEAK summary extracted", s == "Short summary.")
    check("marker never survives to display", "<<SPEAK>>" not in d)
    d, s = server._extract_spoken("First sentence. Second sentence.", "code")
    check("no tag -> first-sentence fallback", s == "First sentence.")

    print("\n[4] flag OFF -> code turns are refused (no spawn, no degrade)")
    _flag(False)
    try:
        _run("code")
        check("run_claude raised", False)
    except HTTPException as e:
        check("run_claude raised", True)
        check("403", e.status_code == 403)
        check("says Operator is turned off", "turned off" in str(e.detail))
    r = client.post("/ask", headers=AUTH, json={"message": "hi", "mode": "code"})
    check("/ask mode=code -> 403", r.status_code == 403)
    r = client.get("/ui-prefs", headers=AUTH)
    check("/ui-prefs reports code_mode_allowed false",
          r.status_code == 200 and r.json().get("code_mode_allowed") is False)

    print("\n[5] flag ON -> Operator is a live, full-power Claude Code session")
    _flag(True)
    config.AGENT_CODE_MODE_DIRS = [str(sandbox)]
    cap, out = _run("code", job_id="jt5")
    cmd = cap["cmd"]
    check("no --disallowedTools", "--disallowedTools" not in cmd)
    check("permission prompts bypassed",
          "--permission-mode" in cmd and cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions")
    check("streaming output", "--output-format" in cmd
          and cmd[cmd.index("--output-format") + 1] == "stream-json")
    check("--verbose present (required for stream-json)", "--verbose" in cmd)
    check("live session: stream-json INPUT (messages, steers, answers)",
          "--input-format" in cmd and cmd[cmd.index("--input-format") + 1] == "stream-json")
    check("questions + plan approval routed to the app (stdio host)",
          "--permission-prompt-tool" in cmd
          and cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio")
    check("steer acknowledgements on (--replay-user-messages)", "--replay-user-messages" in cmd)
    check("not a one-shot -p run", "-p" not in cmd)
    check("cwd is the vault (not the sandbox)", cap["cwd"] == server.VAULT_PATH)
    check("not the agent workspace", cap["cwd"] != str(config.AGENT_WORKSPACE))
    check("code_mode_dirs granted as --add-dir",
          any(cmd[i] == "--add-dir" and cmd[i + 1] == str(sandbox) for i in range(len(cmd) - 1)))
    prompt = cmd[cmd.index("--append-system-prompt") + 1]
    check("CODE_SYSTEM_PROMPT used", "Adam in Operator mode" in prompt)
    check("no SAFETY MODE (draft) note", "SAFETY MODE" not in prompt)
    check("no brain bootstrap note (code runs cwd=vault, loads CLAUDE.md natively)",
          "YOUR BRAIN" not in prompt)
    check("wire mode is 'code'", out.get("mode") == "code")
    check("reply came back through the live session", out.get("result") == "echo: hello there")
    check("session id learned from the session", out.get("session_id") == "fake-sid-1")
    audits = [a for a in cap["audit"] if a.get("action_type") == "code_mode_turn"]
    check("turn audited as code_mode_turn", len(audits) == 1)
    check("audit carries no message content",
          audits and "hello there" not in json.dumps(audits[0]))
    check("live registries cleaned after the turn",
          "jt5" not in server.RUNNING_PROCS and "jt5" not in server.JOB_PROGRESS)
    r = client.get("/ui-prefs", headers=AUTH)
    check("/ui-prefs reports code_mode_allowed true",
          r.status_code == 200 and r.json().get("code_mode_allowed") is True)
    config.AGENT_CODE_MODE_DIRS = real_dirs

    print("\n[6] legacy work mode now runs as Normal (two modes)")
    cap, out = _run("work")
    cmd = cap["cmd"]
    if config.AGENT_RESTRICT_TOOLS:
        check("work still tool-denied", "--disallowedTools" in cmd)
        check("work still sandboxed", cap["cwd"] == str(config.AGENT_WORKSPACE))
    else:
        check("work unrestricted (legacy_direct install)", "--disallowedTools" not in cmd)
        check("work cwd is vault (legacy_direct install)", cap["cwd"] == server.VAULT_PATH)
    check("work never gets bypassPermissions", "--permission-mode" not in cmd)
    # Streaming is NOT a code-mode privilege — it's the one turn transport. Work and
    # voice stream too, which is the only way a long operator turn (weekly review,
    # daily planning) can show live activity instead of a silent orb. What still
    # separates the modes is the sandbox, the tool denial and bypassPermissions above.
    check("work streams too (activity feed)",
          cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd)
    check("wire mode is 'voice' (Normal)", out.get("mode") == "voice")

    print("\n[6b] brain bootstrap note reconnects the vault in voice/work (not code)")
    # --add-dir grants READ access to the vault but Claude Code loads no CLAUDE.md memory
    # from it, so without an injected directive Adam never learns the user's brain (family,
    # identity, memory) exists. This is the regression fix for "Adam knows nothing about me".
    if config.AGENT_RESTRICT_TOOLS:
        capv, _ = _run("voice")
        vprompt = capv["cmd"][capv["cmd"].index("--append-system-prompt") + 1]
        check("voice prompt carries the brain bootstrap directive", "YOUR BRAIN" in vprompt)
        check("voice prompt orders reading the identity + people files",
              "user_profile.md" in vprompt and "people_and_relationships.md" in vprompt)
        check("voice prompt asserts the brain overrides dev/global instructions",
              "OVERRIDES" in vprompt)
        capw, _ = _run("work")
        wprompt = capw["cmd"][capw["cmd"].index("--append-system-prompt") + 1]
        check("work prompt carries the brain bootstrap directive too", "YOUR BRAIN" in wprompt)
    else:
        check("legacy_direct install: no bootstrap note (cwd=vault loads it natively)", True)

    print("\n[7] the flag is settings-only — POST /ui-prefs cannot flip it")
    _flag(False)
    r = client.post("/ui-prefs", headers=AUTH, json={"code_mode_allowed": True})
    # 200 = the field was ignored (pre-StrictModel behaviour); 422 = it was
    # rejected outright, which is what models.StrictModel's extra="forbid" now
    # does. Either satisfies this test — what matters is the assertion below,
    # that the flag itself is untouched. A 5xx would mean the POST blew up.
    check("POST rejected or ignored, never honoured", r.status_code in (200, 422))
    check("flag unchanged by POST", config.AGENT_ALLOW_CODE_MODE is False)
    _flag(True)

    print("\n[8] stream reader: tool events -> activity feed, result returned")
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "python test_hunter.py\nrm x"}}]}},
        "this line is not json",
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "C:/x/y/server.py"}}]}},
        {"type": "result", "subtype": "success", "result": "done", "session_id": "sid-8"},
    ]
    server.JOB_PROGRESS.pop("j8", None)
    data = asyncio.run(server._read_stream_result(
        _FakeStreamProc(_stream_lines(events)), "j8", timeout=30))
    check("result event returned", data.get("session_id") == "sid-8")
    prog = server.JOB_PROGRESS.get("j8") or []
    check("Bash activity captured (first line only)", "Bash: python test_hunter.py" in prog)
    check("Edit activity is basename only", "Edit: server.py" in prog)
    check("exactly the tool events, noise ignored", len(prog) == 2)
    server.JOB_PROGRESS.pop("j8", None)

    server.CANCELLED_JOBS.add("j8s")
    try:
        asyncio.run(server._read_stream_result(
            _FakeStreamProc([], returncode=1), "j8s", timeout=30))
        check("user stop -> TurnStopped", False)
    except server.TurnStopped:
        check("user stop -> TurnStopped", True)
    finally:
        server.CANCELLED_JOBS.discard("j8s")

    # A gone --resume session (CLI exits 1 with "No conversation found") must be
    # signalled distinctly so run_claude can recover, not raised as a raw failure.
    class _FakeStaleProc(_FakeStreamProc):
        def __init__(self):
            super().__init__([], returncode=1)

        async def read(self):
            return b"No conversation found with session ID: stale-xyz"

    try:
        asyncio.run(server._read_stream_result(_FakeStaleProc(), "j8g", timeout=30))
        check("gone resume -> SessionNotFound", False)
    except server.SessionNotFound:
        check("gone resume -> SessionNotFound", True)
    except Exception:
        check("gone resume -> SessionNotFound", False)

    print("\n[8b] Operator stale resume recovers with one fresh session")
    _flag(True)
    import operator_session
    spawns = {"argv": []}
    real_start = operator_session.OperatorSession.start

    async def fake_start(self):
        spawns["argv"].append(list(self.argv))
        self.argv = [sys.executable, FAKE_OPERATOR_CLI] + self.argv[1:]
        await real_start(self)

    async def go_stale():
        try:
            return await server.run_claude("hello", "stale-xyz", mode="code", job_id="jstale")
        finally:
            await operator_session.shutdown_all()

    real_audit = permissions.record_audit_event
    real_note = server._proposal_outcome_note
    real_log_dir = operator_session.LOG_DIR
    operator_session.OperatorSession.start = fake_start
    operator_session.LOG_DIR = Path(tempfile.mkdtemp(prefix="jvl_op_log_"))
    permissions.record_audit_event = lambda ev: None
    server._proposal_outcome_note = lambda: ""
    try:
        out = asyncio.run(go_stale())
    finally:
        operator_session.OperatorSession.start = real_start
        operator_session.LOG_DIR = real_log_dir
        permissions.record_audit_event = real_audit
        server._proposal_outcome_note = real_note
    check("stale Operator resume recovered (fresh session answered)",
          out.get("session_id") == "fake-sid-1" and out.get("result") == "echo: hello")
    check("spawned twice: original + one fresh retry", len(spawns["argv"]) == 2)
    check("first spawn resumed; retry dropped --resume",
          ["--resume" in a for a in spawns["argv"]] == [True, False])
    check("live registries cleaned after recovery",
          "jstale" not in server.RUNNING_PROCS and "jstale" not in server.JOB_PROGRESS)

    print("\n[9] POST /jobs/{id}/stop")
    check("no token -> 403", client.post("/jobs/x/stop").status_code == 403)
    check("unknown job -> 404", client.post("/jobs/nope/stop", headers=AUTH).status_code == 404)
    job_store.create_job("jdone", mode="code", session_id=None, input_summary="x", pid=1)
    job_store.complete_job("jdone", result="r", spoken="s", mode="code",
                           session_id="sid", ts=1)
    check("finished job -> 409", client.post("/jobs/jdone/stop", headers=AUTH).status_code == 409)
    # A real sleeper process, killed for real through the endpoint.
    job_store.create_job("jlive", mode="code", session_id=None, input_summary="x", pid=1)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    server.RUNNING_PROCS["jlive"] = sleeper   # duck-typed: pid/returncode/kill
    audits = []
    real_audit = permissions.record_audit_event
    permissions.record_audit_event = lambda ev: audits.append(ev)
    try:
        r = client.post("/jobs/jlive/stop", headers=AUTH)
    finally:
        permissions.record_audit_event = real_audit
    check("stop -> 200", r.status_code == 200)
    for _ in range(50):
        if sleeper.poll() is not None:
            break
        time.sleep(0.1)
    check("the process is actually dead", sleeper.poll() is not None)
    check("job flagged as user-stopped", "jlive" in server.CANCELLED_JOBS)
    check("audited as job_stopped", any(a.get("action_type") == "job_stopped" for a in audits))
    server.RUNNING_PROCS.pop("jlive", None)
    server.CANCELLED_JOBS.discard("jlive")
    # cancelled status is a clean wire error, not a crash
    job_store.create_job("jcanc", mode="code", session_id=None, input_summary="x", pid=1)
    job_store.cancel_job("jcanc")
    w = job_store.to_wire(job_store.get_job("jcanc"))
    check("cancelled wire-maps to error + 'Stopped by user.'",
          w["status"] == "error" and "Stopped by user" in (w.get("error") or ""))

    print("\n[10] /poll merges the live activity while running")
    job_store.create_job("jprog", mode="code", session_id=None, input_summary="x", pid=1)
    server.JOB_PROGRESS["jprog"] = [f"Bash: step {i}" for i in range(12)]
    r = client.get("/poll/jprog", headers=AUTH)
    b = r.json()
    check("running job polls fine", r.status_code == 200 and b["status"] == "running")
    check("steps = total tool calls", b.get("steps") == 12)
    check("progress = last 8", len(b.get("progress") or []) == 8
          and b["progress"][-1] == "Bash: step 11")
    server.JOB_PROGRESS.pop("jprog", None)
    r = client.get("/poll/jdone", headers=AUTH)
    check("finished job has no progress field", "progress" not in r.json())

    print("\n[11] _run_job: code turns get the long leash; a stop is 'cancelled'")
    calls: dict = {}

    async def fake_rc(message, session_id, timeout=None, mode="voice",
                      attachments=None, job_id=None, project=None, mode_switch=False):
        calls.update(timeout=timeout, mode=mode, job_id=job_id)
        return {"result": "r", "spoken": "s", "mode": mode,
                "session_id": "sid", "proposed_changes": []}

    real_rc = server.run_claude
    real_slr = server._store_last_result
    server.run_claude = fake_rc
    server._store_last_result = lambda *a, **k: None
    server._last_seen = time.time()   # foreground -> never pushes
    try:
        job_store.create_job("jrj", mode="code", session_id=None, input_summary="x", pid=1)
        asyncio.run(server._run_job("jrj", "msg", None, "code", None))
        check("code turn timeout = CODE_CLAUDE_TIMEOUT_SECONDS",
              calls["timeout"] == config.CODE_CLAUDE_TIMEOUT_SECONDS)
        check("job_id passed to run_claude", calls["job_id"] == "jrj")
        check("job completed", job_store.get_job("jrj")["status"] == "complete")
        job_store.create_job("jrw", mode="work", session_id=None, input_summary="x", pid=1)
        asyncio.run(server._run_job("jrw", "msg", None, "work", None))
        check("work turn keeps the normal cap",
              calls["timeout"] == server.ASYNC_CLAUDE_TIMEOUT_SECONDS)

        async def fake_rc_stop(*a, **k):
            raise server.TurnStopped()

        server.run_claude = fake_rc_stop
        job_store.create_job("jst", mode="code", session_id=None, input_summary="x", pid=1)
        asyncio.run(server._run_job("jst", "msg", None, "code", None))
        j = job_store.get_job("jst")
        check("user stop recorded as cancelled (not failed)", j["status"] == "cancelled")
        check("with the clean message", "Stopped by user" in (j.get("error") or ""))
    finally:
        server.run_claude = real_rc
        server._store_last_result = real_slr

    print("\n[12] static UI + template contracts")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    check("hold bar CSS present", ".mode.holding::before" in html)
    check("amber code style present", ".mode.code" in html)
    check("CLAUDE CODE label present", "CLAUDE CODE" in html)
    check("hold constant present", "CODE_HOLD_MS" in html)
    check("UI reads code_mode_allowed", "code_mode_allowed" in html)
    check("send path uses currentMode()", "mode: currentMode()" in html)
    check("new chats never inherit code",
          'Sessions.create({ mode: (workMode || codeMode) ? "work" : "voice" })' in html)
    check("stop button exists", 'id="stopTurn"' in html)
    check("stop wired to the endpoint", "/stop" in html and "refreshStopBtn" in html)
    check("code turns get the longer client cap", "CLIENT_CAP_CODE_MS" in html)
    check("stopped turns settle quietly", "stopped by user" in html.lower())
    check("live activity rendered from poll progress", "job.progress" in html)

    _flag(real_flag)
    config.AGENT_CODE_MODE_DIRS = real_dirs

    print(f"\n{'=' * 50}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 50}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
