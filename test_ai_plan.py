"""
Jarvis Voice Local — AI plan tests (two doors: subscription vs pay-as-you-go).

Covers the productized billing surface:
  * usage_store: per-day accumulation, month totals, summary, garbage tolerance;
  * spawn auth isolation: the claude.exe child env NEVER inherits
    ANTHROPIC_API_KEY implicitly — stripped in subscription mode (even when set
    globally), injected only when auth_mode=api_key and a key is stored;
  * cost meter feed: total_cost_usd from the CLI result lands in usage_store;
  * live model switch: the spawn reads config.VOICE_MODEL at call time;
  * budget governor: api_key mode + month spend >= budget -> 402 BEFORE spawn;
  * /ai-plan endpoints: token-gated, validation (mode/model/key/budget), the
    key-required rule for switching to api_key, per-door recommended model on a
    door switch, key never echoed back (key_set only).

No real claude.exe is spawned; usage DB redirected to a temp sandbox; settings/
.env writers and the audit writer are stubbed so dev data stays untouched.

Run:  python test_ai_plan.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import config

if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "b" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
import usage_store  # noqa: E402
import integration_config  # noqa: E402
import permissions  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = server.JARVIS_TOKEN
AUTH = {"Authorization": "Bearer " + TOKEN}
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


class _FakeJsonProc:
    returncode = 0
    pid = 4242

    async def communicate(self):
        payload = {"result": "Done. <<SPEAK>>Done, sir.<<SPEAK>>",
                   "session_id": "sid-plan-1", "total_cost_usd": 0.0421}
        return (json.dumps(payload).encode("utf-8"), b"")


def _run_voice(captured: dict):
    """One stubbed voice turn; captures cmd/env; audit + prefs writers stubbed."""

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["env"] = kw.get("env")
        return _FakeJsonProc()

    real_exec = asyncio.create_subprocess_exec
    real_audit = permissions.record_audit_event
    real_note = server._proposal_outcome_note
    asyncio.create_subprocess_exec = fake_exec
    permissions.record_audit_event = lambda ev: None
    server._proposal_outcome_note = lambda: ""
    try:
        return asyncio.run(server.run_claude("hello", None, mode="voice"))
    finally:
        asyncio.create_subprocess_exec = real_exec
        permissions.record_audit_event = real_audit
        server._proposal_outcome_note = real_note


def main() -> int:
    # Sandbox the usage DB before any rows are written.
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_aiplan_test_"))
    config.USAGE_DB = sandbox / "usage.db"
    usage_store.init()

    real_mode = config.AUTH_MODE
    real_key = config.ANTHROPIC_API_KEY
    real_model = config.VOICE_MODEL
    real_budget = config.API_BUDGET_MONTHLY_USD
    real_env_key = os.environ.pop("ANTHROPIC_API_KEY", None)

    print("\n[1] usage_store math")
    usage_store.record_turn(0.02)
    usage_store.record_turn(0.03)
    check("day_total accumulates", abs(usage_store.day_total() - 0.05) < 1e-9)
    check("month_total >= day_total", usage_store.month_total() >= 0.05 - 1e-9)
    s = usage_store.summary()
    check("summary has today/month", s["today_usd"] >= 0.05 - 1e-6 and s["today_turns"] == 2)
    usage_store.record_turn(-1)       # nonsense: ignored
    usage_store.record_turn("x")      # type: ignore[arg-type]
    check("garbage cost ignored", usage_store.summary()["today_turns"] == 2)
    usage_store.record_turn(1.0, day="2001-01-15")
    check("other-month rows don't leak into this month",
          abs(usage_store.month_total() - 0.05) < 1e-9)
    check("but land in their own month", abs(usage_store.month_total("2001-01") - 1.0) < 1e-9)

    print("\n[2] spawn auth isolation")
    # Subscription mode: even with a key stored AND one set globally, the child
    # env must not carry it — a stray key must never shadow the plan login.
    config.AUTH_MODE = "subscription"
    config.ANTHROPIC_API_KEY = "sk-ant-stored-key-000000000000"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-global-key-000000000000"
    cap: dict = {}
    _run_voice(cap)
    check("child env passed explicitly", isinstance(cap.get("env"), dict))
    check("subscription: key stripped from child env",
          "ANTHROPIC_API_KEY" not in (cap.get("env") or {}))
    os.environ.pop("ANTHROPIC_API_KEY", None)

    # api_key mode: the stored key (and only the stored key) is injected.
    config.AUTH_MODE = "api_key"
    config.API_BUDGET_MONTHLY_USD = 0  # gate off for this case
    cap2: dict = {}
    before = usage_store.summary()["today_turns"]
    _run_voice(cap2)
    check("api_key: stored key injected",
          (cap2.get("env") or {}).get("ANTHROPIC_API_KEY") == config.ANTHROPIC_API_KEY)
    check("turn cost recorded from total_cost_usd",
          usage_store.summary()["today_turns"] == before + 1)

    print("\n[3] live model switch")
    config.VOICE_MODEL = "claude-sonnet-5"
    cap3: dict = {}
    _run_voice(cap3)
    cmd = cap3.get("cmd") or []
    check("spawn uses config.VOICE_MODEL at call time",
          "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-sonnet-5")

    print("\n[4] budget governor")
    config.AUTH_MODE = "api_key"
    config.API_BUDGET_MONTHLY_USD = 0.01   # already spent > this above
    cap4: dict = {}
    code = None
    try:
        _run_voice(cap4)
    except HTTPException as e:
        code = e.status_code
    check("over budget -> 402", code == 402)
    check("over budget -> no spawn", "cmd" not in cap4)
    config.API_BUDGET_MONTHLY_USD = 0
    code = None
    try:
        _run_voice({})
    except HTTPException as e:
        code = e.status_code
    check("budget 0 disables the gate", code is None)

    print("\n[5] /ai-plan endpoints")
    # Stub the file writers + audit so no real settings.json/.env is touched.
    writes: list = []
    env_writes: list = []
    real_top = integration_config.set_settings_top_level
    real_envw = integration_config.set_env_var
    real_audit = permissions.record_audit_event
    integration_config.set_settings_top_level = (
        lambda k, v, **kw: writes.append((k, v)) or Path("settings.json"))
    integration_config.set_env_var = (
        lambda k, v, **kw: env_writes.append(k) or Path(".env"))
    permissions.record_audit_event = lambda ev: None
    try:
        config.AUTH_MODE = "subscription"
        config.ANTHROPIC_API_KEY = ""
        config.VOICE_MODEL = "claude-opus-4-8"

        r = client.get("/ai-plan")
        check("GET token-gated", r.status_code in (401, 403))
        r = client.get("/ai-plan", headers=AUTH)
        d = r.json()
        check("GET ok", r.status_code == 200 and d["mode"] == "subscription")
        check("GET lists models + recommendations",
              {m["id"] for m in d["models"]} >= {"claude-opus-4-8", "claude-sonnet-5"}
              and d["recommended"]["api_key"] == "claude-sonnet-5")
        check("GET never echoes a key", "api_key" not in d and "key" not in d)

        r = client.post("/ai-plan", headers=AUTH, json={"mode": "sonnet"})
        check("bad mode -> 400", r.status_code == 400)
        r = client.post("/ai-plan", headers=AUTH, json={"model": "gpt-4"})
        check("unknown model -> 400", r.status_code == 400)
        r = client.post("/ai-plan", headers=AUTH, json={"api_key": "not-a-key"})
        check("malformed key -> 400", r.status_code == 400)
        r = client.post("/ai-plan", headers=AUTH, json={"budget_usd": -5})
        check("negative budget -> 400", r.status_code == 400)
        r = client.post("/ai-plan", headers=AUTH, json={"mode": "api_key"})
        check("api_key mode without a key -> 400", r.status_code == 400)

        r = client.post("/ai-plan", headers=AUTH, json={
            "mode": "api_key", "api_key": "sk-ant-test-key-000000000000", "budget_usd": 12})
        d = r.json()
        check("door switch ok", r.status_code == 200 and d["mode"] == "api_key")
        check("key stored via .env writer, marked set",
              env_writes == ["ANTHROPIC_API_KEY"] and d["key_set"] is True)
        check("door switch applies recommended model", d["model"] == "claude-sonnet-5")
        check("budget persisted + applied",
              ("api_budget_monthly_usd", 12.0) in writes and d["budget_usd"] == 12.0)

        r = client.post("/ai-plan", headers=AUTH, json={"model": "claude-haiku-4-5"})
        check("explicit model override sticks (even Haiku)",
              r.status_code == 200 and r.json()["model"] == "claude-haiku-4-5")

        r = client.post("/ai-plan", headers=AUTH,
                        json={"mode": "subscription", "model": "claude-opus-4-8"})
        d = r.json()
        check("switch back to subscription with pinned model",
              r.status_code == 200 and d["mode"] == "subscription"
              and d["model"] == "claude-opus-4-8")
        check("settings writes were allow-listed keys only",
              set(k for k, _ in writes) <=
              {"auth_mode", "voice_model", "api_budget_monthly_usd"})
    finally:
        integration_config.set_settings_top_level = real_top
        integration_config.set_env_var = real_envw
        permissions.record_audit_event = real_audit
        config.AUTH_MODE = real_mode
        config.ANTHROPIC_API_KEY = real_key
        config.VOICE_MODEL = real_model
        config.API_BUDGET_MONTHLY_USD = real_budget
        if real_env_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = real_env_key

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
