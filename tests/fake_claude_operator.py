"""A stand-in for `claude` in stream-json host mode, for the Operator tests.

Speaks just enough of the real protocol (verified against CLI 2.1.288): the
initialize handshake, --replay-user-messages echoes, can_use_tool control requests
for AskUserQuestion / ExitPlanMode, interrupt, and the terminal `result` event.
What a message does is keyed off its text:

  ASK      ask a question; reply with the answer it got
  PLAN     submit a plan; reply with whether it was approved
  SLOW     a 3 s "tool" that can be steered (a user message mid-run) or interrupted
  /clear   switch to a new session id
  DIE      exit 1 mid-turn with an error on stderr
  else     reply "echo: <text>"
"""

import json
import queue
import sys
import threading
import time

args = sys.argv[1:]
sid = args[args.index("--resume") + 1] if "--resume" in args else "fake-sid-1"
inbox: "queue.Queue[dict]" = queue.Queue()
_n = [0]


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reader():
    for line in sys.stdin:
        line = line.strip()
        if line:
            inbox.put(json.loads(line))
    inbox.put({"type": "_eof"})


def next_msg(timeout=None):
    try:
        return inbox.get(timeout=timeout)
    except queue.Empty:
        return None


def text(t):
    out({"type": "assistant", "parent_tool_use_id": None,
         "message": {"content": [{"type": "text", "text": t}]}})


def tool(name, inp):
    _n[0] += 1
    tid = f"toolu_{_n[0]}"
    out({"type": "assistant", "parent_tool_use_id": None,
         "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    return tid


def tool_result(tid, content):
    out({"type": "user", "parent_tool_use_id": None,
         "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}})


def result(t, subtype="success"):
    out({"type": "result", "subtype": subtype, "is_error": False, "result": t,
         "session_id": sid, "total_cost_usd": 0})


def ask_host(name, inp):
    rid = f"cli_{_n[0]}"
    out({"type": "control_request", "request_id": rid,
         "request": {"subtype": "can_use_tool", "tool_name": name, "input": inp}})
    while True:
        m = next_msg()
        if m is None or m.get("type") == "_eof":
            sys.exit(0)
        if m.get("type") == "control_response" and m["response"].get("request_id") == rid:
            return m["response"].get("response") or {}
        if m.get("type") == "control_request":
            handle_control(m)


def handle_control(m):
    sub = m["request"].get("subtype")
    resp = {}
    if sub == "initialize":
        resp = {"commands": [{"name": "compact", "description": "Compact the conversation"},
                             {"name": "save", "description": "Checkpoint skill",
                              "argumentHint": "[note]"}]}
    out({"type": "control_response",
         "response": {"subtype": "success", "request_id": m["request_id"], "response": resp}})
    return sub


def turn(m):
    global sid
    content = m["message"]["content"]
    out({"type": "user", "isReplay": True, "uuid": m.get("uuid"),
         "message": {"role": "user", "content": content}})
    out({"type": "system", "subtype": "init", "session_id": sid})
    if content == "/clear":
        sid = "fake-sid-cleared"
        result("")
    elif content == "ASK":
        tid = tool("AskUserQuestion", {"questions": [{"question": "Red or blue?", "header": "Color",
                   "multiSelect": False, "options": [{"label": "Red", "description": "warm"},
                                                    {"label": "Blue", "description": "cool"}]}]})
        r = ask_host("AskUserQuestion", {"questions": [{"question": "Red or blue?", "header": "Color",
                     "multiSelect": False, "options": [{"label": "Red", "description": "warm"},
                                                      {"label": "Blue", "description": "cool"}]}]})
        if r.get("behavior") == "allow":
            picked = (r.get("updatedInput") or {}).get("answers", {}).get("Red or blue?")
            tool_result(tid, f"answered {picked}")
            text(f"You picked {picked}.")
            result(f"You picked {picked}.")
        else:
            tool_result(tid, r.get("message", "denied"))
            result("No answer.")
    elif content == "PLAN":
        tid = tool("ExitPlanMode", {"plan": "1. Do the thing"})
        r = ask_host("ExitPlanMode", {"plan": "1. Do the thing"})
        if r.get("behavior") == "allow":
            tool_result(tid, "approved")
            result("Plan approved — working.")
        else:
            tool_result(tid, r.get("message", ""))
            result("Still planning: " + r.get("message", ""))
    elif content == "SLOW":
        tid = tool("Bash", {"command": "sleep 3"})
        end = time.time() + 3
        while time.time() < end:
            m2 = next_msg(timeout=0.1)
            if m2 is None:
                continue
            if m2.get("type") == "control_request":
                if handle_control(m2) == "interrupt":
                    tool_result(tid, "interrupted")
                    result("", subtype="error_during_execution")
                    return
            elif m2.get("type") == "user":
                out({"type": "user", "isReplay": True, "uuid": m2.get("uuid"),
                     "message": {"role": "user", "content": m2["message"]["content"]}})
                tool_result(tid, "cut short")
                t = "Steered: " + m2["message"]["content"]
                text(t)
                result(t)
                return
            elif m2.get("type") == "_eof":
                sys.exit(0)
        tool_result(tid, "slow done\n" + "x" * 100_000)
        text("Slow finished.")
        result("Slow finished.")
    elif content == "DIE":
        sys.stderr.write("fatal: something broke\n")
        sys.stderr.flush()
        sys.exit(1)
    else:
        text("echo: " + content)
        result("echo: " + content)


if sid.startswith("stale"):
    # Real CLI 2.1.288 with a gone --resume id: complains on stderr, emits an
    # error result, exits 0 — before ever answering the initialize handshake.
    sys.stderr.write(f"No conversation found with session ID: {sid}\n")
    sys.stderr.flush()
    out({"type": "result", "subtype": "error_during_execution", "is_error": True,
         "session_id": sid, "total_cost_usd": 0})
    sys.exit(0)

threading.Thread(target=reader, daemon=True).start()
while True:
    m = next_msg()
    if m is None or m.get("type") == "_eof":
        break
    if m.get("type") == "control_request":
        handle_control(m)
    elif m.get("type") == "user":
        turn(m)
