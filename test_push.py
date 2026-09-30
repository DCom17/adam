"""
Adam — Web Push activation tests (Phase 1: notifications).

Covers the fix for pushes silently no-op'ing on a fresh install: the server now
auto-generates a *matched* VAPID keypair on startup and serves the matching public
key from /push/key. Proves:
  * a fresh install (no PEM, no hand-set public key) generates a self-consistent
    P-256 keypair: private PEM on disk + a matching base64url public key;
  * the generated PEM is loadable + signable by py_vapid (so _send_push can sign);
  * keygen is idempotent — a second call reuses the on-disk pair, never rotates it;
  * /push/key is token-gated and returns {enabled: true, key: <generated>};
  * /push/subscribe stores a subscription (token-gated) and dedupes by endpoint;
  * _send_push degrades gracefully (no subs, or pywebpush missing) without raising.

State is isolated: STATE_DIR, the job DB, the VAPID files, and the push-sub store
are pointed at a throwaway temp dir so the run never touches real keys or history.

Run:  python test_push.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

import config

# Make the app importable on any machine (server.validate() needs a token + Claude).
if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

# Isolate ALL mutable state BEFORE importing server (it generates the keypair and
# opens the job DB at import). Simulate a fresh install: a temp state dir, no
# private PEM, and no hand-set public key — exactly the case that was broken.
_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_push_test_"))
_STATE = _SANDBOX / "state"
_STATE.mkdir(parents=True, exist_ok=True)
config.STATE_DIR = _STATE
config.JOBS_DB = _STATE / "adam.db"
config.PUSH_SUB_FILE = _STATE / "push_sub.json"
config.LAST_RESULT_FILE = _STATE / "last_result.json"
config.VAPID_PRIVATE_PEM = _STATE / "vapid_private.pem"
config.VAPID_PUBLIC_KEY = ""   # no hand-set key -> exercise the generation path

import server  # noqa: E402  (import after the sandbox is wired)
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
AUTH = {"Authorization": "Bearer " + config.ADAM_TOKEN}

_passed = 0


def check(label: str, cond: bool) -> None:
    global _passed
    if not cond:
        print(f"FAIL: {label}")
        raise SystemExit(1)
    _passed += 1
    print(f"ok: {label}")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- keypair generation -----------------------------------------------------

check("private PEM was generated on startup", server.VAPID_PRIVATE_PEM.exists())
check("public key cache was written", server.VAPID_PUBLIC_FILE.exists())
check("in-memory public key is set", bool(server.VAPID_PUBLIC_KEY))

_pub_bytes = _b64url_decode(server.VAPID_PUBLIC_KEY)
check("public key is a 65-byte uncompressed point", len(_pub_bytes) == 65 and _pub_bytes[0] == 0x04)
check("cached public file matches in-memory key",
      server.VAPID_PUBLIC_FILE.read_text("utf-8").strip() == server.VAPID_PUBLIC_KEY)
check("PEM is SEC1 EC format", server.VAPID_PRIVATE_PEM.read_text("utf-8").startswith("-----BEGIN EC PRIVATE KEY"))

# The generated PEM must be loadable + signable the way pywebpush signs each push,
# and its public half must equal what we advertise to the browser.
from py_vapid import Vapid01  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

_v = Vapid01.from_file(str(server.VAPID_PRIVATE_PEM))
_hdr = _v.sign({"aud": "https://example.com", "sub": server.VAPID_SUBJECT})
check("py_vapid loads + signs the generated PEM", bool(_hdr.get("Authorization")))
_adv = _v.public_key.public_bytes(serialization.Encoding.X962,
                                  serialization.PublicFormat.UncompressedPoint)
check("advertised key matches the PEM's public half",
      base64.urlsafe_b64encode(_adv).rstrip(b"=").decode() == server.VAPID_PUBLIC_KEY)

# --- idempotence ------------------------------------------------------------

_pem_before = server.VAPID_PRIVATE_PEM.read_bytes()
_pub_before = server.VAPID_PUBLIC_KEY
server._ensure_vapid_keypair()
check("re-running keygen does NOT rotate the PEM", server.VAPID_PRIVATE_PEM.read_bytes() == _pem_before)
check("re-running keygen keeps the same public key", server.VAPID_PUBLIC_KEY == _pub_before)

# Regression (2026-09-29): a sandbox that redirected STATE_DIR (so the public
# cache was missing) but not the PEM path generated a fresh pair OVER the real
# PEM, orphaning every phone subscription. An existing PEM must never be
# rewritten — a missing or mismatched public cache is re-derived from it.
server.VAPID_PUBLIC_FILE.unlink()
server._ensure_vapid_keypair()
check("missing public cache does NOT rotate an existing PEM",
      server.VAPID_PRIVATE_PEM.read_bytes() == _pem_before)
check("missing public cache is re-derived from the PEM",
      server.VAPID_PUBLIC_KEY == _pub_before
      and server.VAPID_PUBLIC_FILE.read_text("utf-8").strip() == _pub_before)
server.VAPID_PUBLIC_FILE.write_text("BStaleKeyFromAnOlderPair", encoding="utf-8")
server._ensure_vapid_keypair()
check("mismatched public cache does NOT rotate the PEM",
      server.VAPID_PRIVATE_PEM.read_bytes() == _pem_before)
check("mismatched public cache is healed to the PEM's public half",
      server.VAPID_PUBLIC_KEY == _pub_before
      and server.VAPID_PUBLIC_FILE.read_text("utf-8").strip() == _pub_before)

# --- /push/key --------------------------------------------------------------

check("/push/key rejects a missing token", client.get("/push/key").status_code == 403)
_r = client.get("/push/key", headers=AUTH)
check("/push/key accepts the real token", _r.status_code == 200)
_body = _r.json()
check("/push/key reports enabled", _body.get("enabled") is True)
check("/push/key returns the generated key", _body.get("key") == server.VAPID_PUBLIC_KEY)

# --- /push/subscribe --------------------------------------------------------

_sub = {"endpoint": "https://push.example.com/abc123",
        "keys": {"p256dh": "BFakeKey", "auth": "fakeAuth"}}
check("/push/subscribe rejects a missing token",
      client.post("/push/subscribe", json={"subscription": _sub}).status_code == 403)
check("/push/subscribe rejects an endpoint-less subscription",
      client.post("/push/subscribe", json={"subscription": {"keys": {}}}, headers=AUTH).status_code == 400)
_r = client.post("/push/subscribe", json={"subscription": _sub}, headers=AUTH)
check("/push/subscribe stores the subscription", _r.status_code == 200 and _r.json().get("count") == 1)
check("subscription persisted to disk",
      any(s.get("endpoint") == _sub["endpoint"] for s in json.loads(config.PUSH_SUB_FILE.read_text("utf-8"))))
# Same endpoint again -> dedup, still one.
_r = client.post("/push/subscribe", json={"subscription": _sub}, headers=AUTH)
check("/push/subscribe dedupes by endpoint", _r.json().get("count") == 1)

# --- _send_push degrades gracefully ----------------------------------------

# pywebpush missing -> early return, no raise (the guard's first clause).
_real_webpush = server.webpush
try:
    server.webpush = None
    server._send_push("hi", "sess", 123)        # must not raise
    check("_send_push is a no-op when pywebpush is unavailable", True)
finally:
    server.webpush = _real_webpush

# No subscriptions -> early return, no raise (even though a real fake sub exists,
# clear it to hit the 'no subs' branch).
config.PUSH_SUB_FILE.write_text("[]", encoding="utf-8")
server._send_push("hi", "sess", 123)            # must not raise
check("_send_push is a no-op with zero subscriptions", True)

# A 403 (subscription made for a different VAPID key) can never verify: prune it,
# along with 404/410. A 5xx is transient and keeps the subscription.
class _Resp:
    def __init__(self, code, text=""):
        self.status_code = code
        self.text = text


def _fake_webpush(subscription_info, **_kw):
    tail = subscription_info["endpoint"].rsplit("/", 1)[-1]
    code = int(tail.split("-")[0])
    if code != 201:
        text = '{"reason":"VapidPkHashMismatch"}' if tail == "400-apple" else ""
        raise server.WebPushException("rejected", response=_Resp(code, text))


# 400-apple / 401 = the real key-mismatch answers from Apple / WNS; plain 400 = a
# bad payload (not the subscription's fault) → keep.
config.PUSH_SUB_FILE.write_text(json.dumps([
    {"endpoint": f"https://push.example.com/{c}", "keys": {}}
    for c in ("201", "400", "400-apple", "401", "403", "410", "503")
]), encoding="utf-8")
try:
    server.webpush = _fake_webpush
    server._send_push("hi", "sess", 123)
finally:
    server.webpush = _real_webpush
_left = sorted(s["endpoint"].rsplit("/", 1)[-1]
               for s in json.loads(config.PUSH_SUB_FILE.read_text("utf-8")))
check("_send_push prunes key-mismatch/gone subs, keeps delivered + transient",
      _left == ["201", "400", "503"])

# --- PEM location follows STATE_DIR ------------------------------------------
# Unless .env pins it, the private key lives beside its public cache, so a
# sandbox that redirects STATE_DIR can never read or clobber the real key.
check("PEM path follows STATE_DIR (not the repo's data/state)",
      config.VAPID_PRIVATE_PEM_EXPLICIT
      or server.VAPID_PRIVATE_PEM.parent == config.STATE_DIR == server.VAPID_PUBLIC_FILE.parent)

# --- /push/status ------------------------------------------------------------
check("/push/status rejects a missing token", client.get("/push/status").status_code == 403)
_st = client.get("/push/status", headers=AUTH).json()
check("/push/status: ok after a send that reached a device",
      _st["state"] == "ok" and _st["key_matches"] and _st["last_delivered"] == 1)

# Total wipe-out: every device rejected + pruned. Must read FAILING, not the
# "no_devices" the pruning leaves behind — that silence was the original bug.
config.PUSH_SUB_FILE.write_text(json.dumps(
    [{"endpoint": "https://push.example.com/400-apple", "keys": {}}]), encoding="utf-8")
try:
    server.webpush = _fake_webpush
    server._send_push("hi", "sess", 123)
finally:
    server.webpush = _real_webpush
_st = client.get("/push/status", headers=AUTH).json()
check("/push/status: failing (not no_devices) after every device was rejected",
      _st["state"] == "failing" and _st["devices"] == 0
      and "VapidPkHashMismatch" in _st.get("last_error", ""))

client.post("/push/subscribe", json={"subscription": {
    "endpoint": "https://push.example.com/201", "keys": {}}}, headers=AUTH)
check("/push/status: unverified once a device re-registers after a failure",
      client.get("/push/status", headers=AUTH).json()["state"] == "unverified")
try:
    server.webpush = _fake_webpush
    server._send_push("hi", "sess", 123)
finally:
    server.webpush = _real_webpush
check("/push/status: back to ok after the next delivered send",
      client.get("/push/status", headers=AUTH).json()["state"] == "ok")

_real_pub = server.VAPID_PUBLIC_KEY
try:
    server.VAPID_PUBLIC_KEY = "BStaleKeyFromAnOlderPair"
    _st = client.get("/push/status", headers=AUTH).json()
    check("/push/status: failing when the advertised key isn't the PEM's",
          _st["state"] == "failing" and _st["key_matches"] is False)
finally:
    server.VAPID_PUBLIC_KEY = _real_pub

print(f"\nALL PASSED ({_passed})")
