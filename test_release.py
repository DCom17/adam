"""
Jarvis Voice Local — release packaging tests (v0.9.0, Slice 3).

Proves the allow-list release builder is secret-safe:
  * the staged set INCLUDES the core product files, templates, user-facing docs, and
    the test suites (F&F beta self-verification);
  * it EXCLUDES .env, settings.json, runtime data, logs, backups (*.bak / *.pre-*),
    __pycache__/*.pyc, and internal process docs;
  * the fail-closed deny guard raises if a secret/runtime/backup path is staged;
  * an actually-built ZIP contains none of the excluded categories.

The exclusion checks are meaningful: the live repo really does contain a .env and
*.bak rollback files at root, and the builder must drop them.

Run:  python test_release.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_release as mr  # noqa: E402

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


def _excluded_member(rel: str) -> bool:
    """True if a packaged path falls in a category that must NEVER ship."""
    base = rel.split("/")[-1]
    if rel in ("data/.gitkeep",):
        return False
    if rel in (".env", "settings.json"):
        return True
    if base.endswith(".bak") or "pre-" in base:
        return True
    if base.endswith(".pyc") or "__pycache__" in rel:
        return True
    if base.endswith(".pem"):
        return True
    if rel.startswith("data/"):
        return True
    if rel.startswith("docs/") and base in (
        "DECISION_LOG.md", "ROADMAP.md", "PHASE_HISTORY.md", "NEXT_PHASE_PROMPT.md",
        "PARKING_LOT.md", "PERSONAL_FIX_PORTING_LOG.md",
    ):
        return True
    if rel.startswith("docs/DEVICE_ACCEPTANCE_RUN"):
        return True
    return False


def main() -> int:
    rels = mr.staged_files()
    relset = set(rels)

    print("\n[1] Staged set INCLUDES the core product files")
    for f in ("server.py", "config.py", "permissions.py", "proposed_changes.py",
              "approvals.py", "diffs.py", "job_store.py", "onboarding.py",
              "requirements.txt"):
        check(f"includes {f}", f in relset)
    check("includes .env.example", ".env.example" in relset)
    check("includes settings.example.json", "settings.example.json" in relset)
    check("includes README.md", "README.md" in relset)
    check("includes CHANGELOG.md", "CHANGELOG.md" in relset)
    check("includes LICENSE", "LICENSE" in relset)
    check("includes .gitignore", ".gitignore" in relset)

    print("\n[2] Staged set INCLUDES web + scripts + user-facing docs + tests")
    for f in ("web/index.html", "web/console.html", "web/sw.js", "web/manifest.json",
              "web/icon.png"):
        check(f"includes {f}", f in relset)
    for f in ("scripts/setup.py", "scripts/doctor.py", "scripts/make_release.py",
              "scripts/make_release.ps1", "scripts/start-jarvis.ps1",
              "scripts/connect-phone.py", "scripts/connect-phone.ps1",
              "scripts/copy-token.ps1"):
        check(f"includes {f}", f in relset)
    for f in ("docs/BETA_HANDOFF.md", "docs/CONNECT_YOUR_PHONE.md",
              "docs/ADVANCED_REMOTE.md", "docs/SUPPORT.md", "docs/RELEASE.md",
              "docs/CONSUMER_TEST_CHECKLIST.md"):
        check(f"includes {f}", f in relset)
    check("includes data/.gitkeep", "data/.gitkeep" in relset)
    check("ships the test suites", any(r.startswith("test_") and r.endswith(".py") for r in rels))

    print("\n[3] Staged set EXCLUDES secrets / runtime / backups / internal docs")
    check("excludes .env", ".env" not in relset)
    check("excludes settings.json", "settings.json" not in relset)
    check("no *.bak staged", not any(r.endswith(".bak") for r in rels))
    check("no *.pre-* staged", not any("pre-" in r.split("/")[-1] for r in rels))
    check("no __pycache__/*.pyc staged",
          not any("__pycache__" in r or r.endswith(".pyc") for r in rels))
    check("no runtime data staged (except .gitkeep)",
          not any(r.startswith("data/") and r != "data/.gitkeep" for r in rels))
    check("no *.pem staged", not any(r.endswith(".pem") for r in rels))
    for d in ("docs/DECISION_LOG.md", "docs/ROADMAP.md", "docs/PHASE_HISTORY.md",
              "docs/NEXT_PHASE_PROMPT.md", "docs/PARKING_LOT.md",
              "docs/PERSONAL_FIX_PORTING_LOG.md"):
        check(f"excludes internal doc {d}", d not in relset)
    check("excludes DEVICE_ACCEPTANCE_RUN docs",
          not any(r.startswith("docs/DEVICE_ACCEPTANCE_RUN") for r in rels))

    print("\n[4] Exclusion is meaningful (repo really contains these)")
    check("repo has a real .env to exclude", (ROOT / ".env").exists())
    check("repo has real backup files to exclude",
          any(ROOT.glob("settings.json.bak.*")) or any(ROOT.rglob("*.bak")))

    print("\n[5] Deny guard fails closed on injected secret/runtime/backup paths")
    try:
        mr.check_no_excluded(rels)
        check("clean staged set passes the guard", True)
    except RuntimeError:
        check("clean staged set passes the guard", False)

    def _guard_raises(extra: str) -> bool:
        try:
            mr.check_no_excluded(rels + [extra])
            return False
        except RuntimeError:
            return True

    for bad in (".env", "settings.json", "server.py.pre-v0.9.bak",
                "data/state/jarvis.db", "scripts/__pycache__/x.pyc",
                "vapid_private.pem", "config.py.pre-v0.8.2-bump.bak"):
        check(f"guard raises on {bad!r}", _guard_raises(bad))
    # And it must NOT trip on the legitimately-shippable exceptions.
    check("guard allows .env.example", not _guard_raises(".env.example"))
    check("guard allows data/.gitkeep", not _guard_raises("data/.gitkeep"))

    print("\n[6] An actually-built ZIP is secret-free and complete")
    with tempfile.TemporaryDirectory(prefix="jvl_rel_test_") as td:
        zpath = mr.build_zip(out_dir=td, version="test")
        check("zip was written", zpath.exists())
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        # Files live at the ZIP ROOT (no internal wrapper folder) so Windows "Extract All"
        # lands SETUP directly in the extracted folder, not one level deeper.
        members = [n for n in names if not n.endswith("/")]
        check("zip has NO internal wrapper folder (files at root)",
              not any(n.startswith("jarvis-voice-local-v") for n in names))
        check("consumer entry points sit at the zip root",
              "SETUP.cmd" in members and "START_HERE.txt" in members)
        offenders = [m for m in members if _excluded_member(m)]
        check("zip contains NO excluded files", not offenders)
        if offenders:
            print("     offenders:", offenders[:10])
        for must in ("server.py", ".env.example", "settings.example.json",
                     "web/index.html", "web/console.html", "scripts/setup.py",
                     "scripts/copy-token.ps1",
                     "docs/BETA_HANDOFF.md", "docs/CONNECT_YOUR_PHONE.md",
                     "docs/SUPPORT.md"):
            check(f"zip includes {must}", must in members)

    print("\n[7] CLI default version label resolves correctly (run as a script)")
    # Invoke as a real CLI: `python scripts/make_release.py` puts scripts/ on sys.path,
    # which is the exact condition that used to fall back to v0.0.0.
    import subprocess
    import config as _cfg
    expected = str(_cfg.APP_VERSION)
    check("APP_VERSION is not the 0.0.0 fallback", expected != "0.0.0")
    with tempfile.TemporaryDirectory(prefix="jvl_rel_cli_") as td:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_release.py"),
                            "--out", td], cwd=str(ROOT), capture_output=True, text=True)
        check("CLI build exits 0", r.returncode == 0)
        produced = list(Path(td).glob("*.zip"))
        check("CLI produced exactly one zip", len(produced) == 1)
        if produced:
            name = produced[0].name
            check(f"default zip name carries v{expected} (not v0.0.0)",
                  name == f"jarvis-voice-local-v{expected}.zip")
            with zipfile.ZipFile(produced[0]) as z:
                cli_members = [n for n in z.namelist() if not n.endswith("/")]
            check("CLI-built zip is still secret-free",
                  not any(_excluded_member(m) for m in cli_members))

    print("\n[8] --version override still wins over the detected version")
    with tempfile.TemporaryDirectory(prefix="jvl_rel_ver_") as td:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_release.py"),
                            "--version", "9.9.9-test", "--out", td],
                           cwd=str(ROOT), capture_output=True, text=True)
        check("override build exits 0", r.returncode == 0)
        produced = list(Path(td).glob("*.zip"))
        check("override zip name uses the supplied version",
              len(produced) == 1 and produced[0].name == "jarvis-voice-local-v9.9.9-test.zip")

    print("\n[9] Brain bundle ships and is content-guarded")
    brain_rels = [r for r in rels if r.startswith("brain/")]
    check("brain/ is bundled into the release", len(brain_rels) > 0)
    check("brain includes the operating bootstrap (CLAUDE.md)", "brain/CLAUDE.md" in relset)
    check("brain includes the onboarding (BOOTSTRAP.md)", "brain/BOOTSTRAP.md" in relset)
    try:
        mr.check_brain_clean(rels)
        check("clean brain passes the content guard", True)
    except RuntimeError:
        check("clean brain passes the content guard", False)
    check("no real brain .env shipped", "brain/.env" not in relset)
    check("no real brain config.json shipped",
          not any(r.startswith("brain/") and r.endswith("config/jarvis.config.json") for r in rels))
    _blic = ROOT / "brain" / "LICENSE"
    check("brain LICENSE carries no forbidden content",
          (not mr._BRAIN_FORBIDDEN.search(_blic.read_text("utf-8", errors="ignore")))
          if _blic.exists() else True)

    print("\n[9b] Brain content-guard regex behaves")
    check("regex flags Morrow infra path", bool(mr._BRAIN_FORBIDDEN.search("see 15_morrow/captures")))
    check("regex flags morrow_distilled", bool(mr._BRAIN_FORBIDDEN.search("morrow_distilled.md")))
    check("regex does NOT flag the word 'tomorrow'", not mr._BRAIN_FORBIDDEN.search("See you tomorrow"))
    # Owner-identifying terms come from scripts/release_guard.local (gitignored).
    # On the owner's machine every listed term must be flagged; on a user install
    # the file is absent and the base patterns alone apply.
    if mr._GUARD_LOCAL.is_file():
        terms = [t.strip() for t in mr._GUARD_LOCAL.read_text("utf-8").splitlines()
                 if t.strip() and not t.strip().startswith("#")]
        check("release_guard.local has at least one term", len(terms) > 0)
        for t in terms:
            check(f"regex flags local guard term #{terms.index(t) + 1}",
                  bool(mr._BRAIN_FORBIDDEN.search(f"padding {t} padding")))
        check("release_guard.local itself never ships",
              "scripts/release_guard.local" not in relset
              and _guard_raises("scripts/release_guard.local"))

    print("\n[9c] Brain guard fails closed on a planted violation")
    planted = ROOT / "brain" / "_guard_probe_tmp.md"
    try:
        planted.write_text("leak test: see 15_morrow/captures\n", encoding="utf-8")
        raised = False
        try:
            mr.check_brain_clean(["brain/_guard_probe_tmp.md"])
        except RuntimeError:
            raised = True
        check("guard raises when a brain file carries owner content", raised)
    finally:
        if planted.exists():
            planted.unlink()

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
