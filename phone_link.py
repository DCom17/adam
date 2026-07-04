"""
Jarvis Voice Local — phone-reachable URL detection for the Connect-phone QR.

The Connect-phone QR used to encode whatever URL you signed in with — which on the
PC is `localhost`, useless to a phone. This figures out URLs a phone can actually
reach, in preference order:

  1. a configured public_base_url (if set),
  2. the Tailscale Serve HTTPS URL that proxies to THIS app (works anywhere, and
     it's HTTPS, which iPhone needs for mic/voice/PWA),
  3. the PC's LAN IP (same-Wi-Fi only, http -> no iPhone voice; last resort).

localhost / 127.0.0.1 are always excluded. Pure stdlib; the Tailscale CLI is called
read-only with a short timeout and never raises.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

_TS_FALLBACK_PATHS = [
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]


def _resolve_tailscale() -> str | None:
    p = shutil.which("tailscale")
    if p:
        return p
    for cand in _TS_FALLBACK_PATHS:
        try:
            if Path(cand).is_file():
                return cand
        except Exception:
            continue
    return None


def _run_ts(args: list[str], timeout: int = 4) -> str | None:
    """Run a read-only tailscale subcommand, return stdout or None. Never raises."""
    ex = _resolve_tailscale()
    if not ex:
        return None
    try:
        p = subprocess.run([ex, *args], capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else None
    except Exception:
        return None


def lan_ip() -> str | None:
    """The LAN IPv4 this machine would use to reach the internet (no traffic is sent).
    None if only loopback is available."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None


def parse_serve_url(serve_json_text: str, app_port: int) -> str | None:
    """From `tailscale serve status --json`, the https://host:port whose handler
    proxies to THIS app's port (so we don't grab a different app's mapping)."""
    try:
        data = json.loads(serve_json_text)
    except Exception:
        return None
    web = data.get("Web") or {}
    for hostport, conf in web.items():
        handlers = (conf or {}).get("Handlers") or {}
        for _path, h in handlers.items():
            proxy = str((h or {}).get("Proxy") or "").rstrip("/")
            if proxy.endswith(":" + str(app_port)):
                return "https://" + hostport
    return None


def parse_dns_name(status_json_text: str) -> str | None:
    """This device's MagicDNS name from `tailscale status --json` (Self.DNSName)."""
    try:
        data = json.loads(status_json_text)
    except Exception:
        return None
    dn = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return dn or None


def _is_local(u: str) -> bool:
    u = (u or "").lower()
    return "localhost" in u or "127.0.0.1" in u or "0.0.0.0" in u


def phone_urls(app_port: int, public_base_url: str | None = None) -> list[dict]:
    """Ordered phone-reachable URL candidates (best first). Each: {url, label,
    secure, note?}. localhost is never included."""
    cands: list[dict] = []

    pb = (public_base_url or "").strip().rstrip("/")
    if pb and not _is_local(pb):
        cands.append({"url": pb, "label": "Your configured URL",
                      "secure": pb.lower().startswith("https://")})

    # ONLY an actual Tailscale Serve mapping to this app counts as a usable HTTPS URL.
    # We must NOT guess "https://<dns>" from the MagicDNS name alone: if no serve is
    # configured there's nothing listening, and a phone pointed at it just hangs on a
    # black screen. No serve mapping -> no secure candidate -> the wizard keeps showing
    # the `tailscale serve` command until it's actually run.
    serve = _run_ts(["serve", "status", "--json"])
    ts_url = parse_serve_url(serve, app_port) if serve else None
    if ts_url:
        cands.append({"url": ts_url, "label": "Tailscale — works anywhere", "secure": True})

    ip = lan_ip()
    if ip:
        cands.append({"url": f"http://{ip}:{app_port}", "label": "Same Wi-Fi only",
                      "secure": False,
                      "note": "same network only; iPhone voice/mic need the HTTPS (Tailscale) URL"})

    seen: set[str] = set()
    out: list[dict] = []
    for c in cands:
        if _is_local(c["url"]) or c["url"] in seen:
            continue
        seen.add(c["url"])
        out.append(c)
    return out


def best_phone_url(app_port: int, public_base_url: str | None = None) -> dict | None:
    """Prefer a secure (HTTPS) candidate; else the first; else None."""
    cands = phone_urls(app_port, public_base_url)
    return next((c for c in cands if c.get("secure")), cands[0] if cands else None)
