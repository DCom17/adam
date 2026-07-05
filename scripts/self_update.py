"""
Adam — UPDATE.cmd's worker. Resolves the latest GitHub release,
and if it's newer, downloads + applies it with the smart 3-way updater. The
in-app "Update now" button uses the same updater module, so both paths behave
identically.

Exit codes:  0 = up to date or updated cleanly · 10 = updated, conflicts need
review in-app · 1 = couldn't reach the update server / apply failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config       # noqa: E402
import updater      # noqa: E402


def main() -> int:
    info = updater.check_for_update(use_cache=False)
    if not info.get("latest") and info.get("error"):
        print(f"  Couldn't reach the update server: {info['error']}")
        print("  Check your internet connection and try again.")
        return 1
    if not info.get("available"):
        print(f"  You're already up to date (v{config.APP_VERSION}).")
        return 0

    print(f"  Updating to v{info['latest']} (you have v{config.APP_VERSION})...")
    res = updater.download_and_apply(info["url"])
    if not res.get("ok"):
        print(f"  The update couldn't be applied: {res.get('error')}")
        print("  Your files were not changed.")
        return 1

    print(f"  Updated {res.get('updated', 0)} file(s); kept {res.get('kept_local', 0)} "
          f"of your own change(s).")
    if res.get("backups"):
        print(f"  ({res['backups']} backup(s) saved under data/backups — fully undoable.)")
    conflicts = res.get("conflicts") or []
    if conflicts:
        print(f"  {len(conflicts)} file(s) you'd customized were also changed by this update:")
        for r in conflicts[:20]:
            print(f"    needs review: {r}")
        print("  Open Adam and say 'merge the update conflicts' to reconcile them.")
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main())
