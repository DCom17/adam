"""
Operator mode — one LIVE Claude Code session per Operator chat.

A normal turn is a one-shot `claude -p` run: it can't ask the user anything, can't
take input while it works, and its output collapses to a status line. An Operator
chat instead keeps one claude process alive and talks the CLI's own host protocol
(the one the Agent SDK speaks) over stdin/stdout JSON lines:

  stdin  ← {"type":"user", "uuid":…, "message":{…}}          a message or a steer
         ← {"type":"control_request", "request":{"subtype":"interrupt"}}
         ← {"type":"control_response", …}                     an answer to a question
  stdout → assistant / user(tool_result) / system / result    the full transcript
         → {"type":"control_request", "request":{"subtype":"can_use_tool"}}

Spoken directly — no SDK dependency (it pulls ~20 packages incl. pywin32, and the
updater never installs new requirements). Verified against CLI 2.1.288 on
2026-10-02; see docs/SPEC-OPERATOR-MODE.md for the probe results this relies on:

  * --replay-user-messages echoes each user message back, with OUR uuid, at the
    moment the CLI consumes it — so a turn is finished at a `result` only once every
    message we sent (the first one + any steers) has been echoed. A steer that lands
    mid-turn is folded into the same turn (one result); a late one starts another.
  * AskUserQuestion and ExitPlanMode still reach the host under bypassPermissions.
  * `interrupt` stops the running tool, ends the turn with subtype
    error_during_execution, and leaves the session alive with its context.

One job (an /ask_async turn) drives one session at a time. Everything the session
prints during that job lands in the job's event log, which /poll and
/jobs/{id}/events hand to the app as the live, full-output transcript.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("adam.voice.operator")   # under the app logger, so it lands in voice_server.log

# Tools whose permission request is really a question for the user. Everything else
# is full power (the session runs bypassPermissions) and is allowed on arrival.
ASK_TOOLS = ("AskUserQuestion", "ExitPlanMode")

TOOL_OUTPUT_CAP = 64 * 1024          # one tool result kept in the event log
JOB_EVENT_BYTES_CAP = 8 * 1024 * 1024  # past this, further tool output is clipped hard
JOB_EVENT_COUNT_CAP = 5000
CLIPPED_OUTPUT_CAP = 2 * 1024
STREAM_LINE_LIMIT = 64 * 1024 * 1024
CONTROL_TIMEOUT = 20.0

# Wired by server.py at import (kept as hooks so this module never imports server).
progress_hook: Callable[[str, str], None] | None = None   # (job_id, activity line)
activity_line: Callable[[str, dict], str] | None = None   # (tool name, input) -> line
ask_hook: Callable[[str, dict], None] | None = None       # (job_id, ask) — e.g. a push

SESSIONS: dict[str, "OperatorSession"] = {}   # live sessions by Claude session id
_UNKEYED: set["OperatorSession"] = set()      # spawned, no session id seen yet
JOB_SESSION: dict[str, "OperatorSession"] = {}  # running job -> its session
JOB_EVENTS: dict[str, list[dict]] = {}          # job -> event log (live + recent)
JOB_CHAT: dict[str, str] = {}                   # running job -> the app's chat key (reattach)
_RECENT_JOBS: list[str] = []
RECENT_JOBS_KEPT = 40
_COMMANDS: list[dict] = []
_reaper: asyncio.Task | None = None

# Settings (set by server from config; module defaults keep tests self-contained).
IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 4
ASK_TIMEOUT_SECONDS = 30 * 60
LOG_DIR: Path | None = None


class OperatorBusy(Exception):
    """The chat's live session is mid-turn; the new message belongs in a steer."""


class OperatorStopped(Exception):
    """The turn ended because the user stopped it (interrupt)."""


class OperatorDied(Exception):
    """The claude process exited mid-turn; carries its last words."""


def _now() -> float:
    return time.time()


def _clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n… [{len(text) - cap:,} more characters not shown]"


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(str(b.get("text") or ""))
                elif b.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(b)[:500])
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else json.dumps(content)[:TOOL_OUTPUT_CAP]


# --- Event log --------------------------------------------------------------------

def _log_path(job_id: str) -> Path | None:
    if LOG_DIR is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id or ""):
        return None
    return LOG_DIR / f"{job_id}.json"


def events_since(job_id: str, since: int = 0) -> list[dict] | None:
    """Events with n > since. Live/recent jobs come from memory; older ones from the
    log file written when the job ended. None = no such log."""
    evs = JOB_EVENTS.get(job_id)
    if evs is None:
        p = _log_path(job_id)
        if p is None or not p.exists():
            return None
        try:
            evs = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return [e for e in evs if e.get("n", 0) > since]


def event_count(job_id: str) -> int:
    evs = JOB_EVENTS.get(job_id)
    return evs[-1]["n"] if evs else 0


def _persist_log(job_id: str) -> None:
    p = _log_path(job_id)
    evs = JOB_EVENTS.get(job_id)
    if p is None or evs is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(evs, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001 — never fail a finished turn over its transcript
        log.warning("operator: could not persist event log for %s", job_id, exc_info=True)


def sweep_logs(max_age_seconds: float) -> int:
    """Drop event-log files older than the job-history TTL."""
    if LOG_DIR is None or not LOG_DIR.exists():
        return 0
    cutoff = _now() - max_age_seconds
    n = 0
    for f in LOG_DIR.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            pass
    return n


# --- Transcript carry-over --------------------------------------------------------

def claude_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def project_dir_for(cwd: str) -> Path:
    """Claude Code keeps a session's transcript under projects/<cwd with every
    non-alphanumeric character replaced by '-'>."""
    return claude_projects_dir() / re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))


def migrate_transcript(session_id: str, to_cwd: str) -> bool:
    """Make `session_id` resumable from `to_cwd` by copying its transcript into that
    cwd's project dir. A resume only looks in the current cwd's project dir, which is
    why switching a chat between modes (different folders) used to start it fresh.
    True when the transcript is (now) in place. Never raises."""
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
        return False
    try:
        dest_dir = project_dir_for(to_cwd)
        dest = dest_dir / f"{session_id}.jsonl"
        root = claude_projects_dir()
        srcs = [p for p in root.glob(f"*/{session_id}.jsonl") if p.parent != dest_dir]
        if not srcs:
            return dest.exists()
        src = max(srcs, key=lambda p: p.stat().st_mtime)
        if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
            return True
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        log.info("operator: carried session %s over to %s", session_id[:12], dest_dir.name)
        return True
    except Exception:  # noqa: BLE001
        log.warning("operator: transcript carry-over failed for %s", session_id[:12],
                    exc_info=True)
        return False


# --- Slash-command catalog --------------------------------------------------------

def commands() -> list[dict]:
    """The CLI's own command list (built-ins, custom commands, skills) from the last
    session's initialize handshake, cached to disk so the app's menu works before
    the first Operator turn of a server run."""
    if _COMMANDS:
        return list(_COMMANDS)
    p = (LOG_DIR / "_commands.json") if LOG_DIR else None
    if p and p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _COMMANDS[:] = data
        except Exception:  # noqa: BLE001
            pass
    return list(_COMMANDS)


def _store_commands(raw: Any) -> None:
    if not isinstance(raw, list):
        return
    out = []
    for c in raw:
        if isinstance(c, dict) and c.get("name"):
            out.append({"name": str(c["name"])[:80],
                        "description": str(c.get("description") or "")[:300],
                        "hint": str(c.get("argumentHint") or "")[:120]})
    if not out:
        return
    _COMMANDS[:] = out
    if LOG_DIR:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "_commands.json").write_text(json.dumps(out), encoding="utf-8")
        except OSError:
            pass


# --- The session ------------------------------------------------------------------

class _Turn:
    def __init__(self, job_id: str, deadline: float):
        self.job_id = job_id
        self.pending: set[str] = set()   # our user-message uuids not yet echoed
        self.done: asyncio.Future = asyncio.get_running_loop().create_future()
        self.deadline = deadline
        self.stopping = False
        self.bytes = 0


class OperatorSession:
    def __init__(self, argv: list[str], cwd: str, env: dict, signature: str,
                 cleanup: Callable[[], None] | None = None):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.signature = signature
        self.cleanup = cleanup
        self.proc: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self.turn: _Turn | None = None
        self.asks: dict[str, dict] = {}         # ask_id -> {request_id, kind, input, fut}
        self._ctrl: dict[str, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_tail: list[str] = []
        self.last_used = _now()
        self.dead = False

    # -- lifecycle --
    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LINE_LIMIT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._reader = asyncio.create_task(self._read_loop())
        resp = await self.control({"subtype": "initialize", "hooks": None},
                                  timeout=60.0)
        if isinstance(resp, dict):
            _store_commands(resp.get("commands"))

    @property
    def alive(self) -> bool:
        return (not self.dead and self.proc is not None
                and self.proc.returncode is None)

    @property
    def busy(self) -> bool:
        return self.turn is not None

    async def close(self) -> None:
        self.dead = True
        p = self.proc
        if p is not None and p.returncode is None:
            try:
                p.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(p.wait(), 5)
            except Exception:  # noqa: BLE001
                await _kill_tree(p)
        self._forget()

    def _forget(self) -> None:
        if self.session_id and SESSIONS.get(self.session_id) is self:
            SESSIONS.pop(self.session_id, None)
        _UNKEYED.discard(self)
        if self.cleanup:
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self.cleanup = None

    # -- wire --
    def _send(self, obj: dict) -> None:
        if not self.alive:
            raise OperatorDied("The Operator session is no longer running.")
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))

    async def _drain(self) -> None:
        try:
            await self.proc.stdin.drain()
        except Exception as e:  # noqa: BLE001
            raise OperatorDied(f"Lost the Operator session: {e}") from e

    async def control(self, request: dict, timeout: float = CONTROL_TIMEOUT) -> dict:
        rid = "req_" + uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._ctrl[rid] = fut
        self._send({"type": "control_request", "request_id": rid, "request": request})
        await self._drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._ctrl.pop(rid, None)

    async def send_user(self, text: str) -> str:
        uid = str(uuid.uuid4())
        self._send({"type": "user", "uuid": uid,
                    "message": {"role": "user", "content": text},
                    "parent_tool_use_id": None, "session_id": self.session_id or "default"})
        if self.turn is not None:
            self.turn.pending.add(uid)
        await self._drain()
        self.last_used = _now()
        return uid

    # -- event log --
    def _event(self, ev: dict) -> None:
        t = self.turn
        if t is None:
            return
        evs = JOB_EVENTS.setdefault(t.job_id, [])
        if len(evs) >= JOB_EVENT_COUNT_CAP:
            return
        ev["n"] = (evs[-1]["n"] + 1) if evs else 1
        ev["ts"] = int(_now() * 1000)
        evs.append(ev)

    # -- reader --
    async def _read_stderr(self) -> None:
        try:
            async for raw in self.proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line[:2000])
                    del self._stderr_tail[:-20]
        except Exception:  # noqa: BLE001
            pass

    async def _read_loop(self) -> None:
        tail: list[str] = []
        try:
            while True:
                try:
                    raw = await self.proc.stdout.readline()
                except ValueError:
                    continue   # an oversize line; skip it, keep the session
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace")
                tail.append(text[:2000])
                del tail[:-20]
                try:
                    m = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(m, dict):
                    try:
                        self._on_message(m)
                    except Exception:  # noqa: BLE001 — one odd event must not kill the session
                        log.warning("operator: event handling failed", exc_info=True)
        finally:
            self.dead = True
            try:
                await asyncio.wait_for(self.proc.wait(), 10)
            except Exception:  # noqa: BLE001
                pass
            try:   # the CLI's own words often arrive on stderr — let them land first
                await asyncio.wait_for(asyncio.shield(self._stderr_task), 3)
            except Exception:  # noqa: BLE001
                pass
            why = "\n".join(tail[-6:] + self._stderr_tail[-6:]).strip()
            for fut in self._ctrl.values():
                if not fut.done():
                    fut.set_exception(OperatorDied(why or "session ended"))
            for a in list(self.asks.values()):
                if not a["fut"].done():
                    a["fut"].cancel()
            self.asks.clear()
            t = self.turn
            if t is not None and not t.done.done():
                t.done.set_exception(
                    OperatorStopped() if t.stopping else OperatorDied(why))
            self._forget()

    def _on_message(self, m: dict) -> None:
        typ = m.get("type")
        sub = m.get("parent_tool_use_id") is not None
        if typ == "control_response":
            r = m.get("response") or {}
            fut = self._ctrl.get(r.get("request_id"))
            if fut and not fut.done():
                if r.get("subtype") == "error":
                    fut.set_exception(RuntimeError(str(r.get("error") or "control error")))
                else:
                    fut.set_result(r.get("response") or {})
            return
        if typ == "control_request":
            self._on_control_request(m)
            return
        if typ == "control_cancel_request":
            rid = m.get("request_id")
            for aid, a in list(self.asks.items()):
                if a["request_id"] == rid:
                    a["fut"].cancel()
                    self.asks.pop(aid, None)
                    self._event({"t": "ask_closed", "id": aid})
            return
        if typ == "system":
            st = m.get("subtype")
            if st == "init":
                self._learn_session_id(m.get("session_id"))
            elif st == "status" and m.get("permissionMode"):
                self._event({"t": "status", "mode": m.get("permissionMode")})
            elif st == "compact_boundary":
                self._event({"t": "status", "text": "Conversation compacted."})
            return
        if typ == "assistant":
            for b in (m.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    self._event({"t": "text", "text": b["text"], "sub": sub})
                elif b.get("type") == "tool_use":
                    name = str(b.get("name") or "tool")
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    raw = json.dumps(inp, ensure_ascii=False)
                    self._event({"t": "tool", "id": b.get("id"), "name": name,
                                 "input": inp if len(raw) <= TOOL_OUTPUT_CAP
                                 else {"_clipped": _clip(raw, TOOL_OUTPUT_CAP)},
                                 "sub": sub})
                    if self.turn and progress_hook:
                        line = activity_line(name, inp) if activity_line else name
                        progress_hook(self.turn.job_id, line)
            return
        if typ == "user":
            msg = m.get("message") or {}
            content = msg.get("content")
            uid = m.get("uuid")
            if m.get("isReplay") or (self.turn and uid in self.turn.pending):
                if self.turn and uid in self.turn.pending:
                    self.turn.pending.discard(uid)
                return
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        self._on_tool_result(b, sub)
            return
        if typ == "result":
            self._learn_session_id(m.get("session_id"))
            t = self.turn
            if t is None or t.done.done():
                return
            if t.pending:
                # A steer is still queued behind this result; the CLI runs it as the
                # next turn. Keep the job open until that one finishes too.
                self._event({"t": "status", "text": "Picking up your follow-up…"})
                return
            t.done.set_result(m)

    def _on_tool_result(self, b: dict, sub: bool) -> None:
        t = self.turn
        if t is None:
            return
        text = _tool_result_text(b.get("content"))
        cap = CLIPPED_OUTPUT_CAP if t.bytes > JOB_EVENT_BYTES_CAP else TOOL_OUTPUT_CAP
        text = _clip(text, cap)
        t.bytes += len(text)
        self._event({"t": "out", "id": b.get("tool_use_id"), "text": text,
                     "error": bool(b.get("is_error")), "sub": sub})

    def _learn_session_id(self, sid: Any) -> None:
        if not isinstance(sid, str) or not sid or sid == self.session_id:
            return
        old = self.session_id
        if old and SESSIONS.get(old) is self:
            SESSIONS.pop(old, None)   # /clear (or a fork) moved us to a new id
        self.session_id = sid
        _UNKEYED.discard(self)
        prior = SESSIONS.get(sid)
        if prior is not None and prior is not self:
            asyncio.create_task(prior.close())
        SESSIONS[sid] = self

    def _on_control_request(self, m: dict) -> None:
        rid = m.get("request_id")
        req = m.get("request") or {}
        if req.get("subtype") != "can_use_tool":
            self._send({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid,
                "error": f"Unsupported request: {req.get('subtype')}"}})
            return
        name = req.get("tool_name")
        inp = req.get("input") if isinstance(req.get("input"), dict) else {}
        if name not in ASK_TOOLS:
            self._respond(rid, {"behavior": "allow", "updatedInput": inp})
            return
        aid = uuid.uuid4().hex[:12]
        kind = "question" if name == "AskUserQuestion" else "plan"
        fut = asyncio.get_running_loop().create_future()
        ask = {"id": aid, "kind": kind, "request_id": rid, "input": inp, "fut": fut,
               "ts": int(_now() * 1000)}
        self.asks[aid] = ask
        pub = public_ask(ask)
        self._event({"t": "ask", **pub})
        if self.turn and ask_hook:
            try:
                ask_hook(self.turn.job_id, pub)
            except Exception:  # noqa: BLE001
                pass
        asyncio.create_task(self._await_answer(ask))

    def _respond(self, rid: str, response: dict) -> None:
        try:
            self._send({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": response}})
        except OperatorDied:
            pass

    async def _await_answer(self, ask: dict) -> None:
        inp = ask["input"]
        try:
            ans = await asyncio.wait_for(ask["fut"], ASK_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        except asyncio.TimeoutError:
            ans = {"timeout": True}
        finally:
            self.asks.pop(ask["id"], None)
        if ans.get("timeout"):
            resp = {"behavior": "deny",
                    "message": "The user did not answer in time. Stop and summarize "
                               "where things stand instead of guessing."}
            self._event({"t": "answer", "id": ask["id"], "text": "(no answer — timed out)"})
        elif ask["kind"] == "question":
            answers = ans.get("answers") or {}
            resp = {"behavior": "allow", "updatedInput": {**inp, "answers": answers}}
            self._event({"t": "answer", "id": ask["id"],
                         "text": "; ".join(f"{k} → {v}" for k, v in answers.items())})
        else:
            if ans.get("approve"):
                resp = {"behavior": "allow", "updatedInput": inp}
                self._event({"t": "answer", "id": ask["id"], "text": "Plan approved."})
            else:
                fb = str(ans.get("feedback") or "").strip()
                resp = {"behavior": "deny",
                        "message": "The user wants to keep planning — do not start yet."
                                   + (f" Their feedback: {fb}" if fb else "")}
                self._event({"t": "answer", "id": ask["id"],
                             "text": "Keep planning." + (f" {fb}" if fb else "")})
        self._respond(ask["request_id"], resp)
        try:
            await self._drain()
        except OperatorDied:
            pass

    # -- a turn --
    async def run_turn(self, job_id: str | None, text: str, timeout: float) -> dict:
        if self.busy:
            raise OperatorBusy()
        job = job_id or ("direct-" + uuid.uuid4().hex[:12])
        loop = asyncio.get_running_loop()
        self.turn = t = _Turn(job, loop.time() + timeout)
        JOB_SESSION[job] = self
        JOB_EVENTS[job] = []
        try:
            await self.send_user(text)
            while True:
                # Time spent waiting on the user's answer doesn't count against the
                # turn's deadline — a question left overnight is not a hung turn.
                if self.asks:
                    t.deadline += 1.0
                remaining = t.deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    result = await asyncio.wait_for(asyncio.shield(t.done), min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue
                if t.stopping:
                    raise OperatorStopped()
                return result
        finally:
            self.turn = None
            self.last_used = _now()
            JOB_SESSION.pop(job, None)
            JOB_CHAT.pop(job, None)
            for a in list(self.asks.values()):
                a["fut"].cancel()
            self.asks.clear()
            _persist_log(job)
            _RECENT_JOBS.append(job)
            while len(_RECENT_JOBS) > RECENT_JOBS_KEPT:
                JOB_EVENTS.pop(_RECENT_JOBS.pop(0), None)

    async def interrupt(self) -> None:
        if self.turn is not None:
            self.turn.stopping = True
        for a in list(self.asks.values()):
            a["fut"].cancel()
        try:
            await self.control({"subtype": "interrupt"})
        except Exception:  # noqa: BLE001
            # The CLI didn't answer — fall back to ending the process. The transcript
            # is on disk; the next message resumes it.
            if self.proc is not None:
                await _kill_tree(self.proc)


def public_ask(ask: dict) -> dict:
    inp = ask.get("input") or {}
    out = {"id": ask["id"], "kind": ask["kind"], "ts": ask.get("ts")}
    if ask["kind"] == "question":
        qs = []
        for q in inp.get("questions") or []:
            if not isinstance(q, dict):
                continue
            qs.append({"question": str(q.get("question") or ""),
                       "header": str(q.get("header") or ""),
                       "multi": bool(q.get("multiSelect")),
                       "options": [{"label": str(o.get("label") or ""),
                                    "description": str(o.get("description") or "")}
                                   for o in (q.get("options") or []) if isinstance(o, dict)]})
        out["questions"] = qs
    else:
        out["plan"] = str(inp.get("plan") or "")
    return out


async def _kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            await asyncio.to_thread(subprocess.run,
                                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                    capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        pass
    try:
        if proc.returncode is None:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


# --- Module API used by server.py -------------------------------------------------

async def get_session(session_id: str | None, argv: list[str], cwd: str, env: dict,
                      signature: str, cleanup: Callable[[], None] | None = None,
                      ) -> tuple[OperatorSession, bool]:
    """The live session for this chat, or a fresh one (resuming `session_id`).
    Returns (session, reused). A live session spawned under different settings
    (model, folders, prompt) is replaced — its transcript carries over by resume."""
    s = SESSIONS.get(session_id) if session_id else None
    if s is not None and s.alive and s.signature == signature:
        if cleanup:
            cleanup()
        return s, True
    if s is not None:
        if s.busy:
            if cleanup:
                cleanup()
            raise OperatorBusy()
        await s.close()
    await _make_room()
    full = list(argv) + (["--resume", session_id] if session_id else [])
    s = OperatorSession(full, cwd, env, signature, cleanup=cleanup)
    if session_id:
        s.session_id = session_id
        SESSIONS[session_id] = s
    else:
        _UNKEYED.add(s)
    try:
        await s.start()
    except BaseException:
        await s.close()
        raise
    _ensure_reaper()
    return s, False


def live_sessions() -> list[OperatorSession]:
    seen, out = set(), []
    for s in list(SESSIONS.values()) + list(_UNKEYED):
        if id(s) not in seen:
            seen.add(id(s))
            out.append(s)
    return out


async def _make_room() -> None:
    live = [s for s in live_sessions() if s.alive]
    idle = sorted((s for s in live if not s.busy and not s.asks), key=lambda s: s.last_used)
    while len(live) >= MAX_SESSIONS and idle:
        victim = idle.pop(0)
        live.remove(victim)
        await victim.close()


async def reap_idle() -> int:
    n = 0
    for s in live_sessions():
        if not s.alive:
            s._forget()
            continue
        if not s.busy and not s.asks and _now() - s.last_used > IDLE_SECONDS:
            await s.close()
            n += 1
    return n


def _ensure_reaper() -> None:
    global _reaper
    if _reaper is not None and not _reaper.done():
        return

    async def loop():
        while True:
            await asyncio.sleep(60)
            try:
                await reap_idle()
            except Exception:  # noqa: BLE001
                log.warning("operator: reaper pass failed", exc_info=True)
            if not live_sessions():
                return

    _reaper = asyncio.create_task(loop())


async def shutdown_all() -> None:
    for s in live_sessions():
        await s.close()


def session_for_job(job_id: str) -> OperatorSession | None:
    return JOB_SESSION.get(job_id)


def pending_ask(job_id: str) -> dict | None:
    s = JOB_SESSION.get(job_id)
    if s is None or not s.asks:
        return None
    first = min(s.asks.values(), key=lambda a: a.get("ts") or 0)
    return public_ask(first)


def answer(job_id: str, ask_id: str, payload: dict) -> bool:
    s = JOB_SESSION.get(job_id)
    a = s.asks.get(ask_id) if s else None
    if a is None or a["fut"].done():
        return False
    a["fut"].set_result(payload)
    return True


async def steer(job_id: str, text: str) -> bool:
    s = JOB_SESSION.get(job_id)
    if s is None or not s.busy or not s.alive:
        return False
    s._event({"t": "steer", "text": text})
    await s.send_user(text)
    return True


async def interrupt(job_id: str) -> bool:
    s = JOB_SESSION.get(job_id)
    if s is None:
        return False
    await s.interrupt()
    return True


async def close_session(session_id: str | None) -> bool:
    """Close this chat's live session if it's idle (a deliberate mode switch). A busy
    one is left alone — the switch takes effect at its next turn."""
    s = SESSIONS.get(session_id) if session_id else None
    if s is None or s.busy:
        return False
    await s.close()
    return True


def running_jobs() -> list[dict]:
    """Operator turns in flight, for an app that lost track of one (a reload, a phone
    that suspended the page): job id, chat key, session id, and any open question.
    A turn is listed from the moment it's accepted (JOB_CHAT), not only once its
    session has finished starting — a reload in those first seconds must find it too."""
    out, seen = [], set()
    for job, sess in list(JOB_SESSION.items()):
        if sess.turn is None or sess.turn.job_id != job:
            continue
        seen.add(job)
        out.append({"job_id": job, "chat": JOB_CHAT.get(job),
                    "session_id": sess.session_id, "ask": pending_ask(job),
                    "events_n": event_count(job)})
    for job, chat in list(JOB_CHAT.items()):
        if job not in seen:
            out.append({"job_id": job, "chat": chat, "session_id": None,
                        "ask": None, "events_n": 0})
    return out


def is_busy(session_id: str | None) -> bool:
    """True when this chat's live session is mid-turn (a new message must steer)."""
    s = SESSIONS.get(session_id) if session_id else None
    return bool(s is not None and s.alive and s.busy)
