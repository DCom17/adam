"""
Adam — Phase 2 smart-update engine tests (M1).

Proves the three-way update never silently loses a friend's local change:
  * each verdict category is classified correctly (new/unchanged/take_incoming/
    keep_local/conflict/no_baseline);
  * apply OVERWRITES only files the friend didn't touch (backing them up first);
  * apply KEEPS a friend's local edit when the maintainer didn't change that file;
  * a true conflict (both sides changed) is HELD, not clobbered, and reported;
  * the pristine baseline advances to the shipped build after apply;
  * .env / settings.json / data/ are never managed by the updater.

Self-contained: redirects config.BACKUP_DIR at a temp sandbox so it never touches
real data/. Run:  python test_update_engine.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import config
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


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_update_test_"))
    baseline = sandbox / "baseline"
    install = sandbox / "install"
    new = sandbox / "new"
    backups = sandbox / "backups"
    for d in (baseline, install, new, backups):
        d.mkdir(parents=True, exist_ok=True)

    # backups land in our sandbox, not real data/
    config.BACKUP_DIR = backups
    config.PERM_BACKUP_BEFORE_WRITE = True
    config.PERM_MAX_FILE_SIZE_MB = 25

    # --- Build the three trees ---------------------------------------------------
    # a.py : friend untouched, maintainer changed     -> take_incoming
    write(baseline / "a.py", "v1\n"); write(install / "a.py", "v1\n"); write(new / "a.py", "v2\n")
    # b.py : friend changed, maintainer untouched       -> keep_local
    write(baseline / "b.py", "v1\n"); write(install / "b.py", "LOCAL\n"); write(new / "b.py", "v1\n")
    # c.py : both changed differently                   -> conflict
    write(baseline / "c.py", "v1\n"); write(install / "c.py", "LOCALc\n"); write(new / "c.py", "v2c\n")
    # d.py : no ancestor recorded                       -> no_baseline (overwrite + backup)
    write(install / "d.py", "x\n"); write(new / "d.py", "y\n")
    # e.py : brand new file in the build                -> new_file
    write(new / "e.py", "brand new\n")
    # f.py : nobody changed it                          -> unchanged
    write(baseline / "f.py", "same\n"); write(install / "f.py", "same\n"); write(new / "f.py", "same\n")
    # excluded: must never be managed by the updater
    write(new / ".env", "TOKEN=should-not-ship\n")
    write(new / "settings.json", "{}\n")
    write(new / "data" / "state" / "x.json", "runtime\n")

    # --- classify ----------------------------------------------------------------
    verdicts = {v.rel: v.category for v in ue.classify(new, install, baseline)}
    check("a.py -> take_incoming", verdicts.get("a.py") == "take_incoming")
    check("b.py -> keep_local", verdicts.get("b.py") == "keep_local")
    check("c.py -> conflict", verdicts.get("c.py") == "conflict")
    check("d.py -> no_baseline", verdicts.get("d.py") == "no_baseline")
    check("e.py -> new_file", verdicts.get("e.py") == "new_file")
    check("f.py -> unchanged", verdicts.get("f.py") == "unchanged")
    check(".env excluded from management", ".env" not in verdicts)
    check("settings.json excluded from management", "settings.json" not in verdicts)
    check("data/ excluded from management",
          not any(r.startswith("data/") for r in verdicts))

    # --- apply (M1: hold conflicts, no merge) ------------------------------------
    res = ue.apply_update(new, install, baseline, merge=False)

    check("a.py overwritten to v2", read(install / "a.py") == "v2\n")
    check("a.py backed up before overwrite",
          any(Path(b).name.endswith("a.py") for b in res.backups))
    check("b.py KEPT as the friend's local edit", read(install / "b.py") == "LOCAL\n")
    check("b.py reported as kept_local", "b.py" in res.kept_local)
    check("c.py conflict HELD as theirs", read(install / "c.py") == "LOCALc\n")
    check("c.py reported as conflict", "c.py" in res.conflicts)
    check("d.py overwritten (no_baseline) to y", read(install / "d.py") == "y\n")
    check("d.py backed up before overwrite",
          any(Path(b).name.endswith("d.py") for b in res.backups))
    check("e.py created", (install / "e.py").is_file() and read(install / "e.py") == "brand new\n")
    check("f.py untouched (unchanged)", read(install / "f.py") == "same\n")
    check(".env never written to install", not (install / ".env").exists())
    check("settings.json never written to install", not (install / "settings.json").exists())
    check("data/ never written to install", not (install / "data").exists())

    # --- baseline advanced to the shipped build ----------------------------------
    check("baseline a.py advanced to v2", read(baseline / "a.py") == "v2\n")
    check("baseline now has e.py", (baseline / "e.py").is_file())
    check("baseline excludes .env", not (baseline / ".env").exists())
    check("baseline excludes data/", not (baseline / "data").exists())

    # --- a second apply of the SAME build is a clean no-op -----------------------
    res2 = ue.apply_update(new, install, baseline, merge=False)
    check("re-running same update writes nothing new", res2.written == [])
    check("re-running same update finds no conflicts", res2.conflicts == [])
    # b.py is now keep_local again (friend still differs, maintainer still didn't change it)
    check("friend's b.py still preserved on re-run", read(install / "b.py") == "LOCAL\n")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
