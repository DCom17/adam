"""System routes: liveness, health, static PWA/console/setup pages, phone
connection info, and the self-updater endpoints."""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
import sys
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse

import config
import integration_registry
import job_store
import merge
import permissions
import updater
from security import require_token, token_matches

import server

router = APIRouter()


@router.get("/ping")
async def ping():
    """Liveness check — no auth, no detail."""
    return {"status": "ok"}


@router.get("/health")
async def health(authorization: str = Header(default="")):
    """Health + config sanity for setup verification. Anonymous callers get only
    liveness + version (the PWA footer needs no more); the full config summary —
    filesystem paths, the public URL, enabled integrations — requires the bearer
    token. Any web page can hit http://localhost:<port>/health cross-origin, so
    the anonymous body must not be worth reading. Never any secrets either way."""
    if token_matches(authorization):
        return {"status": "ok", **config.safe_summary()}
    return {"status": "ok", "app": config.APP_NAME, "version": config.APP_VERSION}


@router.get("/phone-setup", dependencies=[Depends(require_token)])
async def phone_setup():
    """Tailscale setup state + the exact (copy-paste) commands to connect a phone over
    HTTPS. Runs the READ-ONLY connect-phone helper (it never changes Tailscale or any
    config — it only inspects and prints commands), so the in-app wizard can guide the
    user step by step. No secrets in the output."""
    script = str(config.ROOT / "scripts" / "connect-phone.py")
    try:
        p = await asyncio.to_thread(
            subprocess.run, [sys.executable, script, "--json"],
            capture_output=True, text=True, timeout=20)
        if p.stdout and p.stdout.strip():
            return json.loads(p.stdout)
        return {"tailscale_found": False, "error": (p.stderr or "no output").strip()[:300]}
    except Exception as e:  # noqa: BLE001
        return {"tailscale_found": False, "error": str(e)}


@router.get("/connect-info", dependencies=[Depends(require_token)])
async def connect_info():
    """Phone-reachable URLs for the Connect-phone QR. The browser is on localhost (no
    use to a phone), so the server reports the Tailscale HTTPS URL / LAN IP instead.
    `best` prefers a secure (HTTPS) URL — what an iPhone needs for voice."""
    import phone_link
    cands = await asyncio.to_thread(phone_link.phone_urls, config.PORT, config.PUBLIC_BASE_URL)
    best = next((c for c in cands if c.get("secure")), cands[0] if cands else None)
    return {"best": best, "candidates": cands, "app_port": config.PORT}


def _redact(text: str) -> str:
    """Scrub every configured secret (and the owner's phone number) out of a
    diagnostics line. Belt-and-suspenders: none of these should ever be logged
    in the first place, but a support bundle gets pasted into chats/issues, so
    it must be safe even if a future log line slips."""
    secrets = [
        config.ADAM_TOKEN, config.CALENDAR_TOKEN, config.HUNTER_TOKEN,
        config.GMAIL_TOKEN, config.LINKEDIN_CLIENT_SECRET,
        config.LINKEDIN_ACCESS_TOKEN, config.TWILIO_AUTH_TOKEN,
        config.OWNER_PHONE,
        # Claude's stderr can echo a bad key back into a job error message.
        getattr(config, "ANTHROPIC_API_KEY", ""),
    ]
    for s in secrets:
        if s and s in text:
            text = text.replace(s, "***")
    return text


@router.get("/diagnostics", dependencies=[Depends(require_token)])
async def diagnostics():
    """A copy-pasteable, secret-free support bundle: versions, platform, uptime,
    the non-secret config summary, add-on status, recent job outcomes (status +
    error only — never the user's prompt text), and the recent log tail. Every
    line is redacted of configured secrets before it leaves the machine. This is
    what 'Copy diagnostics' in the Operator Console fetches."""
    import fastapi

    jobs = []
    try:
        for j in job_store.list_jobs(limit=10):
            jobs.append({
                "job_id": j.get("job_id"), "status": j.get("status"),
                "mode": j.get("mode"), "created_at": j.get("created_at"),
                "completed_at": j.get("completed_at"),
                "error": _redact(str(j.get("error"))) if j.get("error") else None,
            })
    except Exception:
        jobs = [{"error": "job store unavailable"}]

    try:
        addons = [{"name": a.get("name"), "status": a.get("status")}
                  for a in integration_registry.snapshot()]
    except Exception:
        addons = []

    # The in-memory ring dies with the process — after the crash that most needs
    # diagnosing, it's empty. Tail the persistent log FILE too (same redaction),
    # so "Copy diagnostics" still carries the pre-restart evidence.
    file_tail: list[str] = []
    try:
        from collections import deque
        with open(config.LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            file_tail = [_redact(ln.rstrip("\n")) for ln in deque(fh, maxlen=120)]
    except Exception:
        file_tail = ["(log file unavailable)"]

    return {
        "app": {
            "name": config.APP_NAME,
            "version": config.APP_VERSION,
            "python": sys.version.split()[0],
            "fastapi": fastapi.__version__,
            "platform": platform.platform(),
            "uptime_seconds": int(time.time() - server.START_TIME),
        },
        "config": config.safe_summary(),
        "addons": addons,
        "recent_jobs": jobs,
        "log_tail": [_redact(line) for line in list(server.LOG_RING)[-200:]],
        "log_file_tail": file_tail,
    }


@router.get("/update/check", dependencies=[Depends(require_token)])
async def update_check():
    """Is a newer release available? Reads the public GitHub Releases 'latest'
    endpoint (no auth). Never errors out — a network/rate problem returns
    available:false with a reason, so the banner just stays hidden."""
    if not config.UPDATE_CHECK_ENABLED:
        return {"enabled": False, "available": False, "current": config.APP_VERSION}
    info = await asyncio.to_thread(updater.check_for_update)
    return {"enabled": True, **info}


@router.post("/update/apply", dependencies=[Depends(require_token)])
async def update_apply():
    """Download + apply the latest release with the smart 3-way updater. The URL is
    resolved server-side (the client can't point this at an arbitrary zip). On
    success the app must be restarted to run the new code."""
    if not config.UPDATE_CHECK_ENABLED:
        raise HTTPException(status_code=403, detail="Updates are turned off.")
    info = await asyncio.to_thread(updater.check_for_update, None, None, use_cache=False)
    if not info.get("available") or not info.get("url"):
        return {"applied": False, "reason": "no update available",
                "current": config.APP_VERSION, "latest": info.get("latest")}
    res = await asyncio.to_thread(updater.download_and_apply, info["url"])
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=res.get("error") or "update failed")
    permissions.record_audit_event({
        "action_type": "update_applied", "to_version": res.get("version"),
        "updated": res.get("updated"), "conflicts": len(res.get("conflicts") or []),
    })
    return {"applied": True, "restart_required": True, **res}


@router.get("/update-conflicts", dependencies=[Depends(require_token)])
async def get_update_conflicts():
    """Files a recent update held back because the user had also customized them in
    the same spot (the auto-merge couldn't reconcile them). Their version is intact;
    the agent reconciles these when asked to 'merge the update conflicts'. Lightweight
    summary only — the full versions stay server-side for the agent."""
    return {
        "conflicts": [
            {"rel": c.get("rel"), "recorded_at": c.get("recorded_at"),
             "status": c.get("status", "pending")}
            for c in merge.list_conflicts()
        ]
    }


@router.get("/sw.js")
async def service_worker():
    """Serve the service worker at root scope so it controls the whole app."""
    path = server.FRONTEND.parent / "sw.js"
    if path.exists():
        return FileResponse(
            path,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/"},
        )
    raise HTTPException(status_code=404, detail="sw.js not found")


@router.get("/manifest.json")
async def manifest():
    path = server.FRONTEND.parent / "manifest.json"
    if path.exists():
        return FileResponse(path, media_type="application/manifest+json",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="manifest.json not found")


@router.get("/icon.png")
async def icon():
    path = server.FRONTEND.parent / "icon.png"
    if path.exists():
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="icon.png not found")


@router.get("/favicon.ico")
async def favicon():
    # Browsers request this on every page load; without a route each load
    # printed a 404 line in the always-visible server window.
    path = server.FRONTEND.parent / "icon.ico"
    if path.exists():
        return FileResponse(path, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="icon.ico not found")


def _static_page(name: str) -> FileResponse:
    """Serve one of the static web/ pages no-store (so an edit shows without a
    cache clear). Every one of these pages carries NO secret — the token is
    pasted/stored client-side and every data fetch the page makes goes through
    the token-gated API routes."""
    path = server.FRONTEND.parent / name
    if path.exists():
        return FileResponse(path, headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail=f"{name} not found")


@router.get("/console")
async def console():
    """Serve the read-only desktop Operator Console (web/console.html)."""
    return _static_page("console.html")


@router.get("/settings")
async def settings_page():
    """Serve the Settings → Add-ons cards view (web/settings.html)."""
    return _static_page("settings.html")


@router.get("/setup-calendar")
async def setup_calendar_page():
    """Serve the standalone Google Calendar setup wizard."""
    return _static_page("setup-calendar.html")


@router.get("/setup-voicemail")
async def setup_voicemail_page():
    """Serve the standalone Voicemail setup wizard."""
    return _static_page("setup-voicemail.html")


@router.get("/setup-linkedin")
async def setup_linkedin_page():
    """Serve the standalone LinkedIn setup wizard."""
    return _static_page("setup-linkedin.html")


@router.get("/setup-email")
async def setup_email_page():
    """Serve the standalone Gmail setup wizard."""
    return _static_page("setup-email.html")


@router.get("/setup-sms")
async def setup_sms_page():
    """Serve the standalone SMS setup wizard."""
    return _static_page("setup-sms.html")


@router.get("/setup-hunter")
async def setup_hunter_page():
    """Serve the standalone Hunter setup wizard."""
    return _static_page("setup-hunter.html")


@router.get("/hunter-dashboard")
async def hunter_dashboard_page():
    """Serve the in-app Hunter dashboard (web/hunter-dashboard.html) — the mobile
    view, rendered locally from GET /integrations/hunter/board instead of Google.
    Opened in an overlay iframe from the main app's view switcher."""
    return _static_page("hunter-dashboard.html")


@router.get("/")
async def index():
    if server.FRONTEND.exists():
        # Never let the phone cache the HTML — it's a single tiny file and Safari
        # caches it aggressively, so edits wouldn't show without clearing data.
        return FileResponse(server.FRONTEND, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {"status": "Adam running. Frontend not found in web/."}
    )
