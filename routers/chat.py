"""Chat routes: uploads, sync + async Claude turns, job polling/history/stop,
and cross-device session sync.

Patchable seams (run_claude, the live-turn registry, session_store) are read
as ``server.<name>`` at request time — see routers/__init__.py."""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

import config
import job_store
import permissions
from models import AskRequest, SessionSyncPush
from rate_limit import limiter
from security import require_token

import server

router = APIRouter()

UPLOAD_ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif",
    ".pdf", ".txt", ".md", ".csv", ".json", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".yml", ".yaml",
}


def _heic_to_jpeg(data: bytes, name: str) -> tuple[bytes, str]:
    """Convert iPhone HEIC/HEIF bytes to JPEG so the Read tool can view them."""
    import io
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    new = re.sub(r"\.(heic|heif)$", ".jpg", name, flags=re.IGNORECASE)
    if not new.lower().endswith(".jpg"):
        new += ".jpg"
    return buf.getvalue(), new


def _sweep_uploads() -> None:
    """Delete uploads older than the TTL so the folder can't grow unbounded."""
    try:
        cutoff = time.time() - config.UPLOAD_TTL_SECONDS
        for f in config.UPLOAD_DIR.glob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


@router.post("/upload", dependencies=[Depends(require_token)])
@limiter.limit("30/minute")
async def upload(request: Request, file: UploadFile = File(...)):
    """Accept one file (image or doc), store it off-vault, return its server path.
    The path is then sent as an attachment on a later /ask or /ask_async turn, where
    Claude's Read tool views it. iPhone HEIC photos are converted to JPEG first."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (25 MB max)")
    raw_name = file.filename or "upload"
    ext = Path(raw_name).suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"Unsupported type: {ext or 'unknown'}")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)[:60] or "upload"
    if ext in (".heic", ".heif"):
        try:
            data, safe = _heic_to_jpeg(data, safe)
        except Exception:
            pass  # conversion unavailable — store as-is; Read may still cope
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:12]
    dest = config.UPLOAD_DIR / f"{uid}_{safe}"

    # --- Permission + safety layer (Level 3) --------------------------------
    # An upload is a server-managed infrastructure write (into UPLOAD_DIR), not an
    # agent edit of the user's own files, so it doesn't pass through the human
    # approval gate. It is still permission-checked, backed up on overwrite, and
    # audited — the same machinery every future content write will use.
    if not permissions.is_path_allowed_for_write(dest):
        permissions.record_audit_event({
            "action_type": "write", "target": str(dest), "allowed": False,
            "requires_approval": False, "approved": False, "risk": "medium",
            "reason": "upload target not in an allowed write directory",
        })
        raise HTTPException(status_code=403, detail="Upload directory not permitted")
    backup = permissions.make_backup_before_write(dest)  # no-op for a new filename
    try:
        dest.write_bytes(data)
    except Exception as e:
        server.log.error("upload save failed: %s", e)
        permissions.record_audit_event({
            "action_type": "write", "target": str(dest), "allowed": True,
            "approved": True, "risk": "low", "reason": f"upload save failed: {e}",
        })
        raise HTTPException(status_code=500, detail=f"Save failed: {e}")
    permissions.record_audit_event({
        "action_type": "write", "target": str(dest), "allowed": True,
        "requires_approval": False, "approved": True, "risk": "low",
        "reason": "upload saved", "bytes": len(data),
        "backup_path": str(backup) if backup else None,
    })
    _sweep_uploads()
    return {"id": uid, "path": str(dest), "name": safe}


@router.post("/ask", dependencies=[Depends(require_token)])
@limiter.limit("30/minute")
async def ask(request: Request, body: AskRequest):
    message = body.message.strip()
    if not message and not body.attachments:
        raise HTTPException(status_code=400, detail="Empty message")
    return await server.run_claude(
        message, body.session_id, mode=body.mode or "voice",
        attachments=body.attachments,
    )


@router.post("/ask_async", dependencies=[Depends(require_token)])
@limiter.limit("30/minute")
async def ask_async(request: Request, body: AskRequest):
    """Kick off a Claude turn in the background; return a job_id immediately. The
    job is persisted before the task starts, so it survives a restart."""
    message = body.message.strip()
    if not message and not body.attachments:
        raise HTTPException(status_code=400, detail="Empty message")
    job_store.sweep(config.JOB_HISTORY_TTL_SECONDS)
    mode = server._normalize_mode(body.mode or "voice")
    job_id = uuid.uuid4().hex
    # Persist only a short, truncated summary of the user's input (never the full
    # prompt) so job history is readable without storing private text wholesale.
    summary = (message or "").strip()[: config.JOB_INPUT_SUMMARY_MAX] or None
    job_store.create_job(
        job_id, mode=mode, session_id=body.session_id,
        input_summary=summary, pid=os.getpid(),
    )
    server.keep_task(asyncio.create_task(
        server._run_job(job_id, message, body.session_id, mode, body.attachments)
    ))
    return {"job_id": job_id}


@router.get("/poll/{job_id}", dependencies=[Depends(require_token)])
async def poll(job_id: str):
    """Return a job's status/result in the original wire shape (status is
    running/done/error). Unlike the old in-memory store this no longer deletes the
    job — it's retained for history — but a terminal result is marked delivered.
    The PWA dedupes by `ts`, so an idempotent re-poll is harmless."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    out = job_store.to_wire(job)
    if out["status"] == "running":
        # Live activity view (code-mode streaming turns): the last few tool
        # calls + a step count, so the phone can show WHAT it's doing, not just
        # "Thinking…". Ephemeral — gone once the turn ends.
        prog = server.JOB_PROGRESS.get(job_id)
        if prog:
            out["steps"] = len(prog)
            out["progress"] = prog[-8:]
    if out["status"] in ("done", "error"):
        job_store.mark_delivered(job_id)
    return out


# --- Job history (Phase 5) --------------------------------------------------
# Persistent job records, for the PWA's future job-history view and the planned
# desktop companion/tray. Token-gated. Exposes the full canonical record
# (statuses queued/running/complete/failed/interrupted/cancelled), including the
# truncated input summary — never the full prompt, never any secret.

@router.get("/jobs", dependencies=[Depends(require_token)])
async def list_jobs(status: str | None = None, limit: int = 50):
    """Recent jobs, newest first. Optional ?status= (canonical status) and ?limit=."""
    items = job_store.list_jobs(limit=limit, status=status)
    return {"jobs": items, "count": len(items)}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
async def get_job(job_id: str):
    """One job's full persistent record. 404 if unknown."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job


@router.post("/jobs/{job_id}/stop", dependencies=[Depends(require_token)])
async def stop_job(job_id: str):
    """Stop a RUNNING turn: kill the Claude process (and its children) and mark
    the job cancelled. The poll loop then reports a clean "Stopped by user." —
    the chat keeps its resume id, so the user just speaks again to redirect.
    409 when the job isn't running (already finished, or lost to a restart)."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    proc = server.RUNNING_PROCS.get(job_id)
    if proc is None or proc.returncode is not None:
        raise HTTPException(status_code=409, detail="Job is not running")
    # Order matters: flag first, then kill — the reader must see the flag when
    # the process dies, or a stop would be reported as a crash.
    server.CANCELLED_JOBS.add(job_id)
    await server._kill_proc_tree(proc)
    permissions.record_audit_event({
        "action_type": "job_stopped", "target": job_id,
        "allowed": True, "requires_approval": False, "approved": True,
        "risk": "low", "reason": "user stopped a running turn",
    })
    return {"ok": True, "job_id": job_id}


# --- Cross-device chat sync -------------------------------------------------
# The server holds one authoritative copy of the user's chats + transcripts so a
# chat started on one device shows up on every other signed-in device. Merge is
# last-write-wins per session by the client-stamped `updated` ms timestamp.

@router.get("/sessions", dependencies=[Depends(require_token)])
async def sessions_pull(since: int = 0):
    """Every session changed since the client's cursor (`since` is a server-assigned
    `seq`, NOT a timestamp). Keying delivery on `seq` instead of the client `updated`
    clock is what lets a delete from a lagging-clock device still reach every other
    device — otherwise its tombstone could sort below a peer's cursor and vanish.
    Clients advance their cursor to the highest `seq` they applied."""
    if server.session_store is None or not config.SESSION_SYNC_ENABLED:
        return {"enabled": False, "sessions": [], "now": int(time.time() * 1000)}
    # Self-maintaining retention: sweep tombstones past the archive window on each
    # pull (cheap indexed delete; the sync poll runs it often enough to keep the DB
    # from accreting deleted chats without needing a separate scheduled job).
    retention_ms = int(getattr(config, "SESSION_ARCHIVE_RETENTION_DAYS", 7)) * 86_400_000
    try:
        server.session_store.purge_expired(retention_ms)
    except Exception:
        pass
    return {
        "enabled": True,
        "sessions": server.session_store.changed_since(since),
        "now": server.session_store.now_ms(),
        "archive_retention_days": int(getattr(config, "SESSION_ARCHIVE_RETENTION_DAYS", 7)),
    }


@router.post("/sessions", dependencies=[Depends(require_token)])
async def sessions_push(body: SessionSyncPush):
    """Merge the client's locally-changed sessions (last-write-wins by `updated`).
    A record not strictly newer than the stored copy is ignored, so a stale device
    can't overwrite a fresher edit from another device."""
    if server.session_store is None or not config.SESSION_SYNC_ENABLED:
        return {"enabled": False, "applied": 0}
    res = server.session_store.upsert([r.model_dump() for r in body.sessions])
    return {"enabled": True, "applied": res["applied"]}
