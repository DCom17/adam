"""
Jarvis Voice Local — Phase 2 smart-update engine.

The old update path (scripts/update.ps1) blind-robocopied the maintainer's new
build over the install and did NOT back anything up. That was safe only while no
install had local changes. Once self-edit (Phase 1) lets a friend customize their
OWN copy, a blind overwrite silently destroys those edits. This module fixes that.

It does a per-file THREE-WAY comparison using a pristine ancestor we keep under
`data/baseline/` (the exact bytes the friend last installed/updated — never the
friend's possibly-edited working copy):

    baseline  (O) = pristine last-shipped version  (data/baseline/<rel>)
    theirs    (A) = the friend's current file       (<install>/<rel>)
    incoming  (B) = the maintainer's new version     (<newbuild>/<rel>)

Per file the verdict is:

    new_file        in the new build, not installed yet            -> create
    unchanged       nobody changed it                              -> noop
    take_incoming   friend didn't touch it, maintainer changed it  -> overwrite (backup first)
    keep_local      maintainer didn't change it, friend did        -> keep theirs
    already_matches friend + maintainer ended on the same bytes    -> noop
    conflict        BOTH changed it, differently                   -> 3-way merge / hold
    no_baseline     no ancestor recorded yet (first run / new file)-> overwrite (backup first)

Only `take_incoming`, `new_file`, `no_baseline`, and cleanly-merged conflicts ever
write to the install, and every overwrite is backed up first via the same
`permissions.make_backup_before_write` the approve flow uses. `keep_local` is the
whole point: your bug fix and the friend's customization coexist.

Conflicts (both sides changed the same file) are handled by `merge.py` in the M2
layer; here in M1 they are simply HELD (left as theirs) and reported, so nothing
is ever lost. Pure-stdlib except for `config`/`permissions` (project modules).
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import config
import permissions

# The pristine ancestor lives under data/ so it survives an update (update.ps1
# never touches data/). It is refreshed to the new build after every apply.
BASELINE_DIR = config.DATA_DIR / "baseline"

# Files / dirs the updater must NEVER manage: the friend's secrets, their settings,
# and the entire runtime data tree (which includes baseline/, backups/, state/).
# These mirror the robocopy exclusions in the old update.ps1.
_EXCLUDE_NAMES = {".env", "settings.json"}
_EXCLUDE_DIRS = {"data", "__pycache__", ".git"}
# Rollback/backup artifacts that may exist in an install but are never "the program".
_EXCLUDE_SUFFIXES = (".pyc",)


def _is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0] in _EXCLUDE_DIRS:
        return True
    if parts[-1] in _EXCLUDE_NAMES:
        return True
    if rel.endswith(_EXCLUDE_SUFFIXES):
        return True
    # *.bak / *.pre-* rollback files are not shipped program files.
    base = parts[-1]
    if base.endswith(".bak") or ".pre-" in base:
        return True
    return False


def iter_program_files(root: str | Path) -> list[str]:
    """Posix rel paths of the manageable program files under `root`, sorted."""
    root = Path(root)
    out: list[str] = []
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _is_excluded(rel):
            continue
        out.append(rel)
    return sorted(out)


def sha256_file(path: str | Path) -> str | None:
    """SHA-256 of a file's raw bytes, or None if it doesn't exist / can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


@dataclass
class FileVerdict:
    rel: str
    category: str          # new_file | unchanged | take_incoming | keep_local |
                           # already_matches | conflict | no_baseline
    h_baseline: str | None = None
    h_theirs: str | None = None
    h_incoming: str | None = None


def _classify_one(rel: str, new_dir: Path, install_dir: Path, baseline_dir: Path,
                  have_baseline_tree: bool) -> FileVerdict:
    h_inc = sha256_file(new_dir / rel)
    h_the = sha256_file(install_dir / rel)
    h_base = sha256_file(baseline_dir / rel) if have_baseline_tree else None

    if h_the is None:
        # Not installed yet -> brand new file from the new build.
        return FileVerdict(rel, "new_file", h_base, h_the, h_inc)

    if h_the == h_inc:
        # Friend's copy already equals the incoming bytes (incl. unchanged files).
        cat = "unchanged" if (h_base is None or h_base == h_inc) else "already_matches"
        return FileVerdict(rel, cat, h_base, h_the, h_inc)

    if not have_baseline_tree or h_base is None:
        # No ancestor recorded for this file -> we can't tell who changed what.
        # Safe play: take the maintainer's version but back the friend's up first.
        return FileVerdict(rel, "no_baseline", h_base, h_the, h_inc)

    the_changed = h_the != h_base
    inc_changed = h_inc != h_base

    if not the_changed and inc_changed:
        return FileVerdict(rel, "take_incoming", h_base, h_the, h_inc)
    if the_changed and not inc_changed:
        return FileVerdict(rel, "keep_local", h_base, h_the, h_inc)
    if not the_changed and not inc_changed:
        return FileVerdict(rel, "unchanged", h_base, h_the, h_inc)
    # Both changed, and (h_the != h_inc from above) -> genuine conflict.
    return FileVerdict(rel, "conflict", h_base, h_the, h_inc)


def classify(new_dir: str | Path, install_dir: str | Path,
             baseline_dir: str | Path | None = None) -> list[FileVerdict]:
    """Classify every program file in the new build against the install + baseline.

    Deletions (a file in the install but not the new build) are intentionally NOT
    handled — the updater never deletes, so a friend's extra files are always safe.
    """
    if baseline_dir is None:
        baseline_dir = BASELINE_DIR          # resolved at call time, not import time
    new_dir, install_dir, baseline_dir = Path(new_dir), Path(install_dir), Path(baseline_dir)
    have_baseline = baseline_dir.is_dir() and any(baseline_dir.rglob("*"))
    verdicts: list[FileVerdict] = []
    for rel in iter_program_files(new_dir):
        verdicts.append(
            _classify_one(rel, new_dir, install_dir, baseline_dir, have_baseline)
        )
    return verdicts


@dataclass
class ApplyResult:
    counts: dict[str, int] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)      # rels overwritten/created
    kept_local: list[str] = field(default_factory=list)   # rels left as theirs
    conflicts: list[str] = field(default_factory=list)    # rels needing review
    backups: list[str] = field(default_factory=list)      # backup paths made
    merged: list[str] = field(default_factory=list)       # rels auto-merged (M2)


def _copy_over(src: Path, dst: Path, result: ApplyResult, *, backup: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if backup and dst.exists():
        b = permissions.make_backup_before_write(dst)
        if b:
            result.backups.append(str(b))
    shutil.copy2(src, dst)


def snapshot_baseline(src_dir: str | Path, baseline_dir: str | Path | None = None) -> int:
    """Record `src_dir`'s program files as the pristine ancestor for next time.

    Called with the NEW build after a successful apply (so the baseline advances to
    what was just shipped) and at first run to seed an install. Replaces the prior
    baseline wholesale. Returns the number of files snapshotted.
    """
    if baseline_dir is None:
        baseline_dir = BASELINE_DIR          # resolved at call time, not import time
    src_dir, baseline_dir = Path(src_dir), Path(baseline_dir)
    if baseline_dir.exists():
        shutil.rmtree(baseline_dir, ignore_errors=True)
    baseline_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for rel in iter_program_files(src_dir):
        dst = baseline_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_dir / rel, dst)
        n += 1
    return n


def apply_update(new_dir: str | Path, install_dir: str | Path,
                 baseline_dir: str | Path | None = None, *,
                 merge: bool = True) -> ApplyResult:
    """Apply a new build over the install, three-way safe.

    `merge=True` (M2) attempts an automatic 3-way merge of conflicting files and
    records anything it can't merge cleanly as a proposed change for in-app review.
    `merge=False` (M1) simply holds every conflict as theirs. Either way, nothing a
    friend changed is ever silently lost: overwrites are backed up, `keep_local`
    files are untouched, and conflicts are surfaced.
    """
    if baseline_dir is None:
        baseline_dir = BASELINE_DIR          # resolved at call time, not import time
    new_dir, install_dir, baseline_dir = Path(new_dir), Path(install_dir), Path(baseline_dir)
    verdicts = classify(new_dir, install_dir, baseline_dir)
    result = ApplyResult()

    for v in verdicts:
        result.counts[v.category] = result.counts.get(v.category, 0) + 1
        src = new_dir / v.rel
        dst = install_dir / v.rel

        if v.category in ("new_file",):
            _copy_over(src, dst, result, backup=False)
            result.written.append(v.rel)
        elif v.category in ("take_incoming", "no_baseline"):
            _copy_over(src, dst, result, backup=True)
            result.written.append(v.rel)
        elif v.category in ("keep_local",):
            result.kept_local.append(v.rel)
        elif v.category in ("unchanged", "already_matches"):
            pass
        elif v.category == "conflict":
            handled = False
            if merge:
                handled = _try_merge_conflict(v, new_dir, install_dir, baseline_dir, result)
            if not handled:
                result.conflicts.append(v.rel)

    # The baseline advances to what we just shipped, so the NEXT update three-ways
    # against this version. (Held conflicts are resolved by the friend in-app; once
    # resolved their file matches and won't re-conflict.)
    snapshot_baseline(new_dir, baseline_dir)
    return result


def _try_merge_conflict(v: FileVerdict, new_dir: Path, install_dir: Path,
                        baseline_dir: Path, result: ApplyResult) -> bool:
    """M2 hook: attempt a clean 3-way merge or record a proposed change. Imported
    lazily so M1 never depends on the merge layer. Returns True if fully handled
    (auto-merged or recorded for review), False to fall back to holding it."""
    try:
        import merge as _merge  # M2 module
    except Exception:
        return False
    return _merge.handle_conflict(v, new_dir, install_dir, baseline_dir, result)
