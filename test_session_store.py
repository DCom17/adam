"""
Adam — cross-device chat-sync store + endpoint tests.

Proves the server-side session store and its /sessions pull/push endpoints:
  * upsert is last-write-wins by `updated` (a stale device can't clobber a fresher
    edit); a strictly-newer record applies;
  * the sync cursor rides a server-assigned monotonic `seq`, NOT the client clock:
    changed_since(seq) returns only later writes, oldest-first, and a delete stamped
    by a lagging device clock still crosses to a device whose cursor a faster peer
    already advanced (the v0.9.31 "delete doesn't propagate" fix);
  * delete tombstones (deleted=1) round-trip;
  * transcript blobs are capped server-side;
  * GET/POST /sessions are token-gated, and a push on one "device" is visible to a
    pull from another (the whole point — cross-device continuity);
  * a same-key edit from a second device wins iff its `updated` is newer.

State is isolated to a temp dir; the real DBs are never touched.

Run:  python test_session_store.py   (exit code 0 = all passed)
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

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_session_test_"))
_STATE = _SANDBOX / "state"
_STATE.mkdir(parents=True, exist_ok=True)
config.STATE_DIR = _STATE
config.JOBS_DB = _STATE / "adam.db"
config.SESSIONS_DB = _STATE / "sessions.db"
config.PUSH_SUB_FILE = _STATE / "push_sub.json"
config.LAST_RESULT_FILE = _STATE / "last_result.json"
config.VAPID_PRIVATE_PEM = _STATE / "vapid_private.pem"
config.SESSION_SYNC_ENABLED = True

import server  # noqa: E402
import session_store  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# The store opened against the default path at import; re-point it at our sandbox.
session_store.init(config.SESSIONS_DB)

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


def rec(key, updated, **kw):
    base = {"key": key, "title": "", "mode": "voice", "sid": None, "last_ts": 0,
            "last_spoken": "", "tx": "", "deleted": False, "created": 0, "used": 0,
            "updated": updated}
    base.update(kw)
    return base


def _seq_of(key):
    hits = [s for s in session_store.changed_since(0) if s["key"] == key]
    return hits[0]["seq"] if hits else 0


def _max_seq():
    rows = session_store.changed_since(0)
    return max((s["seq"] for s in rows), default=0)


# --- store unit behavior ----------------------------------------------------

session_store.upsert([rec("a", 100, title="Alpha", tx="<b>hi</b>")])
got = {s["key"]: s for s in session_store.changed_since(0)}
check("upsert then read back", got.get("a", {}).get("title") == "Alpha")
check("transcript round-trips", got["a"]["tx"] == "<b>hi</b>")
check("applied write carries a server seq", got["a"]["seq"] > 0)

# Stale update (older `updated`) is ignored.
session_store.upsert([rec("a", 50, title="STALE")])
check("older update is ignored (LWW)", session_store.changed_since(0)[0]["title"] == "Alpha")

# Newer update wins.
session_store.upsert([rec("a", 200, title="Alpha2")])
check("newer update wins (LWW)", session_store.changed_since(0)[0]["title"] == "Alpha2")

# The sync cursor is a server seq: changed_since(seq) returns only later writes.
before_b = _max_seq()
session_store.upsert([rec("b", 300, title="Bravo")])
since = session_store.changed_since(before_b)
check("changed_since filters by the server seq cursor", [s["key"] for s in since] == ["b"])

# seq is monotonic and independent of the client `updated` clock: a LATER write with a
# SMALLER `updated` still gets a HIGHER seq. This is the invariant the fix rests on.
session_store.upsert([rec("seqA", 100_000)])
session_store.upsert([rec("seqB", 40_000)])   # smaller updated, but written afterward
check("a later write gets a higher seq even with a smaller updated",
      _seq_of("seqB") > _seq_of("seqA"))

# Delete tombstone round-trips.
session_store.upsert([rec("a", 400, deleted=True)])
a = [s for s in session_store.changed_since(0) if s["key"] == "a"][0]
check("delete tombstone persists", a["deleted"] is True)

# Transcript cap.
big = "x" * 250_000
session_store.upsert([rec("c", 500, tx=big)])
c = [s for s in session_store.changed_since(0) if s["key"] == "c"][0]
check("oversize transcript is capped", 0 < len(c["tx"]) <= 200_000)

# --- the regression: a delete propagates despite a peer's higher clock ------
# THE BUG (v0.9.30 and earlier): the cursor was the max client `updated` ms. A device
# with a fast clock could advance a puller's cursor past a slower device's not-yet-seen
# delete, whose lower `updated` then failed the `updated > cursor` filter forever — so
# the deleted chat lived on. THE FIX: filter on server seq, which every applied write
# bumps regardless of the client clock. Timestamps below are recent so the retention
# sweep never touches the tombstone; the skew is modeled by their relative order.
NOW = session_store.now_ms()
session_store.upsert([rec("skewX", NOW, title="X (slow device clock)")])
# Device D syncs everything; its cursor is the current max seq.
cur = _max_seq()
# A fast-clock device saves a DIFFERENT chat with a much larger `updated`.
session_store.upsert([rec("skewY", NOW + 3_600_000, title="Y (fast device clock)")])
# D pulls again -> sees Y, advances its cursor past it.
cur = max([s["seq"] for s in session_store.changed_since(cur)] + [cur])
# The slow device now DELETES skewX. Its clock lags: updated is newer than skewX's own
# (so LWW keeps the delete) but far below skewY's, i.e. below D's wall-clock cursor.
session_store.upsert([rec("skewX", NOW + 1000, deleted=True)])
delivered = {s["key"]: s for s in session_store.changed_since(cur)}
check("delete propagates despite a peer's higher clock (seq cursor)",
      delivered.get("skewX", {}).get("deleted") is True)

# --- endpoints --------------------------------------------------------------

check("GET /sessions rejects missing token", client.get("/sessions").status_code == 403)
check("POST /sessions rejects missing token",
      client.post("/sessions", json={"sessions": []}).status_code == 403)

# Device 1 pushes a brand-new chat.
r = client.post("/sessions", json={"sessions": [rec("dev1chat", 1000, title="From PC", tx="<i>pc</i>")]}, headers=AUTH)
check("POST applies a new session", r.status_code == 200 and r.json().get("applied") == 1)

# Device 2 pulls and sees it (cross-device continuity).
r = client.get("/sessions?since=0", headers=AUTH)
pulled = {s["key"]: s for s in r.json().get("sessions", [])}
check("another device pulls the pushed chat", pulled.get("dev1chat", {}).get("title") == "From PC")
check("pulled chat carries its transcript", pulled["dev1chat"]["tx"] == "<i>pc</i>")
check("pulled record carries a server seq", pulled["dev1chat"].get("seq", 0) > 0)
check("GET reports enabled + server clock", r.json().get("enabled") is True and r.json().get("now", 0) > 0)

# Device 2 edits the same chat with a newer timestamp -> wins.
client.post("/sessions", json={"sessions": [rec("dev1chat", 2000, title="Edited on phone")]}, headers=AUTH)
r = client.get("/sessions?since=0", headers=AUTH)
check("newer cross-device edit wins over the network",
      [s for s in r.json()["sessions"] if s["key"] == "dev1chat"][0]["title"] == "Edited on phone")

# A stale push from a lagging device does NOT clobber it.
client.post("/sessions", json={"sessions": [rec("dev1chat", 1500, title="LATE STALE")]}, headers=AUTH)
r = client.get("/sessions?since=0", headers=AUTH)
check("stale cross-device push is rejected",
      [s for s in r.json()["sessions"] if s["key"] == "dev1chat"][0]["title"] == "Edited on phone")

# The same clock-skew delete scenario, end-to-end over the real /sessions endpoints.
now = session_store.now_ms()
client.post("/sessions", json={"sessions": [rec("wsX", now, title="wsX")]}, headers=AUTH)
seen = client.get("/sessions?since=0", headers=AUTH).json()["sessions"]
wcur = max((s["seq"] for s in seen), default=0)                       # device D's cursor
client.post("/sessions", json={"sessions": [rec("wsY", now + 3_600_000, title="fast clock")]}, headers=AUTH)
seen = client.get("/sessions?since=" + str(wcur), headers=AUTH).json()["sessions"]
wcur = max([s["seq"] for s in seen] + [wcur])                         # advanced past wsY
client.post("/sessions", json={"sessions": [rec("wsX", now + 1000, deleted=True)]}, headers=AUTH)
got = {s["key"]: s for s in client.get("/sessions?since=" + str(wcur), headers=AUTH).json()["sessions"]}
check("wire: delete crosses devices despite clock skew",
      got.get("wsX", {}).get("deleted") is True)

# --- archive retention (v0.9.24) -------------------------------------------
# A deleted chat keeps its transcript (recoverable from the Archived drawer) until
# the retention window lapses, then the sweep hard-purges the row.

# A tombstone retains BOTH its transcript AND its resume id (`sid`) — the sid is the
# real conversation context the server resumes with `--resume`, so it must survive a
# delete for a revived chat to continue where it left off; the transcript is the
# visible scrollback.
session_store.upsert([rec("arch1", 10_000, title="Kept", tx="<b>ctx</b>", sid="claude-sess-xyz", deleted=True)])
a1 = [s for s in session_store.changed_since(0) if s["key"] == "arch1"][0]
check("deleted chat retains its transcript", a1["deleted"] is True and a1["tx"] == "<b>ctx</b>")
check("deleted chat retains its resume id (context handle)", a1["sid"] == "claude-sess-xyz")

# Full delete -> revive round-trip: reviving is an un-delete with a newer `updated`,
# and it must carry the same sid + transcript back so the recovered chat continues
# with full context (this is the cross-device path a phone delete + PC revive takes).
session_store.upsert([rec("arch1", 20_000, title="Kept", tx="<b>ctx</b>", sid="claude-sess-xyz", deleted=False)])
r1 = [s for s in session_store.changed_since(0) if s["key"] == "arch1"][0]
check("revive un-deletes the chat", r1["deleted"] is False)
check("revived chat keeps its resume id (context intact)", r1["sid"] == "claude-sess-xyz")
check("revived chat keeps its transcript", r1["tx"] == "<b>ctx</b>")

# purge_expired reaps only tombstones older than the ttl; a recent tombstone and any
# live chat are untouched.
now = session_store.now_ms()
session_store.upsert([
    rec("old_tomb", now - 10 * 86_400_000, deleted=True, tx="<i>old</i>"),   # 10 days old
    rec("new_tomb", now - 1 * 86_400_000, deleted=True, tx="<i>fresh</i>"),   # 1 day old
    rec("live_old", now - 10 * 86_400_000, deleted=False, tx="<i>alive</i>"),  # old but NOT deleted
])
purged = session_store.purge_expired(7 * 86_400_000, now=now)
keys_after = {s["key"] for s in session_store.all_sessions()}
check("purge removes expired tombstone", "old_tomb" not in keys_after and purged >= 1)
check("purge keeps in-window tombstone", "new_tomb" in keys_after)
check("purge never touches a live chat", "live_old" in keys_after)
check("purge with non-positive ttl is a no-op", session_store.purge_expired(0, now=now) == 0)

# The pull endpoint advertises the retention window and self-sweeps.
config.SESSION_ARCHIVE_RETENTION_DAYS = 7
client.post("/sessions", json={"sessions": [rec("ep_old_tomb", session_store.now_ms() - 30 * 86_400_000,
                                                 deleted=True, tx="<i>x</i>")]}, headers=AUTH)
rj = client.get("/sessions?since=0", headers=AUTH).json()
check("pull reports the archive retention window", rj.get("archive_retention_days") == 7)
check("pull sweeps an expired tombstone",
      "ep_old_tomb" not in {s["key"] for s in client.get("/sessions?since=0", headers=AUTH).json()["sessions"]})

# Disabled flag short-circuits.
config.SESSION_SYNC_ENABLED = False
check("GET reports disabled when flag off", client.get("/sessions", headers=AUTH).json().get("enabled") is False)
check("POST is a no-op when flag off",
      client.post("/sessions", json={"sessions": [rec("z", 9999)]}, headers=AUTH).json().get("applied") == 0)
config.SESSION_SYNC_ENABLED = True

print(f"\nALL PASSED ({_passed})")