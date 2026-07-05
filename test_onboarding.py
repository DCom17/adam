"""
Adam — onboarding / setup-doctor tests (v0.7.0, Slice 1).

Proves the safety-critical setup behavior:
  * .env token bootstrap is idempotent and never overwrites a real token;
  * unrelated lines/comments are preserved; a backup is made before edits;
  * the doctor returns the expected checks and never leaks a secret value;
  * set_settings_value refuses to edit anything but the whitelisted keys.

Run:  python test_onboarding.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import onboarding

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
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_onb_test_"))
    example = sandbox / ".env.example"
    example.write_text(
        "# comment line\n"
        "ADAM_TOKEN=" + onboarding.TOKEN_PLACEHOLDER + "\n"
        "VAPID_PUBLIC_KEY=\n"
        "# trailing note\n",
        encoding="utf-8",
    )

    print("\n[1] Fresh .env created with a generated token")
    env = sandbox / ".env"
    res = onboarding.ensure_env_token(env, example)
    check("action is created_with_token", res["action"] == "created_with_token")
    check("env file now exists", env.exists())
    val = onboarding._token_value_from_lines(env.read_text("utf-8").splitlines())
    check("token is real (not placeholder)", onboarding._is_real_token(val))
    check("token is 64 hex chars", isinstance(val, str) and len(val) == 64)
    check("unrelated lines preserved", "VAPID_PUBLIC_KEY=" in env.read_text("utf-8")
          and "# trailing note" in env.read_text("utf-8"))
    check("return dict leaks no token value", val not in str(res))

    print("\n[2] Re-run is idempotent — real token NOT overwritten")
    first = onboarding._token_value_from_lines(env.read_text("utf-8").splitlines())
    res2 = onboarding.ensure_env_token(env, example)
    second = onboarding._token_value_from_lines(env.read_text("utf-8").splitlines())
    check("action is unchanged", res2["action"] == "unchanged")
    check("token is identical across runs", first == second)
    check("no backup made when nothing changed", res2["backup"] is None)

    print("\n[3] Placeholder / empty token gets replaced + backup made")
    env3 = sandbox / "envdir3" / ".env"
    env3.parent.mkdir(parents=True)
    env3.write_text(
        "ADAM_TOKEN=" + onboarding.TOKEN_PLACEHOLDER + "\n"
        "OWNER_PHONE=+15555550123\n",
        encoding="utf-8",
    )
    res3 = onboarding.ensure_env_token(env3, example)
    check("placeholder replaced (token_generated)", res3["action"] == "token_generated")
    check("backup file was written", res3["backup"] is not None and Path(res3["backup"]).exists())
    check("backup matches '.env.bak.*' naming", ".env.bak." in Path(res3["backup"]).name)
    new_val = onboarding._token_value_from_lines(env3.read_text("utf-8").splitlines())
    check("new token is real", onboarding._is_real_token(new_val))
    check("other line preserved", "OWNER_PHONE=+15555550123" in env3.read_text("utf-8"))

    print("\n[4] Empty token value is treated as missing")
    env4 = sandbox / "envdir4" / ".env"
    env4.parent.mkdir(parents=True)
    env4.write_text("ADAM_TOKEN=\n", encoding="utf-8")
    res4 = onboarding.ensure_env_token(env4, example)
    check("empty token replaced", res4["action"] == "token_generated")
    check("token now real",
          onboarding._is_real_token(
              onboarding._token_value_from_lines(env4.read_text("utf-8").splitlines())))

    print("\n[5] set_settings_value only edits whitelisted keys")
    settings = sandbox / "settings.json"
    settings.write_text('{"agent_safety": {"mode": "draft_only"}}\n', encoding="utf-8")
    onboarding.set_settings_value(settings, "vault_path", str(sandbox))
    import json
    data = json.loads(settings.read_text("utf-8"))
    check("vault_path written", data.get("vault_path") == str(sandbox))
    check("agent_safety preserved untouched", data.get("agent_safety") == {"mode": "draft_only"})
    refused = False
    try:
        onboarding.set_settings_value(settings, "agent_safety", "legacy_direct")
    except ValueError:
        refused = True
    check("editing agent_safety is refused", refused)
    data2 = json.loads(settings.read_text("utf-8"))
    check("agent_safety still draft_only after refused edit",
          data2.get("agent_safety") == {"mode": "draft_only"})

    print("\n[6] Secret-leak guard helper")
    check("clean summary -> no secret detected",
          onboarding._summary_contains_secret({"a": "ok", "b": 1}, ["TOPSECRET"]) is False)
    check("summary containing the secret -> detected",
          onboarding._summary_contains_secret({"x": "has TOPSECRET inside"}, ["TOPSECRET"]) is True)
    check("empty secret values are ignored",
          onboarding._summary_contains_secret({"x": "anything"}, [""]) is False)

    print("\n[7] run_doctor returns the expected checks; no secret leak")
    checks = onboarding.run_doctor(reload_config=False)
    names = {c["name"] for c in checks}
    expected = {
        "ADAM_TOKEN present", "Claude executable resolved", "settings.json",
        "agent_safety.mode", "vault_path", "Write directories",
        "Protected file patterns", "data/state path", "Server reachable",
        "Python dependencies", "No secret leakage in /health",
    }
    check("all expected checks present", expected.issubset(names))
    check("every check has a valid status",
          all(c["status"] in ("PASS", "WARN", "FAIL") for c in checks))
    leak = next(c for c in checks if c["name"] == "No secret leakage in /health")
    check("live /health summary does not leak secrets", leak["status"] == "PASS")

    print("\n[8] run_doctor scenarios (monkeypatched config, no reload)")
    _saved_token = config.ADAM_TOKEN
    _saved_mode = config.AGENT_MODE
    try:
        config.ADAM_TOKEN = ""
        c = onboarding.run_doctor(reload_config=False)
        tok = next(x for x in c if x["name"] == "ADAM_TOKEN present")
        check("missing token -> FAIL", tok["status"] == "FAIL")

        config.ADAM_TOKEN = "x" * 64
        config.AGENT_MODE = "legacy_direct"
        c = onboarding.run_doctor(reload_config=False)
        mode = next(x for x in c if x["name"] == "agent_safety.mode")
        check("legacy_direct -> FAIL", mode["status"] == "FAIL")

        config.AGENT_MODE = "draft_only"
        c = onboarding.run_doctor(reload_config=False)
        mode = next(x for x in c if x["name"] == "agent_safety.mode")
        check("draft_only -> PASS", mode["status"] == "PASS")
    finally:
        config.ADAM_TOKEN = _saved_token
        config.AGENT_MODE = _saved_mode

    print("\n[9] Config-file validation is BOM-tolerant + friendly on malformed")
    import json as _json
    good = sandbox / "good.json"
    good.write_text('{"vault_path": "x", "port": 8000}\n', encoding="utf-8")
    check("clean JSON -> None (no error)", onboarding.validate_settings_file(good) is None)

    bom = sandbox / "bom.json"
    # utf-8-sig prepends a BOM; the file is otherwise identical valid JSON.
    bom.write_text('{"vault_path": "x"}\n', encoding="utf-8-sig")
    check("BOM-prefixed JSON -> None (tolerated)", onboarding.validate_settings_file(bom) is None)
    check("BOM file really starts with a BOM",
          bom.read_bytes().startswith(b"\xef\xbb\xbf"))

    bad = sandbox / "bad.json"
    bad.write_text('{"vault_path": "x",,}\n', encoding="utf-8")  # stray comma = invalid
    msg = onboarding.validate_settings_file(bad)
    check("malformed JSON -> message (not None)", isinstance(msg, str) and bool(msg))
    check("malformed message names the file", msg is not None and "bad.json" in msg)
    check("malformed message points at the fix",
          msg is not None and "settings.example.json" in msg)

    missing = sandbox / "nope.json"
    check("absent file -> None (not an error)", onboarding.validate_settings_file(missing) is None)

    print("\n[10] run_doctor degrades cleanly when config can't load (no crash, no leak)")
    _saved_reload = onboarding._reload_config
    _saved_tok2 = config.ADAM_TOKEN
    try:
        config.ADAM_TOKEN = "LEAKSENTINEL_TOKEN_VALUE"
        onboarding._reload_config = lambda: (
            None, "settings.json is not valid JSON (line 1, column 5): "
                  "Expecting value. Fix the JSON syntax, or restore it from settings.example.json.")
        dchecks = onboarding.run_doctor(reload_config=True)
        check("doctor returns checks instead of crashing",
              isinstance(dchecks, list) and len(dchecks) >= 1)
        cfgc = next((c for c in dchecks if c["name"] == "Configuration file"), None)
        check("config-load failure -> Configuration FAIL",
              cfgc is not None and cfgc["status"] == "FAIL")
        check("every degraded check has a valid status",
              all(c["status"] in ("PASS", "WARN", "FAIL") for c in dchecks))
        check("degraded doctor leaks no secret value",
              "LEAKSENTINEL_TOKEN_VALUE" not in _json.dumps(dchecks))
    finally:
        onboarding._reload_config = _saved_reload
        config.ADAM_TOKEN = _saved_tok2

    print("\n[11] Mobile-access diagnostics (v0.9, intent-gated, advisory, never FAIL)")
    import types as _types

    def fakecfg(**kw):
        d = {"PUBLIC_BASE_URL": "", "CORS_ALLOWED_ORIGINS": ["*"]}
        d.update(kw)
        return _types.SimpleNamespace(**d)

    # remote-intent helper
    check("no intent for desktop-local", onboarding._has_remote_intent(fakecfg()) is False)
    check("intent when public_base_url set",
          onboarding._has_remote_intent(fakecfg(PUBLIC_BASE_URL="https://x.ts.net")) is True)
    check("intent for a non-loopback CORS origin",
          onboarding._has_remote_intent(fakecfg(CORS_ALLOWED_ORIGINS=["https://x.example"])) is True)
    check("no intent for a loopback CORS origin",
          onboarding._has_remote_intent(fakecfg(CORS_ALLOWED_ORIGINS=["http://localhost:8010"])) is False)

    # Mobile access (HTTPS)
    check("desktop-local (no base url) -> PASS",
          onboarding._mobile_access_check(fakecfg())["status"] == "PASS")
    check("https base url -> PASS",
          onboarding._mobile_access_check(fakecfg(PUBLIC_BASE_URL="https://x.ts.net"))["status"] == "PASS")
    check("http base url -> WARN",
          onboarding._mobile_access_check(fakecfg(PUBLIC_BASE_URL="http://x.example"))["status"] == "WARN")

    # Tailscale detection (control which() + the standard-path fallback + subprocess.run).
    # NOTE: this machine may actually have Tailscale at the standard path, so the
    # fallback list is overridden for deterministic absent/present scenarios.
    _saved_which = onboarding.shutil.which
    _saved_run = onboarding.subprocess.run
    _saved_fb = onboarding._TAILSCALE_FALLBACK_PATHS
    _cap = {}

    def _run_ok(argv, *a, **k):
        _cap["argv"] = argv
        return _types.SimpleNamespace(returncode=0)

    try:
        # --- absent everywhere: not on PATH, no usable fallback ---
        onboarding.shutil.which = lambda name: None
        onboarding._TAILSCALE_FALLBACK_PATHS = ["C:/does/not/exist/tailscale.exe"]
        check("resolver returns None when absent", onboarding._resolve_tailscale() is None)
        check("tailscale absent + no intent -> PASS",
              onboarding._tailscale_check(fakecfg())["status"] == "PASS")
        check("tailscale absent + no intent -> not WARN",
              onboarding._tailscale_check(fakecfg())["status"] != "WARN")
        check("tailscale absent + intent -> WARN",
              onboarding._tailscale_check(fakecfg(PUBLIC_BASE_URL="https://x"))["status"] == "WARN")

        # --- found on PATH: resolver prefers it; status check uses that exe ---
        onboarding.shutil.which = lambda name: "C:/fake/onpath/tailscale.exe"
        check("resolver prefers PATH", onboarding._resolve_tailscale() == "C:/fake/onpath/tailscale.exe")
        _cap.clear(); onboarding.subprocess.run = _run_ok
        check("PATH tailscale + status ok -> PASS",
              onboarding._tailscale_check(fakecfg())["status"] == "PASS")
        check("status check used the PATH exe",
              _cap.get("argv", [None])[0] == "C:/fake/onpath/tailscale.exe")

        # --- absent on PATH but present at the standard Windows path (the bug fix) ---
        onboarding.shutil.which = lambda name: None
        onboarding._TAILSCALE_FALLBACK_PATHS = [sys.executable]  # a real existing file
        check("resolver falls back to the standard install path",
              onboarding._resolve_tailscale() == sys.executable)
        _cap.clear(); onboarding.subprocess.run = _run_ok
        check("standard-path tailscale detected + status ok -> PASS",
              onboarding._tailscale_check(fakecfg())["status"] == "PASS")
        check("status check used the standard-path exe (not PATH)",
              _cap.get("argv", [None])[0] == sys.executable)

        # --- status nonzero / timeout: WARN at most, never raise, never FAIL ---
        onboarding.subprocess.run = lambda *a, **k: _types.SimpleNamespace(returncode=1)
        check("present + nonzero status -> WARN",
              onboarding._tailscale_check(fakecfg())["status"] == "WARN")

        def _boom(*a, **k):
            raise RuntimeError("timeout")
        onboarding.subprocess.run = _boom
        check("status failure -> WARN (never raises)",
              onboarding._tailscale_check(fakecfg())["status"] == "WARN")
        check("status failure never FAIL",
              onboarding._tailscale_check(fakecfg())["status"] != "FAIL")
    finally:
        onboarding.shutil.which = _saved_which
        onboarding.subprocess.run = _saved_run
        onboarding._TAILSCALE_FALLBACK_PATHS = _saved_fb

    # CORS exposure
    check("CORS '*' + intent -> WARN",
          onboarding._cors_exposure_check(fakecfg(PUBLIC_BASE_URL="https://x"))["status"] == "WARN")
    check("CORS '*' + no intent -> PASS",
          onboarding._cors_exposure_check(fakecfg())["status"] == "PASS")
    check("CORS restricted -> PASS",
          onboarding._cors_exposure_check(fakecfg(CORS_ALLOWED_ORIGINS=["https://x.example"]))["status"] == "PASS")

    # None of the three access checks ever returns FAIL across a sweep.
    _w = onboarding.shutil.which
    _fb = onboarding._TAILSCALE_FALLBACK_PATHS
    onboarding.shutil.which = lambda name: None
    onboarding._TAILSCALE_FALLBACK_PATHS = ["C:/does/not/exist/tailscale.exe"]  # deterministic
    try:
        sweep = []
        for fc in (fakecfg(), fakecfg(PUBLIC_BASE_URL="http://x"),
                   fakecfg(PUBLIC_BASE_URL="https://x"),
                   fakecfg(CORS_ALLOWED_ORIGINS=["https://x.example"])):
            sweep += [onboarding._mobile_access_check(fc),
                      onboarding._tailscale_check(fc),
                      onboarding._cors_exposure_check(fc)]
    finally:
        onboarding.shutil.which = _w
        onboarding._TAILSCALE_FALLBACK_PATHS = _fb
    check("no access check ever returns FAIL", all(c["status"] != "FAIL" for c in sweep))
    check("desktop-local sweep entry is all PASS (0 WARN)",
          all(onboarding._mobile_access_check(fakecfg())["status"] == "PASS"
              for _ in range(1)))

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
