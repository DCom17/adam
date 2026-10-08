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

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

import config
import job_store
import permissions
from models import AskRequest, OperatorAnswer, OperatorSteer, ProjectSyncPush, SessionSyncPush
from rate_limit import limiter
from security import require_token

import operator_session
import plus_gate
import server

router = APIRouter()

UPLOAD_ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif",
    ".pdf", ".txt", ".md", ".csv", ".json", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".yml", ".yaml",
}


# Leading bytes each binary format must actually start with. Only formats with a
# stable, well-known signature are listed — the text types (.txt/.md/.csv/.json/
# .py/…) have none, so there is nothing to check and they are skipped rather than
# guessed at. A tuple of (offset, magic) pairs; any one matching is enough.
_UPLOAD_MAGIC: dict[str, tuple[tuple[int, bytes], ...]] = {
    ".png":  ((0, b"\x89PNG\r\n\x1a\n"),),
    ".gif":  ((0, b"GIF87a"), (0, b"GIF89a")),
    ".jpg":  ((0, b"\xff\xd8\xff"),),
    ".jpeg": ((0, b"\xff\xd8\xff"),),
    ".bmp":  ((0, b"BM"),),
    ".pdf":  ((0, b"%PDF-"),),
    ".webp": ((0, b"RIFF"), (8, b"WEBP")),
    # ISO-BMFF: the brand box starts at offset 4 for both HEIC and HEIF.
    ".heic": ((4, b"ftyp"),),
    ".heif": ((4, b"ftyp"),),
}


def _ext_matches_content(ext: str, data: bytes) -> bool:
    """True when the bytes look like what the extension claims, or when the type
    has no signature to check. Stops a file from being handed to the image
    pipeline (or to Claude's Read tool) as something it isn't."""
    sigs = _UPLOAD_MAGIC.get(ext)
    if not sigs:
        return True
    return any(data[off:off + len(magic)] == magic for off, magic in sigs)


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
async def upload(request: Request, response: Response, file: UploadFile = File(...)):
    """Accept one file (image or doc), store it off-vault, return its server path.
    The path is then sent as an attachment on a later /ask or /ask_async turn, where
    Claude's Read tool views it. iPhone HEIC photos are converted to JPEG first."""
    # Enforce the cap DURING the read, not after it. A bare file.read()
    # materialises the entire body as one bytes object before the size check can
    # reject it, so the 413 arrived only once the cost had already been paid.
    # Reading in chunks lets an oversized upload be refused after one megabyte.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > config.UPLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail="File too large (25 MB max)")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    raw_name = file.filename or "upload"
    ext = Path(raw_name).suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"Unsupported type: {ext or 'unknown'}")
    # The extension alone decided what this file was; nothing looked at the bytes.
    if not _ext_matches_content(ext, data):
        raise HTTPException(
            status_code=415,
            detail=f"File contents don't match the {ext} extension — rename it to "
                   "its real type and try again.",
        )
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
        # The exception text carries the absolute destination path (and on Windows
        # the account name inside it). Logged above, not returned.
        raise HTTPException(status_code=500, detail="Could not save the upload")
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
async def ask(request: Request, response: Response, body: AskRequest):
    message = body.message.strip()
    if not message and not body.attachments:
        raise HTTPException(status_code=400, detail="Empty message")
    return await server.run_claude(
        message, body.session_id, mode=body.mode or "voice",
        attachments=body.attachments, project=body.project,
        mode_switch=bool(body.mode_switch),
    )


@router.post("/ask_async", dependencies=[Depends(require_token)])
@limiter.limit("30/minute")
async def ask_async(request: Request, response: Response, body: AskRequest):
    """Kick off a Claude turn in the background; return a job_id immediately. The
    job is persisted before the task starts, so it survives a restart."""
    message = body.message.strip()
    if not message and not body.attachments:
        raise HTTPException(status_code=400, detail="Empty message")
    # A cooperative restart is draining the in-flight turn(s) and about to exit —
    # refuse new turns (503) so the drain can reach zero instead of chasing fresh
    # work. The PWA surfaces this as a brief "Adam is restarting" note, not a
    # connection error; polling an already-running turn is unaffected.
    if server.is_draining():
        raise HTTPException(status_code=503,
                            detail="Adam is restarting — try again in a moment.")
    job_store.sweep(config.JOB_HISTORY_TTL_SECONDS)
    mode = server._normalize_mode(body.mode or "voice")
    if (mode == "code" and config.AGENT_ALLOW_CODE_MODE
            and not plus_gate.operator_available(True)):
        # Operator is Adam Plus. Refuse before a job exists so the app can show the
        # Plus prompt and drop the chat back to Normal, instead of a failed turn.
        raise HTTPException(status_code=402, detail=plus_gate.lock_detail("operator"))
    if mode == "code" and operator_session.is_busy(body.session_id):
        # The chat's live Operator session is mid-turn (e.g. started on another
        # device). Refuse up front — the app shows this calmly and keeps the chat's
        # context, instead of a failed job that would make it start over.
        raise HTTPException(status_code=409, detail=(
            "Operator is still working on the last message — send this as a "
            "follow-up while it runs, or stop it first."))
    job_id = uuid.uuid4().hex
    # Persist only a short, truncated summary of the user's input (never the full
    # prompt) so job history is readable without storing private text wholesale.
    summary = (message or "").strip()[: config.JOB_INPUT_SUMMARY_MAX] or None
    job_store.create_job(
        job_id, mode=mode, session_id=body.session_id,
        input_summary=summary, pid=os.getpid(),
    )
    if mode == "code" and body.chat:
        operator_session.JOB_CHAT[job_id] = str(body.chat)[:200]
    elif mode != "code":
        server.RUNNING_TURNS[job_id] = {
            "chat": str(body.chat)[:200] if body.chat else None,
            "session_id": body.session_id, "mode": mode,
        }
    server.keep_task(asyncio.create_task(
        server._run_job(job_id, message, body.session_id, mode, body.attachments,
                        project=body.project, chat=body.chat,
                        mode_switch=bool(body.mode_switch))
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
        # Operator turns: a question/plan waiting on the user, and how many
        # full-output events exist (the app fetches new ones from /events).
        ask = operator_session.pending_ask(job_id)
        if ask:
            out["ask"] = ask
        n = operator_session.event_count(job_id)
        if n:
            out["events_n"] = n
    else:
        evs = operator_session.events_since(job_id, 0)
        if evs:
            out["events_n"] = evs[-1]["n"]
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
    if operator_session.session_for_job(job_id) is not None:
        # Operator: interrupt the turn, keep the live session (and its context).
        await operator_session.interrupt(job_id)
    else:
        await server._kill_proc_tree(proc)
    permissions.record_audit_event({
        "action_type": "job_stopped", "target": job_id,
        "allowed": True, "requires_approval": False, "approved": True,
        "risk": "low", "reason": "user stopped a running turn",
    })
    return {"ok": True, "job_id": job_id}


# --- Operator mode: questions, steering, full output, slash commands ---------

@router.post("/jobs/{job_id}/answer", dependencies=[Depends(require_token)])
async def answer_job(job_id: str, body: OperatorAnswer):
    """Answer the question (or plan) an Operator turn is waiting on. 409 when that
    question is no longer open (answered elsewhere, timed out, or the turn ended)."""
    if body.answers is None and body.approve is None:
        raise HTTPException(status_code=400, detail="Nothing to answer with")
    payload = {"answers": {str(k)[:2000]: str(v)[:4000] for k, v in (body.answers or {}).items()},
               "approve": body.approve, "feedback": (body.feedback or "")[:4000]}
    if not operator_session.answer(job_id, body.ask_id, payload):
        raise HTTPException(status_code=409, detail="That question is no longer open")
    return {"ok": True}


@router.post("/jobs/{job_id}/steer", dependencies=[Depends(require_token)])
@limiter.limit("30/minute")
async def steer_job(request: Request, response: Response, job_id: str, body: OperatorSteer):
    """Send a message into an Operator turn while it works. Claude reads it at its
    next step. 409 when the turn is no longer running (send it as a new message)."""
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    if not await operator_session.steer(job_id, text):
        raise HTTPException(status_code=409, detail="That turn is no longer running")
    return {"ok": True}


@router.get("/jobs/{job_id}/events", dependencies=[Depends(require_token)])
async def job_events(job_id: str, since: int = 0):
    """The Operator turn's full transcript: text, every tool call with its input,
    tool output (capped per result), questions, answers, steers. `since` = the last
    event number the app already has."""
    evs = operator_session.events_since(job_id, max(0, since))
    if evs is None:
        raise HTTPException(status_code=404, detail="No transcript for that job")
    return {"events": evs}


@router.post("/operator/consent", dependencies=[Depends(require_token)])
async def operator_consent():
    """Record the user's one-time consent to Operator mode (full Claude Code power).
    Stored server-side so the phone and the PC ask only once between them."""
    prefs = server._load_ui_prefs()
    if not prefs.get("operator_consent"):
        prefs["operator_consent"] = True
        server._save_ui_prefs(prefs)
        permissions.record_audit_event({
            "action_type": "operator_mode_consent", "allowed": True,
            "requires_approval": False, "approved": True, "risk": "high",
            "reason": "user accepted Operator mode (full Claude Code power)",
        })
    return {"ok": True}


@router.get("/operator/running", dependencies=[Depends(require_token)])
async def operator_running():
    """Operator turns still working right now — the app re-attaches to them after a
    reload (iOS suspends and reloads backgrounded web apps), so a question asked
    while the phone was away is still answerable when the user comes back."""
    return {"running": operator_session.running_jobs()}


@router.get("/turns/running", dependencies=[Depends(require_token)])
async def turns_running():
    """Every turn still working right now, any mode — the app re-attaches after iOS
    kills or reloads it, so a message sent just before closing the app still gets
    its reply (and a reopened chat shows it's busy instead of inviting a duplicate)."""
    out = [{"job_id": jid, "operator": False, **info}
           for jid, info in list(server.RUNNING_TURNS.items())]
    out += [dict(j, operator=True) for j in operator_session.running_jobs()]
    return {"running": out}


@router.get("/operator/commands", dependencies=[Depends(require_token)])
async def operator_commands():
    """Slash commands the user's Claude Code offers (built-ins, custom commands,
    skills) — learned from the last Operator session's handshake."""
    return {"commands": operator_session.commands()}


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


# --- Project folders ---------------------------------------------------------
# ChatGPT-style folders for chats. Synced exactly like sessions (LWW on the client
# `updated`, delivery by server `seq`, tombstone deletes); a chat's `project` field
# on its SessionRecord is what files it. Deleting a project never deletes chats —
# the client un-files them back to the loose list first.

@router.get("/projects", dependencies=[Depends(require_token)])
async def projects_pull(since: int = 0):
    if server.session_store is None or not config.SESSION_SYNC_ENABLED:
        return {"enabled": False, "projects": []}
    return {"enabled": True, "projects": server.session_store.projects_changed_since(since)}


@router.post("/projects", dependencies=[Depends(require_token)])
async def projects_push(body: ProjectSyncPush):
    if server.session_store is None or not config.SESSION_SYNC_ENABLED:
        return {"enabled": False, "applied": 0}
    res = server.session_store.upsert_projects([r.model_dump() for r in body.projects])
    return {"enabled": True, "applied": res["applied"]}
