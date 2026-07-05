"""
Adam — Phase 2 smart-update engine.

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
import os
import shutil
import time
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
# .jvltmp = our own in-flight atomic-write temp; .removed = a retired shipped file.
_EXCLUDE_SUFFIXES = (".pyc", ".jvltmp", ".removed")


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
    retired: list[str] = field(default_factory=list)      # rels renamed *.removed
    backup_root: str | None = None                        # this run's backup folder


def atomic_write_bytes(dst: Path, data: bytes) -> None:
    """Write `data` to `dst` atomically: full write to a sibling temp file, then
    os.replace (atomic on NTFS). A crash mid-write leaves the old file intact —
    never a truncated server.py the next classify would mistake for a user edit
    and preserve forever."""
    tmp = dst.with_name(dst.name + ".jvltmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dst)


def _atomic_copy(src: Path, dst: Path) -> None:
    atomic_write_bytes(dst, Path(src).read_bytes())
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass  # mtime is cosmetic; the bytes are what matter


def update_backup(dst: Path, install_dir: Path, backup_root: Path,
                  result: ApplyResult) -> None:
    """Back `dst` up into THIS update run's own folder, preserving the relative
    tree. Unlike the shared pruned pool (make_backup_before_write), a per-run
    folder can't collide on basenames (two requirements.txt) and can't have its
    own entries pruned away mid-run by PERM_BACKUP_KEEP — so 'the update is
    undoable' stays true for a 200-file release."""
    try:
        rel = dst.resolve().relative_to(Path(install_dir).resolve())
    except ValueError:
        rel = Path(dst.name)
    bdst = backup_root / rel
    bdst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dst, bdst)
    result.backups.append(str(bdst))


def _prune_update_backup_dirs(keep: int = 3) -> None:
    """Keep only the newest `keep` update-run backup folders (each is a full
    program-file snapshot; unbounded they'd eat the disk update by update)."""
    try:
        runs = sorted(
            (p for p in config.BACKUP_DIR.glob("update-*") if p.is_dir()),
            key=lambda p: p.name,
        )
        for p in runs[:-keep] if keep > 0 else runs:
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _copy_over(src: Path, dst: Path, result: ApplyResult, *, backup: bool,
               install_dir: Path | None = None, backup_root: Path | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if backup and dst.exists():
        if backup_root is not None and install_dir is not None:
            update_backup(dst, install_dir, backup_root, result)
        else:
            b = permissions.make_backup_before_write(dst)
            if b:
                result.backups.append(str(b))
    _atomic_copy(src, dst)


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


class UpdateInProgress(RuntimeError):
    """Another apply is already running against this install."""


_LOCK_STALE_SECONDS = 3600


def _acquire_apply_lock(install_dir: Path) -> Path:
    """One apply at a time: /update/apply racing a double-clicked UPDATE.cmd can
    interleave copies and corrupt the baseline for every future update. The lock
    is a state file created exclusively; a crash's leftover goes stale after an
    hour so a failed run can't wedge updates forever."""
    lock = Path(config.STATE_DIR) / "update_apply.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock
    except FileExistsError:
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = 0.0
        if age > _LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
            return _acquire_apply_lock(install_dir)
        raise UpdateInProgress(
            "An update is already being applied to this install. Wait for it to "
            "finish (or, if it crashed, try again in an hour)."
        )


def _check_free_disk(new_dir: Path, install_dir: Path) -> None:
    """Refuse to start an apply the disk can't finish: a mid-run ENOSPC leaves a
    half-old/half-new tree. Needs ~2x the new build (copies + per-run backups)."""
    try:
        need = sum(p.stat().st_size for p in new_dir.rglob("*") if p.is_file()) * 2
        free = shutil.disk_usage(install_dir).free
    except OSError:
        return  # can't measure — don't block the update over the probe itself
    if free < need + 50 * 1024 * 1024:  # +50MB headroom
        raise RuntimeError(
            f"Not enough free disk space to update safely (need ~{need // (1024*1024)} MB "
            f"plus headroom, have {free // (1024*1024)} MB free). Free some space and retry."
        )


def _retire_removed_files(new_dir: Path, install_dir: Path, baseline_dir: Path,
                          result: ApplyResult) -> None:
    """Handle files the new version REMOVED. Three-way rule, mirror of classify:
    a file that exists in the old baseline but not the new build was dropped by
    the maintainer — if the friend never touched it (theirs == baseline), rename
    it to *.removed so a stale module can't shadow its replacement (the 0.9.36
    routers/ restructure would have left a stale top-level module importable
    forever). If the friend DID change it, keep it untouched and report it. Runs
    BEFORE the baseline advances; never deletes bytes, only renames."""
    if not (baseline_dir.is_dir() and any(baseline_dir.rglob("*"))):
        return
    new_rels = set(iter_program_files(new_dir))
    for rel in iter_program_files(baseline_dir):
        if rel in new_rels:
            continue
        live = install_dir / rel
        if not live.is_file():
            continue  # already gone
        if sha256_file(live) == sha256_file(baseline_dir / rel):
            try:
                os.replace(live, live.with_name(live.name + ".removed"))
                result.retired.append(rel)
            except OSError:
                result.kept_local.append(rel)
        else:
            # The friend modified a file the new version dropped — never touch it.
            result.kept_local.append(rel)


def apply_update(new_dir: str | Path, install_dir: str | Path,
                 baseline_dir: str | Path | None = None, *,
                 merge: bool = True) -> ApplyResult:
    """Apply a new build over the install, three-way safe.

    `merge=True` (M2) attempts an automatic 3-way merge of conflicting files and
    records anything it can't merge cleanly as a proposed change for in-app review.
    `merge=False` (M1) simply holds every conflict as theirs. Either way, nothing a
    friend changed is ever silently lost: overwrites are backed up (into this run's
    own un-pruned backup folder), `keep_local` files are untouched, and conflicts
    are surfaced. Every write is atomic (temp + os.replace), the whole run holds a
    lock so two applies can't interleave, and files the new version removed are
    retired to *.removed (never deleted) when the friend hadn't modified them.
    """
    if baseline_dir is None:
        baseline_dir = BASELINE_DIR          # resolved at call time, not import time
    new_dir, install_dir, baseline_dir = Path(new_dir), Path(install_dir), Path(baseline_dir)
    lock = _acquire_apply_lock(install_dir)
    try:
        _check_free_disk(new_dir, install_dir)
        verdicts = classify(new_dir, install_dir, baseline_dir)
        result = ApplyResult()
        backup_root = config.BACKUP_DIR / f"update-{time.strftime('%Y%m%d_%H%M%S')}"
        result.backup_root = str(backup_root)

        for v in verdicts:
            result.counts[v.category] = result.counts.get(v.category, 0) + 1
            src = new_dir / v.rel
            dst = install_dir / v.rel

            if v.category in ("new_file",):
                _copy_over(src, dst, result, backup=False)
                result.written.append(v.rel)
            elif v.category in ("take_incoming", "no_baseline"):
                _copy_over(src, dst, result, backup=True,
                           install_dir=install_dir, backup_root=backup_root)
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

        # Files the new version dropped: retire (rename), never delete.
        _retire_removed_files(new_dir, install_dir, baseline_dir, result)

        # The baseline advances to what we just shipped, so the NEXT update three-ways
        # against this version. (Held conflicts are resolved by the friend in-app; once
        # resolved their file matches and won't re-conflict.)
        snapshot_baseline(new_dir, baseline_dir)
        _prune_update_backup_dirs()
        return result
    finally:
        lock.unlink(missing_ok=True)


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
