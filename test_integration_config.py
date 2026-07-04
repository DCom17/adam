"""
Jarvis Voice Local — integration_config writer tests.

Proves the comma/bracket-proof config writer that backs the wizards' one-click
"Enable" buttons:

  * set_settings_integration merges into existing settings.json, preserves every
    other key, and always writes JSON that re-parses;
  * it seeds from settings.example.json when settings.json doesn't exist yet;
  * it refuses to clobber a settings.json it can't parse (raises, no write);
  * set_env_var replaces an existing (or commented) KEY= line in place and leaves
    every other line byte-intact, appends with a section header when absent, and
    preserves trailing-newline behavior;
  * both writers back up the prior file before overwriting;
  * a token written to .env never appears in settings.json.

Run:  python test_integration_config.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import integration_config as ic

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
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_intcfg_test_"))
    backups = sandbox / "backups"

    print("\n[1] set_settings_integration merges + preserves, writes valid JSON")
    sp = sandbox / "settings.json"
    sp.write_text(json.dumps({
        "port": 8010,
        "vault_path": "C:/x",
        "integrations": {"hunter": {"enabled": False}},
    }, indent=2), "utf-8")
    ic.set_settings_integration(
        "google_calendar",
        {"enabled": True, "bridge_url": "https://script.google.com/macros/s/X/exec",
         "calendar_id": "primary", "timeout_seconds": 20},
        settings_path=sp, backup_dir=backups,
    )
    data = json.loads(sp.read_text("utf-8"))  # must re-parse
    check("re-parses as valid JSON", isinstance(data, dict))
    check("unrelated top-level keys preserved", data["port"] == 8010 and data["vault_path"] == "C:/x")
    check("sibling integration preserved", data["integrations"]["hunter"] == {"enabled": False})
    check("calendar block written + enabled", data["integrations"]["google_calendar"]["enabled"] is True)
    check("bridge_url written",
          data["integrations"]["google_calendar"]["bridge_url"].endswith("/exec"))
    check("a backup of the prior settings.json was made",
          any(p.name.startswith("settings.json.") for p in backups.glob("*.bak")))

    print("\n[2] seeds from settings.example.json when settings.json is absent")
    sp2 = sandbox / "no_settings.json"
    ex = sandbox / "settings.example.json"
    ex.write_text(json.dumps({
        "port": 8010,
        "integrations": {"google_calendar": {"enabled": False, "bridge_url": ""},
                         "gmail": {"enabled": False}},
    }, indent=2), "utf-8")
    ic.set_settings_integration(
        "google_calendar", {"enabled": True, "bridge_url": "https://e/exec",
                            "calendar_id": "primary", "timeout_seconds": 20},
        settings_path=sp2, example_path=ex, backup_dir=backups,
    )
    d2 = json.loads(sp2.read_text("utf-8"))
    check("seeded file carries the example's other integrations", "gmail" in d2["integrations"])
    check("calendar enabled in seeded file", d2["integrations"]["google_calendar"]["enabled"] is True)

    print("\n[3] refuses to clobber an unparseable settings.json")
    bad = sandbox / "bad_settings.json"
    bad.write_text('{ "integrations": { , broken }', "utf-8")
    raised = False
    try:
        ic.set_settings_integration("google_calendar", {"enabled": True},
                                    settings_path=bad, backup_dir=backups)
    except json.JSONDecodeError:
        raised = True
    check("raises on malformed settings.json", raised)
    check("malformed file left untouched (not overwritten)",
          bad.read_text("utf-8") == '{ "integrations": { , broken }')

    print("\n[4] set_env_var replaces an existing KEY= line in place")
    ep = sandbox / ".env"
    ep.write_text(
        "JARVIS_TOKEN=keepme\n"
        "# --- Google Calendar bridge ---\n"
        "GOOGLE_CALENDAR_TOKEN=\n"
        "HUNTER_TOKEN=keep-too\n",
        "utf-8",
    )
    ic.set_env_var("GOOGLE_CALENDAR_TOKEN", "abc123def", env_path=ep, backup_dir=backups)
    txt = ep.read_text("utf-8")
    check("token value written", "GOOGLE_CALENDAR_TOKEN=abc123def" in txt)
    check("other secrets untouched", "JARVIS_TOKEN=keepme" in txt and "HUNTER_TOKEN=keep-too" in txt)
    check("no duplicate key line",
          txt.count("GOOGLE_CALENDAR_TOKEN=") == 1)
    check("trailing newline preserved", txt.endswith("\n"))
    check("env backup was made", any(p.name.startswith(".env.") for p in backups.glob("*.bak")))

    print("\n[5] set_env_var replaces a commented #KEY= placeholder")
    ep2 = sandbox / ".env2"
    ep2.write_text("JARVIS_TOKEN=x\n#GOOGLE_CALENDAR_TOKEN=placeholder\n", "utf-8")
    ic.set_env_var("GOOGLE_CALENDAR_TOKEN", "tok", env_path=ep2, backup_dir=backups)
    t2 = ep2.read_text("utf-8")
    check("commented placeholder replaced (uncommented)", "GOOGLE_CALENDAR_TOKEN=tok" in t2)
    check("no leftover commented placeholder", "#GOOGLE_CALENDAR_TOKEN=" not in t2)

    print("\n[6] set_env_var appends with a section header when the key is absent")
    ep3 = sandbox / ".env3"
    ep3.write_text("JARVIS_TOKEN=x\n", "utf-8")
    ic.set_env_var("GOOGLE_CALENDAR_TOKEN", "newtok", env_path=ep3,
                   backup_dir=backups, section_header="# --- Google Calendar bridge ---")
    t3 = ep3.read_text("utf-8")
    check("key appended", "GOOGLE_CALENDAR_TOKEN=newtok" in t3)
    check("section header added once", t3.count("# --- Google Calendar bridge ---") == 1)
    check("original key preserved", "JARVIS_TOKEN=x" in t3)

    print("\n[7] token written to .env never lands in settings.json")
    check("settings.json carries no token", "abc123def" not in sp.read_text("utf-8"))

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
