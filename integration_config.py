"""
Adam — first-party integration config writer.

Turning an add-on ON means writing two of the app's OWN config files: the
non-secret block in settings.json and the secret token in .env. Doing that by
hand is the single most error-prone step in setup — a missing comma or a stray
bracket breaks the whole settings.json and the server refuses to boot. This
module lets the SERVER write those files itself, always producing valid JSON and
a valid .env line, so a wizard can offer a one-click "enable" instead of asking
a non-technical user to edit files.

This is FIRST-PARTY config management (the app writing its own settings in
response to an explicit, token-gated user action), NOT the agent/Claude write
path. It deliberately does not route through the permission layer that protects
.env/settings.json from the AGENT — that layer exists to stop Claude from
touching secrets, not to stop the app from managing its own configuration. Every
write backs up the prior file first, and writes are atomic (temp + replace) so a
crash mid-write can never leave a half-written, unbootable config.

Nothing here logs a token. Callers pass secrets in; this module only writes them
to the local .env and never returns or prints them.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent


# --- backups ----------------------------------------------------------------

def _backup(path: Path, backup_dir: Path | None = None) -> Path | None:
    """Copy `path` into the backups tree with a timestamped name before it is
    overwritten. Best-effort: a backup failure must not block the config write
    (the atomic write itself is the real safety net). Backups land under
    data/backups, which is gitignored AND deny-guarded out of release ZIPs, so a
    .env backup can never leak into a shipped build. Returns the backup path."""
    if not path.exists():
        return None
    bdir = Path(backup_dir) if backup_dir else Path(config.BACKUP_DIR)
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = bdir / f"{path.name}.{stamp}.bak"
        dest.write_bytes(path.read_bytes())
        return dest
    except Exception:
        return None


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: write a sibling temp file, then replace.
    os.replace is atomic on the same volume on both POSIX and Windows, so a reader
    (or a crash) never sees a partially written config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- settings.json ----------------------------------------------------------

def _load_settings_dict(settings_path: Path, example_path: Path) -> dict:
    """Return the current settings as a dict. Prefer the real settings.json; if it
    doesn't exist yet, seed from settings.example.json so a first-time enable
    preserves the full template (and its comments) instead of writing a bare file.
    utf-8-sig tolerates a BOM that some Windows editors add."""
    for p in (settings_path, example_path):
        if p and p.exists():
            try:
                data = json.loads(p.read_text("utf-8-sig"))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                # Don't silently clobber a file we can't parse — surface it so the
                # caller can tell the user to fix or restore settings.json. (The
                # example file should always parse; only the real one can be dirty.)
                if p == settings_path:
                    raise
                return {}
    return {}


def set_settings_integration(
    name: str,
    block: dict,
    settings_path: str | os.PathLike | None = None,
    example_path: str | os.PathLike | None = None,
    backup_dir: str | os.PathLike | None = None,
) -> Path:
    """Set integrations[`name`] = `block` in settings.json, preserving every other
    setting, and write valid JSON. This is the comma/bracket-proof replacement for
    hand-editing the file. Backs up the existing settings.json first. Returns the
    settings.json path written.

    `block` is the non-secret integration config only (enabled flag, bridge_url,
    ids, timeouts) — never a token; tokens go to .env via set_env_var."""
    sp = Path(settings_path) if settings_path else (ROOT / "settings.json")
    ep = Path(example_path) if example_path else (ROOT / "settings.example.json")

    data = _load_settings_dict(sp, ep)
    integrations = data.get("integrations")
    if not isinstance(integrations, dict):
        integrations = {}
    integrations[name] = block
    data["integrations"] = integrations

    _backup(sp, Path(backup_dir) if backup_dir else None)
    _atomic_write_text(sp, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return sp


# Top-level settings keys this writer is allowed to set. Deliberately tiny — this
# is for the app's own one-click controls (the capability tier + the AI-plan
# settings), never a path to rewrite arbitrary config or safety internals by name.
_ALLOWED_TOP_LEVEL = {
    "capability_tier",
    "auth_mode",                 # subscription | api_key (the two-door choice)
    "voice_model",               # active Claude model for spawned turns
    "api_budget_monthly_usd",    # pay-as-you-go monthly ceiling (0 = off)
}


def set_settings_top_level(
    key: str,
    value,
    settings_path: str | os.PathLike | None = None,
    example_path: str | os.PathLike | None = None,
    backup_dir: str | os.PathLike | None = None,
) -> Path:
    """Set a top-level settings.json key (currently only `capability_tier`),
    preserving every other setting and writing valid JSON. Same comma/bracket-proof,
    atomic, backed-up path as set_settings_integration. Raises ValueError for any
    key outside the small allow-list."""
    if key not in _ALLOWED_TOP_LEVEL:
        raise ValueError(f"refusing to set non-allow-listed top-level key: {key!r}")
    sp = Path(settings_path) if settings_path else (ROOT / "settings.json")
    ep = Path(example_path) if example_path else (ROOT / "settings.example.json")

    data = _load_settings_dict(sp, ep)
    data[key] = value

    _backup(sp, Path(backup_dir) if backup_dir else None)
    _atomic_write_text(sp, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return sp


# --- .env -------------------------------------------------------------------

def set_env_var(
    key: str,
    value: str,
    env_path: str | os.PathLike | None = None,
    backup_dir: str | os.PathLike | None = None,
    section_header: str | None = None,
) -> Path:
    """Set `key`=`value` in the .env file, in place. If a line for `key` already
    exists (commented or not, with or without leading whitespace), it is replaced;
    otherwise the pair is appended (under `section_header` if the file doesn't
    already contain that header). Other lines are left byte-for-byte intact. Backs
    up the existing .env first and writes atomically. Returns the .env path.

    The value is written verbatim as KEY=value (bridge tokens are hex, so no
    escaping is needed). Nothing here is logged."""
    ep = Path(env_path) if env_path else (ROOT / ".env")
    existing = ep.read_text("utf-8") if ep.exists() else ""
    # Split keeping it simple; we rejoin with "\n". Track whether the file ended
    # with a newline so we can preserve that.
    had_trailing_nl = existing.endswith("\n") or existing == ""
    lines = existing.split("\n")
    if existing.endswith("\n"):
        lines = lines[:-1]  # drop the empty element split() leaves after a final \n

    new_line = f"{key}={value}"
    replaced = False
    out: list[str] = []
    for ln in lines:
        stripped = ln.lstrip()
        # Match "KEY=" or "#KEY=" (a commented placeholder) so we update in place.
        bare = stripped[1:].lstrip() if stripped.startswith("#") else stripped
        if bare.startswith(key + "="):
            out.append(new_line)
            replaced = True
        else:
            out.append(ln)

    if not replaced:
        if out and out[-1].strip() != "":
            out.append("")  # blank spacer before a new appended block
        if section_header and section_header not in existing:
            out.append(section_header)
        out.append(new_line)

    text = "\n".join(out)
    if had_trailing_nl and not text.endswith("\n"):
        text += "\n"

    _backup(ep, Path(backup_dir) if backup_dir else None)
    _atomic_write_text(ep, text)
    return ep
