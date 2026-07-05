"""
Adam — permission-system tests.

Self-contained: it redirects the permission policy at a temporary sandbox (so it
never touches your real data/ tree), then exercises every layer:

    read allow/deny · write allow/deny · blocked paths · protected files ·
    destructive detection · backup-before-write · audit logging ·
    evaluate() decisions · approval create/list/approve/deny lifecycle.

Run:  python test_permissions.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import config
import permissions
import approvals

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
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_perm_test_"))

    vault = sandbox / "vault"
    writable = sandbox / "outputs"
    blocked = sandbox / "secret_zone"
    backups = sandbox / "backups"
    state = sandbox / "state"
    logs = sandbox / "logs"
    for d in (vault, writable, blocked, backups, state, logs):
        d.mkdir(parents=True, exist_ok=True)

    # --- Point the policy at the sandbox ------------------------------------
    config.PERM_READ_DIRS = [str(vault)]
    config.PERM_WRITE_DIRS = [str(writable)]
    config.INFRA_WRITE_DIRS = [str(state), str(logs), str(backups)]
    config.PERM_BLOCKED_PATHS = [str(blocked)]
    config.PERM_PROTECTED_FILE_PATTERNS = [".env", "*.pem", "*.key", "settings.json"]
    config.PERM_MAX_FILE_SIZE_MB = 1
    config.PERM_REQUIRE_APPROVAL_WRITES = True
    config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE = True
    config.PERM_BACKUP_BEFORE_WRITE = True
    config.PERM_AUDIT_LOG_ENABLED = True
    config.PERM_ALLOW_SHELL = False
    config.PERM_ALLOWED_COMMANDS = []
    config.BACKUP_DIR = backups
    config.AUDIT_LOG_FILE = logs / "audit.jsonl"
    config.APPROVALS_FILE = state / "approvals.json"
    config.VAULT_PATH = str(vault)
    config.BRAIN_WRITE_ENABLED = False  # shipped default; toggled on in [BW] below

    print("\n[BW] Brain self-write lane (vault as a guarded write target)")
    brain_file = vault / "02_command_memory" / "long_term_memory.md"
    check("brain write denied when lane OFF",
          not permissions.is_path_allowed_for_write(brain_file))
    config.BRAIN_WRITE_ENABLED = True
    check("brain write allowed when lane ON",
          permissions.is_path_allowed_for_write(brain_file))
    check("is_within_vault true for a vault file", permissions.is_within_vault(brain_file))
    check("is_within_vault false for an outputs file",
          not permissions.is_within_vault(writable / "x.md"))
    # Lane ON must NOT open secrets inside the vault.
    check("protected .env in vault STILL refused under lane",
          not permissions.is_path_allowed_for_write(vault / ".env"))
    check("protected settings.json in vault STILL refused under lane",
          not permissions.is_path_allowed_for_write(vault / "settings.json"))
    check("blocked path STILL refused under lane",
          not permissions.is_path_allowed_for_write(blocked / "x.md"))
    config.BRAIN_WRITE_ENABLED = False  # restore off for the remaining sections

    print("\n[SE] Self-edit lane (app may write its OWN source when enabled)")
    appdir = sandbox / "app"
    (appdir / "web").mkdir(parents=True, exist_ok=True)
    (appdir / "data" / "state").mkdir(parents=True, exist_ok=True)
    config.PERM_APP_SELF_EDIT_DIRS = [str(appdir)]
    config.PERM_APP_SELF_EDIT_EXCLUDE = [str(appdir / "data")]
    config.PERM_APP_SELF_EDIT_EXTS = [".py", ".html", ".css", ".js", ".json", ".md", ".txt"]
    config.PERM_ALLOW_APP_SELF_EDIT = False
    check("app code NOT writable when self-edit OFF",
          not permissions.is_path_allowed_for_write(appdir / "server.py"))
    config.PERM_ALLOW_APP_SELF_EDIT = True
    check("app .py writable when self-edit ON",
          permissions.is_path_allowed_for_write(appdir / "server.py"))
    check("app web/index.html writable when self-edit ON",
          permissions.is_path_allowed_for_write(appdir / "web" / "index.html"))
    check("secrets STILL refused under self-edit (.env)",
          not permissions.is_path_allowed_for_write(appdir / ".env"))
    check("secrets STILL refused under self-edit (settings.json)",
          not permissions.is_path_allowed_for_write(appdir / "settings.json"))
    check("runtime data/ tree EXCLUDED from self-edit",
          not permissions.is_path_allowed_for_write(appdir / "data" / "state" / "x.json"))
    check("non-code file type refused under self-edit (.onnx)",
          not permissions.is_path_allowed_for_write(appdir / "model.onnx"))
    check("files OUTSIDE the app dir not made writable by self-edit",
          not permissions.is_path_allowed_for_write(sandbox / "elsewhere.py"))
    config.PERM_ALLOW_APP_SELF_EDIT = False  # restore off

    print("\n[BK] Backup pruning keeps the undo history bounded")
    prunedir = sandbox / "prunebk"
    prunedir.mkdir(parents=True, exist_ok=True)
    _save_backup_dir = config.BACKUP_DIR
    config.BACKUP_DIR = prunedir
    config.PERM_BACKUP_MAX_AGE_DAYS = 0   # disable age-prune; test count-prune only
    config.PERM_BACKUP_KEEP = 3
    for i in range(7):
        (prunedir / f"bk_{i}.txt").write_text("x")
    removed = permissions.prune_backups()
    remaining = len([p for p in prunedir.iterdir() if p.is_file()])
    check("prune removes the excess backups (7 -> keep 3)", removed == 4)
    check("prune keeps exactly PERM_BACKUP_KEEP newest", remaining == 3)
    config.BACKUP_DIR = _save_backup_dir       # restore for later sections
    config.PERM_BACKUP_KEEP = 50               # permissive so later backups aren't pruned
    config.PERM_BACKUP_MAX_AGE_DAYS = 30

    print("\n[1] Read permission")
    check("read inside vault allowed", permissions.is_path_allowed_for_read(vault / "notes.md"))
    check("read outside everything denied", not permissions.is_path_allowed_for_read(sandbox / "elsewhere.md"))
    check("read of blocked path denied", not permissions.is_path_allowed_for_read(blocked / "x.txt"))
    check("traversal out of vault denied",
          not permissions.is_path_allowed_for_read(vault / ".." / "elsewhere.md"))

    print("\n[2] Write permission")
    check("write inside outputs allowed", permissions.is_path_allowed_for_write(writable / "draft.md"))
    check("write to vault denied (read-only)", not permissions.is_path_allowed_for_write(vault / "draft.md"))
    check("write outside everything denied", not permissions.is_path_allowed_for_write(sandbox / "x.md"))
    check("write to blocked path denied", not permissions.is_path_allowed_for_write(blocked / "x.md"))
    check("write protected .env denied (even in allowed dir)",
          not permissions.is_path_allowed_for_write(writable / ".env"))
    check("write protected *.pem denied", not permissions.is_path_allowed_for_write(writable / "id.pem"))
    check("write infra (state) allowed", permissions.is_path_allowed_for_write(state / "approvals.json"))

    print("\n[3] Destructive detection")
    check("'delete the report' flagged", permissions.is_destructive_action("please delete the report"))
    check("'rm -rf /tmp/x' flagged", permissions.is_destructive_action("rm -rf /tmp/x"))
    check("'git reset --hard' flagged", permissions.is_destructive_action("git reset --hard HEAD~1"))
    check("tool-call dict flagged", permissions.is_destructive_action({"tool": "Bash", "command": "del file.txt"}))
    check("'send email to ...' flagged", permissions.is_destructive_action("send email to the team"))
    check("benign read NOT flagged", not permissions.is_destructive_action("summarize my notes for me"))
    check("'information' not a false positive", not permissions.is_destructive_action("give me information about models"))

    print("\n[4] Risk + approval policy")
    check("destructive risk == high", permissions.classify_risk("rm -rf x") == "high")
    check("plain write risk == medium", permissions.classify_risk("write a draft", is_write=True) == "medium")
    check("read risk == low", permissions.classify_risk("read it") == "low")
    check("write requires approval", permissions.requires_approval("draft", is_write=True))
    check("destructive requires approval", permissions.requires_approval("delete x", destructive=True))

    print("\n[5] Shell policy")
    check("command denied when shell off", not permissions.is_command_allowed("ls -la"))
    config.PERM_ALLOW_SHELL = True
    config.PERM_ALLOWED_COMMANDS = ["git"]
    check("git allowed when whitelisted", permissions.is_command_allowed("git status"))
    check("rm denied (not whitelisted)", not permissions.is_command_allowed("rm -rf x"))
    config.PERM_ALLOW_SHELL = False
    config.PERM_ALLOWED_COMMANDS = []

    print("\n[6] Backup before write")
    target = writable / "report.md"
    target.write_text("original content", encoding="utf-8")
    backup_path = permissions.make_backup_before_write(target)
    check("backup created for existing file", backup_path is not None and Path(backup_path).is_file())
    check("backup preserves content",
          backup_path is not None and Path(backup_path).read_text("utf-8") == "original content")
    check("no backup for nonexistent file",
          permissions.make_backup_before_write(writable / "does_not_exist.md") is None)

    print("\n[7] Audit logging")
    permissions.record_audit_event({"action_type": "write", "target": str(target), "allowed": True})
    audit_lines = config.AUDIT_LOG_FILE.read_text("utf-8").strip().splitlines()
    check("audit file has at least one line", len(audit_lines) >= 1)
    last = json.loads(audit_lines[-1])
    check("audit record has ts + action_type", "ts" in last and last.get("action_type") == "write")

    print("\n[8] evaluate() decisions")
    d_ok = permissions.evaluate("write", path=str(writable / "draft.md"), content_len=10, audit=False)
    check("allowed write -> allowed", d_ok.allowed)
    check("allowed write -> needs approval", d_ok.requires_approval)
    d_bad = permissions.evaluate("write", path=str(vault / "draft.md"), audit=False)
    check("write to read-only vault -> denied", not d_bad.allowed)
    d_big = permissions.evaluate("write", path=str(writable / "big.bin"),
                                 content_len=5 * 1024 * 1024, audit=False)
    check("oversize write -> denied", not d_big.allowed and "max_file_size" in d_big.reason)
    d_read = permissions.evaluate("read", path=str(vault / "notes.md"), audit=False)
    check("read inside vault -> allowed, low risk", d_read.allowed and d_read.risk == "low")

    print("\n[9] Approval lifecycle")
    a = approvals.create(action_summary="overwrite report.md", action_type="write",
                         risk_level="medium", target=str(target), session_id="s1")
    check("approval created pending", a["status"] == "pending" and a["id"])
    pend = approvals.list_all(status="pending")
    check("appears in pending list", any(r["id"] == a["id"] for r in pend))
    approved = approvals.approve(a["id"])
    check("approve -> status approved", approved and approved["status"] == "approved")
    check("re-approve is idempotent-ish (already approved)",
          approvals.approve(a["id"])["status"] == "approved")
    b = approvals.create(action_summary="delete temp", action_type="destructive", risk_level="high")
    denied = approvals.deny(b["id"])
    check("deny -> status denied", denied and denied["status"] == "denied")
    # Dismiss clears an approval from the queue entirely (any status).
    cc = approvals.create(action_summary="clear me", action_type="write")
    cleared = approvals.dismiss(cc["id"])
    check("dismiss returns the removed approval", cleared and cleared["id"] == cc["id"])
    check("dismissed approval is gone from the queue", approvals.get(cc["id"]) is None)
    check("dismiss unknown approval -> None", approvals.dismiss("nope_x") is None)
    check("get unknown id -> None", approvals.get("nope_not_real") is None)
    check("audit captured approval events",
          any("approval" in json.loads(l).get("action_type", "")
              for l in config.AUDIT_LOG_FILE.read_text("utf-8").strip().splitlines()))

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"  sandbox: {sandbox}")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
