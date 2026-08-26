"""Native pytest coverage for the untrusted-turn clamp.

An inbound SMS body is text this machine did not author, and SMS_WRAP hands it to
the agent with an instruction to ACT on it. That is the one input path where a
prompt injection arrives from a sender rather than from the person at the
keyboard, so it must not inherit whatever posture the box happens to be set to.

Pinned here:
  * untrusted=True forces the restricted spawn (sandbox cwd + --disallowedTools)
    even with AGENT_RESTRICT_TOOLS off, which is the legacy_direct configuration;
  * a trusted turn on the same config is NOT restricted (proves the clamp is what
    made the difference, not a coincidence of defaults);
  * nothing an untrusted turn proposes is auto-applied, even when the global
    auto-apply pref is on — the Unrestricted tier's write dirs include APP_ROOT;
  * _run_sms_job actually passes the flag (the clamp is worthless if the one
    caller that needs it forgets).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_untrusted_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "untrusted-test-token-" + "z" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import permissions  # noqa: E402
import proposed_changes  # noqa: E402
import server  # noqa: E402

PROPOSE_REPLY = (
    'Sure.\n<<PROPOSE path="note.md" action="create" risk="low" summary="a note">>\n'
    "hello\n<<END_PROPOSE>>\n"
)


class _FakeStreamProc:
    """Every mode spawns --output-format stream-json now, so the fake speaks NDJSON
    on stdout.readline() rather than handing back one blob from communicate()."""
    pid = 4242

    def __init__(self, result_text: str):
        self._lines = [json.dumps({
            "type": "result", "subtype": "success",
            "result": result_text, "session_id": "sid-untrusted-1",
        }).encode("utf-8") + b"\n"]
        self.returncode: int | None = None
        self.stdout = self
        self.stderr = self

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""

    async def read(self):
        return b""      # stderr

    async def wait(self):
        self.returncode = 0
        return 0


def _run(result_text: str = "ok", *, untrusted: bool = False,
         restrict_tools: bool = True, auto_apply: bool = False) -> dict:
    """One stubbed run_claude turn. Returns what the run captured: the argv, the
    subprocess kwargs, every audit event, and every approve() call."""
    captured: dict = {"audit": [], "approved": []}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["kw"] = kw
        return _FakeStreamProc(result_text)

    real = {
        "exec": asyncio.create_subprocess_exec,
        "audit": permissions.record_audit_event,
        "note": server._proposal_outcome_note,
        "approve": proposed_changes.approve,
        "get_auto": server._get_auto_apply,
        "restrict": config.AGENT_RESTRICT_TOOLS,
    }
    asyncio.create_subprocess_exec = fake_exec
    permissions.record_audit_event = lambda ev: captured["audit"].append(ev)
    server._proposal_outcome_note = lambda: ""
    proposed_changes.approve = lambda *a, **k: captured["approved"].append((a, k))
    server._get_auto_apply = lambda: auto_apply
    config.AGENT_RESTRICT_TOOLS = restrict_tools
    try:
        captured["out"] = asyncio.run(
            server.run_claude("hello", None, mode="voice", untrusted=untrusted)
        )
    finally:
        asyncio.create_subprocess_exec = real["exec"]
        permissions.record_audit_event = real["audit"]
        server._proposal_outcome_note = real["note"]
        proposed_changes.approve = real["approve"]
        server._get_auto_apply = real["get_auto"]
        config.AGENT_RESTRICT_TOOLS = real["restrict"]
    return captured


def test_untrusted_forces_restriction_on_a_legacy_direct_box():
    """restrict_tools=False is the legacy_direct posture: raw spawn, cwd=vault,
    no tool denial. An untrusted turn must refuse to run that way."""
    cap = _run(untrusted=True, restrict_tools=False)
    assert "--disallowedTools" in cap["cmd"], cap["cmd"]
    assert cap["kw"].get("cwd") == str(config.AGENT_WORKSPACE)


def test_trusted_turn_on_the_same_config_is_not_restricted():
    """The control case — without the clamp this config really is unrestricted,
    so the test above is proving the clamp and not a default."""
    cap = _run(untrusted=False, restrict_tools=False)
    assert "--disallowedTools" not in cap["cmd"]
    assert cap["kw"].get("cwd") != str(config.AGENT_WORKSPACE)


def test_untrusted_turn_never_auto_applies_even_with_auto_apply_on():
    cap = _run(PROPOSE_REPLY, untrusted=True, auto_apply=True)
    assert cap["approved"] == [], "an untrusted turn auto-applied a change"
    kinds = [e.get("action_type") for e in cap["audit"]]
    assert "untrusted_turn_proposals_held" in kinds, kinds


def test_trusted_turn_with_auto_apply_on_does_apply():
    """Control case again: the hold is the untrusted flag, not a broken loop."""
    cap = _run(PROPOSE_REPLY, untrusted=False, auto_apply=True)
    assert cap["approved"], "auto-apply did not fire on a trusted turn"


def test_sms_job_marks_its_turn_untrusted():
    """The clamp only matters if the SMS path actually sets it."""
    seen: dict = {}

    async def fake_run_claude(message, session_id, **kw):
        seen.update(kw)
        seen["message"] = message
        return {"result": "done", "session_id": "s1"}

    real_run = server.run_claude
    real_store = server._store_last_result
    real_push = server._send_push
    server.run_claude = fake_run_claude
    server._store_last_result = lambda *a, **k: None
    server._send_push = lambda *a, **k: None
    try:
        asyncio.run(server._run_sms_job("pay the invoice"))
    finally:
        server.run_claude = real_run
        server._store_last_result = real_store
        server._send_push = real_push

    assert seen.get("untrusted") is True, seen
    assert "pay the invoice" in seen["message"]
