"""Adam Plus gate: phone/remote access and Operator mode need a license after the trial.

Every case runs from a temp CWD with licensing's state files pointed there — licensing.py
resolves data/state/ relative to CWD, so a test from the repo root would read and rewrite
the owner's real license and trial clock (see the CWD trap in the paywall verification).
"""
import base64
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ADAM_TOKEN", "test-token-plus-gate-000000000000000000")

import config  # noqa: E402
import licensing  # noqa: E402
import plus_gate  # noqa: E402
import server  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}
PHONE = {"Host": "mypc.tail1234.ts.net", "X-Forwarded-For": "100.101.102.103"}
_PRIV = Ed25519PrivateKey.generate()
_PUB_HEX = _PRIV.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _mint(**extra) -> str:
    payload = {"p": "adam", "t": "lifetime", "e": "Founding Tester #001",
               "i": date.today().isoformat(), "o": "FOUNDER-001", **extra}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return "ADAM1-" + _b64(raw) + "." + _b64(_PRIV.sign(raw))


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Selling build, isolated state dir, Operator switched on in settings."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADAM_SELL_MODE", "1")
    monkeypatch.delenv("ADAM_LICENSE_KEY", raising=False)
    monkeypatch.setattr(licensing, "LICENSE_PUBLIC_KEY_HEX", _PUB_HEX)
    monkeypatch.setattr(licensing, "LICENSE_KEY_FILE", tmp_path / "data/state/license.key")
    monkeypatch.setattr(licensing, "TRIAL_FILE", tmp_path / "data/state/trial_start.txt")
    monkeypatch.setattr(config, "AGENT_ALLOW_CODE_MODE", True)
    (tmp_path / "data/state").mkdir(parents=True)

    class S:
        def expire_trial(self):
            licensing.TRIAL_FILE.write_text((date.today() - timedelta(days=45)).isoformat())

        def fresh_trial(self):
            licensing.TRIAL_FILE.write_text(date.today().isoformat())

        def license(self):
            licensing.LICENSE_KEY_FILE.write_text(_mint())
    return S()


def _pc():
    """A browser on the PC. The phone uses the same socket (Tailscale Serve connects from
    loopback) and differs only by the PHONE headers: its own Host, plus X-Forwarded-For."""
    return TestClient(server.app, base_url="http://127.0.0.1:8000",
                      client=("127.0.0.1", 50123), raise_server_exceptions=False)


_phone = _pc


# --- what counts as remote -------------------------------------------------------

class _Req:
    def __init__(self, host="127.0.0.1:8000", client="127.0.0.1", headers=None):
        self.headers = {"host": host, **{k.lower(): v for k, v in (headers or {}).items()}}
        self.client = type("C", (), {"host": client})()


@pytest.mark.parametrize("req,remote", [
    (_Req(), False),
    (_Req(host="localhost:8000"), False),
    (_Req(host="[::1]:8000", client="::1"), False),
    (_Req(host="mypc.tail1234.ts.net"), True),                       # Serve keeps Host
    (_Req(headers={"X-Forwarded-For": "100.64.0.5"}), True),         # any proxy hop
    (_Req(host="192.168.1.20:8000", client="192.168.1.33"), True),   # LAN
])
def test_is_remote(req, remote):
    assert plus_gate.is_remote(req) is remote


# --- phone / remote access -------------------------------------------------------

def test_expired_trial_blocks_the_phone_with_a_paywall_page(state):
    state.expire_trial()
    r = _phone().get("/", headers={**PHONE, "Accept": "text/html"})
    assert r.status_code == 402
    assert "ADAM PLUS" in r.text and licensing.BUY_URL in r.text
    assert "ACTIVATE" in r.text


def test_expired_trial_blocks_phone_api_calls_with_json(state):
    state.expire_trial()
    r = _phone().get("/ui-prefs", headers={**PHONE, **AUTH})
    assert r.status_code == 402
    d = r.json()["detail"]
    assert d["locked"] is True and d["feature"] == "phone" and d["buy_url"]


def test_locked_phone_can_still_reach_the_paywall_plumbing(state):
    state.expire_trial()
    c = _phone()
    assert c.get("/ping", headers=PHONE).status_code == 200
    assert c.get("/manifest.json", headers=PHONE).status_code in (200, 404)  # never 402
    assert c.get("/license", headers={**PHONE, **AUTH}).status_code == 200


def test_phone_can_activate_a_key_and_is_let_straight_in(state):
    state.expire_trial()
    c = _phone()
    r = c.post("/license", headers={**PHONE, **AUTH}, json={"key": _mint()})
    assert r.status_code == 200 and r.json()["valid"] is True
    assert c.get("/ui-prefs", headers={**PHONE, **AUTH}).status_code == 200


def test_the_pc_itself_is_never_gated(state):
    state.expire_trial()
    r = _pc().get("/ui-prefs", headers=AUTH)
    assert r.status_code == 200


def test_phone_works_during_the_trial(state):
    state.fresh_trial()
    assert _phone().get("/ui-prefs", headers={**PHONE, **AUTH}).status_code == 200


def test_phone_works_with_a_license_after_the_trial(state):
    state.expire_trial()
    state.license()
    assert _phone().get("/ui-prefs", headers={**PHONE, **AUTH}).status_code == 200


def test_dormant_build_gates_nothing(state, monkeypatch):
    state.expire_trial()
    monkeypatch.setenv("ADAM_SELL_MODE", "0")
    assert _phone().get("/ui-prefs", headers={**PHONE, **AUTH}).status_code == 200


# --- Operator mode ---------------------------------------------------------------

def test_ui_prefs_reports_operator_locked_after_trial(state):
    state.expire_trial()
    d = _pc().get("/ui-prefs", headers=AUTH).json()
    assert d["code_mode_allowed"] is True and d["operator_locked"] is True


def test_ui_prefs_operator_unlocked_with_license(state):
    state.expire_trial()
    state.license()
    assert _pc().get("/ui-prefs", headers=AUTH).json()["operator_locked"] is False


def test_operator_turn_refused_with_402_before_any_job(state):
    state.expire_trial()
    r = _pc().post("/ask_async", headers=AUTH, json={"message": "hi", "mode": "code"})
    assert r.status_code == 402
    assert r.json()["detail"]["feature"] == "operator"


def test_run_claude_refuses_a_locked_operator_turn(state):
    """Defense in depth: the job runner refuses too, whatever the entry point."""
    import asyncio
    from fastapi import HTTPException
    state.expire_trial()
    with pytest.raises(HTTPException) as e:
        asyncio.run(server.run_claude("hi", None, mode="code"))
    assert e.value.status_code == 402


def test_adam_wont_switch_a_locked_install_into_operator(state):
    state.expire_trial()
    _, control = server._extract_chat_control("Switching. <<SET_MODE: operator>>")
    assert not (control or {}).get("set_mode")
    assert "Adam Plus" in server._chat_control_note()


def test_adam_switches_into_operator_when_licensed(state):
    state.expire_trial()
    state.license()
    _, control = server._extract_chat_control("Switching. <<SET_MODE: operator>>")
    assert control["set_mode"] == "code"
