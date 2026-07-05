"""
Adam — self-edit transaction guard (Phase 2 self-edit hardening).

When Adam edits its OWN application source (the self-edit lane, unlocked by the
Unrestricted capability tier), a bad change can stop the
server from ever starting again: a syntax error, a missing import, a removed
symbol another module still needs. Backup-before-write means the BYTES are always
recoverable — but a dead process can't roll itself back. This already happened
once (v0.9.13 bricked with `ModuleNotFoundError: session_store`); recovery was
manual. This module makes app-source self-edits self-healing:

    pre-flight   — validate the proposed content in ISOLATION before the live file
                   is touched (compile() for .py, json.loads() for .json). A
                   broken change never reaches disk. This is the primary safety.
    health probe — after an app-source .py change is committed, boot a throwaway
                   subprocess that does `import server`. That pulls in the WHOLE
                   app import graph, so a bad import / removed symbol / syntax
                   error anywhere surfaces — while uvicorn.run and the
                   @app.on_event("startup") handlers stay gated behind
                   __main__/startup, so the probe binds no port and starts no
                   watcher. It's the second net (catches breakage pre-flight can't
                   see, like a sibling module that imports the changed one).
    auto-rollback — if the probe fails, restore the pre-change backup (a pure
                   filesystem op that does NOT need the app to import) and
                   re-probe, leaving the tree in its last-good state. Audited.

This is the 4th always-on rail alongside backup / audit / secret-guard. It
engages ONLY for app-source self-edits; ordinary vault/drafts writes are never
touched. Pure stdlib + config/permissions (no third-party imports), so it stays
importable even from a partially-broken tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import config
import permissions

# Only these suffixes can brick the Python import graph, so only they earn the
# (heavier) subprocess boot probe. Everything else self-edit can touch — .html,
# .css, .js, .md, .txt, .ps1, .cmd, .bat, .gs — can't stop `import server`, so the
# backup rail alone covers them.
_PROBE_SUFFIXES = {".py"}

# The boot probe imports the whole app; generous so a slow disk / cold cache on a
# friend's machine never trips a false "unhealthy" that rolls back a good edit.
_HEALTH_TIMEOUT_S = 40


def is_app_source_target(path: str | os.PathLike) -> bool:
    """True if `path` is one of the app's own source files under the self-edit lane.
    Thin, readable wrapper over permissions.is_app_self_editable (which already
    enforces the flag, the self-edit dirs, the data/ exclusion, and the ext list)."""
    return permissions.is_app_self_editable(path)


def preflight(target: str | os.PathLike, content: str | None) -> tuple[bool, str]:
    """Validate proposed self-edit content in ISOLATION, without touching disk.

    Returns (ok, reason). Only cheap, side-effect-free parsers run here:
        .py   -> compile()      (catches a SyntaxError anywhere in the file)
        .json -> json.loads()
    Other text types have no cheap validator; they pass pre-flight and rely on the
    backup rail. Empty content is allowed (an empty .py / a bare-{} .json is valid,
    and a delete/rename carries no content)."""
    try:
        suffix = Path(target).suffix.lower()
    except Exception:  # noqa: BLE001 — an unparseable path can't be pre-flighted
        return True, ""
    text = content or ""
    if suffix == ".py":
        try:
            compile(text, str(target), "exec")
        except SyntaxError as e:
            where = f" (line {e.lineno})" if e.lineno else ""
            return False, f"Python syntax error: {e.msg}{where}"
        except ValueError as e:  # e.g. source contains null bytes
            return False, f"invalid Python source: {e}"
    elif suffix == ".json":
        try:
            json.loads(text)
        except ValueError as e:
            return False, f"invalid JSON: {e}"
    return True, ""


def needs_health_probe(target: str | os.PathLike, action: str) -> bool:
    """Whether a committed app-source change warrants the subprocess boot probe.
    Only .py changes can break `import server`; other text types can't, so they
    skip the (heavier) probe. Rename IS included: renaming an app-source .py makes
    its old import path vanish, which bricks the app just as surely as a bad edit —
    and the rollback path reverses the move (see _rollback_self_edit). `target` here
    is the SOURCE path of a rename, so the .py suffix check catches exactly the case
    where an importable module is being moved out from under the app."""
    if action not in ("create", "edit", "replace", "delete", "rename"):
        return False
    try:
        return Path(target).suffix.lower() in _PROBE_SUFFIXES
    except Exception:  # noqa: BLE001
        return False


def health_probe(install_root: str | os.PathLike | None = None,
                 timeout: int = _HEALTH_TIMEOUT_S) -> tuple[bool, str]:
    """Boot a throwaway subprocess that imports the whole app graph and report
    whether it loads.

    `import server` transitively imports every module the running app uses, so a
    syntax error / missing module / bad import ANYWHERE fails the import — while
    uvicorn.run and the startup handlers stay gated, so nothing binds a port or
    starts a watcher. The probe runs with ADAM_SELF_EDIT_PROBE=1 so server.py
    keeps its logging off the live rotating file (another process owns it).

    Never raises: a spawn failure or timeout is reported as UNHEALTHY, so the
    caller rolls back rather than trusting a tree it couldn't verify. Returns
    (healthy, reason)."""
    root = Path(install_root or config.ROOT)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ADAM_SELF_EDIT_PROBE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import server"],
            cwd=str(root), env=env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"health probe timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001 — could not spawn -> treat as unhealthy
        return False, f"health probe could not run: {e}"
    if proc.returncode == 0:
        return True, ""
    detail = _last_error_line(proc.stderr or proc.stdout or "")
    return False, detail or f"app failed to import (exit {proc.returncode})"


def _last_error_line(text: str, limit: int = 400) -> str:
    """The most informative tail of a traceback — the final non-empty line, which
    is usually the exception itself (e.g. 'ModuleNotFoundError: No module named
    ...'). Bounded so the reason stays small enough for the audit log."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""
