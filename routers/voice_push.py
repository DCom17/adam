"""Voice + push routes: local TTS proxy (/speak), voice-install status, Web
Push subscription/replay, and the PWA failure beacon (/clientlog)."""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import config
from models import ClientLog, PushSubscribe, SpeakRequest
from rate_limit import limiter
from security import require_token

import server

router = APIRouter()


# Characters Kokoro would otherwise read aloud literally ("asterisk", "number
# sign") or mangle prosody on. Stripped from every spoken line as a hard backstop,
# regardless of what the frontend sent — so even a stale cached client stays clean.
_SPEAK_STRIP = re.compile(r"[*_`#~>|^=\[\]{}<>✓✔☑☐✗✘•▪◦‣·●○]")
_SPEAK_DASHPAUSE = re.compile(r"\s*[—–―]\s*")  # em/en/bar dash -> spoken pause


def _sanitize_for_speech(text: str) -> str:
    """Strip symbols and hyphens/dashes that the TTS would verbalize. JARVIS gets
    its pauses from commas and periods, never from dashes or punctuation art."""
    t = _SPEAK_STRIP.sub("", text)
    t = _SPEAK_DASHPAUSE.sub(", ", t)   # long dashes become a real spoken pause
    t = t.replace("-", " ")             # no hyphen survives to be read aloud
    t = re.sub(r",\s*,", ", ", t)       # collapse any doubled-up commas we created
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def _fetch_tts(payload: bytes) -> bytes:
    """Blocking POST to the local Kokoro service; run off the event loop."""
    req = urllib.request.Request(
        config.TTS_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=config.TTS_TIMEOUT_SECONDS) as resp:
        return resp.read()


@router.post("/speak", dependencies=[Depends(require_token)])
@limiter.limit("120/minute")  # a turn now streams up to ~3 sentence requests
async def speak(request: Request, body: SpeakRequest):
    text = _sanitize_for_speech(body.text).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    payload = json.dumps({"text": text, "voice": body.voice}).encode("utf-8")
    try:
        wav = await asyncio.to_thread(_fetch_tts, payload)
    except Exception as e:
        # TTS down or slow — tell the frontend so it can fall back to browser TTS.
        raise HTTPException(status_code=502, detail=f"TTS unavailable: {e}")
    return Response(content=wav, media_type="audio/wav")


@router.get("/voice/status", dependencies=[Depends(require_token)])
async def voice_status():
    """Read-only: has the real Jarvis voice been installed on this PC? Lets the app
    surface the INSTALL-VOICE upgrade to anyone still on the browser fallback. The
    app can't run the installer (a browser can't), so this only informs. No secrets."""
    return {"installed": server._voice_pkg_installed()}


@router.get("/push/key", dependencies=[Depends(require_token)])
async def push_key():
    """Hand the browser the VAPID public key it needs to subscribe."""
    return {"key": server.VAPID_PUBLIC_KEY,
            "enabled": bool(server.VAPID_PUBLIC_KEY and server.webpush)}


@router.post("/push/subscribe", dependencies=[Depends(require_token)])
async def push_subscribe(body: PushSubscribe):
    """Store (or refresh) the phone's push subscription. Dedupe by endpoint."""
    sub = body.subscription
    if not sub.get("endpoint"):
        raise HTTPException(status_code=400, detail="Bad subscription")
    subs = [s for s in server._load_subs() if s.get("endpoint") != sub["endpoint"]]
    subs.append(sub)
    server._save_subs(subs)
    return {"status": "subscribed", "count": len(subs)}


@router.post("/clientlog", dependencies=[Depends(require_token)])
@limiter.limit("60/minute")
async def clientlog(request: Request, body: ClientLog):
    """Beacon endpoint: the PWA reports a turn failure here so it lands in the
    server log. The phone has no dev console, so without this a 'Connection
    error' on-device is invisible — this records the specific cause."""
    server.log.warning(
        "CLIENT-FAIL reason=%s detail=%s sid=%s mode=%s elapsed_ms=%s",
        body.reason, body.detail, body.sid, body.mode, body.elapsed_ms,
    )
    return {"ok": True}


@router.post("/push/seen", dependencies=[Depends(require_token)])
async def push_seen():
    """Foreground heartbeat — the app is on-screen, so suppress the next banner."""
    server._last_seen = time.time()
    return {"ok": True}


@router.get("/push/last", dependencies=[Depends(require_token)])
async def push_last():
    """The most recent finished reply — used to speak it when a tap opens the app."""
    try:
        return json.loads(server.LAST_RESULT_FILE.read_text("utf-8"))
    except Exception:
        return {"result": "", "session_id": "", "ts": 0}
