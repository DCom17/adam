"""
Adam Plus gate — what the license actually unlocks.

Adam Plus covers two things:
  * PHONE / REMOTE ACCESS — reaching this Adam from any device other than the PC it runs
    on (Tailscale Serve, LAN, any proxy). Enforced on EVERY request, not just the setup
    wizard: before this, a phone connected during the trial kept working forever.
  * OPERATOR MODE — a chat running as the user's own full Claude Code.

Both follow `licensing.is_entitled()` — a build that isn't selling, a valid license, or
the free trial — so nothing here can lock out a paying user, the owner's dev box, or a
dormant beta. Using Adam at the PC itself (loopback) is never gated: the desktop app
stays free in full.

"Remote" is decided from the request alone, no network calls: a loopback client AND a
loopback Host AND no proxy forwarding header means the PC itself. Tailscale Serve proxies
from 127.0.0.1 but keeps the phone's Host (`<machine>.<tailnet>.ts.net`) and adds
X-Forwarded-For, so it reads as remote. An honest bar, not DRM — same posture as
licensing.py.
"""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse, JSONResponse

import licensing

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Reachable remotely even when locked: the paywall must be able to load, a phone must be
# able to activate a key it was just sent, liveness probes must answer, and the Twilio
# SMS webhook is its own add-on with its own signature check (not "phone access").
_OPEN_PATHS = {
    "/ping", "/health", "/license", "/sms",
    "/sw.js", "/manifest.json", "/icon.png", "/icon-maskable.png", "/logo.png",
    "/favicon.ico", "/adam-ui.css", "/license-agreement", "/legal",
}
_OPEN_PREFIXES = ("/legal/",)

PHONE_MESSAGE = ("Using Adam from your phone or another device is part of Adam Plus, "
                 "and this Adam's free trial has ended. Adam still works in full on the PC "
                 "it runs on.")
OPERATOR_MESSAGE = ("Operator mode (full Claude Code inside Adam) is part of Adam Plus, "
                    "and this Adam's free trial has ended. Normal mode is unaffected.")


def _host_only(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):                       # [::1]:8000
        return h[1:h.find("]")] if "]" in h else h
    if h.count(":") == 1:                       # name:port (a bare IPv6 has many colons)
        return h.split(":", 1)[0]
    return h


def is_remote(request) -> bool:
    """True when the request did NOT come from a browser on this PC. Never raises."""
    try:
        if request.headers.get("x-forwarded-for") or request.headers.get("forwarded"):
            return True
        client = request.client.host if request.client else ""
        if client not in _LOOPBACK_HOSTS:
            return True
        return _host_only(request.headers.get("host", "")) not in _LOOPBACK_HOSTS
    except Exception:  # noqa: BLE001 — a gate must never crash a request
        return False   # fail-open, like is_entitled()


def operator_available(operator_mode_on: bool) -> bool:
    """Operator can run on this install right now: switched on in settings AND entitled."""
    return bool(operator_mode_on) and licensing.is_entitled()


def lock_detail(feature: str) -> dict:
    """The 402 body every Plus lock returns, so the app renders one consistent prompt."""
    return {
        "locked": True,
        "feature": feature,
        "message": OPERATOR_MESSAGE if feature == "operator" else PHONE_MESSAGE,
        "buy_url": licensing.BUY_URL,
    }


def _is_open_path(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


def remote_lock_response(request):
    """The response for a request that must be refused, or None to let it through."""
    if licensing.is_entitled() or not is_remote(request) or _is_open_path(request.url.path):
        return None
    wants_page = (request.method == "GET"
                  and "text/html" in (request.headers.get("accept") or ""))
    if wants_page:
        return HTMLResponse(_paywall_page(), status_code=402,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse({"detail": lock_detail("phone")}, status_code=402)


def _paywall_page() -> str:
    """A self-contained page (no external assets beyond the open CSS route): what happened,
    a buy button, and a key field that activates right here on the phone — the app already
    stored its access token in this browser, so POST /license works from the phone."""
    buy = html.escape(licensing.BUY_URL, quote=True)
    msg = html.escape(PHONE_MESSAGE)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Adam Plus</title>
<style>
  body {{ margin:0; min-height:100vh; background:#05080c; color:#d8f6ff;
         font:15px/1.5 -apple-system, system-ui, "Segoe UI", sans-serif;
         display:flex; align-items:center; justify-content:center; padding:24px 16px;
         box-sizing:border-box; }}
  .card {{ max-width:420px; width:100%; }}
  h1 {{ font-size:22px; letter-spacing:.12em; margin:0 0 10px; color:#21e6ff; }}
  p {{ color:#9fc3cf; margin:0 0 16px; }}
  .price {{ font-size:26px; font-weight:800; color:#fff; }}
  .fine {{ font-size:12px; color:#6f8f99; }}
  a.buy {{ display:block; text-align:center; text-decoration:none; background:#21e6ff;
           color:#001018; font-weight:800; letter-spacing:.08em; padding:12px; border-radius:8px;
           margin:14px 0 22px; }}
  input {{ width:100%; box-sizing:border-box; padding:11px; border-radius:8px;
           border:1px solid rgba(33,230,255,.35); background:#0b1218; color:#d8f6ff;
           font:13px ui-monospace, monospace; }}
  button {{ width:100%; margin-top:8px; padding:11px; border-radius:8px; cursor:pointer;
            border:1px solid rgba(33,230,255,.5); background:transparent; color:#21e6ff;
            font-weight:700; letter-spacing:.06em; }}
  #msg {{ min-height:20px; font-size:13px; margin-top:8px; }}
</style></head>
<body><main class="card">
  <h1>ADAM PLUS</h1>
  <p>{msg}</p>
  <div><span class="price">$24.99</span> <span class="fine">one-time · not a subscription</span></div>
  <a class="buy" href="{buy}" target="_blank" rel="noopener">GET ADAM PLUS</a>
  <label class="fine" for="key">Already have a key? Paste it here:</label>
  <input id="key" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="ADAM1-...">
  <button id="go" type="button">ACTIVATE</button>
  <div id="msg" role="status"></div>
</main>
<script>
  document.getElementById("go").onclick = async function () {{
    var out = document.getElementById("msg");
    var key = document.getElementById("key").value.trim();
    if (!key) {{ out.textContent = "Paste your license key first."; return; }}
    var token = ""; try {{ token = localStorage.getItem("jarvis_token") || ""; }} catch (_) {{}}
    if (!token) {{ out.textContent = "This phone isn't signed in to Adam. Activate the key on your PC instead: gear menu → AI plan → License."; return; }}
    out.textContent = "Checking…";
    try {{
      var r = await fetch("/license", {{ method: "POST",
        headers: {{ "Content-Type": "application/json", "Authorization": "Bearer " + token }},
        body: JSON.stringify({{ key: key }}) }});
      var d = {{}}; try {{ d = await r.json(); }} catch (_) {{}}
      if (r.ok && d.valid) {{ out.textContent = "Activated. Opening Adam…"; setTimeout(function () {{ location.replace("/"); }}, 700); }}
      else {{ out.textContent = (d && d.detail) || "That key didn't activate."; }}
    }} catch (_) {{ out.textContent = "Couldn't reach Adam."; }}
  }};
</script>
</body></html>"""
