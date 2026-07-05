"""
Allow-list release builder for Adam (v0.9.0, Slice 3).

Builds a distributable ZIP containing ONLY the files on an explicit allow-list — never
a directory walk — so a new secret/runtime/backup file can't slip in just by existing.
A fail-closed deny guard re-scans the staged set and ABORTS before writing anything if
any path matches a secret / runtime / rollback-backup pattern. Pure stdlib. Touches no
product behavior; only reads files and writes a ZIP under dist/.

Usage:
    python scripts/make_release.py                 # build dist/adam-local-vX.Y.Z.zip
    python scripts/make_release.py --out <dir>     # choose output dir
    python scripts/make_release.py --version 0.9.0 # override the version label
    python scripts/make_release.py --list          # print the staged file list, build nothing
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- Allow-list: ONLY these ship -------------------------------------------------
_ROOT_FILES = [
    # product modules
    "server.py", "config.py", "permissions.py", "proposed_changes.py",
    "self_edit_guard.py", "security.py", "models.py", "rate_limit.py",
    "usage_store.py", "tts_supervisor.py",
    "approvals.py", "diffs.py", "job_store.py", "session_store.py", "onboarding.py", "agent_write_probe.py",
    "google_calendar.py", "integration_registry.py", "twilio_sms.py",
    "twilio_voicemail.py", "voicemail_provision.py", "voicemail_store.py",
    "voicemail_contacts.py",
    "gmail.py", "gmail_bridge.gs", "linkedin.py", "hunter.py",
    "external_actions.py", "update_engine.py", "merge.py", "updater.py", "phone_link.py",
    "integration_config.py",
    # meta / templates (NEVER the real .env or settings.json — those are deny-guarded)
    "requirements.txt", "README.md", "CHANGELOG.md", "LICENSE",
    ".env.example", "settings.example.json", ".gitignore",
    # consumer one-click entry points (double-clickable; the non-technical front door)
    "START_HERE.txt", "SETUP.cmd", "START.cmd", "UPDATE.cmd", "INSTALL-VOICE.cmd",
    # Apps Script bridge the user pastes into their OWN Google account (template,
    # no secret — the token is generated inside the user's script, not shipped).
    "calendar_bridge.gs",
    "hunter_dashboard.gs", "hunter_verify.gs",
]
_WEB_FILES = ["index.html", "console.html", "settings.html", "setup-calendar.html", "setup-email.html", "setup-linkedin.html", "setup-sms.html", "setup-voicemail.html", "setup-hunter.html", "hunter-dashboard.html", "sw.js", "manifest.json", "icon.png", "icon.ico"]
_SCRIPT_FILES = [
    "setup.py", "doctor.py", "make_release.py", "make_release.ps1", "apply_update.py",
    "self_update.py", "publish-release.ps1",
    "wizard.ps1", "add-app-shortcut.ps1", "adam-app.vbs", "update.ps1", "install-voice.ps1",
    "tts_server/tts_server.py", "tts_server/requirements.txt",
    "start-adam.ps1", "connect-phone.py", "connect-phone.ps1",
    # forwarding shims: pre-rename desktop/taskbar shortcuts point at the old
    # launcher names (start-jarvis.ps1 directly, jarvis-app.vbs via wscript)
    "start-jarvis.ps1", "jarvis-app.vbs",
    "copy-token.ps1",
    "start-dev.ps1", "stop-dev.ps1", "restart-dev.ps1",
    "health-check.ps1", "test-permissions.ps1", "agent-write-probe.ps1",
]
# User-facing docs only — internal process docs are intentionally NOT shipped.
_DOC_FILES = [
    "BETA_HANDOFF.md", "CONNECT_YOUR_PHONE.md", "ADVANCED_REMOTE.md", "SUPPORT.md",
    "RELEASE.md", "CONSUMER_TEST_CHECKLIST.md",
]
_ROUTERS_FILES = [  # the routers/ package server.py imports at boot
    "__init__.py", "chat.py", "integrations.py", "reviews.py", "system.py",
    "voice_push.py",
]
_TEST_GLOB = "test_*.py"   # F&F beta ships the test suites for self-verification
_DATA_KEEP = "data/.gitkeep"

# The de-personalized brain vault ships as a whole folder (walked, not hand-listed).
# Every brain file still passes the path deny-guard AND a content guard below.
_BRAIN_DIR = "brain"
# Content that must NEVER appear in a shipped brain file — Morrow infrastructure or
# owner-identifying text. Fail-closed backstop against a personal-data leak.
# Owner-identifying terms live in scripts/release_guard.local (gitignored, never
# shipped, one term per line) so the guard itself doesn't publish them.
_GUARD_LOCAL = Path(__file__).with_name("release_guard.local")
_BRAIN_FORBIDDEN_BASE = (
    r"15_morrow|morrow_distilled|morrow_index|morrow_overview|morrow_workflow"
    r"|inheritance layer|Morrow Systems"
)


def _forbidden_regex() -> re.Pattern:
    parts = [_BRAIN_FORBIDDEN_BASE]
    if _GUARD_LOCAL.is_file():
        for line in _GUARD_LOCAL.read_text("utf-8", errors="ignore").splitlines():
            term = line.strip()
            if term and not term.startswith("#"):
                parts.append(re.escape(term))
    return re.compile("|".join(parts), re.IGNORECASE)


_BRAIN_FORBIDDEN = _forbidden_regex()

# --- Deny guard: the staged set must match NONE of these -------------------------
_DENY_GLOBS = [
    ".env", ".env.*", "settings.json", "settings.json.bak.*",
    "*.pem", "*.bak", "*.pre-*", "*.pyc", "*.local",
    "vapid_private*", "push_sub*", "last_result*",
    "data/*",  # runtime user data; only data/.gitkeep is allow-listed (excepted below)
    # internal process / rig-reference docs (belt-and-suspenders; allow-list omits them)
    "docs/DECISION_LOG.md", "docs/ROADMAP.md", "docs/PHASE_HISTORY.md",
    "docs/NEXT_PHASE_PROMPT.md", "docs/PARKING_LOT.md",
    "docs/PERSONAL_FIX_PORTING_LOG.md", "docs/DEVICE_ACCEPTANCE_RUN_*.md",
]
_DENY_CONTAINS = ["__pycache__/", ".git/"]   # any path inside these dirs
# Paths that match a deny glob but are deliberately shippable.
_DENY_EXCEPT = {".env.example", "data/.gitkeep"}


def _is_denied(rel: str) -> str | None:
    """Return the matched deny pattern if `rel` (posix) must NOT ship, else None."""
    base = rel.split("/")[-1]
    # `.env.example` is a template and may ship from anywhere (root or brain/).
    if rel in _DENY_EXCEPT or base == ".env.example":
        return None
    for pat in _DENY_GLOBS:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat):
            return pat
    probe = rel if rel.endswith("/") else rel + "/"
    for sub in _DENY_CONTAINS:
        if sub in probe:
            return sub
    return None


def _brain_files() -> list[str]:
    """Every file under brain/ as posix rel paths, skipping VCS/cache dirs. The
    deny-guard and the brain content-guard still vet each one before it ships."""
    base = ROOT / _BRAIN_DIR
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if "/__pycache__/" in "/" + rel or "/.git/" in "/" + rel:
            continue
        out.append(rel)
    return out


def staged_files() -> list[str]:
    """Resolve the allow-list to existing files, as sorted posix rel paths."""
    rels: list[str] = []
    for f in _ROOT_FILES:
        if (ROOT / f).is_file():
            rels.append(f)
    for f in _WEB_FILES:
        if (ROOT / "web" / f).is_file():
            rels.append(f"web/{f}")
    for f in _SCRIPT_FILES:
        if (ROOT / "scripts" / f).is_file():
            rels.append(f"scripts/{f}")
    for f in _DOC_FILES:
        if (ROOT / "docs" / f).is_file():
            rels.append(f"docs/{f}")
    for f in _ROUTERS_FILES:
        if (ROOT / "routers" / f).is_file():
            rels.append(f"routers/{f}")
    for p in sorted(ROOT.glob(_TEST_GLOB)):
        if p.is_file():
            rels.append(p.name)
    if (ROOT / _DATA_KEEP).is_file():
        rels.append(_DATA_KEEP)
    rels.extend(_brain_files())
    return sorted(set(rels))


def check_no_excluded(rels: list[str]) -> None:
    """Fail closed: raise if ANY staged path matches a deny pattern."""
    bad = [(r, _is_denied(r)) for r in rels if _is_denied(r)]
    if bad:
        raise RuntimeError(
            "release deny-guard tripped (refusing to ship): "
            + "; ".join(f"{r} ~ {pat}" for r, pat in bad)
        )


def check_brain_clean(rels: list[str]) -> None:
    """Fail closed: refuse to ship if any bundled brain file contains Morrow
    infrastructure or owner-identifying text. The path deny-guard covers secret/
    runtime files; this is the content backstop for the personal-data leak risk."""
    bad = []
    for rel in rels:
        if not rel.startswith(_BRAIN_DIR + "/"):
            continue
        try:
            text = (ROOT / rel).read_text("utf-8", errors="ignore")
        except OSError:
            continue
        m = _BRAIN_FORBIDDEN.search(text)
        if m:
            bad.append(f"{rel} ~ '{m.group(0)}'")
    if bad:
        raise RuntimeError(
            "brain bundle guard tripped (Morrow/personal content, refusing to ship): "
            + "; ".join(bad)
        )


_IMPORT_RE = re.compile(
    r"^(?:from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\s+([A-Za-z0-9_,\s]+)"
    r"|import\s+([A-Za-z_][A-Za-z0-9_.]*))",
    re.M,
)


def check_imports_ship(rels: list[str]) -> None:
    """Fail closed: every top-level import in a staged .py that resolves to a
    repo-local module/package must itself be staged. This is what catches a
    server.py refactor (new module, new package) whose files were never added
    to the allow-list — the v0.9.35 ZIP shipped without routers/, security.py,
    models.py, rate_limit.py and usage_store.py and could not boot at all."""
    relset = set(rels)

    def _local_kind(mod: str) -> str | None:
        if (ROOT / f"{mod}.py").is_file():
            return "module"
        if (ROOT / mod / "__init__.py").is_file():
            return "package"
        return None

    def _staged(mod: str) -> bool:
        return f"{mod}.py" in relset or f"{mod}/__init__.py" in relset

    missing: list[str] = []
    for rel in rels:
        if not rel.endswith(".py") or rel.startswith(_BRAIN_DIR + "/"):
            continue
        try:
            text = (ROOT / rel).read_text("utf-8", errors="ignore")
        except OSError:
            continue
        for m in _IMPORT_RE.finditer(text):
            root_mod = (m.group(1) or m.group(3) or "").split(".")[0]
            kind = _local_kind(root_mod)
            if kind and not _staged(root_mod):
                missing.append(f"{rel} imports {root_mod} ({kind} not staged)")
            # `from PKG import a, b` — each name that is a repo file must ship too
            if kind == "package" and m.group(1) and m.group(2):
                for name in (n.strip() for n in m.group(2).split(",")):
                    if name and (ROOT / root_mod / f"{name}.py").is_file() \
                            and f"{root_mod}/{name}.py" not in relset:
                        missing.append(f"{rel} imports {root_mod}.{name} (submodule not staged)")
    if missing:
        raise RuntimeError(
            "release import-guard tripped (staged code imports local files that "
            "would NOT ship — the ZIP could not run): " + "; ".join(sorted(set(missing)))
        )


def _version() -> str:
    # When run as a CLI, sys.path[0] is scripts/, so a bare `import config` misses the
    # repo-root module. Add ROOT to the path first so the version label resolves.
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import config
        return str(config.APP_VERSION)
    except Exception:
        return "0.0.0"


def build_zip(out_dir: Path | str | None = None, version: str | None = None) -> Path:
    """Stage the allow-list, run the deny guard (fail-closed), then write the ZIP."""
    rels = staged_files()
    check_no_excluded(rels)  # abort BEFORE creating any file
    check_brain_clean(rels)  # brain bundle must carry no Morrow/owner content
    check_imports_ship(rels)  # staged code must not import files that don't ship
    version = version or _version()
    out_dir = Path(out_dir) if out_dir else (ROOT / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"adam-local-v{version}.zip"
    # Write files at the ZIP ROOT (no internal wrapper folder). Windows "Extract All"
    # already creates a folder named after the zip, so a root-level layout puts SETUP
    # directly inside the extracted folder instead of one more nested level down.
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in rels:
            z.write(ROOT / rel, rel)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build a secret-free Adam release ZIP.")
    ap.add_argument("--out", default=None, help="output directory (default: ./dist)")
    ap.add_argument("--version", default=None, help="version label (default: config.APP_VERSION)")
    ap.add_argument("--list", action="store_true", help="print the staged file list and exit")
    args = ap.parse_args(argv)

    rels = staged_files()
    try:
        check_no_excluded(rels)
        check_brain_clean(rels)
        check_imports_ship(rels)
    except RuntimeError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 2

    if args.list:
        print(f"{len(rels)} files would ship:")
        for r in rels:
            print(f"  {r}")
        return 0

    out = build_zip(out_dir=args.out, version=args.version)
    print(f"[OK] wrote {out} ({len(rels)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
