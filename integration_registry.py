"""
Jarvis Voice Local — add-on (integration) registry.

ONE source of truth for the optional add-ons the product can offer: Calendar,
Hunter, Email, SMS, LinkedIn, ... Each add-on is opt-in, off by default, BYO
credentials, no maintainer account, no secrets in the ZIP, and (when write-
capable) routes through the approval flow.

This module is the contract every add-on conforms to. It powers three things:

  * the Settings "add-on cards" UI                (name + description + status)
  * the `GET /integrations/status` API            (booleans only, no secrets)
  * the brain's add-on awareness                  (which add-ons are enabled, so
                                                   Jarvis redirects to Settings
                                                   instead of faking a capability)

Design rules (do NOT break — the parallel add-on builds depend on them):
  * An entry is pure metadata + a `status_fn`. The status function reads
    `config` and returns one of the STATUS_* strings below. It must NEVER read,
    return, or log a secret value — only whether things are present.
  * `snapshot()` returns plain dicts with booleans/strings only. It is safe to
    serialize to /health, the console, an API response, or a vault state file.
  * Adding an add-on = appending ONE entry here + its own connector module +
    its own setup wizard page + its own tests. It does not require touching any
    other add-on's code.

Status ladder (what a card pill shows, and what the brain keys off):
  * AVAILABLE   — offered but not set up (no creds / disabled). Card: "Add".
  * NEEDS_SETUP — partially configured (some creds present, not fully working).
  * ENABLED     — fully configured and on. The brain may use it.
"""

from __future__ import annotations

from typing import Callable

import config

STATUS_AVAILABLE = "available"
STATUS_NEEDS_SETUP = "needs_setup"
STATUS_ENABLED = "enabled"

# Where an add-on is best SET UP. Setup means copy-pasting API keys, OAuth sign-ins,
# or pasting a Google Apps Script — all painful on a phone — so every current add-on
# is "computer". USING an add-on works from anywhere (the server does the work), so
# this is only about the one-time setup. Per-entry override via the "setup_location"
# key on a registry entry; default is "computer".
SETUP_LOCATION_HINTS = {
    "computer": "Set up on a computer — it needs copy-pasting keys or a Google script "
                "that's painful on a phone. Once it's set up, use it from anywhere, "
                "including your phone.",
    "phone": "Can be set up right on your phone.",
    "either": "Set up on a computer or your phone — whichever's handy.",
}


# --- Per-add-on status functions -------------------------------------------
# Each returns one of the STATUS_* constants from the live config. Secrets are
# only ever tested for presence (bool), never read out.

def _calendar_status() -> str:
    """Calendar is ENABLED only when turned on AND it has both the bridge URL
    and the bridge token. The flag-on-but-missing-creds state is NEEDS_SETUP."""
    has_creds = bool(config.CALENDAR_BRIDGE_URL) and bool(config.CALENDAR_TOKEN)
    if config.CALENDAR_ENABLED and has_creds:
        return STATUS_ENABLED
    if config.CALENDAR_ENABLED or config.CALENDAR_BRIDGE_URL or config.CALENDAR_TOKEN:
        return STATUS_NEEDS_SETUP
    return STATUS_AVAILABLE


def _sms_status() -> str:
    """SMS is ENABLED only when turned on AND it has the Twilio Account SID, Auth
    Token, and number (everything the inbound poller needs). Flag-on-but-missing-
    creds is NEEDS_SETUP. Presence only — never reads a secret value."""
    has_creds = bool(config.TWILIO_ACCOUNT_SID) and bool(config.TWILIO_AUTH_TOKEN) and bool(config.TWILIO_NUMBER)
    if config.SMS_ENABLED and has_creds:
        return STATUS_ENABLED
    if config.SMS_ENABLED or config.TWILIO_ACCOUNT_SID or config.TWILIO_AUTH_TOKEN or config.TWILIO_NUMBER:
        return STATUS_NEEDS_SETUP
    return STATUS_AVAILABLE


def _voicemail_status() -> str:
    """Voicemail is ENABLED only when turned on AND it has the Twilio Account SID,
    Auth Token, and number (everything the recordings poller needs — it shares the
    Twilio creds with SMS). Flag-on-but-missing-creds is NEEDS_SETUP. Presence only —
    never reads a secret value."""
    has_creds = bool(config.TWILIO_ACCOUNT_SID) and bool(config.TWILIO_AUTH_TOKEN) and bool(config.TWILIO_NUMBER)
    if config.VOICEMAIL_ENABLED and has_creds:
        return STATUS_ENABLED
    if config.VOICEMAIL_ENABLED or config.TWILIO_ACCOUNT_SID or config.TWILIO_AUTH_TOKEN or config.TWILIO_NUMBER:
        return STATUS_NEEDS_SETUP
    return STATUS_AVAILABLE


def _email_status() -> str:
    """Email is ENABLED only when turned on AND it has both the bridge URL and
    the bridge token. The flag-on-but-missing-creds state is NEEDS_SETUP."""
    has_creds = bool(config.GMAIL_BRIDGE_URL) and bool(config.GMAIL_TOKEN)
    if config.GMAIL_ENABLED and has_creds:
        return STATUS_ENABLED
    if config.GMAIL_ENABLED or config.GMAIL_BRIDGE_URL or config.GMAIL_TOKEN:
        return STATUS_NEEDS_SETUP
    return STATUS_AVAILABLE


def _linkedin_status() -> str:
    """LinkedIn has two opt-in lanes. Connector OFF -> AVAILABLE. ON but the API
    auto-post lane off -> the DRAFT lane is fully usable -> ENABLED (copy/paste
    posting needs no credential). API lane on too -> ENABLED only when the access
    token AND author URN are present, else NEEDS_SETUP. Presence tested only."""
    if not config.LINKEDIN_ENABLED:
        return STATUS_AVAILABLE
    if not config.LINKEDIN_API_ENABLED:
        return STATUS_ENABLED  # draft lane: copy/paste posting, no credential
    has_creds = bool(config.LINKEDIN_ACCESS_TOKEN) and bool(config.LINKEDIN_AUTHOR_URN)
    return STATUS_ENABLED if has_creds else STATUS_NEEDS_SETUP


def _hunter_status() -> str:
    """Hunter is ENABLED only when turned on AND it has both the bridge URL and
    the bridge token. The flag-on-but-missing-creds state is NEEDS_SETUP."""
    has_creds = bool(config.HUNTER_BRIDGE_URL) and bool(config.HUNTER_TOKEN)
    if config.HUNTER_ENABLED and has_creds:
        return STATUS_ENABLED
    if config.HUNTER_ENABLED or config.HUNTER_BRIDGE_URL or config.HUNTER_TOKEN:
        return STATUS_NEEDS_SETUP
    return STATUS_AVAILABLE


# --- The registry ----------------------------------------------------------
# id            stable key (matches the config `integrations.<id>` block).
# name          human label for the card.
# short_description  one line: what you could DO with it (drives the card + the
#               brain's "what can you do" answer).
# category      coarse grouping ("calendar", "email", "messaging", "gamification",
#               "social"); lets the brain map a request to an add-on.
# setup_path    the focused setup wizard route the card's "Add" button opens.
# requires      human-readable names of what the user must supply (NOT values).
# write_capable whether it can change anything (writes go through approval).
# delete_capable whether it can delete (Calendar is deliberately False).
# status_fn     callable -> STATUS_* from live config.

_ADDONS: list[dict] = [
    {
        "id": "google_calendar",
        "name": "Calendar",
        "short_description": "Let Jarvis read your schedule and add events you approve.",
        "category": "calendar",
        "setup_path": "/setup-calendar",
        "requires": ["bridge_url", "GOOGLE_CALENDAR_TOKEN"],
        "write_capable": True,
        "delete_capable": False,
        "status_fn": _calendar_status,
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "short_description": "Let Jarvis draft LinkedIn posts you approve before they go out.",
        "category": "social",
        "setup_path": "/setup-linkedin",
        "requires": ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_CLIENT_SECRET"],
        "write_capable": True,
        "delete_capable": False,
        "status_fn": _linkedin_status,
    },
    {
        "id": "gmail",
        "name": "Email",
        "short_description": "Let Jarvis read your inbox and draft replies you approve before sending.",
        "category": "email",
        "setup_path": "/setup-email",
        "requires": ["bridge_url", "GMAIL_BRIDGE_TOKEN"],
        "write_capable": True,
        "delete_capable": False,
        "status_fn": _email_status,
    },
    {
        "id": "sms",
        "name": "SMS",
        "short_description": "Text Jarvis hands-free; it acts on the message and replies.",
        "category": "messaging",
        "setup_path": "/setup-sms",
        "requires": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER"],
        "write_capable": False,
        "delete_capable": False,
        "status_fn": _sms_status,
    },
    {
        "id": "voicemail",
        "name": "Voicemail",
        "short_description": "When a call goes unanswered, Jarvis answers in a British voice, takes a message, and texts you the transcript.",
        "category": "messaging",
        "setup_path": "/setup-voicemail",
        "requires": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER"],
        "write_capable": False,
        "delete_capable": False,
        "status_fn": _voicemail_status,
    },
    {
        "id": "hunter",
        "name": "Operating System",
        "short_description": "Let Jarvis track your XP, quests, stats, and rank on your own Operating System dashboard.",
        "category": "gamification",
        "setup_path": "/setup-hunter",
        "requires": ["bridge_url", "HUNTER_TOKEN"],
        "write_capable": True,
        "delete_capable": False,
        "status_fn": _hunter_status,
    },
]


# --- Public surface --------------------------------------------------------

def _entry_snapshot(entry: dict) -> dict:
    """One add-on's public, secret-free view, with its live status merged in."""
    status_fn: Callable[[], str] = entry["status_fn"]
    status = status_fn()
    location = entry.get("setup_location", "computer")
    return {
        "id": entry["id"],
        "name": entry["name"],
        "short_description": entry["short_description"],
        "category": entry["category"],
        "setup_path": entry["setup_path"],
        "requires": list(entry["requires"]),
        "write_capable": bool(entry["write_capable"]),
        "delete_capable": bool(entry.get("delete_capable", False)),
        "status": status,
        "enabled": status == STATUS_ENABLED,
        "setup_location": location,
        "setup_hint": SETUP_LOCATION_HINTS.get(location, SETUP_LOCATION_HINTS["computer"]),
    }


def snapshot() -> list[dict]:
    """All add-ons with live status. Booleans/strings only — safe to serialize
    anywhere (API, /health, console, a vault state file). NEVER contains a
    secret value."""
    return [_entry_snapshot(a) for a in _ADDONS]


def get(addon_id: str) -> dict | None:
    """One add-on's snapshot by id, or None if unknown."""
    for a in _ADDONS:
        if a["id"] == addon_id:
            return _entry_snapshot(a)
    return None


def enabled_ids() -> list[str]:
    """Ids of add-ons that are fully configured and on — what the brain may use."""
    return [a["id"] for a in snapshot() if a["enabled"]]


def ids() -> list[str]:
    """All registered add-on ids, regardless of status."""
    return [a["id"] for a in _ADDONS]
