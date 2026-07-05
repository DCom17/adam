"""
Adam — secure-context banner tests (v0.9.0, Slice 2).

Static (always-run, TestClient): the PWA (/) and console (/console) carry a guarded
secure-context banner + the pure helper window.__jvlSecCtxWarn, the banner adds no
remote asset / no secret / no innerHTML, and existing controls/actions are intact.

Headless (CDP, only if Chrome is installed — otherwise SKIPPED so the suite stays
portable): the live __jvlSecCtxWarn returns the right answer for the required cases,
the banner is hidden when the page is loaded over 127.0.0.1, and the rule CAN show it
for a simulated non-loopback http host.

Run:  python test_pwa_banner.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import config

if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = "test-token-" + "a" * 48
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

_SANDBOX = Path(tempfile.mkdtemp(prefix="jvl_banner_test_"))
config.PROPOSED_CHANGES_FILE = _SANDBOX / "proposed_changes.json"
config.APPROVALS_FILE = _SANDBOX / "approvals.json"

import job_store          # noqa: E402
import server             # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

job_store.init(_SANDBOX / "jobs.db")
TOKEN = server.ADAM_TOKEN
SECRETS = [s for s in (
    server.ADAM_TOKEN,
    getattr(config, "VAPID_PUBLIC_KEY", ""),
    getattr(config, "TWILIO_AUTH_TOKEN", ""),
) if s]
client = TestClient(server.app)

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


def _no_secret(text: str) -> bool:
    return all(s not in text for s in SECRETS)


def _banner_segment(html: str) -> str:
    i = html.find('id="secCtxBanner"')
    return html[max(0, i - 80): i + 1500] if i >= 0 else ""


def main() -> int:
    print("\n[1] PWA (/) carries the guarded banner + helper")
    html = client.get("/").text
    check("/ -> serves index", "<body" in html.lower())
    check("banner element present", 'id="secCtxBanner"' in html)
    check("dismiss button present", 'id="secCtxDismiss"' in html)
    check("pure helper present", "window.__jvlSecCtxWarn" in html)
    check("rule checks protocol http", 'protocol === "http:"' in html)
    for host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        check(f"rule treats {host!r} as loopback", host in html)

    print("\n[2] Banner adds no remote asset / no secret / no innerHTML")
    seg = _banner_segment(html)
    check("banner has no src= (no remote/img asset)", "src=" not in seg)
    check("banner has no <img>", "<img" not in seg.lower())
    check("banner has no href=", "href=" not in seg)
    check("banner introduces no innerHTML", "innerHTML" not in seg)
    check("banner makes no network call (no fetch/XHR)",
          "fetch(" not in seg and "XMLHttpRequest" not in seg)
    check("page (with banner) leaks no secret", _no_secret(html))

    print("\n[3] Existing PWA controls still present (regression guard)")
    for cid in ("orb", "activate", "mute", "composerInput", "send", "gear"):
        check(f"control id={cid!r} present", f'id="{cid}"' in html)

    print("\n[4] Console (/console) carries the banner + helper; actions intact")
    chtml = client.get("/console").text
    check("console banner present", 'id="secCtxBanner"' in chtml)
    check("console helper present", "window.__jvlSecCtxWarn" in chtml)
    cseg = _banner_segment(chtml)
    check("console banner no remote asset", "src=" not in cseg and "<img" not in cseg.lower())
    check("console banner no innerHTML", "innerHTML" not in cseg)
    for label in ("Approve & Apply", "Apply", "Deny", "Refresh"):
        check(f"console action {label!r} still present", label in chtml)
    check("console page leaks no secret", _no_secret(chtml))

    print("\n[5] Headless: live helper + banner visibility")
    rc = _headless_checks()
    if rc == "skip":
        print("  SKIP  Chrome not found — headless banner checks skipped (static checks cover the rule)")

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {_SANDBOX}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


def _headless_checks():
    """Run the page in headless Chrome via CDP. Returns 'skip' if Chrome is absent."""
    import json as _json
    import socket as _socket
    import struct as _struct
    import subprocess as _sp
    import threading as _th
    import time as _t
    import urllib.request as _u

    chrome = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
    if not Path(chrome).exists():
        return "skip"

    import uvicorn
    port = 8021
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    _th.Thread(target=srv.run, daemon=True).start()
    ready = False
    for _ in range(60):
        try:
            if _u.urlopen(f"http://127.0.0.1:{port}/ping", timeout=2).status == 200:
                ready = True
                break
        except Exception:
            _t.sleep(0.25)
    check("headless: test server reachable", ready)

    dport = 9226
    udd = tempfile.mkdtemp(prefix="cdp_banner_")
    proc = _sp.Popen([chrome, "--headless=new", f"--remote-debugging-port={dport}",
                      f"--user-data-dir={udd}", "--no-first-run", "--no-default-browser-check",
                      "--disable-gpu", "about:blank"],
                     stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    try:
        ws = None
        for _ in range(40):
            try:
                tabs = _json.loads(_u.urlopen(f"http://127.0.0.1:{dport}/json", timeout=5).read())
                pg = [t for t in tabs if t.get("type") == "page"]
                if pg:
                    ws = pg[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                _t.sleep(0.25)
        if not ws:
            check("headless: devtools attached", False)
            return None

        host, _, wpath = "127.0.0.1", dport, ws.split(str(dport), 1)[1]
        s = _socket.create_connection((host, dport), timeout=15)
        s.settimeout(20)
        import base64 as _b64
        key = _b64.b64encode(os.urandom(16)).decode()
        s.send((f"GET {wpath} HTTP/1.1\r\nHost: {host}:{dport}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += s.recv(4096)

        def send(o):
            d = _json.dumps(o).encode(); h = bytearray([0x81]); n = len(d); m = os.urandom(4)
            if n < 126:
                h.append(0x80 | n)
            elif n < 65536:
                h.append(0x80 | 126); h += _struct.pack(">H", n)
            else:
                h.append(0x80 | 127); h += _struct.pack(">Q", n)
            h += m; s.send(bytes(h) + bytes(x ^ m[i % 4] for i, x in enumerate(d)))

        def rf():
            def rd(n):
                d = b""
                while len(d) < n:
                    d += s.recv(n - len(d))
                return d
            b0, b1 = rd(2); ln = b1 & 0x7f
            if ln == 126:
                ln = _struct.unpack(">H", rd(2))[0]
            elif ln == 127:
                ln = _struct.unpack(">Q", rd(8))[0]
            return rd(ln)

        _id = [0]

        def cmd(method, params=None):
            _id[0] += 1; mid = _id[0]
            send({"id": mid, "method": method, "params": params or {}})
            while True:
                m = _json.loads(rf().decode("utf-8", "replace"))
                if m.get("id") == mid:
                    return m

        def ev(expr):
            r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("result", {}).get("value")

        cmd("Page.enable"); cmd("Runtime.enable")
        cmd("Page.navigate", {"url": f"http://127.0.0.1:{port}/"}); _t.sleep(2.0)

        check("headless: __jvlSecCtxWarn('http:','192.168.1.26') == true",
              ev("window.__jvlSecCtxWarn('http:','192.168.1.26')") is True)
        check("headless: __jvlSecCtxWarn('http:','localhost') == false",
              ev("window.__jvlSecCtxWarn('http:','localhost')") is False)
        check("headless: __jvlSecCtxWarn('http:','127.0.0.1') == false",
              ev("window.__jvlSecCtxWarn('http:','127.0.0.1')") is False)
        check("headless: __jvlSecCtxWarn('http:','::1') == false",
              ev("window.__jvlSecCtxWarn('http:','::1')") is False)
        check("headless: __jvlSecCtxWarn('https:','example.com') == false",
              ev("window.__jvlSecCtxWarn('https:','example.com')") is False)
        check("headless: banner hidden when loaded over 127.0.0.1",
              ev("document.getElementById('secCtxBanner').hidden") is True)
        # Simulate a non-loopback http host: the rule shows the banner.
        shown = ev("(function(){var b=document.getElementById('secCtxBanner');"
                   "if(window.__jvlSecCtxWarn('http:','192.168.1.26')){b.hidden=false;}"
                   "return b.hidden===false;})()")
        check("headless: banner CAN show for simulated non-loopback http", shown is True)
        try:
            s.close()
        except Exception:
            pass
        return None
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
