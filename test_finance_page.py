"""
Adam — Finance Tracker page + view-menu wiring tests (Phase F3).

Cheap, fast checks that the dashboard page is served and the main app is wired to
open it:
  * GET /finance serves the finance.html shell (no token needed for the shell —
    its data fetches are token-gated), with the dashboard + import markers present;
  * the served page reads its bearer token from the shared localStorage keys and
    talks only to the /finance/* API (never a URL-embedded token);
  * web/index.html carries the view-menu button, the overlay, the IIFE opener
    (JVLViews.finance / showFinance), and the Help Hub Finance category.

No DB or model needed. Run:  python test_finance_page.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "p" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
WEB = server.FRONTEND.parent

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def main() -> int:
    print("\n[1] GET /finance serves the dashboard shell")
    r = client.get("/finance")
    check("page 200", r.status_code == 200)
    html = r.text
    check("content is html", "text/html" in r.headers.get("content-type", ""))
    check("titled Finance", "<title>Finance — Adam</title>" in html)
    check("has dashboard tab", "DASHBOARD" in html and 'id="dashBody"' in html)
    check("has import tab (paste)", 'id="tabImport"' in html and 'id="impText"' in html)
    check("has file upload", 'id="impFile"' in html and 'type="file"' in html and "/upload" in html)
    check("has setup tab", 'id="tabSetup"' in html and 'id="setupView"' in html)
    check("has income + settings fields", 'id="setIncome"' in html and "/finance/settings" in html)
    check("has balance snapshot entry", 'id="snapDate"' in html and "/finance/snapshot" in html)
    check("has account entry", 'id="acctName"' in html and "/finance/accounts" in html)
    check("has manage tab", 'id="tabManage"' in html and 'id="manageView"' in html)
    check("has review tab", 'id="tabReview"' in html and 'id="reviewTabView"' in html)
    check("review tab wired to needs-review API", "/finance/needs-review" in html and "showReviewTab" in html)
    check("manage can undo imports", "/finance/imports" in html and "delImport" in html)
    check("manage can delete transactions + snapshots", "delTxn" in html and "delSnap" in html)
    check("import accepts multiple CSV files at once", "multiple" in html and "file_paths" in html)
    check("balances live in import as step 2", "Step 2 · Balances" in html and "snapBody" in html)
    check("screenshot-to-balance wired", "/finance/balance-photo" in html and "scanBalancePhoto" in html)
    check("review has a top approve bar (no scroll to bottom)",
          "topapprove" in html and "#reviewFrame { overflow:visible" in html)
    check("served no-store (edits show without cache clear)",
          "no-store" in r.headers.get("cache-control", "").lower())

    print("\n[2] The page uses the shared token transport, not URL tokens")
    check("reads jarvis_token from localStorage", 'localStorage.getItem("jarvis_token")' in html)
    check("sends bearer header", '"Authorization": "Bearer "' in html)
    check("calls the finance summary API", "/finance/summary" in html)
    check("no token in any URL query", "token=" not in html)

    print("\n[3] finance.html is in the release web allow-list")
    import importlib.util
    mr_path = WEB.parent / "scripts" / "make_release.py"
    spec = importlib.util.spec_from_file_location("_mr_probe", mr_path)
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    check("finance.html shipped", "finance.html" in mr._WEB_FILES)
    check("finance router shipped", "finance.py" in mr._ROUTERS_FILES)
    check("finance engine modules shipped",
          "finance_store.py" in mr._ROOT_FILES and "finance_import.py" in mr._ROOT_FILES)

    print("\n[4] Main app (index.html) is wired to open the Finance view")
    index_html = (WEB / "index.html").read_text("utf-8", errors="ignore")
    check("view-menu button present", 'id="viewFinance"' in index_html and "Finance Tracker" in index_html)
    check("overlay + iframe present", 'id="financeOverlay"' in index_html and 'id="financeFrame"' in index_html)
    check("IIFE opener defined", "function showFinance()" in index_html)
    check("exposed on JVLViews", "finance: showFinance" in index_html)
    check("loads /finance in the iframe", 'base() + "/finance"' in index_html)
    check("assistant view hides finance", "hideFinance()" in index_html)
    check("Help Hub has a Finance category", "Finance Tracker" in index_html and
          "Import a statement, Adam sorts it" in index_html)

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
