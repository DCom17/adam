"""Integration routes: the Twilio SMS webhook, the add-on registry status, and
every add-on's wizard surface (bridge-code / test / enable / read routes) for
Calendar, LinkedIn, Email, SMS, Voicemail, and Hunter."""

from __future__ import annotations

import asyncio
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request, Response

import config
import gmail
import google_calendar
import hunter
import integration_config
import integration_registry
import linkedin
import twilio_sms
import twilio_voicemail
import voicemail_provision
import voicemail_store
from models import (
    CalendarEnableRequest,
    CalendarTestRequest,
    EmailEnableRequest,
    EmailTestRequest,
    HunterEnableRequest,
    HunterTestRequest,
    LinkedInEnableRequest,
    LinkedInTestRequest,
    SmsEnableRequest,
    SmsTestRequest,
    VoicemailEnableRequest,
    VoicemailGreetingRequest,
    VoicemailTestRequest,
    VoicemailWireRequest,
)
from security import require_token

import server

router = APIRouter()


@router.post("/sms")
async def sms(request: Request):
    """Twilio inbound-SMS webhook. Auth = Twilio signature + sender allowlist.
       Validates, kicks the message to Claude in the background, and returns empty
       TwiML immediately so Twilio doesn't time out. The reply arrives as a push."""
    # Parse the urlencoded body directly — Twilio always posts
    # application/x-www-form-urlencoded.
    raw = (await request.body()).decode("utf-8", errors="replace")
    params = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    signature = request.headers.get("X-Twilio-Signature", "")
    empty = Response(content="<Response></Response>", media_type="application/xml")

    if not server._valid_twilio_sig(server.TWILIO_WEBHOOK_URL, params, signature):
        raise HTTPException(status_code=403, detail="Bad Twilio signature")
    if server.OWNER_PHONE and params.get("From", "") != server.OWNER_PHONE:
        return empty  # unknown sender — ignore silently
    body = (params.get("Body") or "").strip()
    if body:
        server.keep_task(asyncio.create_task(server._run_sms_job(body)))
    return empty


@router.get("/integrations/status", dependencies=[Depends(require_token)])
async def integrations_status():
    """The add-on registry's live status — every optional add-on with its name,
    short description, setup path, and current status (available / needs_setup /
    enabled). Booleans and strings only; NO secret (bridge URLs, tokens, keys)
    is ever included. This is the single source of truth the Settings add-on
    cards render from and the brain reads to know its own capabilities."""
    return {"integrations": integration_registry.snapshot()}


# --- Google Calendar ---------------------------------------------------------

@router.get("/integrations/calendar/bridge-code", dependencies=[Depends(require_token)])
async def calendar_bridge_code():
    """Return the calendar_bridge.gs template text for the wizard's copy-code
    button. The template is shipped, non-secret code (the bridge token is
    generated inside the USER's own Apps Script, never here). Token-gated for
    consistency with the rest of the API. No secret is read or returned."""
    path = server.HERE / "calendar_bridge.gs"
    if not path.exists():
        raise HTTPException(status_code=404, detail="calendar_bridge.gs not found")
    return {"filename": "calendar_bridge.gs", "code": path.read_text("utf-8")}


@router.post("/integrations/calendar/test", dependencies=[Depends(require_token)])
async def calendar_test(req: CalendarTestRequest):
    """Validate a bridge URL + token against the user's deployed Apps Script
    bridge, WITHOUT saving or enabling anything (the wizard's green-check). This
    is a read (lists a 1-minute window). The bridge token is used only for this
    call and is never logged; any error is redacted of the submitted token before
    returning."""
    try:
        result = await asyncio.to_thread(
            google_calendar.probe_bridge,
            req.bridge_url, req.token, req.calendar_id or "primary",
        )
        return {"ok": True, "calendar_id": result.get("calendar_id", "primary")}
    except google_calendar.CalendarError as e:
        # Defense in depth: the connector never puts the token in its messages,
        # but redact anyway so a token can never ride out in an error string.
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": msg}


@router.post("/integrations/calendar/enable", dependencies=[Depends(require_token)])
async def calendar_enable(req: CalendarEnableRequest):
    """Turn the calendar connector ON by writing the app's OWN config — the
    one-click alternative to hand-editing settings.json + .env, which is the most
    error-prone step in setup (a stray comma/bracket breaks settings.json and the
    server won't boot). Re-validates the bridge first so config is never written
    for a bridge that doesn't work, then writes the non-secret block to
    settings.json and the bridge token to .env (each backed up first, each written
    atomically). The token is written only to the local .env and is NEVER logged
    or echoed back. A server RESTART is required for the new config to load."""
    # Re-probe so we never enable on unverified input (defends against a stale
    # field, a copy-paste slip, or a token that has since rotated).
    try:
        result = await asyncio.to_thread(
            google_calendar.probe_bridge,
            req.bridge_url, req.token, req.calendar_id or "primary",
        )
    except google_calendar.CalendarError as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": msg}

    cal_id = result.get("calendar_id", req.calendar_id or "primary")
    try:
        integration_config.set_settings_integration("google_calendar", {
            "enabled": True,
            "bridge_url": req.bridge_url.strip(),
            "calendar_id": cal_id,
            "timeout_seconds": 20,
        })
        integration_config.set_env_var(
            "GOOGLE_CALENDAR_TOKEN", req.token.strip(),
            section_header="# --- Google Calendar bridge ---",
        )
    except Exception as e:
        # A filesystem error must never carry the token out in its string.
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": "Could not save your config: " + msg}

    # Apply it live so Calendar works (and shows ENABLED) immediately — it's
    # request-driven, so no restart is needed.
    applied = config.refresh_integrations(integration_config.ROOT)
    return {"ok": True, "restart_required": not applied, "calendar_id": cal_id}


@router.get("/integrations/calendar/events", dependencies=[Depends(require_token)])
async def calendar_events(time_min: str | None = None, time_max: str | None = None,
                          calendar_id: str | None = None):
    """Read events from the user's calendar bridge. READ-ONLY — no approval is
    needed and nothing is ever created or edited here. Defaults to the next 7
    days when no window is given. This is the reference 'safe read' surface every
    add-on copies: token-gated, configured-gated, connector errors surfaced
    token-free. Returns 400 if the connector isn't set up, 502 if the bridge call
    fails (the message carries no token by construction)."""
    if not google_calendar.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Google Calendar is not configured. Set it up at /setup-calendar.",
        )
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    tmin = time_min or now.isoformat()
    tmax = time_max or (now + timedelta(days=7)).isoformat()
    try:
        events = await asyncio.to_thread(google_calendar.list_events, tmin, tmax, calendar_id)
        return {"ok": True, "count": len(events), "events": events}
    except google_calendar.CalendarError as e:
        raise HTTPException(status_code=502, detail=str(e))


# --- LinkedIn -----------------------------------------------------------------

@router.post("/integrations/linkedin/test", dependencies=[Depends(require_token)])
async def linkedin_test(req: LinkedInTestRequest):
    """Validate a LinkedIn member access token against /v2/userinfo WITHOUT saving
    or enabling anything (the wizard's green-check). Read-only; resolves the author
    URN the API lane needs. The token is used only for this call and is never
    logged; any error is redacted of the submitted token before returning. There
    is deliberately NO agent-callable post route — publishing is an explicit
    operator action against an approved draft, keeping 'never auto-post' structural."""
    try:
        result = await asyncio.to_thread(linkedin.probe_credentials, req.access_token)
        return {"ok": True, "author_urn": result.get("author_urn", ""),
                "name": result.get("name", "")}
    except linkedin.LinkedInError as e:
        msg = str(e)
        if req.access_token and req.access_token in msg:
            msg = msg.replace(req.access_token, "***")
        return {"ok": False, "error": msg}


@router.post("/integrations/linkedin/enable", dependencies=[Depends(require_token)])
async def linkedin_enable(req: LinkedInEnableRequest):
    """Turn LinkedIn ON by writing the app's OWN config — the one-click alternative
    to hand-editing settings.json (+ .env for the API lane).

    DRAFT lane (api_enabled=false): writes only the settings.json linkedin block —
    no credentials, no .env. This is the recommended default (Adam drafts, you
    paste). API auto-post lane (api_enabled=true): re-validates the access token,
    resolves the author URN, writes the settings block (client_id + author_urn) AND
    the .env secrets. The access token / client secret are written only to the local
    .env and are NEVER logged or echoed back. A server RESTART is required."""
    if not req.api_enabled:
        # Draft lane — settings only, no secrets touched.
        try:
            integration_config.set_settings_integration("linkedin", {
                "enabled": True, "api_enabled": False,
                "client_id": "", "author_urn": "", "timeout_seconds": 20,
            })
        except Exception as e:
            return {"ok": False, "error": "Could not save your config: " + str(e)}
        applied = config.refresh_integrations(integration_config.ROOT)
        return {"ok": True, "restart_required": not applied, "api_enabled": False}

    # API auto-post lane — validate the token, resolve the author URN, write secrets.
    if not (req.access_token or "").strip():
        return {"ok": False, "error": "The auto-post lane needs your member access token."}
    try:
        result = await asyncio.to_thread(linkedin.probe_credentials, req.access_token)
    except linkedin.LinkedInError as e:
        msg = str(e)
        for sec in (req.access_token, req.client_secret):
            if sec and sec in msg:
                msg = msg.replace(sec, "***")
        return {"ok": False, "error": msg}

    author_urn = result.get("author_urn", "")
    try:
        integration_config.set_settings_integration("linkedin", {
            "enabled": True, "api_enabled": True,
            "client_id": (req.client_id or "").strip(),
            "author_urn": author_urn,
            "timeout_seconds": 20,
        })
        integration_config.set_env_var(
            "LINKEDIN_ACCESS_TOKEN", req.access_token.strip(),
            section_header="# --- LinkedIn API auto-post lane ---",
        )
        if (req.client_secret or "").strip():
            integration_config.set_env_var("LINKEDIN_CLIENT_SECRET", req.client_secret.strip())
    except Exception as e:
        msg = str(e)
        for sec in (req.access_token, req.client_secret):
            if sec and sec in msg:
                msg = msg.replace(sec, "***")
        return {"ok": False, "error": "Could not save your config: " + msg}

    applied = config.refresh_integrations(integration_config.ROOT)
    return {"ok": True, "restart_required": not applied, "api_enabled": True, "author_urn": author_urn}


# --- Email (Gmail bridge) -----------------------------------------------------

@router.get("/integrations/email/bridge-code", dependencies=[Depends(require_token)])
async def email_bridge_code():
    """Return the gmail_bridge.gs template text for the wizard's copy-code button.
    The template is shipped, non-secret code (the bridge token is generated inside
    the USER's own Apps Script, never here). Token-gated for consistency with the
    rest of the API. No secret is read or returned."""
    path = server.HERE / "gmail_bridge.gs"
    if not path.exists():
        raise HTTPException(status_code=404, detail="gmail_bridge.gs not found")
    return {"filename": "gmail_bridge.gs", "code": path.read_text("utf-8")}


@router.post("/integrations/email/test", dependencies=[Depends(require_token)])
async def email_test(req: EmailTestRequest):
    """Validate a bridge URL + token against the user's deployed Apps Script Gmail
    bridge, WITHOUT saving or enabling anything (the wizard's green-check). This is
    a read (one inbox message) and returns the account email only — never message
    contents. The bridge token is used only for this call and is never logged; any
    error is redacted of the submitted token before returning."""
    try:
        result = await asyncio.to_thread(gmail.probe_bridge, req.bridge_url, req.token)
        return {"ok": True, "account": result.get("account", "")}
    except gmail.GmailError as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": msg}


@router.post("/integrations/email/enable", dependencies=[Depends(require_token)])
async def email_enable(req: EmailEnableRequest):
    """Turn the email connector ON by writing the app's OWN config — the one-click
    alternative to hand-editing settings.json + .env. Re-validates the bridge first,
    then writes the gmail block to settings.json (DRAFT-ONLY: allow_send stays false)
    and the bridge token to .env (each backed up, each atomic). The token is written
    only to the local .env and is NEVER logged or echoed back. A server RESTART is
    required for the new config to load."""
    try:
        await asyncio.to_thread(gmail.probe_bridge, req.bridge_url, req.token)
    except gmail.GmailError as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": msg}

    try:
        integration_config.set_settings_integration("gmail", {
            "enabled": True,
            "bridge_url": req.bridge_url.strip(),
            "allow_send": False,
            "timeout_seconds": 20,
        })
        integration_config.set_env_var(
            "GMAIL_BRIDGE_TOKEN", req.token.strip(),
            section_header="# --- Gmail bridge ---",
        )
    except Exception as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": "Could not save your config: " + msg}

    # Apply it live so the add-on works (and shows ENABLED) immediately — email is
    # request-driven (the connector reads config at call time), so no restart needed.
    applied = config.refresh_integrations(integration_config.ROOT)
    return {"ok": True, "restart_required": not applied}


# --- SMS ------------------------------------------------------------------------

@router.post("/integrations/sms/test", dependencies=[Depends(require_token)])
async def sms_test(req: SmsTestRequest):
    """Verify a Twilio Account SID + Auth Token + number WITHOUT saving or enabling
    anything (the wizard's green-check). One authenticated read; a success proves
    the credentials work. The Auth Token is used only for this call, never logged,
    and scrubbed from any error before returning."""
    try:
        result = await asyncio.to_thread(
            twilio_sms.probe, req.account_sid, req.auth_token, req.number
        )
        return {"ok": True, "number_configured": bool(result.get("number_configured"))}
    except twilio_sms.TwilioError as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": msg}


@router.post("/integrations/sms/enable", dependencies=[Depends(require_token)])
async def sms_enable(req: SmsEnableRequest):
    """Turn inbound SMS ON by writing the app's OWN config — the one-click alternative
    to hand-editing settings.json + .env. Re-validates the Twilio credentials first
    (one authenticated read), then writes the sms block to settings.json and the four
    values to .env: TWILIO_ACCOUNT_SID, TWILIO_NUMBER, TWILIO_AUTH_TOKEN, and OWNER_PHONE
    (the only number whose texts are acted on). The Auth Token is written only to the
    local .env and is NEVER logged or echoed back. A server RESTART is required to start
    the poller."""
    try:
        await asyncio.to_thread(
            twilio_sms.probe, req.account_sid, req.auth_token, req.number
        )
    except twilio_sms.TwilioError as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": msg}

    try:
        integration_config.set_settings_integration("sms", {
            "enabled": True,
            "poll_interval_seconds": 15,
            "poll_timeout_seconds": 15,
        })
        integration_config.set_env_var(
            "TWILIO_ACCOUNT_SID", req.account_sid.strip(),
            section_header="# --- Twilio inbound SMS ---",
        )
        integration_config.set_env_var("TWILIO_NUMBER", req.number.strip())
        integration_config.set_env_var("TWILIO_AUTH_TOKEN", req.auth_token.strip())
        if req.owner_phone and req.owner_phone.strip():
            integration_config.set_env_var("OWNER_PHONE", req.owner_phone.strip())
    except Exception as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": "Could not save your config: " + msg}

    return {"ok": True, "restart_required": True}


# --- Voicemail --------------------------------------------------------------------

@router.post("/integrations/voicemail/test", dependencies=[Depends(require_token)])
async def voicemail_test(req: VoicemailTestRequest):
    """Verify the Twilio creds + that the account owns the number, WITHOUT saving or
    enabling anything (the wizard's green-check). One authenticated read; the Auth
    Token is used only for this call, never logged, and scrubbed from any error."""
    try:
        result = await asyncio.to_thread(
            twilio_voicemail.probe, req.account_sid, req.auth_token, req.number
        )
        return {"ok": True, "number_owned": bool(result.get("number_owned"))}
    except twilio_voicemail.TwilioError as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": msg}


@router.get("/integrations/voicemail/answer-twiml", dependencies=[Depends(require_token)])
async def voicemail_answer_twiml(owner_name: str | None = None, greeting: str | None = None):
    """Return the TwiML the user pastes into a Twilio TwiML Bin to make the number
    answer + record (the 'deploy the external thing' step, like the Apps Script
    bridges). Non-secret, generated from the shipped template; the British <Say>
    greeting needs no audio hosting. Token-gated for consistency."""
    text = (greeting or "").strip() or None
    if text is None and (owner_name or "").strip():
        text = voicemail_provision.DEFAULT_GREETING.format(owner=(owner_name or "").strip())
    twiml = voicemail_provision.build_answer_twiml(text)
    return {"twiml": twiml}


@router.post("/integrations/voicemail/greeting-preview", dependencies=[Depends(require_token)])
async def voicemail_greeting_preview(req: VoicemailGreetingRequest):
    """Render the greeting in Adam's Kokoro voice (bm_daniel) so the user can hear
    the upgrade voice before deciding to host it. Returns audio/wav, or 503 if the
    local TTS service isn't running. No credentials, nothing saved."""
    text = (req.text or "").strip() or None
    if text is None and (req.owner_name or "").strip():
        text = voicemail_provision.DEFAULT_GREETING.format(owner=(req.owner_name or "").strip())
    wav = await asyncio.to_thread(voicemail_provision.render_greeting_clip, text)
    if not wav:
        raise HTTPException(status_code=503,
                            detail="Couldn't reach the local Kokoro voice service (tts_server).")
    return Response(content=wav, media_type="audio/wav")


@router.post("/integrations/voicemail/wire", dependencies=[Depends(require_token)])
async def voicemail_wire(req: VoicemailWireRequest):
    """Point the Twilio number's Voice webhook at the user's TwiML Bin URL via the
    Twilio API — the one-click 'wire my number' so they don't hunt through the
    console. Resolves the number's PN SID first. The Auth Token is used transiently,
    never logged, and scrubbed from any error."""
    try:
        pn_sid = await asyncio.to_thread(
            twilio_voicemail.incoming_number_sid, req.account_sid, req.auth_token, req.number
        )
    except twilio_voicemail.TwilioError as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": msg}
    if not pn_sid:
        return {"ok": False, "error": "That number isn't on this Twilio account."}
    try:
        result = await asyncio.to_thread(
            voicemail_provision.set_number_voice_url,
            req.account_sid, req.auth_token, pn_sid, req.voice_url,
        )
        return {"ok": True, "voice_url": result.get("voice_url", req.voice_url)}
    except voicemail_provision.ProvisionError as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": msg}


@router.post("/integrations/voicemail/enable", dependencies=[Depends(require_token)])
async def voicemail_enable(req: VoicemailEnableRequest):
    """Turn the voicemail poller ON by writing the app's OWN config — the one-click
    alternative to hand-editing settings.json + .env. Re-validates the Twilio creds
    first, then writes the voicemail block to settings.json and the three Twilio
    values to .env (shared with SMS). The Auth Token is written only to the local
    .env and is NEVER logged or echoed back. A server RESTART starts the poller."""
    try:
        await asyncio.to_thread(
            twilio_voicemail.probe, req.account_sid, req.auth_token, req.number
        )
    except twilio_voicemail.TwilioError as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": msg}

    mode = (req.transcribe_mode or "twilio").strip().lower()
    if mode not in ("twilio", "whisper"):
        mode = "twilio"
    try:
        integration_config.set_settings_integration("voicemail", {
            "enabled": True,
            "poll_interval_seconds": 20,
            "poll_timeout_seconds": 20,
            "transcribe_mode": mode,
            "greeting_text": (req.greeting_text or "").strip(),
            "owner_name": (req.owner_name or "").strip(),
        })
        integration_config.set_env_var(
            "TWILIO_ACCOUNT_SID", req.account_sid.strip(),
            section_header="# --- Twilio (shared by SMS + Voicemail) ---",
        )
        integration_config.set_env_var("TWILIO_NUMBER", req.number.strip())
        integration_config.set_env_var("TWILIO_AUTH_TOKEN", req.auth_token.strip())
    except Exception as e:
        msg = str(e)
        if req.auth_token and req.auth_token in msg:
            msg = msg.replace(req.auth_token, "***")
        return {"ok": False, "error": "Could not save your config: " + msg}

    return {"ok": True, "restart_required": True}


@router.get("/voicemails", dependencies=[Depends(require_token)])
async def list_voicemails():
    """List stored voicemails (newest last), for the console/app. Token-gated.
    Contains caller number, name, duration, and transcript — never a secret."""
    return {"voicemails": voicemail_store.load_all()}


# --- Hunter ---------------------------------------------------------------------

@router.get("/integrations/hunter/bridge-code", dependencies=[Depends(require_token)])
async def hunter_bridge_code():
    """Return the hunter_dashboard.gs script text for the wizard's copy-code
    button. This is the full bound-script template the user pastes into a blank
    Sheet: setupHunterDashboard() builds the tabs, doPost() is the sync bridge,
    and doGet()?action=verify is the read-back. It is shipped, non-secret code —
    the bridge token is generated inside the USER's own Apps Script (setHunterToken),
    never here. Token-gated for consistency. No secret is read or returned."""
    path = server.HERE / "hunter_dashboard.gs"
    if not path.exists():
        raise HTTPException(status_code=404, detail="hunter_dashboard.gs not found")
    return {"filename": "hunter_dashboard.gs", "code": path.read_text("utf-8")}


@router.post("/integrations/hunter/test", dependencies=[Depends(require_token)])
async def hunter_test(req: HunterTestRequest):
    """Validate a bridge URL + token against the user's deployed Hunter verify
    endpoint, WITHOUT saving or enabling anything (the wizard's green-check). This
    is a read. The bridge token is used only for this call and is never logged;
    any error is redacted of the submitted token before returning."""
    try:
        result = await asyncio.to_thread(hunter.probe_bridge, req.bridge_url, req.token)
        return {"ok": True,
                "quest_count": result.get("quest_count"),
                "stat_count": result.get("stat_count")}
    except hunter.HunterError as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": msg}


@router.post("/integrations/hunter/enable", dependencies=[Depends(require_token)])
async def hunter_enable(req: HunterEnableRequest):
    """Turn the Hunter tracker ON by writing the app's OWN config — the one-click
    alternative to hand-editing settings.json + .env (the most error-prone step;
    a stray comma/bracket breaks settings.json and the server won't boot). Re-
    validates the bridge first so config is never written for a bridge that doesn't
    work, then writes the non-secret block to settings.json and the bridge token to
    .env (each backed up first, each written atomically). The token is written only
    to the local .env and is NEVER logged or echoed back. A server RESTART is
    required for the new config to load."""
    try:
        await asyncio.to_thread(hunter.probe_bridge, req.bridge_url, req.token)
    except hunter.HunterError as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": msg}

    try:
        integration_config.set_settings_integration("hunter", {
            "enabled": True,
            "bridge_url": req.bridge_url.strip(),
            "timeout_seconds": 20,
        })
        integration_config.set_env_var(
            "HUNTER_TOKEN", req.token.strip(),
            section_header="# --- Hunter dashboard bridge ---",
        )
    except Exception as e:
        msg = str(e)
        if req.token and req.token in msg:
            msg = msg.replace(req.token, "***")
        return {"ok": False, "error": "Could not save your config: " + msg}

    # Apply it live so Hunter works (and shows ENABLED) immediately — request-driven,
    # so no restart is needed.
    applied = config.refresh_integrations(integration_config.ROOT)
    return {"ok": True, "restart_required": not applied}


@router.get("/integrations/hunter/board", dependencies=[Depends(require_token)])
async def hunter_board():
    """Read-only: return the user's live Hunter board (state, stats, daily_quests,
    bosses, weaknesses, rank_rules) for the in-app dashboard view. Calls the same
    verify endpoint the connector uses — never writes. Booleans/data only; the
    bridge token is used server-side and never returned."""
    try:
        return {"ok": True, "board": hunter.board()}
    except hunter.HunterError as e:
        return {"ok": False, "error": str(e)}
