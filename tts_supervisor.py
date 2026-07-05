"""Keeps the real Adam voice alive.

The Kokoro TTS service (scripts/tts_server/, 127.0.0.1:8001) is a separate
process. start-adam.ps1 launches it at boot, but nothing brought it back if it
died mid-session — every /speak then 502'd and the app silently degraded to the
robotic browser voice until someone restarted it by hand. This module is the
self-heal: `recover()` pings the service and, if it's down but installed,
respawns it from its own venv.

Called from two places (both fire-and-forget, never blocking a reply):
  - server startup — covers installs started without start-adam.ps1
  - the /speak failure path — covers a mid-session Kokoro death

Guards:
  - only acts when `tts_url` points at this machine (a custom remote TTS is
    the operator's to manage)
  - only acts when the voice package is fully installed (venv + model files,
    same size floors as server._voice_pkg_installed — a partial download
    must not be spawned)
  - one attempt per COOLDOWN_SECONDS, so a broken install can never spawn-loop
  - a duplicate spawn is harmless: the second instance can't bind :8001 and
    exits on its own
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import config

COOLDOWN_SECONDS = 90
PING_TIMEOUT_SECONDS = 2

_lock = threading.Lock()
_last_attempt = 0.0


def _tts_dir():
    return config.ROOT / "scripts" / "tts_server"


def _venv_python():
    d = _tts_dir() / ".venv"
    return d / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def is_local_tts_url() -> bool:
    """True when tts_url targets this machine — the only case we may manage."""
    try:
        host = urlparse(config.TTS_URL).hostname
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def runnable() -> bool:
    """Everything needed to spawn the service exists: venv python, the server
    script, and complete model files (same size floors as the install check)."""
    try:
        d = _tts_dir()
        model = d / "kokoro-v1.0.onnx"
        voices = d / "voices-v1.0.bin"
        return (_venv_python().is_file() and (d / "tts_server.py").is_file()
                and model.is_file() and model.stat().st_size > 200 * 1024 * 1024
                and voices.is_file() and voices.stat().st_size > 10 * 1024 * 1024)
    except Exception:
        return False


def _ping() -> bool:
    """Is the service answering? GET /ping on the tts_url origin."""
    u = urlparse(config.TTS_URL)
    ping_url = f"{u.scheme}://{u.netloc}/ping"
    try:
        with urllib.request.urlopen(ping_url, timeout=PING_TIMEOUT_SECONDS) as resp:
            return resp.status == 200
    except Exception:
        return False


def _tts_log_handle():
    """Append-mode handle for the voice service's own log. A real file can't
    fill like a pipe, and without it a crash-looping Kokoro (broken venv, bad
    model) respawns every cooldown forever with its traceback existing NOWHERE
    on the machine — the single most undiagnosable cold-tester failure."""
    try:
        log_dir = Path(config.DATA_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "tts.log"
        # Simple size cap: past ~2MB start fresh (one .old generation kept).
        if path.is_file() and path.stat().st_size > 2 * 1024 * 1024:
            old = path.with_suffix(".log.old")
            old.unlink(missing_ok=True)
            path.rename(old)
        return open(path, "ab")
    except Exception:
        return subprocess.DEVNULL


def _spawn() -> None:
    out = _tts_log_handle()
    subprocess.Popen(
        [str(_venv_python()), "tts_server.py"],
        cwd=str(_tts_dir()),
        stdin=subprocess.DEVNULL,
        # An append-mode FILE, never PIPE: an unread pipe fills and wedges the
        # child, while a file both absorbs unlimited output and preserves the
        # crash traceback for data/logs/tts.log.
        stdout=out,
        stderr=out,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if out is not subprocess.DEVNULL:
        out.close()  # the child holds its own duplicated handle


def recover(reason: str, log=None) -> bool:
    """Ping the TTS service and respawn it if it's down. Cooldown-gated, never
    raises. Returns True only when a spawn was actually attempted. Blocks up to
    PING_TIMEOUT_SECONDS — callers on the event loop use a thread."""
    global _last_attempt
    try:
        if not is_local_tts_url() or not runnable():
            return False
        with _lock:
            now = time.monotonic()
            if now - _last_attempt < COOLDOWN_SECONDS:
                return False
            if _ping():
                # Healthy — and a healthy check must NOT consume the cooldown,
                # or a death right after one waits out the window unhealed.
                return False
            _last_attempt = now
            _spawn()
        if log:
            log.info("Adam voice (Kokoro TTS) not answering — restarted it (%s); "
                     "first replies may still use the fallback voice while the "
                     "model warms up", reason)
        return True
    except Exception as e:  # noqa: BLE001 — self-heal must never take a reply down
        if log:
            log.warning("could not restart the Adam voice service: %s", e)
        return False
