"""
Jarvis Voice Local — Phase 2 three-way merge tests (M2 / the AI-merge core).

Proves diff3 combines non-overlapping changes automatically and refuses to guess
when both sides edit the same lines:
  * non-overlapping edits      -> clean auto-merge, BOTH changes present, no conflict
  * one-sided edits            -> take that side
  * identical edits both sides -> taken once, no conflict
  * overlapping different edits -> CONFLICT flagged, friend's version kept
And the integration through update_engine.apply_update(merge=True):
  * a both-changed-but-disjoint file is auto-merged and written
  * a both-changed-overlapping file is held as theirs AND recorded for in-app review

Self-contained: redirects config.BACKUP_DIR + config.STATE_DIR at a temp sandbox.
Run:  python test_merge.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
import merge
import update_engine as ue

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


def L(s: str) -> list[str]:
    return s.splitlines(keepends=True)


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def main() -> int:
    print("[diff3] unit")
    # Non-overlapping: theirs changes line 1, incoming changes line 5.
    base = "a\nb\nc\nd\ne\n"
    theirs = "AAA\nb\nc\nd\ne\n"
    incoming = "a\nb\nc\nd\nEEE\n"
    merged, conflict = merge.merge3(L(base), L(theirs), L(incoming))
    check("disjoint edits merge without conflict", not conflict)
    check("disjoint merge keeps theirs' change", "AAA\n" in merged)
    check("disjoint merge keeps incoming's change", "EEE\n" in merged)

    # Only theirs changed.
    merged, conflict = merge.merge3(L(base), L("a\nB2\nc\nd\ne\n"), L(base))
    check("only-theirs change taken, no conflict", not conflict and "B2\n" in merged)

    # Only incoming changed.
    merged, conflict = merge.merge3(L(base), L(base), L("a\nb\nC2\nd\ne\n"))
    check("only-incoming change taken, no conflict", not conflict and "C2\n" in merged)

    # Identical change on both sides.
    same = "a\nXX\nc\nd\ne\n"
    merged, conflict = merge.merge3(L(base), L(same), L(same))
    check("identical edits -> no conflict", not conflict)
    check("identical edits -> change present once", merged.count("XX\n") == 1)

    # Overlapping different edits on the SAME line.
    merged, conflict = merge.merge3(L(base), L("a\nTHEIRS\nc\nd\ne\n"),
                                    L("a\nINCOMING\nc\nd\ne\n"))
    check("overlapping different edits -> CONFLICT", conflict)
    check("conflict keeps friend's (theirs) version", "THEIRS\n" in merged)

    print("\n[integration] apply_update(merge=True)")
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_merge_test_"))
    baseline = sandbox / "baseline"; install = sandbox / "install"; new = sandbox / "new"
    backups = sandbox / "backups"; state = sandbox / "state"
    for d in (baseline, install, new, backups, state):
        d.mkdir(parents=True, exist_ok=True)
    config.BACKUP_DIR = backups
    config.STATE_DIR = state
    config.DATA_DIR = sandbox          # update_review/ lands here, not real data/
    config.PERM_BACKUP_BEFORE_WRITE = True
    config.PERM_MAX_FILE_SIZE_MB = 25

    # auto.py: both changed, DISJOINT regions -> should auto-merge.
    write(baseline / "auto.py", "a\nb\nc\nd\ne\n")
    write(install / "auto.py", "AAA\nb\nc\nd\ne\n")     # friend changed top
    write(new / "auto.py", "a\nb\nc\nd\nEEE\n")          # maintainer changed bottom
    # clash.py: both changed the SAME line -> should conflict + record.
    write(baseline / "clash.py", "x\ny\nz\n")
    write(install / "clash.py", "x\nFRIEND\nz\n")
    write(new / "clash.py", "x\nMAINT\nz\n")

    res = ue.apply_update(new, install, baseline, merge=True)

    check("auto.py auto-merged (in result.merged)", "auto.py" in res.merged)
    check("auto.py kept friend's top change", "AAA\n" in read(install / "auto.py"))
    check("auto.py kept maintainer's bottom change", "EEE\n" in read(install / "auto.py"))
    check("auto.py backed up before merge write",
          any(Path(b).name.endswith("auto.py") for b in res.backups))

    check("clash.py NOT auto-merged", "clash.py" not in res.merged)
    check("clash.py held as conflict", "clash.py" in res.conflicts)
    check("clash.py left as the friend's version", read(install / "clash.py") == "x\nFRIEND\nz\n")
    recs = merge.list_conflicts()
    rec = next((r for r in recs if r["rel"] == "clash.py"), None)
    check("clash.py recorded for in-app review", rec is not None)
    check("conflict record carries all three versions",
          rec is not None and rec["base"] and rec["theirs"] and rec["incoming"])
    # review copies materialized for the agent to read (incoming + ancestor)
    inc_review, base_review = merge.review_paths("clash.py")
    check("update's version written to review dir", inc_review.is_file() and read(inc_review) == "x\nMAINT\nz\n")
    check("ancestor written to review dir", base_review.is_file() and read(base_review) == "x\ny\nz\n")
    check("record points at the live target + review copies",
          rec["target"].endswith("clash.py") and rec["incoming_review"] and rec["base_review"])

    # resolve-on-apply: clearing by the applied target path drops the record + review copies
    cleared = merge.resolve_for_target(rec["target"])
    check("resolve_for_target clears by live path", cleared == "clash.py")
    check("conflict gone after resolve", not any(r["rel"] == "clash.py" for r in merge.list_conflicts()))
    check("review copies cleaned up", not inc_review.exists() and not base_review.exists())
    check("resolve_for_target on a non-conflict path is a no-op",
          merge.resolve_for_target(str(install / "auto.py")) is None)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
