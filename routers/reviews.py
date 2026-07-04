"""Review/safety routes: controlled-action approvals, proposed file changes,
ui-prefs, the capability tier, undo, and the read-only audit viewer. The server
is the sole writer — Claude proposes; these routes are how the user disposes."""

from __future__ import annotations

import asyncio
import json
from collections import deque

from fastapi import APIRouter, Depends, HTTPException

import approvals
import config
import external_actions
import integration_config
import merge
import permissions
import proposed_changes
from models import (
    ActionProposeRequest,
    AiPlanBody,
    ApprovalCreate,
    CapabilityTierBody,
    ProposedChangeCreate,
    UiPrefs,
)
import usage_store
from security import require_token

import server

router = APIRouter()


# --- Controlled-action approvals (Level 3) ----------------------------------
# When a write/command would need a human yes/no, it's parked as a pending
# approval. The owner lists and resolves them here (and, later, from the PWA).

@router.get("/approvals", dependencies=[Depends(require_token)])
async def list_approvals(status: str | None = None):
    """List approvals, newest first. Optional ?status=pending|approved|denied|expired."""
    items = approvals.list_all(status=status)
    return {"approvals": items, "count": len(items),
            "pending": len([r for r in items if r["status"] == "pending"])}


@router.post("/approvals", dependencies=[Depends(require_token)])
async def create_approval(body: ApprovalCreate):
    """Park a new pending approval. (Used by write/command helpers, and directly
    for testing the flow.)"""
    rec = approvals.create(
        action_summary=body.action_summary,
        action_type=body.action_type,
        risk_level=body.risk_level,
        target=body.target,
        session_id=body.session_id,
        job_id=body.job_id,
    )
    return rec


@router.post("/integrations/actions/propose", dependencies=[Depends(require_token)])
async def propose_action(body: ActionProposeRequest):
    """Stage an external action (calendar event, email draft, …) for approval. The
    action is validated against the registry and the add-on must be enabled; it is
    then parked as a pending approval carrying the payload. NOTHING executes here —
    execution happens only when the user approves. 400 unknown type; 409 add-on off."""
    if not external_actions.is_known(body.action_type):
        raise HTTPException(status_code=400, detail=f"Unknown action type: {body.action_type}")
    if not external_actions.available(body.action_type):
        raise HTTPException(
            status_code=409,
            detail=f"{body.action_type} is not available — enable its add-on in Settings -> Add-ons.",
        )
    summary = (body.summary or external_actions.label_for(body.action_type)).strip()
    rec = approvals.create(
        action_summary=summary,
        action_type=body.action_type,
        risk_level=external_actions.risk_for(body.action_type),
        target=body.target,
        payload=body.payload,
    )
    return rec


@router.post("/approvals/{approval_id}/approve", dependencies=[Depends(require_token)])
async def approve_approval(approval_id: str):
    """Approve a pending action. 404 if unknown; 409 if already resolved/expired.

    If the approval carries a known EXTERNAL ACTION (it has a payload and a
    registered action_type), the server executes it now — the user's approval is
    the gate, and the server is the sole writer (the agent never executes). The
    execution outcome is recorded on the returned record under `execution`. A
    failed execution does NOT un-approve the record; it returns 200 with the
    error so the UI can show what went wrong."""
    rec = approvals.approve(approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown approval")
    if rec["status"] != "approved":
        raise HTTPException(status_code=409, detail=f"Approval is {rec['status']}")

    # Execute on approval iff this is a registered external action with a payload.
    if external_actions.is_known(rec.get("action_type", "")) and rec.get("payload"):
        try:
            result = await asyncio.to_thread(
                external_actions.execute, rec["action_type"], rec["payload"]
            )
            rec = approvals.record_execution(approval_id, ok=True, result=result) or rec
        except external_actions.ActionError as e:
            # Connector/availability/payload error — surface it, keep the approval.
            rec = approvals.record_execution(approval_id, ok=False, error=str(e)) or rec
        except Exception:  # pragma: no cover — unexpected; never leak internals
            server.log.exception("external action execution failed for %s", approval_id)
            rec = approvals.record_execution(approval_id, ok=False, error="execution failed") or rec
    return rec


@router.post("/approvals/{approval_id}/deny", dependencies=[Depends(require_token)])
async def deny_approval(approval_id: str):
    """Deny a pending action. 404 if unknown; 409 if already resolved/expired."""
    rec = approvals.deny(approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown approval")
    if rec["status"] != "denied":
        raise HTTPException(status_code=409, detail=f"Approval is {rec['status']}")
    return rec


@router.post("/approvals/{approval_id}/dismiss", dependencies=[Depends(require_token)])
async def dismiss_approval(approval_id: str):
    """Clear an approval from the panel (remove the record). 404 if unknown."""
    rec = approvals.dismiss(approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown approval")
    return rec


# --- Proposed changes (Phase 3 controlled-write flow) -----------------------
# A proposed change carries a target path + action + content. The server applies
# it (and only the server) after approval, with permission checks, backup, and
# audit. Claude proposes; the server enforces.

@router.get("/proposed-changes", dependencies=[Depends(require_token)])
async def list_proposed_changes(status: str | None = None):
    """List proposed changes (content omitted), newest first. Optional ?status=."""
    items = proposed_changes.list_all(status=status)
    return {"proposed_changes": items, "count": len(items),
            "pending": len([r for r in items if r["status"] == "pending"])}


@router.get("/ui-prefs", dependencies=[Depends(require_token)])
async def get_ui_prefs():
    """Current user-togglable runtime prefs (auto-apply file changes; auto-run
    calendar actions; auto-sync the Hunter dashboard)."""
    return {"auto_apply_proposed": server._get_auto_apply(),
            "auto_run_calendar": server._get_auto_run_calendar(),
            "auto_run_hunter": server._get_auto_run_hunter(),
            # Read-only capability fact (set in settings.json, not togglable here):
            # tells the PWA whether the long-press Claude Code escalation exists.
            "code_mode_allowed": config.AGENT_ALLOW_CODE_MODE}


@router.post("/ui-prefs", dependencies=[Depends(require_token)])
async def set_ui_prefs(body: UiPrefs):
    """Set runtime prefs. Each posture change is audited (the actions themselves are
    still individually audited when they run). Only the fields the client sends are
    changed."""
    if body.auto_apply_proposed is not None:
        prev = server._get_auto_apply()
        server._set_auto_apply(body.auto_apply_proposed)
        if prev != body.auto_apply_proposed:
            permissions.record_audit_event({
                "action_type": "auto_apply_setting_changed",
                "auto_apply_proposed": body.auto_apply_proposed,
            })
    if body.auto_run_calendar is not None:
        prev = server._get_auto_run_calendar()
        server._set_auto_run_calendar(body.auto_run_calendar)
        if prev != body.auto_run_calendar:
            permissions.record_audit_event({
                "action_type": "auto_run_calendar_setting_changed",
                "auto_run_calendar": body.auto_run_calendar,
            })
    if body.auto_run_hunter is not None:
        prev = server._get_auto_run_hunter()
        server._set_auto_run_hunter(body.auto_run_hunter)
        if prev != body.auto_run_hunter:
            permissions.record_audit_event({
                "action_type": "auto_run_hunter_setting_changed",
                "auto_run_hunter": body.auto_run_hunter,
            })
    return {"auto_apply_proposed": server._get_auto_apply(),
            "auto_run_calendar": server._get_auto_run_calendar(),
            "auto_run_hunter": server._get_auto_run_hunter()}


# NOTE: the standalone /self-edit toggle endpoints were removed in 0.9.17. Self-edit
# is now governed solely by the capability tier (see /capability-tier); Unrestricted
# turns it on. Custom mode still reads permissions.allow_app_self_edit from settings.json.


@router.get("/capability-tier", dependencies=[Depends(require_token)])
async def get_capability_tier():
    """Current capability tier (safe / powerful / unrestricted, or 'custom' when the
    granular agent_safety/permissions are hand-tuned) plus the resulting posture."""
    return {
        "tier": config.CAPABILITY_TIER or "custom",
        "agent_safety": config.agent_safety_summary(),
        "permissions": config.permission_summary(),
    }


@router.post("/capability-tier", dependencies=[Depends(require_token)])
async def set_capability_tier(body: CapabilityTierBody):
    """Set the capability tier. Persists to settings.json (atomic + backed up), clears
    the overlapping runtime prefs so the tier is authoritative, and applies live (no
    restart). 'unrestricted' requires explicit confirmation. The always-on rails
    (backup / audit / secret-protection) are enforced by config at every tier and are
    not affected by this call."""
    tier = (body.tier or "").strip().lower()
    if tier not in ("safe", "powerful", "unrestricted"):
        raise HTTPException(status_code=400, detail="tier must be safe, powerful, or unrestricted")
    if tier == "unrestricted" and not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="unrestricted requires explicit confirmation (confirm=true)",
        )
    prev = config.CAPABILITY_TIER or "custom"
    # 1. Persist the tier (comma/bracket-proof, atomic, backed up).
    integration_config.set_settings_top_level("capability_tier", tier)
    # 2. Clear the overlapping runtime prefs so the tier governs — a stale self-edit or
    #    auto-apply pref must never override the tier's posture.
    prefs = server._load_ui_prefs()
    prefs.pop("allow_app_self_edit", None)
    prefs.pop("auto_apply_proposed", None)
    server._save_ui_prefs(prefs)
    # 3. Apply live (no restart): the spawn + permission layer read these at runtime.
    config.CAPABILITY_TIER = tier
    config._apply_capability_tier()
    permissions.record_audit_event({
        "action_type": "capability_tier_changed",
        "from": prev, "to": tier,
    })
    return {"tier": config.CAPABILITY_TIER or "custom",
            "agent_safety": config.agent_safety_summary()}


# --- AI plan (the two-door choice: subscription vs pay-as-you-go) ------------
# 'subscription' = the CLI's own Claude login covers usage at the plan's flat
# rate. 'api_key' = the user's Anthropic API key, prepaid pay-as-you-go, with
# the budget governor + cost meter keeping it bounded. The key itself lives in
# .env and is injected only into the claude.exe subprocess env (run_claude).

def _ai_plan_state() -> dict:
    try:
        usage = usage_store.summary()
    except Exception:  # noqa: BLE001 — the meter must never break the settings page
        usage = {}
    return {
        "mode": config.AUTH_MODE,
        "model": config.VOICE_MODEL,
        "models": list(config.KNOWN_MODELS),
        "recommended": dict(config.RECOMMENDED_MODEL),
        "budget_usd": config.API_BUDGET_MONTHLY_USD,
        "key_set": bool(config.ANTHROPIC_API_KEY),
        "usage": usage,
    }


@router.get("/ai-plan", dependencies=[Depends(require_token)])
async def get_ai_plan():
    """Current AI plan: door, model, budget, whether a key is stored (never the
    key itself), and the cost-meter numbers."""
    return _ai_plan_state()


@router.post("/ai-plan", dependencies=[Depends(require_token)])
async def set_ai_plan(body: AiPlanBody):
    """Update any subset of the AI plan. Persists to settings.json / .env (atomic,
    backed up) and applies live — the next turn runs under the new plan. The API
    key is write-only: stored to .env, surfaced afterwards only as key_set=true."""
    known_ids = {m["id"] for m in config.KNOWN_MODELS}

    mode = (body.mode or "").strip().lower() or None
    if mode is not None and mode not in ("subscription", "api_key"):
        raise HTTPException(status_code=400, detail="mode must be subscription or api_key")

    model = (body.model or "").strip() or None
    if model is not None and model not in known_ids:
        raise HTTPException(status_code=400,
                            detail=f"model must be one of: {', '.join(sorted(known_ids))}")

    key = (body.api_key or "").strip() or None
    if key is not None and (not key.startswith("sk-ant-") or len(key) < 20):
        raise HTTPException(status_code=400,
                            detail="That doesn't look like an Anthropic API key "
                                   "(they start with sk-ant-).")

    budget = body.budget_usd
    if budget is not None and not (0 <= float(budget) <= 10000):
        raise HTTPException(status_code=400, detail="budget_usd must be between 0 and 10000")

    # Switching to pay-as-you-go without a key (stored or provided) is a dead end
    # — every turn would fail auth. Refuse with the fix named.
    if mode == "api_key" and not key and not config.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400,
                            detail="Add your Anthropic API key to switch to pay-as-you-go.")

    prev_mode = config.AUTH_MODE

    if key is not None:
        # .env is the only place the key persists; config holds it for the spawn.
        # It is deliberately NOT put in os.environ (see config's pop-on-load note).
        integration_config.set_env_var("ANTHROPIC_API_KEY", key,
                                       section_header="# AI plan (pay-as-you-go)")
        config.ANTHROPIC_API_KEY = key

    if mode is not None and mode != prev_mode:
        integration_config.set_settings_top_level("auth_mode", mode)
        config.AUTH_MODE = mode
        # Each door has a recommended model (Opus on flat-rate, Sonnet on
        # pay-as-you-go). Apply it on a door switch unless this same request
        # pins a model explicitly — the picker stays fully user-overridable.
        if model is None:
            model = config.RECOMMENDED_MODEL[mode]

    if model is not None and model != config.VOICE_MODEL:
        integration_config.set_settings_top_level("voice_model", model)
        config.VOICE_MODEL = model

    if budget is not None:
        integration_config.set_settings_top_level("api_budget_monthly_usd", float(budget))
        config.API_BUDGET_MONTHLY_USD = float(budget)

    permissions.record_audit_event({
        "action_type": "ai_plan_changed",
        "from": prev_mode, "to": config.AUTH_MODE,
        "model": config.VOICE_MODEL,
        "budget_usd": config.API_BUDGET_MONTHLY_USD,
        "key_updated": key is not None,   # never the key itself
    })
    return _ai_plan_state()


@router.post("/undo-last", dependencies=[Depends(require_token)])
async def undo_last_change():
    """Undo the most recently applied change (restore its backup, or remove a created file)."""
    rec = proposed_changes.undo_last()
    if rec is None:
        raise HTTPException(status_code=404, detail="Nothing to undo")
    if not rec.get("ok", True):
        raise HTTPException(status_code=500, detail=rec.get("error") or "Undo failed")
    permissions.record_audit_event({
        "action_type": "change_undone",
        "target": rec.get("target_path"),
        "restored_from": rec.get("backup_path"),
    })
    return rec


@router.get("/proposed-changes/{change_id}", dependencies=[Depends(require_token)])
async def get_proposed_change(change_id: str):
    """Full proposed change, including the proposed content."""
    rec = proposed_changes.get(change_id, include_content=True)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    return rec


@router.post("/proposed-changes/{change_id}/dismiss", dependencies=[Depends(require_token)])
async def dismiss_proposed_change(change_id: str):
    """Clear a proposed change from the panel (remove the record). Works on any
    status; the audit trail is retained. 404 if unknown."""
    rec = proposed_changes.dismiss(change_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    return rec


@router.post("/proposed-changes", dependencies=[Depends(require_token)])
async def create_proposed_change(body: ProposedChangeCreate):
    """Record a new proposed change in 'pending'."""
    try:
        return proposed_changes.create(
            target_path=body.target_path, action=body.action, content=body.content,
            new_path=body.new_path, summary=body.summary, risk_level=body.risk_level,
            session_id=body.session_id, job_id=body.job_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposed-changes/{change_id}/approve", dependencies=[Depends(require_token)])
async def approve_proposed_change(change_id: str):
    """Approve a pending change (does not apply it). 404 unknown; 409 if resolved."""
    rec = proposed_changes.approve(change_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    if rec["status"] != "approved":
        raise HTTPException(status_code=409, detail=f"Change is {rec['status']}")
    return rec


@router.post("/proposed-changes/{change_id}/deny", dependencies=[Depends(require_token)])
async def deny_proposed_change(change_id: str):
    """Deny a pending change. 404 unknown; 409 if already resolved."""
    rec = proposed_changes.deny(change_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    if rec["status"] != "denied":
        raise HTTPException(status_code=409, detail=f"Change is {rec['status']}")
    return rec


@router.post("/proposed-changes/{change_id}/apply", dependencies=[Depends(require_token)])
async def apply_proposed_change(change_id: str):
    """Apply an APPROVED change to disk (server-enforced: permission check,
    staleness/conflict check, backup, audit). 404 unknown; 409 if not approved or
    if the target changed since the proposal (conflict); 422 if otherwise refused.

    On conflict the body still carries the record (status=conflict, conflict_reason)
    so the client can show the user why and offer Refresh."""
    # apply() may boot a blocking self-edit health probe (subprocess, up to 40s);
    # run it off the event loop so the server stays responsive during a self-edit.
    rec, err = await asyncio.to_thread(proposed_changes.apply, change_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    if err:
        status = (rec or {}).get("status")
        # A self-edit that was auto-rolled-back is a RESOLVED outcome, not a request
        # error: the change was reverted and the app left in its last-good state.
        # Return the record (200) so the UI can show "rolled back — why" instead of
        # an exception. The 4th rail already restored + re-probed before we got here.
        if status == "rolled_back":
            return rec
        if status == "conflict" or "not approved" in err:
            code = 409
        else:
            code = 422
        raise HTTPException(status_code=code, detail=err)
    # If this change resolved a held update conflict, clear it (and its review copies).
    if rec.get("status") == "applied":
        merge.resolve_for_target(rec.get("target_path"))
    return rec


@router.get("/proposed-changes/{change_id}/diff", dependencies=[Depends(require_token)])
async def get_proposed_change_diff(change_id: str):
    """The review payload for one change: the unified diff plus hashes/sizes and
    availability. 404 if unknown."""
    rec = proposed_changes.get(change_id, include_content=False)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    return {
        "id": rec["id"],
        "action": rec.get("action"),
        "target_path": rec.get("target_path"),
        "new_path": rec.get("new_path"),
        "status": rec.get("status"),
        "diff": rec.get("diff"),
        "diff_available": rec.get("diff_available"),
        "diff_unavailable_reason": rec.get("diff_unavailable_reason"),
        "diff_created_at": rec.get("diff_created_at"),
        "original_hash": rec.get("original_hash"),
        "original_size": rec.get("original_size"),
        "proposed_hash": rec.get("proposed_hash"),
        "proposed_size": rec.get("proposed_size"),
    }


@router.get("/proposed-changes/{change_id}/status", dependencies=[Depends(require_token)])
async def get_proposed_change_status(change_id: str):
    """Live status of one change, including a fresh staleness check against the
    file on disk (read-only — does not mutate the record). 404 if unknown."""
    out = proposed_changes.status_preview(change_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    return out


@router.post("/proposed-changes/{change_id}/refresh-diff", dependencies=[Depends(require_token)])
async def refresh_proposed_change_diff(change_id: str):
    """Re-baseline a change against the current file and regenerate its diff. The
    proposed content is unchanged; the record is reset to 'pending' so it must be
    re-approved. 404 unknown; 409 if the change is terminal (applied/expired)."""
    rec = proposed_changes.refresh_diff(change_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Unknown proposed change")
    if rec["status"] not in ("pending",):
        raise HTTPException(status_code=409, detail=f"Change is {rec['status']}")
    return rec


# --- Read-only audit viewer (v0.8.1) ----------------------------------------
# A bounded, defensively-parsed, field-whitelisted view of the structured audit
# log. Read-only and additive: it surfaces the SAME audit.jsonl the permission +
# proposed-change lifecycle already writes. It reads ONLY config.AUDIT_LOG_FILE
# (no path parameter, no traversal surface) and never exposes the raw server log.

# Only these keys are ever returned. Everything else in a record is dropped, so a
# secret accidentally written into a future audit field can never surface here.
_AUDIT_FIELDS = (
    "ts", "action_type", "target", "change_action",
    "allowed", "requires_approval", "approved",
    "risk", "destructive", "reason",
    "session_id", "job_id", "proposed_change_id",
    "backup_path", "original_hash", "current_hash",
)
_AUDIT_LIMIT_DEFAULT = 100
_AUDIT_LIMIT_CAP = 500


def _project_audit_record(obj) -> dict:
    """Whitelist one parsed record down to known-safe fields. A non-dict line
    (any JSON value that isn't an object) is treated as malformed: a safe marker
    with NO raw content is returned instead."""
    if not isinstance(obj, dict):
        return {"_parse_error": True}
    return {k: obj[k] for k in _AUDIT_FIELDS if k in obj}


@router.get("/audit", dependencies=[Depends(require_token)])
async def get_audit(limit: int = _AUDIT_LIMIT_DEFAULT):
    """Read-only tail of the structured audit log (config.AUDIT_LOG_FILE).

    Newest-first. `?limit=` defaults to 100 and is clamped to [1, 500]. Reads a
    bounded tail only (a fixed-size deque, so memory stays bounded on a large
    file). Each line is parsed defensively: a malformed line becomes a safe
    `{"_parse_error": true}` marker — its raw text is never echoed. Returned
    fields are whitelisted; unknown keys (and any secret-like field) are dropped.

    Missing/disabled audit -> 200 with empty entries and available=false.
    Empty file -> 200 with empty entries and available=true."""
    eff_limit = max(1, min(int(limit), _AUDIT_LIMIT_CAP))
    enabled = bool(config.PERM_AUDIT_LOG_ENABLED)
    path = config.AUDIT_LOG_FILE
    base = {"enabled": enabled, "limit": eff_limit, "count": 0,
            "malformed": 0, "entries": []}

    if not enabled or not path.exists():
        return {**base, "available": False}

    # Bounded tail: keep only the last eff_limit raw lines in memory.
    try:
        tail: deque = deque(maxlen=eff_limit)
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    tail.append(line)
    except OSError:
        # File vanished or unreadable between the exists() check and the open.
        return {**base, "available": False}

    entries = []
    malformed = 0
    for raw in tail:
        try:
            rec = _project_audit_record(json.loads(raw))
        except (ValueError, TypeError):
            rec = {"_parse_error": True}
        if rec.get("_parse_error"):
            malformed += 1
        entries.append(rec)

    entries.reverse()  # newest-first
    return {"available": True, "enabled": enabled, "limit": eff_limit,
            "count": len(entries), "malformed": malformed, "entries": entries}
