"""
Jarvis Voice Local — phone-reachable URL detection tests.

Covers the Tailscale JSON parsing (serve mapping matched to THIS app's port, and the
MagicDNS fallback), localhost exclusion, and candidate ordering (HTTPS preferred).
Network/CLI calls are stubbed, so it's offline + deterministic.

Run:  python test_phone_link.py   (exit 0 = all passed)
"""

from __future__ import annotations

import json
import sys

import phone_link as pl

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


SERVE_JSON = json.dumps({
    "TCP": {"8443": {"HTTPS": True}},
    "Web": {
        "chainforge.tail4a86a8.ts.net:8443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8010"}}
        }
    },
})
# a serve config for a DIFFERENT app (Morrow on 8849) — must NOT match app 8010
SERVE_JSON_OTHER = json.dumps({
    "Web": {"chainforge.tail4a86a8.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8849"}}}}
})
STATUS_JSON = json.dumps({"Self": {"DNSName": "chainforge.tail4a86a8.ts.net."}})


def main() -> int:
    print("[parsing]")
    check("serve url matches this app's port",
          pl.parse_serve_url(SERVE_JSON, 8010) == "https://chainforge.tail4a86a8.ts.net:8443")
    check("serve url ignores another app's mapping",
          pl.parse_serve_url(SERVE_JSON_OTHER, 8010) is None)
    check("bad serve json -> None", pl.parse_serve_url("not json", 8010) is None)
    check("dns name parsed + trailing dot stripped",
          pl.parse_dns_name(STATUS_JSON) == "chainforge.tail4a86a8.ts.net")

    check("localhost flagged local", pl._is_local("http://localhost:8010"))
    check("127.0.0.1 flagged local", pl._is_local("http://127.0.0.1:8010"))
    check("tailnet url not local", not pl._is_local("https://chainforge.tail4a86a8.ts.net:8443"))

    print("\n[candidate ordering — stubbed CLI/LAN]")
    orig_run, orig_lan = pl._run_ts, pl.lan_ip
    try:
        # Tailscale Serve present + a LAN IP available
        pl._run_ts = lambda args, timeout=4: SERVE_JSON if args[:2] == ["serve", "status"] else STATUS_JSON
        pl.lan_ip = lambda: "192.168.1.50"
        cands = pl.phone_urls(8010, public_base_url="")
        urls = [c["url"] for c in cands]
        check("tailscale https url is a candidate",
              "https://chainforge.tail4a86a8.ts.net:8443" in urls)
        check("LAN url is a candidate", "http://192.168.1.50:8010" in urls)
        check("no localhost in candidates", not any(pl._is_local(u) for u in urls))
        best = pl.best_phone_url(8010, "")
        check("best is the secure (HTTPS) tailscale url",
              best and best["url"] == "https://chainforge.tail4a86a8.ts.net:8443" and best["secure"])

        # configured public_base_url wins and is offered first
        cands2 = pl.phone_urls(8010, public_base_url="https://jarvis.example.com")
        check("configured public_base_url is first",
              cands2 and cands2[0]["url"] == "https://jarvis.example.com")

        # No Tailscale at all -> only LAN, and best is the LAN url (insecure)
        pl._run_ts = lambda args, timeout=4: None
        cands3 = pl.phone_urls(8010, "")
        check("no tailscale -> LAN-only candidate", [c["url"] for c in cands3] == ["http://192.168.1.50:8010"])
        check("best falls back to LAN when nothing secure", pl.best_phone_url(8010, "")["secure"] is False)

        # Tailscale up but NO serve mapping -> NO https candidate (never guess from DNS,
        # which would point a phone at a dead port -> black screen). Only LAN remains.
        pl._run_ts = lambda args, timeout=4: (None if args[:2] == ["serve", "status"] else STATUS_JSON)
        cands4 = pl.phone_urls(8010, "")
        check("no serve mapping -> no https candidate",
              all(not c["url"].startswith("https://") for c in cands4))
        check("no serve mapping -> best is insecure LAN (wizard will guide serve setup)",
              pl.best_phone_url(8010, "") and pl.best_phone_url(8010, "")["secure"] is False)
    finally:
        pl._run_ts, pl.lan_ip = orig_run, orig_lan

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
