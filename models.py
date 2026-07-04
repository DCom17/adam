"""Request models for the Jarvis Voice Local API.

Split out of server.py so the router modules can import them without pulling
in the whole app. server.py re-exports every name for back-compat."""

from __future__ import annotations

from pydantic import BaseModel


class AskRequest(BaseModel):
    message: str
    session_id: str | None = None
    mode: str | None = None  # "voice" (default) | "work" | "code" (needs allow_code_mode)
    attachments: list[str] | None = None  # server-side upload paths from /upload


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None


class PushSubscribe(BaseModel):
    subscription: dict


class SessionRecord(BaseModel):
    """One chat as the client mirrors it for cross-device sync. `tx` is the chat's
    transcript HTML; `updated` is the client-stamped ms timestamp the merge uses."""
    key: str
    title: str = ""
    mode: str = "voice"
    sid: str | None = None
    last_ts: int = 0
    last_spoken: str = ""
    tx: str = ""
    deleted: bool = False
    created: int = 0
    used: int = 0
    updated: int = 0
    seq: int = 0  # server-assigned delivery cursor (set on pull; ignored on push)


class SessionSyncPush(BaseModel):
    sessions: list[SessionRecord] = []


class ApprovalCreate(BaseModel):
    """Park a pending controlled-action approval (see POST /approvals)."""
    action_summary: str
    action_type: str = "write"          # "write" | "command" | "destructive" | ...
    risk_level: str = "medium"          # "low" | "medium" | "high"
    target: str | None = None           # path or command the action concerns
    session_id: str | None = None
    job_id: str | None = None


class ActionProposeRequest(BaseModel):
    """Stage an EXTERNAL ACTION (calendar event, email draft, …) for approval. The
    server parks it as a pending approval carrying the payload; nothing executes
    until the user approves. action_type must be a known external_actions type and
    its add-on must be enabled. payload holds content only — never a secret."""
    action_type: str
    payload: dict
    summary: str | None = None
    target: str | None = None


class ProposedChangeCreate(BaseModel):
    """Record a proposed file change for later server-applied write."""
    target_path: str
    action: str = "create"              # create | edit | replace | delete | rename
    content: str | None = None          # required for create/edit/replace
    new_path: str | None = None         # required for rename
    summary: str = ""
    risk_level: str | None = None
    session_id: str | None = None
    job_id: str | None = None


class UiPrefs(BaseModel):
    """User-togglable runtime prefs set from the PWA. A field left unset (None) is
    not changed, so toggling one pref never resets another."""
    auto_apply_proposed: bool | None = None
    auto_run_calendar: bool | None = None
    auto_run_hunter: bool | None = None


class ClientLog(BaseModel):
    """A failure beacon from the PWA — see POST /clientlog."""
    reason: str                       # short code, e.g. "kickoff-http", "poll-404"
    detail: str | None = None         # free text (status, error name, message)
    sid: str | None = None            # the session_id the turn was resuming, if any
    mode: str | None = None           # "voice" | "work"
    elapsed_ms: int | None = None     # how long the turn ran before it died


class CapabilityTierBody(BaseModel):
    tier: str
    confirm: bool | None = None


class AiPlanBody(BaseModel):
    """Partial update for the AI plan: any field may be omitted to leave it as-is."""
    mode: str | None = None            # subscription | api_key
    model: str | None = None           # one of config.KNOWN_MODELS ids
    api_key: str | None = None         # stored to .env; never echoed back
    budget_usd: float | None = None    # monthly ceiling for api_key mode (0 = off)


class CalendarTestRequest(BaseModel):
    """A wizard 'Test connection' check against the user's OWN deployed Apps
    Script calendar bridge. The token is the bridge token the user generated; it
    is validated transiently and NEVER stored or logged by this request."""
    bridge_url: str
    token: str
    calendar_id: str | None = "primary"


class CalendarEnableRequest(BaseModel):
    """A wizard 'Enable calendar' action: turn the connector ON by having the
    SERVER write its own config (the comma/bracket-proof alternative to hand-
    editing settings.json + .env). The bridge is re-validated first, then the
    non-secret block is written to settings.json and the token to .env. The token
    is written only to the local .env and is NEVER logged or echoed back."""
    bridge_url: str
    token: str
    calendar_id: str | None = "primary"


class EmailTestRequest(BaseModel):
    """A wizard 'Test connection' check against the user's OWN deployed Apps
    Script Gmail bridge. The token is the bridge token the user generated; it is
    validated transiently and NEVER stored or logged by this request."""
    bridge_url: str
    token: str


class EmailEnableRequest(BaseModel):
    """A wizard 'Enable email' action: turn the connector ON by having the SERVER
    write its own config (settings.json gmail block + .env token). The bridge is
    re-validated first. Email enables in DRAFT-ONLY mode (allow_send stays false —
    sending is a separate, deliberate opt-in). The token is written only to the
    local .env and is NEVER logged or echoed back."""
    bridge_url: str
    token: str


class LinkedInTestRequest(BaseModel):
    """A wizard 'Test connection' check for the LinkedIn API auto-post lane. Only
    the member access token is validated (read-only /v2/userinfo) to resolve the
    author URN. The token is used transiently and NEVER stored or logged; the
    client_id is non-secret and optional. The client secret is never sent here."""
    access_token: str
    client_id: str | None = None


class LinkedInEnableRequest(BaseModel):
    """A wizard 'Enable' action for LinkedIn. Two lanes:
    - DRAFT lane (api_enabled=false): no credentials; the server writes only the
      settings.json linkedin block (enabled, api_enabled=false).
    - API auto-post lane (api_enabled=true): the access token is re-validated and
      the author URN resolved; the server writes the settings block (with client_id
      + author_urn) AND the .env secrets (access token, optional client secret).
    The access token and client secret are written only to the local .env and are
    NEVER logged or echoed back."""
    api_enabled: bool = False
    access_token: str | None = ""
    client_id: str | None = ""
    client_secret: str | None = ""


class SmsTestRequest(BaseModel):
    """A wizard 'Test connection' check against the user's OWN Twilio account.
    The Auth Token is validated transiently (one authenticated read) and is
    NEVER stored or logged by this request."""
    account_sid: str
    auth_token: str
    number: str


class SmsEnableRequest(BaseModel):
    """A wizard 'Enable SMS' action: turn the inbound poller ON by having the SERVER
    write its own config (settings.json sms block + the four .env values). The Twilio
    credentials are re-validated first. owner_phone is the ONLY number whose texts are
    ever acted on. The Auth Token is written only to the local .env and is NEVER logged
    or echoed back."""
    account_sid: str
    auth_token: str
    number: str
    owner_phone: str | None = ""


class VoicemailTestRequest(BaseModel):
    """A wizard 'Test connection' check for the voicemail add-on. Verifies the Twilio
    Account SID + Auth Token and that the account owns the number (one authenticated
    read). The Auth Token is validated transiently and NEVER stored or logged."""
    account_sid: str
    auth_token: str
    number: str


class VoicemailEnableRequest(BaseModel):
    """A wizard 'Enable voicemail' action: turn the recordings poller ON by having
    the SERVER write its own config (settings.json voicemail block + the Twilio .env
    values, shared with SMS). The credentials are re-validated first. The Auth Token
    is written only to the local .env and is NEVER logged or echoed back."""
    account_sid: str
    auth_token: str
    number: str
    owner_name: str | None = ""
    greeting_text: str | None = ""
    transcribe_mode: str | None = "twilio"


class VoicemailWireRequest(BaseModel):
    """A wizard 'Wire my number' action: point the Twilio number's Voice webhook at
    the user's TwiML Bin URL via the Twilio API (the automatable slice of standing up
    the answer flow). The Auth Token is used transiently and NEVER stored or logged."""
    account_sid: str
    auth_token: str
    number: str
    voice_url: str


class VoicemailGreetingRequest(BaseModel):
    """A wizard 'Hear my greeting' action: render the greeting in Jarvis's Kokoro
    voice (bm_daniel) via the local tts_server and return the audio to preview. No
    credentials involved; nothing is saved or enabled."""
    text: str | None = ""
    owner_name: str | None = ""


class HunterTestRequest(BaseModel):
    """A wizard 'Test connection' check against the user's OWN deployed Hunter
    bridge (the verify endpoint). The token is the bridge token the user
    generated; it is validated transiently and NEVER stored or logged."""
    bridge_url: str
    token: str


class HunterEnableRequest(BaseModel):
    """A wizard 'Enable Hunter' action: turn the connector ON by having the SERVER
    write its own config (the comma/bracket-proof alternative to hand-editing
    settings.json + .env). The bridge is re-validated first, then the non-secret
    block is written to settings.json and the token to .env. The token is written
    only to the local .env and is NEVER logged or echoed back."""
    bridge_url: str
    token: str
