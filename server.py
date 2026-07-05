"""
Adam — FastAPI backend.

Bridges a phone voice web app to Claude Code running on the user's own machine,
against the user's own files, using the user's own Claude Code credentials. Flow:

    POST /ask  ->  spawn  claude.exe -p --output-format json [--resume id]
               ->  return clean text from the top-level `result` field

Security model: a single bearer token (the token IS the security). CORS defaults
to open because the token is the gate; tighten via settings.json for production.

Every machine-specific value lives in config.py — this file has no personal paths.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

try:
    from pywebpush import webpush, WebPushException
except Exception:  # pragma: no cover — push just stays disabled if missing
    webpush = None
    WebPushException = Exception

import config
import permissions
import approvals
import proposed_changes
import job_store
import usage_store
try:
    import session_store
except Exception:  # a missing module (e.g. a partial update) must NOT brick the server
    session_store = None
import google_calendar
import hunter
import integration_config
import integration_registry
import twilio_sms
import twilio_voicemail
import voicemail_provision
import voicemail_store
import voicemail_contacts
import gmail
import linkedin
import external_actions
import merge
import updater

# Shared pieces split out of this module. Every name is re-exported here for
# back-compat: tests (and the routers) reach them as server.<name>.
from models import (  # noqa: F401 — re-exports
    ActionProposeRequest, ApprovalCreate, AskRequest, CalendarEnableRequest,
    CalendarTestRequest, CapabilityTierBody, ClientLog, EmailEnableRequest,
    EmailTestRequest, HunterEnableRequest, HunterTestRequest,
    LinkedInEnableRequest, LinkedInTestRequest, ProposedChangeCreate,
    PushSubscribe, SessionRecord, SessionSyncPush, SmsEnableRequest,
    SmsTestRequest, SpeakRequest, UiPrefs, VoicemailEnableRequest,
    VoicemailGreetingRequest, VoicemailTestRequest, VoicemailWireRequest,
)
from rate_limit import limiter
from security import require_token  # noqa: F401 — re-export; routes Depends() on it

# --- Configuration ----------------------------------------------------------

config.ensure_dirs()
config.validate()

HERE = Path(__file__).resolve().parent
FRONTEND = HERE / "web" / "index.html"

ADAM_TOKEN = config.ADAM_TOKEN
CLAUDE_EXE = config.CLAUDE_EXE
VAULT_PATH = config.VAULT_PATH
VOICE_MODEL = config.VOICE_MODEL
WORK_EXTRA_DIRS = config.WORK_EXTRA_DIRS

# --- Web Push (PWA notifications) -------------------------------------------
# When a background job finishes, push the result to the installed Home Screen
# PWA so the phone is notified even with the app closed. VAPID keypair: public
# key goes to the browser, private key (PEM) stays here and signs the push.
VAPID_PUBLIC_KEY = config.VAPID_PUBLIC_KEY
VAPID_SUBJECT = config.VAPID_SUBJECT
VAPID_PRIVATE_PEM = config.VAPID_PRIVATE_PEM
# Push subscriptions persist to disk so a server restart doesn't lose them.
PUSH_SUB_FILE = config.PUSH_SUB_FILE
# The most recent finished result, so a notification tap can re-fetch and speak
# it even though /poll is one-shot. Single user → one slot is enough.
LAST_RESULT_FILE = config.LAST_RESULT_FILE
# Small user-togglable runtime prefs (e.g. auto-apply proposed changes), persisted
# alongside the rest of the state so the choice survives a restart.
UI_PREFS_FILE = LAST_RESULT_FILE.parent / "ui_prefs.json"
# Where the auto-generated public key is cached (next to the private PEM).
VAPID_PUBLIC_FILE = config.STATE_DIR / "vapid_public.txt"


def _ensure_vapid_keypair() -> None:
    """Guarantee a *matched* VAPID keypair so Web Push actually delivers.

    The product shipped a public key in .env with no matching private PEM, so
    every push silently no-op'd (``_send_push`` returns when the PEM is absent).
    On first start we generate a self-consistent P-256 keypair — private PEM under
    data/state, public key (browser ``applicationServerKey``) cached beside it —
    and serve that public key from /push/key. A hand-provided .env keypair (public
    key AND a matching PEM both present) is left untouched. If generation isn't
    possible the app still runs; push just stays disabled.
    """
    global VAPID_PUBLIC_KEY
    pem_path = VAPID_PRIVATE_PEM
    # A previously auto-generated pair on disk is authoritative — this also makes
    # us ignore any stale/orphaned VAPID_PUBLIC_KEY left behind in .env.
    if pem_path.exists() and VAPID_PUBLIC_FILE.exists():
        try:
            saved = VAPID_PUBLIC_FILE.read_text("utf-8").strip()
            if saved:
                VAPID_PUBLIC_KEY = saved
            return
        except Exception:
            pass
    # An explicit, hand-set .env keypair (public key + its matching PEM) wins.
    if VAPID_PUBLIC_KEY and pem_path.exists():
        return
    try:
        import base64 as _b64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        priv = ec.generate_private_key(ec.SECP256R1())
        pem_path.parent.mkdir(parents=True, exist_ok=True)
        # SEC1 ("BEGIN EC PRIVATE KEY") is the format py_vapid reads most reliably
        # across versions; pywebpush is handed this file path to sign each push.
        pem_path.write_bytes(priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )  # 65-byte uncompressed point (0x04 || X || Y)
        pub_b64 = _b64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        VAPID_PUBLIC_FILE.write_text(pub_b64, encoding="utf-8")
        VAPID_PUBLIC_KEY = pub_b64
        log.info("vapid: generated a fresh push keypair (%s)", pem_path.name)
    except Exception as e:  # noqa: BLE001 — push is optional; never block startup
        log.warning("vapid: could not generate a push keypair: %s", e)


def _load_ui_prefs() -> dict:
    try:
        return json.loads(UI_PREFS_FILE.read_text("utf-8"))
    except Exception:
        return {}


def _get_auto_apply() -> bool:
    return bool(_load_ui_prefs().get("auto_apply_proposed", False))


def _set_auto_apply(on: bool) -> None:
    prefs = _load_ui_prefs()
    prefs["auto_apply_proposed"] = bool(on)
    _save_ui_prefs(prefs)


def _get_auto_run_calendar() -> bool:
    """Whether calendar create/edit actions the assistant stages should RUN
    immediately instead of waiting for a tap in the approval panel. Off by default
    (the shipped/safe posture); the owner turns it on for hands-free, phone-friendly
    calendaring. Only calendar actions auto-run — file/code changes, email send, and
    LinkedIn posts always stay gated."""
    return bool(_load_ui_prefs().get("auto_run_calendar", False))


def _set_auto_run_calendar(on: bool) -> None:
    prefs = _load_ui_prefs()
    prefs["auto_run_calendar"] = bool(on)
    _save_ui_prefs(prefs)


def _get_auto_run_hunter() -> bool:
    """Whether a hunter.sync action the assistant stages (push the Hunter dashboard
    to the user's own Sheet) should RUN immediately instead of waiting for a tap in
    the approval panel. Off by default (the shipped/safe posture); the owner turns it
    on so daily planning / shutdown can sync the board hands-free. Mirrors
    auto_run_calendar exactly. Only hunter.sync auto-runs — file/code changes, email
    send, and LinkedIn posts always stay gated. Safe to auto-run: hunter has no delete
    path by construction (see hunter.py) and the payload carries no secret."""
    return bool(_load_ui_prefs().get("auto_run_hunter", False))


def _set_auto_run_hunter(on: bool) -> None:
    prefs = _load_ui_prefs()
    prefs["auto_run_hunter"] = bool(on)
    _save_ui_prefs(prefs)


# Self-edit is no longer a standalone runtime toggle: as of 0.9.17 it is governed
# solely by the capability tier (Unrestricted turns it on; Safe/Powerful keep it off).
# The live flag is config.PERM_ALLOW_APP_SELF_EDIT, set by config._apply_capability_tier().
# Custom mode (no tier) still honors permissions.allow_app_self_edit in settings.json.


def _save_ui_prefs(prefs: dict) -> None:
    try:
        UI_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        UI_PREFS_FILE.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        log.exception("failed to persist ui prefs")


# Verbs for narrating a proposal's resolution back to the agent.
_OUTCOME_VERB = {
    "applied": "applied",
    "denied": "denied by the user",
    "conflict": "blocked — the file had changed since the proposal",
    "failed": "failed to apply",
}


def _proposal_outcome_note() -> str:
    """One-line context fed into the NEXT turn's prompt: which proposed file changes
    the user resolved (applied/denied/…) since the last turn, so the agent can
    acknowledge naturally instead of guessing. A marker advances every turn so the
    same outcome is never reported twice. Default-on; cheap when there's nothing."""
    prefs = _load_ui_prefs()
    marker = int(prefs.get("proposals_reported_ts") or 0)
    try:
        resolved = proposed_changes.resolved_since(marker)
    except Exception:
        resolved = []
    prefs["proposals_reported_ts"] = int(time.time())  # advance regardless → no repeats
    _save_ui_prefs(prefs)
    if not resolved:
        return ""
    lines = []
    for r in resolved[:8]:
        name = os.path.basename(r.get("target_path") or "") or r.get("id")
        lines.append(f"- {name}: {_OUTCOME_VERB.get(r.get('status'), r.get('status'))}")
    return (
        "\n\nCONTEXT — proposed file changes the user resolved since your last turn "
        "(they act on these in an approval panel you cannot see or operate):\n"
        + "\n".join(lines)
        + "\nAcknowledge naturally only if relevant; do NOT re-propose a denied change."
    )
# The app heartbeats /push/seen while it's on-screen. If a result lands within
# this window of the last heartbeat, the app is foreground (it'll speak the reply
# itself), so we skip the push banner. Banners only fire when the app is away.
FOREGROUND_SEEN_WINDOW = 8.0  # seconds
_last_seen = 0.0

# --- Twilio inbound SMS -----------------------------------------------------
# "Hey Meta, text Adam: ..." → Twilio POSTs the SMS here. Auth is Twilio's
# request signature (Twilio can't send our bearer token), plus a sender allowlist.
TWILIO_AUTH_TOKEN = config.TWILIO_AUTH_TOKEN
TWILIO_WEBHOOK_URL = config.TWILIO_WEBHOOK_URL
OWNER_PHONE = config.OWNER_PHONE

# Local Kokoro TTS service. Localhost-only; we proxy to it so the phone never
# needs to reach it directly. If it's down, the frontend falls back to browser TTS.
TTS_URL = config.TTS_URL
TTS_TIMEOUT_SECONDS = config.TTS_TIMEOUT_SECONDS

# How long to wait on a single Claude turn. First call of a session loads the
# full vault/CLAUDE.md startup protocol (~8-20s); give it generous headroom.
CLAUDE_TIMEOUT_SECONDS = config.CLAUDE_TIMEOUT_SECONDS
# Async (fire-and-poll) jobs aren't bound by the edge HTTP timeout — only polls are.
ASYNC_CLAUDE_TIMEOUT_SECONDS = config.ASYNC_CLAUDE_TIMEOUT_SECONDS
# Drop finished/abandoned jobs after this long so JOBS can't grow unbounded.
JOB_TTL_SECONDS = config.JOB_TTL_SECONDS

VOICE_SYSTEM_PROMPT = (
    "You are in Adam voice mode. Keep responses to 1-3 sentences maximum "
    "unless detail is explicitly requested. No markdown, no bullet points, no "
    "headers, no asterisks, no check marks or emoji, no symbol characters — they "
    "get read aloud. Write plain spoken sentences. Speak conversationally and "
    "directly. Maintain the dry, precise "
    "Adam tone at all times. Sir when appropriate. "
    "Never use <<SPEAK>> tags, summary markers, or a separate spoken-summary line — "
    "reply in plain spoken sentences only, even if earlier turns in this conversation "
    "used such markers."
)

# Work mode: the same engine unmuzzled. Long, thorough replies are fine because
# they render on-screen; only the trailing <<SPEAK>> summary is read aloud. The
# tag is mandatory — _extract_spoken() falls back gracefully if a reply omits it.
WORK_SYSTEM_PROMPT = (
    "You are Adam in work mode — a full Claude Code agent on the user's machine. "
    "Long, thorough replies are fine; use every tool; investigate and make decisions "
    "rather than asking permission for routine steps. Keep the dry, precise Adam "
    "register. Detail may use normal formatting — it is shown on a screen, not only "
    "spoken. End EVERY reply with a one or two sentence spoken summary wrapped exactly "
    "in <<SPEAK>> and <<SPEAK>> — plain text, no markdown, no symbols — for text to "
    "speech. Before any destructive or outward-facing action (delete, force push, "
    "send a message, external API write), state what you will do and ask for a yes first."
)


# Claude Code mode: a chat the user deliberately escalated (long-press on the
# Operator Mode button; gated by agent_safety.allow_code_mode, default OFF). The
# spawn is RAW Claude Code — full tools, cwd=vault, permission prompts bypassed —
# so this prompt must be honest about that power, keep the voice loop working
# (<<SPEAK>> summary), and demand a heads-up before anything irreversible.
CODE_SYSTEM_PROMPT = (
    "You are Adam in Claude Code mode — raw Claude Code with FULL tools (file "
    "edits, shell, everything) running directly in the user's files. The safety "
    "wrapper that normally turns your writes into approval-gated proposals is OFF "
    "for this chat: what you do happens immediately, for real. The user escalated "
    "this chat deliberately. Work like Claude Code: investigate, edit, run, verify. "
    "Keep the dry, precise Adam register. Long on-screen replies are fine — they "
    "render on a screen. End EVERY reply with a one or two sentence spoken summary "
    "wrapped exactly in <<SPEAK>> and <<SPEAK>> — plain text, no markdown, no "
    "symbols — for text to speech. Never use <<PROPOSE>> blocks here; edit files "
    "directly. Before anything destructive or hard to reverse (delete, force push, "
    "sending anything anywhere), say what you're about to do and get a yes first."
)


def _normalize_mode(mode: str | None) -> str:
    """'voice' | 'work' | 'code'; anything else (incl. None) means voice.
    'code' is only reachable when agent_safety.allow_code_mode is on — run_claude
    enforces that (403), so a stale/forged client value can't escalate silently."""
    m = (mode or "").strip().lower()
    return m if m in ("work", "code") else "voice"


# Draft-mode addendum (safe agent modes): Claude has no file-editing or shell
# tools, so it must PROPOSE changes for the server to apply, never edit directly.
# The <<PROPOSE>> blocks are parsed out of the reply into proposed-change records.
DRAFT_MODE_NOTE = (
    "\n\nSAFETY MODE: You are running WITHOUT any file-editing or shell tools "
    "(Write/Edit/Bash are disabled) and your working directory is a throwaway "
    "sandbox, not the user's files. You CANNOT modify the user's files directly. "
    "When a file should be created or changed, do NOT try to edit it — PROPOSE the "
    "change so the server can apply it after the user approves. Emit each proposed "
    "file change as a block in EXACTLY this format, each marker on its own line:\n"
    "<<PROPOSE path=\"name.ext\" action=\"create\" risk=\"low\" summary=\"one line\">>\n"
    "the full proposed file content goes here\n"
    "<<END_PROPOSE>>\n"
    "action is one of create|edit|replace|delete|rename. For delete and rename, "
    "include no body (for rename add newpath=\"...\"). Explain the proposal in your "
    "normal reply; the blocks are extracted automatically, so keep them exact.\n"
    "IMPORTANT — you do NOT approve or apply changes. The user reviews each proposal in "
    "an on-screen panel (an Approve & Apply / Deny control) that you cannot see or "
    "operate. So: never say a change was approved, applied, saved, written, or 'on its "
    "way' — it is only PROPOSED until the user acts. Do not ask the user to approve it, "
    "and do not tell them to confirm 'so we can carry on'. Just describe what you "
    "proposed and stop. Do not nag for approval or chase a pending proposal: if and "
    "when the user resolves it, you'll be told the outcome at the start of a later "
    "turn (applied/denied) — acknowledge it then, and never re-pitch a denied change."
)


def _addon_awareness_note() -> str:
    """Tell the agent which optional add-ons are ENABLED (it may use them) vs not
    set up (it must REDIRECT, never fake the capability). Fed live from the
    registry; booleans/strings only, no secret. Applies in both voice and work
    modes so 'put this on my calendar' is handled correctly either way."""
    snap = integration_registry.snapshot()
    enabled = [a for a in snap if a["enabled"]]
    available = [a for a in snap if not a["enabled"]]
    parts = ["\n\nADD-ONS (optional capabilities):"]
    if enabled:
        parts.append(
            "Enabled (you may use these; any write still needs the user's approval): "
            + "; ".join(f"{a['name']} — {a['short_description']}" for a in enabled) + "."
        )
    else:
        parts.append("Enabled: none.")
    if available:
        parts.append(
            "NOT set up — do not pretend you can do these. If a request needs one, say it "
            "isn't set up yet and tell the user to add it in Settings -> Add-ons (/settings): "
            + "; ".join(f"{a['name']}" for a in available) + "."
        )
        parts.append(
            "Setting up an add-on (API keys, Google sign-ins, pasting a Google script) is "
            "best done ON A COMPUTER, not a phone — say so when you suggest one. Once set up, "
            "the add-on works from anywhere, including the phone."
        )
    # The phone-connection capability is NOT a registry add-on (it's a built-in
    # remote-access setup, not a calendar-style integration), so it has to be named
    # explicitly or the agent never knows it exists. It's one of the biggest
    # capabilities — using Adam (voice included) from a phone, anywhere.
    parts.append(
        "USE ME ON YOUR PHONE (built-in — this is a major capability, treat it like a "
        "headline add-on): the user can run Adam, voice and all, from their phone while "
        "it talks to this same PC. To set it up: open the gear menu (the cog) -> Operator "
        "Console -> the 'Connect phone' section — scanning the QR there uses Adam on the "
        "SAME Wi-Fi, and setting up Tailscale (guide: docs/CONNECT_YOUR_PHONE.md) lets them "
        "use it ANYWHERE, on the go. When the user asks what you can do, about add-ons, or "
        "about using you on the go / on mobile, ALWAYS mention this and offer to walk them "
        "through it. Don't claim a phone is already connected unless asked to set it up."
    )
    parts.append(
        "When asked what you can do, mention the enabled add-ons, the phone capability above, "
        "and offer the add-ons not yet set up. Never invent a result for a disabled capability."
    )
    return "\n".join(parts)


# A turn dies in ~2s with a bare "connection error" when Claude Code isn't signed in
# — invisible to a non-technical user. Detect that specific failure and surface clear
# sign-in guidance instead. The sentinel lets the frontend show the message verbatim
# (and speak a short line) rather than the generic connection-error.
AUTH_REQUIRED_SENTINEL = "JVL_AUTH_REQUIRED:"
AUTH_REQUIRED_MESSAGE = (
    "Adam isn't signed in to Claude. Open a terminal (or the black Adam window), "
    "type  claude  and press Enter, then type  /login  and follow the prompts to sign "
    "in. Then try again."
)
_AUTH_FAILURE_MARKERS = (
    "/login", "claude login", "not logged in", "log in to", "not authenticated",
    "authenticat", "invalid api key", "unauthorized", "no api key", "credentials",
)


def _is_claude_auth_failure(err: str) -> bool:
    """True if Claude's stderr looks like a not-signed-in / auth failure (vs any
    other crash). Keyword match on the CLI's own wording."""
    e = (err or "").lower()
    return any(m in e for m in _AUTH_FAILURE_MARKERS)


_ACTION_BLOCK_RE = re.compile(r"<<ACTION\b([^>]*)>>(.*?)<<END_ACTION>>", re.DOTALL)
_ACTION_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _action_proposal_note(auto_run_calendar: bool = False, auto_run_hunter: bool = False) -> str:
    """Tell the agent which external actions it may STAGE and the exact block format.
    The agent never executes — it emits a block, the server does the work. By default
    a staged action waits for the user's tap in the approval panel. When
    `auto_run_calendar` is on, staged CALENDAR actions run immediately (the user
    opted in), so the agent may confirm those naturally. Empty when no add-on offers
    a stageable action. Used in BOTH voice and work mode."""
    avail = [t for t in external_actions.known_types() if external_actions.brain_proposable(t)]
    if not avail:
        return ""
    lines = [
        "\n\nEXTERNAL ACTIONS — you may STAGE these for the user. You do NOT execute "
        "them yourself; the server does. Available right now:",
    ]
    for t in avail:
        lines.append(f"  {t} — {external_actions.label_for(t)}")
    lines.append(
        "To stage one, emit a block EXACTLY like this, each marker on its own line:\n"
        '<<ACTION type="calendar.create" summary="one line">>\n'
        '{ "events": [ { "title": "Dentist", "start": "2026-07-04T09:00:00", "end": "2026-07-04T10:00:00" } ] }\n'
        "<<END_ACTION>>\n"
        'The body is JSON for that action (calendar.update needs {"event_id","changes"}). '
        "Use the user's local date/time; assume the current year if they don't say one. Describe "
        "what you did in your normal reply; the block is extracted automatically, so keep it exact. "
        "Only stage an action the user clearly asked for."
    )
    if "hunter.sync" in avail:
        lines.append(
            "SYNCING THE HUNTER DASHBOARD (the Operating System board / XP / quests): do it with a "
            'hunter.sync block — NOT a shell or PowerShell script (you have neither, and you do not '
            "need them; the server pushes to the user's own Sheet for you). The block body is the "
            "sheet_sync payload — the same JSON shape as 11_dashboard/sheet_sync.json in the brain "
            "(top-level state{}, stats[], daily_quests[], bosses[], weaknesses[], xp_log[]). Build it "
            "from the CURRENT board state plus today's changes; the full field-by-field template and "
            "rules live in 11_dashboard/dashboard_update_workflow.md — read it before you build the "
            'payload. Example: <<ACTION type="hunter.sync" summary="Sync tonight\'s quests">>{ "state": '
            '{ ... }, "daily_quests": [ ... ], "stats": [ ... ] }<<END_ACTION>>. Never tell the user to '
            "run a script to sync — that is the old desktop path and does not apply to you. Every "
            "daily quest you stage carries its planned xp_value — the BOUNTY it pays if completed, "
            "picked from the action class in the brain's XP rules — NEVER 0 (staging a bounty is not "
            "awarding XP; the award is decided at shutdown review against evidence, so the "
            "no-XP-for-intentions rule does not apply to the displayed value)."
        )
    if auto_run_calendar:
        lines.append(
            "CALENDAR AUTO-RUN IS ON: any calendar.create / calendar.update block you emit takes "
            "effect IMMEDIATELY (no approval tap). So confirm it naturally and in past tense, e.g. "
            "'Done — Fortnite's on your calendar, 5 to 6 tonight.' Don't tell the user to approve "
            "anything for calendar. (Other actions still wait for their approval.)"
        )
    if auto_run_hunter:
        lines.append(
            "DASHBOARD AUTO-SYNC IS ON: any hunter.sync block you emit takes effect IMMEDIATELY "
            "(no approval tap) — the board updates the moment you stage it. So sync the dashboard "
            "yourself as the last step of daily planning, shutdown, or 'give me credit', and confirm "
            "it in past tense (e.g. 'Board's synced — tonight's quests are live.'). Do not tell the "
            "user to approve or run anything to sync."
        )
    if not auto_run_calendar and not auto_run_hunter:
        lines.append(
            "These actions wait for the user to approve them in an on-screen panel "
            "you cannot see or operate. Never say a staged action already happened — it is only "
            "PROPOSED until the user approves; you'll be told the outcome on a later turn."
        )
    elif not auto_run_calendar or not auto_run_hunter:
        lines.append(
            "Any action NOT covered by an auto-run above waits for the user to approve it in an "
            "on-screen panel you cannot operate — for those, never claim it already happened; it is "
            "only PROPOSED until approved, and you'll be told the outcome on a later turn."
        )
    return "\n".join(lines)


def _extract_actions(text: str) -> tuple[str, list[dict]]:
    """Pull <<ACTION ...>>{json}<<END_ACTION>> blocks from a work-mode reply and
    park each VALID one as a pending external-action approval. Returns (cleaned
    text, [records]). Only registry actions that are brain_proposable AND available
    are staged; unknown/disabled/non-proposable types, bad JSON, or a non-dict
    payload are dropped from the text and skipped — the agent can neither force nor
    execute anything (execution still requires the user's approval)."""
    records: list[dict] = []

    def _repl(m: re.Match) -> str:
        attrs = dict(_ACTION_ATTR_RE.findall(m.group(1) or ""))
        atype = (attrs.get("type") or "").strip()
        summary = (attrs.get("summary") or "").strip()
        body = (m.group(2) or "").strip()
        if not external_actions.brain_proposable(atype):
            return ""  # unknown / disabled / send|post / not agent-proposable -> drop
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return ""
        if not isinstance(payload, dict):
            return ""
        try:
            rec = approvals.create(
                action_summary=summary or external_actions.label_for(atype),
                action_type=atype,
                risk_level=external_actions.risk_for(atype),
                payload=payload,
            )
            records.append(rec)
        except Exception:
            log.exception("failed to park proposed action %s", atype)
        return ""

    cleaned = _ACTION_BLOCK_RE.sub(_repl, text)
    return cleaned, records


# --- Chat management directives ---------------------------------------------
# Adam owns the conversation it's in, hands-free: it can rename the current chat
# and (on the user's spoken yes) open a fresh one. The server never touches the
# chat list — chats live client-side — so these are pure directives relayed to the
# client, which does the rename/new-chat. Both markers are single-line and always
# stripped from the reply so they never reach the screen or the voice.
_RENAME_CHAT_RE = re.compile(r"<<RENAME_CHAT:\s*(.+?)>>", re.IGNORECASE)
_NEW_CHAT_RE = re.compile(r"<<NEW_CHAT(?::\s*(.+?))?>>", re.IGNORECASE)
# Adam can also switch the mode of the chat it's in on request ("go into operator
# mode", "switch to Claude Code", "back to voice"). The client owns the mode toggle,
# so this is the same relay pattern — and the client applies the same confirmation
# gate for a switch INTO code that the hold gesture uses (code starts a fresh session).
_SET_MODE_RE = re.compile(r"<<SET_MODE:\s*(voice|operator|work|code)\s*>>", re.IGNORECASE)
_MODE_ALIASES = {"voice": "voice", "operator": "operator", "work": "operator", "code": "code"}
_CHAT_TITLE_MAX = 60


def _chat_control_note() -> str:
    """Teach the agent the chat-management directives + the consent rules (open a new
    chat / switch into code mode). Used in EVERY mode (voice/work/code) — organizing the
    conversation is a UI relay, not a file write, so it isn't tied to the safe-write
    posture. The code-mode switch is only offered when allow_code_mode is on."""
    mode_line = (
        "- SWITCH MODE: put <<SET_MODE: operator>> or <<SET_MODE: voice>> in your reply "
        "when the user asks you to change modes (e.g. 'go into operator mode', 'back to "
        "voice'). Confirm what you're switching to before the marker."
    )
    if config.AGENT_ALLOW_CODE_MODE:
        mode_line += (
            " You may also switch INTO Claude Code with <<SET_MODE: code>>, but that one "
            "starts a FRESH conversation (code runs in its own workspace, so this chat's "
            "context won't carry over) — say so in your reply, and the app will show a "
            "final confirm before it switches. Only emit <<SET_MODE: code>> when the user "
            "actually asks for code mode."
        )
    return (
        "\n\nORGANIZING THIS CONVERSATION (hands-free chat control). You can manage the "
        "chat you're in so the user can keep things tidy by voice:\n"
        "- RENAME the current chat: put <<RENAME_CHAT: New Title>> anywhere in your reply "
        "(one line, plain text, <=60 chars). Use it when the user asks you to name/rename "
        "this chat, or offer it when a chat clearly has a topic (e.g. 'Want me to name "
        "this Daily Planning July 3?'). The rename applies immediately; confirm it "
        "naturally.\n"
        "- OPEN A NEW chat: put <<NEW_CHAT>> (or <<NEW_CHAT: Title>> to name it) in your "
        "reply. This one REQUIRES the user's consent: never open a new chat unprompted. "
        "First ask — 'Want me to start a fresh chat?' — and only emit <<NEW_CHAT>> AFTER "
        "the user says yes in the same exchange. The current chat is preserved (your "
        "reply still lands in it); the user is moved into the new blank chat. You cannot "
        "delete chats — don't claim to.\n"
        + mode_line + "\n"
        "The markers are extracted automatically and never shown or spoken, so keep them "
        "exact and don't read them aloud."
    )


def _extract_chat_control(text: str) -> tuple[str, dict | None]:
    """Pull chat-management directives out of a reply. Returns (cleaned_text, control)
    where control is e.g. {"rename": "Daily Planning July 3"}, {"new_chat": {...}},
    and/or {"set_mode": "operator"}, or None if the reply had none. The markers are
    always stripped from the returned text. Last RENAME_CHAT / last SET_MODE wins if the
    model emits more than one."""
    control: dict = {}
    cleaned = text or ""

    renames = [r.strip() for r in _RENAME_CHAT_RE.findall(cleaned) if r.strip()]
    if renames:
        control["rename"] = renames[-1][:_CHAT_TITLE_MAX]
    cleaned = _RENAME_CHAT_RE.sub("", cleaned)

    m = _NEW_CHAT_RE.search(cleaned)
    if m:
        title = (m.group(1) or "").strip()
        control["new_chat"] = {"title": title[:_CHAT_TITLE_MAX]} if title else {}
    cleaned = _NEW_CHAT_RE.sub("", cleaned)

    modes = [m.lower() for m in _SET_MODE_RE.findall(cleaned)]
    if modes:
        control["set_mode"] = _MODE_ALIASES[modes[-1]]  # last SET_MODE wins
    cleaned = _SET_MODE_RE.sub("", cleaned)

    return cleaned.strip(), (control or None)


def _strip_chat_control_markers(text: str) -> str:
    """Remove chat-management markers from any text bound for the screen/TTS."""
    t = _RENAME_CHAT_RE.sub("", text or "")
    t = _NEW_CHAT_RE.sub("", t)
    return _SET_MODE_RE.sub("", t)


async def _auto_run_actions_matching(actions: list[dict], predicate) -> None:
    """Execute staged actions whose action_type satisfies `predicate` immediately
    instead of parking them for a tap (the opt-in auto-run posture). Each record's
    status is updated in place to executed/failed. Still SERVER-executed (the agent
    never executes), still re-checks the add-on is available inside execute(), still
    has no delete path (calendar/hunter have none by construction), every run audited.
    Actions the predicate rejects are left pending for the approval panel."""
    for a in actions:
        if not predicate(str(a.get("action_type", ""))):
            continue
        if a.get("status") not in (None, "pending"):
            continue
        try:
            approvals.approve(a["id"])
            result = await asyncio.to_thread(
                external_actions.execute, a["action_type"], a.get("payload")
            )
            approvals.record_execution(a["id"], ok=True, result=result)
            a["status"] = "executed"
        except external_actions.ActionError as e:
            approvals.record_execution(a["id"], ok=False, error=str(e))
            a["status"] = "failed"
        except Exception:
            log.exception("auto-run action failed for %s", a.get("id"))
            approvals.record_execution(a["id"], ok=False, error="execution failed")
            a["status"] = "failed"


async def _auto_run_calendar_actions(actions: list[dict]) -> None:
    """Auto-run only calendar.* actions (opt-in). Thin wrapper kept for call-site
    clarity; delegates to the shared executor."""
    await _auto_run_actions_matching(actions, lambda t: t.startswith("calendar."))


async def _auto_run_hunter_actions(actions: list[dict]) -> None:
    """Auto-run only hunter.sync (opt-in). Hunter has no delete path by construction,
    so an auto-synced board update is non-destructive and fully audited."""
    await _auto_run_actions_matching(actions, lambda t: t == "hunter.sync")


def _brain_write_note(vault_path: str) -> str:
    """Tell the agent how to update the user's brain. Convention = ABSOLUTE vault
    paths: a non-destructive write whose path is inside the vault auto-applies
    through the server's guardrails; anything else is reviewed (default behavior)."""
    return (
        "\n\nBRAIN UPDATES — the user's Adam brain (memory, daily logs, tasks, profile) lives "
        "in this folder:\n"
        f"  {vault_path}\n"
        "To update the brain, PROPOSE the change with the FULL ABSOLUTE path to the file inside "
        "that folder. Creating a NEW brain file applies automatically; editing an EXISTING brain "
        "file is reviewed by the user first.\n"
        "CRITICAL — a proposed edit REPLACES THE ENTIRE FILE with the content in your block. To "
        "ADD to an existing brain file you MUST first read its current contents and include ALL "
        "of it PLUS your addition; otherwise everything else in that file is ERASED. Preserve "
        "what is already there and append a new section rather than rewriting. When unsure, read "
        "the file first. Never claim a change was saved — just propose it and describe it. "
        "Deleting or renaming a brain file, or writing ANYWHERE outside this folder, also requires "
        "the user's approval — propose those with a plain filename, not the absolute brain path."
    )


def _self_edit_note() -> str:
    """Tell the agent it may change Adam ITSELF when the user has enabled self-edit.
    Same proposal convention as brain writes: propose with the ABSOLUTE path to the app
    file; EVERY self-edit is reviewed by the user and backed up first, so it's undoable."""
    return (
        "\n\nSELF-EDIT — the user has allowed you to change Adam ITSELF (its own program: its "
        "code, appearance, name, wake word, and so on). Adam's own files live in:\n"
        f"  {config.APP_ROOT}\n"
        "ONLY when the user explicitly asks you to change Adam itself, PROPOSE the edit with the "
        "FULL ABSOLUTE path to the app file. EVERY such change is reviewed by the user and a backup "
        "is saved first, so it can be undone. The whole-file rule applies: a proposed edit REPLACES "
        "THE ENTIRE FILE, so READ the file first and include ALL of its current contents PLUS your "
        "change. You cannot touch secrets (.env, settings.json), the data folder, or anything "
        "outside Adam's own folder — those are refused. Make the SMALLEST change that satisfies "
        "the request, and never claim it's done: propose it and let the user approve."
    )


def _self_edit_offer_note() -> str:
    """When self-edit is OFF, give the agent the ONE correct answer for "change
    Adam itself" requests: raise the capability tier to Unrestricted (self-edit is
    no longer a separate toggle — the tier governs it). Without this the agent invents
    a nonexistent 'folder access' pop-up or points at Settings (which can't rename
    Adam). Added in both modes so a voice request gets the right answer too."""
    return (
        "\n\nCHANGING Adam ITSELF — if the user asks you to change Adam's own program (its "
        "NAME / what it goes by, its wake word, its appearance, or its code), you currently CANNOT, "
        "because self-edit is only enabled on the Unrestricted capability tier and you are not on it. "
        "Do not attempt it and do not claim you will. Do NOT mention any 'folder access' or permission "
        "pop-up (there is none), and do NOT point at the Settings/sign-in page (it can't rename Adam). "
        "The ONLY way is to raise the capability tier: tell the user to open the gear menu (the cog/⚙ at "
        "the top of the app), tap '⚡ Capability' until it reads Unrestricted (they'll confirm the "
        "warning), then ask you again — at that point you can make the change (every self-edit is still "
        "reviewed and backed up first, and a broken self-edit auto-rolls-back). Renaming Adam to go by "
        "another name is a self-edit and needs the Unrestricted tier."
    )


def _capability_awareness_note() -> str:
    """Make Adam self-aware of the capability-tier system and how it itself works,
    so it can answer 'what can you do', 'what mode am I on', and 'how do the tiers
    differ' accurately — and never claim a power the active tier forbids. The current
    tier is read live from config, so this always reflects the real posture."""
    tier = config.CAPABILITY_TIER or "custom"
    current = {
        "safe": "Safe", "powerful": "Powerful",
        "unrestricted": "Unrestricted", "custom": "Custom",
    }.get(tier, "Custom")
    return (
        "\n\nCAPABILITY TIERS — you run under a capability tier the user sets in the gear menu "
        f"(⚡ Capability). You are CURRENTLY on: {current}. The tier decides how much you may do on "
        "your own. If the user asks what you can do, what mode you're in, or how the tiers differ, "
        "answer from this (keep it short — one or two lines per tier unless they want detail):\n"
        "  • Safe (default) — you PROPOSE changes; nothing is written until the user approves each "
        "one. You can read their files and stage calendar/add-on actions, but every file or brain "
        "edit waits for their tap. No shell, and you cannot edit your own code.\n"
        "  • Powerful — you AUTO-APPLY non-destructive changes across the user's vault with no tap; "
        "destructive ones (delete/rename) still ask first. Connectors are on. Still no shell, still "
        "no self-edit.\n"
        "  • Unrestricted — full power: you may edit your OWN code, run shell commands, and write "
        "across the vault and the app, auto-approving everything. This is the ONLY tier that enables "
        "self-edit (changing Adam itself), and turning it on requires an explicit confirmation.\n"
        "ALWAYS-ON RAILS (true at EVERY tier, even Unrestricted, and cannot be disabled): every write "
        "is backed up first, everything is written to an audit log, secret files (.env, settings.json, "
        "keys) are never readable or writable, and a self-edit that breaks the app auto-rolls-back. So "
        "full power stays recoverable.\n"
        "HOW YOU WORK — you never write files directly: you PROPOSE changes and the server applies the "
        "approved ones (it is the sole writer, which is what makes the tiers enforceable rather than "
        "just advice). Your 'brain' — memory, daily logs, tasks, profile — is a folder of files you "
        "keep updated. Add-ons (calendar, email, SMS, etc.) are opt-in and off by default. Be accurate "
        "about the CURRENT tier: don't offer to do something it forbids; if the user wants more, tell "
        "them to raise the tier in the gear menu."
    )


def _update_conflicts_note() -> str:
    """Tell the work-mode agent about files an UPDATE held back because the user had
    also customized them in the same spot (the auto-merge couldn't reconcile them).
    With self-edit ON, point it at the three versions and have it propose a merge via
    the self-edit lane; with self-edit OFF, have it ask the user to enable self-edit.
    Empty when there are no pending update conflicts."""
    conflicts = merge.list_conflicts()
    if not conflicts:
        return ""
    if not config.PERM_ALLOW_APP_SELF_EDIT:
        names = ", ".join(c.get("rel", "?") for c in conflicts[:20])
        return (
            "\n\nUPDATE CONFLICTS — a recent update changed file(s) the user had also customized "
            f"in the same place, so their version was kept: {names}. Merging them is a self-edit, "
            "which is only enabled on the Unrestricted capability tier. If the user asks to merge the "
            "update conflicts, tell them to raise the tier (gear menu -> ⚡ Capability -> Unrestricted), "
            "then you can reconcile them."
        )
    lines = [
        "\n\nUPDATE CONFLICTS — a recent update changed file(s) the user had ALSO customized in the "
        "same spot, so the auto-merge kept THEIR version and held the update's copy. When (and only "
        "when) the user asks to 'merge the update conflicts', reconcile each one and PROPOSE the "
        "merged result as a self-edit (full absolute path, whole file). For each, three versions "
        "are available to read:",
    ]
    for c in conflicts[:20]:
        lines.append(
            f"  {c.get('rel')}:\n"
            f"    your current version (edit THIS path): {c.get('target')}\n"
            f"    the update's version:                  {c.get('incoming_review')}\n"
            f"    the common ancestor:                   {c.get('base_review')}"
        )
    lines.append(
        "Read all three, combine the user's intent with the update's change (keep both where they "
        "don't conflict; if they truly clash, ask the user which to favor), and propose ONE edit to "
        "the current-version path. A backup is saved on apply; the conflict clears automatically once "
        "your merge is applied. Never claim it's merged — propose it and let the user approve."
    )
    return "\n".join(lines)


# --- Uploads (attachments: images + docs) -----------------------------------
# Stored off the synced vault so they don't churn cloud sync. The turn references
# the saved path and Claude's Read tool views it (images + PDFs read natively).
UPLOAD_DIR = config.UPLOAD_DIR
UPLOAD_MAX_BYTES = config.UPLOAD_MAX_BYTES
UPLOAD_TTL_SECONDS = config.UPLOAD_TTL_SECONDS
# The allowed-extension set lives with the /upload route in routers/chat.py.

# --- Logging ----------------------------------------------------------------
# A rotating file log so failures persist for diagnosis. The phone PWA has no
# dev console, so client-reported failures (POST /clientlog) are the only way to
# see WHY a turn died on-device — they land here. uvicorn's own loggers are
# attached to the same file so a real server-side error shows up right next to
# the client beacon that reported it.
LOG_FILE = config.LOG_FILE

# The self-edit health probe (self_edit_guard.health_probe) imports this module in
# a throwaway subprocess purely to confirm the app still loads. In that mode it must
# NOT open the live rotating log file — the running server owns it and could be
# mid-rollover — so we log to nowhere and skip the startup lines. Normal (non-probe)
# startup is byte-for-byte unchanged.
_PROBE_MODE = os.environ.get("ADAM_SELF_EDIT_PROBE") == "1"

log = logging.getLogger("adam.voice")
log.setLevel(logging.INFO)

# When the process started — /diagnostics reports uptime from this.
START_TIME = time.time()

# In-memory tail of recent log lines, so /diagnostics can hand the user a
# copy-pasteable support bundle without them ever hunting for the log file.
# Bounded; the rotating file log stays the durable record.
LOG_RING: deque[str] = deque(maxlen=400)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # never raises
        try:
            LOG_RING.append(self.format(record))
        except Exception:
            pass


_LOG_FORMAT = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
_ring_handler = _RingHandler()
_ring_handler.setFormatter(_LOG_FORMAT)
log.addHandler(_ring_handler)

if _PROBE_MODE:
    log.addHandler(logging.NullHandler())
else:
    _log_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _log_handler.setFormatter(_LOG_FORMAT)
    log.addHandler(_log_handler)

    # Capture uvicorn errors but NOT the high-volume access log — otherwise routine
    # poll/heartbeat traffic would churn the WARNING beacons out of the rotation.
    for _n in ("uvicorn", "uvicorn.error"):
        logging.getLogger(_n).addHandler(_log_handler)
        logging.getLogger(_n).addHandler(_ring_handler)

    log.info("%s v%s starting", config.APP_NAME, config.APP_VERSION)
    log.info("config: %s", json.dumps(config.safe_summary()))


# --- Persistent runtime state (Phase 5) -------------------------------------
# Open the job database and run startup recovery BEFORE the app serves traffic.
# Any job a previous process left queued/running can never finish (its worker
# died with that process), so we mark it `interrupted` — never silently complete
# — and audit it. This runs once at import, so recovery is done before the first
# request can hit /poll or /jobs.

def _init_state() -> None:
    info = job_store.init()
    log.info(
        "job store ready: db=%s schema_version=%s%s",
        info.get("db"), info.get("schema_version"),
        " (migrated)" if info.get("migrated") else "",
    )
    try:
        uinfo = usage_store.init()
        log.info("usage store ready: db=%s", uinfo.get("db"))
    except Exception as e:  # noqa: BLE001 — the cost meter is never worth blocking boot
        log.warning("usage store unavailable (cost meter disabled): %s", e)
    if session_store is not None:
        try:
            sinfo = session_store.init()
            log.info("session store ready: db=%s sync_enabled=%s",
                     sinfo.get("db"), config.SESSION_SYNC_ENABLED)
        except Exception as e:  # noqa: BLE001 — sync is optional; never block startup
            log.warning("session store unavailable (chat sync disabled): %s", e)
    else:
        log.warning("session_store module missing - cross-device chat sync disabled")
    recovered = job_store.recover_interrupted()
    if recovered:
        log.warning(
            "startup recovery: marked %d in-flight job(s) interrupted: %s",
            len(recovered), ", ".join(r["job_id"] for r in recovered),
        )
        for r in recovered:
            permissions.record_audit_event({
                "action_type": "job_interrupted_on_startup",
                "job_id": r["job_id"], "target": None,
                "reason": "server restarted while job was queued/running",
                "session_id": r.get("session_id"), "risk": "low",
            })


_init_state()

# Make sure Web Push has a usable, matched keypair (fixes pushes silently no-op'ing
# on a fresh install). Runs once at import, before the first request can hit
# /push/key or a background job calls _send_push.
_ensure_vapid_keypair()


# --- App setup --------------------------------------------------------------

app = FastAPI(title="Adam")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _unhandled_exception(request, exc):
    """Last-resort crash catcher. HTTPException and rate-limit errors have their
    own handlers; anything ELSE reaching here is a bug. Log the full traceback
    (file log + the /diagnostics ring buffer) so a stranger's machine can report
    WHY, and return a clean 500 that leaks no internals to the client."""
    log.exception("UNHANDLED %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.on_event("startup")
async def _start_sms_poller():
    """Background inbound-SMS poller. Fire-and-forget; it self-gates on config
    (idle unless SMS is enabled + fully configured) and never raises. Reuses
    _run_sms_job as the per-message callback, so polled texts take the exact same
    path as webhook texts (brain -> action -> Web Push reply)."""
    asyncio.create_task(twilio_sms.run_poller(_run_sms_job, log=log))


@app.on_event("startup")
async def _start_voicemail_poller():
    """Background voicemail (missed-call recordings) poller. Fire-and-forget; it
    self-gates on config (idle unless voicemail is enabled + fully configured) and
    never raises. Pulls new recordings from Twilio, transcribes, and pushes the
    message to the phone — same private outbound-poll posture as the SMS poller."""
    asyncio.create_task(twilio_voicemail.run_poller(_run_voicemail_job, log=log))


@app.on_event("startup")
async def _seed_update_baseline():
    """Phase 2: ensure a pristine update baseline exists. The smart updater 3-ways
    against the exact bytes this install last shipped (data/baseline/); on a fresh
    install that ancestor doesn't exist yet, so seed it from the current program
    files on first launch (before any self-edit could have diverged them). Never
    raises into startup."""
    try:
        import update_engine
        if not (update_engine.BASELINE_DIR.is_dir() and any(update_engine.BASELINE_DIR.rglob("*"))):
            n = update_engine.snapshot_baseline(config.ROOT)
            log.info("update baseline seeded from current install (%d files)", n)
    except Exception as e:  # noqa: BLE001 - baseline seeding must never block startup
        log.warning("update baseline seed skipped: %s", e)


# Request models now live in models.py; require_token in security.py. Both
# are re-exported above, so server.<name> keeps working for tests/back-compat.


# --- Claude subprocess ------------------------------------------------------

_SPEAK_MARK = "<<SPEAK>>"
_SPEAK_TAG = re.compile(r"<<SPEAK>>(.*?)<<SPEAK>>", re.DOTALL)


def _strip_markers(text: str) -> str:
    """Remove any literal <<SPEAK>> markers (incl. stray/unpaired ones) so they
    never reach the screen or TTS — where they'd be read aloud as the word 'speak'."""
    return (text or "").replace(_SPEAK_MARK, "").strip()


def _clean_spoken_blocks(text: str) -> str:
    """Strip every machine block from text the TTS will read: <<ACTION>>…<<END_ACTION>>,
    <<PROPOSE>>…<<END_PROPOSE>>, and <<SPEAK>> markers. Voice mode speaks the WHOLE reply,
    so an un-stripped action/propose block would otherwise be read aloud as raw JSON
    ending in 'end action'. No-op in work mode (the spoken text is a separate summary)."""
    t = _ACTION_BLOCK_RE.sub("", text or "")
    t = proposed_changes._PROPOSE_RE.sub("", t)
    t = _strip_chat_control_markers(t)
    return _strip_markers(t)


def _first_sentence(text: str) -> str:
    """Best-effort first sentence, for spoken fallbacks when no <<SPEAK>> tag."""
    t = (text or "").strip()
    if not t:
        return ""
    m = re.search(r"(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t[:200]).strip()


def _extract_spoken(result: str, mode: str) -> tuple[str, str]:
    """Return (display_text, spoken_text), with all <<SPEAK>> markers removed.

    Work mode: pull the <<SPEAK>>...<<SPEAK>> summary for TTS; show the rest on screen.
    Voice mode: normally there's no tag. But a mixed-mode session resumes earlier
    work-mode turns, so the model can MIMIC the marker even in voice mode. Handle it
    gracefully either way — honor a leaked summary, and never let a raw marker survive
    to the display or the speaker."""
    text = result or ""
    m = _SPEAK_TAG.search(text)
    if m:
        spoken = _strip_markers(m.group(1))
        display = _strip_markers(_SPEAK_TAG.sub("", text)) or spoken or text
        if mode in ("work", "code"):
            return display, (spoken or _first_sentence(display))
        return display, (spoken or display)        # voice: leaked tag, still clean
    display = _strip_markers(text)                  # kill any lone/unpaired marker
    if mode in ("work", "code"):
        return display, (_first_sentence(display) or "Done, sir. Details are on screen.")
    return display, display


def _attachment_block(attachments: list[str] | None) -> str:
    """Build a prompt prefix listing attached files for the Read tool. Only paths
    that actually live under UPLOAD_DIR are honored — a client can't smuggle an
    arbitrary filesystem path in through the attachments field."""
    if not attachments:
        return ""
    safe = []
    for p in attachments:
        try:
            rp = Path(p).resolve()
            if rp.is_file() and UPLOAD_DIR.resolve() in rp.parents:
                safe.append(str(rp))
        except Exception:
            continue
    if not safe:
        return ""
    listing = "\n".join(f"- {p}" for p in safe)
    return (
        "The user attached the following file(s). Use the Read tool to view them "
        "as part of answering:\n" + listing + "\n\n"
    )


# --- Live-turn registry (streaming activity + the stop button) ---------------
# In-memory only, keyed by job_id: the running subprocess (so /jobs/{id}/stop
# can kill it), a rolling tool-activity feed (merged into /poll while running),
# and the jobs the user stopped (so a dead process reads as "stopped", not a
# crash). Deliberately ephemeral — progress is a live view, never history; the
# durable record stays in job_store/audit.
RUNNING_PROCS: dict[str, asyncio.subprocess.Process] = {}
JOB_PROGRESS: dict[str, list[str]] = {}
CANCELLED_JOBS: set[str] = set()
PROGRESS_MAX_LINES = 200                # rolling cap per turn
STREAM_LINE_LIMIT = 16 * 1024 * 1024    # stream-json lines embed whole tool results


class TurnStopped(Exception):
    """The user stopped this turn via POST /jobs/{id}/stop."""


def _tool_activity_line(name: str, tool_input: dict) -> str:
    """One short, human-readable line per tool call for the live activity feed —
    a filename or truncated command, enough to see WHAT the agent is doing."""
    d = ""
    try:
        if name == "Bash":
            d = (tool_input.get("command") or "").strip().splitlines()[0][:80]
        elif name in ("Read", "Write", "Edit", "NotebookEdit"):
            d = os.path.basename(str(tool_input.get("file_path")
                                     or tool_input.get("notebook_path") or ""))
        elif name in ("Glob", "Grep"):
            d = str(tool_input.get("pattern") or "")[:60]
        elif name == "WebFetch":
            d = str(tool_input.get("url") or "")[:60]
        elif name == "WebSearch":
            d = str(tool_input.get("query") or "")[:60]
        elif name == "Task":
            d = str(tool_input.get("description") or "")[:60]
    except Exception:
        d = ""
    return f"{name}: {d}" if d else str(name)


def _note_progress(job_id: str | None, line: str) -> None:
    if not job_id or not line:
        return
    buf = JOB_PROGRESS.setdefault(job_id, [])
    buf.append(line)
    if len(buf) > PROGRESS_MAX_LINES:
        del buf[: len(buf) - PROGRESS_MAX_LINES]


async def _kill_proc_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill a Claude turn AND its children (a Bash tool may have spawned work).
    Windows TerminateProcess doesn't cascade, so prefer taskkill /T there."""
    try:
        if os.name == "nt":
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )
    except Exception:
        pass
    try:
        if proc.returncode is None:
            proc.kill()
    except Exception:
        pass


async def _read_stream_result(proc, job_id: str | None, timeout: int) -> dict:
    """Consume a --output-format stream-json turn line by line: tool_use events
    feed the live activity buffer; the terminal 'result' event is returned in
    the same shape --output-format json would have produced. Enforces the turn
    deadline (raises asyncio.TimeoutError for the caller's kill path) and
    translates a user stop into TurnStopped."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    stderr_task = asyncio.create_task(proc.stderr.read())
    result_event: dict | None = None
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            if not line:
                break
            try:
                ev = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue   # partial/noise line — never kill the turn over telemetry
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            if etype == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        _note_progress(job_id, _tool_activity_line(
                            str(block.get("name") or "tool"), block.get("input") or {}))
            elif etype == "result":
                result_event = ev
    except asyncio.TimeoutError:
        stderr_task.cancel()
        raise
    await proc.wait()
    try:
        stderr = (await stderr_task).decode("utf-8", errors="replace").strip()
    except Exception:
        stderr = ""
    if job_id and job_id in CANCELLED_JOBS:
        raise TurnStopped()
    if proc.returncode != 0:
        log.error("Claude exited %s: %s", proc.returncode, stderr[:500])
        if _is_claude_auth_failure(stderr):
            raise HTTPException(status_code=502,
                                detail=f"{AUTH_REQUIRED_SENTINEL} {AUTH_REQUIRED_MESSAGE}")
        raise HTTPException(status_code=502, detail=f"Claude failed: {stderr[:500]}")
    if not isinstance(result_event, dict):
        log.error("Claude stream ended without a result event")
        raise HTTPException(status_code=502, detail="Claude returned no result")
    return result_event


async def run_claude(
    message: str, session_id: str | None, timeout: int = CLAUDE_TIMEOUT_SECONDS,
    mode: str = "voice", attachments: list[str] | None = None,
    job_id: str | None = None,
) -> dict:
    """Spawn claude.exe in the vault and return parsed JSON output.

    `mode` picks the system prompt (terse voice vs. full agent), which folders it
    can reach, and whether the whole reply or just a <<SPEAK>> summary is spoken.
    `attachments` are uploaded file paths injected for the Read tool to view."""
    mode = _normalize_mode(mode)
    if mode == "code" and not config.AGENT_ALLOW_CODE_MODE:
        # Never degrade silently into a different capability level — the user must
        # know they are NOT in Claude Code mode. Flip agent_safety.allow_code_mode
        # in settings.json to enable it.
        raise HTTPException(
            status_code=403,
            detail="Claude Code mode is not enabled on this install "
                   "(agent_safety.allow_code_mode is off).",
        )

    # Budget governor (pay-as-you-go installs only). Checked BEFORE spawning so a
    # capped install refuses cleanly instead of billing one more turn. 402 keeps
    # it distinct from auth (401/403) so the UI can show the raise-budget hint.
    if config.AUTH_MODE == "api_key" and config.API_BUDGET_MONTHLY_USD > 0:
        try:
            _spent = usage_store.month_total()
        except Exception:  # noqa: BLE001 — a broken meter must not brick the assistant
            _spent = 0.0
        if _spent >= config.API_BUDGET_MONTHLY_USD:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly AI budget reached (${_spent:.2f} of "
                    f"${config.API_BUDGET_MONTHLY_USD:.2f}). Raise it under "
                    "Settings → AI plan to keep going."
                ),
            )

    prompt = (CODE_SYSTEM_PROMPT if mode == "code"
              else WORK_SYSTEM_PROMPT if mode == "work" else VOICE_SYSTEM_PROMPT)
    message = _attachment_block(attachments) + message

    # --- Agent-safety spawn policy (Phase 3) --------------------------------
    # In a safe mode (draft_only / controlled_write) the file-mutation and shell
    # tools are denied at the CLI and Claude runs in a throwaway workspace, NOT
    # the user's files — so even a stray write can't touch the vault. The vault
    # (and work dirs) are added as READ context. In legacy_direct we keep the old
    # behavior: cwd=vault, full tools. This is the true enforcement boundary, not
    # the prompt — verified by scripts/agent-write-probe.ps1.
    # A 'code' chat (explicitly escalated + flag-gated above) opts OUT of the
    # restriction for that chat only — raw Claude Code, same as legacy_direct.
    restrict = config.AGENT_RESTRICT_TOOLS and mode != "code"
    if restrict:
        # The agent's safe-write capabilities apply in BOTH voice and work mode — the
        # user talks to ONE Adam (their daily voice driver with a bundled brain) and
        # shouldn't have to switch modes to save a memory, add a calendar event, or
        # rename the assistant. (Work vs voice differ in verbosity + reachable dirs,
        # set elsewhere — not in which write lanes exist.) Without this, a brain/self-
        # edit PROPOSE block emitted in voice mode would leak into the chat unparsed
        # and never save.
        prompt = prompt + DRAFT_MODE_NOTE
        if config.BRAIN_WRITE_ENABLED and VAULT_PATH:
            prompt = prompt + _brain_write_note(VAULT_PATH)
        prompt = prompt + _action_proposal_note(
            auto_run_calendar=_get_auto_run_calendar(),
            auto_run_hunter=_get_auto_run_hunter(),
        )
        if config.PERM_ALLOW_APP_SELF_EDIT:
            prompt = prompt + _self_edit_note()
        else:
            prompt = prompt + _self_edit_offer_note()
        # Surface any update conflicts the auto-merge held back (cheap when none).
        prompt = prompt + _update_conflicts_note()
    # Default-on: give the agent awareness of proposals the user resolved since the
    # last turn so it has continuity on what you're working on (and never re-pitches
    # a denied change). Empty/cheap when nothing's been resolved.
    prompt = prompt + _proposal_outcome_note()
    # Live add-on awareness (both modes): use an enabled add-on, redirect a disabled one.
    prompt = prompt + _addon_awareness_note()
    # Hands-free chat management (every mode): rename this chat / open a new one on consent.
    prompt = prompt + _chat_control_note()
    # Self-awareness of the capability tiers + how Adam itself works (both modes), so
    # it can answer "what can you do / what mode am I on" and never overclaim its powers.
    # Skipped in a code chat — it describes the restricted posture, which is exactly
    # what a code chat is NOT running under; CODE_SYSTEM_PROMPT is the truth there.
    if mode != "code":
        prompt = prompt + _capability_awareness_note()

    # config.VOICE_MODEL (not the module-load copy): the AI-plan endpoint changes
    # the model live, and the next turn must pick it up without a restart.
    cmd = [CLAUDE_EXE, "-p", "--model", config.VOICE_MODEL]
    if mode == "code":
        # stream-json emits per-tool events while the turn runs — that's the live
        # activity feed on the phone. (--verbose is required with -p+stream-json.)
        # The terminal 'result' event carries the same payload json mode returns.
        cmd += ["--output-format", "stream-json", "--verbose"]
    else:
        cmd += ["--output-format", "json"]
    if session_id:
        cmd += ["--resume", session_id]

    if restrict:
        run_cwd = str(config.AGENT_WORKSPACE)
        # Vault is read context (cwd is the sandbox now); work mode adds the extra
        # dirs too. add-dir grants read; writes are blocked by --disallowedTools.
        read_dirs = [VAULT_PATH] + (WORK_EXTRA_DIRS if mode == "work" else [])
        # Self-edit: let the agent READ its own code so it can propose changes to it
        # (works in voice and work mode — renaming Adam is a common voice request).
        if config.PERM_ALLOW_APP_SELF_EDIT:
            read_dirs = read_dirs + [config.APP_ROOT]
            # Plus the held update-conflict copies, so it can read the update's
            # version + the ancestor to reconcile them. Only when conflicts pend.
            if merge.list_conflicts():
                read_dirs = read_dirs + [str(merge._review_dir())]
        for d in read_dirs:
            cmd += ["--add-dir", d]
        if config.AGENT_DENIED_TOOLS:
            cmd += ["--disallowedTools", *config.AGENT_DENIED_TOOLS]
    else:
        # legacy_direct — old, unrestricted behavior (personal use only) — or an
        # escalated 'code' chat (flag-gated above), which runs the same raw spawn.
        run_cwd = VAULT_PATH
        if mode in ("work", "code"):
            for d in WORK_EXTRA_DIRS:
                cmd += ["--add-dir", d]
        if mode == "code":
            # Extra reach the owner granted code chats (repos they code on, etc.).
            for d in config.AGENT_CODE_MODE_DIRS:
                cmd += ["--add-dir", d]
            # Full Claude Code, headless: without this, tools that need an
            # interactive permission grant (Bash, edits outside cwd, …) are
            # silently denied — a spawned -p run has no terminal to prompt into.
            # The user opted into exactly this power: allow_code_mode in settings
            # AND a deliberate long-press escalation on this specific chat.
            cmd += ["--permission-mode", "bypassPermissions"]
            # Every code-mode turn is audited (no message content — structural
            # facts only), so the escalated lane stays visible in /audit.
            permissions.record_audit_event({
                "action_type": "code_mode_turn",
                "target": VAULT_PATH,
                "allowed": True, "requires_approval": False, "approved": True,
                "risk": "high",
                "reason": "chat escalated to raw Claude Code (allow_code_mode on)",
                "session_id": session_id,
            })

    # --append-system-prompt must come last (before the positional message) so the
    # variadic --disallowedTools / --add-dir lists terminate cleanly.
    cmd += ["--append-system-prompt", prompt, message]

    # Auth isolation: the CLI bills whichever credential it finds, and an env
    # ANTHROPIC_API_KEY silently outranks a subscription login. So the child env
    # NEVER inherits a key implicitly — it's stripped here and re-added only when
    # this install explicitly chose pay-as-you-go. (config already popped the
    # .env key out of os.environ at load; this also covers keys set globally.)
    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)
    if config.AUTH_MODE == "api_key" and config.ANTHROPIC_API_KEY:
        child_env["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=run_cwd,
        env=child_env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_LINE_LIMIT,
    )
    # Register the live turn so /jobs/{id}/stop can kill it and /poll can show
    # its activity. Any mode — the stop button works for voice/work turns too.
    if job_id:
        RUNNING_PROCS[job_id] = proc
        JOB_PROGRESS.pop(job_id, None)

    try:
        if mode == "code":
            try:
                data = await _read_stream_result(proc, job_id, timeout)
            except asyncio.TimeoutError:
                await _kill_proc_tree(proc)
                await proc.wait()
                log.error("Claude turn timed out after %ss (mode=%s)", timeout, mode)
                raise HTTPException(status_code=504, detail="Claude timed out")
        else:
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                log.error("Claude turn timed out after %ss (mode=%s)", timeout, mode)
                raise HTTPException(status_code=504, detail="Claude timed out")

            if job_id and job_id in CANCELLED_JOBS:
                raise TurnStopped()   # the user stopped this turn mid-flight

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                log.error("Claude exited %s: %s", proc.returncode, err[:500])
                if _is_claude_auth_failure(err):
                    # Surfaced to the user as clear sign-in guidance, not "connection error".
                    raise HTTPException(status_code=502,
                                        detail=f"{AUTH_REQUIRED_SENTINEL} {AUTH_REQUIRED_MESSAGE}")
                raise HTTPException(status_code=502, detail=f"Claude failed: {err[:500]}")

            raw = stdout.decode("utf-8", errors="replace").strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.error("Claude returned non-JSON output: %s", raw[:500])
                raise HTTPException(status_code=502, detail="Claude returned non-JSON output")
    finally:
        if job_id:
            RUNNING_PROCS.pop(job_id, None)
            JOB_PROGRESS.pop(job_id, None)
            CANCELLED_JOBS.discard(job_id)

    # Feed the cost meter. The CLI reports total_cost_usd in every result
    # (subscription runs too — there it reads as what the plan covered). The
    # budget gate above is what turns these rows into a hard ceiling.
    _cost = data.get("total_cost_usd")
    if isinstance(_cost, (int, float)) and _cost > 0:
        try:
            usage_store.record_turn(float(_cost))
        except Exception:  # noqa: BLE001 — never fail a finished turn over bookkeeping
            log.warning("usage_store.record_turn failed", exc_info=True)

    display, spoken = _extract_spoken(data.get("result", ""), mode)
    sid = data.get("session_id", session_id or "")

    # In a safe mode, pull any <<PROPOSE>> blocks out of the reply and turn them into
    # pending proposed changes (or auto-apply brain self-writes). Runs in BOTH modes:
    # a block emitted in voice mode must never leak into the chat unparsed — it gets
    # stripped + parked/applied just like in work mode. The cleaned reply is what
    # reaches the screen/speaker.
    proposed: list[dict] = []
    if config.AGENT_RESTRICT_TOOLS:
        display, proposed = proposed_changes.extract_from_reply(
            display, session_id=sid, job_id=None
        )
        if proposed:
            spoken = _clean_spoken_blocks(spoken)  # never read a PROPOSE block aloud
            # Auto-apply (opt-in, off by default): approve + apply each proposed change
            # immediately so the user isn't asked to tap. This ONLY skips the manual
            # approval — the server still enforces every guardrail in apply(): the write
            # allow-list, protected-path refusal (.env/keys/settings.json), the
            # staleness/conflict check, backup-before-overwrite, and the audit log. A
            # refused/conflicted change comes back unapplied with its status, never
            # silently forced.
            global_auto = _get_auto_apply()
            for p in proposed:
                brain_self = proposed_changes.is_brain_self_write(p)
                # Capability-tier / permission-aware auto-apply: a change auto-applies
                # when the active policy doesn't require approval for IT specifically.
                # Non-destructive writes auto-apply unless require_approval_for_writes is
                # set; destructive writes auto-apply only when require_approval_for_destructive
                # is off. This is what makes the Powerful tier (auto non-destructive, confirm
                # destructive) and Unrestricted tier (auto everything) real — while Safe /
                # custom default keep both flags True, so policy_auto stays False and behavior
                # is unchanged. apply() STILL enforces every guardrail regardless.
                policy_auto = not permissions.requires_approval(
                    p.get("target_path", ""), is_write=True,
                    destructive=bool(p.get("destructive")),
                )
                # Auto-apply when: the global pref is on, OR it's a brain self-write
                # (in-vault, non-destructive), OR the policy doesn't require approval for it.
                # Everything else stays pending for the user to approve.
                if not (global_auto or brain_self or policy_auto):
                    continue
                try:
                    proposed_changes.approve(p["id"])
                    # apply() may boot a blocking self-edit health probe (subprocess,
                    # up to 40s); run it off the event loop so the server stays
                    # responsive during a self-edit.
                    rec, err = await asyncio.to_thread(proposed_changes.apply, p["id"])
                    if rec is not None:
                        p["status"] = rec.get("status", p.get("status"))
                        if err:
                            p["apply_error"] = err
                        if rec.get("status") == "applied":
                            merge.resolve_for_target(p.get("target_path"))
                    permissions.record_audit_event({
                        "action_type": "proposed_change_auto_applied",
                        "proposed_change_id": p["id"], "target": p.get("target_path"),
                        "final_status": p.get("status"), "error": err,
                        "via": "brain_self_write" if (brain_self and not global_auto) else "auto_apply",
                    })
                except Exception:
                    log.exception("auto-apply failed for %s", p.get("id"))

    # External actions the agent staged (calendar event, email draft, …). Parsed
    # out of the work-mode reply and parked as pending approvals; NOTHING runs
    # until the user approves (the server executes then, never the agent).
    actions: list[dict] = []
    if config.AGENT_RESTRICT_TOOLS:
        display, actions = _extract_actions(display)
        if actions:
            spoken = _clean_spoken_blocks(spoken)  # never read an ACTION block aloud
            # Auto-run calendar + hunter actions (opt-in, off by default): the
            # phone-friendly, hands-free posture the owner opted into. Each has its
            # own toggle; whichever is off keeps waiting for a tap, as do all other
            # action types (email draft, etc.).
            if _get_auto_run_calendar():
                await _auto_run_calendar_actions(actions)
            if _get_auto_run_hunter():
                await _auto_run_hunter_actions(actions)

    # Chat management (EVERY mode, incl. code): rename the current chat, or open a
    # fresh one on the user's spoken yes. Not gated on restrict — it's a UI relay to
    # the client (which owns the chat list), touches no files, and has no delete path.
    display, chat_control = _extract_chat_control(display)
    if chat_control:
        spoken = _strip_chat_control_markers(spoken)  # never read a directive aloud

    return {
        "result": display,
        "spoken": spoken,
        "mode": mode,
        "session_id": sid,
        "chat_control": chat_control,
        "proposed_changes": [
            {"id": p["id"], "action": p["action"], "target_path": p["target_path"],
             "risk_level": p["risk_level"], "summary": p["summary"],
             "status": p.get("status", "pending")}
            for p in proposed
        ],
        "proposed_actions": [
            {"id": r["id"], "action_type": r["action_type"], "risk_level": r["risk_level"],
             "summary": r["action_summary"], "status": r.get("status", "pending")}
            for r in actions
        ],
    }


# --- Fire-and-poll jobs -----------------------------------------------------
# Heavy commands run longer than the edge HTTP timeout. Instead of one long
# blocking call, /ask_async kicks off the work in the background and /poll/{id}
# returns fast until it's done. Phase 5: jobs persist to SQLite (job_store), so a
# restart/crash/sleep no longer vanishes an in-flight or finished job.


async def _run_job(
    job_id: str, message: str, session_id: str | None, mode: str = "voice",
    attachments: list[str] | None = None,
) -> None:
    """Background runner — writes its outcome into the persistent job store."""
    try:
        # Code chats get the long leash: real coding turns (edit → test → fix)
        # routinely outlive the normal async cap; streaming + stop keep them
        # supervisable, so the cap is a wedge backstop, not the UX.
        timeout = (config.CODE_CLAUDE_TIMEOUT_SECONDS if mode == "code"
                   else ASYNC_CLAUDE_TIMEOUT_SECONDS)
        out = await run_claude(
            message, session_id, timeout=timeout, mode=mode,
            attachments=attachments, job_id=job_id,
        )
        # One canonical timestamp per finished result, shared by the poll
        # response, the stored last-result, and the push payload — so the phone
        # can dedupe (foreground poll vs. notification-tap replay) by ts alone.
        ts = int(time.time() * 1000)
        # Only push a banner if the app isn't on-screen. A recent /push/seen
        # heartbeat means the user is looking at the app and will hear the reply
        # spoken in-app — no banner needed.
        foreground = (time.time() - _last_seen) <= FOREGROUND_SEEN_WINDOW
        notification_status = "suppressed_foreground" if foreground else "pushed"
        job_store.complete_job(
            job_id, result=out["result"], spoken=out["spoken"], mode=out["mode"],
            session_id=out["session_id"], ts=ts,
            proposed_changes=out.get("proposed_changes", []),
            chat_control=out.get("chat_control"),
            notification_status=notification_status,
        )
        # Always stash the result so a notification tap (or reopen) can replay it.
        # Carry the user's prompt too, so a device that surfaces this reply from
        # /push/last (before the full transcript syncs) shows input → output.
        _store_last_result(
            out["result"], out["session_id"], ts, spoken=out["spoken"],
            prompt=message,
        )
        if not foreground:
            await asyncio.to_thread(
                _send_push, out["result"], out["session_id"], ts, out["spoken"]
            )
    except TurnStopped:
        # Not a failure: the user hit stop. The chat keeps its resume id — the
        # next utterance continues from the last COMPLETED turn.
        log.info("job %s stopped by user", job_id)
        job_store.cancel_job(job_id, "Stopped by user.")
    except HTTPException as e:
        log.error("job %s failed: %s", job_id, e.detail)
        job_store.fail_job(job_id, str(e.detail))
    except Exception as e:  # noqa: BLE001 — never let a job die silently
        log.exception("job %s crashed", job_id)
        job_store.fail_job(job_id, str(e)[:500])


# --- Twilio inbound SMS -----------------------------------------------------

def _valid_twilio_sig(url: str, params: dict, signature: str) -> bool:
    """Verify Twilio's X-Twilio-Signature: HMAC-SHA1 over the public webhook URL
       plus every POST param (sorted by key, concatenated key+value), base64'd.
       The URL must match what Twilio called — behind the tunnel the request sees
       localhost, so we sign against the configured public URL, not request.url."""
    if not TWILIO_AUTH_TOKEN or not signature:
        return False
    s = url
    for k in sorted(params.keys()):
        s += k + params[k]
    mac = hmac.new(TWILIO_AUTH_TOKEN.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)


SMS_WRAP = (
    "This is an inbound text message from the user, sent hands-free (likely via "
    "Meta glasses or phone while busy). Comprehend the intent and ACT on it inside "
    "the vault: route it to the right place — phone capture, a task, a memory "
    "candidate, or stage a calendar item — or answer if it's a question. Take the "
    "action, don't just acknowledge. Then reply in ONE short sentence confirming "
    'exactly what you did. Message: "{body}"'
)


async def _run_sms_job(body: str) -> None:
    """Process an inbound SMS through Claude and push the reply back. Best-effort —
       any failure still pushes a short error so the user isn't left guessing."""
    ts = int(time.time() * 1000)
    try:
        out = await run_claude(
            SMS_WRAP.format(body=body), None, timeout=ASYNC_CLAUDE_TIMEOUT_SECONDS
        )
        _store_last_result(out["result"], out["session_id"], ts)
        await asyncio.to_thread(_send_push, out["result"], out["session_id"], ts)
    except HTTPException as e:
        log.error("SMS job failed: %s", e.detail)
        await asyncio.to_thread(_send_push, f"SMS capture failed: {e.detail}", "", ts)
    except Exception as e:  # noqa: BLE001 — never die silently
        log.exception("SMS job crashed")
        await asyncio.to_thread(_send_push, f"SMS capture error: {str(e)[:200]}", "", ts)


# --- Voicemail (missed-call message taker) ----------------------------------

async def _run_voicemail_job(vm: dict) -> None:
    """Turn one polled voicemail into a stored record + a phone push. The transcript
    IS the message — no brain call needed (and none happens, so there's no latency
    and nothing the caller said is ever acted on as a command). Best-effort: any
    failure still pushes a short notice so the user isn't left guessing."""
    ts = int(time.time() * 1000)
    try:
        name = voicemail_contacts.lookup(vm.get("from_number", ""))
        record = voicemail_store.Voicemail(
            recording_sid=vm.get("recording_sid", ""),
            call_sid=vm.get("call_sid", ""),
            from_number=vm.get("from_number", ""),
            received_at=vm.get("date_created", ""),
            duration_seconds=int(vm.get("duration", 0) or 0),
            caller_name=name,
            transcript=(vm.get("transcript", "") or ""),
            transcribed=bool(vm.get("transcribed", False)),
        )
        voicemail_store.save(record)
        banner = record.summary_line()
        _store_last_result(banner, "", ts)
        await asyncio.to_thread(_send_push, banner, "", ts)
    except Exception as e:  # noqa: BLE001 — never die silently
        log.exception("voicemail job crashed")
        await asyncio.to_thread(
            _send_push, f"New voicemail (couldn't process: {str(e)[:120]})", "", ts
        )


# --- Web Push helpers -------------------------------------------------------

def _load_subs() -> list[dict]:
    """Read stored push subscriptions (one list, single user)."""
    try:
        return json.loads(PUSH_SUB_FILE.read_text("utf-8"))
    except Exception:
        return []


def _save_subs(subs: list[dict]) -> None:
    PUSH_SUB_FILE.write_text(json.dumps(subs), encoding="utf-8")


def _store_last_result(
    result: str, session_id: str, ts: int, spoken: str | None = None,
    prompt: str = "",
) -> None:
    """Persist the latest finished reply so a notification tap can replay it.
    `spoken` is what gets read aloud (the summary in work mode); defaults to the
    full result for voice mode. `prompt` is the user's input that produced this
    reply — carried so a device surfacing the reply from /push/last can show the
    question above the answer instead of a reply with nothing before it. Empty for
    server-initiated replies (SMS/voicemail) that have no in-app input bubble."""
    try:
        LAST_RESULT_FILE.write_text(
            json.dumps({
                "result": result, "session_id": session_id, "ts": ts,
                "spoken": spoken if spoken is not None else result,
                "prompt": prompt or "",
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def _send_push(
    result: str, session_id: str, ts: int, spoken: str | None = None
) -> None:
    """Push the finished result to every stored subscription. Best-effort.

    Runs off the event loop (blocking HTTP to the push service). The banner shows
    the spoken summary (short, readable); the full reply is replayed via
    /push/last on tap. The service worker hands spoken + session_id to the page so
    it can speak and continue the conversation. Dead subscriptions (404/410) pruned.
    """
    if webpush is None or not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_PEM.exists():
        return
    subs = _load_subs()
    if not subs:
        return
    banner = (spoken if spoken is not None else result) or ""
    banner = banner.strip() or "Done, sir."
    payload = json.dumps({
        "title": "Adam",
        "body": banner[:1500],        # banner text; full result replayed on open
        "spoken": banner[:1500],      # what the page speaks on tap
        "session_id": session_id,
        "ts": ts,
    })
    # pywebpush wants a PEM *file path* here — handing it the PEM contents makes
    # it try to parse the string as a raw base64 key and fail to deserialize.
    pem_path = str(VAPID_PRIVATE_PEM)
    alive: list[dict] = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=pem_path,
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=10,
            )
            alive.append(sub)
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                continue  # subscription gone — drop it
            alive.append(sub)  # transient — keep it
        except Exception:
            alive.append(sub)  # network hiccup — keep it
    if len(alive) != len(subs):
        _save_subs(alive)


# --- Voice-install status helper ---------------------------------------------
# Kept here (not in routers/voice_push.py): it is app-level state the tests
# exercise directly as server._voice_pkg_installed().
def _voice_pkg_installed() -> bool:
    """True once the real (Kokoro) voice model has been downloaded via INSTALL-VOICE.
    Pure filesystem check — no network, no secrets. Drives the in-app nudge so a
    friend still on the browser-fallback robotic voice learns the upgrade exists."""
    try:
        tts_dir = config.ROOT / "scripts" / "tts_server"
        model = tts_dir / "kokoro-v1.0.onnx"
        voices = tts_dir / "voices-v1.0.bin"
        return (model.is_file() and model.stat().st_size > 200 * 1024 * 1024
                and voices.is_file() and voices.stat().st_size > 10 * 1024 * 1024)
    except Exception:
        return False


# --- Routes ------------------------------------------------------------------
# All HTTP routes live in routers/ (split out of this file's old monolith).
# They are imported HERE, at the bottom, so everything they reference through
# the `server` module (run_claude, the live-turn registry, push helpers, ...)
# is already defined. Each router reads server.<name> at request time, so a
# test that monkeypatches an attribute on this module patches every route.
from routers import chat, integrations, reviews, system, voice_push  # noqa: E402

app.include_router(system.router)
app.include_router(chat.router)
app.include_router(reviews.router)
app.include_router(voice_push.router)
app.include_router(integrations.router)


if __name__ == "__main__":
    # On Windows, asyncio subprocess support requires the Proactor event loop.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)
