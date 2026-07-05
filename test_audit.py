"""
Adam — read-only audit viewer tests (v0.8.1).

Headless FastAPI TestClient coverage for GET /audit. Proves:
  * auth matrix: missing/wrong token -> 403, valid token -> 200;
  * missing audit file -> 200, empty entries, available=false;
  * disabled audit logging -> 200, empty entries, available=false;
  * empty audit file -> 200, empty entries, available=true;
  * valid JSONL parses, newest-first;
  * limit is honored, clamped to [1, 500];
  * a malformed line becomes a safe marker and never echoes its raw content;
  * extra / secret-like fields are dropped by the whitelist;
  * ADAM_TOKEN / VAPID / TWILIO values never appear in the response body.

The route reads config.AUDIT_LOG_FILE / config.PERM_AUDIT_LOG_ENABLED live, so
each case repoints the audit file at a throwaway temp path. Real history is never
touched.

Run:  python test_audit.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import config

# Make the app importable on any machine: server.py calls config.validate() at
# import and refuses to start without a token + a resolvable Claude. Supply
# harmless stand-ins where the real box hasn't (no secret is asserted from these).
if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_audit_test_"))

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = server.ADAM_TOKEN
AUTH = {"Authorization": "Bearer " + TOKEN}
SECRETS = [s for s in (
    server.ADAM_TOKEN,
    getattr(config, "VAPID_PUBLIC_KEY", ""),
    getattr(config, "VAPID_PRIVATE_KEY", ""),
    getattr(config, "TWILIO_AUTH_TOKEN", ""),
) if s]

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


def _no_secret(text: str) -> bool:
    return all(s not in text for s in SECRETS)


def _use_audit_file(name: str) -> Path:
    """Point the route at a fresh temp audit path and return it."""
    p = _SANDBOX / name
    config.AUDIT_LOG_FILE = p
    config.PERM_AUDIT_LOG_ENABLED = True
    return p


def _write_lines(p: Path, lines) -> None:
    p.write_text("".join(l + "\n" for l in lines), encoding="utf-8")


def main() -> int:
    print("\n[1] Auth matrix")
    _use_audit_file("auth.jsonl").write_text("", encoding="utf-8")
    check("/audit no token -> 403", client.get("/audit").status_code == 403)
    check("/audit wrong token -> 403",
          client.get("/audit", headers={"Authorization": "Bearer nope"}).status_code == 403)
    check("/audit valid token -> 200", client.get("/audit", headers=AUTH).status_code == 200)

    print("\n[2] Missing audit file -> 200, available=false, empty")
    config.AUDIT_LOG_FILE = _SANDBOX / "does_not_exist.jsonl"
    config.PERM_AUDIT_LOG_ENABLED = True
    r = client.get("/audit", headers=AUTH)
    b = r.json()
    check("status 200", r.status_code == 200)
    check("available is false", b.get("available") is False)
    check("entries empty", b.get("entries") == [] and b.get("count") == 0)
    check("enabled reflected true", b.get("enabled") is True)

    print("\n[3] Audit logging disabled -> 200, available=false")
    p = _use_audit_file("disabled.jsonl")
    _write_lines(p, [json.dumps({"action_type": "x", "ts": "t"})])
    config.PERM_AUDIT_LOG_ENABLED = False
    b = client.get("/audit", headers=AUTH).json()
    check("available is false when disabled", b.get("available") is False)
    check("enabled is false", b.get("enabled") is False)
    check("no entries when disabled", b.get("entries") == [])
    config.PERM_AUDIT_LOG_ENABLED = True

    print("\n[4] Empty audit file -> 200, available=true, empty entries")
    p = _use_audit_file("empty.jsonl")
    p.write_text("", encoding="utf-8")
    b = client.get("/audit", headers=AUTH).json()
    check("available is true (file exists)", b.get("available") is True)
    check("entries empty", b.get("entries") == [] and b.get("count") == 0)

    print("\n[5] Valid JSONL parses, newest-first")
    p = _use_audit_file("valid.jsonl")
    _write_lines(p, [
        json.dumps({"action_type": "evt1", "ts": "2026-06-23T00:00:01", "target": "a"}),
        json.dumps({"action_type": "evt2", "ts": "2026-06-23T00:00:02", "target": "b"}),
        json.dumps({"action_type": "evt3", "ts": "2026-06-23T00:00:03", "target": "c"}),
    ])
    b = client.get("/audit", headers=AUTH).json()
    check("count is 3", b.get("count") == 3)
    check("newest-first (evt3 first)", b["entries"][0].get("action_type") == "evt3")
    check("oldest last (evt1 last)", b["entries"][-1].get("action_type") == "evt1")
    check("no malformed", b.get("malformed") == 0)

    print("\n[6] limit honored")
    b = client.get("/audit?limit=2", headers=AUTH).json()
    check("limit=2 returns 2", b.get("count") == 2)
    check("limit=2 keeps the newest two", b["entries"][0].get("action_type") == "evt3"
          and b["entries"][1].get("action_type") == "evt2")
    check("effective limit echoed as 2", b.get("limit") == 2)

    print("\n[7] limit clamped to [1, 500]")
    over = client.get("/audit?limit=9999", headers=AUTH).json()
    check("limit over cap clamped to 500", over.get("limit") == 500)
    check("clamped request still returns all 3 present", over.get("count") == 3)
    under = client.get("/audit?limit=0", headers=AUTH).json()
    check("limit=0 clamped to 1", under.get("limit") == 1)
    check("limit=0 returns 1 (newest)", under.get("count") == 1
          and under["entries"][0].get("action_type") == "evt3")
    neg = client.get("/audit?limit=-5", headers=AUTH).json()
    check("negative limit clamped to 1", neg.get("limit") == 1 and neg.get("count") == 1)

    print("\n[8] Malformed line -> safe marker, no raw content echoed")
    p = _use_audit_file("malformed.jsonl")
    _write_lines(p, [
        json.dumps({"action_type": "good1", "ts": "t1"}),
        "this is NOT json {{{ RAWSECRETXYZ",
        json.dumps(["a", "list", "not", "an", "object"]),
        json.dumps({"action_type": "good2", "ts": "t2"}),
    ])
    r = client.get("/audit", headers=AUTH)
    b = r.json()
    check("all 4 lines accounted for", b.get("count") == 4)
    check("two malformed counted", b.get("malformed") == 2)
    check("raw malformed text never echoed", "RAWSECRETXYZ" not in r.text)
    markers = [e for e in b["entries"] if e.get("_parse_error")]
    check("malformed entries are bare markers (no extra keys)",
          all(set(e.keys()) == {"_parse_error"} for e in markers))
    check("good records still parsed",
          any(e.get("action_type") == "good1" for e in b["entries"])
          and any(e.get("action_type") == "good2" for e in b["entries"]))

    print("\n[9] Extra + secret-like fields dropped by whitelist")
    p = _use_audit_file("secrets.jsonl")
    _write_lines(p, [json.dumps({
        "action_type": "leak_probe",
        "ts": "2026-06-23T00:00:09",
        "target": "vault/note.md",
        "risk": "low",
        # fields that must NOT pass the whitelist:
        "token": TOKEN,
        "api_key": "sk-SHOULD-NOT-APPEAR",
        "secret_field": "PASSWORD123",
        "content": "raw file body that should never surface",
    })])
    r = client.get("/audit", headers=AUTH)
    b = r.json()
    e = b["entries"][0]
    check("whitelisted fields kept", e.get("action_type") == "leak_probe"
          and e.get("target") == "vault/note.md" and e.get("risk") == "low")
    check("token field dropped", "token" not in e)
    check("api_key field dropped", "api_key" not in e)
    check("secret_field dropped", "secret_field" not in e)
    check("content field dropped", "content" not in e)
    check("api_key value absent from body", "sk-SHOULD-NOT-APPEAR" not in r.text)
    check("password value absent from body", "PASSWORD123" not in r.text)
    check("raw content value absent from body", "raw file body" not in r.text)

    print("\n[10] No real secret value ever appears in the response body")
    p = _use_audit_file("nosecret.jsonl")
    _write_lines(p, [json.dumps({"action_type": "evt", "ts": "t", "target": "x"})])
    r = client.get("/audit", headers=AUTH)
    check("response body carries no secret", _no_secret(r.text))
    check("SECRETS list is non-empty (test is meaningful)", len(SECRETS) > 0)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
