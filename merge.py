"""
Adam — Phase 2 three-way merge (the "AI-merge" core).

When BOTH the maintainer and the friend changed the same file, update_engine asks
this module to reconcile them. The deterministic part is a classic line-based
diff3: it combines changes that DON'T overlap automatically (maintainer edited one
region, friend edited another → both kept), and only declares a CONFLICT when the
two sides edited the very same lines differently.

Safety posture: never produce a wrong silent merge. When the two sides touch the
same region, we do NOT guess — the file is left as the friend's, the conflict is
recorded for in-app review, and update_engine reports it. Auto-merge only happens
when it is provably unambiguous.

Pure stdlib (difflib). `handle_conflict` is the hook update_engine calls; it
returns True only when it cleanly auto-merged, False to let the caller hold the
file as a conflict (so nothing is ever lost).
"""

from __future__ import annotations

import json
import time
from difflib import SequenceMatcher
from pathlib import Path

import config
import permissions


def _base_map(base: list[str], other: list[str]) -> dict[int, int]:
    """Map base-line index -> other-line index for every line the two share, using
    difflib's matching blocks (monotonic by construction)."""
    m: dict[int, int] = {}
    for i, j, n in SequenceMatcher(a=base, b=other, autojunk=False).get_matching_blocks():
        for d in range(n):
            m[i + d] = j + d
    return m


def merge3(base: list[str], theirs: list[str], incoming: list[str]) -> tuple[list[str], bool]:
    """Three-way line merge. Returns (merged_lines, had_conflict).

    Anchors are base lines that survive UNCHANGED in both `theirs` and `incoming`;
    between consecutive anchors lies an unstable region that we classify:
        - only theirs changed it      -> take theirs
        - only incoming changed it    -> take incoming
        - both changed it identically -> take that
        - both changed it differently -> CONFLICT (keep theirs, flag it)
    """
    map_t = _base_map(base, theirs)
    map_i = _base_map(base, incoming)

    # Anchor = base line present (unchanged) in BOTH sides. Sentinels bracket the ends.
    anchors: list[tuple[int, int, int]] = [(-1, -1, -1)]
    for b in range(len(base)):
        if b in map_t and b in map_i:
            anchors.append((b, map_t[b], map_i[b]))
    anchors.append((len(base), len(theirs), len(incoming)))

    out: list[str] = []
    conflict = False
    for (pb, pt, pi), (nb, nt, ni) in zip(anchors, anchors[1:]):
        # Unstable region strictly between the two anchors.
        b_seg = base[pb + 1:nb]
        t_seg = theirs[pt + 1:nt]
        i_seg = incoming[pi + 1:ni]

        if t_seg == b_seg:
            out.extend(i_seg)          # only incoming changed (or neither)
        elif i_seg == b_seg:
            out.extend(t_seg)          # only theirs changed
        elif t_seg == i_seg:
            out.extend(t_seg)          # both made the same change
        else:
            conflict = True            # both changed the same region, differently
            out.extend(t_seg)          # keep the friend's version; never lose it

        # Emit the trailing anchor line itself (unless it's the end sentinel).
        if nb < len(base):
            out.append(base[nb])

    return out, conflict


# --- Conflict recording (for in-app review) ---------------------------------

def _conflicts_file() -> Path:
    """Resolved at call time so a runtime/test config change to STATE_DIR is honored."""
    return config.STATE_DIR / "update_conflicts.json"


def _review_dir() -> Path:
    """Where the agent-readable copies of each conflict's versions live. Under data/
    so an update never touches it; added to the agent's READ dirs while conflicts
    pend so 'merge the update conflicts' can read all three versions."""
    return config.DATA_DIR / "update_review"


def _safe_name(rel: str) -> str:
    return rel.replace("/", "__").replace("\\", "__")


def review_paths(rel: str) -> tuple[Path, Path]:
    """(incoming_copy, base_copy) review paths for a conflict. The third version —
    'theirs' — is the live file itself, already readable via the self-edit lane."""
    rd = _review_dir()
    safe = _safe_name(rel)
    return rd / f"{safe}.incoming", rd / f"{safe}.base"


def _record_conflict(rel: str, base_dir: Path, install_dir: Path, new_dir: Path) -> None:
    """Persist a conflict so the app can surface it for agent-assisted resolution.
    Stores the three versions inline AND drops readable copies of the update's
    version + the common ancestor into the review dir for the agent to read."""
    try:
        cf = _conflicts_file()
        cf.parent.mkdir(parents=True, exist_ok=True)
        try:
            items = json.loads(cf.read_text("utf-8"))
            if not isinstance(items, list):
                items = []
        except Exception:
            items = []
        # de-dupe by rel: a fresh update supersedes a stale record for the same file
        items = [it for it in items if it.get("rel") != rel]

        incoming_text = _read(new_dir / rel)
        base_text = _read(base_dir / rel)
        inc_path, base_path = review_paths(rel)
        try:
            inc_path.parent.mkdir(parents=True, exist_ok=True)
            if incoming_text is not None:
                inc_path.write_text(incoming_text, encoding="utf-8")
            if base_text is not None:
                base_path.write_text(base_text, encoding="utf-8")
        except Exception:
            pass

        items.append({
            "rel": rel,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "pending",
            "target": str((install_dir / rel).resolve()),  # the live file ('theirs')
            "incoming_review": str(inc_path),
            "base_review": str(base_path),
            "base": base_text,
            "theirs": _read(install_dir / rel),
            "incoming": incoming_text,
        })
        cf.write_text(json.dumps(items, indent=2), encoding="utf-8")
    except Exception:
        pass  # recording is best-effort; the file is still safely held as theirs


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def list_conflicts() -> list[dict]:
    try:
        items = json.loads(_conflicts_file().read_text("utf-8"))
        return items if isinstance(items, list) else []
    except Exception:
        return []


def resolve_conflict(rel: str) -> bool:
    """Drop a conflict record once it's been resolved in-app, and clean up its review
    copies. Returns True if a record was found and removed."""
    items = list_conflicts()
    kept = [it for it in items if it.get("rel") != rel]
    if len(kept) == len(items):
        return False
    for p in review_paths(rel):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        _conflicts_file().write_text(json.dumps(kept, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def resolve_for_target(target_path: str | Path) -> str | None:
    """Clear the pending conflict whose live file is `target_path` (the path a just-
    applied proposed change wrote to). Returns the resolved rel, or None if the path
    isn't a pending conflict. Lets the apply flow auto-clear a conflict once the
    agent's merge is approved."""
    try:
        tp = Path(target_path).resolve()
    except Exception:
        return None
    for c in list_conflicts():
        rec_t = c.get("target")
        if not rec_t:
            continue
        try:
            if Path(rec_t).resolve() == tp:
                rel = c.get("rel")
                if rel and resolve_conflict(rel):
                    return rel
        except Exception:
            continue
    return None


# --- Hook called by update_engine -------------------------------------------

def handle_conflict(v, new_dir: Path, install_dir: Path, baseline_dir: Path, result) -> bool:
    """Try to auto-merge a both-changed file. Returns True only on a clean merge
    (file written, recorded in result.merged); False to let update_engine hold the
    file as theirs and report it (after we record the conflict for review)."""
    base = _read(baseline_dir / v.rel)
    theirs = _read(install_dir / v.rel)
    incoming = _read(new_dir / v.rel)
    if base is None or theirs is None or incoming is None:
        return False  # can't 3-way without all three; hold it

    merged, had_conflict = merge3(base.splitlines(keepends=True),
                                  theirs.splitlines(keepends=True),
                                  incoming.splitlines(keepends=True))
    if had_conflict:
        _record_conflict(v.rel, baseline_dir, install_dir, new_dir)
        return False  # held by caller, but now recorded for in-app resolution

    merged_text = "".join(merged)
    # A textually-clean diff3 merge can still be syntactically broken Python —
    # and once written, the baseline advances and the breakage would be
    # classified as the friend's own edit and preserved forever. Refuse: hold it
    # as a normal conflict for in-app review instead.
    if v.rel.endswith(".py"):
        try:
            compile(merged_text, v.rel, "exec")
        except SyntaxError:
            _record_conflict(v.rel, baseline_dir, install_dir, new_dir)
            return False

    dst = install_dir / v.rel
    backup_root = getattr(result, "backup_root", None)
    if backup_root:
        import update_engine as _ue
        _ue.update_backup(dst, install_dir, Path(backup_root), result)
    else:
        b = permissions.make_backup_before_write(dst)
        if b:
            result.backups.append(str(b))
    from update_engine import atomic_write_bytes
    atomic_write_bytes(dst, merged_text.encode("utf-8"))
    result.written.append(v.rel)
    result.merged.append(v.rel)
    return True
