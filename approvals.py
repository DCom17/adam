"""
Adam — pending-approval store (Level 3 approval flow foundation).

When the permission layer decides an action needs a human yes/no before it runs,
it parks a record here. The owner can list pending approvals and approve/deny
each one (via the API or, later, the PWA). Records persist to disk so a server
restart doesn't lose a pending request, and they expire so the queue can't grow
stale forever.

Single user, single process → a simple JSON file under data/state with an
in-process lock is sufficient. No database.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import config
import permissions

# How long a pending approval stays valid before it auto-expires.
DEFAULT_TTL_SECONDS = 24 * 3600

_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def _iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _load() -> list[dict]:
    try:
        return json.loads(Path(config.APPROVALS_FILE).read_text("utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    Path(config.APPROVALS_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.APPROVALS_FILE).write_text(
        json.dumps(items, indent=2), encoding="utf-8"
    )


# Resolved records older than this are shed (same rationale as proposed_changes:
# the store is one JSON file, fully re-parsed and rewritten on use — it must not
# grow forever). The audit log keeps the full decision history.
_TERMINAL_RETENTION_SECONDS = 30 * 24 * 3600


def _expire(items: list[dict]) -> list[dict]:
    """Mark still-pending records past their expiry as 'expired', and shed
    non-pending records older than the retention window. Returns the list."""
    now = _now()
    for it in items:
        if it.get("status") == "pending" and it.get("expires_at_ts", 0) and now > it["expires_at_ts"]:
            it["status"] = "expired"
            it["resolved_at"] = _iso(now)
    cutoff = now - _TERMINAL_RETENTION_SECONDS
    return [
        it for it in items
        if it.get("status") == "pending"
        or (it.get("resolved_at_ts") or it.get("expires_at_ts")
            or it.get("created_at_ts") or now) >= cutoff
    ]


def create(
    *,
    action_summary: str,
    action_type: str,
    risk_level: str = "medium",
    target: str | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
    payload: dict | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict:
    """Park a new pending approval and return the created record.

    `payload` carries the arguments for an EXTERNAL ACTION (e.g. the calendar
    event fields) so the server can execute it via the right connector after the
    user approves. It holds content only — never a secret/token. Approvals with no
    payload are plain yes/no records (the original behavior)."""
    now = _now()
    rec = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _iso(now),
        "created_at_ts": now,
        "status": "pending",
        "action_summary": action_summary,
        "action_type": action_type,
        "risk_level": risk_level,
        "target": target,
        "session_id": session_id,
        "job_id": job_id,
        "payload": payload or None,
        # Execution outcome, filled in by record_execution() after approval runs
        # the action. None until then; "executed"/"failed" once attempted.
        "execution": None,
        "expires_at": _iso(now + ttl_seconds),
        "expires_at_ts": now + ttl_seconds,
        "resolved_at": None,
    }
    with _LOCK:
        items = _expire(_load())
        items.append(rec)
        _save(items)
    permissions.record_audit_event({
        "action_type": "approval_created",
        "approval_id": rec["id"],
        "target": target,
        "risk": risk_level,
        "reason": action_summary,
        "session_id": session_id,
        "job_id": job_id,
    })
    return rec


def list_all(status: str | None = None) -> list[dict]:
    """All approvals (expiry applied), newest first. Optionally filter by status."""
    with _LOCK:
        items = _expire(_load())
        _save(items)  # persist any just-applied expirations
    items = sorted(items, key=lambda r: r.get("created_at_ts", 0), reverse=True)
    if status:
        items = [r for r in items if r.get("status") == status]
    return items


def get(approval_id: str) -> dict | None:
    for r in list_all():
        if r["id"] == approval_id:
            return r
    return None


def _resolve(approval_id: str, new_status: str) -> dict | None:
    """Shared approve/deny transition. Only a still-pending record can change."""
    with _LOCK:
        items = _expire(_load())
        rec = next((r for r in items if r["id"] == approval_id), None)
        if rec is None:
            return None
        if rec["status"] != "pending":
            return rec  # already resolved/expired — return as-is, caller checks
        rec["status"] = new_status
        rec["resolved_at"] = _iso(_now())
        _save(items)
    permissions.record_audit_event({
        "action_type": f"approval_{new_status}",
        "approval_id": approval_id,
        "target": rec.get("target"),
        "risk": rec.get("risk_level"),
        "approved": (new_status == "approved"),
        "session_id": rec.get("session_id"),
        "job_id": rec.get("job_id"),
    })
    return rec


def approve(approval_id: str) -> dict | None:
    return _resolve(approval_id, "approved")


def deny(approval_id: str) -> dict | None:
    return _resolve(approval_id, "denied")


def dismiss(approval_id: str) -> dict | None:
    """Remove an approval from the store entirely — clears it from the panel. Works
    on any status. Returns the removed record, or None if absent."""
    with _LOCK:
        items = _expire(_load())
        rec = next((r for r in items if r["id"] == approval_id), None)
        if rec is None:
            return None
        _save([r for r in items if r["id"] != approval_id])
    permissions.record_audit_event({
        "action_type": "approval_dismissed",
        "approval_id": approval_id, "target": rec.get("target"),
        "prior_status": rec.get("status"),
    })
    return rec


def record_execution(approval_id: str, *, ok: bool, result=None, error: str | None = None) -> dict | None:
    """Record the outcome of executing an approved external action. Sets the
    record's `execution` field to {status, result, error, at}. Audited. Returns
    the updated record, or None if the approval is gone."""
    with _LOCK:
        items = _expire(_load())
        rec = next((r for r in items if r["id"] == approval_id), None)
        if rec is None:
            return None
        rec["execution"] = {
            "status": "executed" if ok else "failed",
            "result": result,
            "error": error,
            "at": _iso(_now()),
        }
        _save(items)
    permissions.record_audit_event({
        "action_type": "external_action_executed" if ok else "external_action_failed",
        "approval_id": approval_id,
        "target": rec.get("target"),
        "risk": rec.get("risk_level"),
        "external_action": rec.get("action_type"),
        "error": error,
        "session_id": rec.get("session_id"),
        "job_id": rec.get("job_id"),
    })
    return rec
