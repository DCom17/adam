"""
Adam — hands-free chat management (rename / new chat) tests.

Covers the two chat-control directives Adam can emit in a reply so the user can
organize the conversation by voice:
  * _extract_chat_control: pulls <<RENAME_CHAT: ...>> and <<NEW_CHAT[: title]>> out
    of a reply, returns the control dict, and always strips the markers; last
    RENAME_CHAT wins; a reply with neither returns None;
  * _strip_chat_control_markers + _clean_spoken_blocks never leave a directive in
    text bound for the screen/TTS;
  * _chat_control_note teaches both directives + the CONSENT rule for a new chat,
    and is appended to the system prompt in EVERY mode (voice / work / code);
  * run_claude end-to-end (stubbed subprocess): the returned dict carries
    chat_control, and the marker survives in neither result nor spoken;
  * a reply with no directive yields chat_control=None (no false positives);
  * job_store carries chat_control through the async/poll path (complete_job ->
    get_job -> to_wire), schema v2 with the chat_control column.

No real claude.exe is spawned (subprocess stubbed); the jobs DB is redirected to a
temp sandbox; audit + note writers are stubbed so the suite leaves dev data alone.

Run:  python test_chat_control.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
import job_store  # noqa: E402
import permissions  # noqa: E402

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


class _FakeJsonProc:
    """--output-format json path: everything arrives via communicate()."""
    returncode = 0
    pid = 4242

    def __init__(self, result_text: str):
        self._result = result_text

    async def communicate(self):
        payload = {"result": self._result, "session_id": "sid-cc-1"}
        return (json.dumps(payload).encode("utf-8"), b"")


class _FakeStreamProc:
    """--output-format stream-json path (code mode): NDJSON via stdout.readline()."""

    def __init__(self, result_text: str):
        self._lines = [
            json.dumps({"type": "result", "subtype": "success",
                        "result": result_text, "session_id": "sid-cc-1"}).encode() + b"\n",
        ]
        self.returncode: int | None = None
        self.pid = 4242
        self.stdout = self
        self.stderr = self

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""

    async def read(self):
        return b""   # stderr

    async def wait(self):
        self.returncode = 0
        return 0


def _run(result_text: str, mode: str = "voice") -> tuple[dict, dict]:
    """Run one run_claude turn whose (stubbed) subprocess returns `result_text`.
    Returns (captured, out). Stubs audit + the proposal-outcome note writer so dev
    data/logs stay untouched."""
    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        if "stream-json" in cmd:
            return _FakeStreamProc(result_text)
        return _FakeJsonProc(result_text)

    real_exec = asyncio.create_subprocess_exec
    real_audit = permissions.record_audit_event
    real_note = server._proposal_outcome_note
    asyncio.create_subprocess_exec = fake_exec
    permissions.record_audit_event = lambda ev: None
    server._proposal_outcome_note = lambda: ""
    try:
        out = asyncio.run(server.run_claude("hello", None, mode=mode))
    finally:
        asyncio.create_subprocess_exec = real_exec
        permissions.record_audit_event = real_audit
        server._proposal_outcome_note = real_note
    return captured, out


def _appended_prompt(cmd: list) -> str:
    i = cmd.index("--append-system-prompt")
    return cmd[i + 1]


def main() -> int:
    real_allow_code = config.AGENT_ALLOW_CODE_MODE
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_chatctrl_test_"))
    job_store.close()
    config.JOBS_DB = sandbox / "adam.db"
    job_store.init(config.JOBS_DB)

    print("\n[1] _extract_chat_control")
    d, c = server._extract_chat_control("Done. <<RENAME_CHAT: Daily Planning July 3>>")
    check("rename parsed", c == {"rename": "Daily Planning July 3"})
    check("rename marker stripped", "RENAME_CHAT" not in d and d == "Done.")

    d, c = server._extract_chat_control("Sure. <<NEW_CHAT>> Fresh one coming up.")
    check("new_chat (no title) parsed", c == {"new_chat": {}})
    check("new_chat marker stripped", "NEW_CHAT" not in d)

    d, c = server._extract_chat_control("<<NEW_CHAT: Groceries>>")
    check("new_chat titled parsed", c == {"new_chat": {"title": "Groceries"}})

    d, c = server._extract_chat_control("Nothing to organize here.")
    check("no directive -> None", c is None)
    check("plain text untouched", d == "Nothing to organize here.")

    _, c = server._extract_chat_control("<<RENAME_CHAT: A>> then <<RENAME_CHAT: B>>")
    check("last RENAME_CHAT wins", c == {"rename": "B"})

    _, c = server._extract_chat_control(
        "rename <<RENAME_CHAT: Trip>> and <<NEW_CHAT: Errands>>")
    check("both directives parsed",
          c == {"rename": "Trip", "new_chat": {"title": "Errands"}})

    long = "x" * 200
    _, c = server._extract_chat_control(f"<<RENAME_CHAT: {long}>>")
    check("rename title capped at 60", len(c["rename"]) == 60)

    print("\n[1b] _extract_chat_control — SET_MODE")
    d, c = server._extract_chat_control("Switching now. <<SET_MODE: operator>>")
    check("set_mode operator parsed", c == {"set_mode": "operator"})
    check("set_mode marker stripped", "SET_MODE" not in d and d == "Switching now.")
    _, c = server._extract_chat_control("<<SET_MODE: work>>")
    check("set_mode work aliases to operator", c == {"set_mode": "operator"})
    _, c = server._extract_chat_control("<<SET_MODE: voice>>")
    check("set_mode voice parsed", c == {"set_mode": "voice"})
    _, c = server._extract_chat_control("<<SET_MODE: code>>")
    check("set_mode code parsed", c == {"set_mode": "code"})
    _, c = server._extract_chat_control("<<SET_MODE: voice>> hmm <<SET_MODE: code>>")
    check("last SET_MODE wins", c == {"set_mode": "code"})
    _, c = server._extract_chat_control(
        "rename <<RENAME_CHAT: T>> and switch <<SET_MODE: operator>>")
    check("rename + set_mode together", c == {"rename": "T", "set_mode": "operator"})

    print("\n[2] markers never reach screen / TTS")
    stripped = server._strip_chat_control_markers(
        "a <<RENAME_CHAT: X>> b <<NEW_CHAT>> c <<SET_MODE: code>> d")
    check("_strip_chat_control_markers removes all three",
          "RENAME_CHAT" not in stripped and "NEW_CHAT" not in stripped
          and "SET_MODE" not in stripped)
    cleaned = server._clean_spoken_blocks("hi <<NEW_CHAT: Q>> there")
    check("_clean_spoken_blocks strips chat control", "NEW_CHAT" not in cleaned)

    print("\n[3] _chat_control_note content + consent")
    note = server._chat_control_note()
    check("note names RENAME_CHAT", "<<RENAME_CHAT:" in note)
    check("note names NEW_CHAT", "<<NEW_CHAT>>" in note)
    check("note requires consent for new chat",
          "REQUIRES the user's consent" in note
          and "AFTER" in note and "says yes" in note)
    check("note says cannot delete chats", "cannot delete" in note.lower())

    _real_allow = config.AGENT_ALLOW_CODE_MODE
    config.AGENT_ALLOW_CODE_MODE = True
    note_code = server._chat_control_note()
    check("note names SET_MODE", "<<SET_MODE:" in note_code)
    check("note offers code switch + FRESH warning when allowed",
          "<<SET_MODE: code>>" in note_code and "FRESH" in note_code)
    config.AGENT_ALLOW_CODE_MODE = False
    note_nocode = server._chat_control_note()
    check("note omits code switch when flag off", "<<SET_MODE: code>>" not in note_nocode)
    check("note still offers operator/voice switch",
          "<<SET_MODE: operator>>" in note_nocode and "<<SET_MODE: voice>>" in note_nocode)
    config.AGENT_ALLOW_CODE_MODE = _real_allow

    print("\n[4] note is in the system prompt for every mode")
    for m in ("voice", "work"):
        cap, out = _run("ok <<SPEAK>>ok<<SPEAK>>", mode=m)
        check(f"{m}: chat-control note appended",
              "ORGANIZING THIS CONVERSATION" in _appended_prompt(cap["cmd"]))
    config.AGENT_ALLOW_CODE_MODE = True
    cap, out = _run("ok <<SPEAK>>ok<<SPEAK>>", mode="code")
    check("code: chat-control note appended",
          "ORGANIZING THIS CONVERSATION" in _appended_prompt(cap["cmd"]))
    config.AGENT_ALLOW_CODE_MODE = real_allow_code

    print("\n[5] run_claude end-to-end extraction")
    _, out = _run("Named it for you. <<RENAME_CHAT: Daily Planning July 3>>")
    check("out carries chat_control", out.get("chat_control") == {"rename": "Daily Planning July 3"})
    check("result has no marker", "RENAME_CHAT" not in out["result"])
    check("spoken has no marker", "RENAME_CHAT" not in out["spoken"])

    _, out = _run("On it. <<NEW_CHAT>>")
    check("new_chat routed to chat_control", out.get("chat_control") == {"new_chat": {}})
    check("new_chat marker not spoken", "NEW_CHAT" not in out["spoken"])

    _, out = _run("Just a normal answer, sir.")
    check("no directive -> chat_control None", out.get("chat_control") is None)

    _, out = _run("Operator mode it is. <<SET_MODE: operator>>")
    check("set_mode routed to chat_control", out.get("chat_control") == {"set_mode": "operator"})
    check("set_mode marker not in result", "SET_MODE" not in out["result"])
    check("set_mode marker not spoken", "SET_MODE" not in out["spoken"])

    print("\n[6] job_store async/poll passthrough")
    check("schema is v2", job_store.SCHEMA_VERSION == 2)
    cols = {r[1] for r in job_store._conn().execute("PRAGMA table_info(jobs)").fetchall()}
    check("jobs has chat_control column", "chat_control" in cols)
    job_store.create_job("cc1", session_id="sid-cc-1", input_summary="hi")
    job_store.complete_job("cc1", result="ok", spoken="ok", mode="voice",
                           session_id="sid-cc-1", ts=111,
                           chat_control={"rename": "Trip planning"})
    rec = job_store.get_job("cc1")
    check("stored chat_control round-trips", rec["chat_control"] == {"rename": "Trip planning"})
    check("to_wire carries chat_control",
          job_store.to_wire(rec)["chat_control"] == {"rename": "Trip planning"})
    job_store.create_job("cc2", session_id="s2", input_summary="hi")
    job_store.complete_job("cc2", result="ok", spoken="ok", mode="voice",
                           session_id="s2", ts=112)
    check("no directive -> stored None", job_store.get_job("cc2")["chat_control"] is None)

    print("\n" + "=" * 50)
    print(f"  {_passed} passed, {_failed} failed")
    print("  sandbox:", sandbox)
    print("=" * 50)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
