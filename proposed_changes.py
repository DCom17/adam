"""
Adam — proposed-change store (Phase 3 controlled-write flow).

This is the file-specific half of the approval system. Where `approvals.py` is a
generic yes/no queue, a *proposed change* carries everything needed to actually
apply a file edit later: the target path, the action (create/edit/replace/
delete/rename), and the proposed content.

The lifecycle is: created -> (approved | denied) -> applied | failed. **Only the
server applies the change**, and only through `apply()`, which re-runs the full
permission check, backs up an existing file before overwriting, refuses blocked
/ protected / out-of-allow-list targets, gates destructive actions on approval,
and writes the whole lifecycle to the audit log. Claude never touches the file —
it only proposes; the server is the enforcement boundary.

Persisted to data/state/proposed_changes.json. Single user, single process.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import config
import diffs
import permissions
import self_edit_guard

DEFAULT_TTL_SECONDS = 7 * 24 * 3600
VALID_ACTIONS = ("create", "edit", "replace", "delete", "rename")

_LOCK = threading.Lock()


def _now() -> int:
    return int(time.time())


def _iso(ts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _load() -> list[dict]:
    try:
        return json.loads(Path(config.PROPOSED_CHANGES_FILE).read_text("utf-8"))
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    Path(config.PROPOSED_CHANGES_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.PROPOSED_CHANGES_FILE).write_text(
        json.dumps(items, indent=2), encoding="utf-8"
    )


def _resolve_target(p: str | os.PathLike) -> Path:
    """Resolve a proposed target. Relative paths land under the drafts dir, so a
    proposal of 'notes.md' is safe-by-default; absolute paths are honored as-is.

    F1 (v0.7.0): if a relative path, interpreted against the project root, already
    lands inside an allowed write dir, honor that placement instead of nesting it a
    second time under the drafts dir. Without this, a caller passing a path that
    re-includes the write dir (e.g. 'data/drafts/notes.md' when drafts IS
    data/drafts) produced data/drafts/data/drafts/notes.md. The permission check
    still gates the result either way, so this only fixes WHERE a legit proposal
    lands — it never widens what is writable."""
    raw = os.path.expandvars(os.path.expanduser(str(p).strip()))
    pp = Path(raw)
    if pp.is_absolute():
        return pp.resolve()
    write_dirs = [str(config.DRAFTS_DIR), str(config.OUTPUTS_DIR), *config.PERM_WRITE_DIRS]
    root_rel = (Path(config.ROOT) / pp).resolve()
    if permissions.is_within_allowed_dir(root_rel, write_dirs):
        return root_rel
    return (Path(config.DRAFTS_DIR) / pp).resolve()


def _is_destructive(action: str, target: Path) -> bool:
    """A change is destructive if it removes/renames, or overwrites an existing
    file. Creating/editing a brand-new file is not."""
    if action in ("delete", "rename"):
        return True
    return target.exists()


def is_brain_self_write(rec: dict) -> bool:
    """True if a proposed change is a NON-destructive write INSIDE the configured
    vault — the brain self-write lane. The server auto-applies these even when the
    global auto-apply pref is off; destructive vault writes (delete/rename/overwrite)
    and writes outside the vault are NOT this lane and still require approval."""
    if not getattr(config, "BRAIN_WRITE_ENABLED", False):
        return False
    if rec.get("destructive"):
        return False
    return permissions.is_within_vault(rec.get("target_path", ""))


def create(
    *,
    target_path: str,
    action: str = "create",
    content: str | None = None,
    new_path: str | None = None,
    summary: str = "",
    risk_level: str | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict:
    """Record a proposed change in 'pending' state and return it."""
    action = (action or "create").strip().lower()
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action: {action}")
    target = _resolve_target(target_path)
    new_target = _resolve_target(new_path) if new_path else None

    # F2 (v0.7.0): validate the write target at CREATE time, not only at apply.
    # A proposal that could never be applied (blocked / protected / outside the
    # write allow-list) is refused up front instead of sitting pending until apply.
    # apply() STILL re-runs this same check (defense in depth) — this does not
    # replace the apply-time gate, it adds an earlier one. Internal callers
    # (extract_from_reply) already skip a raised proposal; the /proposed-changes
    # endpoint maps ValueError -> 400.
    if not permissions.is_path_allowed_for_write(target):
        raise ValueError(
            "target path not allowed for write (blocked, protected, or outside "
            "the write allow-list)"
        )
    if new_target is not None and not permissions.is_path_allowed_for_write(new_target):
        raise ValueError(
            "rename destination not allowed for write (blocked, protected, or "
            "outside the write allow-list)"
        )

    destructive = _is_destructive(action, target)
    if risk_level is None:
        risk_level = "high" if destructive else "medium"

    # Phase 4 — capture the review baseline at proposal time: a unified diff plus
    # SHA-256 hashes/sizes of the current target and the proposed content. These
    # let the user see exactly what changes and let apply() detect if the file
    # was edited underneath the proposal (staleness/conflict).
    diff_fields = diffs.build_diff_fields(
        target_path=target, action=action, content=content, new_path=new_target,
    )
    risk_reason = (
        "destructive (delete/rename or overwrites an existing file)"
        if destructive else "non-destructive write"
    )

    now = _now()
    rec = {
        "id": uuid.uuid4().hex[:12],
        "created_at": _iso(now),
        "created_at_ts": now,
        "session_id": session_id,
        "job_id": job_id,
        "target_path": str(target),
        "new_path": str(new_target) if new_target else None,
        "action": action,
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "destructive": destructive,
        "summary": summary or f"{action} {target.name}",
        "content": content,
        "status": "pending",
        # --- Phase 4 review baseline ---
        "original_hash": diff_fields["original_hash"],
        "original_size": diff_fields["original_size"],
        "proposed_hash": diff_fields["proposed_hash"],
        "proposed_size": diff_fields["proposed_size"],
        "diff": diff_fields["diff"],
        "diff_available": diff_fields["diff_available"],
        "diff_unavailable_reason": diff_fields["diff_unavailable_reason"],
        "diff_created_at": diff_fields["diff_created_at"],
        # --- apply / conflict bookkeeping (filled later) ---
        "current_hash": None,
        "current_size": None,
        "stale_checked_at": None,
        "conflict_reason": None,
        "backup_path": None,
        "applied_at": None,
        "applied_by": None,
        "apply_error": None,
        "error": None,
        "expires_at": _iso(now + ttl_seconds),
        "expires_at_ts": now + ttl_seconds,
    }
    with _LOCK:
        items = _expire(_load())
        items.append(rec)
        _save(items)
    permissions.record_audit_event({
        "action_type": "proposed_change_created",
        "proposed_change_id": rec["id"],
        "target": rec["target_path"],
        "change_action": action,
        "risk": risk_level,
        "destructive": destructive,
        "reason": rec["summary"],
        "session_id": session_id,
        "job_id": job_id,
    })
    return _public(rec)


def _expire(items: list[dict]) -> list[dict]:
    now = _now()
    for it in items:
        if it.get("status") == "pending" and it.get("expires_at_ts", 0) and now > it["expires_at_ts"]:
            it["status"] = "expired"
    return items


def _public(rec: dict) -> dict:
    """A copy safe to return over the API. Content can be large; include a length
    and a short preview rather than dumping huge blobs into list responses."""
    out = dict(rec)
    content = out.get("content")
    out["content_len"] = len(content) if isinstance(content, str) else 0
    return out


def list_all(status: str | None = None, *, include_content: bool = False) -> list[dict]:
    with _LOCK:
        items = _expire(_load())
        _save(items)
    items = sorted(items, key=lambda r: r.get("created_at_ts", 0), reverse=True)
    if status:
        items = [r for r in items if r.get("status") == status]
    out = []
    for r in items:
        pub = _public(r)
        if not include_content:
            pub.pop("content", None)
            pub.pop("diff", None)  # large; fetch via /{id}/diff on demand
        out.append(pub)
    return out


def get(change_id: str, *, include_content: bool = True) -> dict | None:
    with _LOCK:
        items = _expire(_load())
    rec = next((r for r in items if r["id"] == change_id), None)
    if rec is None:
        return None
    pub = _public(rec)
    if not include_content:
        pub.pop("content", None)
    return pub


def _set_status(change_id: str, new_status: str, **fields) -> dict | None:
    """Transition a record and persist. Returns the raw record or None if absent."""
    with _LOCK:
        items = _expire(_load())
        rec = next((r for r in items if r["id"] == change_id), None)
        if rec is None:
            return None
        rec["status"] = new_status
        rec.update(fields)
        # Stamp when a change reached a terminal state, so the server can tell the
        # agent on its next turn which proposals were resolved (and how) since.
        if new_status in ("applied", "denied", "conflict", "failed", "rolled_back") and not rec.get("resolved_at_ts"):
            rec["resolved_at_ts"] = _now()
            rec["resolved_at"] = _iso(rec["resolved_at_ts"])
        _save(items)
    return rec


def resolved_since(ts: int) -> list[dict]:
    """Terminal changes resolved after `ts` (oldest first), for feeding outcome
    context into the agent's next turn so it can acknowledge naturally."""
    with _LOCK:
        items = _expire(_load())
    out = [r for r in items
           if r.get("status") in ("applied", "denied", "conflict", "failed", "rolled_back")
           and int(r.get("resolved_at_ts") or 0) > int(ts or 0)]
    out.sort(key=lambda r: int(r.get("resolved_at_ts") or 0))
    return [_public(r) for r in out]


def approve(change_id: str) -> dict | None:
    rec = _get_raw(change_id)
    if rec is None:
        return None
    if rec["status"] != "pending":
        return _public(rec)  # already resolved — caller inspects status
    rec = _set_status(change_id, "approved")
    permissions.record_audit_event({
        "action_type": "proposed_change_approved",
        "proposed_change_id": change_id, "target": rec.get("target_path"),
        "approved": True, "risk": rec.get("risk_level"),
    })
    return _public(rec)


def deny(change_id: str) -> dict | None:
    rec = _get_raw(change_id)
    if rec is None:
        return None
    if rec["status"] != "pending":
        return _public(rec)
    rec = _set_status(change_id, "denied")
    permissions.record_audit_event({
        "action_type": "proposed_change_denied",
        "proposed_change_id": change_id, "target": rec.get("target_path"),
        "approved": False, "risk": rec.get("risk_level"),
    })
    return _public(rec)


def dismiss(change_id: str) -> dict | None:
    """Remove a proposed change from the store entirely — clears it from the panel.
    Works on any status (pending or resolved); the audit log retains its lifecycle,
    this only drops the panel entry. Returns the removed record, or None if absent."""
    with _LOCK:
        items = _expire(_load())
        rec = next((r for r in items if r["id"] == change_id), None)
        if rec is None:
            return None
        _save([r for r in items if r["id"] != change_id])
    permissions.record_audit_event({
        "action_type": "proposed_change_dismissed",
        "proposed_change_id": change_id, "target": rec.get("target_path"),
        "prior_status": rec.get("status"),
    })
    return _public(rec)


def _get_raw(change_id: str) -> dict | None:
    with _LOCK:
        items = _expire(_load())
    return next((r for r in items if r["id"] == change_id), None)


def apply(change_id: str) -> tuple[dict | None, str | None]:
    """Apply an APPROVED change to disk, enforcing the full permission policy.

    Returns (record, error). `error` is None on success. The change is never
    applied unless it is approved; the target must pass the write permission
    check (allow-list + blocked + protected); an existing file is backed up
    first; and the lifecycle is audited."""
    rec = _get_raw(change_id)
    if rec is None:
        return None, "unknown proposed change"

    if rec["status"] == "applied":
        return _public(rec), None  # idempotent
    if rec["status"] != "approved":
        return _public(rec), f"not approved (status={rec['status']})"

    action = rec["action"]
    target = Path(rec["target_path"])
    new_path = Path(rec["new_path"]) if rec.get("new_path") else None

    def _fail(reason: str) -> tuple[dict, str]:
        permissions.record_audit_event({
            "action_type": "proposed_change_failed",
            "proposed_change_id": change_id, "target": str(target),
            "allowed": False, "reason": reason, "risk": rec.get("risk_level"),
        })
        r = _set_status(change_id, "failed", error=reason, apply_error=reason)
        return _public(r), reason

    def _conflict(reason: str, current_hash: str | None) -> tuple[dict, str]:
        """Block an apply because the target changed since the proposal. The
        change is NOT applied and NOT failed — it goes to 'conflict' so the user
        can review the new diff (via refresh-diff) and re-approve, rather than
        silently overwriting newer work."""
        permissions.record_audit_event({
            "action_type": "proposed_change_conflict",
            "proposed_change_id": change_id, "target": str(target),
            "allowed": False, "reason": reason, "risk": rec.get("risk_level"),
            "original_hash": rec.get("original_hash"), "current_hash": current_hash,
        })
        r = _set_status(
            change_id, "conflict",
            conflict_reason=reason, current_hash=current_hash,
            stale_checked_at=diffs.now_iso(), apply_error=reason,
        )
        return _public(r), reason

    # 1. Permission gate — target (and rename destination) must be writable.
    if not permissions.is_path_allowed_for_write(target):
        return _fail("target not in an allowed write directory (or blocked/protected)")
    if action == "rename":
        if new_path is None:
            return _fail("rename requires new_path")
        if not permissions.is_path_allowed_for_write(new_path):
            return _fail("rename destination not allowed")

    # 2. Content sanity for write actions.
    if action in ("create", "edit", "replace"):
        if rec.get("content") is None:
            return _fail(f"{action} requires content")
        if len(rec["content"].encode("utf-8")) > config.PERM_MAX_FILE_SIZE_MB * 1024 * 1024:
            return _fail(f"content exceeds max_file_size_mb ({config.PERM_MAX_FILE_SIZE_MB} MB)")

    # 2b. Self-edit pre-flight — when this change targets the app's OWN source, validate
    #     the proposed content in isolation BEFORE we touch the live file, so a broken
    #     .py/.json never reaches disk. This is the primary safety of the self-edit
    #     auto-rollback rail; the post-commit health probe (step 5b) is the second net.
    if (getattr(config, "PERM_SELF_EDIT_AUTO_ROLLBACK", True)
            and action in ("create", "edit", "replace")
            and permissions.is_app_self_editable(target)):
        ok, why = self_edit_guard.preflight(target, rec.get("content"))
        if not ok:
            return _fail(f"self-edit pre-flight failed: {why}")

    # 3. Destructive gate — destructive applies require an approved record AND the
    #    policy to permit them. (We're already approved here; this enforces the
    #    policy switch and documents intent.)
    if rec.get("destructive") and config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE and rec["status"] != "approved":
        return _fail("destructive change requires approval")

    # 3b. Staleness / conflict gate — has the target changed since the proposal
    #     was created? Compare the current on-disk hash to the baseline captured
    #     at create() time. If they differ, refuse rather than overwrite newer
    #     work. Legacy records (created before Phase 4, no baseline hash) skip
    #     this check and behave as before. For a rename we protect the SOURCE
    #     (the record's target_path); for everything else, the target itself.
    conflict_reason, current_hash, _skipped = _check_staleness(rec, target, action)
    rec["current_hash"] = current_hash
    rec["stale_checked_at"] = diffs.now_iso()
    if conflict_reason:
        return _conflict(conflict_reason, current_hash)

    # 4. Back up an existing file before we overwrite or delete it.
    backup = None
    try:
        if action in ("create", "edit", "replace", "delete") and target.exists():
            backup = permissions.make_backup_before_write(target)
        if action == "rename" and new_path is not None and new_path.exists():
            backup = permissions.make_backup_before_write(new_path)

        # 5. Perform the operation.
        if action in ("create", "edit", "replace"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rec["content"], encoding="utf-8")
        elif action == "delete":
            if target.exists():
                target.unlink()
        elif action == "rename":
            new_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, new_path)
    except Exception as e:  # noqa: BLE001
        return _fail(f"apply failed: {e}")

    # 5b. Self-edit auto-rollback rail — if this app-source .py change stops the app
    #     from importing cleanly, restore the pre-change state and report rolled_back.
    #     The health probe boots a throwaway subprocess (`import server`) so nothing is
    #     imported into THIS process, and the rollback is a pure byte-restore, so it
    #     works even when the app itself no longer imports. (4th always-on rail.)
    if (getattr(config, "PERM_SELF_EDIT_AUTO_ROLLBACK", True)
            and permissions.is_app_self_editable(target)
            and self_edit_guard.needs_health_probe(target, action)):
        healthy, why = self_edit_guard.health_probe()
        if not healthy:
            rolled, how = _rollback_self_edit(action, target, backup, new_path)
            healthy_after, _ = self_edit_guard.health_probe()
            # Finding #3: if the tree is STILL unhealthy after a clean byte-restore,
            # the probe failure was environmental (a moved .env, a PATH glitch, a
            # sibling module already broken) — NOT caused by this change. The
            # healthy_after_rollback=False signal already exists; surface it in the
            # user-facing message so a good edit isn't reported as if it were at fault.
            environmental = not healthy_after
            if environmental:
                user_msg = (
                    "reverted — but the app was already unhealthy before this change "
                    f"(environment/config, not this edit): {why}"
                )
            else:
                user_msg = f"auto-rolled-back: {why}"
            permissions.record_audit_event({
                "action_type": "self_edit_rolled_back",
                "proposed_change_id": change_id, "target": str(target),
                "change_action": action, "reason": why,
                "rolled_back": rolled, "rollback_detail": how,
                "healthy_after_rollback": healthy_after,
                "environmental": environmental,
                "backup_path": str(backup) if backup else None,
                "session_id": rec.get("session_id"), "job_id": rec.get("job_id"),
            })
            r = _set_status(
                change_id, "rolled_back",
                backup_path=str(backup) if backup else None,
                apply_error=user_msg,
                error=user_msg,
                rolled_back=True, rollback_detail=how,
                healthy_after_rollback=healthy_after,
                environmental=environmental,
                applied_at=None, applied_by=None,
            )
            return _public(r), (
                f"self-edit rolled back — {user_msg}" if environmental
                else f"self-edit rolled back: {why}"
            )

    # Record the post-apply on-disk hash so the applied state is itself auditable.
    applied_hash, applied_size = diffs.hash_and_size_of_file(
        new_path if action == "rename" and new_path else target
    )
    r = _set_status(
        change_id, "applied",
        backup_path=str(backup) if backup else None, applied_at=_iso(_now()),
        applied_by="server", current_hash=applied_hash, current_size=applied_size,
        conflict_reason=None, apply_error=None, error=None,
    )
    permissions.record_audit_event({
        "action_type": "proposed_change_applied",
        "proposed_change_id": change_id, "target": str(target),
        "change_action": action, "allowed": True, "approved": True,
        "risk": rec.get("risk_level"), "backup_path": str(backup) if backup else None,
        "current_hash": applied_hash,
        "session_id": rec.get("session_id"), "job_id": rec.get("job_id"),
    })
    return _public(r), None


def undo_last() -> dict | None:
    """Undo the most recently applied change: restore its backup over the target, or
    delete a newly-created file (which had no prior version). Marks it undone so it can't
    be undone twice. Returns a small summary, or None if there's nothing safe to undo."""
    with _LOCK:
        items = _load()
        applied = [r for r in items if r.get("status") == "applied" and not r.get("undone")]
        if not applied:
            return None
        applied.sort(key=lambda r: r.get("resolved_at_ts") or r.get("created_at_ts", 0), reverse=True)
        rec = applied[0]
        target = rec.get("target_path")
        backup = rec.get("backup_path")
        action = rec.get("action")
        try:
            if backup and target and Path(backup).is_file():
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_bytes(Path(backup).read_bytes())
                how = "restored from backup"
            elif action == "create" and target and Path(target).is_file():
                Path(target).unlink()
                how = "removed the created file"
            else:
                return None  # no backup and not a fresh create — nothing safe to restore
        except OSError as e:
            return {"ok": False, "error": str(e), "target_path": target}
        rec["undone"] = True
        rec["undone_at_ts"] = _now()
        _save(items)
        return {"ok": True, "id": rec.get("id"), "target_path": target,
                "action": action, "how": how, "backup_path": backup}


def _rollback_self_edit(
    action: str, target: Path, backup, new_path: Path | None = None
) -> tuple[bool, str]:
    """Revert a just-applied app-source change so the tree returns to its last-good
    state. This is a pure filesystem operation — it never imports the (possibly
    broken) app — so it works precisely when it's needed most: after a self-edit
    bricked the import. Returns (ok, human-readable note).

    By action:
      * rename          — move the file back from new_path to the original target
                          (os.replace), then restore any backup of a destination the
                          rename overwrote. This is what makes an app-source .py
                          rename recoverable (the old import path reappears).
      * create          — delete the freshly-created file (it had no prior version).
      * edit/replace/…  — restore the pre-change backup over the target."""
    try:
        if action == "rename":
            if new_path is None:
                return False, "rename rollback missing new_path"
            src, dst = Path(target), Path(new_path)
            if dst.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                os.replace(dst, src)  # move it back to the original path
            elif not src.exists():
                return False, "renamed file not found at either path"
            # If the rename overwrote a pre-existing destination, put it back too.
            if backup and Path(backup).is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(Path(backup).read_bytes())
                return True, "reversed the rename and restored the overwritten destination"
            return True, "reversed the rename"
        if backup and Path(backup).is_file():
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(Path(backup).read_bytes())
            return True, "restored from backup"
        if action == "create" and Path(target).is_file():
            Path(target).unlink()
            return True, "removed the created file"
        return False, "no backup available to restore"
    except OSError as e:
        return False, f"rollback failed: {e}"


def _check_staleness(
    rec: dict, guard_path: Path, action: str
) -> tuple[str | None, str | None, bool]:
    """Decide whether `guard_path` has drifted from the proposal's baseline.

    Returns (conflict_reason, current_hash, skipped):
      * conflict_reason  — non-None means block the apply with this message
      * current_hash     — the file's current SHA-256 (None if absent/unreadable)
      * skipped          — True for legacy records with no baseline hash field

    A record created before Phase 4 has no "original_hash" key; for those we skip
    the check (graceful backward compatibility) and let the apply proceed as it
    did before. For Phase 4 records, original_hash == None means the target was
    absent when proposed (a create); a non-None value is the bytes-hash captured
    then."""
    if "original_hash" not in rec:
        return None, None, True  # legacy record — no baseline to compare

    original_hash = rec.get("original_hash")
    current_hash, _ = diffs.hash_and_size_of_file(guard_path)

    if action in ("create", "edit", "replace"):
        if original_hash is None:
            # Target was absent at proposal time. If something exists now, applying
            # would overwrite a file that appeared after the proposal.
            if current_hash is not None:
                return (
                    "target now exists but was absent when the change was proposed",
                    current_hash, False,
                )
            return None, current_hash, False
        if current_hash is None:
            return "target was deleted since the change was proposed", current_hash, False
        if current_hash != original_hash:
            return "target file changed since the change was proposed", current_hash, False
        return None, current_hash, False

    if action == "delete":
        if original_hash is None or current_hash is None:
            return None, current_hash, False  # nothing to protect / already gone
        if current_hash != original_hash:
            return "target file changed since the deletion was proposed", current_hash, False
        return None, current_hash, False

    if action == "rename":
        if original_hash is None or current_hash is None:
            return None, current_hash, False
        if current_hash != original_hash:
            return "source file changed since the rename was proposed", current_hash, False
        return None, current_hash, False

    return None, current_hash, False


def refresh_diff(change_id: str) -> dict | None:
    """Re-baseline a proposal against the file as it is NOW and regenerate its
    diff. Use this after a conflict (or any manual edit) so the user can review
    the up-to-date diff before applying.

    The proposed *content* is unchanged — only the baseline (original_hash/size)
    and the rendered diff are recomputed. Because the diff the user originally
    approved may now be different, this resets a non-pending record back to
    'pending' so it must be re-approved. Returns the refreshed record or None."""
    rec = _get_raw(change_id)
    if rec is None:
        return None
    if rec["status"] in ("applied", "expired"):
        return _public(rec)  # terminal — nothing to refresh

    target = Path(rec["target_path"])
    new_target = Path(rec["new_path"]) if rec.get("new_path") else None
    diff_fields = diffs.build_diff_fields(
        target_path=target, action=rec["action"],
        content=rec.get("content"), new_path=new_target,
    )
    fields = dict(diff_fields)
    fields["conflict_reason"] = None
    fields["apply_error"] = None
    fields["error"] = None
    fields["current_hash"] = None
    fields["stale_checked_at"] = diffs.now_iso()
    # Force re-approval: the diff may have changed since it was last reviewed.
    r = _set_status(change_id, "pending", **fields)
    permissions.record_audit_event({
        "action_type": "proposed_change_refreshed",
        "proposed_change_id": change_id, "target": str(target),
        "original_hash": fields["original_hash"], "risk": rec.get("risk_level"),
    })
    return _public(r)


def status_preview(change_id: str) -> dict | None:
    """A live, read-only status view: the stored status plus a fresh check of
    whether the target currently matches the proposal's baseline. Does NOT mutate
    the record. Lets the UI warn 'this file changed' before the user hits Apply."""
    rec = _get_raw(change_id)
    if rec is None:
        return None
    action = rec.get("action", "create")
    guard = Path(rec["target_path"])
    conflict_reason, current_hash, skipped = _check_staleness(rec, guard, action)
    return {
        "id": rec["id"],
        "status": rec.get("status"),
        "action": action,
        "target_path": rec.get("target_path"),
        "risk_level": rec.get("risk_level"),
        "original_hash": rec.get("original_hash"),
        "current_hash": current_hash,
        "stale": bool(conflict_reason),
        "stale_reason": conflict_reason,
        "staleness_checked": not skipped,
        "conflict_reason": rec.get("conflict_reason"),
        "backup_path": rec.get("backup_path"),
        "applied_at": rec.get("applied_at"),
        "applied_by": rec.get("applied_by"),
        "apply_error": rec.get("apply_error"),
        "diff_available": rec.get("diff_available"),
        "diff_unavailable_reason": rec.get("diff_unavailable_reason"),
    }


# --- Extraction from Claude replies -----------------------------------------

import re as _re

_PROPOSE_RE = _re.compile(
    r"<<PROPOSE\s+(?P<attrs>[^>]*?)>>\n?(?P<body>.*?)\n?<<END_PROPOSE>>",
    _re.DOTALL,
)
_ATTR_RE = _re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def extract_from_reply(
    text: str, *, session_id: str | None = None, job_id: str | None = None
) -> tuple[str, list[dict]]:
    """Pull <<PROPOSE>>…<<END_PROPOSE>> blocks out of a Claude reply, create a
    proposed change for each, and return (cleaned_text, [records]).

    Best-effort: a malformed block is skipped, never raised. When
    force_outputs_to_drafts is on, every proposed target is routed under the
    drafts dir (by basename) so an applied change can never escape the safe area
    unless an operator widens the write allow-list."""
    if not text or "<<PROPOSE" not in text:
        return text, []
    records: list[dict] = []
    for m in _PROPOSE_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group("attrs") or ""))
        path = (attrs.get("path") or "").strip()
        if not path:
            continue
        if config.FORCE_OUTPUTS_TO_DRAFTS:
            # Brain self-write lane: preserve an ABSOLUTE path inside the configured
            # vault so the change can land in the brain (and auto-apply); flatten
            # everything else to a basename so it lands safely under drafts.
            _expanded = os.path.expandvars(os.path.expanduser(path.strip()))
            keep_in_vault = (
                getattr(config, "BRAIN_WRITE_ENABLED", False)
                and Path(_expanded).is_absolute()
                and permissions.is_within_vault(path)
            )
            if not keep_in_vault:
                path = Path(path).name  # relative -> resolves under drafts in create()
        body = m.group("body")
        action = (attrs.get("action") or "create").strip().lower()
        if action not in VALID_ACTIONS:
            action = "create"
        try:
            rec = create(
                target_path=path,
                action=action,
                content=None if action in ("delete", "rename") else body,
                new_path=attrs.get("newpath"),
                summary=attrs.get("summary", ""),
                risk_level=attrs.get("risk"),
                session_id=session_id,
                job_id=job_id,
            )
            records.append(rec)
        except Exception:
            continue
    cleaned = _PROPOSE_RE.sub("", text).strip()
    return cleaned, records
