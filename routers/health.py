"""
Health Tracker routes (Phase H1b) — the private, local health engine's JSON API.

Log weight, meals (with macros), water, and daily wearable metrics; set targets; read a
computed daily summary (macro rings vs targets, weight + trend) and trends; and
manage/remove entries to fix mistakes. Every displayed number is computed by
health_metrics from health_store — the model never does the arithmetic. All
routes are token-gated. The HTML dashboard + view-menu entry are H1c; the
photo-meal estimator is H2 (see /health/meal-photo). Mirrors routers/finance.py.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

import config
import garmin
import health_import as himport
import health_store as hs
import health_metrics as hm
import integration_config
from models import (
    GarminEnableBody,
    GarminSyncBody,
    HealthCorrectionBody,
    HealthMealBody,
    HealthMealEdit,
    HealthMetricBody,
    HealthPhotoBody,
    HealthTargetsBody,
    HealthTextBody,
    HealthWaterBody,
    HealthWaterEdit,
    HealthWeightBody,
    HealthWeightEdit,
)
from security import require_token

router = APIRouter()

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif"}


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _check_date(date: str | None) -> None:
    """Reject a malformed date before it reaches the store. Edits can move an
    entry between days, so a typo here would strand a row on a date no view
    ever queries."""
    if date is None:
        return
    try:
        time.strptime(date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD.")


# --- Summary + trends -------------------------------------------------------

@router.get("/health/summary", dependencies=[Depends(require_token)])
async def health_summary(date: str | None = None):
    """The whole day's dashboard payload (rings, weight, trends), computed."""
    return hm.summary(hs, date=date or _today())


@router.get("/health/trends", dependencies=[Depends(require_token)])
async def health_trends(since: str | None = None):
    """Weight + daily intake + water series (for the weekly/monthly view)."""
    return {"weight": hm.weight_trend(hs, since=since),
            "intake": hm.intake_trend(hs, since=since),
            "water": hm.water_trend(hs, since=since),
            "water_unit": hs.get_setting("water_unit", "oz"),
            "target_water_ml": hs.get_setting("target_water_ml"),
            "target_water_amount": hm.from_ml(hs.get_setting("target_water_ml"),
                                              hs.get_setting("water_unit", "oz") or "oz")}


@router.get("/health/day", dependencies=[Depends(require_token)])
async def health_day(date: str | None = None):
    """Everything logged on one date — what the tap-a-bar day sheet edits.
    Returns each entry with its id so the sheet can patch or delete in place,
    and `logged` flags so a skipped day is distinguishable from a zero day."""
    d = date or _today()
    _check_date(d)
    meals = hs.list_meals(date=d)
    water = hm.water_rows(hs, date=d)
    weights = [w for w in hs.list_weights(since=d) if w["date"] == d]
    totals = hm.today_totals(hs, d)
    return {
        "date": d,
        "is_today": d == _today(),
        "meals": meals,
        "water": water,
        "water_summary": hm.water_summary(hs, d),
        "weights": weights,
        "totals": totals,
        "targets": hm.targets(hs),
        "daily_metric": hs.get_daily_metric(d),
        "logged": {"meals": bool(meals), "water": bool(water), "weight": bool(weights)},
    }


# --- Weight -----------------------------------------------------------------

@router.post("/health/weight", dependencies=[Depends(require_token)])
async def health_add_weight(body: HealthWeightBody):
    _check_date(body.date)
    wid = hs.add_weight(body.date or _today(), body.weight, unit=body.unit, note=body.note)
    return {"id": wid, "weight": body.weight, "date": body.date or _today()}


@router.post("/health/weight/{weight_id}", dependencies=[Depends(require_token)])
async def health_edit_weight(weight_id: int, body: HealthWeightEdit):
    """Patch a weigh-in — the 'tap the point and fix it' path behind Trends."""
    if hs.get_weight(weight_id) is None:
        raise HTTPException(status_code=404, detail="Unknown weigh-in.")
    _check_date(body.date)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    hs.update_weight(weight_id, **patch)
    return hs.get_weight(weight_id)


@router.get("/health/weights", dependencies=[Depends(require_token)])
async def health_weights(since: str | None = None, limit: int | None = None):
    return {"weights": hs.list_weights(since=since, limit=limit)}


@router.delete("/health/weight", dependencies=[Depends(require_token)])
async def health_delete_weight(id: int):
    if not hs.delete_weight(id):
        raise HTTPException(status_code=404, detail="Unknown weigh-in.")
    return {"deleted": id}


# --- Meals ------------------------------------------------------------------

@router.post("/health/meal", dependencies=[Depends(require_token)])
async def health_add_meal(body: HealthMealBody):
    _check_date(body.date)
    mid = hs.add_meal(
        body.date or _today(), body.name, qty=body.qty, kcal=body.kcal,
        protein_g=body.protein_g, carbs_g=body.carbs_g, fat_g=body.fat_g,
        source=body.source, note=body.note,
    )
    return hs.get_meal(mid)


@router.get("/health/meals", dependencies=[Depends(require_token)])
async def health_meals(date: str | None = None, month: str | None = None,
                       limit: int | None = None):
    return {"meals": hs.list_meals(date=date, month=month, limit=limit)}


@router.post("/health/meal/{meal_id}", dependencies=[Depends(require_token)])
async def health_edit_meal(meal_id: int, body: HealthMealEdit):
    if hs.get_meal(meal_id) is None:
        raise HTTPException(status_code=404, detail="Unknown meal.")
    _check_date(body.date)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    hs.update_meal(meal_id, **patch)
    return hs.get_meal(meal_id)


@router.delete("/health/meal", dependencies=[Depends(require_token)])
async def health_delete_meal(id: int):
    if not hs.delete_meal(id):
        raise HTTPException(status_code=404, detail="Unknown meal.")
    return {"deleted": id}


# --- Water ------------------------------------------------------------------

@router.post("/health/water", dependencies=[Depends(require_token)])
async def health_add_water(body: HealthWaterBody):
    """Log a drink. The amount arrives in the user's unit and is stored as ml;
    the response carries the day's recomputed water block so a one-tap counter
    can update without a second round-trip."""
    if (body.unit or "oz").lower() not in ("oz", "ml", "cup"):
        raise HTTPException(status_code=400, detail="Unit must be oz, ml, or cup.")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be more than zero.")
    _check_date(body.date)
    date = body.date or _today()
    wid = hs.add_water(date, hm.to_ml(body.amount, body.unit), note=body.note)
    return {"id": wid, "date": date, "water": hm.water_summary(hs, date)}


@router.get("/health/waters", dependencies=[Depends(require_token)])
async def health_waters(date: str | None = None, since: str | None = None,
                        limit: int | None = None):
    """The day's (or period's) drinks, newest first, plus the day's water block."""
    return {"water": hm.water_rows(hs, date=date, since=since, limit=limit),
            "summary": hm.water_summary(hs, date or _today())}


@router.post("/health/water/{water_id}", dependencies=[Depends(require_token)])
async def health_edit_water(water_id: int, body: HealthWaterEdit):
    """Patch a drink. `amount` arrives in the user's unit and is converted to ml
    so an edited row sums identically to a freshly logged one."""
    row = hs.get_water(water_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown water entry.")
    _check_date(body.date)
    unit = (body.unit or "oz").lower()
    if unit not in ("oz", "ml", "cup"):
        raise HTTPException(status_code=400, detail="Unit must be oz, ml, or cup.")
    patch: dict = {}
    if body.date is not None:
        patch["date"] = body.date
    if body.note is not None:
        patch["note"] = body.note
    if body.amount is not None:
        if body.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be more than zero.")
        patch["ml"] = hm.to_ml(body.amount, unit)
    hs.update_water(water_id, **patch)
    out = hs.get_water(water_id)
    return {"water_row": out, "water": hm.water_summary(hs, out["date"])}


@router.delete("/health/water", dependencies=[Depends(require_token)])
async def health_delete_water(id: int):
    """Remove one logged drink — the 'undo' behind the counter."""
    if not hs.delete_water(id):
        raise HTTPException(status_code=404, detail="Unknown water entry.")
    return {"deleted": id, "water": hm.water_summary(hs, _today())}


# --- Daily metrics ----------------------------------------------------------

@router.post("/health/metric", dependencies=[Depends(require_token)])
async def health_set_metric(body: HealthMetricBody):
    date = body.date or _today()
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if k != "date" and v is not None}
    hs.set_daily_metric(date, **fields)
    return hs.get_daily_metric(date)


# --- Targets / settings -----------------------------------------------------

@router.get("/health/targets", dependencies=[Depends(require_token)])
async def health_get_targets():
    return hs.get_settings()


@router.post("/health/targets", dependencies=[Depends(require_token)])
async def health_set_targets(body: HealthTargetsBody):
    sent = body.model_dump(exclude_unset=True)
    for key in ("target_kcal", "target_protein_g", "target_carbs_g",
                "target_fat_g", "target_water_ml", "weight_goal", "weight_unit",
                "water_unit"):
        if key in sent:
            hs.set_setting(key, sent[key])
    return hs.get_settings()


# --- Photo meals (Phase H2) -------------------------------------------------

@router.post("/health/meal-photo", dependencies=[Depends(require_token)])
async def health_meal_photo(body: HealthPhotoBody):
    """Estimate a meal's foods + macros from an uploaded photo. Returns candidate
    rows for the user to confirm/edit — nothing is logged here. The vision step
    runs off the event loop."""
    try:
        rp = Path(body.file_path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file_path.")
    if not (rp.is_file() and config.UPLOAD_DIR.resolve() in rp.parents):
        raise HTTPException(status_code=400, detail="file_path must be an uploaded image.")
    if rp.suffix.lower() not in _IMAGE_SUFFIXES:
        raise HTTPException(status_code=415, detail="That file isn't an image.")
    candidates = await asyncio.to_thread(himport.estimate_meal_photo, str(rp))
    if not candidates:
        raise HTTPException(status_code=422, detail="Couldn't recognize any food in that photo.")
    return {"candidates": candidates}


@router.post("/health/meal-estimate", dependencies=[Depends(require_token)])
async def health_meal_estimate(body: HealthTextBody):
    """Estimate a meal's macros from a plain-text description ("1 banana"). Returns
    an editable candidate — not logged. The estimate runs off the event loop."""
    if not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="Describe a food first (e.g. 1 banana).")
    cand = await asyncio.to_thread(himport.estimate_meal_text, body.text)
    if not cand:
        raise HTTPException(status_code=422, detail="Couldn't estimate that — enter the macros manually.")
    return cand


@router.post("/health/correction", dependencies=[Depends(require_token)])
async def health_correction(body: HealthCorrectionBody):
    """Remember a food's macros so future photo estimates of it use these numbers."""
    hs.upsert_correction(body.name, body.kcal, body.protein_g, body.carbs_g, body.fat_g)
    return {"name": body.name, "saved": True}


# --- Garmin add-on (Phase H3) -----------------------------------------------

def _garmin_state() -> dict:
    return {
        "status": garmin.status(),
        "enabled": bool(config.GARMIN_ENABLED),
        "has_credentials": bool(config.GARMIN_EMAIL and config.GARMIN_PASSWORD),
        "library_installed": garmin.library_installed(),
    }


@router.get("/health/garmin/status", dependencies=[Depends(require_token)])
async def garmin_status():
    """Whether the Garmin add-on is enabled/configured and its library present.
    Never returns the credentials."""
    return _garmin_state()


@router.post("/health/garmin/enable", dependencies=[Depends(require_token)])
async def garmin_enable(body: GarminEnableBody):
    """Turn the Garmin add-on on/off. Credentials are written to .env only; the
    enabled flag to settings.json. Applies live (no restart). Never echoes the
    password back."""
    if body.email:
        integration_config.set_env_var("GARMIN_EMAIL", body.email.strip(),
                                       section_header="# Garmin (unofficial health sync)")
    if body.password:
        integration_config.set_env_var("GARMIN_PASSWORD", body.password)
    integration_config.set_settings_integration("garmin", {"enabled": bool(body.enabled)})
    config.refresh_integrations()
    return _garmin_state()


@router.post("/health/garmin/install", dependencies=[Depends(require_token)])
async def garmin_install():
    """One-click install of the optional garminconnect library (so the user never
    needs a terminal). Runs off the event loop; returns the outcome + a short log.
    Always 200 — the body's `ok`/`installed` flags tell the UI what happened."""
    res = await asyncio.to_thread(garmin.install_library)
    return {**res, **_garmin_state()}


@router.post("/health/garmin/sync", dependencies=[Depends(require_token)])
async def garmin_sync(body: GarminSyncBody):
    """Pull the day's Garmin metrics + weight into the health store. 409 with a
    plain fix if the add-on isn't ready (disabled, no creds, or library missing);
    502 if Garmin login/read fails."""
    if not config.GARMIN_ENABLED:
        raise HTTPException(status_code=409, detail="Garmin add-on is off — enable it first.")
    if not (config.GARMIN_EMAIL and config.GARMIN_PASSWORD):
        raise HTTPException(status_code=409, detail="Add your Garmin email + password first.")
    if not garmin.library_installed():
        raise HTTPException(status_code=409,
                            detail="The Garmin library isn't installed. Run: pip install garminconnect")
    try:
        return await asyncio.to_thread(garmin.sync, body.date)
    except garmin.GarminUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:  # noqa: BLE001 — Garmin's unofficial endpoints can fail many ways
        raise HTTPException(status_code=502, detail=f"Garmin sync failed: {e}")


_GARMIN_REFRESH_MIN_S = 600  # throttle open-the-Tracker auto-refresh to once / 10 min


@router.post("/health/garmin/refresh", dependencies=[Depends(require_token)])
async def garmin_refresh(body: GarminSyncBody):
    """Best-effort, throttled Garmin pull for when the Health Tracker opens, so the
    'From your watch' panel shows the day's CURRENT totals — Garmin's daily numbers
    climb through the day, and without this the panel froze on the first sync (the
    reported "wrong watch data"). Unlike /sync (the manual, forceful button), this
    NEVER errors the page: it returns {refreshed, reason} and quietly no-ops when
    the add-on is off, recently synced, or Garmin is unreachable/rate-limited."""
    if not garmin.available():
        return {"refreshed": False, "reason": "not_available"}
    now = time.time()
    try:
        last = float(hs.get_setting("garmin_last_refresh_epoch", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    age = now - last
    if 0 <= age < _GARMIN_REFRESH_MIN_S:
        return {"refreshed": False, "reason": "throttled", "age_s": int(age)}
    # Stamp the attempt up front so a slow/failing Garmin can't be hammered on every
    # page open — both success and failure back off for the interval.
    hs.set_setting("garmin_last_refresh_epoch", now)
    try:
        result = await asyncio.to_thread(garmin.sync, body.date)
        return {"refreshed": True, "metrics": result.get("metrics", {}),
                "errors": result.get("errors", [])}
    except garmin.GarminUnavailable as e:
        return {"refreshed": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001 — Garmin's unofficial endpoints fail many ways
        return {"refreshed": False, "reason": f"sync_failed: {e}"}
