"""Notifications panel routes — the proactive reminders' prefs, a test send,
pause/resume, and the /notifications page itself.

The reminder engine lives in reminders.py; server.py starts its loop and owns
the Web Push sender. All data routes are token-gated.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import reminders
from models import ReminderPauseBody, ReminderPrefsBody, ReminderTestBody
from rate_limit import limiter
from security import require_token

import server

router = APIRouter()


@router.get("/notifications")
async def notifications_page():
    """Serve the Notifications panel (web/notifications.html). Carries no secret."""
    from routers.system import _static_page
    return _static_page("notifications.html")


@router.get("/reminders", dependencies=[Depends(require_token)])
async def reminders_overview():
    """Prefs + each reminder's status/preview + recent sends + push health."""
    out = await asyncio.to_thread(reminders.overview)
    out["push"] = server.push_status()
    return out


@router.post("/reminders/prefs", dependencies=[Depends(require_token)])
async def reminders_set_prefs(body: ReminderPrefsBody):
    patch = body.model_dump(exclude_none=True)
    if "kinds" in patch:
        patch["kinds"] = {k: v for k, v in patch["kinds"].items() if k in reminders.KIND_IDS}
    prefs = await asyncio.to_thread(reminders.save_prefs, patch)
    return {"prefs": prefs}


@router.post("/reminders/test", dependencies=[Depends(require_token)])
@limiter.limit("12/minute")
async def reminders_test(request: Request, response: Response, body: ReminderTestBody):
    """Send one reminder now, regardless of its conditions, so the user can see
    exactly how it lands on their phone."""
    if body.kind not in reminders.KIND_IDS:
        raise HTTPException(status_code=400, detail="Unknown reminder")
    st = server.push_status()
    if st.get("state") == "disabled":
        raise HTTPException(status_code=409, detail="Push notifications are unavailable on this server.")
    if not st.get("devices"):
        raise HTTPException(status_code=409, detail="No device is signed up for notifications yet.")
    msg = await asyncio.to_thread(reminders.send_test, body.kind, server._send_reminder_push)
    return {"sent": msg}


@router.post("/reminders/pause", dependencies=[Depends(require_token)])
async def reminders_pause(body: ReminderPauseBody):
    if body.mode == "resume":
        prefs = await asyncio.to_thread(reminders.save_prefs, {"paused_until": 0})
    elif body.mode == "until_tomorrow":
        p = reminders.load_prefs()
        h, m = reminders._hm(p.get("quiet_end", "07:00"))
        until = (datetime.now() + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
        prefs = await asyncio.to_thread(reminders.save_prefs, {"paused_until": until.timestamp()})
    else:
        raise HTTPException(status_code=400, detail="mode must be until_tomorrow or resume")
    return {"prefs": prefs}
