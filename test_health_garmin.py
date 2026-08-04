"""
Adam — Garmin add-on tests (Phase H3).

The real sync can't run here (no python-garminconnect, no Garmin credentials), so
this covers everything around it: the connector degrades cleanly when the library
is absent, the registry lists the add-on, status/gating endpoints behave, and the
sync endpoint's success path works with the connector monkeypatched. The live
pull against real Garmin is deliberately UNVERIFIED (built to fail soft).

Run:  python test_health_garmin.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "g" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import garmin                       # noqa: E402
import integration_config           # noqa: E402
import integration_registry         # noqa: E402
import server                       # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

AUTH = {"Authorization": "Bearer " + server.ADAM_TOKEN}
client = TestClient(server.app)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="adam_garmin_"))
    config.HEALTH_DB = sandbox / "health.db"
    config.GARMIN_ENABLED = False
    config.GARMIN_EMAIL = ""
    config.GARMIN_PASSWORD = ""

    print("\n[1] Connector degrades cleanly when off / library absent")
    check("status not_added when off", garmin.status() == "not_added")
    check("not available when off", garmin.available() is False)
    raised = False
    try:
        garmin.sync("2030-01-15")
    except garmin.GarminUnavailable:
        raised = True
    check("sync raises GarminUnavailable (no lib/creds)", raised)

    print("\n[2] Registry lists the add-on")
    snap = integration_registry.snapshot() if hasattr(integration_registry, "snapshot") else None
    ids = []
    if isinstance(snap, list):
        ids = [e.get("id") for e in snap]
    elif isinstance(snap, dict):
        ids = [e.get("id") for e in snap.get("integrations", snap.get("addons", []))]
    check("garmin present in registry snapshot", "garmin" in ids)

    print("\n[3] Status + gating endpoints")
    r = client.get("/health/garmin/status", headers=AUTH)
    check("status endpoint 200", r.status_code == 200 and "library_installed" in r.json())
    check("status no token -> rejected", client.get("/health/garmin/status").status_code in (401, 403))
    r = client.post("/health/garmin/sync", headers=AUTH, json={})
    check("sync while off -> 409", r.status_code == 409)

    print("\n[4] Enable endpoint (config writers stubbed — no real .env/settings)")
    _env, _set, _refresh = integration_config.set_env_var, integration_config.set_settings_integration, config.refresh_integrations
    calls = {"env": [], "settings": []}
    integration_config.set_env_var = lambda k, v, **kw: calls["env"].append(k)
    integration_config.set_settings_integration = lambda name, block, **kw: calls["settings"].append((name, block))
    config.refresh_integrations = lambda *a, **k: True
    try:
        r = client.post("/health/garmin/enable", headers=AUTH,
                        json={"email": "x@example.com", "password": "secret", "enabled": True})
        check("enable 200", r.status_code == 200)
        check("wrote both credentials to .env", set(calls["env"]) == {"GARMIN_EMAIL", "GARMIN_PASSWORD"})
        check("wrote garmin settings block", calls["settings"] and calls["settings"][0][0] == "garmin")
        check("password never echoed", "secret" not in r.text)
    finally:
        integration_config.set_env_var, integration_config.set_settings_integration, config.refresh_integrations = _env, _set, _refresh

    print("\n[5] Sync success path (connector monkeypatched)")
    config.GARMIN_ENABLED = True
    config.GARMIN_EMAIL = "x@example.com"
    config.GARMIN_PASSWORD = "secret"
    _lib, _sync = garmin.library_installed, garmin.sync
    garmin.library_installed = lambda: True
    garmin.sync = lambda date=None, **kw: {"date": date or "2030-01-15", "metrics": {"steps": 8000},
                                           "weight": {"weight": 177.0, "unit": "lb"}, "errors": []}
    try:
        r = client.post("/health/garmin/sync", headers=AUTH, json={"date": "2030-01-15"})
        check("sync success 200", r.status_code == 200 and r.json()["metrics"]["steps"] == 8000)
    finally:
        garmin.library_installed, garmin.sync = _lib, _sync
        config.GARMIN_ENABLED = False; config.GARMIN_EMAIL = ""; config.GARMIN_PASSWORD = ""

    print("\n[6] One-click library install")
    _li = garmin.library_installed
    garmin.library_installed = lambda: True
    try:
        res = garmin.install_library()   # short-circuits (no pip) when already present
        check("install no-ops when already installed", res["ok"] and res.get("already") is True)
    finally:
        garmin.library_installed = _li
    _inst = garmin.install_library
    garmin.install_library = lambda timeout=600: {"ok": True, "installed": True, "log": "ok"}
    try:
        r = client.post("/health/garmin/install", headers=AUTH)
        check("install endpoint 200 + merged state", r.status_code == 200 and r.json()["installed"] is True and "status" in r.json())
    finally:
        garmin.install_library = _inst
    check("install endpoint token-gated", client.post("/health/garmin/install").status_code in (401, 403))

    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n  sandbox: {sandbox}\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
