"""
Jarvis Voice Local — GitHub Releases update channel.

The old model pinned every install to ONE Google Drive file and made the build BE
that file, so publishing meant swapping the file's bytes in place ("Manage
versions") and a moved/deleted file broke every install with no recovery. This
replaces that with GitHub Releases: installs read the repo's PERMANENT "latest
release" endpoint (public, no auth, no file IDs), and the maintainer ships by
publishing a release. Deleting or replacing a release never breaks an install.

    check_for_update()      -> {current, latest, available, notes, url, error}
    download_and_apply(url) -> applies the build with the smart 3-way updater

Read-only access needs NO credentials (public repo). Only PUBLISHING needs the
maintainer's GitHub login. Pure stdlib (urllib/json/zipfile) + update_engine.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import config
import update_engine

_API = "https://api.github.com/repos/{repo}/releases/latest"
_UA = "jarvis-voice-local-updater"

# A short cache so the banner check doesn't hammer GitHub's unauthenticated rate
# limit (60/hr/IP) on every page load.
_CACHE: dict = {"at": 0.0, "data": None}
_CACHE_TTL = 300  # seconds


def parse_version(s: str) -> tuple[int, ...]:
    """'v0.9.3' / '0.9.3' / '0.9.3-beta.1' -> (0, 9, 3). Non-numeric/suffix parts
    stop the parse, so a pre-release compares as its base version (good enough for
    'is there something newer')."""
    s = (s or "").strip().lstrip("vV")
    out: list[int] = []
    for part in s.split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _fetch_latest_release(repo: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(_API.format(repo=repo), headers={
        "User-Agent": _UA,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _zip_asset_url(release: dict) -> str | None:
    """The .zip asset's download URL from a release payload (the build to apply)."""
    for a in release.get("assets", []) or []:
        name = (a.get("name") or "").lower()
        if name.endswith(".zip"):
            url = a.get("browser_download_url")
            if url:
                return url
    return None


def check_for_update(repo: str | None = None, current: str | None = None,
                     *, use_cache: bool = True) -> dict:
    """Resolve whether a newer release exists. Never raises — a network/parse/rate
    error comes back as {available: False, error: <reason>} so the UI degrades
    quietly. Shape: {current, latest, available, notes, url, error}."""
    repo = repo or config.UPDATE_REPO
    current = current or config.APP_VERSION
    now = time.time()
    if use_cache and _CACHE["data"] and (now - _CACHE["at"] < _CACHE_TTL):
        return _CACHE["data"]

    result = {"current": current, "latest": None, "available": False,
              "notes": "", "url": None, "error": None}
    try:
        rel = _fetch_latest_release(repo)
        latest = (rel.get("tag_name") or rel.get("name") or "").strip()
        url = _zip_asset_url(rel)
        result["latest"] = latest.lstrip("vV") or None
        result["notes"] = (rel.get("body") or "").strip()
        result["url"] = url
        # An update is only offered if it's both NEWER and has a zip to download.
        result["available"] = bool(latest and url and is_newer(latest, current))
    except Exception as e:  # noqa: BLE001 - update check must never break the app
        result["error"] = str(e)

    _CACHE.update(at=now, data=result)
    return result


def _download(url: str, dest: Path, timeout: int = 120) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh)


def download_and_apply(url: str, install_root: str | Path | None = None) -> dict:
    """Download a release zip and apply it with the smart 3-way updater. Returns a
    summary {ok, updated, kept_local, merged, conflicts, version, error}. The caller
    restarts the app to finish (the running process holds the old code in memory)."""
    install_root = Path(install_root or config.ROOT)
    tmp = Path(tempfile.mkdtemp(prefix="jvl_update_"))
    try:
        zip_path = tmp / "update.zip"
        _download(url, zip_path)
        # Must be a real zip (PK header), not an HTML error page.
        with open(zip_path, "rb") as fh:
            if fh.read(2) != b"PK":
                return {"ok": False, "error": "downloaded file was not a valid zip"}
        extract = tmp / "x"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)
        # Tolerate a wrapper folder (build files may be nested one level).
        src = extract
        if not (src / "server.py").is_file():
            inner = next((d for d in extract.iterdir()
                          if d.is_dir() and (d / "server.py").is_file()), None)
            if inner:
                src = inner
        if not (src / "server.py").is_file():
            return {"ok": False, "error": "update did not contain the expected files"}

        res = update_engine.apply_update(src, install_root, merge=True)
        # Read the version we just moved to (from the applied config.py).
        version = _read_applied_version(install_root)
        return {
            "ok": True,
            "version": version,
            "updated": len(res.written),
            "kept_local": len(res.kept_local),
            "merged": len(res.merged),
            "conflicts": list(res.conflicts),
            "backups": len(res.backups),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_applied_version(root: Path) -> str | None:
    try:
        for line in (root / "config.py").read_text("utf-8").splitlines():
            if line.strip().startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None
