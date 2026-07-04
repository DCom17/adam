"""
Jarvis Voice Local — GitHub Releases updater tests.

Covers the version logic, release parsing, the (network-free, mocked) update check,
and a full download-and-apply that runs the real 3-way engine on a temp install.

Self-contained: redirects config dirs + monkeypatches the network calls, so it never
touches GitHub or real data/. Run:  python test_updater.py   (exit 0 = all passed)
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import config
import update_engine
import updater

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


def main() -> int:
    print("[version logic]")
    check("parse 'v0.9.3' -> (0,9,3)", updater.parse_version("v0.9.3") == (0, 9, 3))
    check("parse '0.10.0' -> (0,10,0)", updater.parse_version("0.10.0") == (0, 10, 0))
    check("0.9.3 newer than 0.9.2", updater.is_newer("0.9.3", "0.9.2"))
    check("0.10.0 newer than 0.9.9", updater.is_newer("v0.10.0", "0.9.9"))
    check("same version is NOT newer", not updater.is_newer("0.9.2", "0.9.2"))
    check("older is NOT newer", not updater.is_newer("0.9.1", "0.9.2"))

    print("\n[release parsing]")
    rel = {"tag_name": "v0.9.3", "body": "notes here",
           "assets": [{"name": "readme.txt", "browser_download_url": "u1"},
                      {"name": "jarvis-voice-local-v0.9.3.zip", "browser_download_url": "u2"}]}
    check("picks the .zip asset url", updater._zip_asset_url(rel) == "u2")
    check("no zip asset -> None", updater._zip_asset_url({"assets": []}) is None)

    print("\n[check_for_update — mocked network]")
    orig_fetch = updater._fetch_latest_release
    try:
        updater._fetch_latest_release = lambda repo, timeout=15: rel
        r = updater.check_for_update(current="0.9.2", use_cache=False)
        check("newer release -> available", r["available"] and r["latest"] == "0.9.3")
        check("carries notes + url", r["notes"] == "notes here" and r["url"] == "u2")
        r2 = updater.check_for_update(current="0.9.3", use_cache=False)
        check("up to date -> not available", not r2["available"])

        def boom(repo, timeout=15):
            raise OSError("no network")
        updater._fetch_latest_release = boom
        r3 = updater.check_for_update(current="0.9.2", use_cache=False)
        check("network error -> not available, error set", not r3["available"] and r3["error"])
    finally:
        updater._fetch_latest_release = orig_fetch

    print("\n[download_and_apply — mocked download, real apply]")
    sandbox = Path(tempfile.mkdtemp(prefix="jvl_updater_test_"))
    install = sandbox / "install"
    newbuild = sandbox / "newbuild"
    backups = sandbox / "backups"
    baseline = sandbox / "baseline"
    for d in (install, newbuild, backups):
        d.mkdir(parents=True, exist_ok=True)
    config.BACKUP_DIR = backups
    config.PERM_BACKUP_BEFORE_WRITE = True
    config.PERM_MAX_FILE_SIZE_MB = 25
    update_engine.BASELINE_DIR = baseline   # default ancestor for apply_update

    # an install at 0.9.2 ...
    (install / "server.py").write_text("# server\n", encoding="utf-8")
    (install / "config.py").write_text('APP_VERSION = "0.9.2"\n', encoding="utf-8")
    # ... and a new build at 0.9.3 packaged as a zip
    (newbuild / "server.py").write_text("# server v2\n", encoding="utf-8")
    (newbuild / "config.py").write_text('APP_VERSION = "0.9.3"\n', encoding="utf-8")
    build_zip = sandbox / "build.zip"
    with zipfile.ZipFile(build_zip, "w") as zf:
        for f in newbuild.rglob("*"):
            zf.write(f, f.relative_to(newbuild).as_posix())

    # mock the network download to just copy our local build zip into place
    import shutil as _sh
    updater._download = lambda url, dest, timeout=120: _sh.copyfile(build_zip, dest)

    out = updater.download_and_apply("https://example/whatever.zip", install_root=install)
    check("apply ok", out["ok"] and out["error"] is None)
    check("install moved to 0.9.3", 'APP_VERSION = "0.9.3"' in (install / "config.py").read_text("utf-8"))
    check("reported version is 0.9.3", out["version"] == "0.9.3")
    check("no conflicts on a clean install", out["conflicts"] == [])

    # a non-zip download is rejected, install untouched
    updater._download = lambda url, dest, timeout=120: Path(dest).write_bytes(b"<html>nope")
    out2 = updater.download_and_apply("https://example/bad.zip", install_root=install)
    check("non-zip download rejected", not out2["ok"] and "zip" in (out2["error"] or ""))
    check("install still at 0.9.3 after bad download", 'APP_VERSION = "0.9.3"' in (install / "config.py").read_text("utf-8"))

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
