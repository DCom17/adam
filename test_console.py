"""
Jarvis Voice Local — operator console + server smoke tests (v0.8.0, Slice 1).

Headless FastAPI TestClient coverage for the read-only Operator Console and the
routes it depends on. Proves:
  * GET /console serves HTML and leaks no secret value;
  * /ping is open; token-gated routes reject missing/wrong tokens and accept the
    real one; /health exposes no secret;
  * /jobs and /jobs/{id} response shapes are stable;
  * /proposed-changes list/diff/status read shapes are stable;
  * the existing proposed-change lifecycle (apply-requires-approval -> 409,
    unknown id -> 404) is unchanged.

State is isolated: the job DB and the proposed-changes/approvals JSON are pointed
at a throwaway temp dir so the run never touches real history.

Run:  python test_console.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import config

# Make the app importable on any machine: server.py calls config.validate() at
# import and refuses to start without a token + a resolvable Claude. On the real
# box these are already set (and are used as-is); elsewhere we supply harmless
# stand-ins so the smoke can still run. No secret is asserted from these.
if not config.JARVIS_TOKEN:
    config.JARVIS_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable  # any existing executable path satisfies validate()

# Isolate mutable state BEFORE we touch the endpoints.
_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_console_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

# Also sandbox the WRITE side so the v0.8.2 action-lifecycle checks (approve/apply/
# conflict/blocked) exercise the real server-enforced path without ever touching the
# product's real data tree. Mirrors test_proposed_changes' isolation.
_VAULT = _SANDBOX / "vault"
_DRAFTS = _SANDBOX / "drafts"
_OUTPUTS = _SANDBOX / "outputs"
_BLOCKED = _SANDBOX / "secret_zone"
_BACKUPS = _SANDBOX / "backups"
_STATE = _SANDBOX / "state"
_LOGS = _SANDBOX / "logs"
for _d in (_VAULT, _DRAFTS, _OUTPUTS, _BLOCKED, _BACKUPS, _STATE, _LOGS):
    _d.mkdir(parents=True, exist_ok=True)
config.PERM_READ_DIRS = [str(_VAULT)]
config.PERM_WRITE_DIRS = [str(_DRAFTS), str(_OUTPUTS)]
config.INFRA_WRITE_DIRS = [str(_STATE), str(_LOGS), str(_BACKUPS)]
config.PERM_BLOCKED_PATHS = [str(_BLOCKED)]
config.PERM_PROTECTED_FILE_PATTERNS = [".env", "*.pem", "settings.json"]
config.PERM_BACKUP_BEFORE_WRITE = True
config.PERM_AUDIT_LOG_ENABLED = True
config.BACKUP_DIR = _BACKUPS
config.AUDIT_LOG_FILE = _LOGS / "audit.jsonl"
config.DRAFTS_DIR = _DRAFTS
config.OUTPUTS_DIR = _OUTPUTS
config.FORCE_OUTPUTS_TO_DRAFTS = True

import job_store          # noqa: E402
import proposed_changes   # noqa: E402
import server             # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Repoint the job store at a temp DB; the server endpoints read the same module
# global, so this isolates /jobs without forking the process.
job_store.init(_SANDBOX / "jobs.db")

TOKEN = server.JARVIS_TOKEN
AUTH = {"Authorization": "Bearer " + TOKEN}
SECRETS = [s for s in (
    server.JARVIS_TOKEN,
    getattr(config, "VAPID_PUBLIC_KEY", ""),
    getattr(config, "TWILIO_AUTH_TOKEN", ""),
) if s]

client = TestClient(server.app)

_passed = 0
_failed = 0
_skipped = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def _no_secret(text: str) -> bool:
    return all(s not in text for s in SECRETS)


def main() -> int:
    print("\n[1] GET /console serves HTML and leaks no secret")
    r = client.get("/console")
    check("/console -> 200", r.status_code == 200)
    check("/console is HTML", "text/html" in r.headers.get("content-type", ""))
    check("/console looks like the console page", "Operator Console" in r.text)
    check("/console sends no-store", "no-store" in r.headers.get("cache-control", ""))
    check("/console contains no secret value", _no_secret(r.text))
    check("/console does not require auth (no token sent)", r.status_code == 200)

    print("\n[2] PWA index still served (web/index.html untouched)")
    ri = client.get("/")
    check("/ -> 200", ri.status_code == 200)

    print("\n[3] /ping is open")
    rp = client.get("/ping")
    check("/ping -> 200", rp.status_code == 200)
    check("/ping body ok", rp.json().get("status") == "ok")

    print("\n[4] Token-gated routes reject missing / wrong token")
    check("/jobs no token -> 403", client.get("/jobs").status_code == 403)
    check("/jobs wrong token -> 403",
          client.get("/jobs", headers={"Authorization": "Bearer nope"}).status_code == 403)
    check("/proposed-changes no token -> 403",
          client.get("/proposed-changes").status_code == 403)
    check("/jobs/{id} no token -> 403", client.get("/jobs/anything").status_code == 403)

    print("\n[5] Token-gated routes accept the valid token")
    check("/jobs valid token -> 200", client.get("/jobs", headers=AUTH).status_code == 200)
    check("/proposed-changes valid token -> 200",
          client.get("/proposed-changes", headers=AUTH).status_code == 200)
    check("/approvals valid token -> 200", client.get("/approvals", headers=AUTH).status_code == 200)

    print("\n[6] /health exposes no secret")
    rh = client.get("/health", headers=AUTH)
    check("/health -> 200", rh.status_code == 200)
    check("/health version present", bool(rh.json().get("version")))
    check("/health agent_safety.mode present", "agent_safety" in rh.json())
    check("/health contains no secret value", _no_secret(rh.text))

    print("\n[7] /jobs shape is stable (seeded job)")
    job_store.create_job("smoke-job-1", mode="voice", session_id="sid-1",
                         input_summary="smoke summary", pid=1234)
    lj = client.get("/jobs", headers=AUTH)
    body = lj.json()
    check("list has 'jobs' + 'count'", "jobs" in body and "count" in body)
    seeded = next((j for j in body["jobs"] if j.get("job_id") == "smoke-job-1"), None)
    check("seeded job present", seeded is not None)
    check("job row has expected keys",
          seeded is not None and all(k in seeded for k in
          ("job_id", "status", "mode", "created_at", "input_summary")))

    print("\n[8] /jobs/{id} shape is stable when a job exists")
    gj = client.get("/jobs/smoke-job-1", headers=AUTH)
    check("/jobs/{id} -> 200", gj.status_code == 200)
    jd = gj.json()
    check("detail has core fields",
          all(k in jd for k in ("job_id", "status", "mode", "session_id",
                                "input_summary", "proposed_change_ids")))
    check("/jobs/{unknown} -> 404", client.get("/jobs/nope-xyz", headers=AUTH).status_code == 404)
    check("seeded job summary survives", jd.get("input_summary") == "smoke summary")

    print("\n[9] /proposed-changes list/diff/status read shapes are stable")
    rec = proposed_changes.create(
        target_path="console_smoke_note.md", action="create",
        content="hello\nworld\n", summary="console smoke", session_id="sid-1",
    )
    cid = rec["id"]
    lc = client.get("/proposed-changes", headers=AUTH).json()
    check("list has 'proposed_changes'/'count'/'pending'",
          all(k in lc for k in ("proposed_changes", "count", "pending")))
    listed = next((c for c in lc["proposed_changes"] if c.get("id") == cid), None)
    check("created change is listed", listed is not None)
    check("list omits content + diff (fetched on demand)",
          listed is not None and "content" not in listed and "diff" not in listed)

    gd = client.get(f"/proposed-changes/{cid}/diff", headers=AUTH)
    check("/diff -> 200", gd.status_code == 200)
    dd = gd.json()
    check("/diff has stable keys",
          all(k in dd for k in ("id", "action", "target_path", "status",
                                "diff", "diff_available")))

    gs = client.get(f"/proposed-changes/{cid}/status", headers=AUTH)
    check("/status -> 200", gs.status_code == 200)
    check("/status has a status field", "status" in gs.json())

    gfull = client.get(f"/proposed-changes/{cid}", headers=AUTH)
    check("/proposed-changes/{id} returns content", "content" in gfull.json())
    check("/proposed-changes/{unknown} -> 404",
          client.get("/proposed-changes/nope-xyz", headers=AUTH).status_code == 404)

    print("\n[10] Existing proposed-change lifecycle unchanged (apply needs approval)")
    ap = client.post(f"/proposed-changes/{cid}/apply", headers=AUTH)
    check("apply before approve -> 409", ap.status_code == 409)
    still = client.get(f"/proposed-changes/{cid}", headers=AUTH).json()
    check("change is still pending (not applied/mutated)", still.get("status") == "pending")
    target = Path(config.DRAFTS_DIR) / "console_smoke_note.md"
    check("target file was NOT written by the smoke", not target.exists())

    print("\n[11] Console serves the read-only Audit section (v0.8.1)")
    rc = client.get("/console")
    html = rc.text
    check("console has an Audit section", 'id="auditSec"' in html and "Audit log" in html)
    check("console references the /audit route", "/audit?limit=" in html or "/audit" in html)
    check("console has an audit limit selector", 'id="auditLimit"' in html)
    check("console still contains no secret value", _no_secret(html))

    print("\n[12] Action layer (v0.8.2): only existing endpoints, no innerHTML")
    # The console now POSTs, but ONLY to the existing token-gated action endpoints
    # the PWA already uses, through a single POST sink, and never via innerHTML.
    check("page has a single POST sink (method:\"POST\" appears once)",
          html.count('method: "POST"') == 1)
    post_targets = re.findall(r'postApi\(\s*"([^"]*)"', html)
    check("postApi is actually used", len(post_targets) > 0)
    # Every POST target prefix must be an approved proposed-changes/approvals path.
    _allowed = ("/proposed-changes/", "/approvals/")
    check("every postApi target is an approved action path",
          all(p.startswith(_allowed) for p in post_targets))
    # Must NOT POST the create endpoint or any non-action/mutation endpoint.
    check("console never POSTs the create endpoint", "/proposed-changes" not in post_targets)
    for f in ("/ui-prefs", "/upload", "/ask", "/push", "/speak", "/clientlog", "/audit"):
        check(f"no postApi target touches {f!r}",
              not any(p.startswith(f) for p in post_targets))
    # The action suffixes the console wires up must be within the approved set.
    for needed in ("/approve", "/deny", "/apply", "/refresh-diff", "/approvals/"):
        check(f"console wires the existing endpoint {needed!r}", needed in html)
    check("console action handlers reference approve+apply (one-tap)",
          "/approve" in html and "/apply" in html)
    check("console JS never assigns innerHTML",
          "innerHTML =" not in html and "innerHTML=" not in html)

    print("\n[13] Action lifecycle uses the existing server-enforced endpoints")
    # Happy path: create -> approve (HTTP) -> apply (HTTP) writes via the server.
    h = proposed_changes.create(target_path="console_apply.md", action="create",
                                content="hello console\n", summary="console apply")
    hid = h["id"]
    check("apply before approve -> 409 (unchanged)",
          client.post(f"/proposed-changes/{hid}/apply", headers=AUTH).status_code == 409)
    check("target not written before approve",
          not (_DRAFTS / "console_apply.md").exists())
    check("approve -> 200/approved",
          client.post(f"/proposed-changes/{hid}/approve", headers=AUTH).json().get("status") == "approved")
    ap = client.post(f"/proposed-changes/{hid}/apply", headers=AUTH)
    check("apply after approve -> 200/applied",
          ap.status_code == 200 and ap.json().get("status") == "applied")
    check("file written by the server",
          (_DRAFTS / "console_apply.md").read_text("utf-8") == "hello console\n")
    check("apply response leaks no secret", _no_secret(ap.text))

    # Deny path: never writes.
    d = proposed_changes.create(target_path="console_deny.md", action="create",
                                content="nope\n", summary="console deny")
    did = d["id"]
    check("deny -> 200/denied",
          client.post(f"/proposed-changes/{did}/deny", headers=AUTH).json().get("status") == "denied")
    check("denied apply -> 409", client.post(f"/proposed-changes/{did}/apply",
          headers=AUTH).status_code == 409)
    check("denied change not written", not (_DRAFTS / "console_deny.md").exists())

    # Refresh-diff resets an approved change to pending.
    rf = proposed_changes.create(target_path="console_refresh.md", action="create",
                                 content="r\n", summary="console refresh")
    rid = rf["id"]
    client.post(f"/proposed-changes/{rid}/approve", headers=AUTH)
    check("refresh-diff -> pending",
          client.post(f"/proposed-changes/{rid}/refresh-diff", headers=AUTH).json().get("status") == "pending")

    print("\n[14] Apply conflict still blocks; protected/blocked still rejected")
    # Conflict: approve, then change the target on disk -> apply must 409 and NOT overwrite.
    (_DRAFTS / "console_conf.md").write_text("ORIGINAL", encoding="utf-8")
    cf = proposed_changes.create(target_path="console_conf.md", action="replace",
                                 content="PROPOSED-NEW", summary="console conflict")
    cfid = cf["id"]
    client.post(f"/proposed-changes/{cfid}/approve", headers=AUTH)
    (_DRAFTS / "console_conf.md").write_text("EXTERNALLY-CHANGED", encoding="utf-8")
    cr = client.post(f"/proposed-changes/{cfid}/apply", headers=AUTH)
    check("apply on a changed target -> 409 conflict", cr.status_code == 409)
    check("conflicting apply did NOT overwrite the file",
          (_DRAFTS / "console_conf.md").read_text("utf-8") == "EXTERNALLY-CHANGED")

    # Protected + blocked targets are refused at create (the gate the console relies on).
    def _create_rejected(name: str, **kw) -> None:
        try:
            proposed_changes.create(**kw)
            check(name, False)
        except ValueError:
            check(name, True)
    _create_rejected("blocked-path create rejected",
                     target_path=str(_BLOCKED / "x.md"), action="create", content="x")
    _create_rejected("protected settings.json create rejected",
                     target_path="settings.json", action="create", content="x")
    check("blocked file not written", not (_BLOCKED / "x.md").exists())

    print("\n[15] No secret in any rendered surface")
    check("served console page carries no secret", _no_secret(client.get("/console").text))
    check("proposed-changes list carries no secret",
          _no_secret(client.get("/proposed-changes", headers=AUTH).text))
    check("approvals list carries no secret",
          _no_secret(client.get("/approvals", headers=AUTH).text))

    print("\n[16] Connect phone — local SPLIT-QR handoff (static structure, v1.0 Slice 3.1)")
    html = client.get("/console").text
    check("has a Connect phone section", 'id="connectPhoneSec"' in html)
    check("section sits after Status, before Jobs",
          html.find('id="connectPhoneSec"') > html.find('id="statusSec"')
          and html.find('id="connectPhoneSec"') < html.find('id="jobsSec"'))
    check("has the 3-step layout (Step 1/2/3)",
          "Step 1 — Open Jarvis on phone" in html
          and "Step 2 — Copy access token" in html
          and "Step 3" in html)
    check("has a Show URL QR button", 'id="qrUrlShow"' in html and "Show URL QR" in html)
    check("has a Show token QR button", 'id="qrTokShow"' in html and "Show token QR" in html)
    check("has a separate URL QR canvas", 'id="qrUrlCanvas"' in html)
    check("has a separate token QR canvas", 'id="qrTokCanvas"' in html)
    check("has a single Hide button", 'id="qrHide"' in html)
    check("has a Reveal token fallback", 'id="qrReveal"' in html and "Reveal token" in html)
    check("has a Copy URL button", 'id="qrCopyUrl"' in html and "Copy URL" in html)
    check("shows the sensitive-token warning",
          "Sensitive — this token controls access" in html and "Do not screenshot" in html)
    # Vendored, local, attributed encoder — reused unchanged, no CDN / no remote asset.
    check("QR encoder is vendored + attributed (MIT)",
          "VENDORED QR ENCODER" in html and "Kazuhiko Arase" in html and "MIT" in html)
    check("QR encoder is pinned to a commit", "@ commit " in html)
    check("QR encoder exposes the local API (getModuleCount/isDark)",
          "getModuleCount" in html and "isDark" in html)
    check("no CDN / remote script asset",
          all(s not in html for s in ("cdn.", "unpkg", "jsdelivr", "googleapis",
                                      "<script src", "<script  src", "integrity=")))
    check("no remote stylesheet/link asset", '<link ' not in html)
    check("console JS still never assigns innerHTML",
          "innerHTML =" not in html and "innerHTML=" not in html)
    # Token discipline in the STATIC page.
    check("served HTML carries no secret token value", _no_secret(html))
    check("no ?token= anywhere in the page", "?token=" not in html)
    # Split payloads: URL QR = qrUrl(); token QR = bare qrToken() (no url/label).
    check("URL QR payload built from qrUrl() at runtime",
          "qrUrlPayload" in html and "function qrUrl()" in html and "LS.url" in html)
    check("token QR payload is the BARE token from localStorage at runtime",
          "qrTokenPayload" in html and "function qrToken()" in html and "LS.token" in html)
    check("token QR payload does not embed the URL or a label",
          'qrTokenPayload() { var t = qrToken(); return t ? t : null; }' in html)
    check("runtime-only split hooks exist (not baked values)",
          "window.__jvlQrUrlPayload" in html and "window.__jvlQrTokenPayload" in html)
    check("token text is hidden by default",
          'id="qrTokenText" class="hidden"' in html.replace('  ', ' '))
    # The QR path is client-only: no new endpoint / no new write path.
    check("single POST sink unchanged (still exactly one)",
          html.count('method: "POST"') == 1)
    check("QR generation uses the LOCAL encoder, not the network",
          "qrcode(0" in html)
    post_targets = re.findall(r'postApi\(\s*"([^"]*)"', html)
    check("Connect phone added no new POST target",
          all(p.startswith(("/proposed-changes/", "/approvals/")) for p in post_targets))
    # Easy path (default) — single combo QR = <url>/#token=<token>; the split flow is
    # preserved behind a "cautious mode" toggle, hidden by default.
    check("has a one-scan combo QR button", 'id="qrComboShow"' in html and "Show sign-in QR" in html)
    check("has a combo QR canvas", 'id="qrComboCanvas"' in html)
    check("combo payload built from url + #token fragment at runtime",
          "qrComboPayload" in html and '"/#token="' in html and "encodeURIComponent" in html)
    check("runtime-only combo hook exists (not a baked value)",
          "window.__jvlQrComboPayload" in html)
    check("split (cautious) flow preserved behind a toggle, hidden by default",
          'id="qrSplitToggle"' in html and 'id="qrSplitWrap" class="hidden"' in html)
    check("combo QR signs in via #fragment, never a query token",
          "#token=" in html and "?token=" not in html)
    # The auth bootstrap captures a #token fragment, then strips it from the URL/history.
    check("auth bootstrap reads the #token fragment", 'URLSearchParams' in html and "get(\"token\")" in html)
    check("auth bootstrap strips the fragment via replaceState", "history.replaceState" in html)

    print("\n[17] Connect phone — headless render + decode + UX (optional)")
    _headless_qr_checks()

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed"
          + (f", {_skipped} skipped" if _skipped else ""))
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


def _headless_qr_checks() -> None:
    """Real-browser proof of the QR handoff: render in a headless browser, screenshot
    the canvas, DECODE it, and exercise reveal/hide. Requires playwright + Pillow +
    zxing-cpp + a Chromium build; if any is missing it SKIPS (never fails the suite),
    so the shipped suite stays green on a plain consumer box. The QR is client-only,
    so the page is served from a throwaway local file server — no app, no token, no
    network calls from the QR code path itself."""
    global _skipped
    try:
        import http.server          # noqa: F401
        import io
        import socketserver
        import threading
        from pathlib import Path as _P
        from PIL import Image
        import zxingcpp
        from playwright.sync_api import sync_playwright
    except Exception as e:           # deps absent -> skip, do not fail
        _skipped += 1
        print(f"  SKIP  headless QR checks (browser/decoder deps unavailable: "
              f"{type(e).__name__})")
        return

    html_bytes = (_P(__file__).resolve().parent / "web" / "console.html").read_bytes()
    # A stand-in token — NEVER a real one. 48 chars, distinctive, easy to spot.
    tok = "deadbeef" + "0" * 32 + "cafe1234"

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):           # serve the console for any path
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html_bytes)

        def log_message(self, *a):  # quiet
            return

    srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:
                _skipped += 1
                print(f"  SKIP  headless QR checks (no Chromium build: "
                      f"{type(e).__name__})")
                return
            pg = browser.new_page()
            errs = []
            pg.on("pageerror", lambda exc: errs.append(str(exc)))
            url = "https://chainforge.tail4a86a8.ts.net:8443"
            pg.goto(f"http://127.0.0.1:{port}/console")
            pg.evaluate("([t, u]) => { localStorage.setItem('jarvis_token', t);"
                        " localStorage.setItem('jarvis_url', u); }", [tok, url])
            pg.evaluate("() => showConsole()")   # reveal console; QR needs no real sign-in
            def decode(canvas_sel):
                png = pg.locator(canvas_sel).screenshot()
                img = Image.open(io.BytesIO(png)).convert("L")
                res = zxingcpp.read_barcodes(img)
                return res[0].text if res else None

            def dark(canvas_id):
                return pg.evaluate("(id) => { var c=document.getElementById(id);"
                                   " var d=c.getContext('2d').getImageData(0,0,c.width,c.height).data,n=0;"
                                   " for(var i=0;i<d.length;i+=4){ if(d[i]<128) n++; } return n; }", canvas_id)

            check("[hl] global qrcode encoder is defined",
                  pg.evaluate("() => typeof qrcode === 'function'"))
            # Runtime split hooks: URL-only and bare-token payloads.
            up = pg.evaluate("() => window.__jvlQrUrlPayload()")
            tp = pg.evaluate("() => window.__jvlQrTokenPayload()")
            check("[hl] URL payload is the URL only (no token)",
                  up == url and tok not in (up or ""))
            check("[hl] token payload is the bare token (no url/https/label/?token=)",
                  tp == tok and all(s not in (tp or "")
                                    for s in ("http", "https", "URL:", "Jarvis Voice Local", "?token=")))
            check("[hl] token NOT in section DOM before reveal",
                  tok not in pg.inner_text("#connectPhoneSec"))
            check("[hl] token text hidden before reveal",
                  pg.evaluate("() => document.getElementById('qrTokenText').classList.contains('hidden')"))

            # Easy path — single combo QR: payload + decode == <url>/#token=<token>.
            expected_combo = url + "/#token=" + tok   # token is url-safe; encode is a no-op
            combo_payload = pg.evaluate("() => window.__jvlQrComboPayload()")
            check("[hl] combo payload is url + #token fragment", combo_payload == expected_combo)
            check("[hl] combo payload uses a #fragment, never ?token=",
                  "#token=" in (combo_payload or "") and "?token=" not in (combo_payload or ""))
            pg.click("#qrComboShow")
            check("[hl] combo QR panel visible after Show sign-in QR", pg.is_visible("#qrComboPanel"))
            check("[hl] combo QR canvas drawn (non-empty)", dark("qrComboCanvas") > 50)
            dcombo = decode("#qrComboCanvas")
            check("[hl] combo QR decodes to url + #token exactly", dcombo == expected_combo)
            check("[hl] token NOT in section DOM after combo Show (only inside the QR)",
                  tok not in pg.inner_text("#connectPhoneSec"))
            # Reveal the cautious two-step (split) flow before exercising it.
            check("[hl] split flow hidden by default", not pg.is_visible("#qrSplitWrap"))
            pg.click("#qrSplitToggle")
            check("[hl] split (cautious) flow revealed by toggle", pg.is_visible("#qrSplitWrap"))

            # Step 1 — URL QR: render, decode == URL exactly, contains no token.
            pg.click("#qrUrlShow")
            check("[hl] URL QR panel visible after Show URL QR", pg.is_visible("#qrUrlPanel"))
            check("[hl] URL QR canvas drawn (non-empty)", dark("qrUrlCanvas") > 50)
            durl = decode("#qrUrlCanvas")
            check("[hl] URL QR decodes to the URL EXACTLY", durl == url)
            check("[hl] URL QR decoded content has no token",
                  bool(durl) and tok not in durl)

            # Step 2 — token QR: render, decode == bare token exactly, no url/label.
            pg.click("#qrTokShow")
            check("[hl] token QR panel visible after Show token QR", pg.is_visible("#qrTokPanel"))
            check("[hl] token QR canvas drawn (non-empty)", dark("qrTokCanvas") > 50)
            check("[hl] token STILL not in DOM after Show (only inside the QR)",
                  tok not in pg.inner_text("#connectPhoneSec"))
            dtok = decode("#qrTokCanvas")
            check("[hl] token QR decodes to the bare token EXACTLY", dtok == tok)
            check("[hl] token QR decoded content has no http/https/URL:/title",
                  bool(dtok) and all(s not in dtok
                                     for s in ("http", "https", "URL:", "Jarvis Voice Local")))

            # Reveal shows the token only on explicit click.
            pg.click("#qrReveal")
            check("[hl] Reveal shows the token only after explicit click",
                  pg.evaluate("() => document.getElementById('qrTokenText').textContent") == tok)

            # Hide clears BOTH canvases and the revealed token.
            pg.click("#qrHide")
            check("[hl] Hide hides all QR panels",
                  (not pg.is_visible("#qrUrlPanel")) and (not pg.is_visible("#qrTokPanel"))
                  and (not pg.is_visible("#qrComboPanel")))
            check("[hl] Hide clears the URL canvas", dark("qrUrlCanvas") == 0)
            check("[hl] Hide clears the token canvas", dark("qrTokCanvas") == 0)
            check("[hl] Hide clears the combo canvas", dark("qrComboCanvas") == 0)
            check("[hl] Hide re-masks the token",
                  pg.evaluate("() => document.getElementById('qrTokenText').textContent") == ""
                  and pg.evaluate("() => document.getElementById('qrTokenText').classList.contains('hidden')"))

            st = pg.evaluate("() => window.__jvlQrSelfTest()")
            check("[hl] in-page self-test passes", st.get("fail") == 0 and st.get("pass") > 0)
            check("[hl] no uncaught page errors", not errs)
            browser.close()
    finally:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
