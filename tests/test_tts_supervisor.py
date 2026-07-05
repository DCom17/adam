"""Native pytest coverage for the TTS supervisor (Adam-voice self-heal):

- recover() only ever acts on a local tts_url with a fully installed voice
  package (venv + complete model files — size floors enforced);
- one spawn attempt per cooldown window (no spawn-loop on a broken install);
- a service that answers /ping is never respawned;
- a spawn failure is swallowed (self-heal must never take a reply down);
- a failing /speak schedules recover() in the background and still 502s
  promptly so the app can fall back to the browser voice.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Sandbox config BEFORE anything imports it: clean example defaults, no live
# settings.json/.env from the dev machine.
os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_ttssup_cfg_"))

import config  # noqa: E402

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "ttssup-test-token-" + "x" * 32
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import tts_supervisor  # noqa: E402

AUTH = {"Authorization": f"Bearer {config.ADAM_TOKEN}"}


def _reset_cooldown():
    tts_supervisor._last_attempt = 0.0


def test_is_local_tts_url(monkeypatch):
    monkeypatch.setattr(config, "TTS_URL", "http://127.0.0.1:8001/tts")
    assert tts_supervisor.is_local_tts_url()
    monkeypatch.setattr(config, "TTS_URL", "http://localhost:8001/tts")
    assert tts_supervisor.is_local_tts_url()
    monkeypatch.setattr(config, "TTS_URL", "http://192.168.1.50:8001/tts")
    assert not tts_supervisor.is_local_tts_url()
    monkeypatch.setattr(config, "TTS_URL", "not a url")
    assert not tts_supervisor.is_local_tts_url()


def test_runnable_requires_complete_install(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert not tts_supervisor.runnable()  # nothing there at all

    d = tmp_path / "scripts" / "tts_server"
    d.mkdir(parents=True)
    py = tts_supervisor._venv_python()
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    (d / "tts_server.py").write_text("# stub", encoding="utf-8")
    (d / "kokoro-v1.0.onnx").write_bytes(b"tiny")   # partial download
    (d / "voices-v1.0.bin").write_bytes(b"tiny")
    assert not tts_supervisor.runnable()  # size floors must reject partials

    # Grow the model files to the real floors without writing real bytes.
    with open(d / "kokoro-v1.0.onnx", "wb") as f:
        f.truncate(201 * 1024 * 1024)
    with open(d / "voices-v1.0.bin", "wb") as f:
        f.truncate(11 * 1024 * 1024)
    assert tts_supervisor.runnable()


def test_recover_ignores_remote_tts(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(config, "TTS_URL", "http://192.168.1.50:8001/tts")
    monkeypatch.setattr(tts_supervisor, "runnable", lambda: True)
    monkeypatch.setattr(tts_supervisor, "_spawn",
                        lambda: (_ for _ in ()).throw(AssertionError("must not spawn")))
    assert tts_supervisor.recover("test") is False


def test_recover_spawns_once_per_cooldown(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(config, "TTS_URL", "http://127.0.0.1:8001/tts")
    monkeypatch.setattr(tts_supervisor, "runnable", lambda: True)
    monkeypatch.setattr(tts_supervisor, "_ping", lambda: False)
    spawns = []
    monkeypatch.setattr(tts_supervisor, "_spawn", lambda: spawns.append(1))
    assert tts_supervisor.recover("test") is True
    assert tts_supervisor.recover("test") is False  # inside the cooldown window
    assert spawns == [1]


def test_recover_skips_when_service_answers(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(config, "TTS_URL", "http://127.0.0.1:8001/tts")
    monkeypatch.setattr(tts_supervisor, "runnable", lambda: True)
    monkeypatch.setattr(tts_supervisor, "_ping", lambda: True)
    monkeypatch.setattr(tts_supervisor, "_spawn",
                        lambda: (_ for _ in ()).throw(AssertionError("must not spawn")))
    assert tts_supervisor.recover("test") is False


def test_healthy_ping_does_not_consume_cooldown(monkeypatch):
    """A death right after a healthy check must still heal immediately."""
    _reset_cooldown()
    monkeypatch.setattr(config, "TTS_URL", "http://127.0.0.1:8001/tts")
    monkeypatch.setattr(tts_supervisor, "runnable", lambda: True)
    spawns = []
    monkeypatch.setattr(tts_supervisor, "_spawn", lambda: spawns.append(1))
    monkeypatch.setattr(tts_supervisor, "_ping", lambda: True)
    assert tts_supervisor.recover("healthy check") is False
    monkeypatch.setattr(tts_supervisor, "_ping", lambda: False)  # it just died
    assert tts_supervisor.recover("death after check") is True
    assert spawns == [1]


def test_recover_never_raises(monkeypatch):
    _reset_cooldown()
    monkeypatch.setattr(config, "TTS_URL", "http://127.0.0.1:8001/tts")
    monkeypatch.setattr(tts_supervisor, "runnable", lambda: True)
    monkeypatch.setattr(tts_supervisor, "_ping", lambda: False)

    def boom():
        raise OSError("spawn failed")

    monkeypatch.setattr(tts_supervisor, "_spawn", boom)
    assert tts_supervisor.recover("test") is False  # swallowed, reported False


def test_speak_failure_schedules_recover(monkeypatch):
    """A dead TTS backend 502s /speak promptly AND kicks the self-heal."""
    import server
    from fastapi.testclient import TestClient

    calls = []
    monkeypatch.setattr(
        tts_supervisor, "recover",
        lambda reason, log=None: calls.append(reason) or False,
    )
    # Port 9 (discard) refuses instantly — the fastest possible dead backend.
    monkeypatch.setattr(config, "TTS_URL", "http://127.0.0.1:9/tts")

    with TestClient(server.app) as client:  # context manager: loop stays alive
        r = client.post("/speak", headers=AUTH, json={"text": "Hello there."})
        assert r.status_code == 502
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and "speak failed" not in calls:
            time.sleep(0.05)
    assert "speak failed" in calls
