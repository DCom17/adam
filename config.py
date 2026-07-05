"""
Adam — configuration layer.

One place to resolve every runtime value. Nothing machine-specific lives in
server.py anymore; it all flows from here.

Resolution order (last wins):
    1. Built-in defaults below.
    2. settings.json   (non-secret app settings; copy from settings.example.json)
    3. .env            (secrets + machine values; copy from .env.example)

Secrets (tokens, auth keys) come ONLY from the environment / .env — never from
settings.json, so settings.json stays safe to commit/share.

Paths in settings.json may be absolute, or relative to the project root (this
file's directory). Relative paths keep a cloned project self-contained.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

# --- Roots ------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
# Where the user's settings.json + .env live. Defaults to the repo root; the
# ADAM_CONFIG_ROOT env var points it elsewhere so tests run against clean
# example defaults (and a packaged install can keep config outside the app dir).
# JARVIS_CONFIG_ROOT is honored as a fallback for pre-rename environments.
CONFIG_ROOT = Path(os.environ.get("ADAM_CONFIG_ROOT", "").strip()
                   or os.environ.get("JARVIS_CONFIG_ROOT", "").strip() or ROOT)
load_dotenv(CONFIG_ROOT / ".env")  # secrets + machine values

APP_NAME = "adam-local"
APP_VERSION = "0.9.35"


# --- Settings file ----------------------------------------------------------

def _load_settings() -> dict:
    """Read settings.json if present, else fall back to settings.example.json so
    a fresh clone still boots with sensible defaults before the user customizes."""
    for p in (CONFIG_ROOT / "settings.json", ROOT / "settings.example.json"):
        name = p.name
        if p.exists():
            # utf-8-sig reads files with OR without a BOM — a BOM-prefixed
            # settings.json (e.g. saved by some Windows editors) no longer
            # crashes config import.
            try:
                return json.loads(p.read_text("utf-8-sig"))
            except json.JSONDecodeError as e:
                # Fail fast (the server must not boot on broken config) but with
                # a plain-language message that names the file + parse location
                # and points at the fix. Never echoes file contents or secrets.
                raise RuntimeError(
                    f"{name} is not valid JSON (line {e.lineno}, column {e.colno}): "
                    f"{e.msg}. Fix the JSON syntax, or restore it from "
                    f"settings.example.json."
                ) from e
    return {}


_S = _load_settings()


def _get(key: str, default):
    """settings.json value for `key`, or `default` if missing/null."""
    val = _S.get(key)
    return default if val is None else val


def _resolve_path(value: str | os.PathLike, default_rel: str) -> Path:
    """Turn a configured path into an absolute Path. Empty/None -> default_rel
    under the project root. Relative values resolve against the project root."""
    raw = str(value).strip() if value else ""
    if not raw:
        raw = default_rel
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    return p if p.is_absolute() else (ROOT / p).resolve()


# --- Claude executable ------------------------------------------------------
# Explicit setting wins; otherwise look on PATH so most installs need no config.

def _find_claude() -> str:
    configured = str(_get("claude_exe", "")).strip()
    if configured:
        return os.path.expandvars(os.path.expanduser(configured))
    found = shutil.which("claude") or shutil.which("claude.exe")
    return found or ""


CLAUDE_EXE = _find_claude()
VOICE_MODEL = str(_get("voice_model", "claude-opus-4-8"))

# --- AI plan (how Claude usage is paid for) ----------------------------------
# 'subscription': the CLI's own `claude /login` credentials (Pro/Max) cover
#   usage at the plan's flat rate — the pre-productization behavior.
# 'api_key': pay-as-you-go on the user's own Anthropic API key. The key lives in
#   .env as ANTHROPIC_API_KEY and reaches ONLY the claude.exe subprocess env
#   (see run_claude) — never this process's environ — so a stored key can never
#   silently shadow a subscription login, and a subscription install with a
#   stray global key never gets billed per-token by accident.
AUTH_MODE = str(_get("auth_mode", "subscription")).strip().lower()
if AUTH_MODE not in ("subscription", "api_key"):
    AUTH_MODE = "subscription"
# Pop (not read): load_dotenv above put any .env key into os.environ, and the
# spawned CLI inherits os.environ — the pop is what makes the injection opt-in.
ANTHROPIC_API_KEY = os.environ.pop("ANTHROPIC_API_KEY", "").strip()

# Monthly spend ceiling for api_key mode, in USD. When the month's recorded
# spend (usage_store) reaches it, turns are refused with a raise-budget hint
# instead of silently running up a bill. 0 disables the gate.
try:
    API_BUDGET_MONTHLY_USD = float(_get("api_budget_monthly_usd", 10.0) or 0.0)
except (TypeError, ValueError):
    API_BUDGET_MONTHLY_USD = 10.0

# The model picker the UI offers, with the per-plan recommendation. Haiku is
# deliberately never recommended — it follows Adam's structured safety seams
# (PROPOSE blocks, memory-write formats) too loosely — but stays selectable.
KNOWN_MODELS = (
    {"id": "claude-opus-4-8", "label": "Claude Opus", "note": "most capable"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet", "note": "fast + affordable"},
    {"id": "claude-haiku-4-5", "label": "Claude Haiku", "note": "cheapest — not recommended for Adam"},
)
RECOMMENDED_MODEL = {"subscription": "claude-opus-4-8", "api_key": "claude-sonnet-5"}

# --- Working directories ----------------------------------------------------
# VAULT_PATH is the cwd Claude runs in (the user's own files/projects root).
VAULT_PATH = str(_resolve_path(_get("vault_path", ""), "."))
# Extra dirs work mode may reach beyond the vault.
WORK_EXTRA_DIRS = [
    os.path.expandvars(os.path.expanduser(str(d)))
    for d in _get("work_extra_dirs", []) if str(d).strip()
]

# --- Server -----------------------------------------------------------------
HOST = str(_get("host", "0.0.0.0"))
PORT = int(_get("port", 8000))
# Where the PWA is reached from outside (used for Twilio signature checks, docs).
PUBLIC_BASE_URL = str(_get("public_base_url", "")).strip()
# CORS: list of allowed origins, or ["*"] to allow all (the prototype default,
# since the bearer token is the real gate). Tighten in settings.json for prod.
CORS_ALLOWED_ORIGINS = _get("cors_allowed_origins", ["*"]) or ["*"]

# --- Local TTS service ------------------------------------------------------
TTS_URL = str(_get("tts_url", "http://127.0.0.1:8001/tts"))
TTS_TIMEOUT_SECONDS = int(_get("tts_timeout_seconds", 30))

# --- Timeouts / job lifecycle ----------------------------------------------
CLAUDE_TIMEOUT_SECONDS = int(_get("claude_timeout_seconds", 120))
ASYNC_CLAUDE_TIMEOUT_SECONDS = int(_get("async_claude_timeout_seconds", 600))
# Claude Code mode chats get their own, much longer leash: a real coding task
# (edit → run tests → fix → re-run) routinely outlives the 10-minute async cap.
# Live tool-activity streaming + the stop button make a long turn supervisable,
# so the cap is a backstop against a wedged process, not a UX guarantee.
CODE_CLAUDE_TIMEOUT_SECONDS = int(_get("code_claude_timeout_seconds", 3600))
JOB_TTL_SECONDS = int(_get("job_ttl_seconds", 600))

# --- Data directories (off the synced vault by default) ---------------------
DATA_DIR = _resolve_path(_get("data_dir", ""), "data")
UPLOAD_DIR = _resolve_path(_get("upload_dir", ""), "data/uploads")
LOG_DIR = _resolve_path(_get("log_dir", ""), "data/logs")
STATE_DIR = _resolve_path(_get("state_dir", ""), "data/state")

UPLOAD_MAX_BYTES = int(_get("upload_max_mb", 25)) * 1024 * 1024
UPLOAD_TTL_SECONDS = int(_get("upload_ttl_days", 7)) * 24 * 3600

# State files (persist across restarts).
LOG_FILE = LOG_DIR / "voice_server.log"
PUSH_SUB_FILE = STATE_DIR / "push_sub.json"
LAST_RESULT_FILE = STATE_DIR / "last_result.json"
# VAPID private key path: secret, so it comes from .env (or defaults under state).
VAPID_PRIVATE_PEM = _resolve_path(
    os.environ.get("VAPID_PRIVATE_PEM", ""), "data/state/vapid_private.pem"
)

# --- Permissions (Level 3 controlled-action foundation) ---------------------
# A conservative allow-list model. By default the agent may READ the vault but
# only WRITE into an outputs/drafts tree under data/ — never the vault, never the
# system. Shell and destructive actions are off; writes need approval; existing
# files are backed up before being overwritten; everything is audited. All of
# this is settings.json-configurable so an operator can widen it deliberately.
_PERM = _get("permissions", {}) or {}


def _perm(key: str, default):
    val = _PERM.get(key)
    return default if val is None else val


def _perm_dirs(key: str, fallback_defaults: list[str]) -> list[str]:
    """Resolve a list of configured directories to absolute path strings,
    dropping blanks. An empty/missing list falls back to `fallback_defaults`."""
    raw = _perm(key, None)
    items = raw if raw is not None else fallback_defaults
    out: list[str] = []
    for d in items or []:
        if str(d).strip():
            out.append(str(_resolve_path(d, ".")))
    return out


# Directories the agent may read from. Empty -> just the vault.
PERM_READ_DIRS = _perm_dirs("read_dirs", []) or [VAULT_PATH]
# Directories writes are permitted into. Conservative default: data/outputs and
# data/drafts, both under the project's data tree — never the vault itself.
PERM_WRITE_DIRS = _perm_dirs("write_dirs", ["data/outputs", "data/drafts"])
# Shell commands explicitly permitted (only consulted if allow_shell is true).
PERM_ALLOWED_COMMANDS = [
    str(c).strip() for c in (_perm("allowed_commands", []) or []) if str(c).strip()
]

PERM_REQUIRE_APPROVAL_WRITES = bool(_perm("require_approval_for_writes", True))
PERM_REQUIRE_APPROVAL_DESTRUCTIVE = bool(_perm("require_approval_for_destructive", True))
PERM_BACKUP_BEFORE_WRITE = bool(_perm("backup_before_write", True))
# Backup retention (keeps the undo history bounded so it can't grow forever). After
# each backup, anything older than max-age is removed, then only the newest N are kept.
PERM_BACKUP_KEEP = int(_perm("backup_keep", 50))
PERM_BACKUP_MAX_AGE_DAYS = int(_perm("backup_max_age_days", 30))
PERM_AUDIT_LOG_ENABLED = bool(_perm("audit_log_enabled", True))
PERM_ALLOW_SHELL = bool(_perm("allow_shell", False))
PERM_ALLOW_NETWORK_TOOLS = bool(_perm("allow_network_tools", False))
PERM_MAX_FILE_SIZE_MB = int(_perm("max_file_size_mb", 25))

# Paths that are never readable or writable, regardless of the allow-lists. The
# project's own secret/config files are protected out of the box.
PERM_BLOCKED_PATHS = [
    str(_resolve_path(d, "."))
    for d in (_perm("blocked_paths", []) or [])
    if str(d).strip()
]
# Filename glob patterns that may never be written (secrets/keys/config), even
# inside an allowed write directory.
PERM_PROTECTED_FILE_PATTERNS = list(
    _perm(
        "protected_file_patterns",
        [".env", ".env.*", "*.pem", "*.key", "*.secret", "id_rsa*", "settings.json"],
    )
)

# Self-edit lane (default OFF). When enabled, the agent's proposed changes may write to
# the app's OWN source files - so a user can tell Adam to change/customize itself -
# STILL through the full approve -> backup -> audit pipeline. Secrets (via the protected
# patterns above) stay refused, the runtime data tree stays off-limits, and only
# source/text file types are eligible. This is the "let Adam edit itself" gate.
PERM_ALLOW_APP_SELF_EDIT = bool(_perm("allow_app_self_edit", False))
APP_ROOT = str(_resolve_path(".", "."))
PERM_APP_SELF_EDIT_DIRS = _perm_dirs("app_self_edit_dirs", [APP_ROOT])
# Never self-edit the runtime data tree (state, db, backups, uploads, logs).
PERM_APP_SELF_EDIT_EXCLUDE = [str(_resolve_path("data", "."))]
# Only these file types are eligible for self-edit (code + simple text/config).
PERM_APP_SELF_EDIT_EXTS = [
    str(e).lower() for e in _perm(
        "app_self_edit_exts",
        [".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".ps1", ".cmd", ".gs", ".bat"],
    )
]

# Permission-system data locations (under the data tree, gitignored).
BACKUP_DIR = _resolve_path(_get("backup_dir", ""), "data/backups")
AUDIT_LOG_FILE = LOG_DIR / "audit.jsonl"
APPROVALS_FILE = STATE_DIR / "approvals.json"

# Infrastructure directories the server manages itself (uploads, logs, state,
# backups). These are always writable by the server's own machinery and are
# implicitly trusted as write targets — permission gating governs *content*
# writes into user directories, not the app's own bookkeeping.
INFRA_WRITE_DIRS = [str(UPLOAD_DIR), str(STATE_DIR), str(LOG_DIR), str(BACKUP_DIR)]

# --- Agent safety (Phase 3: control how Claude Code itself may act) ----------
# Phase 2 made the *server* a safe write boundary. But Claude runs in its own
# subprocess and the server can't see its individual tool calls — so if Claude
# can edit files directly, the permission layer isn't the true boundary. These
# settings make the spawn itself safe:
#
#   draft_only     (DEFAULT) — Claude gets NO file-mutation or shell tools and
#                  runs in a throwaway workspace, not the vault. It reads context
#                  and proposes changes; the server applies approved ones.
#   controlled_write — same Claude-side restriction; reserved for future
#                  server-mediated write automation. Still no direct Claude edits.
#   legacy_direct  — old behavior: cwd=vault, full tools, no restriction. For
#                  PERSONAL use only. Clearly UNSAFE for a shipped product.
_AS = _get("agent_safety", {}) or {}


def _as(key: str, default):
    val = _AS.get(key)
    return default if val is None else val


AGENT_MODE = str(_as("mode", "draft_only")).strip().lower()
if AGENT_MODE not in ("draft_only", "controlled_write", "legacy_direct"):
    AGENT_MODE = "draft_only"

ALLOW_DIRECT_CLAUDE_WRITES = bool(_as("allow_direct_claude_writes", False))
FORCE_OUTPUTS_TO_DRAFTS = bool(_as("force_outputs_to_drafts", True))
REQUIRE_SERVER_APPLIED_WRITES = bool(_as("require_server_applied_writes", True))

# Brain self-write lane (U4b). When on, the agent's NON-destructive writes INTO
# the configured vault auto-apply through apply()'s guardrails (backup, audit,
# conflict, size); destructive vault writes (delete/rename) and any write OUTSIDE
# the vault still require approval and route to drafts. This lets the bundled
# brain update its own memory/logs/tasks WITHOUT weakening the tool-restriction
# core: Claude still gets no write tools and the server remains the sole writer.
# Defaults ON for the unified product: the bundled brain must self-update (memory,
# logs, tasks) to be usable. Set agent_safety.brain_write=false to disable the lane
# (writes then fall back to drafts + approval like any other write).
BRAIN_WRITE_ENABLED = bool(_as("brain_write", True))

# Tools Claude may NOT use under a safe (non-legacy) mode. This is the real
# enforcement lever: --disallowedTools denies them at the CLI regardless of the
# customer's permission config (verified empirically). Mutators + shell.
#
# IMPORTANT: every name here MUST be a CURRENT Claude Code tool. The CLI HARD-ERRORS
# the entire turn if a deny rule names a tool it doesn't know ("Permission deny rule
# X matches no known tool — check for typos"), so a stale name bricks EVERY turn. The
# obsolete MultiEdit / BashOutput / KillShell were removed for exactly that reason;
# Write + Edit + NotebookEdit + Bash still block all file writes and shell execution.
AGENT_DENIED_TOOLS = list(_as("denied_tools", [
    "Write", "Edit", "NotebookEdit", "Bash",
]))
# Self-heal: an existing install's settings.json may still carry obsolete tool names
# (older templates shipped MultiEdit/BashOutput/KillShell). The CLI hard-errors a turn
# on any unknown deny rule, and updates intentionally DON'T overwrite settings.json —
# so strip the known-obsolete names here, at load, no matter their source. This keeps
# every file-write/shell tool denied while never feeding the CLI a name it rejects.
_OBSOLETE_DENY_TOOLS = {"MultiEdit", "BashOutput", "KillShell"}
AGENT_DENIED_TOOLS = [t for t in AGENT_DENIED_TOOLS if t not in _OBSOLETE_DENY_TOOLS]

# Claude Code mode chats (opt-in, default OFF — never ships hot). When true, a
# chat the user deliberately escalates (long-press on the Operator Mode button)
# runs as RAW Claude Code: cwd=vault, full tools, no --disallowedTools, permission
# prompts bypassed. That spawn BYPASSES the server-sole-writer rails (no proposal
# gate, no backup-before-write, no write allow-list) for that chat only — the
# global tier/mode below stays authoritative for every other chat. Owner-grade
# power; a friend's install keeps this false unless they flip it themselves.
AGENT_ALLOW_CODE_MODE = bool(_as("allow_code_mode", False))
# Extra folders a Claude Code mode chat can reach with file tools (--add-dir),
# on top of the vault + work_extra_dirs. Point this at repos you actually code
# on (e.g. the app's own dev source). Only meaningful when allow_code_mode is on.
AGENT_CODE_MODE_DIRS = [str(d) for d in (_as("code_mode_dirs", []) or []) if str(d).strip()]

# True when the spawn must be restricted (any mode except legacy_direct, unless a
# deliberate override turns direct writes back on inside a safe mode — which we
# still refuse, since mode is the authority). Direct Claude writes happen ONLY in
# legacy_direct.
AGENT_RESTRICT_TOOLS = AGENT_MODE != "legacy_direct"
AGENT_DIRECT_WRITES = (AGENT_MODE == "legacy_direct") and ALLOW_DIRECT_CLAUDE_WRITES is not False

# Isolated, throwaway workspace Claude runs in under safe modes (NOT the vault),
# so any stray write lands here instead of in the user's files.
AGENT_WORKSPACE = _resolve_path(_get("agent_workspace", ""), "data/agent_workspace")
# Where proposed-change drafts and applied outputs land (also the default write
# allow-list targets).
DRAFTS_DIR = _resolve_path(_get("drafts_dir", ""), "data/drafts")
OUTPUTS_DIR = _resolve_path(_get("outputs_dir", ""), "data/outputs")
PROPOSED_CHANGES_FILE = STATE_DIR / "proposed_changes.json"

# --- Capability tier (Safe / Powerful / Unrestricted) -----------------------
# One friendly dial that presets the agent mode + permission policy — the
# product's front-door control. Power users can still hand-tune the granular
# agent_safety / permissions blocks instead (leave capability_tier unset/"custom").
#
#   safe         — draft_only: Adam proposes; writes need approval. (default)
#   powerful     — controlled_write, auto-approve non-destructive writes across the
#                  whole vault, connectors on; destructive still confirmed; no shell.
#   unrestricted — controlled_write opened all the way: self-edit ON, shell ON,
#                  write across vault + app, auto-approve everything.
#
# CRITICAL DESIGN: 'unrestricted' is NOT legacy_direct. legacy_direct hands Claude
# raw write/shell tools the server can't see, which would BYPASS the rails below.
# Every tier here keeps the server as the sole writer, so the always-on rails hold.
#
# ALWAYS-ON RAILS (forced true at EVERY tier, even unrestricted; cannot be disabled):
#   backup-before-write · audit log · secret-file protection · self-edit
#   auto-rollback. Full power stays recoverable. The 4th rail (self_edit_guard)
#   validates an app-source self-edit and reverts it if it breaks the app's import;
#   backup-before-write provides the restore point it rolls back to.
CAPABILITY_TIER = str(_get("capability_tier", "") or "").strip().lower()
if CAPABILITY_TIER not in ("safe", "powerful", "unrestricted"):
    CAPABILITY_TIER = ""  # "" = custom: honor the granular settings as-is

_SECRET_PROTECT_DEFAULTS = [
    ".env", ".env.*", "*.pem", "*.key", "*.secret", "id_rsa*", "settings.json",
]


def _apply_capability_tier() -> None:
    """Override the agent-mode + permission globals from the selected tier preset.
    No-op when no tier is set (custom). The enforcement layer (permissions.py)
    reads these globals at runtime, so setting them here is the whole wiring."""
    global AGENT_MODE, AGENT_RESTRICT_TOOLS, AGENT_DIRECT_WRITES
    global BRAIN_WRITE_ENABLED, PERM_ALLOW_APP_SELF_EDIT
    global PERM_REQUIRE_APPROVAL_WRITES, PERM_REQUIRE_APPROVAL_DESTRUCTIVE
    global PERM_ALLOW_SHELL, PERM_ALLOWED_COMMANDS, PERM_ALLOW_NETWORK_TOOLS
    global PERM_WRITE_DIRS
    if not CAPABILITY_TIER:
        return
    if CAPABILITY_TIER == "safe":
        AGENT_MODE = "draft_only"
        BRAIN_WRITE_ENABLED = True
        PERM_ALLOW_APP_SELF_EDIT = False
        PERM_REQUIRE_APPROVAL_WRITES = True
        PERM_REQUIRE_APPROVAL_DESTRUCTIVE = True
        PERM_ALLOW_SHELL = False
        PERM_ALLOW_NETWORK_TOOLS = False
        PERM_WRITE_DIRS = [str(DRAFTS_DIR), str(OUTPUTS_DIR)]
    elif CAPABILITY_TIER == "powerful":
        AGENT_MODE = "controlled_write"
        BRAIN_WRITE_ENABLED = True
        PERM_ALLOW_APP_SELF_EDIT = False
        PERM_REQUIRE_APPROVAL_WRITES = False        # auto-approve non-destructive
        PERM_REQUIRE_APPROVAL_DESTRUCTIVE = True     # destructive still confirmed
        PERM_ALLOW_SHELL = False
        PERM_ALLOW_NETWORK_TOOLS = True
        PERM_WRITE_DIRS = [VAULT_PATH, str(OUTPUTS_DIR), str(DRAFTS_DIR)]
    elif CAPABILITY_TIER == "unrestricted":
        AGENT_MODE = "controlled_write"              # NOT legacy_direct — rails must hold
        BRAIN_WRITE_ENABLED = True
        PERM_ALLOW_APP_SELF_EDIT = True
        PERM_REQUIRE_APPROVAL_WRITES = False
        PERM_REQUIRE_APPROVAL_DESTRUCTIVE = False     # auto-approve everything
        PERM_ALLOW_SHELL = True
        PERM_ALLOWED_COMMANDS = []                    # empty + shell on = any command
        PERM_ALLOW_NETWORK_TOOLS = True
        PERM_WRITE_DIRS = [VAULT_PATH, APP_ROOT, str(OUTPUTS_DIR), str(DRAFTS_DIR)]
    # Recompute the mode-derived flags (mirrors the originals above).
    AGENT_RESTRICT_TOOLS = AGENT_MODE != "legacy_direct"
    AGENT_DIRECT_WRITES = (AGENT_MODE == "legacy_direct") and ALLOW_DIRECT_CLAUDE_WRITES is not False


_apply_capability_tier()

# Always-on rails — forced at EVERY tier (and as a safe strengthening in custom
# mode too). These three are cheap and make full power recoverable, so they are
# never disablable through the tier system.
PERM_BACKUP_BEFORE_WRITE = True
PERM_AUDIT_LOG_ENABLED = True
for _sp in _SECRET_PROTECT_DEFAULTS:
    if _sp not in PERM_PROTECTED_FILE_PATTERNS:
        PERM_PROTECTED_FILE_PATTERNS.append(_sp)
# 4th rail: a self-edit to the app's OWN source is validated in isolation and
# auto-rolled-back if it stops the app from importing (see self_edit_guard.py).
# Forced on like the three above — not tier-disablable. Tests inject a stub probe
# rather than flipping this.
PERM_SELF_EDIT_AUTO_ROLLBACK = True

# --- Updates (Phase 2 delivery) ---------------------------------------------
# Updates are delivered via GitHub Releases: installs read the repo's permanent
# "latest release" endpoint (no auth, no fragile file IDs), and apply the attached
# build with the smart 3-way updater. The maintainer publishes a release to ship.
# `update_repo` is "owner/name" of the PUBLIC releases repo; override in settings.json.
UPDATE_REPO = str(_as("update_repo", "DCom17/adam-releases")).strip()
# Master switch for the in-app update check/banner (the UPDATE.cmd path still works).
UPDATE_CHECK_ENABLED = bool(_as("update_check_enabled", True))

# --- Persistent runtime state (Phase 5) -------------------------------------
# Async jobs + their history live in SQLite so a restart/crash/sleep doesn't
# vanish an in-flight or finished job (the old in-memory dict did). Stdlib
# sqlite3 — no new dependency. The already-persisted JSON stores (approvals,
# proposed_changes, push_sub, last_result) are intentionally left as-is.
JOBS_DB = STATE_DIR / "adam.db"
# Pre-rename installs (<= 0.9.34) have their job history in jarvis.db; updates
# preserve data/, so carry it forward once. WAL/SHM sidecars move with it.
_LEGACY_JOBS_DB = STATE_DIR / "jarvis.db"
if _LEGACY_JOBS_DB.exists() and not JOBS_DB.exists():
    for _suffix in ("", "-wal", "-shm"):
        _old = Path(str(_LEGACY_JOBS_DB) + _suffix)
        if _old.exists():
            _old.rename(Path(str(JOBS_DB) + _suffix))
USAGE_DB = STATE_DIR / "usage.db"
STATE_SCHEMA_VERSION = 1
# Cross-device chat sync: the server holds one authoritative copy of the user's
# chats + transcripts so every signed-in device shows the same list. Separate
# SQLite file from the job DB. ON by default; set false to keep chats device-local.
SESSIONS_DB = STATE_DIR / "sessions.db"
SESSION_SYNC_ENABLED = bool(_get("session_sync_enabled", True))
# A deleted chat isn't shredded immediately — its transcript is retained as a
# recoverable tombstone for this many days so an accidental X can be revived with
# full context from the drawer's Archived section. Past the window the sweep
# hard-purges the row (transcript and all). Clients mirror this same window.
SESSION_ARCHIVE_RETENTION_DAYS = int(_get("session_archive_retention_days", 7))
# How long terminal (complete/failed/interrupted/cancelled) jobs are retained in
# the DB before sweep — the local job-history window. Active jobs are never swept.
JOB_HISTORY_TTL_SECONDS = int(_get("job_history_ttl_days", 7)) * 24 * 3600
# Max characters of the user's input stored as a job summary (truncated — the
# full prompt is never persisted; only a short summary for history readability).
JOB_INPUT_SUMMARY_MAX = int(_get("job_input_summary_max_chars", 200))

# --- Integrations: Google Calendar (opt-in, OFF by default) -----------------
# A connector to the user's OWN Google Apps Script calendar bridge
# (calendar_bridge.gs), which runs in the user's Google account. We never hold a
# Google credential — only the random bridge token, which lives in .env. The
# connector is disabled unless explicitly enabled here AND given a bridge_url
# (settings.json) and GOOGLE_CALENDAR_TOKEN (.env). Reads are safe; writes
# (create/edit) route through the approval/confirmation flow. Delete is not
# supported anywhere. See google_calendar.py.
_CAL = (_get("integrations", {}) or {}).get("google_calendar", {}) or {}
CALENDAR_ENABLED = bool(_CAL.get("enabled", False))
CALENDAR_BRIDGE_URL = str(_CAL.get("bridge_url", "") or "").strip()
CALENDAR_DEFAULT_ID = str(_CAL.get("calendar_id", "primary") or "primary").strip()
CALENDAR_TIMEOUT_SECONDS = int(_CAL.get("timeout_seconds", 20))

# --- Integrations: SMS (opt-in, OFF by default) -----------------------------
# Inbound texts via POLLING the Twilio REST API (no public webhook / ingress).
# The server pulls inbound messages to TWILIO_NUMBER every poll_interval_seconds
# and routes them through the brain (see twilio_sms.py + server's startup poller).
# Secrets live in .env (Secrets section below). Inbound only — Adam never sends
# SMS out, and there is no delete.
# --- Integrations: Gmail (opt-in, OFF by default) ---------------------------
# A connector to the user's OWN Google Apps Script Gmail bridge (gmail_bridge.gs),
# which runs in the user's Google account. We never hold a Google credential —
# only the random bridge token, which lives in .env as GMAIL_BRIDGE_TOKEN. Reads +
# drafting are the default; SENDING is double-locked: allow_send must be true here
# (default false) AND each send routes through the approval flow. Delete/archive/
# trash is not supported anywhere. See gmail.py.
# --- Integrations: LinkedIn (opt-in, OFF by default) ------------------------
# Two opt-in lanes. DRAFT lane (default): Adam writes posts/profile text the
# user copies into LinkedIn by hand — no credentials, no automation. API
# auto-post lane (advanced, off): BYO LinkedIn Developer App + the user's OWN
# member access token (w_member_social); create_post() publishes ONLY to the
# user's own feed and is approval-gated, never agent-called. There is no shared
# app and no maintainer credential. Account/profile creation is never automated.
# See linkedin.py.
_LI = (_get("integrations", {}) or {}).get("linkedin", {}) or {}
LINKEDIN_ENABLED = bool(_LI.get("enabled", False))
LINKEDIN_API_ENABLED = bool(_LI.get("api_enabled", False))   # auto-post lane
LINKEDIN_CLIENT_ID = str(_LI.get("client_id", "") or "").strip()    # not a secret
LINKEDIN_AUTHOR_URN = str(_LI.get("author_urn", "") or "").strip()  # urn:li:person:XXXX
LINKEDIN_TIMEOUT_SECONDS = int(_LI.get("timeout_seconds", 20))

_GMAIL = (_get("integrations", {}) or {}).get("gmail", {}) or {}
GMAIL_ENABLED = bool(_GMAIL.get("enabled", False))
GMAIL_BRIDGE_URL = str(_GMAIL.get("bridge_url", "") or "").strip()
GMAIL_ALLOW_SEND = bool(_GMAIL.get("allow_send", False))
GMAIL_TIMEOUT_SECONDS = int(_GMAIL.get("timeout_seconds", 20))

_SMS = (_get("integrations", {}) or {}).get("sms", {}) or {}
SMS_ENABLED = bool(_SMS.get("enabled", False))
# Poll cadence: floor enforced in twilio_sms (>=5s) so a misconfig can't hammer the API.
SMS_POLL_INTERVAL_SECONDS = int(_SMS.get("poll_interval_seconds", 15))
SMS_POLL_TIMEOUT_SECONDS = int(_SMS.get("poll_timeout_seconds", 15))

# --- Integrations: Voicemail (opt-in, OFF by default) -----------------------
# AI message-taker for missed calls. The user's carrier forwards unanswered calls
# to TWILIO_NUMBER; Twilio answers with a greeting + records (see voicemail_provision
# .py / the TwiML the wizard deploys), then the server POLLS the Twilio REST API for
# new recordings (twilio_voicemail.py) — no public webhook / ingress, same private
# posture as the SMS add-on. Shares the Twilio creds with SMS (one number can serve
# both Voice and Messaging). Transcription is Twilio's by default; 'whisper' uses a
# local model if installed. Read/transcribe only — never originates a call, never deletes.
_VM = (_get("integrations", {}) or {}).get("voicemail", {}) or {}
VOICEMAIL_ENABLED = bool(_VM.get("enabled", False))
# Poll cadence: floor enforced in twilio_voicemail (>=5s) so a misconfig can't hammer the API.
VOICEMAIL_POLL_INTERVAL_SECONDS = int(_VM.get("poll_interval_seconds", 20))
VOICEMAIL_POLL_TIMEOUT_SECONDS = int(_VM.get("poll_timeout_seconds", 20))
VOICEMAIL_TRANSCRIBE_MODE = str(_VM.get("transcribe_mode", "twilio") or "twilio").strip().lower()
VOICEMAIL_GREETING_TEXT = str(_VM.get("greeting_text", "") or "").strip()
# Owner first name spoken in the default greeting ("…you've reached {owner}'s assistant").
# Falls back to OWNER_NAME elsewhere; non-secret, lives in settings.json.
OWNER_NAME = str(_VM.get("owner_name", "") or "").strip()

# --- Integrations: Hunter Tracker (opt-in, OFF by default) ------------------
# A connector to the user's OWN Hunter dashboard: a Google Sheet they built from
# the shipped hunter_dashboard.gs, with a bound Apps Script that accepts a
# sheet_sync payload (POST) and exposes a read-only verify endpoint (GET). We
# never hold a Google credential — only the random bridge token, which lives in
# .env as HUNTER_TOKEN. The read (verify/board) is safe; the write (sync) routes
# through the approval flow. Delete is not supported. See hunter.py.
_HUN = (_get("integrations", {}) or {}).get("hunter", {}) or {}
HUNTER_ENABLED = bool(_HUN.get("enabled", False))
HUNTER_BRIDGE_URL = str(_HUN.get("bridge_url", "") or "").strip()
HUNTER_TIMEOUT_SECONDS = int(_HUN.get("timeout_seconds", 20))

# --- Secrets (environment / .env ONLY) --------------------------------------
# ADAM_TOKEN is the product token; JARVIS_TOKEN is honored as a fallback so
# pre-rename installs (whose .env survives updates untouched) keep working.
ADAM_TOKEN = (os.environ.get("ADAM_TOKEN", "").strip()
              or os.environ.get("JARVIS_TOKEN", "").strip())
# Calendar bridge token: secret, so .env only (never settings.json, never logged).
CALENDAR_TOKEN = os.environ.get("GOOGLE_CALENDAR_TOKEN", "").strip()
# Hunter bridge token: secret, so .env only (never settings.json, never logged).
HUNTER_TOKEN = os.environ.get("HUNTER_TOKEN", "").strip()
# Gmail bridge token: secret, so .env only (never settings.json, never logged).
GMAIL_TOKEN = os.environ.get("GMAIL_BRIDGE_TOKEN", "").strip()
# LinkedIn API lane secrets: .env ONLY (never settings.json, never logged).
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com").strip()

TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
OWNER_PHONE = os.environ.get("OWNER_PHONE", "").strip()
# Twilio Account SID + the Twilio number to poll for inbound texts. Not secret, but
# kept in .env alongside the Auth Token so all Twilio config is in one place.
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "").strip()
# Public webhook URL Twilio calls; signatures are validated against it. Falls
# back to PUBLIC_BASE_URL + /sms so it's not a personal default.
TWILIO_WEBHOOK_URL = (
    os.environ.get("TWILIO_WEBHOOK_URL", "").strip()
    or (f"{PUBLIC_BASE_URL.rstrip('/')}/sms" if PUBLIC_BASE_URL else "")
)


# --- Live integration refresh ----------------------------------------------

def refresh_integrations(root: str | os.PathLike | None = None) -> bool:
    """Re-read the integration config (settings.json + .env) and update the live,
    in-memory integration constants WITHOUT a server restart. Returns True if the
    reload succeeded, False if it couldn't (in which case nothing is changed and the
    caller should tell the user a restart is needed).

    The one-click "Enable" wizards write settings.json + .env via integration_config,
    then call this so the add-on's status pill flips to ENABLED right away and the
    request-driven connectors (calendar/email/hunter/linkedin — which read these
    `config.*` values at call time) work immediately. Before this existed, the values
    were frozen at import, so an add-on looked "not added" until the server was
    restarted — the most confusing step in setup.

    Poller-based add-ons (SMS, voicemail) still need a restart to START their
    background loop; this only refreshes config/status, it does not launch a poller.

    `root` is the directory holding settings.json + .env — defaults to the project
    root, but the wizard passes the same root integration_config wrote to, so this
    reads exactly what was just written (and stays isolated under tests). Only the
    `integrations.*` blocks and the .env bridge tokens are re-read; paths, permissions,
    and server settings are boot-time concerns and are NOT re-derived."""
    base = Path(root) if root else CONFIG_ROOT
    env_path = base / ".env"
    settings_path = base / "settings.json"
    try:
        # Read settings.json FIRST (the only step that can fail) so a malformed file
        # leaves every live constant untouched — a half-applied refresh is worse than
        # none (the caller falls back to "restart required").
        new_settings: dict = {}
        if settings_path.exists():
            new_settings = json.loads(settings_path.read_text("utf-8-sig"))
            if not isinstance(new_settings, dict):
                new_settings = {}
    except Exception:
        return False

    global _S, _CAL, _GMAIL, _SMS, _VM, _LI, _HUN
    global CALENDAR_ENABLED, CALENDAR_BRIDGE_URL, CALENDAR_DEFAULT_ID, CALENDAR_TIMEOUT_SECONDS
    global GMAIL_ENABLED, GMAIL_BRIDGE_URL, GMAIL_ALLOW_SEND, GMAIL_TIMEOUT_SECONDS
    global SMS_ENABLED, SMS_POLL_INTERVAL_SECONDS, SMS_POLL_TIMEOUT_SECONDS
    global VOICEMAIL_ENABLED, VOICEMAIL_POLL_INTERVAL_SECONDS, VOICEMAIL_POLL_TIMEOUT_SECONDS
    global VOICEMAIL_TRANSCRIBE_MODE, VOICEMAIL_GREETING_TEXT, OWNER_NAME
    global LINKEDIN_ENABLED, LINKEDIN_API_ENABLED, LINKEDIN_CLIENT_ID, LINKEDIN_AUTHOR_URN, LINKEDIN_TIMEOUT_SECONDS
    global HUNTER_ENABLED, HUNTER_BRIDGE_URL, HUNTER_TIMEOUT_SECONDS
    global CALENDAR_TOKEN, HUNTER_TOKEN, GMAIL_TOKEN, LINKEDIN_CLIENT_SECRET, LINKEDIN_ACCESS_TOKEN
    global TWILIO_AUTH_TOKEN, OWNER_PHONE, TWILIO_ACCOUNT_SID, TWILIO_NUMBER

    # Reload .env over the live process env (override=True so a changed token wins).
    if env_path.exists():
        load_dotenv(env_path, override=True)
    _S = new_settings
    ints = (_S.get("integrations") or {}) if isinstance(_S, dict) else {}

    _CAL = ints.get("google_calendar", {}) or {}
    CALENDAR_ENABLED = bool(_CAL.get("enabled", False))
    CALENDAR_BRIDGE_URL = str(_CAL.get("bridge_url", "") or "").strip()
    CALENDAR_DEFAULT_ID = str(_CAL.get("calendar_id", "primary") or "primary").strip()
    CALENDAR_TIMEOUT_SECONDS = int(_CAL.get("timeout_seconds", 20))

    _GMAIL = ints.get("gmail", {}) or {}
    GMAIL_ENABLED = bool(_GMAIL.get("enabled", False))
    GMAIL_BRIDGE_URL = str(_GMAIL.get("bridge_url", "") or "").strip()
    GMAIL_ALLOW_SEND = bool(_GMAIL.get("allow_send", False))
    GMAIL_TIMEOUT_SECONDS = int(_GMAIL.get("timeout_seconds", 20))

    _SMS = ints.get("sms", {}) or {}
    SMS_ENABLED = bool(_SMS.get("enabled", False))
    SMS_POLL_INTERVAL_SECONDS = int(_SMS.get("poll_interval_seconds", 15))
    SMS_POLL_TIMEOUT_SECONDS = int(_SMS.get("poll_timeout_seconds", 15))

    _VM = ints.get("voicemail", {}) or {}
    VOICEMAIL_ENABLED = bool(_VM.get("enabled", False))
    VOICEMAIL_POLL_INTERVAL_SECONDS = int(_VM.get("poll_interval_seconds", 20))
    VOICEMAIL_POLL_TIMEOUT_SECONDS = int(_VM.get("poll_timeout_seconds", 20))
    VOICEMAIL_TRANSCRIBE_MODE = str(_VM.get("transcribe_mode", "twilio") or "twilio").strip().lower()
    VOICEMAIL_GREETING_TEXT = str(_VM.get("greeting_text", "") or "").strip()
    OWNER_NAME = str(_VM.get("owner_name", "") or "").strip()

    _LI = ints.get("linkedin", {}) or {}
    LINKEDIN_ENABLED = bool(_LI.get("enabled", False))
    LINKEDIN_API_ENABLED = bool(_LI.get("api_enabled", False))
    LINKEDIN_CLIENT_ID = str(_LI.get("client_id", "") or "").strip()
    LINKEDIN_AUTHOR_URN = str(_LI.get("author_urn", "") or "").strip()
    LINKEDIN_TIMEOUT_SECONDS = int(_LI.get("timeout_seconds", 20))

    _HUN = ints.get("hunter", {}) or {}
    HUNTER_ENABLED = bool(_HUN.get("enabled", False))
    HUNTER_BRIDGE_URL = str(_HUN.get("bridge_url", "") or "").strip()
    HUNTER_TIMEOUT_SECONDS = int(_HUN.get("timeout_seconds", 20))

    # .env-only secrets / Twilio identifiers.
    CALENDAR_TOKEN = os.environ.get("GOOGLE_CALENDAR_TOKEN", "").strip()
    HUNTER_TOKEN = os.environ.get("HUNTER_TOKEN", "").strip()
    GMAIL_TOKEN = os.environ.get("GMAIL_BRIDGE_TOKEN", "").strip()
    LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "").strip()
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    OWNER_PHONE = os.environ.get("OWNER_PHONE", "").strip()
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "").strip()
    return True


# --- Helpers ----------------------------------------------------------------

def ensure_dirs() -> None:
    """Create the runtime directories if missing. Called at startup so a fresh
    clone needs no manual mkdir."""
    for d in (DATA_DIR, UPLOAD_DIR, LOG_DIR, STATE_DIR, BACKUP_DIR,
              AGENT_WORKSPACE, DRAFTS_DIR, OUTPUTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # Pre-create the configured write directories so a fresh clone can write
    # drafts/outputs immediately without a manual mkdir.
    for d in PERM_WRITE_DIRS:
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass  # an external/unwritable target is fine to skip; checks still apply


def validate() -> None:
    """Fail fast on misconfiguration that would otherwise surface as a confusing
    runtime error. Secrets that are merely optional (Twilio, push) are not required."""
    if not ADAM_TOKEN:
        raise RuntimeError(
            "ADAM_TOKEN missing. Copy .env.example to .env and set it."
        )
    if not CLAUDE_EXE:
        raise RuntimeError(
            "Claude executable not found. Install Claude Code, or set "
            '"claude_exe" in settings.json to its full path.'
        )


def safe_summary() -> dict:
    """Non-secret config snapshot for /health and startup logging. Never includes
    tokens or key material."""
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "claude_configured": bool(CLAUDE_EXE),
        "claude_exe": CLAUDE_EXE,
        "vault_configured": bool(VAULT_PATH) and Path(VAULT_PATH).exists(),
        "vault_path": VAULT_PATH,
        "voice_model": VOICE_MODEL,
        "host": HOST,
        "port": PORT,
        "public_base_url": PUBLIC_BASE_URL or None,
        "cors_allowed_origins": CORS_ALLOWED_ORIGINS,
        "tts_url": TTS_URL,
        "push_enabled": bool(VAPID_PUBLIC_KEY),
        "twilio_enabled": bool(SMS_ENABLED and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_NUMBER),
        "voicemail_enabled": bool(VOICEMAIL_ENABLED and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_NUMBER),
        # Calendar: a single bool. Never the bridge URL or the token.
        "calendar_enabled": bool(CALENDAR_ENABLED and CALENDAR_BRIDGE_URL and CALENDAR_TOKEN),
        "hunter_enabled": bool(HUNTER_ENABLED and HUNTER_BRIDGE_URL and HUNTER_TOKEN),
        # Email: booleans only. Never the bridge URL or the token.
        "email_enabled": bool(GMAIL_ENABLED and GMAIL_BRIDGE_URL and GMAIL_TOKEN),
        "email_send_allowed": bool(GMAIL_ENABLED and GMAIL_ALLOW_SEND),
        # LinkedIn: booleans only. Never the token/secret/URN value.
        "linkedin_enabled": bool(LINKEDIN_ENABLED),
        "linkedin_api_enabled": bool(LINKEDIN_API_ENABLED and LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN),
        "data_dir": str(DATA_DIR),
        "log_file": str(LOG_FILE),
        "jobs_db": str(JOBS_DB),
        "state_schema_version": STATE_SCHEMA_VERSION,
        "permissions": permission_summary(),
        "agent_safety": agent_safety_summary(),
    }


def agent_safety_summary() -> dict:
    """Non-secret snapshot of how Claude is allowed to act (Phase 3)."""
    return {
        "capability_tier": CAPABILITY_TIER or "custom",
        "mode": AGENT_MODE,
        "direct_claude_writes": AGENT_DIRECT_WRITES,
        "tools_restricted": AGENT_RESTRICT_TOOLS,
        "denied_tools": AGENT_DENIED_TOOLS if AGENT_RESTRICT_TOOLS else [],
        "force_outputs_to_drafts": FORCE_OUTPUTS_TO_DRAFTS,
        "require_server_applied_writes": REQUIRE_SERVER_APPLIED_WRITES,
        "brain_write": BRAIN_WRITE_ENABLED,
        "agent_workspace": str(AGENT_WORKSPACE),
        "drafts_dir": str(DRAFTS_DIR),
        "outputs_dir": str(OUTPUTS_DIR),
        "unsafe_legacy_mode": AGENT_MODE == "legacy_direct",
        "code_mode_allowed": AGENT_ALLOW_CODE_MODE,
        "code_mode_dirs": AGENT_CODE_MODE_DIRS,
    }


def permission_summary() -> dict:
    """Non-secret snapshot of the active permission policy, for /health, startup
    logging, and operator verification."""
    return {
        "read_dirs": PERM_READ_DIRS,
        "write_dirs": PERM_WRITE_DIRS,
        "allowed_commands": PERM_ALLOWED_COMMANDS,
        "require_approval_for_writes": PERM_REQUIRE_APPROVAL_WRITES,
        "require_approval_for_destructive": PERM_REQUIRE_APPROVAL_DESTRUCTIVE,
        "backup_before_write": PERM_BACKUP_BEFORE_WRITE,
        "audit_log_enabled": PERM_AUDIT_LOG_ENABLED,
        "allow_shell": PERM_ALLOW_SHELL,
        "allow_network_tools": PERM_ALLOW_NETWORK_TOOLS,
        "max_file_size_mb": PERM_MAX_FILE_SIZE_MB,
        "blocked_paths": PERM_BLOCKED_PATHS,
        "protected_file_patterns": PERM_PROTECTED_FILE_PATTERNS,
        "audit_log_file": str(AUDIT_LOG_FILE),
        "backup_dir": str(BACKUP_DIR),
    }
