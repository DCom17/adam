"""
Adam — Garmin Connect health sync (Phase H3), an OPT-IN add-on.

Pulls the day's wearable metrics (steps, sleep, resting heart rate, stress, body
battery, active calories) and body weight from Garmin Connect into the local
health store, so the Health Tracker reflects a linked watch.

IMPORTANT — this is UNOFFICIAL. Garmin has no public consumer API; this uses the
community `python-garminconnect` library, which logs in with the user's own
Garmin credentials and reads the same endpoints the mobile app uses. Garmin can
change those without notice, so this can break — the add-on is honest about that
and fails soft (a broken sync never touches anything else). Credentials live only
in the local .env (GARMIN_EMAIL / GARMIN_PASSWORD), never logged.

The library is NOT a hard dependency: it's imported lazily, so an install that
never enables Garmin (the default) needs nothing extra and boots fine.
"""

from __future__ import annotations

import os
import time

import config
import health_store as hs


class GarminUnavailable(RuntimeError):
    """Raised when the add-on can't run: library not installed, or no credentials."""


def library_installed() -> bool:
    """True if the optional python-garminconnect library is importable."""
    try:
        import garminconnect  # noqa: F401
        return True
    except Exception:
        return False


def install_library(timeout: int = 600) -> dict:
    """Install the optional `garminconnect` library into the SAME interpreter the
    server runs on, so a non-technical user never has to open a terminal — the
    setup page calls this behind an 'Install sync library' button.

    Fixed package name (never user input), so this can't be turned into an
    arbitrary-install vector. Returns {ok, installed, log} with a short tail of
    pip's output; the running process can import the library afterward (caches
    are invalidated) without a restart. Blocking — the endpoint runs it in a
    thread."""
    import importlib
    import subprocess
    import sys

    if library_installed():
        return {"ok": True, "installed": True, "already": True, "log": "Already installed."}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "garminconnect"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 — no pip / frozen build / timeout
        return {"ok": False, "installed": False,
                "log": f"Couldn't run the installer automatically ({e}). "
                       f"Open the Adam folder and run: pip install garminconnect"}
    importlib.invalidate_caches()
    installed = library_installed()
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-8:])
    return {"ok": bool(installed and proc.returncode == 0),
            "installed": installed, "returncode": proc.returncode, "log": tail}


def _credentials() -> tuple[str, str]:
    email = getattr(config, "GARMIN_EMAIL", "") or ""
    password = getattr(config, "GARMIN_PASSWORD", "") or ""
    return email, password


def available() -> bool:
    """Enabled + configured + library present — ready to sync."""
    email, password = _credentials()
    return bool(getattr(config, "GARMIN_ENABLED", False) and email and password
                and library_installed())


def status() -> str:
    """Short status string for the add-on registry / settings pill."""
    if not getattr(config, "GARMIN_ENABLED", False):
        return "not_added"
    email, password = _credentials()
    if not (email and password):
        return "needs_credentials"
    if not library_installed():
        return "needs_library"   # `pip install garminconnect`
    return "ready"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _tokenstore() -> str | None:
    """Directory where the login's OAuth tokens are cached, so repeat syncs reuse
    the session instead of hitting Garmin's SSO login every time — full logins are
    aggressively rate-limited (HTTP 429), which matters now that the Health Tracker
    refreshes on open. Lives under the gitignored health data dir; a failure to
    make it just means we fall back to a fresh login (older behavior)."""
    try:
        d = os.path.join(str(config.HEALTH_DIR), "garmin_tokens")
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001 — no dir is fine, login still works (just slower)
        return None


def _first(d: dict, *keys):
    """First present, non-None value among keys in a dict (Garmin's payloads vary
    in casing/naming across library versions, so we probe several)."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def sync(date: str | None = None, *, email: str | None = None,
         password: str | None = None, store=hs) -> dict:
    """Log in to Garmin and pull `date`'s metrics + latest weight into the health
    store. Returns a summary of what was written. Raises GarminUnavailable if the
    library or credentials are missing. Each metric is fetched defensively — a
    single endpoint that changed shape is skipped, not fatal — because this rides
    undocumented endpoints that Garmin can alter at any time.

    Not unit-tested against real Garmin (no library/credentials in CI); the
    parsing is written to fail soft so a breaking change degrades to a partial
    or empty sync rather than an error the user can't act on."""
    try:
        from garminconnect import Garmin  # lazy — optional dependency
    except Exception as e:  # noqa: BLE001
        raise GarminUnavailable(
            "The Garmin library isn't installed. Run: pip install garminconnect"
        ) from e

    email = email or _credentials()[0]
    password = password or _credentials()[1]
    if not (email and password):
        raise GarminUnavailable("No Garmin credentials set (GARMIN_EMAIL / GARMIN_PASSWORD).")

    d = date or _today()
    client = Garmin(email, password)
    # Prefer a cached token session; only fall through to a full (rate-limited)
    # SSO login when the library is too old for the kwarg or no token exists yet.
    store_dir = _tokenstore()
    try:
        client.login(tokenstore=store_dir) if store_dir else client.login()
    except TypeError:
        client.login()

    written: dict = {"date": d, "metrics": {}, "weight": None, "errors": []}
    metric_fields: dict = {"source": "garmin"}

    # Daily summary: steps, active calories, stress.
    try:
        stats = client.get_stats(d)
        steps = _first(stats, "totalSteps", "steps")
        active = _first(stats, "activeKilocalories", "activeKilocalorie", "activeCalories")
        stress = _first(stats, "averageStressLevel", "stressLevel")
        bb = _first(stats, "bodyBatteryMostRecentValue", "bodyBattery")
        if steps is not None: metric_fields["steps"] = int(steps)
        if active is not None: metric_fields["active_kcal"] = int(active)
        if stress is not None and stress >= 0: metric_fields["stress"] = int(stress)
        if bb is not None: metric_fields["body_battery"] = int(bb)
    except Exception as e:  # noqa: BLE001
        written["errors"].append(f"stats: {e}")

    # Sleep (minutes).
    try:
        sleep = client.get_sleep_data(d)
        dto = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
        secs = _first(dto, "sleepTimeSeconds") or _first(sleep, "sleepTimeSeconds")
        if secs:
            metric_fields["sleep_min"] = int(secs) // 60
    except Exception as e:  # noqa: BLE001
        written["errors"].append(f"sleep: {e}")

    # Resting heart rate.
    try:
        rhr = client.get_rhr_day(d)
        val = None
        if isinstance(rhr, dict):
            metrics = rhr.get("allMetrics", {}).get("metricsMap", {}) if rhr.get("allMetrics") else {}
            series = metrics.get("WELLNESS_RESTING_HEART_RATE") if metrics else None
            if series and isinstance(series, list):
                val = _first(series[0], "value")
            val = val if val is not None else _first(rhr, "restingHeartRate")
        if val is not None:
            metric_fields["resting_hr"] = int(val)
    except Exception as e:  # noqa: BLE001
        written["errors"].append(f"rhr: {e}")

    if len(metric_fields) > 1:  # more than just 'source'
        store.set_daily_metric(d, **metric_fields)
        written["metrics"] = {k: v for k, v in metric_fields.items() if k != "source"}

    # Body weight (Garmin returns grams; store in the user's configured unit).
    try:
        bc = client.get_body_composition(d)
        grams = None
        if isinstance(bc, dict):
            grams = _first(bc, "weight")
            if grams is None:
                tot = bc.get("totalAverage") if isinstance(bc.get("totalAverage"), dict) else {}
                grams = _first(tot, "weight")
        if grams:
            unit = store.get_setting("weight_unit") or "lb"
            kg = float(grams) / 1000.0
            weight = round(kg * 2.2046226, 1) if unit == "lb" else round(kg, 1)
            # Replace the day's synced weigh-in in place — the Tracker now syncs
            # repeatedly, so appending would stack a duplicate every refresh.
            # Manual weigh-ins (other notes) are untouched.
            writer = getattr(store, "replace_weight_by_note", None) or store.add_weight
            writer(d, weight, unit=unit, note="Garmin sync")
            written["weight"] = {"weight": weight, "unit": unit}
    except Exception as e:  # noqa: BLE001
        written["errors"].append(f"weight: {e}")

    return written
