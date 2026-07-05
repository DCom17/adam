"""
Adam — connect-phone helper tests (v1.0 Slice 2).

Proves the helper is read-only, safe, and recommends the right HTTPS port:
  * Tailscale detected on PATH and via the standard Windows fallback;
  * Tailscale absent -> clear guidance, no crash;
  * :443 occupied by another app -> recommends :8443; :443 free -> :443;
  * generated command targets the local Adam port and NEVER contains reset/funnel;
  * output warns not to run `serve reset` when a serve exists;
  * no ADAM_TOKEN in human or JSON output; subprocess failures are handled;
  * gather() only ever issues READ-ONLY tailscale subcommands (no mutation).

The helper file has a hyphen, so it's loaded via importlib.

Run:  python test_connect_phone.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("connect_phone", ROOT / "scripts" / "connect-phone.py")
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

import config  # noqa: E402

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


OTHER_APP_SERVE = (
    "https://examplebox.tail0000ab.ts.net (tailnet only)\n"
    "|-- / proxy http://127.0.0.1:9100\n"
)
OTHER_443_SERVE = (
    "https://examplebox.tail0000ab.ts.net (tailnet only)\n"
    "|-- / proxy http://127.0.0.1:9000\n"
)
Adam_8443_SERVE = (
    "https://examplebox.tail0000ab.ts.net:8443 (tailnet only)\n"
    "|-- / proxy http://127.0.0.1:8010\n"
)
STATUS_JSON = json.dumps({"Self": {"DNSName": "examplebox.tail0000ab.ts.net."}})


def make_runner(serve_text):
    """A fake subprocess.run that records argv and returns canned READ-ONLY output."""
    calls = []

    def run(argv, capture_output=True, text=True, timeout=None):
        calls.append(list(argv))
        sub = list(argv[1:])
        if sub[:1] == ["status"] and "--json" in sub:
            return types.SimpleNamespace(returncode=0, stdout=STATUS_JSON, stderr="")
        if sub[:1] == ["status"]:
            return types.SimpleNamespace(returncode=0, stdout="100.0.0.1 examplebox ...\n", stderr="")
        if sub[:2] == ["serve", "status"]:
            return types.SimpleNamespace(returncode=0, stdout=serve_text, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    run.calls = calls
    return run


def with_env(ts_path, serve_text):
    """Set deterministic detection + a fake read-only runner; return the runner."""
    cp.onboarding._resolve_tailscale = lambda: ts_path
    runner = make_runner(serve_text)
    cp.subprocess.run = runner
    return runner


def main() -> int:
    _saved_resolve = cp.onboarding._resolve_tailscale
    _saved_run = cp.subprocess.run
    try:
        print("\n[1] Pure parsers / builders")
        mi = cp.parse_serve_status(OTHER_APP_SERVE)
        check("other-app serve -> :443 in use", mi["port_443_in_use"] is True)
        check("other-app serve -> proxy target parsed", mi["proxy_targets"] == {443: 9100})
        oi = cp.parse_serve_status(OTHER_443_SERVE)
        check("other :443 serve -> :443 in use", oi["port_443_in_use"] is True)
        check("other :443 serve -> proxy target parsed", oi["proxy_targets"] == {443: 9000})
        none_i = cp.parse_serve_status("No serve config")
        check("no serve -> has_serve False", none_i["has_serve"] is False)
        check("no serve -> :443 free", none_i["port_443_in_use"] is False)
        j = cp.parse_serve_status(Adam_8443_SERVE)
        check("adam :8443 serve parsed", 8443 in j["served_https_ports"] and not j["port_443_in_use"])

        check("recommend :8443 when :443 in use", cp.recommend_https_port(mi) == 8443)
        check("recommend :443 when free", cp.recommend_https_port(none_i) == 443)
        check("override wins", cp.recommend_https_port(mi, override=9443) == 9443)
        cmd = cp.build_serve_command(8443, 8010)
        check("command targets local port 8010", "http://127.0.0.1:8010" in cmd)
        check("command sets --https=8443", "--https=8443" in cmd)
        check("command never contains reset", "reset" not in cmd)
        check("command never contains funnel", "funnel" not in cmd)
        check("teardown is port-scoped off (not reset)",
              cp.build_teardown_command(8443) == "tailscale serve --https=8443 off"
              and "reset" not in cp.build_teardown_command(8443))
        check("url with :8443", cp.build_phone_url("host.ts.net", 8443) == "https://host.ts.net:8443/")
        check("url with :443 omits port", cp.build_phone_url("host.ts.net", 443) == "https://host.ts.net/")

        print("\n[2] Tailscale found on PATH (gather)")
        r = with_env("C:/fake/onpath/tailscale.exe", OTHER_APP_SERVE)
        d = cp.gather()
        check("tailscale_found True", d["tailscale_found"] is True)
        check("path reported", d["tailscale_path"] == "C:/fake/onpath/tailscale.exe")
        check("target_port from config", d["target_port"] == int(config.PORT))
        check("occupied :443 -> recommend 8443", d["recommended_https_port"] == 8443)
        check("tailnet name resolved from status --json",
              d["tailnet_name"] == "examplebox.tail0000ab.ts.net")

        print("\n[3] Detection falls back to standard path (resolver reused)")
        # Drive the real resolver: not on PATH, but a real file via the fallback list.
        cp.onboarding._resolve_tailscale = _saved_resolve
        _w = cp.onboarding.shutil.which
        _fb = cp.onboarding._TAILSCALE_FALLBACK_PATHS
        try:
            cp.onboarding.shutil.which = lambda name: None
            cp.onboarding._TAILSCALE_FALLBACK_PATHS = [sys.executable]
            check("resolver finds standard-path tailscale", cp.resolve_tailscale() == sys.executable)
        finally:
            cp.onboarding.shutil.which = _w
            cp.onboarding._TAILSCALE_FALLBACK_PATHS = _fb

        print("\n[4] :443 occupied by a second app -> still :8443")
        with_env("C:/fake/tailscale.exe", OTHER_443_SERVE)
        d = cp.gather()
        check("second-app :443 -> recommend 8443", d["recommended_https_port"] == 8443)
        check("proxy target reported", d["serve_proxy_targets"].get(443) == 9000)

        print("\n[5] :443 free -> recommend :443")
        with_env("C:/fake/tailscale.exe", "No serve config")
        d = cp.gather()
        check(":443 free -> recommend 443", d["recommended_https_port"] == 443)

        print("\n[6] Tailscale absent -> safe guidance, no crash")
        cp.onboarding._resolve_tailscale = lambda: None
        d = cp.gather()
        check("tailscale_found False", d["tailscale_found"] is False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cp._print_human(d)
        check("absent guidance mentions install", "Install it" in out.getvalue())

        print("\n[7] Generated commands & output never mutate / never reset|funnel")
        with_env("C:/fake/tailscale.exe", OTHER_APP_SERVE)
        d = cp.gather()
        blob = json.dumps(d)
        check("serve_command no reset/funnel",
              "reset" not in d["serve_command"] and "funnel" not in d["serve_command"])
        check("teardown no reset", "reset" not in d["teardown_command"])
        check("a warning mentions not running `serve reset`",
              any("reset" in w.lower() for w in d["warnings"]))
        check("warnings mention Funnel/public",
              any("funnel" in w.lower() or "public" in w.lower() for w in d["warnings"]))

        print("\n[8] gather() issues ONLY read-only tailscale subcommands")
        runner = with_env("C:/fake/tailscale.exe", OTHER_APP_SERVE)
        cp.gather()
        mutating = []
        for argv in runner.calls:
            sub = " ".join(argv[1:])
            if any(bad in sub for bad in ("--bg", " off", "reset", "funnel", "login", "set ")):
                mutating.append(sub)
            allowed = (argv[1:][:1] == ["status"] or argv[1:][:2] == ["serve", "status"])
            if not allowed:
                mutating.append(sub)
        check("no mutating tailscale calls were made", mutating == [], )
        if mutating:
            print("     offending calls:", mutating)

        print("\n[9] No token in human or JSON output (even if a token is set)")
        _tok = config.ADAM_TOKEN
        try:
            config.ADAM_TOKEN = "SENTINEL_TOKEN_DO_NOT_LEAK_abc123"
            with_env("C:/fake/tailscale.exe", OTHER_APP_SERVE)
            d = cp.gather()
            human = io.StringIO()
            with contextlib.redirect_stdout(human):
                cp._print_human(d)
            check("token sentinel not in human output", "SENTINEL_TOKEN_DO_NOT_LEAK_abc123" not in human.getvalue())
            check("token sentinel not in JSON output", "SENTINEL_TOKEN_DO_NOT_LEAK_abc123" not in json.dumps(d))
            check("no 'ADAM_TOKEN' key/value in diagnostic", "ADAM_TOKEN" not in json.dumps(d))
        finally:
            config.ADAM_TOKEN = _tok

        print("\n[10] Subprocess failure is handled safely (no crash)")
        cp.onboarding._resolve_tailscale = lambda: "C:/fake/tailscale.exe"

        def _boom(*a, **k):
            raise RuntimeError("tailscale exploded")
        cp.subprocess.run = _boom
        d = cp.gather()
        check("gather survives subprocess failure", isinstance(d, dict) and "recommended_https_port" in d)
        check("failure -> serve treated as absent (recommend 443)", d["recommended_https_port"] == 443)

        # --- opt-in --apply / --off (the mutation path) -------------------------
        def make_d(**over):
            d = {
                "tailscale_found": True, "tailscale_logged_in": True,
                "tailscale_backend_state": "Running", "served_https_ports": [443],
                "serve_proxy_targets": {443: 9100}, "recommended_https_port": 8443,
                "target_port": 8010, "phone_url": "https://host.ts.net:8443/",
                "serve_command": "tailscale serve --bg --https=8443 http://127.0.0.1:8010",
                "teardown_command": "tailscale serve --https=8443 off",
            }
            d.update(over)
            return d

        def rec_runner(returncode=0, stderr=""):
            calls = []

            def run(argv, capture_output=True, text=True, timeout=None):
                calls.append(list(argv))
                return types.SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
            run.calls = calls
            return run

        cp.onboarding._resolve_tailscale = lambda: "C:/fake/tailscale.exe"

        print("\n[11] apply_serve / turn_off_serve issue ONLY port-scoped serve on/off")
        rr = rec_runner(); cp.subprocess.run = rr
        res = cp.apply_serve("C:/fake/tailscale.exe", 8443, 8010)
        argv = rr.calls[-1]
        check("apply argv is `serve --bg`", argv[1:3] == ["serve", "--bg"])
        check("apply targets local port 8010", "http://127.0.0.1:8010" in argv)
        check("apply sets --https=8443", "--https=8443" in argv)
        check("apply never reset/funnel/login",
              not any(x in " ".join(argv) for x in ("reset", "funnel", "login")))
        check("apply ok on returncode 0", res["ok"] is True)
        rr = rec_runner(); cp.subprocess.run = rr
        cp.turn_off_serve("C:/fake/tailscale.exe", 8443)
        offargv = rr.calls[-1]
        check("off argv is `serve --https=8443 off`", offargv[1:] == ["serve", "--https=8443", "off"])
        check("off never reset", "reset" not in " ".join(offargv))

        print("\n[12] --apply guards: refuse when not signed in or the port is occupied")
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_apply(make_d(tailscale_logged_in=False, tailscale_backend_state="NeedsLogin"), assume_yes=True)
        check("not-signed-in -> exit 1", rc == 1)
        check("not-signed-in -> ran nothing", rr.calls == [])
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_apply(make_d(served_https_ports=[443, 8443]), assume_yes=True)
        check("recommended port already served -> exit 1", rc == 1)
        check("occupied -> ran nothing", rr.calls == [])
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_apply(make_d(recommended_https_port=443, served_https_ports=[443]), assume_yes=True)
        check("would-clobber :443 (another app) -> refused", rc == 1 and rr.calls == [])

        print("\n[13] --apply happy path runs exactly one `serve --bg`")
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_apply(make_d(), assume_yes=True)   # signed in, 8443 free (another app on 443)
        check("apply happy -> exit 0", rc == 0)
        serve_calls = [c for c in rr.calls if c[1:2] == ["serve"]]
        check("apply happy -> exactly one serve call", len(serve_calls) == 1)
        check("apply happy -> it's the --bg serve on 8443", serve_calls and "--bg" in serve_calls[0]
              and "--https=8443" in serve_calls[0])

        print("\n[14] --apply without --yes in a non-interactive shell changes nothing")
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_apply(make_d(), assume_yes=False)   # stdin isn't a tty here -> confirm=False
        check("no --yes, non-tty -> exit 0 (cancelled)", rc == 0)
        check("no --yes -> ran no serve call", [c for c in rr.calls if c[1:2] == ["serve"]] == [])

        print("\n[15] --off is port-scoped and refuses a foreign serve")
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_off(make_d(), 443, assume_yes=True)
        check("off :443 (foreign target) -> refused (exit 1)", rc == 1)
        check("off refused -> ran nothing", rr.calls == [])
        rr = rec_runner(); cp.subprocess.run = rr
        rc = cp._do_off(make_d(), 8443, assume_yes=True)
        check("off :8443 -> exit 0", rc == 0)
        offc = [c for c in rr.calls if c[1:2] == ["serve"]]
        check("off -> exactly one `serve … off` call", len(offc) == 1 and offc[0][-1] == "off")
        check("off never reset", not any("reset" in " ".join(c) for c in rr.calls))
    finally:
        cp.onboarding._resolve_tailscale = _saved_resolve
        cp.subprocess.run = _saved_run

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
