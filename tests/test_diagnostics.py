"""Native pytest coverage for the crash-reporting layer:

- GET /diagnostics is token-gated, returns the support bundle, and REDACTS
  every configured secret from the log tail.
- The global exception handler turns an unhandled route crash into a clean
  500 (no internals leaked) and records the traceback in the log ring buffer.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Sandbox config BEFORE anything imports it: clean example defaults, no live
# settings.json/.env from the dev machine.
os.environ.setdefault("JARVIS_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_diag_cfg_"))

import config  # noqa: E402

SECRET = "diag-secret-token-" + "x" * 32
if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = SECRET
else:  # pragma: no cover — sandboxed config should have no token
    SECRET = config.JARVIS_TOKEN
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app, raise_server_exceptions=False)
AUTH = {"Authorization": f"Bearer {config.JARVIS_TOKEN}"}


def test_diagnostics_requires_token():
    assert client.get("/diagnostics").status_code == 403
    bad = client.get("/diagnostics", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 403


def test_diagnostics_bundle_shape():
    r = client.get("/diagnostics", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["app"]["version"] == config.APP_VERSION
    assert d["app"]["uptime_seconds"] >= 0
    assert "python" in d["app"] and "platform" in d["app"]
    assert isinstance(d["config"], dict)
    assert isinstance(d["addons"], list)
    assert isinstance(d["recent_jobs"], list)
    assert isinstance(d["log_tail"], list)


def test_diagnostics_redacts_secrets():
    # Simulate a buggy future log line that embeds the bearer token.
    server.log.warning("oops leaked %s in a log line", config.JARVIS_TOKEN)
    r = client.get("/diagnostics", headers=AUTH)
    assert r.status_code == 200
    assert config.JARVIS_TOKEN not in r.text
    assert any("***" in line for line in r.json()["log_tail"])


def test_unhandled_exception_returns_clean_500_and_is_recorded():
    @server.app.get("/__boom_test")
    async def _boom():
        raise RuntimeError("kaboom-sentinel")

    r = client.get("/__boom_test")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "kaboom-sentinel" not in r.text  # no internals leak to the client
    ring = "\n".join(server.LOG_RING)
    assert "UNHANDLED GET /__boom_test" in ring
    assert "kaboom-sentinel" in ring  # full traceback kept server-side
