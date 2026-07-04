"""
Jarvis Voice Local — capability tier tests (Safe / Powerful / Unrestricted).

Proves the front-door capability dial:
  * each tier maps to the right agent mode + permission posture;
  * the always-on rails (backup, audit, secret-file protection) hold at EVERY
    tier and cannot be turned off — including Unrestricted;
  * NO tier ever uses legacy_direct (the rail-bypassing mode);
  * the permission-aware auto-apply decision (requires_approval) matches each
    tier's intent: Safe holds everything, Powerful auto-applies non-destructive
    but holds destructive, Unrestricted auto-applies everything;
  * the settings writer persists capability_tier and refuses other top-level keys.

Run:  python test_capability_tier.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config            # noqa: E402
import permissions       # noqa: E402
import integration_config  # noqa: E402

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


def _apply(tier: str) -> None:
    """Switch config to a tier the way the live endpoint does."""
    config.CAPABILITY_TIER = tier
    config._apply_capability_tier()


def main() -> int:
    print("\n[1] Tier -> agent mode + posture mapping")
    _apply("safe")
    check("safe -> draft_only", config.AGENT_MODE == "draft_only")
    check("safe requires approval for writes", config.PERM_REQUIRE_APPROVAL_WRITES is True)
    check("safe requires approval for destructive", config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE is True)
    check("safe shell off", config.PERM_ALLOW_SHELL is False)
    check("safe self-edit off", config.PERM_ALLOW_APP_SELF_EDIT is False)

    _apply("powerful")
    check("powerful -> controlled_write", config.AGENT_MODE == "controlled_write")
    check("powerful auto-approves writes", config.PERM_REQUIRE_APPROVAL_WRITES is False)
    check("powerful STILL confirms destructive", config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE is True)
    check("powerful shell off", config.PERM_ALLOW_SHELL is False)
    check("powerful self-edit off", config.PERM_ALLOW_APP_SELF_EDIT is False)
    check("powerful can write the vault", any("JARVIS" in d or config.VAULT_PATH == d for d in config.PERM_WRITE_DIRS))

    _apply("unrestricted")
    check("unrestricted -> controlled_write (NOT legacy_direct)", config.AGENT_MODE == "controlled_write")
    check("unrestricted auto-approves writes", config.PERM_REQUIRE_APPROVAL_WRITES is False)
    check("unrestricted auto-approves destructive", config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE is False)
    check("unrestricted shell on", config.PERM_ALLOW_SHELL is True)
    check("unrestricted shell = any command (empty allow-list)", config.PERM_ALLOWED_COMMANDS == [])
    check("unrestricted self-edit on", config.PERM_ALLOW_APP_SELF_EDIT is True)

    print("\n[2] No tier ever uses the rail-bypassing legacy_direct")
    for t in ("safe", "powerful", "unrestricted"):
        _apply(t)
        check(f"{t}: mode != legacy_direct", config.AGENT_MODE != "legacy_direct")
        check(f"{t}: tools restricted (server is sole writer)", config.AGENT_RESTRICT_TOOLS is True)
        check(f"{t}: no direct Claude writes", config.AGENT_DIRECT_WRITES is False)

    print("\n[3] Always-on rails hold at EVERY tier (incl. Unrestricted)")
    for t in ("safe", "powerful", "unrestricted"):
        _apply(t)
        check(f"{t}: backup-before-write ON", config.PERM_BACKUP_BEFORE_WRITE is True)
        check(f"{t}: audit log ON", config.PERM_AUDIT_LOG_ENABLED is True)
        check(f"{t}: .env protected", ".env" in config.PERM_PROTECTED_FILE_PATTERNS)
        check(f"{t}: settings.json protected", "settings.json" in config.PERM_PROTECTED_FILE_PATTERNS)

    print("\n[4] Secret guard refuses secrets even in Unrestricted")
    _apply("unrestricted")
    check("is_protected_file('.env')", permissions.is_protected_file(".env"))
    check("is_protected_file('settings.json')", permissions.is_protected_file("settings.json"))
    check("is_protected_file('id_rsa')", permissions.is_protected_file("id_rsa"))
    check("is_protected_file('x.key')", permissions.is_protected_file("x.key"))
    check("a plain note is NOT protected", not permissions.is_protected_file("note.md"))

    print("\n[5] Auto-apply decision matches each tier's intent")
    # Safe: hold both.
    _apply("safe")
    check("safe holds a non-destructive write",
          permissions.requires_approval("x.md", is_write=True, destructive=False) is True)
    check("safe holds a destructive write",
          permissions.requires_approval("x.md", is_write=True, destructive=True) is True)
    # Powerful: auto non-destructive, hold destructive.
    _apply("powerful")
    check("powerful auto-applies non-destructive",
          permissions.requires_approval("x.md", is_write=True, destructive=False) is False)
    check("powerful holds destructive",
          permissions.requires_approval("x.md", is_write=True, destructive=True) is True)
    # Unrestricted: auto everything.
    _apply("unrestricted")
    check("unrestricted auto-applies non-destructive",
          permissions.requires_approval("x.md", is_write=True, destructive=False) is False)
    check("unrestricted auto-applies destructive",
          permissions.requires_approval("x.md", is_write=True, destructive=True) is False)

    print("\n[6] Settings writer persists the tier and refuses other keys")
    with tempfile.TemporaryDirectory(prefix="jvl_tier_") as td:
        sp = Path(td) / "settings.json"
        sp.write_text(json.dumps({"port": 8010, "vault_path": "C:/x"}), encoding="utf-8")
        ep = Path(td) / "settings.example.json"
        ep.write_text("{}", encoding="utf-8")
        out = integration_config.set_settings_top_level(
            "capability_tier", "powerful", settings_path=sp, example_path=ep, backup_dir=td)
        data = json.loads(Path(out).read_text("utf-8"))
        check("capability_tier written", data.get("capability_tier") == "powerful")
        check("other settings preserved", data.get("port") == 8010 and data.get("vault_path") == "C:/x")
        raised = False
        try:
            integration_config.set_settings_top_level("permissions", {}, settings_path=sp, example_path=ep, backup_dir=td)
        except ValueError:
            raised = True
        check("refuses a non-allow-listed top-level key", raised)

    print("\n[7] Unknown / blank tier falls back to custom (honors granular settings)")
    config.CAPABILITY_TIER = ""
    check("blank tier reads as custom in summary",
          config.agent_safety_summary().get("capability_tier") == "custom")

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
