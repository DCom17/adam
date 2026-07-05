"""
Adam — /voice/status + real-voice nudge smoke tests.

Covers the step that makes the real Adam voice discoverable in-app (so a friend
still on the browser-fallback voice learns INSTALL-VOICE exists):
  * GET /voice/status is token-gated (missing/wrong -> 403, valid -> 200);
  * the body shape is stable ({"installed": <bool>}) and leaks no secret;
  * the underlying _voice_pkg_installed() filesystem check is correct:
      - absent model/voices           -> False
      - present but undersized        -> False
      - present at the expected size  -> True
    (sized via truncate() so the test never writes hundreds of real MB.)

Run:  python test_voice_status.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = server.ADAM_TOKEN
AUTH = {"Authorization": "Bearer " + TOKEN}
SECRETS = [s for s in (server.ADAM_TOKEN,) if s]

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


def _mk(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)   # sparse — reports `size` via stat() without writing it all


def main() -> int:
    print("\n[1] /voice/status is token-gated")
    check("no token -> 403", client.get("/voice/status").status_code == 403)
    check("wrong token -> 403",
          client.get("/voice/status", headers={"Authorization": "Bearer nope"}).status_code == 403)

    print("\n[2] valid token -> 200 with a stable shape, no secret")
    r = client.get("/voice/status", headers=AUTH)
    check("valid token -> 200", r.status_code == 200)
    body = r.json() if r.status_code == 200 else {}
    check("body has 'installed'", "installed" in body)
    check("'installed' is a bool", isinstance(body.get("installed"), bool))
    check("response leaks no secret", all(s not in r.text for s in SECRETS))

    print("\n[3] _voice_pkg_installed() filesystem logic")
    orig_root = config.ROOT
    try:
        sandbox = Path(tempfile.mkdtemp(prefix="jvl_voice_test_"))
        config.ROOT = sandbox
        tts = sandbox / "scripts" / "tts_server"
        model = tts / "kokoro-v1.0.onnx"
        voices = tts / "voices-v1.0.bin"

        check("absent files -> not installed", server._voice_pkg_installed() is False)

        _mk(model, 10 * 1024 * 1024)      # too small
        _mk(voices, 1 * 1024 * 1024)      # too small
        check("undersized files -> not installed", server._voice_pkg_installed() is False)

        _mk(model, 210 * 1024 * 1024)     # realistic model size
        _mk(voices, 25 * 1024 * 1024)     # realistic voices size
        check("correctly sized files -> installed", server._voice_pkg_installed() is True)

        model.unlink()
        check("model missing again -> not installed", server._voice_pkg_installed() is False)
    finally:
        config.ROOT = orig_root

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
