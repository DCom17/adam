"""Request models for the Adam API.

Split out of server.py so the router modules can import them without pulling
in the whole app. server.py re-exports every name for back-compat.

Input validation (OWASP API3:2023 — Broken Object Property Level Authorization,
and API4 — Unrestricted Resource Consumption). Every model below derives from
StrictModel, which enforces two things across the whole API surface:

  1. `extra="forbid"` — an unexpected field is a 422, not a silent drop. That
     turns a typo'd or injected property into a loud error instead of a value
     that quietly does nothing.
  2. A backstop cap on EVERY string field (MAX_STRING). Pydantic already
     enforces types; what it does not do by default is bound size, so without
     this a single POST could carry an arbitrarily large body.

Four fields legitimately carry bulk text (a pasted message, a synced
transcript, a pasted statement, a proposed file's content). Rather than scatter
per-field constraints across 44 models, their real ceilings live in one
name-keyed table, BULK_TEXT_FIELDS, which the validator consults; every other
string falls back to MAX_STRING.

Numeric fields are deliberately left unbounded: the finance models document
signed values (liabilities negative) and the health models accept either unit
system, so a blanket ge=0 would reject valid input. Their range checks belong
in the routers that understand the semantics."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

# --- Size ceilings ----------------------------------------------------------
# Backstop for every string field. 64 KB is orders of magnitude above any
# single-line value the UI sends (names, ids, modes, notes, URLs, tokens)
# while still bounding an abusive body.
MAX_STRING = 64 * 1024

# The handful of fields that legitimately carry bulk text, and their real
# ceilings. Keyed by field NAME so the rule stays in one place instead of
# scattered across 44 models — every other string falls back to MAX_STRING.
#   message — one composed or pasted turn
#   tx      — a chat's transcript HTML, mirrored for cross-device sync
#   raw     — a pasted bank statement awaiting parsing
#   content — the body of a proposed file write
MAX_BLOB = 4 * 1024 * 1024
BULK_TEXT_FIELDS = {
    "message": 200_000,
    "tx": MAX_BLOB,
    "raw": MAX_BLOB,
    "content": MAX_BLOB,
}


class StrictModel(BaseModel):
    """Base for every request model: reject unknown fields, bound string size."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _bound_string_size(cls, v: Any, info) -> Any:
        """Reject oversized strings before any coercion work happens.

        `before` mode matters: it runs on the raw input, so a 10 MB string is
        refused up front rather than after Pydantic has parsed and copied it.
        """
        if isinstance(v, str):
            cap = BULK_TEXT_FIELDS.get(info.field_name or "", MAX_STRING)
            if len(v) > cap:
                raise ValueError(
                    f"{info.field_name}: {len(v)} characters exceeds the {cap} limit"
                )
        return v


class AskRequest(StrictModel):
    message: str
    session_id: str | None = None
    mode: str | None = None  # "voice" (default) | "work" | "code" (needs allow_code_mode)
    attachments: list[str] | None = None  # server-side upload paths from /upload


class SpeakRequest(StrictModel):
    text: str
    voice: str | None = None


class PushSubscribe(StrictModel):
    subscription: dict


class SessionRecord(StrictModel):
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


class SessionSyncPush(StrictModel):
    sessions: list[SessionRecord] = []


class ApprovalCreate(StrictModel):
    """Park a pending controlled-action approval (see POST /approvals)."""
    action_summary: str
    action_type: str = "write"          # "write" | "command" | "destructive" | ...
    risk_level: str = "medium"          # "low" | "medium" | "high"
    target: str | None = None           # path or command the action concerns
    session_id: str | None = None
    job_id: str | None = None


class ActionProposeRequest(StrictModel):
    """Stage an EXTERNAL ACTION (calendar event, email draft, …) for approval. The
    server parks it as a pending approval carrying the payload; nothing executes
    until the user approves. action_type must be a known external_actions type and
    its add-on must be enabled. payload holds content only — never a secret."""
    action_type: str
    payload: dict
    summary: str | None = None
    target: str | None = None


class ProposedChangeCreate(StrictModel):
    """Record a proposed file change for later server-applied write."""
    target_path: str
    action: str = "create"              # create | edit | replace | delete | rename
    content: str | None = None          # required for create/edit/replace
    new_path: str | None = None         # required for rename
    summary: str = ""
    risk_level: str | None = None
    session_id: str | None = None
    job_id: str | None = None


class UiPrefs(StrictModel):
    """User-togglable runtime prefs set from the PWA. A field left unset (None) is
    not changed, so toggling one pref never resets another."""
    auto_apply_proposed: bool | None = None
    auto_run_calendar: bool | None = None
    auto_run_hunter: bool | None = None


class ClientLog(StrictModel):
    """A failure beacon from the PWA — see POST /clientlog."""
    reason: str                       # short code, e.g. "kickoff-http", "poll-404"
    detail: str | None = None         # free text (status, error name, message)
    sid: str | None = None            # the session_id the turn was resuming, if any
    mode: str | None = None           # "voice" | "work"
    elapsed_ms: int | None = None     # how long the turn ran before it died


class CapabilityTierBody(StrictModel):
    tier: str
    confirm: bool | None = None


class AiPlanBody(StrictModel):
    """Partial update for the AI plan: any field may be omitted to leave it as-is."""
    mode: str | None = None            # subscription | api_key
    model: str | None = None           # one of config.KNOWN_MODELS ids
    api_key: str | None = None         # stored to .env; never echoed back
    budget_usd: float | None = None    # monthly ceiling for api_key mode (0 = off)


class LicenseBody(StrictModel):
    """Activate or clear the paid-product license. An omitted/empty key deactivates."""
    key: str | None = None             # ADAM1-... license key; verified offline


class AssentBody(StrictModel):
    """Record the user's affirmative acceptance of the EULA at first run (clickwrap).
    `version` must match the EULA version the app presented; `method` distinguishes the
    in-app clickwrap from a checkout checkbox."""
    version: str
    method: str = "install-clickwrap"
    order_id: str | None = None


class CalendarTestRequest(StrictModel):
    """A wizard 'Test connection' check against the user's OWN deployed Apps
    Script calendar bridge. The token is the bridge token the user generated; it
    is validated transiently and NEVER stored or logged by this request."""
    bridge_url: str
    token: str
    calendar_id: str | None = "primary"


class CalendarEnableRequest(StrictModel):
    """A wizard 'Enable calendar' action: turn the connector ON by having the
    SERVER write its own config (the comma/bracket-proof alternative to hand-
    editing settings.json + .env). The bridge is re-validated first, then the
    non-secret block is written to settings.json and the token to .env. The token
    is written only to the local .env and is NEVER logged or echoed back."""
    bridge_url: str
    token: str
    calendar_id: str | None = "primary"


class EmailTestRequest(StrictModel):
    """A wizard 'Test connection' check against the user's OWN deployed Apps
    Script Gmail bridge. The token is the bridge token the user generated; it is
    validated transiently and NEVER stored or logged by this request."""
    bridge_url: str
    token: str


class EmailEnableRequest(StrictModel):
    """A wizard 'Enable email' action: turn the connector ON by having the SERVER
    write its own config (settings.json gmail block + .env token). The bridge is
    re-validated first. Email enables in DRAFT-ONLY mode (allow_send stays false —
    sending is a separate, deliberate opt-in). The token is written only to the
    local .env and is NEVER logged or echoed back."""
    bridge_url: str
    token: str


class LinkedInTestRequest(StrictModel):
    """A wizard 'Test connection' check for the LinkedIn API auto-post lane. Only
    the member access token is validated (read-only /v2/userinfo) to resolve the
    author URN. The token is used transiently and NEVER stored or logged; the
    client_id is non-secret and optional. The client secret is never sent here."""
    access_token: str
    client_id: str | None = None


class LinkedInEnableRequest(StrictModel):
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


class SmsTestRequest(StrictModel):
    """A wizard 'Test connection' check against the user's OWN Twilio account.
    The Auth Token is validated transiently (one authenticated read) and is
    NEVER stored or logged by this request."""
    account_sid: str
    auth_token: str
    number: str


class SmsEnableRequest(StrictModel):
    """A wizard 'Enable SMS' action: turn the inbound poller ON by having the SERVER
    write its own config (settings.json sms block + the four .env values). The Twilio
    credentials are re-validated first. owner_phone is the ONLY number whose texts are
    ever acted on. The Auth Token is written only to the local .env and is NEVER logged
    or echoed back."""
    account_sid: str
    auth_token: str
    number: str
    owner_phone: str | None = ""


class VoicemailTestRequest(StrictModel):
    """A wizard 'Test connection' check for the voicemail add-on. Verifies the Twilio
    Account SID + Auth Token and that the account owns the number (one authenticated
    read). The Auth Token is validated transiently and NEVER stored or logged."""
    account_sid: str
    auth_token: str
    number: str


class VoicemailEnableRequest(StrictModel):
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


class VoicemailWireRequest(StrictModel):
    """A wizard 'Wire my number' action: point the Twilio number's Voice webhook at
    the user's TwiML Bin URL via the Twilio API (the automatable slice of standing up
    the answer flow). The Auth Token is used transiently and NEVER stored or logged."""
    account_sid: str
    auth_token: str
    number: str
    voice_url: str


class VoicemailGreetingRequest(StrictModel):
    """A wizard 'Hear my greeting' action: render the greeting in Adam's Kokoro
    voice (bm_daniel) via the local tts_server and return the audio to preview. No
    credentials involved; nothing is saved or enabled."""
    text: str | None = ""
    owner_name: str | None = ""


class FinanceImportBody(StrictModel):
    """Import a statement into the review staging area (Phase F2). Provide ONE of:
      - `rows`: already-structured rows (client-parsed, or a test) — no LLM used;
      - `raw`: pasted statement text/CSV — parsed by Claude;
      - `file_path`: an uploaded text/CSV file (from /upload) — read + parsed.
    `account` is the account the rows belong to when a row doesn't name its own
    (the user confirms it per import). Nothing hits the ledger until the batch is
    approved."""
    account: str | None = None
    rows: list[dict] | None = None
    raw: str | None = None
    file_path: str | None = None           # single uploaded file
    file_paths: list[str] | None = None    # several uploaded files → one combined batch
    source: str | None = None
    batch_id: str | None = None


class FinanceBalancePhotoBody(StrictModel):
    """Read account balances from an uploaded screenshot (a bank/brokerage view).
    `file_path` is an /upload path; the detected balances are returned for the user
    to confirm before saving as a snapshot."""
    file_path: str


class FinanceRowEdit(StrictModel):
    """Edit a staged row (or a committed transaction): recategorize, rename the
    merchant, add a note, or set a transfer pair key. Any field left None is
    unchanged. Setting a real category teaches a merchant rule unless
    `teach_rule` is false."""
    category: str | None = None
    merchant: str | None = None
    notes: str | None = None
    transfer_pair_key: str | None = None
    teach_rule: bool = True
    # Acknowledge a committed Transfer-type row as a real internal transfer so the
    # review tab stops flagging it as unmatched. Committed transactions only;
    # ignored by the staged-row editor.
    transfer_ack: bool | None = None


class FinanceSettingsBody(StrictModel):
    """Update the settings the metrics engine reads. Any omitted field is left as
    is; sending null for income/debt/reserve clears the override so the value is
    computed from transactions again."""
    ef_months: float | None = None
    extra_buffer: float | None = None
    gross_monthly_income: float | None = None
    monthly_debt_payments: float | None = None
    card_payoff_reserve: float | None = None


class FinanceAccountBody(StrictModel):
    """Create or update an account. `type` is cash | credit | investment | loan."""
    name: str
    institution: str = ""
    type: str = "cash"
    expected_in_snapshot: bool = True


class FinanceCategoryBody(StrictModel):
    """Create or update a category in the taxonomy."""
    name: str
    grp: str = "Lifestyle"
    type: str = "Expense"
    essential: bool = False
    recurring: bool = False
    sort: int = 500


class FinanceSnapshotBody(StrictModel):
    """Record a weekly balance snapshot: per-account balances on `date`. Sign
    convention: assets positive, liabilities negative."""
    date: str
    balances: dict[str, float]


class HealthWeightBody(StrictModel):
    """Log a weigh-in. `date` defaults to today (server-side) when omitted."""
    weight: float
    date: str | None = None
    unit: str = "lb"
    note: str = ""


class HealthMealBody(StrictModel):
    """Log a meal/food with macros. `date` defaults to today when omitted."""
    name: str
    date: str | None = None
    qty: str = ""
    kcal: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    source: str = "manual"
    note: str = ""


class HealthMealEdit(StrictModel):
    """Patch a logged meal. Omitted fields are unchanged. `date` moves the entry
    to another day — the fix for food logged onto the wrong date."""
    name: str | None = None
    date: str | None = None
    qty: str | None = None
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    note: str | None = None


class HealthWeightEdit(StrictModel):
    """Patch a logged weigh-in. Omitted fields are unchanged."""
    weight: float | None = None
    date: str | None = None
    unit: str | None = None
    note: str | None = None


class HealthWaterEdit(StrictModel):
    """Patch a logged drink. `amount` is in `unit` (oz | ml | cup) and is
    converted to millilitres before storage, matching the log path."""
    amount: float | None = None
    unit: str | None = None
    date: str | None = None
    note: str | None = None


class HealthWaterBody(StrictModel):
    """Log a drink of water. `amount` is in `unit` (oz | ml | cup) and is stored
    as millilitres. `date` defaults to today when omitted."""
    amount: float
    unit: str = "oz"
    date: str | None = None
    note: str = ""


class HealthMetricBody(StrictModel):
    """Set (merge) a day's wearable metrics. `date` defaults to today."""
    date: str | None = None
    steps: int | None = None
    sleep_min: int | None = None
    resting_hr: int | None = None
    stress: int | None = None
    body_battery: int | None = None
    active_kcal: int | None = None
    source: str | None = None


class HealthTargetsBody(StrictModel):
    """Set daily targets + weight goal. Omitted fields are left as-is; sending
    null clears a target."""
    target_kcal: float | None = None
    target_protein_g: float | None = None
    target_carbs_g: float | None = None
    target_fat_g: float | None = None
    target_water_ml: float | None = None
    weight_goal: float | None = None
    weight_unit: str | None = None
    water_unit: str | None = None


class HealthPhotoBody(StrictModel):
    """Estimate a meal from an uploaded photo (Phase H2). `file_path` is an
    /upload path; the estimate is returned for review, not logged automatically."""
    file_path: str
    note: str = ""


class HealthTextBody(StrictModel):
    """Estimate a meal's macros from a plain-text description (Phase H2). e.g.
    "1 banana" or "2 eggs and toast". Returns an editable estimate, not logged."""
    text: str


class HealthCorrectionBody(StrictModel):
    """Teach/remember the macros for a named food, so a future photo estimate of
    the same food comes back with the user's numbers (Phase H2 learning loop)."""
    name: str
    kcal: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0


class GarminEnableBody(StrictModel):
    """Enable the (unofficial) Garmin sync add-on (Phase H3). Credentials go to
    .env only; `enabled` flips the integrations.garmin block in settings.json.
    Sending an empty email+password with enabled=false disconnects."""
    email: str = ""
    password: str = ""
    enabled: bool = True


class GarminSyncBody(StrictModel):
    """Pull a day's Garmin metrics into the health store. Date defaults to today."""
    date: str | None = None


class HunterTestRequest(StrictModel):
    """A wizard 'Test connection' check against the user's OWN deployed Hunter
    bridge (the verify endpoint). The token is the bridge token the user
    generated; it is validated transiently and NEVER stored or logged."""
    bridge_url: str
    token: str


class HunterEnableRequest(StrictModel):
    """A wizard 'Enable Hunter' action: turn the connector ON by having the SERVER
    write its own config (the comma/bracket-proof alternative to hand-editing
    settings.json + .env). The bridge is re-validated first, then the non-secret
    block is written to settings.json and the token to .env. The token is written
    only to the local .env and is NEVER logged or echoed back."""
    bridge_url: str
    token: str
