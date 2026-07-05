"""
Adam — guided first-run setup.

Run from the project root (Python avoids the PowerShell execution-policy wall):

    python scripts/setup.py

What it does (idempotent, non-destructive):
  1. Ensures .env exists and has a real ADAM_TOKEN (generates one only if
     missing — never overwrites an existing token).
  2. Confirms Claude Code is resolvable; offers to record claude_exe if not.
  3. Prompts for your vault_path (the files Claude works against).
  4. Runs the setup doctor and prints a PASS/WARN/FAIL report.

It only ever edits ADAM_TOKEN (.env) and, with your consent, claude_exe and
vault_path (settings.json). It never touches agent_safety.mode, the permissions
block, or approval/backup/audit logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Import the repo-root modules regardless of where this is launched from.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import onboarding  # noqa: E402


def _interactive() -> bool:
    return sys.stdin.isatty()


def _ask(prompt: str, default: str = "") -> str:
    if not _interactive():
        return default
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def _yes(prompt: str) -> bool:
    if not _interactive():
        return False
    return _ask(prompt + " [y/N]: ").lower() in ("y", "yes")


def main() -> int:
    env = ROOT / ".env"
    env_example = ROOT / ".env.example"
    settings = ROOT / "settings.json"
    settings_example = ROOT / "settings.example.json"

    print("\nAdam — setup\n" + "=" * 32)

    # 1. Token / .env (never prints the token value).
    res = onboarding.ensure_env_token(env, env_example)
    msg = {
        "unchanged": "Existing ADAM_TOKEN kept (not changed).",
        "token_generated": "Generated a new ADAM_TOKEN and wrote it to .env.",
        "created_with_token": "Created .env with a fresh ADAM_TOKEN.",
    }[res["action"]]
    print("\n[1] Secrets / .env")
    print("    " + msg)
    if res["backup"]:
        print(f"    Backed up previous .env -> {Path(res['backup']).name}")

    # Guard: a malformed settings.json must not crash setup with a traceback, and
    # setup must NOT try to auto-"fix" it. Report it plainly, run the doctor
    # (which degrades cleanly), and exit nonzero so the user knows to fix it.
    active_settings = settings if settings.exists() else settings_example
    cfg_err = onboarding.validate_settings_file(active_settings)
    if cfg_err:
        print("\n[!] Configuration problem")
        print("    " + cfg_err)
        print("    Leaving settings.json as-is — setup will not auto-edit it.")
        print("\n[4] Setup doctor\n" + "-" * 32)
        for c in onboarding.run_doctor():
            print(f"    {c['status']:4}  {c['name']}: {c['detail']}")
        print("-" * 32)
        print("Fix the configuration above, then re-run: python scripts/setup.py")
        return 1

    # 2. Claude detection (Windows-aware).
    print("\n[2] Claude Code")
    try:
        import config  # imported after .env exists
    except Exception as e:  # belt-and-suspenders: never dump a raw traceback
        print("    Could not load configuration:")
        print("    " + str(e))
        return 1
    claude_path, how = onboarding._resolve_claude(config)
    if claude_path and how != "configured but not found":
        print(f"    Found Claude: {claude_path}  ({how})")
    else:
        print("    Claude Code was not found on PATH.")
        path = _ask("    Enter the full path to claude (or blank to skip): ")
        if path and Path(path).exists():
            onboarding.ensure_settings_file(settings, settings_example)
            onboarding.set_settings_value(settings, "claude_exe", path)
            print("    Recorded claude_exe in settings.json.")
        else:
            print("    Skipped — install Claude Code, or set claude_exe later.")

    # 3. Vault path.
    print("\n[3] Your files (vault_path)")
    current = str(config.VAULT_PATH)
    print(f"    Current vault_path resolves to: {current}")
    if _yes("    Set vault_path now?"):
        vp = _ask("    Enter the folder Claude should work against: ")
        if vp and Path(vp).exists():
            onboarding.ensure_settings_file(settings, settings_example)
            onboarding.set_settings_value(settings, "vault_path", vp)
            print("    Recorded vault_path in settings.json.")
        elif vp:
            print(f"    That path does not exist: {vp} — not recorded.")

    # 4. Doctor.
    print("\n[4] Setup doctor\n" + "-" * 32)
    checks = onboarding.run_doctor()
    for c in checks:
        print(f"    {c['status']:4}  {c['name']}: {c['detail']}")
    print("-" * 32)
    if onboarding.doctor_has_failure(checks):
        print("Setup finished with one or more FAIL checks above. Fix those, then "
              "re-run: python scripts/doctor.py")
        return 1
    print("Setup complete. Start the server, then add the PWA on your phone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
