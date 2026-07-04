"""
Jarvis Voice Local — auth hardening + CORS tests (v0.9.0, Slice 1).

Covers the constant-time bearer-token gate and confirms the (pre-existing) CORS
setting is honored end-to-end. Proves:
  * auth matrix is unchanged by the timing-safe rewrite — missing / wrong-scheme /
    malformed / wrong-token -> 403, correct -> 200;
  * a 403/200 body never echoes the token value;
  * with cors_allowed_origins set to a specific origin, that origin is honored and a
    disallowed origin is NOT echoed;
  * the shipped default (["*"]) still allows an arbitrary LAN origin.

CORS middleware reads config.CORS_ALLOWED_ORIGINS at import, so we set a RESTRICTED
origin list BEFORE importing server to exercise the honored/blocked paths against the
real app; the ["*"] default is verified with a throwaway app in the same process.

Run:  python test_auth_cors.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

# Import-time stand-ins so server.py's config.validate() passes off the real box.
if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_authcors_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

# Restrict CORS to a single origin BEFORE importing server, so the real app's
# middleware is wired to this list (proves the existing setting is honored).
ALLOWED_ORIGIN = "https://allowed.example"
EVIL_ORIGIN = "https://evil.example"
config.CORS_ALLOWED_ORIGINS = [ALLOWED_ORIGIN]

import job_store          # noqa: E402
import server             # noqa: E402
from fastapi import FastAPI, HTTPException          # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402

job_store.init(_SANDBOX / "jobs.db")

TOKEN = server.JARVIS_TOKEN
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


def acao(resp) -> str | None:
    return resp.headers.get("access-control-allow-origin")


def gate_403(authorization: str) -> bool:
    """Call the gate directly and report whether it raised a 403. Lets us exercise
    inputs an HTTP client won't transmit (e.g. non-ASCII header values)."""
    try:
        server.require_token(authorization)
        return False  # did not raise -> allowed
    except HTTPException as e:
        return e.status_code == 403


def gate_allows(authorization: str) -> bool:
    try:
        server.require_token(authorization)
        return True
    except HTTPException:
        return False


def main() -> int:
    print("\n[1] Auth matrix at the gate (constant-time; covers transport-blocked inputs)")
    check("empty header -> 403", gate_403(""))
    check("wrong scheme 'Token' -> 403", gate_403("Token " + TOKEN))
    check("lowercase 'bearer' -> 403", gate_403("bearer " + TOKEN))
    check("'Bearer' no token -> 403", gate_403("Bearer"))
    check("'Bearer ' empty token -> 403", gate_403("Bearer "))
    check("'Bearer<token>' no space -> 403", gate_403("Bearer" + TOKEN))
    check("wrong token -> 403", gate_403("Bearer wrong-" + TOKEN))
    check("prefix of real token -> 403", gate_403("Bearer " + TOKEN[:-1]))
    check("real token + extra char -> 403", gate_403("Bearer " + TOKEN + "x"))
    check("non-ASCII token -> 403 (no crash)", gate_403("Bearer naïve-token-✓"))
    check("correct token -> allowed (no raise)", gate_allows("Bearer " + TOKEN))

    print("\n[2] Auth matrix end-to-end via a token-gated route (/jobs)")
    check("no header -> 403", client.get("/jobs").status_code == 403)
    check("wrong scheme -> 403",
          client.get("/jobs", headers={"Authorization": "Token " + TOKEN}).status_code == 403)
    check("malformed 'Bearer' -> 403",
          client.get("/jobs", headers={"Authorization": "Bearer"}).status_code == 403)
    check("wrong token -> 403",
          client.get("/jobs", headers={"Authorization": "Bearer wrong-" + TOKEN}).status_code == 403)
    ok = client.get("/jobs", headers={"Authorization": "Bearer " + TOKEN})
    check("correct token -> 200", ok.status_code == 200)

    print("\n[3] Token value never appears in a response body")
    bad = client.get("/jobs", headers={"Authorization": "Bearer wrong-" + TOKEN})
    check("403 body omits the real token", TOKEN not in bad.text)
    check("403 body omits the supplied wrong credential", ("wrong-" + TOKEN) not in bad.text)
    check("200 body omits the token", TOKEN not in ok.text)

    print("\n[4] CORS honors a configured specific origin (real app)")
    # Simple request with the allowed origin -> echoed.
    r_ok = client.get("/ping", headers={"Origin": ALLOWED_ORIGIN})
    check("allowed origin echoed in ACAO", acao(r_ok) == ALLOWED_ORIGIN)
    # Simple request with a disallowed origin -> not echoed.
    r_evil = client.get("/ping", headers={"Origin": EVIL_ORIGIN})
    check("disallowed origin NOT echoed", acao(r_evil) not in (EVIL_ORIGIN, "*"))
    # Preflight from the allowed origin -> echoed.
    pf_ok = client.options("/jobs", headers={
        "Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "GET"})
    check("preflight allowed origin echoed", acao(pf_ok) == ALLOWED_ORIGIN)
    # Preflight from a disallowed origin -> not echoed as that origin.
    pf_evil = client.options("/jobs", headers={
        "Origin": EVIL_ORIGIN, "Access-Control-Request-Method": "GET"})
    check("preflight disallowed origin NOT echoed", acao(pf_evil) not in (EVIL_ORIGIN, "*"))

    print("\n[5] Default ['*'] still allows an arbitrary LAN origin")
    star_app = FastAPI()
    star_app.add_middleware(CORSMiddleware, allow_origins=["*"],
                            allow_methods=["*"], allow_headers=["*"])

    @star_app.get("/ping")
    def _p():
        return {"status": "ok"}

    star = TestClient(star_app)
    lan = star.get("/ping", headers={"Origin": "http://192.168.1.50:8010"})
    check("'*' allows a LAN origin (ACAO present)", acao(lan) in ("*", "http://192.168.1.50:8010"))

    print("\n[6] CORS config wiring is honored by the real app")
    check("server uses config.CORS_ALLOWED_ORIGINS (restricted list active)",
          config.CORS_ALLOWED_ORIGINS == [ALLOWED_ORIGIN] and acao(r_ok) == ALLOWED_ORIGIN)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
