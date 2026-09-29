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

import re
import time

import checklist_store
import config
import google_calendar
import gmail
import health_import
import health_metrics
import health_store
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


def _checklist_create(p: dict) -> dict:
    title = (p.get("title") or "").strip()
    if not title:
        raise ActionError("checklist.create requires a 'title'.")
    raw = p.get("items")
    if raw is not None and not isinstance(raw, list):
        raise ActionError("checklist.create 'items' must be a list.")
    items: list[dict] = []
    for it in (raw or []):
        if isinstance(it, dict):
            text = (it.get("text") or "").strip()
            if text:
                items.append({"text": text, "note": it.get("note", ""), "done": bool(it.get("done"))})
        elif isinstance(it, str) and it.strip():
            items.append({"text": it.strip(), "note": "", "done": False})
    cid = checklist_store.create_checklist(
        title=title, description=p.get("description", ""),
        source=checklist_store.SOURCE_ADAM, items=items,
    )
    return {"checklist_id": cid, "title": title, "items": len(items)}


def _checklist_add_items(p: dict) -> dict:
    cid = p.get("checklist_id")
    if not isinstance(cid, int):
        raise ActionError("checklist.add_items requires an integer 'checklist_id'.")
    raw = p.get("items")
    if not isinstance(raw, list) or not raw:
        raise ActionError("checklist.add_items requires a non-empty 'items' list.")
    if checklist_store.get_checklist(cid) is None:
        raise ActionError(f"No checklist with id {cid}.")
    added = 0
    for it in raw:
        text = (it.get("text") if isinstance(it, dict) else it) or ""
        text = str(text).strip()
        if not text:
            continue
        note = it.get("note", "") if isinstance(it, dict) else ""
        if checklist_store.add_item(cid, text, note=note) is not None:
            added += 1
    return {"checklist_id": cid, "added": added}


def _checklist_archive(p: dict) -> dict:
    """Adam's only delete path, and it is reversible by construction: the list
    moves to the Archive tab, where the user can restore it. Permanent removal
    (purge) is deliberately absent from this registry."""
    cid = p.get("checklist_id")
    if not isinstance(cid, int):
        raise ActionError("checklist.archive requires an integer 'checklist_id'.")
    if not checklist_store.archive_checklist(cid):
        raise ActionError(f"No active checklist with id {cid}.")
    return {"checklist_id": cid, "archived": True, "recoverable": True}


_WATER_UNITS = ("oz", "ml", "cup")
_WATER_MAX_ML = 5000.0   # one logged drink above ~5 L is a misheard number, not water


def _health_water(p: dict) -> dict:
    """Log water into the Health tracker (local SQLite, same store as the Health
    page's counter). `replace_last` corrects the day's most recent drink in place
    instead of adding a second one ("actually make that 16 ounces"), so a spoken
    correction never double-counts. No delete path: a wrong entry is fixed by a
    replace, or removed by the user in the Log tab."""
    try:
        amount = float(p.get("amount"))
    except (TypeError, ValueError):
        raise ActionError("health.water requires a numeric 'amount'.")
    unit = str(p.get("unit") or "").strip().lower()
    unit = {"ounce": "oz", "ounces": "oz", "fl oz": "oz", "milliliter": "ml",
            "milliliters": "ml", "millilitre": "ml", "millilitres": "ml",
            "cups": "cup"}.get(unit, unit)
    if unit not in _WATER_UNITS:
        raise ActionError("health.water 'unit' must be one of oz, ml, cup.")
    ml = health_metrics.to_ml(amount, unit)
    if not 0 < ml <= _WATER_MAX_ML:
        raise ActionError(f"health.water amount out of range: {amount:g} {unit}.")
    date = _action_date(p, "health.water")
    health_store.init()
    replaced = None
    if p.get("replace_last"):
        last = health_store.list_water(date=date, limit=1)
        if last:
            replaced = last[0]
            health_store.update_water(replaced["id"], ml=ml)
    if replaced is None:
        wid = health_store.add_water(date, ml, note=str(p.get("note") or ""))
    else:
        wid = replaced["id"]
    summary = health_metrics.water_summary(health_store, date)
    return {"water_id": wid, "date": date, "logged_ml": round(ml, 1),
            "replaced_ml": round(replaced["ml"], 1) if replaced else None,
            "day_total": summary}


_MEAL_MAX_KCAL = 5000.0   # one logged item above this is a misheard number


def _action_date(p: dict, action: str) -> str:
    date = str(p.get("date") or time.strftime("%Y-%m-%d", time.localtime())).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ActionError(f"{action} 'date' must be YYYY-MM-DD.")
    return date


def _health_meal(p: dict) -> dict:
    """Log food into the Health tracker (same meals table the Health page writes).
    The agent supplies the estimate; Python owns the arithmetic and the learning
    loop, exactly like the page's own estimator: health_import.normalize_candidate
    reconciles kcal against the macros (4/4/9) and apply_corrections swaps in any
    macros the user has corrected for that food before. Accepts one item or
    {"items": [...]}; `replace_last` (single item only) overwrites the day's most
    recent meal instead of adding a duplicate. No delete path."""
    raw_items = p.get("items") if isinstance(p.get("items"), list) else [p]
    if not raw_items:
        raise ActionError("health.meal requires a food ('name') or a non-empty 'items' list.")
    date = _action_date(p, "health.meal")
    replace_last = bool(p.get("replace_last"))
    if replace_last and len(raw_items) != 1:
        raise ActionError("health.meal 'replace_last' takes exactly one item.")
    cands = []
    for it in raw_items:
        if not isinstance(it, dict) or not str(it.get("name") or "").strip():
            raise ActionError("health.meal: every item needs a 'name'.")
        cand = health_import.normalize_candidate(it, source="voice")
        cand = health_import.apply_corrections(cand, health_store)
        nums = [cand[k] for k in ("kcal", "protein_g", "carbs_g", "fat_g")]
        if any(n < 0 for n in nums) or cand["kcal"] > _MEAL_MAX_KCAL or not any(nums):
            raise ActionError(f"health.meal: implausible numbers for {cand['name']!r}.")
        cands.append(cand)
    health_store.init()
    logged, replaced = [], None
    for cand in cands:
        fields = {k: cand[k] for k in ("name", "qty", "kcal", "protein_g", "carbs_g", "fat_g")}
        last = health_store.list_meals(date=date, limit=1) if replace_last else []
        if last:
            replaced = last[0]
            health_store.update_meal(replaced["id"], **fields, source="voice")
            mid = replaced["id"]
        else:
            mid = health_store.add_meal(date, fields.pop("name"), source="voice", **fields)
        logged.append({"meal_id": mid, **{k: cand[k] for k in
                       ("name", "qty", "kcal", "protein_g", "carbs_g", "fat_g")},
                       "corrected": bool(cand.get("corrected")),
                       "kcal_adjusted_from": cand.get("kcal_adjusted")})
    totals = health_metrics.day_summary(health_store, date)["totals"]
    return {"date": date, "logged": logged,
            "replaced": replaced["name"] if replaced else None, "day_totals": totals}


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
    # Checklists are purely local — no third-party service, no secret, no network.
    # `available` is unconditional because there is nothing to configure, and the
    # only delete path here is an archive the user can undo. Purge is absent on
    # purpose: the assistant can never permanently destroy a list.
    "checklist.create": {
        "executor": _checklist_create,
        "available": lambda: True,
        "risk": "low", "brain_proposable": True, "label": "Create a checklist",
    },
    "checklist.add_items": {
        "executor": _checklist_add_items,
        "available": lambda: True,
        "risk": "low", "brain_proposable": True, "label": "Add steps to a checklist",
    },
    "checklist.archive": {
        "executor": _checklist_archive,
        "available": lambda: True,
        "risk": "low", "brain_proposable": True, "label": "Archive a checklist (recoverable)",
    },
    # Health water/meals are local-only like checklists (SQLite on this machine, no
    # connector, no network) and has no delete path — only add or replace-last.
    "health.water": {
        "executor": _health_water,
        "available": lambda: True,
        "risk": "low", "brain_proposable": True, "label": "Log water in the Health tracker",
    },
    "health.meal": {
        "executor": _health_meal,
        "available": lambda: True,
        "risk": "low", "brain_proposable": True, "label": "Log food in the Health tracker",
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
