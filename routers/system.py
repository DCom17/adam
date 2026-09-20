"""System routes: liveness, health, static PWA/console/setup pages, phone
connection info, and the self-updater endpoints."""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

import config
import integration_registry
import job_store
import licensing
import merge
import permissions
import updater
from security import redact_secrets, require_token, token_matches

import server

router = APIRouter()


def _require_entitlement() -> None:
    """Gate premium (phone/remote) features. No-op on an unconfigured build or during the
    free trial or with a valid license; otherwise 402 with a buy link so the client can
    render a clean 'licensed feature' prompt. Enforcement policy: trial → feature-limit."""
    if licensing.is_entitled():
        return
    raise HTTPException(status_code=402, detail={
        "locked": True,
        "feature": "phone",
        "message": "Taking Adam to your phone is a licensed feature after your free trial.",
        "buy_url": licensing.BUY_URL,
    })


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
    _require_entitlement()
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
    _require_entitlement()
    import phone_link
    cands = await asyncio.to_thread(phone_link.phone_urls, config.PORT, config.PUBLIC_BASE_URL)
    best = next((c for c in cands if c.get("secure")), cands[0] if cands else None)
    return {"best": best, "candidates": cands, "app_port": config.PORT}


def _redact(text: str) -> str:
    """Diagnostics-bundle scrubber. Thin alias kept because this name is used
    throughout this module; the implementation moved to security.py so server.py
    can scrub the same way without importing a router (which would be circular)."""
    return redact_secrets(text)


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
    # Finish the update the way it should finish: bring the new version up on its
    # own. Spawn the detached relauncher, then drain + exit this (old-code) process.
    # The launcher window closes on our clean exit; a fresh one opens running the
    # just-installed files. Returns immediately; the drain+exit run in background.
    server.begin_relaunch_and_exit()
    return {"applied": True, "restart_required": True, "restarting": True, **res}


@router.post("/drain", dependencies=[Depends(require_token)])
async def drain(max_wait: int | None = None):
    """Cooperative restart prep: stop taking new turns, wait for the in-flight
    one(s) to finish (bounded by max_wait, default config.DRAIN_MAX_WAIT_SECONDS),
    then exit — so restart-adam.ps1 (or the updater) can relaunch without killing a
    long code turn mid-flight. Returns immediately; the wait + exit run in the
    background. A hard window-close/reboot skips this entirely and is handled by
    the recoverable 'restarted mid-task' message instead."""
    running = server.begin_drain_and_exit(max_wait)
    permissions.record_audit_event({
        "action_type": "drain_requested", "target": None, "risk": "low",
        "reason": "cooperative restart", "running_jobs": running,
    })
    server.log.info("drain requested: %d in-flight turn(s), cap %ss",
                    running, max_wait if max_wait is not None else config.DRAIN_MAX_WAIT_SECONDS)
    return {"draining": True, "running_jobs": running,
            "max_wait_s": max_wait if max_wait is not None else config.DRAIN_MAX_WAIT_SECONDS}


# --- Data export ------------------------------------------------------------

# Directory names under data/ that never belong in a backup: regenerable, huge,
# or noise. backups/ especially — including it would nest every previous
# pre-write copy inside every new export.
#
# baseline/ is the updater's 3-way-merge reference (the last-shipped file set).
# It is machinery, not user data, and it is rebuilt from the installed version —
# but it is a full copy of the install tree, so it dominates the archive: a first
# export measured 603 MB, of which 655 MB of source was baseline.tmp. Restoring
# it onto another machine would also be actively wrong, since it must describe
# the version installed THERE.
_EXPORT_SKIP_DIRS = {
    "logs", "backups", "uploads", "agent_workspace", "baseline", "baseline.tmp",
}

# Never exported, even by name match inside an included directory. The VAPID
# private key is regenerated on demand, and a .env in the data tree would put
# every API key into a file the user is about to copy to cloud storage.
#
# The SQLite sidecars matter for CORRECTNESS, not size. Each .db goes into the
# archive via sqlite3's backup API, which produces a standalone file with the
# WAL already checkpointed into it. Shipping the source database's -wal/-shm
# alongside that snapshot means a restore drops a STALE log next to a newer
# database — which SQLite may replay or roll back, quietly corrupting the very
# data this feature exists to protect.
_EXPORT_SKIP_GLOBS = (
    "*.pem", "*.log", ".env", ".env.*", "*.tmp",
    "*.db-wal", "*.db-shm", "*.db-journal",
)


def _sqlite_snapshot(src: Path, dest: Path) -> bool:
    """Copy a SQLite DB with the backup API rather than the filesystem.

    A plain file copy of a database the server is still writing can capture a
    torn page or miss a live WAL, producing a backup that only fails at restore
    time. backup() takes a transactionally consistent snapshot of a live DB."""
    import sqlite3
    try:
        src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            dst_con = sqlite3.connect(str(dest))
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
        finally:
            src_con.close()
        return True
    except Exception as e:  # noqa: BLE001 — a locked/corrupt DB must not kill the export
        server.log.warning("export: sqlite snapshot failed for %s: %s", src.name, e)
        return False


def _build_export(zip_path: Path) -> dict:
    """Write the backup ZIP. Returns a small manifest of what went in."""
    import fnmatch
    import zipfile

    included: list[str] = []
    skipped_dbs: list[str] = []
    staging = Path(tempfile.mkdtemp(prefix="adam_export_db_"))
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            data_dir = config.DATA_DIR
            if data_dir.is_dir():
                for path in sorted(data_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(data_dir)
                    if any(part in _EXPORT_SKIP_DIRS for part in rel.parts[:-1]):
                        continue
                    if any(fnmatch.fnmatch(path.name, g) for g in _EXPORT_SKIP_GLOBS):
                        continue
                    arc = str(Path("data") / rel)
                    if path.suffix == ".db":
                        snap = staging / f"{rel.as_posix().replace('/', '_')}"
                        if _sqlite_snapshot(path, snap):
                            zf.write(snap, arc)
                            included.append(arc)
                        else:
                            skipped_dbs.append(arc)
                        continue
                    try:
                        zf.write(path, arc)
                        included.append(arc)
                    except OSError:
                        continue    # a file that vanished or is locked mid-walk

            # settings.json is the user's own configuration and carries no
            # credentials by design (secrets live in .env) — a restore without
            # it would lose every preference.
            settings = config.CONFIG_ROOT / "settings.json"
            if settings.is_file():
                zf.write(settings, "settings.json")
                included.append("settings.json")

            zf.writestr("RESTORE.txt", _EXPORT_README)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {"files": len(included), "skipped_databases": skipped_dbs}


_EXPORT_README = """Adam backup
===========

This archive holds the data Adam cannot regenerate: your trackers, sessions,
job history, saved state and settings.

TO RESTORE
----------
1. Install Adam on the new machine and start it once, so it creates its folders.
2. Stop Adam.
3. Copy the `data` folder from this archive over the one in your Adam folder,
   and `settings.json` next to it, replacing what is there.
4. Start Adam.

WHAT IS DELIBERATELY NOT IN HERE
--------------------------------
* `.env` — your API keys and access token. Left out so this file stays safe to
  put on a USB stick or in cloud storage. Re-enter them from Setup after a
  restore; they take a minute and are the only thing you have to redo.
* Logs, old pre-write backups, uploads and the agent scratch folder. All
  regenerable, and they would dwarf everything worth keeping.
* Your vault/brain folder. That lives wherever you pointed Adam at it (often a
  synced Drive folder) and is backed up by whatever already backs that up.
"""


@router.get("/export", dependencies=[Depends(require_token)])
async def export_data():
    """Download everything that would be painful to lose, as one ZIP.

    Adam kept per-file pre-write copies in data/backups, which is a good undo
    but not a backup: nothing covered 'this machine is gone'. The trackers are
    the sharp edge — months of finance and health entries that exist in exactly
    one SQLite file. Databases are snapshotted through sqlite3's backup API so
    the archive is consistent even while turns are running."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tmp_dir = Path(tempfile.mkdtemp(prefix="adam_export_"))
    zip_path = tmp_dir / f"adam-backup-{stamp}.zip"
    try:
        manifest = await asyncio.to_thread(_build_export, zip_path)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(tmp_dir, ignore_errors=True)
        server.log.exception("export failed")
        raise HTTPException(status_code=500, detail="Could not build the backup") from e

    permissions.record_audit_event({
        "action_type": "data_exported", "target": zip_path.name, "risk": "low",
        "reason": "owner requested a backup", "files": manifest["files"],
        "size_bytes": zip_path.stat().st_size,
    })
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        # The archive lives in a temp dir only until the response is flushed.
        background=BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True),
    )


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


@router.get("/logo.png")
async def logo():
    # The waveform-A brand mark (transparent cutout) used in page headers —
    # same art as the landing site's nav logo.
    path = server.FRONTEND.parent / "logo.png"
    if path.exists():
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="logo.png not found")


@router.get("/icon-maskable.png")
async def icon_maskable():
    # Android launcher icon (manifest purpose "maskable"): same art padded
    # into the safe zone so launcher shapes don't crop the letter.
    path = server.FRONTEND.parent / "icon-maskable.png"
    if path.exists():
        return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="icon-maskable.png not found")


@router.get("/adam-ui.css")
async def adam_ui_css():
    # Shared design language for the operator pages (Add-ons, Operator Console,
    # every setup-* wizard). Carries no secret — it's stylesheet text, so it's
    # served un-gated like the icons. no-store because an edit during setup
    # should show on reload without a cache clear, same rule as _static_page.
    path = server.FRONTEND.parent / "adam-ui.css"
    if path.exists():
        return FileResponse(path, media_type="text/css",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="adam-ui.css not found")


@router.get("/qr-encoder.js")
async def qr_encoder_js():
    # Vendored QR encoder (qrcode-generator, MIT, Kazuhiko Arase), shared by the
    # Connect-phone page and the Operator Console. It is ~2,300 lines; keeping one
    # copy behind a route beats inlining it into two pages that then drift.
    # Carries no secret — library source — so it is served un-gated like the CSS.
    path = server.FRONTEND.parent / "qr-encoder.js"
    if path.exists():
        return FileResponse(path, media_type="application/javascript",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="qr-encoder.js not found")


@router.get("/favicon.ico")
async def favicon():
    # Browsers request this on every page load; without a route each load
    # printed a 404 line in the always-visible server window.
    path = server.FRONTEND.parent / "icon.ico"
    if path.exists():
        return FileResponse(path, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="icon.ico not found")


# Bundled level-up / rank-up celebration videos (web/celebrate/*.mp4). Static and
# carry no secret, so they're served un-gated like the icons. Only allow-listed
# filenames resolve — no user input ever reaches the filesystem path.
_CELEBRATE_NAMES = ("rank-d", "rank-c", "rank-b", "rank-a", "rank-s", "rank-master", "milestone")
# Both codecs ship: H.264 mp4 (universal — Safari/Edge/mobile) and VP9 webm
# (Chromium builds without proprietary H.264). The page picks per canPlayType.
_CELEBRATE_FILES = {n + ext for n in _CELEBRATE_NAMES for ext in (".mp4", ".webm")}


@router.get("/celebrate/{filename}")
async def celebrate_asset(filename: str):
    """Serve a bundled celebration clip (mp4/webm) by its allow-listed name."""
    if filename not in _CELEBRATE_FILES:
        raise HTTPException(status_code=404, detail="not found")
    path = server.FRONTEND.parent / "celebrate" / filename
    if path.exists():
        mt = "video/webm" if filename.endswith(".webm") else "video/mp4"
        return FileResponse(path, media_type=mt,
                            headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail="not found")


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


@router.get("/setup-garmin")
async def setup_garmin_page():
    """Serve the standalone Garmin (unofficial health sync) setup wizard."""
    return _static_page("setup-garmin.html")


@router.get("/setup-phone")
async def setup_phone_page():
    """Serve the standalone Connect-phone page.

    This used to be a section buried in the Operator Console, which is a strange
    place to send someone whose only goal is getting Adam onto their phone. It is
    now an add-on page like every other one, reached from Settings -> Add-ons.
    The console keeps a signpost at #connectPhoneSec pointing here."""
    return _static_page("setup-phone.html")


@router.get("/hunter-dashboard")
async def hunter_dashboard_page():
    """Serve the in-app Hunter dashboard (web/hunter-dashboard.html) — the mobile
    view, rendered locally from GET /integrations/hunter/board instead of Google.
    Opened in an overlay iframe from the main app's view switcher."""
    return _static_page("hunter-dashboard.html")


@router.get("/finance")
async def finance_page():
    """Serve the in-app Finance Tracker (web/finance.html) — the private, local
    money dashboard + import/review view. Numbers are read from the token-gated
    /finance/* API (finance_metrics computes them). Opened in an overlay iframe
    from the main app's view switcher."""
    return _static_page("finance.html")


@router.get("/health-tracker")
async def health_tracker_page():
    """Serve the in-app Health Tracker (web/health.html) — the private, on-device
    weight/meals/macros dashboard. Numbers are read from the token-gated
    /health/* API (health_metrics computes them). Opened in an overlay iframe
    from the main app's view switcher. (The bare /health path is the server's
    liveness endpoint, so the page lives at /health-tracker.)"""
    return _static_page("health.html")


@router.get("/checklists-view")
async def checklists_page():
    """Serve the in-app Checklists view (web/checklists.html) — user-written and
    Adam-built step lists, plus the archive. Data comes from the token-gated
    /checklists API. Opened in an overlay iframe from the main app's view
    switcher. (The bare /checklists path is the JSON index, so the page lives at
    /checklists-view — same split as /health vs /health-tracker.)"""
    return _static_page("checklists.html")


@router.get("/license-agreement")
async def license_agreement_page():
    """Serve the first-run EULA clickwrap screen (web/license-agreement.html). The
    frontend gate sends new users here until they accept the current EULA version."""
    return _static_page("license-agreement.html")


@router.get("/legal")
async def legal_page():
    """Serve the legal-document viewer (web/legal.html); ?doc=eula|terms|privacy|refund."""
    return _static_page("legal.html")


# The bundled legal documents, served as Markdown for the in-app viewer and the
# clickwrap screen. Public and un-gated — the same texts are published on the site,
# and the clickwrap must be readable before the user is "in." Only these allow-listed
# names resolve, so no user input ever reaches the filesystem path.
_LEGAL_DOCS = ("eula", "terms", "privacy", "refund")


@router.get("/legal/{name}.md")
async def legal_markdown(name: str):
    """Serve a bundled legal document (Markdown) by allow-listed name."""
    if name not in _LEGAL_DOCS:
        raise HTTPException(status_code=404, detail="not found")
    path = server.FRONTEND.parent / "legal" / (name + ".md")
    if path.exists():
        return FileResponse(path, media_type="text/markdown; charset=utf-8",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="not found")


@router.get("/")
async def index():
    if server.FRONTEND.exists():
        # Never let the phone cache the HTML — it's a single tiny file and Safari
        # caches it aggressively, so edits wouldn't show without clearing data.
        return FileResponse(server.FRONTEND, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {"status": "Adam running. Frontend not found in web/."}
    )
