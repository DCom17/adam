"""
Connect-phone helper for Adam.

Inspects your Tailscale state and exposes the Adam backend to your phone over
Tailscale Serve HTTPS — the supported mobile path.

DEFAULT is read-only: with no flags it only INSPECTS (read-only `tailscale status` /
`serve status`) and PRINTS the exact command to run. It reads or prints no token, and
never touches Adam config / .env / settings.json / the server / scripts/voice_server.

Two opt-in flags perform the single mutation this helper is allowed to make — running
the port-scoped `tailscale serve` ON or OFF, and nothing else. They ask for confirmation
first, and NEVER run `serve reset`, `funnel`, `login`, or `set`:
    --apply   run the recommended `tailscale serve --bg --https=<port> http://127.0.0.1:<target>`
    --off     stop ONLY the Adam serve on that HTTPS port (`serve --https=<port> off`)

If another app already serves on the tailnet's :443 (any existing proxy to a local
port), Adam is recommended on a separate HTTPS port (:8443), and --apply REFUSES to
overwrite any port already in use — so an existing serve is never clobbered.

Usage:
    python scripts/connect-phone.py            # inspect + print guidance (read-only)
    python scripts/connect-phone.py --json     # machine-readable diagnostic (no secrets)
    python scripts/connect-phone.py --apply     # run the serve command (asks to confirm)
    python scripts/connect-phone.py --apply --yes   # ...without the prompt (non-interactive)
    python scripts/connect-phone.py --off       # stop the Adam serve (port-scoped)
    python scripts/connect-phone.py --port 8443        # force the Adam HTTPS port
    python scripts/connect-phone.py --target-port 8010 # override the local Adam port
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config        # for the local Adam port (never the token)
import onboarding     # reuse the Tailscale detection (PATH + standard Windows paths)

DEFAULT_ALT_HTTPS = 8443    # where we put Adam when :443 is taken


# --- Tailscale (all READ-ONLY) --------------------------------------------------

def resolve_tailscale() -> str | None:
    """Locate the tailscale CLI via onboarding (PATH first, then standard install paths)."""
    return onboarding._resolve_tailscale()


def _run(ts_path: str, args: list[str], timeout: int = 6) -> str | None:
    """Run a READ-ONLY tailscale subcommand and return stdout (best-effort). Never
    raises, never mutates anything. Returns None only if the process couldn't run."""
    try:
        p = subprocess.run([ts_path, *args], capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.stdout is not None else ""
    except Exception:
        return None


def parse_serve_status(text: str | None) -> dict:
    """Parse `tailscale serve status` output (read-only). Determines whether :443 is in
    use, which HTTPS ports are served, and which local port each serve proxies to —
    so callers can tell an Adam serve from another app's without naming any app."""
    info = {
        "has_serve": False,
        "served_https_ports": [],
        "port_443_in_use": False,
        "proxy_targets": {},   # https port -> proxied local port (when parseable)
        "raw": (text or "").strip(),
    }
    if not text or "no serve config" in text.lower():
        return info
    port = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"https://([^\s/]+)", line)
        if m:
            host = m.group(1)
            info["has_serve"] = True
            pm = re.search(r":(\d+)$", host)
            port = int(pm.group(1)) if pm else 443
            if port not in info["served_https_ports"]:
                info["served_https_ports"].append(port)
            if port == 443:
                info["port_443_in_use"] = True
            continue
        tm = re.search(r"proxy https?://127\.0\.0\.1:(\d+)", line)
        if tm and port is not None:
            info["proxy_targets"][port] = int(tm.group(1))
    return info


def get_tailnet_name(ts_path: str | None, serve_info: dict) -> str | None:
    """Best-effort MagicDNS name (e.g. host.tailnet.ts.net). Tries `status --json`'s
    Self.DNSName, then falls back to the host parsed from an existing serve line."""
    if ts_path:
        out = _run(ts_path, ["status", "--json"], timeout=6)
        if out:
            try:
                dns = ((json.loads(out).get("Self") or {}).get("DNSName") or "").rstrip(".")
                if dns:
                    return dns
            except Exception:
                pass
    m = re.search(r"https://([^\s/:]+)", serve_info.get("raw", ""))
    return m.group(1) if m else None


# --- Pure builders (safe, testable; never emit reset/funnel) --------------------

def recommend_https_port(serve_info: dict, override: int | None = None) -> int:
    if override:
        return int(override)
    return DEFAULT_ALT_HTTPS if serve_info.get("port_443_in_use") else 443


def build_serve_command(https_port: int, target_port: int) -> str:
    return f"tailscale serve --bg --https={https_port} http://127.0.0.1:{target_port}"


def build_teardown_command(https_port: int) -> str:
    return f"tailscale serve --https={https_port} off"


def build_phone_url(tailnet_name: str | None, https_port: int) -> str:
    name = tailnet_name or "<machine>.<tailnet>.ts.net"
    return f"https://{name}/" if int(https_port) == 443 else f"https://{name}:{https_port}/"


# --- Apply mode (opt-in, explicit mutation — serve ON/OFF only) -----------------

def _serve_run(ts_path: str, args: list[str]) -> dict:
    """Run one `tailscale serve …` mutation and return a plain result dict. Never
    raises. The argv is fixed by the callers below — this only ever executes the
    port-scoped serve ON/OFF, never reset/funnel/login/set."""
    argv = [ts_path, "serve", *args]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return {"ok": p.returncode == 0, "returncode": p.returncode,
                "stdout": (p.stdout or "").strip(), "stderr": (p.stderr or "").strip(),
                "command": " ".join(argv[1:])}
    except Exception as e:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(e),
                "command": " ".join(argv[1:])}


def apply_serve(ts_path: str, https_port: int, target_port: int) -> dict:
    """Run ONLY `tailscale serve --bg --https=<port> http://127.0.0.1:<target>` — the
    one command that shares Adam to the tailnet. Callers must have already refused to
    overwrite an occupied port, so an existing serve (e.g. another app on :443) is untouched."""
    return _serve_run(ts_path, ["--bg", f"--https={int(https_port)}",
                                f"http://127.0.0.1:{int(target_port)}"])


def turn_off_serve(ts_path: str, https_port: int) -> dict:
    """Run ONLY the port-scoped `tailscale serve --https=<port> off` (never `reset`), so
    stopping Adam leaves every other serve intact."""
    return _serve_run(ts_path, [f"--https={int(https_port)}", "off"])


def _confirm(prompt: str, assume_yes: bool) -> bool:
    """Ask before mutating. --yes skips it. If stdin isn't a console we refuse rather
    than assume — an accidental non-interactive run changes nothing without --yes."""
    if assume_yes:
        return True
    try:
        if not sys.stdin or not sys.stdin.isatty():
            print("    (non-interactive shell — re-run with --yes to confirm; nothing changed.)")
            return False
        return input("    %s [y/N] " % prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _do_apply(d: dict, assume_yes: bool) -> int:
    if not d["tailscale_found"]:
        print("[!] Tailscale isn't installed (see the guidance above). Nothing changed.")
        return 1
    if not d["tailscale_logged_in"]:
        print("[!] Tailscale isn't signed in / running (state: %s)." % (d["tailscale_backend_state"] or "unknown"))
        print("    Open the Tailscale app, sign in, then run this again. Nothing changed.")
        return 1
    port = int(d["recommended_https_port"])
    if port in d["served_https_ports"]:
        tgt = (d.get("serve_proxy_targets") or {}).get(port)
        who = "another app (proxying 127.0.0.1:%d)" % tgt if tgt and tgt != int(d["target_port"]) \
              else "an existing serve"
        print("[!] :%d is already used by %s — refusing to overwrite it." % (port, who))
        print("    If that's already Adam, you're connected:  %s" % d["phone_url"])
        print("    To put Adam on a different port instead:  --apply --port <PORT>")
        return 1
    print("\nAbout to share Adam to your tailnet over HTTPS by running:")
    print("    %s" % d["serve_command"])
    if not _confirm("Run it now?", assume_yes):
        print("    Cancelled — nothing changed.")
        return 0
    res = apply_serve(resolve_tailscale(), port, int(d["target_port"]))
    if not res["ok"]:
        print("[!] `tailscale serve` did not succeed: %s" % (res["stderr"] or res["stdout"] or "unknown error"))
        print("    Nothing else was changed. You can run the command above by hand.")
        return 1
    print("[ok] Adam is now shared on your tailnet.")
    print("     Open on your phone (Tailscale ON):  %s" % d["phone_url"])
    print("     Stop sharing later:  %s" % d["teardown_command"])
    return 0


def _do_off(d: dict, off_port: int, assume_yes: bool) -> int:
    if not d["tailscale_found"]:
        print("[!] Tailscale isn't installed. Nothing changed.")
        return 1
    off_port = int(off_port)
    tgt = (d.get("serve_proxy_targets") or {}).get(off_port)
    if tgt and tgt != int(d["target_port"]):
        print("[!] Refusing to turn off :%d — that serve proxies 127.0.0.1:%d, which is "
              "another app, not Adam." % (off_port, tgt))
        print("    Pass --port <the Adam port> if you really mean a specific one. Nothing changed.")
        return 1
    cmd = build_teardown_command(off_port)
    print("\nAbout to stop ONLY the Adam serve on :%d by running:" % off_port)
    print("    %s" % cmd)
    if not _confirm("Run it now?", assume_yes):
        print("    Cancelled — nothing changed.")
        return 0
    res = turn_off_serve(resolve_tailscale(), off_port)
    if not res["ok"]:
        print("[!] `tailscale serve off` did not succeed: %s" % (res["stderr"] or res["stdout"] or "unknown error"))
        return 1
    print("[ok] Stopped the Adam serve on :%d. Any other serve is untouched." % off_port)
    return 0


# --- Diagnostic gather (no secrets, ever) ---------------------------------------

def gather(https_override: int | None = None, target_override: int | None = None) -> dict:
    target_port = int(target_override) if target_override else int(config.PORT)
    ts_path = resolve_tailscale()
    status_text = _run(ts_path, ["status"]) if ts_path else None
    # Signed in? `tailscale status` prints output even when LOGGED OUT, so "reachable"
    # isn't "signed in". Read BackendState from status --json: only "Running" means
    # signed in and up. Anything else (NeedsLogin/Stopped/NoState) -> not signed in.
    backend_state = None
    if ts_path:
        sj = _run(ts_path, ["status", "--json"])
        if sj:
            try:
                backend_state = json.loads(sj).get("BackendState") or None
            except Exception:
                backend_state = None
    logged_in = backend_state == "Running"
    serve_text = _run(ts_path, ["serve", "status"]) if ts_path else None
    serve_info = parse_serve_status(serve_text)
    https_port = recommend_https_port(serve_info, https_override)
    tailnet = get_tailnet_name(ts_path, serve_info)

    warnings = [
        "Do NOT run `tailscale serve reset` if another serve exists — it removes ALL "
        "serve configs (including another app's serve on :443).",
        "Do NOT use Tailscale Funnel and do NOT expose the backend on the public internet.",
        "The cloudflared public path is advanced and REQUIRES Cloudflare Access "
        "(see docs/ADVANCED_REMOTE.md).",
    ]
    if serve_info["port_443_in_use"]:
        tgt = serve_info["proxy_targets"].get(443)
        which = "another app (127.0.0.1:%d)" % tgt if tgt and tgt != target_port else "another app"
        warnings.insert(0, "Tailscale Serve :443 is already used by %s — Adam is "
                           "recommended on :%d so that serve stays intact." % (which, https_port))

    return {
        "tailscale_found": bool(ts_path),
        "tailscale_path": ts_path or None,         # a path, not a secret
        "tailscale_status_reachable": bool(status_text),
        "tailscale_logged_in": logged_in,          # BackendState == "Running"
        "tailscale_backend_state": backend_state,  # diagnostic, not a secret
        "serve_has_config": serve_info["has_serve"],
        "serve_443_in_use": serve_info["port_443_in_use"],
        "serve_proxy_targets": serve_info["proxy_targets"],
        "served_https_ports": serve_info["served_https_ports"],
        "target_port": target_port,
        "recommended_https_port": https_port,
        "tailnet_name": tailnet,                   # private hostname, not a secret
        "serve_command": build_serve_command(https_port, target_port),
        "teardown_command": build_teardown_command(https_port),
        "phone_url": build_phone_url(tailnet, https_port),
        "warnings": warnings,
    }


# --- Output ---------------------------------------------------------------------

def _print_human(d: dict) -> None:
    p = print
    p("\nAdam - connect your phone (Tailscale Serve, supported path)")
    p("=" * 70)
    if not d["tailscale_found"]:
        p("[!] Tailscale was not found (PATH or the standard Windows install paths).")
        p("    Install it from https://tailscale.com/download/windows, sign in, then")
        p("    re-run this helper. Desktop-local (http://localhost) needs none of this.")
        return
    p("Tailscale: found%s" % (" (status reachable)" if d["tailscale_status_reachable"]
                              else " (status unavailable - is Tailscale running/signed in?)"))
    if d["serve_has_config"]:
        others = sorted(t for pt, t in (d.get("serve_proxy_targets") or {}).items()
                        if t != int(d["target_port"]))
        extra = " incl. another app on 127.0.0.1:%d" % others[0] if others else ""
        p("Existing serve: yes, HTTPS ports %s%s" % (d["served_https_ports"] or "?", extra))
    else:
        p("Existing serve: none")
    p("")
    p("Recommended: serve Adam (local port %d) on HTTPS port %d." %
      (d["target_port"], d["recommended_https_port"]))
    if d["serve_443_in_use"]:
        p("  (:443 is already taken, so Adam uses :%d to avoid clobbering it.)"
          % d["recommended_https_port"])
    p("")
    p("1) Run this on the PC (or let the helper do it: re-run with --apply):")
    p("     %s" % d["serve_command"])
    p("   If `tailscale` isn't on your PATH, prefix with the full path, e.g.:")
    p('     & "%s" %s' % (d["tailscale_path"] or r"C:\Program Files\Tailscale\tailscale.exe",
                          d["serve_command"].split(" ", 1)[1]))
    p("   Then check it:  tailscale serve status")
    p("")
    p("2) Your Adam phone URL will be:")
    p("     %s" % d["phone_url"])
    p("")
    p("3) On the iPhone:")
    p("     - Tailscale app ON (same account/tailnet)")
    p("     - open the URL above in Safari")
    p("     - confirm the insecure-connection banner is HIDDEN (proper HTTPS)")
    p("     - paste your ADAM_TOKEN in the app settings (no 403)")
    p("     - tap Activate, allow the mic, speak -> transcript + reply")
    p("     - Share -> Add to Home Screen; open from the icon; re-confirm auth + voice")
    p("     - negative check (same Wi-Fi): open http://<pc-lan-ip>:%d -> banner SHOWS, "
      "voice unavailable (expected)" % d["target_port"])
    p("")
    p("4) When done, remove ONLY the Adam serve (keeps any other serve intact):")
    p("     %s" % d["teardown_command"])
    p("")
    p("Notes:")
    for w in d["warnings"]:
        p("  - %s" % w)
    p("")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print safe Tailscale Serve guidance for phone access (read-only).")
    ap.add_argument("--json", action="store_true", help="machine-readable diagnostic (no secrets)")
    ap.add_argument("--apply", action="store_true", help="run the recommended `tailscale serve` (opt-in; asks to confirm)")
    ap.add_argument("--off", action="store_true", help="stop ONLY the Adam serve on the chosen HTTPS port (port-scoped)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt for --apply/--off")
    ap.add_argument("--port", type=int, default=None, help="force the Adam HTTPS serve port")
    ap.add_argument("--target-port", type=int, default=None, help="override the local Adam port (default: config.PORT)")
    args = ap.parse_args(argv)

    if args.apply and args.off:
        print("Pick one of --apply or --off, not both.")
        return 2

    d = gather(https_override=args.port, target_override=args.target_port)

    if args.off:
        # Default to the one served port if there's exactly one to remove; otherwise
        # the recommended port (which already honours an explicit --port override).
        if args.port is not None:
            off_port = int(d["recommended_https_port"])
        elif len(d["served_https_ports"]) == 1:
            off_port = int(d["served_https_ports"][0])
        else:
            off_port = int(d["recommended_https_port"])
        return _do_off(d, off_port, args.yes)

    if args.apply:
        return _do_apply(d, args.yes)

    if args.json:
        print(json.dumps(d, indent=2))
    else:
        _print_human(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
