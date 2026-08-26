"""The turn transport is stream-json for EVERY mode — so every failure the CLI can
report now arrives through one reader. This file pins that reader's ladder.

Why it exists: the v0.9.64 bug (a fresh install's first turn dying as "Connection
error — session reset" while the words "Please run /login" sat in output Adam had
thrown away) was a failure ladder reading one source. When voice and work moved off
plain json onto this path, that ladder became the ONLY one — a gap here is a gap for
every user on every turn, not just a code chat.

The cases below are the four shapes a failing run actually takes, each pinned on a
VOICE-mode turn (the mode that used to take the other branch):

  * not signed in, reported as a bare JSON object with no stream events at all
    (exit 1, stderr EMPTY) — the verbatim v0.9.64 shape;
  * not signed in, reported as a terminal `result` event with is_error;
  * the plan's usage window exhausted;
  * a --resume id the CLI can't find → re-run ONCE as a fresh session;
  * a crash that isn't JSON at all → its own words reach the user.

Plus the two positive pins: voice really does spawn stream-json, and a voice turn
really does fill JOB_PROGRESS (that feed IS the orb's activity bubbles).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_streamfail_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "stream-failure-test-token-" + "z" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import permissions  # noqa: E402
import server  # noqa: E402

from fastapi import HTTPException  # noqa: E402

OK_RESULT = "All done, sir."


class _FakeStreamProc:
    """A stream-json spawn: NDJSON on stdout via readline(), stderr via read()."""

    def __init__(self, lines: list[bytes], returncode: int = 0, stderr: bytes = b""):
        self._lines = list(lines)
        self._rc = returncode
        self._stderr = stderr
        self.returncode: int | None = None
        self.pid = 4242
        self.stdout = self
        self.stderr_stream = self
        self.stdin = None

    # stdout
    async def readline(self):
        return self._lines.pop(0) if self._lines else b""

    # stderr (server reads proc.stderr.read())
    async def read(self):
        return self._stderr

    async def wait(self):
        self.returncode = self._rc
        return self._rc

    def __getattr__(self, name):
        # `proc.stderr` must expose .read(); self doubles as both pipes.
        if name == "stderr":
            return self
        raise AttributeError(name)


def _lines(*events) -> list[bytes]:
    out = []
    for e in events:
        raw = json.dumps(e) if isinstance(e, (dict, list)) else str(e)
        out.append(raw.encode("utf-8") + b"\n")
    return out


def _ok_stream(session_id: str = "sid-ok-1", tool: bool = True):
    events = [{"type": "system", "subtype": "init"}]
    if tool:
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "vault/notes.md"}}]}})
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "python x.py",
                                                           "description": "run it"}}]}})
    events.append({"type": "result", "subtype": "success",
                   "result": OK_RESULT, "session_id": session_id})
    return _lines(*events)


def _run(procs, *, mode: str = "voice", session_id: str | None = None,
         job_id: str | None = None):
    """Run one turn against a queue of stubbed spawns. Returns
    (result_or_raised, captured) — captured holds every argv and a snapshot of
    JOB_PROGRESS taken while the turn was still live."""
    queue = list(procs)
    captured: dict = {"cmds": [], "progress": None}

    async def fake_exec(*cmd, **kw):
        captured["cmds"].append(list(cmd))
        proc = queue.pop(0)
        if job_id:
            # Snapshot the live feed as the reader drains this spawn — run_claude
            # clears JOB_PROGRESS in its finally block, so it can't be read after.
            original_wait = proc.wait

            async def wait_and_snapshot():
                captured["progress"] = list(server.JOB_PROGRESS.get(job_id, []))
                return await original_wait()

            proc.wait = wait_and_snapshot
        return proc

    real_exec = asyncio.create_subprocess_exec
    real_audit = permissions.record_audit_event
    real_note = server._proposal_outcome_note
    asyncio.create_subprocess_exec = fake_exec
    permissions.record_audit_event = lambda ev: None
    server._proposal_outcome_note = lambda: ""
    try:
        out = asyncio.run(server.run_claude("hello", session_id, mode=mode, job_id=job_id))
    finally:
        asyncio.create_subprocess_exec = real_exec
        permissions.record_audit_event = real_audit
        server._proposal_outcome_note = real_note
    return out, captured


# --- the flip itself ------------------------------------------------------------

def test_voice_mode_spawns_stream_json():
    """The change under test: voice no longer uses plain json."""
    _, cap = _run([_FakeStreamProc(_ok_stream())])
    cmd = cap["cmds"][0]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json", cmd
    assert "--verbose" in cmd, "-p + stream-json requires --verbose"


def test_voice_turn_feeds_the_activity_progress_buffer():
    """The feature: /poll's `progress` — the orb's bubbles — now exists in voice mode."""
    out, cap = _run([_FakeStreamProc(_ok_stream())], job_id="jt-voice-1")
    assert out["result"] == OK_RESULT
    assert cap["progress"], "a voice turn produced no activity events"
    # Real tool labels, in order: the file's name for Read, the command for Bash.
    assert cap["progress"] == ["Read: notes.md", "Bash: python x.py"], cap["progress"]


def test_live_registries_are_cleaned_after_a_voice_turn():
    _run([_FakeStreamProc(_ok_stream())], job_id="jt-voice-2")
    assert "jt-voice-2" not in server.JOB_PROGRESS
    assert "jt-voice-2" not in server.RUNNING_PROCS


# --- the failure ladder ----------------------------------------------------------

def test_not_signed_in_reported_as_a_bare_json_object_with_no_stream_events():
    """The verbatim v0.9.64 shape: exit 1, stderr EMPTY, the reason in a lone JSON
    object on stdout that is not a stream event. There is no result event to read,
    so a ladder that only inspected result events would see "" and fall through to
    a bare connection error — exactly the bug. It must reach the sign-in message."""
    proc = _FakeStreamProc(
        _lines({"subtype": "success", "is_error": True,
                "result": "Not logged in · Please run /login"}),
        returncode=1, stderr=b"",
    )
    with pytest.raises(HTTPException) as ei:
        _run([proc])
    assert ei.value.status_code == 502
    assert server.AUTH_REQUIRED_SENTINEL in ei.value.detail
    assert "/login" in ei.value.detail or "signed in" in ei.value.detail


def test_not_signed_in_reported_as_a_terminal_result_event():
    proc = _FakeStreamProc(
        _lines({"type": "system", "subtype": "init"},
               {"type": "result", "subtype": "error_during_execution", "is_error": True,
                "result": "Invalid API key · Please run /login"}),
        returncode=1, stderr=b"",
    )
    with pytest.raises(HTTPException) as ei:
        _run([proc])
    assert server.AUTH_REQUIRED_SENTINEL in ei.value.detail


def test_usage_limit_surfaces_the_friendly_limit_message():
    proc = _FakeStreamProc(
        _lines({"type": "result", "is_error": True,
                "result": "Claude AI usage limit reached|1786560000"}),
        returncode=1, stderr=b"",
    )
    with pytest.raises(HTTPException) as ei:
        _run([proc])
    assert server.LIMIT_SENTINEL in ei.value.detail
    assert "usage limit" in ei.value.detail.lower()


def test_gone_resume_id_reruns_once_as_a_fresh_session():
    """A voice chat whose sid aged out must still answer — one silent fresh retry."""
    dead = _FakeStreamProc(
        _lines({"type": "result", "is_error": True,
                "result": "No conversation found with session ID: abc-123"}),
        returncode=1, stderr=b"",
    )
    alive = _FakeStreamProc(_ok_stream(session_id="sid-fresh-9"))
    out, cap = _run([dead, alive], session_id="abc-123")
    assert out["result"] == OK_RESULT
    assert out["session_id"] == "sid-fresh-9"
    assert len(cap["cmds"]) == 2, "expected exactly one retry"
    assert "--resume" in cap["cmds"][0], cap["cmds"][0]
    assert "--resume" not in cap["cmds"][1], "the retry must not resume the dead session"


def test_gone_resume_id_does_not_loop_when_there_was_no_session_to_resume():
    """The retry passes session_id=None, so a second not-found can't recurse."""
    dead = _FakeStreamProc(
        _lines({"type": "result", "is_error": True,
                "result": "No conversation found with session ID: x"}),
        returncode=1, stderr=b"",
    )
    with pytest.raises(HTTPException) as ei:
        _run([dead], session_id=None)
    assert ei.value.status_code == 502


def test_a_non_json_crash_still_reaches_the_user_in_its_own_words():
    """An unanticipated crash must carry the CLI's actual text, not a generic error."""
    proc = _FakeStreamProc(
        [b"Traceback (most recent call last):\n", b"RuntimeError: node heap exhausted\n"],
        returncode=1, stderr=b"",
    )
    with pytest.raises(HTTPException) as ei:
        _run([proc])
    assert "heap exhausted" in ei.value.detail


def test_stderr_only_failures_still_work():
    """Nothing on stdout at all — the old stderr-based path must not have regressed."""
    proc = _FakeStreamProc([], returncode=1, stderr=b"claude: command failed hard\n")
    with pytest.raises(HTTPException) as ei:
        _run([proc])
    assert "command failed hard" in ei.value.detail


def test_exit_zero_with_no_result_event_says_what_it_saw():
    """Exit 0 but the stream ended without a result. The old code raised a bare
    "Claude returned no result"; it must carry the tail so it's diagnosable."""
    proc = _FakeStreamProc(_lines({"type": "system", "subtype": "init"}), returncode=0)
    with pytest.raises(HTTPException) as ei:
        _run([proc])
    assert ei.value.status_code == 502
    assert "no result" in ei.value.detail.lower()


def test_an_oversize_line_costs_a_bubble_not_the_turn():
    """A tool result too big for the reader's line limit must be skipped, not fatal —
    losing one activity event is survivable; losing a weekly review is not."""

    class _OversizeOnce(_FakeStreamProc):
        def __init__(self):
            super().__init__(_ok_stream())
            self._blew_up = False

        async def readline(self):
            if not self._blew_up:
                self._blew_up = True
                raise ValueError("Separator is not found, and chunk exceed the limit")
            return await super().readline()

    out, _ = _run([_OversizeOnce()])
    assert out["result"] == OK_RESULT
