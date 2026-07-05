"""
Adam — self-edit auto-rollback tests (Phase 2 self-edit hardening).

Proves the 4th always-on rail (self_edit_guard) end to end, entirely inside a temp
sandbox (no real app file is ever written, no live data touched):

    pre-flight   — a broken .py/.json self-edit is refused BEFORE the live file is
                   touched (nothing reaches disk).
    health probe — needs_health_probe() targets only .py; the real probe boots a
                   throwaway `import server` and reports the current tree healthy.
    auto-rollback — a self-edit that passes pre-flight but breaks the import is
                   reverted to its last-good bytes (or the created file removed),
                   left in a healthy state, and audited.
    happy path   — a good self-edit applies normally.

Health-probe SUBPROCESS boots are stubbed for the apply() integration cases (fast +
hermetic); one real probe smoke proves the probe itself works. The rollback logic,
statuses, byte-restore and audit trail are all exercised against real apply().

Run:  python test_self_edit_rollback.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import config
import permissions
import proposed_changes as pc
import self_edit_guard as seg

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


def _audit_types(audit_file: Path) -> list[str]:
    """All action_type values currently in the sandbox audit log."""
    out: list[str] = []
    if not audit_file.is_file():
        return out
    for line in audit_file.read_text("utf-8").splitlines():
        try:
            out.append(json.loads(line).get("action_type"))
        except Exception:
            pass
    return out


def _audit_records(audit_file: Path, action_type: str) -> list[dict]:
    recs: list[dict] = []
    if not audit_file.is_file():
        return recs
    for line in audit_file.read_text("utf-8").splitlines():
        try:
            r = json.loads(line)
            if r.get("action_type") == action_type:
                recs.append(r)
        except Exception:
            pass
    return recs


def _apply_selfedit(target: Path, content: str, action: str = "edit") -> tuple[dict, str | None]:
    """create -> approve -> apply a self-edit change, return (record, error)."""
    rec = pc.create(target_path=str(target), action=action, content=content,
                    summary=f"self-edit {target.name}")
    pc.approve(rec["id"])
    return pc.apply(rec["id"])


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_selfedit_test_"))
    appsrc = sandbox / "appsrc"          # stands in for the app's own source tree
    appdata = appsrc / "data"            # the excluded runtime data subtree
    drafts = sandbox / "drafts"
    outputs = sandbox / "outputs"
    backups = sandbox / "backups"
    state = sandbox / "state"
    logs = sandbox / "logs"
    for d in (appsrc, appdata, drafts, outputs, backups, state, logs):
        d.mkdir(parents=True, exist_ok=True)

    # --- policy: self-edit ON, pointed at the sandbox app tree (never the real one) ---
    config.PERM_READ_DIRS = [str(appsrc)]
    config.PERM_WRITE_DIRS = [str(drafts), str(outputs)]
    config.INFRA_WRITE_DIRS = [str(state), str(logs), str(backups)]
    config.PERM_BLOCKED_PATHS = []
    config.PERM_PROTECTED_FILE_PATTERNS = [".env", "*.pem", "settings.json"]
    config.PERM_MAX_FILE_SIZE_MB = 5
    config.PERM_REQUIRE_APPROVAL_WRITES = True
    config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE = True
    config.PERM_BACKUP_BEFORE_WRITE = True
    config.PERM_AUDIT_LOG_ENABLED = True
    config.PERM_SELF_EDIT_AUTO_ROLLBACK = True
    config.PERM_ALLOW_APP_SELF_EDIT = True
    config.PERM_APP_SELF_EDIT_DIRS = [str(appsrc)]
    config.PERM_APP_SELF_EDIT_EXCLUDE = [str(appdata)]
    config.PERM_APP_SELF_EDIT_EXTS = [".py", ".json", ".html", ".md", ".txt"]
    config.BACKUP_DIR = backups
    config.AUDIT_LOG_FILE = logs / "audit.jsonl"
    config.DRAFTS_DIR = drafts
    config.OUTPUTS_DIR = outputs
    config.PROPOSED_CHANGES_FILE = state / "proposed_changes.json"
    config.FORCE_OUTPUTS_TO_DRAFTS = False

    # ------------------------------------------------------------------ [1] pre-flight
    print("\n[1] Pre-flight validates content in isolation")
    ok, why = seg.preflight(appsrc / "m.py", "def f():\n    return 1\n")
    check("valid .py passes pre-flight", ok and why == "")
    ok, why = seg.preflight(appsrc / "m.py", "def f(:\n    return 1\n")
    check("syntax-broken .py fails pre-flight", (not ok) and "syntax" in why.lower())
    ok, why = seg.preflight(appsrc / "c.json", '{"a": 1}')
    check("valid .json passes pre-flight", ok and why == "")
    ok, why = seg.preflight(appsrc / "c.json", '{"a": 1,,}')
    check("broken .json fails pre-flight", (not ok) and "json" in why.lower())
    ok, why = seg.preflight(appsrc / "notes.md", "# not code, no parser")
    check("non-code type passes pre-flight untouched", ok and why == "")
    ok, why = seg.preflight(appsrc / "empty.py", "")
    check("empty .py is valid", ok and why == "")

    # ------------------------------------------------------------ [2] needs_health_probe
    print("\n[2] Only .py changes earn the boot probe (incl. rename — old path vanishes)")
    check(".py edit needs probe", seg.needs_health_probe(appsrc / "m.py", "edit"))
    check(".py create needs probe", seg.needs_health_probe(appsrc / "m.py", "create"))
    check(".py delete needs probe", seg.needs_health_probe(appsrc / "m.py", "delete"))
    check(".py rename needs probe", seg.needs_health_probe(appsrc / "m.py", "rename"))
    check(".json edit skips probe", not seg.needs_health_probe(appsrc / "c.json", "edit"))
    check(".html edit skips probe", not seg.needs_health_probe(appsrc / "x.html", "edit"))
    check(".txt rename skips probe", not seg.needs_health_probe(appsrc / "m.txt", "rename"))

    # ------------------------------------------------------------ [3] pre-flight in apply
    print("\n[3] apply() refuses a broken .py before touching disk")
    target = appsrc / "mod_a.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    original = target.read_text("utf-8")
    rec, err = _apply_selfedit(target, "VALUE = (1\n")  # syntax error
    check("broken self-edit is failed, not applied", rec.get("status") == "failed")
    check("apply reports a pre-flight reason", bool(err) and "pre-flight" in err)
    check("live file is unchanged by a refused pre-flight", target.read_text("utf-8") == original)

    # ------------------------------------------------------------------ [4] happy path
    print("\n[4] A good self-edit applies (probe healthy)")
    _orig_probe = seg.health_probe
    seg.health_probe = lambda *a, **k: (True, "")
    try:
        target = appsrc / "mod_b.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        rec, err = _apply_selfedit(target, "VALUE = 2\n")
        check("good self-edit is applied", rec.get("status") == "applied" and err is None)
        check("good self-edit is written to disk", target.read_text("utf-8") == "VALUE = 2\n")
    finally:
        seg.health_probe = _orig_probe

    # ------------------------------------------------------- [5] rollback of an edit
    print("\n[5] A self-edit that breaks the import is auto-rolled-back")
    _orig_probe = seg.health_probe
    _calls = {"n": 0}

    def _flaky_probe(*a, **k):
        # unhealthy the first time (the just-applied edit), healthy after rollback
        _calls["n"] += 1
        return (False, "ModuleNotFoundError: No module named 'gone'") if _calls["n"] == 1 else (True, "")

    seg.health_probe = _flaky_probe
    try:
        target = appsrc / "mod_c.py"
        good = "VALUE = 1\n"
        target.write_text(good, encoding="utf-8")
        # syntactically valid (passes pre-flight) but 'breaks the import' per the stub
        rec, err = _apply_selfedit(target, "import gone\nVALUE = 2\n")
        check("bricking self-edit ends rolled_back", rec.get("status") == "rolled_back")
        check("apply reports the rollback", bool(err) and "rolled back" in err)
        check("file restored to last-good bytes", target.read_text("utf-8") == good)
        check("re-probe after rollback recorded healthy", rec.get("healthy_after_rollback") is True)
        rb = _audit_records(config.AUDIT_LOG_FILE, "self_edit_rolled_back")
        check("rollback is audited", len(rb) >= 1)
        check("audit carries the failure reason", bool(rb and rb[-1].get("reason")))
        check("audit says restored from backup", bool(rb and rb[-1].get("rolled_back") is True))
    finally:
        seg.health_probe = _orig_probe

    # ------------------------------------------------- [6] rollback of a new file
    print("\n[6] A bricking NEW-file self-edit is removed on rollback")
    _orig_probe = seg.health_probe
    seg.health_probe = lambda *a, **k: (False, "SyntaxError elsewhere in the graph")
    try:
        target = appsrc / "mod_new.py"
        if target.exists():
            target.unlink()
        rec, err = _apply_selfedit(target, "VALUE = 3\n", action="create")
        check("new-file brick ends rolled_back", rec.get("status") == "rolled_back")
        check("created file is removed on rollback", not target.exists())
    finally:
        seg.health_probe = _orig_probe

    # ---------------------------------------------------------- [7] real probe smoke
    # `import server` runs config.validate(), which needs a configured ADAM_TOKEN +
    # CLAUDE_EXE. On a real install both are present (the app is running), so the probe
    # is meaningful there. In a bare checkout with no .env we can't boot, so skip rather
    # than report a spurious failure — the stubbed cases above already prove the wiring.
    print("\n[7] The real health probe boots the current tree cleanly")
    if not getattr(config, "ADAM_TOKEN", ""):
        print("  SKIP  real probe (no ADAM_TOKEN configured in this environment)")
    else:
        healthy, why = seg.health_probe(config.ROOT)
        check("real `import server` probe reports healthy", healthy)
        if not healthy:
            print(f"        (probe said: {why})")

    # ------------------------------------------------- [8] rollback of a rename
    print("\n[8] A bricking app-source .py rename is reversed on rollback")
    _orig_probe = seg.health_probe
    _rcalls = {"n": 0}

    def _flaky_rename_probe(*a, **k):
        # unhealthy right after the rename (old import path gone), healthy once reversed
        _rcalls["n"] += 1
        return (False, "ModuleNotFoundError: No module named 'mod_ren'") if _rcalls["n"] == 1 else (True, "")

    seg.health_probe = _flaky_rename_probe
    try:
        src = appsrc / "mod_ren.py"
        dst = appsrc / "mod_ren_new.py"
        if dst.exists():
            dst.unlink()
        src.write_text("VALUE = 7\n", encoding="utf-8")
        rec = pc.create(target_path=str(src), action="rename", content=None, new_path=str(dst))
        pc.approve(rec["id"])
        rec, err = pc.apply(rec["id"])
        check("bricking rename ends rolled_back", rec.get("status") == "rolled_back")
        check("rename reversed: source restored", src.is_file() and src.read_text("utf-8") == "VALUE = 7\n")
        check("rename reversed: destination gone", not dst.exists())
        check("re-probe after rename rollback healthy", rec.get("healthy_after_rollback") is True)
        rb = _audit_records(config.AUDIT_LOG_FILE, "self_edit_rolled_back")
        check("rename rollback notes the reversal", bool(rb and "reversed the rename" in (rb[-1].get("rollback_detail") or "")))
    finally:
        seg.health_probe = _orig_probe

    # -------------------------- [9] a still-unhealthy tree is flagged environmental
    print("\n[9] A rollback that leaves the tree unhealthy is reported as environmental")
    _orig_probe = seg.health_probe
    seg.health_probe = lambda *a, **k: (False, "ImportError: config not validated")  # unhealthy always
    try:
        target = appsrc / "mod_env.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        rec, err = _apply_selfedit(target, "VALUE = 2\n")
        check("still-unhealthy rollback ends rolled_back", rec.get("status") == "rolled_back")
        check("healthy_after_rollback is False", rec.get("healthy_after_rollback") is False)
        check("record flags the failure as environmental", rec.get("environmental") is True)
        check("message says it was not this edit", "not this edit" in (rec.get("error") or ""))
        check("edit still byte-restored to last-good", target.read_text("utf-8") == "VALUE = 1\n")
    finally:
        seg.health_probe = _orig_probe

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
