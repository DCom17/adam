"""
Adam — permission manager (Level 3 controlled-action foundation).

A small, dependency-free, testable safety layer that sits in front of any write
the server performs (and, in time, any tool action the agent requests). The
model is deny-by-default with an explicit allow-list:

    * READ  is permitted only inside `read_dirs`  (default: the vault).
    * WRITE is permitted only inside `write_dirs` (default: data/outputs+drafts)
            plus the server's own infrastructure dirs (uploads/logs/state/backups).
    * Blocked paths and protected filename patterns (secrets/keys/config) are
      refused even inside an allowed directory.
    * Destructive actions (delete/overwrite/move/run-command/…) are detected by a
      conservative rule list and, by policy, require approval.
    * Writes are backed up before overwriting an existing file, and every
      decision is recorded to a structured JSONL audit log.

The policy comes entirely from `config` (settings.json), so behavior is
operator-controlled, not hard-coded. Nothing here logs secrets.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import config


# --- Path helpers -----------------------------------------------------------


def normalize_and_validate_path(path: str | os.PathLike) -> Path:
    """Expand (~, $VARS), resolve to an absolute path, and return it.

    Resolving collapses any `..` traversal, so callers can't escape an allowed
    directory by smuggling `../../` into a path. Raises ValueError on empty."""
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("empty path")
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return Path(expanded).resolve()


def _contains(parent: str | os.PathLike, child: str | os.PathLike) -> bool:
    """True if `child` is `parent` or lives somewhere beneath it. Case-insensitive
    on Windows (via normcase); returns False across different drives."""
    p = os.path.normcase(str(Path(parent).resolve()))
    c = os.path.normcase(str(Path(child).resolve()))
    try:
        return os.path.commonpath([p, c]) == p
    except ValueError:
        return False  # different drives / mixed absolute-relative


def is_within_allowed_dir(path: str | os.PathLike, allowed_dirs: list[str]) -> bool:
    """True if `path` resolves to within any directory in `allowed_dirs`."""
    try:
        target = normalize_and_validate_path(path)
    except ValueError:
        return False
    return any(_contains(d, target) for d in allowed_dirs if str(d).strip())


# --- Block / protect checks -------------------------------------------------


def is_blocked_path(path: str | os.PathLike) -> bool:
    """True if `path` is under any configured blocked path. Blocked paths win
    over every allow-list."""
    return is_within_allowed_dir(path, config.PERM_BLOCKED_PATHS)


def is_protected_file(path: str | os.PathLike) -> bool:
    """True if the filename matches a protected pattern (secrets/keys/config).
    Matching is case-insensitive on the basename only."""
    try:
        name = normalize_and_validate_path(path).name.lower()
    except ValueError:
        return False
    return any(
        fnmatch.fnmatch(name, str(pat).lower())
        for pat in config.PERM_PROTECTED_FILE_PATTERNS
    )


# --- Read / write permission ------------------------------------------------


def is_path_allowed_for_read(path: str | os.PathLike) -> bool:
    """True if reading `path` is permitted: inside a read dir (or any write/infra
    dir, which are readable too) and not blocked."""
    if is_blocked_path(path):
        return False
    readable = config.PERM_READ_DIRS + config.PERM_WRITE_DIRS + config.INFRA_WRITE_DIRS
    return is_within_allowed_dir(path, readable)


def is_path_allowed_for_write(path: str | os.PathLike) -> bool:
    """True if writing `path` is permitted: inside a write dir (or a server infra
    dir), not blocked, and not a protected filename.

    Brain self-write lane: when `BRAIN_WRITE_ENABLED` is on, the configured vault
    (the bundled brain) is also a write target — but a blocked path or a protected
    filename (.env/*.pem/settings.json/…) INSIDE the vault is still refused above,
    so secrets in the vault can never be written."""
    if is_blocked_path(path) or is_protected_file(path):
        return False
    writable = config.PERM_WRITE_DIRS + config.INFRA_WRITE_DIRS
    if getattr(config, "BRAIN_WRITE_ENABLED", False) and str(getattr(config, "VAULT_PATH", "")).strip():
        writable = writable + [str(config.VAULT_PATH)]
    if is_within_allowed_dir(path, writable):
        return True
    # Self-edit lane: the app may change its OWN source when enabled. The blocked/
    # protected checks above already refused secrets; this also excludes data/ and
    # non-code file types.
    return is_app_self_editable(path)


def is_within_vault(path: str | os.PathLike) -> bool:
    """True if `path` resolves inside the configured vault (the brain). The brain
    self-write lane uses this to decide auto-apply (in-vault, non-destructive) vs.
    approval (everything else)."""
    v = str(getattr(config, "VAULT_PATH", "")).strip()
    return bool(v) and is_within_allowed_dir(path, [v])


def is_app_self_editable(path: str | os.PathLike) -> bool:
    """True if `path` is one of the app's OWN source files the self-edit lane may write
    to. OFF unless config.PERM_ALLOW_APP_SELF_EDIT is enabled. Excludes the runtime data
    tree and anything that isn't a recognized source/text file type. (Secrets are already
    refused by is_protected_file upstream, so they can never reach here as writable.)"""
    if not getattr(config, "PERM_ALLOW_APP_SELF_EDIT", False):
        return False
    if not is_within_allowed_dir(path, getattr(config, "PERM_APP_SELF_EDIT_DIRS", [])):
        return False
    excl = getattr(config, "PERM_APP_SELF_EDIT_EXCLUDE", [])
    if excl and is_within_allowed_dir(path, excl):
        return False
    try:
        suffix = normalize_and_validate_path(path).suffix.lower()
    except ValueError:
        return False
    return suffix in getattr(config, "PERM_APP_SELF_EDIT_EXTS", [])


# --- Destructive action detection -------------------------------------------
# Conservative, rule-based, case-insensitive. Catches both natural-language
# intent ("delete the file", "wipe it") and concrete tool/command syntax
# ("rm -rf", "git reset --hard"). False positives are acceptable here — the cost
# is an extra approval prompt, which is the safe direction.

# Multi-word and substring-safe phrases. Words that are risky as substrings
# ("format" inside "information", "erase"/"rename" inside longer words) live in
# the word-boundary token list below instead.
_DESTRUCTIVE_KEYWORDS = [
    "delete", "remove", "overwrite", "replace file", "wipe",
    "truncate", "drop table", "move file", "uninstall", "purge",
    "send email", "send message", "publish", "deploy", "force push",
]
# Whole-word command/verb tokens. Kept separate so we can require word boundaries
# (so "format" matches but "information" does not, "del" matches but "model" doesn't).
_DESTRUCTIVE_TOKENS = [
    "rm", "rmdir", "del", "erase", "format", "mv", "move", "rename", "kill",
    "shutdown", "shell", "powershell", "pwsh", "cmd", "bash", "exec", "execute",
    "run", "invoke-expression", "iex",
]
_DESTRUCTIVE_PHRASES = [
    "git reset", "git clean", "git push --force", "git push -f",
    "drop database", "rm -rf", "rd /s", "del /f", "remove-item",
]

_TOKEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _DESTRUCTIVE_TOKENS) + r")\b",
    re.IGNORECASE,
)


def _stringify(action: Any) -> str:
    """Flatten an action (string, or tool-call dict like {'tool':'Bash',
    'command':'rm x'}) into one searchable lowercase string."""
    if isinstance(action, str):
        return action.lower()
    try:
        return json.dumps(action, default=str).lower()
    except Exception:
        return str(action).lower()


def is_destructive_action(action: Any) -> bool:
    """True if the action text or tool call looks destructive/risky.

    Accepts a free-text string or a structured tool call (dict/list). Rule-based
    and intentionally broad — better to over-flag than to silently allow."""
    text = _stringify(action)
    if not text.strip():
        return False
    if any(kw in text for kw in _DESTRUCTIVE_KEYWORDS):
        return True
    if any(ph in text for ph in _DESTRUCTIVE_PHRASES):
        return True
    return bool(_TOKEN_RE.search(text))


def classify_risk(action: Any, *, is_write: bool = False) -> str:
    """Coarse risk band for an action: 'high' (destructive), 'medium' (a plain
    write/mutation), or 'low' (read/inspect)."""
    if is_destructive_action(action):
        return "high"
    if is_write:
        return "medium"
    return "low"


# --- Approval policy --------------------------------------------------------


def requires_approval(
    action: Any, *, is_write: bool = False, destructive: bool | None = None
) -> bool:
    """Whether this action must be approved before it runs, per policy.

    Destructive actions require approval when `require_approval_for_destructive`
    is set; plain writes require approval when `require_approval_for_writes` is
    set. `destructive` may be passed explicitly to avoid re-detecting."""
    is_dest = is_destructive_action(action) if destructive is None else destructive
    if is_dest and config.PERM_REQUIRE_APPROVAL_DESTRUCTIVE:
        return True
    if is_write and config.PERM_REQUIRE_APPROVAL_WRITES:
        return True
    return False


# --- Shell / command policy -------------------------------------------------


def is_command_allowed(command: str) -> bool:
    """Whether a shell command may run. Off entirely unless `allow_shell` is true;
    when on, the command's first token must be in `allowed_commands` (empty list
    with shell on means allow any — an explicit operator choice)."""
    if not config.PERM_ALLOW_SHELL:
        return False
    allow = config.PERM_ALLOWED_COMMANDS
    if not allow:
        return True
    first = (command or "").strip().split()
    if not first:
        return False
    head = Path(first[0]).name.lower()
    return any(head == str(c).strip().lower() for c in allow)


# --- Backups ----------------------------------------------------------------


def make_backup_before_write(path: str | os.PathLike) -> Path | None:
    """Copy an existing file into the backup dir with a timestamp before it's
    overwritten. Returns the backup path, or None if there was nothing to back
    up, backups are disabled, or the file exceeds the max size.

    Backups never raise into the caller — a backup failure is logged by the
    caller via the returned None, not by crashing the write path."""
    if not config.PERM_BACKUP_BEFORE_WRITE:
        return None
    try:
        src = normalize_and_validate_path(path)
    except ValueError:
        return None
    if not src.is_file():
        return None  # new file — nothing to preserve
    max_bytes = config.PERM_MAX_FILE_SIZE_MB * 1024 * 1024
    try:
        if src.stat().st_size > max_bytes:
            return None  # too large to back up; caller decides whether to proceed
    except OSError:
        return None
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = config.BACKUP_DIR / f"{stamp}_{src.name}"
    n = 1
    while dest.exists():  # avoid clobbering same-second backups
        dest = config.BACKUP_DIR / f"{stamp}_{n}_{src.name}"
        n += 1
    try:
        shutil.copy2(src, dest)
        prune_backups()
        return dest
    except OSError:
        return None


def prune_backups() -> int:
    """Keep the backup dir bounded so undo history can't grow forever: delete backups
    older than PERM_BACKUP_MAX_AGE_DAYS, then keep only the newest PERM_BACKUP_KEEP.
    Returns how many were removed. Never raises into the caller."""
    keep = int(getattr(config, "PERM_BACKUP_KEEP", 50))
    max_age = int(getattr(config, "PERM_BACKUP_MAX_AGE_DAYS", 30))
    removed = 0
    try:
        if not config.BACKUP_DIR.is_dir():
            return 0
        files = [p for p in config.BACKUP_DIR.iterdir() if p.is_file()]
        if max_age > 0:
            cutoff = time.time() - max_age * 86400
            for p in list(files):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink(); files.remove(p); removed += 1
                except OSError:
                    pass
        if keep > 0 and len(files) > keep:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[keep:]:
                try:
                    p.unlink(); removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed


# --- Audit log --------------------------------------------------------------


def record_audit_event(event: dict) -> None:
    """Append one structured audit record (JSON line) to the audit log.

    A timestamp is added if absent. Best-effort: auditing must never break the
    action it describes. Never call this with secret material in `event`."""
    if not config.PERM_AUDIT_LOG_ENABLED:
        return
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
    try:
        config.AUDIT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.AUDIT_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


# --- High-level decision ----------------------------------------------------


@dataclass
class Decision:
    """The outcome of evaluating a requested action against policy."""
    allowed: bool                      # is the action permitted at all?
    requires_approval: bool            # must a human approve before it runs?
    risk: str                          # "low" | "medium" | "high"
    action_type: str                   # "read" | "write" | "command" | ...
    reason: str                        # human-readable explanation
    target: str | None = None          # path or command the action concerns
    destructive: bool = False
    backup_path: str | None = None     # set once a backup is actually made
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(
    action_type: str,
    *,
    path: str | os.PathLike | None = None,
    command: str | None = None,
    action_text: Any | None = None,
    content_len: int | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
    audit: bool = True,
) -> Decision:
    """Evaluate a requested action against the full policy and (optionally) audit
    the decision. This is the single entry point write/command helpers should
    call before doing anything.

    `action_type` is "read", "write", or "command". The relevant target
    (path/command) drives the allow/deny + approval decision. Returns a Decision;
    it does NOT perform the action or the backup — the caller does that only if
    `allowed` and (approval satisfied)."""
    target = str(path) if path is not None else (command or None)
    probe = action_text if action_text is not None else (command or str(path or ""))
    is_write = action_type == "write"
    is_command = action_type == "command"
    destructive = is_destructive_action(probe) or is_command

    allowed = True
    reason = "allowed by policy"

    if is_command:
        allowed = is_command_allowed(command or "")
        reason = (
            "shell command permitted" if allowed
            else "shell disabled or command not in allow-list"
        )
    elif action_type == "read":
        allowed = is_path_allowed_for_read(path or "")
        reason = "read within allowed dirs" if allowed else "read outside allowed dirs (or blocked)"
    elif is_write:
        if path is not None and is_blocked_path(path):
            allowed, reason = False, "path is blocked"
        elif path is not None and is_protected_file(path):
            allowed, reason = False, "protected file pattern (secret/key/config)"
        elif content_len is not None and content_len > config.PERM_MAX_FILE_SIZE_MB * 1024 * 1024:
            allowed, reason = False, f"exceeds max_file_size_mb ({config.PERM_MAX_FILE_SIZE_MB} MB)"
        elif not is_path_allowed_for_write(path or ""):
            allowed, reason = False, "write outside allowed dirs"
        else:
            reason = "write within allowed dirs"
    else:
        allowed, reason = False, f"unknown action_type: {action_type}"

    needs_approval = allowed and requires_approval(
        probe, is_write=is_write, destructive=destructive
    )
    risk = classify_risk(probe, is_write=is_write)

    decision = Decision(
        allowed=allowed,
        requires_approval=needs_approval,
        risk=risk,
        action_type=action_type,
        reason=reason,
        target=target,
        destructive=destructive,
    )

    if audit:
        record_audit_event({
            "action_type": action_type,
            "target": target,
            "allowed": allowed,
            "requires_approval": needs_approval,
            "approved": None,  # filled in later if/when an approval resolves
            "risk": risk,
            "destructive": destructive,
            "reason": reason,
            "session_id": session_id,
            "job_id": job_id,
        })

    return decision
