"""
Adam — deterministic health metrics (Phase H1).

The health counterpart to finance_metrics: every number the Health dashboard
shows — today's calorie/macro totals against targets, protein progress, water
intake vs the daily target, weight and its trend, weekly/monthly series — is
computed HERE in plain Python from
health_store. The model never does the arithmetic (in the photo phase it only
identifies foods + estimates portions; the macro numbers still come from stored
values and this code).

Pure over the store (pass `store`, defaults to health_store) and no wall-clock
dependency — the reference date is always passed in — so results are
reproducible and tests are deterministic.
"""

from __future__ import annotations

import health_store as _default_store

_MACROS = ("kcal", "protein_g", "carbs_g", "fat_g")


def _round(x: float, n: int = 1) -> float:
    return round(float(x), n)


def today_totals(store=_default_store, date: str = "") -> dict:
    """Sum the day's logged meals into kcal + macros, with a meal count."""
    meals = store.list_meals(date=date)
    tot = {m: 0.0 for m in _MACROS}
    for meal in meals:
        for m in _MACROS:
            tot[m] += meal.get(m, 0) or 0
    return {
        "date": date,
        "kcal": _round(tot["kcal"], 0),
        "protein_g": _round(tot["protein_g"]),
        "carbs_g": _round(tot["carbs_g"]),
        "fat_g": _round(tot["fat_g"]),
        "meals": len(meals),
    }


# --- Water ------------------------------------------------------------------
# Rows are stored in millilitres; the user picks a display unit (oz | ml | cup)
# and every conversion happens here, so the UI never does the arithmetic either.

ML_PER_OZ = 29.5735295625      # US fluid ounce
ML_PER_CUP = 8 * ML_PER_OZ     # US cup (8 fl oz)
_UNIT_ML = {"ml": 1.0, "oz": ML_PER_OZ, "cup": ML_PER_CUP}


def to_ml(amount: float, unit: str = "oz") -> float:
    """Convert an amount in the user's unit to millilitres."""
    return float(amount) * _UNIT_ML.get((unit or "oz").lower(), ML_PER_OZ)


def from_ml(ml: float, unit: str = "oz"):
    """Convert millilitres to the user's unit (None passes through)."""
    if ml is None:
        return None
    u = (unit or "oz").lower()
    val = float(ml) / _UNIT_ML.get(u, ML_PER_OZ)
    return _round(val, 0 if u == "ml" else 1)


def water_today(store=_default_store, date: str = "") -> dict:
    """The day's total water in ml, with how many drinks it came from."""
    rows = store.list_water(date=date)
    return {"date": date, "ml": _round(sum(r.get("ml", 0) or 0 for r in rows), 0),
            "logs": len(rows)}


def water_summary(store=_default_store, date: str = "") -> dict:
    """The day's water vs the daily target, in ml AND the user's display unit."""
    s = store.get_settings()
    unit = s.get("water_unit") or "oz"
    tot = water_today(store, date)
    target_ml = s.get("target_water_ml")
    remaining, frac = _progress(tot["ml"], target_ml)
    return {
        "date": date,
        "unit": unit,
        "ml": tot["ml"],
        "amount": from_ml(tot["ml"], unit),
        "logs": tot["logs"],
        "target_ml": target_ml,
        "target_amount": from_ml(target_ml, unit) if target_ml else None,
        "remaining_ml": remaining,
        "remaining_amount": from_ml(remaining, unit) if remaining is not None else None,
        "progress": frac,
    }


def water_rows(store=_default_store, date: str | None = None,
               since: str | None = None, limit: int | None = None) -> list[dict]:
    """Logged drinks, each also expressed in the user's display unit — so no
    caller (the page included) has to convert."""
    unit = store.get_settings().get("water_unit") or "oz"
    out = []
    for r in store.list_water(date=date, since=since, limit=limit):
        row = dict(r)
        row["amount"] = from_ml(row.get("ml", 0) or 0, unit)
        row["unit"] = unit
        out.append(row)
    return out


def water_trend(store=_default_store, since: str | None = None) -> list[dict]:
    """Per-day water totals from `since` onward, oldest first, in ml AND the
    user's unit. Only days with a logged drink appear."""
    unit = store.get_settings().get("water_unit") or "oz"
    by_day: dict[str, float] = {}
    for row in store.list_water(since=since):
        by_day[row["date"]] = by_day.get(row["date"], 0.0) + (row.get("ml", 0) or 0)
    return [{"date": d, "ml": _round(v, 0), "amount": from_ml(v, unit), "unit": unit}
            for d, v in sorted(by_day.items())]


def targets(store=_default_store) -> dict:
    """The user's daily targets (None where unset)."""
    s = store.get_settings()
    return {
        "kcal": s.get("target_kcal"),
        "protein_g": s.get("target_protein_g"),
        "carbs_g": s.get("target_carbs_g"),
        "fat_g": s.get("target_fat_g"),
    }


def _progress(consumed: float, target):
    """(remaining, fraction-of-target capped at 1) or (None, None) if no target."""
    if not target or target <= 0:
        return None, None
    return _round(target - consumed, 0 if target >= 50 else 1), round(min(1.0, consumed / target), 4)


def day_summary(store=_default_store, date: str = "") -> dict:
    """Today's intake vs targets (remaining + progress per macro), the latest
    weight on/before the date, and any wearable metrics for the day."""
    tot = today_totals(store, date)
    tg = targets(store)
    rings = {}
    for m, key in (("kcal", "kcal"), ("protein_g", "protein_g"),
                   ("carbs_g", "carbs_g"), ("fat_g", "fat_g")):
        remaining, frac = _progress(tot[m], tg[key])
        rings[m] = {"consumed": tot[m], "target": tg[key],
                    "remaining": remaining, "progress": frac}
    w = store.latest_weight(on_or_before=date) if date else store.latest_weight()
    dm = store.get_daily_metric(date) if date else None
    return {
        "date": date,
        "totals": tot,
        "targets": tg,
        "rings": rings,
        "weight": (None if not w else {"weight": w["weight"], "unit": w["unit"], "date": w["date"]}),
        "water": water_summary(store, date),
        "daily_metric": dm,
    }


def weight_trend(store=_default_store, since: str | None = None) -> list[dict]:
    """Weight series (one point per weigh-in) from `since` onward, oldest first."""
    return [{"date": w["date"], "weight": w["weight"], "unit": w["unit"]}
            for w in store.list_weights(since=since)]


def intake_trend(store=_default_store, since: str | None = None) -> list[dict]:
    """Per-day kcal + protein totals from `since` onward — the bars behind the
    weekly/monthly view. Only days that have logged meals appear."""
    by_day: dict[str, dict] = {}
    for meal in store.list_meals():
        d = meal["date"]
        if since and d < since:
            continue
        agg = by_day.setdefault(d, {"date": d, "kcal": 0.0, "protein_g": 0.0})
        agg["kcal"] += meal.get("kcal", 0) or 0
        agg["protein_g"] += meal.get("protein_g", 0) or 0
    out = list(by_day.values())
    for a in out:
        a["kcal"] = _round(a["kcal"], 0)
        a["protein_g"] = _round(a["protein_g"])
    out.sort(key=lambda r: r["date"])
    return out


def summary(store=_default_store, date: str = "") -> dict:
    """The full Health dashboard payload for a given day: today's rings + totals,
    weight + goal, recent meals, and short trend series. This is what
    routers/health.py will serve at /health/summary."""
    ds = day_summary(store, date)
    s = store.get_settings()
    return {
        "date": date,
        "totals": ds["totals"],
        "targets": ds["targets"],
        "rings": ds["rings"],
        "weight": ds["weight"],
        "weight_goal": s.get("weight_goal"),
        "weight_unit": s.get("weight_unit", "lb"),
        "water": ds["water"],
        "daily_metric": ds["daily_metric"],
        "meals_today": store.list_meals(date=date),
        "water_today": water_rows(store, date=date),
        "weight_trend": weight_trend(store),
        "intake_trend": intake_trend(store),
    }
