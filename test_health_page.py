"""
Adam — Health Tracker page + view-menu wiring tests (Phase H1c).

  * GET /health-tracker serves health.html (the bare /health stays the liveness
    endpoint — verified distinct);
  * the page uses the shared token transport and talks only to /health/*;
  * health.html is in the release allow-list;
  * index.html carries the view-menu button, overlay, IIFE opener
    (JVLViews.health / showHealth), and the Help Hub Health category.

Run:  python test_health_page.py   (exit 0 = all passed)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "q" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
WEB = server.FRONTEND.parent

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond: _passed += 1; print(f"  PASS  {name}")
    else: _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    print("\n[1] /health-tracker serves the page; /health stays liveness")
    r = client.get("/health-tracker")
    html = r.text
    check("page 200", r.status_code == 200)
    check("titled Health", "<title>Health — Adam</title>" in html)
    check("has today/log/trends/setup tabs",
          all(t in html for t in ('id="tabToday"', 'id="tabLog"', 'id="tabTrends"', 'id="tabSetup"')))
    check("has meal + weight logging", 'id="mealAddBtn"' in html and 'id="wAddBtn"' in html)
    check("has the water counter (quick-add + custom + target)",
          "function waterCard(" in html and "function addWater(" in html
          and 'id="waAddBtn"' in html and 'id="tWater"' in html)
    check("water undo wired", "function undoWater(" in html and "/health/water?id=" in html)
    check("bare /health is still liveness JSON (not the page)",
          "<title>Health — Adam</title>" not in client.get("/health").text)

    print("\n[2] Token transport, no URL tokens")
    check("reads jarvis_token", 'localStorage.getItem("jarvis_token")' in html)
    check("calls /health/summary", "/health/summary" in html)
    check("no token in URLs", "token=" not in html)
    # Regression: today() must use the LOCAL calendar date, not UTC. toISOString()
    # rolls to tomorrow after ~5pm in a negative-offset zone, which made "today"
    # query an empty future day (data appeared to vanish) and stamp new logs under
    # the wrong date. It must build the date from local getFullYear/getMonth/getDate.
    m = re.search(r"function today\(\)\s*\{.*?\}", html, re.S)
    check("today() defined", bool(m))
    tfn = m.group(0) if m else ""
    check("today() uses LOCAL date parts, not UTC",
          "getFullYear" in tfn and "getDate" in tfn and "toISOString" not in tfn)

    print("\n[3] Release allow-list")
    import importlib.util
    spec = importlib.util.spec_from_file_location("_mr", WEB.parent / "scripts" / "make_release.py")
    mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
    check("health.html shipped", "health.html" in mr._WEB_FILES)
    check("health router shipped", "health.py" in mr._ROUTERS_FILES)
    check("health engine shipped", "health_store.py" in mr._ROOT_FILES and "health_metrics.py" in mr._ROOT_FILES)

    print("\n[4] index.html wiring")
    idx = (WEB / "index.html").read_text("utf-8", errors="ignore")
    check("menu button", 'id="viewHealth"' in idx and "Health Tracker" in idx)
    check("overlay + iframe", 'id="healthOverlay"' in idx and 'id="healthFrame"' in idx)
    check("IIFE opener", "function showHealth()" in idx)
    check("on JVLViews", "health: showHealth" in idx)
    check("loads /health-tracker", '"/health-tracker"' in idx)
    check("assistant hides health", "hideHealth()" in idx)
    check("Help Hub Health category", "Log meals your way" in idx)

    print(f"\n{'=' * 48}\n  {_passed} passed, {_failed} failed\n{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
