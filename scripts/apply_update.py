"""
Jarvis Voice Local — apply a downloaded build over this install, three-way safe.

update.ps1 downloads + extracts the new build, then calls this. It replaces the
old blind robocopy: files the friend never touched are updated (and backed up
first), a friend's own customizations are KEPT, and files both sides changed are
merged (M2) or held for in-app review — never silently overwritten.

Usage:
    python scripts/apply_update.py --new <extracted_build_dir> [--root <install>]
                                   [--merge | --no-merge]

Exit codes:
    0   applied cleanly (no conflicts left for review)
    10  applied, but N file(s) need your review in Jarvis (held conflicts)
    1   hard error (bad arguments, missing build)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Import the INSTALL's own modules (this file lives in <install>/scripts/).
_INSTALL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_INSTALL_ROOT))

import update_engine as ue  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply a Jarvis build over this install.")
    ap.add_argument("--new", required=True, help="extracted new-build directory")
    ap.add_argument("--root", default=str(_INSTALL_ROOT), help="install directory")
    ap.add_argument("--merge", dest="merge", action="store_true", default=True)
    ap.add_argument("--no-merge", dest="merge", action="store_false")
    args = ap.parse_args()

    new_dir = Path(args.new)
    install = Path(args.root)
    if not (new_dir / "server.py").is_file():
        print(f"  The update didn't contain the expected files ({new_dir}).")
        return 1

    res = ue.apply_update(new_dir, install, merge=args.merge)

    updated = len(res.written)
    kept = len(res.kept_local)
    merged = len(res.merged)
    conflicts = len(res.conflicts)

    print(f"  Updated {updated} file(s); kept {kept} of your own change(s)"
          + (f"; auto-merged {merged}" if merged else "")
          + (f"; {conflicts} need your review" if conflicts else "") + ".")
    if res.backups:
        print(f"  ({len(res.backups)} backup(s) saved under data/backups — fully undoable.)")
    if res.kept_local:
        for r in res.kept_local[:10]:
            print(f"    kept your version: {r}")
    if res.conflicts:
        print("  These files were changed by BOTH you and the update — open Jarvis to review:")
        for r in res.conflicts[:20]:
            print(f"    needs review: {r}")
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main())
