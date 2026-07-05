"""
Adam — external-action execution lane (the shared write path).

The connectors (calendar, gmail, hunter, linkedin) can perform real-world writes,
but the safety model forbids Claude from executing them: the server is the SOLE
writer and every write needs the user's explicit approval. This module is the one
bridge — the single place a proposed external action is registered, gated, and
(only after the user approves) executed via the right connector.

Flow (mirrors the file-write lane's propose -> approve -> apply):
    propose  ->  approvals.create(action_type=..., payload=..., status="pending")
    approve  ->  the server calls execute(action_type, payload) HERE
    execute  ->  dispatch to the connector executor; return a result or raise

Design rules:
  * Only an action_type in ACTIONS can EVER execute. Unknown -> refused.
  * available() gates on the connector being enabled/configured (plus any extra
    flag, e.g. email send needs GMAIL_ALLOW_SEND, the LinkedIn API lane needs its
    token+URN). A proposal is refused at propose time AND re-checked at execute
    time, so disabling an add-on neutralizes any already-parked action.
  * brain_proposable marks the actions the assistant may stage from a <<ACTION>>
    block. Irreversible/outward ones (email SEND, linkedin POST) are False — they
    must be proposed deliberately by the operator, never auto-staged by the agent.
  * No delete actions exist here (calendar/gmail/hunter have no delete by design).
  * Executors are transport only; they never bypass a connector's own guards.
  * Secret-free: an action's payload carries content (event fields, message body),
    never a token — each connector reads its own secret from config.
"""

from __future__ import annotations

import config
import google_calendar
import gmail
import hunter
import linkedin


class ActionError(RuntimeError):
    """An external action could not be executed. Message carries no secret."""


class UnknownAction(ActionError):
    """The action_type is not in the registry — refused."""


class ActionNotAvailable(ActionError):
    """The action's add-on is not enabled/configured — refused."""


# --- Executors (transport only; validate payload shape, then call the connector) --

def _calendar_create(p: dict) -> dict:
    events = p.get("events")
    if not isinstance(events, list) or not events:
        raise ActionError("calendar.create requires a non-empty 'events' list.")
    return google_calendar.create_events(events)


def _calendar_update(p: dict) -> dict:
    if not p.get("event_id"):
        raise ActionError("calendar.update requires an 'event_id'.")
    if not isinstance(p.get("changes"), dict) or not p["changes"]:
        raise ActionError("calendar.update requires a non-empty 'changes' dict.")
    return google_calendar.update_event(p["event_id"], p["changes"], p.get("calendar_id"))


def _hunter_sync(p: dict) -> dict:
    # Accept either {"payload": {...}} or the sync dict directly.
    payload = p.get("payload", p)
    if not isinstance(payload, dict) or not payload:
        raise ActionError("hunter.sync requires a non-empty payload dict.")
    return hunter.sync(payload)


def _email_draft(p: dict) -> dict:
    for k in ("to", "subject", "body"):
        if not p.get(k):
            raise ActionError(f"email.draft requires '{k}'.")
    return gmail.create_draft(to=p["to"], subject=p["subject"], body=p["body"])


def _email_send(p: dict) -> dict:
    for k in ("to", "subject", "body"):
        if not p.get(k):
            raise ActionError(f"email.send requires '{k}'.")
    return gmail.send_message(to=p["to"], subject=p["subject"], body=p["body"])


def _linkedin_post(p: dict) -> dict:
    if not p.get("text"):
        raise ActionError("linkedin.post requires 'text'.")
    return linkedin.create_post(p["text"])


# --- The registry: the ONLY actions that can execute -----------------------------
# executor       transport callable(payload) -> result
# available      callable() -> bool: the add-on is enabled+configured for THIS action
# risk           default risk level for the parked approval
# brain_proposable  may the assistant stage this from a <<ACTION>> block?
# label          human one-liner for the approval summary / audit

ACTIONS: dict[str, dict] = {
    "calendar.create": {
        "executor": _calendar_create,
        "available": lambda: google_calendar.is_configured(),
        "risk": "medium", "brain_proposable": True, "label": "Add calendar event(s)",
    },
    "calendar.update": {
        "executor": _calendar_update,
        "available": lambda: google_calendar.is_configured(),
        "risk": "medium", "brain_proposable": True, "label": "Edit a calendar event",
    },
    "hunter.sync": {
        "executor": _hunter_sync,
        "available": lambda: hunter.is_configured(),
        "risk": "low", "brain_proposable": True, "label": "Sync the Hunter dashboard",
    },
    "email.draft": {
        "executor": _email_draft,
        "available": lambda: gmail.is_configured(),
        "risk": "medium", "brain_proposable": True, "label": "Draft an email (not sent)",
    },
    # Outward / irreversible — proposed deliberately by the operator, never by the agent.
    "email.send": {
        "executor": _email_send,
        "available": lambda: gmail.is_configured() and bool(config.GMAIL_ALLOW_SEND),
        "risk": "high", "brain_proposable": False, "label": "Send an email",
    },
    "linkedin.post": {
        "executor": _linkedin_post,
        "available": lambda: bool(
            config.LINKEDIN_ENABLED and config.LINKEDIN_API_ENABLED
            and config.LINKEDIN_ACCESS_TOKEN and config.LINKEDIN_AUTHOR_URN
        ),
        "risk": "high", "brain_proposable": False, "label": "Post to LinkedIn",
    },
}


# --- Public surface --------------------------------------------------------------

def is_known(action_type: str) -> bool:
    return action_type in ACTIONS


def available(action_type: str) -> bool:
    a = ACTIONS.get(action_type)
    if a is None:
        return False
    try:
        return bool(a["available"]())
    except Exception:
        return False


def brain_proposable(action_type: str) -> bool:
    """True for actions the assistant may stage from a <<ACTION>> block AND that
    are currently available. Outward/irreversible actions are never auto-staged."""
    a = ACTIONS.get(action_type)
    return bool(a and a.get("brain_proposable")) and available(action_type)


def risk_for(action_type: str) -> str:
    return ACTIONS.get(action_type, {}).get("risk", "medium")


def label_for(action_type: str) -> str:
    return ACTIONS.get(action_type, {}).get("label", action_type)


def known_types() -> list[str]:
    return sorted(ACTIONS)


def execute(action_type: str, payload: dict | None) -> dict:
    """Run an approved external action. Raises UnknownAction for an unregistered
    type, ActionNotAvailable if the add-on is off, or ActionError on a bad payload
    / connector failure. The server calls this ONLY after the user approves."""
    a = ACTIONS.get(action_type)
    if a is None:
        raise UnknownAction(f"unknown action type: {action_type}")
    if not available(action_type):
        raise ActionNotAvailable(
            f"{action_type} is not available — its add-on isn't enabled/configured."
        )
    return a["executor"](dict(payload or {}))
