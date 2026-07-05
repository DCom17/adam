"""
Adam — first-run onboarding + setup doctor (v0.7.0, Slice 1).

Importable, testable logic behind `scripts/setup.py` (guided first-run) and
`scripts/doctor.py` (re-runnable health check). Kept at the repo root, next to
`config.py`, so tests can import it directly.

Design rules (enforced here, do not weaken):
  * Idempotent. Re-running never regenerates an existing real token.
  * Never overwrite an existing real ADAM_TOKEN.
  * Never print secret VALUES (only booleans / paths / status).
  * Preserve comments and unrelated lines when editing .env.
  * Back up before modifying .env (and settings.json), then write atomically.
  * Setup may only edit ADAM_TOKEN (.env) and — with consent — claude_exe and
    vault_path (settings.json). It must NEVER touch agent_safety.mode, the
    permissions block, or approval/backup/audit/conflict logic.
"""

from __future__ import annotations

import importlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

# A malformed settings.json makes config raise at import time (it fails fast, by
# design). Onboarding/doctor/setup must still be importable so they can REPORT
# that cleanly instead of dying with a traceback — so the import is guarded and
# run_doctor degrades when config is unavailable.
try:
    import config
except Exception:
    config = None

ROOT = Path(__file__).resolve().parent

# The placeholder shipped in .env.example — treated as "no real token yet".
TOKEN_PLACEHOLDER = "replace-with-a-long-random-token"

# settings.json keys setup is allowed to write. Anything else is refused so a
# bug can never silently rewrite agent_safety / permissions.
ALLOWED_SETTINGS_KEYS = ("claude_exe", "vault_path")


# --- .env handling ----------------------------------------------------------

def generate_token() -> str:
    """A long random bearer token. 32 bytes -> 64 hex chars."""
    return secrets.token_hex(32)


def _timestamp() -> str:
    return time.strftime("%Y%m%d%H%M%S")


def _backup_file(path: Path) -> Path | None:
    """Copy `path` to `<name>.bak.<timestamp>` beside it. Returns the backup path,
    or None if the source did not exist. Backups of .env are secret-bearing and are
    git-ignored by the `.env.*` / `*.env.bak` rules."""
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak.{_timestamp()}")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file in the same dir + os.replace, so a
    crash mid-write never leaves a half-written secrets file."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{_timestamp()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _token_value_from_lines(lines: list[str]) -> str | None:
    """Return the current ADAM_TOKEN value (may be empty string), or None if
    there is no ADAM_TOKEN line at all. A legacy JARVIS_TOKEN line counts as
    the current token (same precedence as config.py), so re-running setup on a
    pre-rename install never mints a new token and strands the phone's copy."""
    for key in ("ADAM_TOKEN=", "JARVIS_TOKEN="):
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(key) and not stripped.startswith("#"):
                return stripped[len(key):].strip()
    return None


def _is_real_token(value: str | None) -> bool:
    return bool(value) and value != TOKEN_PLACEHOLDER


def ensure_env_token(env_path: Path, example_path: Path) -> dict:
    """Make sure `.env` exists and carries a real ADAM_TOKEN, without ever
    clobbering an existing one.

    Returns a dict (NO secret values):
        action: "unchanged" | "token_generated" | "created_with_token"
        env_created: bool      (the .env file itself was created this run)
        token_generated: bool  (a new token was written this run)
        backup: str | None     (path to the .env backup, if one was made)
    """
    env_path = Path(env_path)
    example_path = Path(example_path)
    env_created = False

    if env_path.exists():
        original = env_path.read_text("utf-8")
    elif example_path.exists():
        original = example_path.read_text("utf-8")
        env_created = True
    else:
        # No example to seed from — start with a minimal, commented file.
        original = (
            "# Adam — secrets & machine values.\n"
            "ADAM_TOKEN=" + TOKEN_PLACEHOLDER + "\n"
        )
        env_created = True

    lines = original.splitlines()
    current = _token_value_from_lines(lines)

    # An existing real token is sacred — leave the file untouched.
    if _is_real_token(current) and not env_created:
        return {
            "action": "unchanged",
            "env_created": False,
            "token_generated": False,
            "backup": None,
        }

    token = generate_token()
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (not replaced and stripped.startswith("ADAM_TOKEN=")
                and not stripped.startswith("#")):
            new_lines.append(f"ADAM_TOKEN={token}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"ADAM_TOKEN={token}")

    # Preserve a trailing newline.
    text = "\n".join(new_lines)
    if not text.endswith("\n"):
        text += "\n"

    backup = None
    if env_path.exists():
        backup = _backup_file(env_path)
    _atomic_write(env_path, text)

    return {
        "action": "created_with_token" if env_created else "token_generated",
        "env_created": env_created,
        "token_generated": True,
        "backup": str(backup) if backup else None,
    }


# --- settings.json handling (consent-gated, whitelisted keys only) ----------

def ensure_settings_file(settings_path: Path, example_path: Path) -> bool:
    """Create settings.json from the example if missing. Returns True if created.
    The example already carries draft_only + deny-by-default, so the safe posture
    is preserved verbatim."""
    settings_path = Path(settings_path)
    if settings_path.exists():
        return False
    if Path(example_path).exists():
        shutil.copy2(example_path, settings_path)
        return True
    return False


def set_settings_value(settings_path: Path, key: str, value: str) -> dict:
    """Set ONE whitelisted key in settings.json, preserving every other key
    (including agent_safety and permissions). Backs up first, writes atomically.

    Raises ValueError if `key` is not in ALLOWED_SETTINGS_KEYS — a hard guard so
    this helper can never be used to touch safety settings."""
    if key not in ALLOWED_SETTINGS_KEYS:
        raise ValueError(f"setup may not edit settings key: {key}")
    settings_path = Path(settings_path)
    # utf-8-sig tolerates a BOM-prefixed settings.json saved by some editors.
    data = json.loads(settings_path.read_text("utf-8-sig")) if settings_path.exists() else {}
    backup = _backup_file(settings_path)
    data[key] = value
    _atomic_write(settings_path, json.dumps(data, indent=2) + "\n")
    return {"key": key, "backup": str(backup) if backup else None}


# --- Config-file validation (BOM tolerant, never raises) --------------------

def validate_settings_file(path: Path) -> str | None:
    """Check that a settings file parses as JSON, tolerating a UTF-8 BOM.

    Returns None when the file is absent or parses cleanly (with or without a
    BOM). Returns a friendly, one-line message when it is malformed — naming the
    file and the parse line/column, and pointing at the fix. NEVER raises and
    NEVER includes file contents or secret values, so it is safe to print during
    setup/doctor."""
    path = Path(path)
    if not path.exists():
        return None  # absence is a separate concern (handled by other checks)
    try:
        raw = path.read_text("utf-8-sig")  # strips a BOM if present
    except Exception as e:  # unreadable file — report the kind, not the contents
        return f"{path.name} could not be read ({e.__class__.__name__})."
    try:
        json.loads(raw)
        return None
    except json.JSONDecodeError as e:
        return (
            f"{path.name} is not valid JSON (line {e.lineno}, column {e.colno}): "
            f"{e.msg}. Fix the JSON syntax, or restore it from settings.example.json."
        )


def _reload_config():
    """(Re)load the config module. Returns (module, None) on success, or
    (None, friendly_error) when config can't load — almost always a malformed
    settings.json. Never raises. The error text comes from config's own
    already-sanitized RuntimeError, which carries no secrets/contents."""
    import sys as _sys
    try:
        if _sys.modules.get("config") is not None:
            return importlib.reload(_sys.modules["config"]), None
        import config as _cfg
        return _cfg, None
    except Exception as e:
        return None, str(e)


# --- Setup doctor -----------------------------------------------------------

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _resolve_claude(cfg) -> tuple[str, str]:
    """Windows-aware Claude resolution. Returns (resolved_path, how).

    Accepts: an explicit claude_exe, or a PATH entry resolved via shutil.which
    (which honors PATHEXT, so claude.exe / claude.cmd / claude.bat shims all
    resolve). `how` describes the route for the doctor detail line."""
    configured = str(cfg.CLAUDE_EXE or "").strip()
    if configured:
        p = Path(configured)
        if p.exists():
            return str(p), "configured path (exists)"
        # Configured as a bare command name / shim — let which() resolve it.
        which = shutil.which(configured)
        if which:
            return which, "configured command on PATH"
        return configured, "configured but not found"
    for name in ("claude", "claude.exe", "claude.cmd"):
        which = shutil.which(name)
        if which:
            return which, f"found on PATH ({name})"
    return "", "not found"


def _claude_version_ok(claude_path: str) -> bool | None:
    """Best-effort: run `<claude> --version` with a short timeout. Returns True if
    it exits 0, False if it ran but failed, None if it could not be run. NEVER
    raises and NEVER blocks setup — purely enriches the doctor detail."""
    if not claude_path:
        return None
    try:
        proc = subprocess.run(
            [claude_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return None


def _claude_signin_check(cfg) -> dict:
    """Advisory: does Claude Code look signed in? `--version` succeeds signed-out,
    so an installed-but-never-signed-in Claude used to pass the doctor cleanly and
    then fail the user's FIRST real message. Heuristic, never FAIL: pay-as-you-go
    installs need no sign-in; otherwise look for the CLI's stored credentials."""
    if str(getattr(cfg, "AUTH_MODE", "") or "") == "api_key" and getattr(cfg, "ANTHROPIC_API_KEY", ""):
        return _check("Claude sign-in", PASS, "pay-as-you-go API key set — no sign-in needed")
    home = Path.home()
    evidence = ""
    cred = home / ".claude" / ".credentials.json"
    try:
        if cred.is_file() and cred.stat().st_size > 2:
            evidence = ".claude/.credentials.json present"
    except OSError:
        pass
    if not evidence:
        cj = home / ".claude.json"
        try:
            if cj.is_file() and '"oauthAccount"' in cj.read_text("utf-8", errors="ignore"):
                evidence = "account recorded in .claude.json"
        except OSError:
            pass
    if evidence:
        return _check("Claude sign-in", PASS, f"looks signed in ({evidence})")
    return _check("Claude sign-in", WARN,
                  "no sign-in detected — if your first message fails, open a terminal, "
                  "type  claude  , then  /login  (newer Claude versions may store "
                  "credentials elsewhere, so this can be a false alarm)")


def _voice_service_check(cfg) -> dict:
    """Advisory: the optional high-quality Adam voice. Not installed is a valid
    setup (browser-voice fallback); installed-but-dead earns a WARN pointing at
    data/logs/tts.log — 'replies sound robotic' is the most likely tester report
    and used to be undiagnosable."""
    try:
        import tts_supervisor as tsup
        if not tsup.is_local_tts_url():
            return _check("Adam voice (Kokoro)", PASS,
                          f"custom tts_url in use: {getattr(cfg, 'TTS_URL', '')}")
        if not tsup.runnable():
            return _check("Adam voice (Kokoro)", PASS,
                          "not installed — replies use the browser voice; run "
                          "INSTALL-VOICE.cmd for the real Adam voice (optional)")
        if tsup._ping():
            return _check("Adam voice (Kokoro)", PASS, "installed and answering")
        return _check("Adam voice (Kokoro)", WARN,
                      "installed but not answering — Adam restarts it automatically on "
                      "demand (the first reply may use the fallback voice while the "
                      "model warms up); if replies STAY robotic, read data/logs/tts.log")
    except Exception as e:  # noqa: BLE001 — the doctor must never crash on a check
        return _check("Adam voice (Kokoro)", WARN, f"voice check errored: {e}")


def _summary_contains_secret(summary: dict, secret_values: list[str]) -> bool:
    """True if any non-empty secret value appears anywhere in the (stringified)
    health summary. Used to prove /health never leaks a token/key."""
    blob = json.dumps(summary)
    return any(v and v in blob for v in secret_values)


def _port_status(cfg) -> dict:
    """Is the server already answering on the configured port, or is the port at
    least free to bind? Read-only; never starts anything."""
    port = int(cfg.PORT)
    # 1) Is something answering /health?
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            if r.status == 200:
                return _check("Server reachable", PASS, f"/health answered on port {port}")
    except Exception:
        pass
    # 2) Not answering — is the port free for the server to bind later?
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return _check("Server reachable", WARN,
                      f"server not running; port {port} is free to start")
    except OSError:
        return _check("Server reachable", FAIL,
                      f"port {port} is occupied by another process")
    finally:
        s.close()


# --- Mobile-access diagnostics (v0.9, advisory; never FAIL; intent-gated) --------
# These help a user who is setting up phone access; a desktop-local user who hasn't
# configured anything remote stays clean (PASS, no WARN). None block startup.

def _has_remote_intent(cfg) -> bool:
    """True if the config shows intent to serve beyond desktop-local: a public base
    URL, or a non-loopback origin explicitly listed in the CORS allow-list."""
    if str(getattr(cfg, "PUBLIC_BASE_URL", "") or "").strip():
        return True
    for o in (getattr(cfg, "CORS_ALLOWED_ORIGINS", []) or []):
        o = str(o).strip().lower()
        if not o or o == "*":
            continue
        if all(h not in o for h in ("localhost", "127.0.0.1", "::1")):
            return True
    return False


def _mobile_access_check(cfg) -> dict:
    """Is an HTTPS origin configured for phone access? Advisory; never FAIL."""
    base = str(getattr(cfg, "PUBLIC_BASE_URL", "") or "").strip()
    if not base:
        return _check("Mobile access (HTTPS)", PASS,
                      "desktop-local; no remote origin configured")
    if base.lower().startswith("https://"):
        return _check("Mobile access (HTTPS)", PASS,
                      "HTTPS origin configured for mobile access")
    return _check("Mobile access (HTTPS)", WARN,
                  "public_base_url is not https — iPhone mic/voice/PWA need HTTPS "
                  "(use Tailscale Serve or another TLS path)")


# Standard Windows install locations to check when tailscale isn't on PATH (the
# Tailscale installer does not always add itself to PATH).
_TAILSCALE_FALLBACK_PATHS = [
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
]


def _resolve_tailscale() -> str | None:
    """Locate the tailscale CLI: PATH first (shutil.which), then the standard Windows
    install paths. Returns the executable path, or None if not found. Read-only."""
    path = shutil.which("tailscale")
    if path:
        return path
    for cand in _TAILSCALE_FALLBACK_PATHS:
        try:
            if Path(cand).is_file():
                return cand
        except Exception:
            continue
    return None


def _tailscale_check(cfg) -> dict:
    """Is the supported mobile path (Tailscale) available? Detects PATH first, then the
    standard Windows install path. Intent-gated so desktop-local users aren't nagged.
    Failure-safe: short timeout, never raises, never FAILs."""
    path = _resolve_tailscale()
    if path:
        try:
            proc = subprocess.run([path, "status"], capture_output=True,
                                  text=True, timeout=3)
            if proc.returncode == 0:
                return _check("Tailscale (mobile path)", PASS, "Tailscale detected and up")
            return _check("Tailscale (mobile path)", WARN,
                          "Tailscale present but status unavailable")
        except Exception:
            return _check("Tailscale (mobile path)", WARN,
                          "Tailscale present but the status check timed out/failed")
    if _has_remote_intent(cfg):
        return _check("Tailscale (mobile path)", WARN,
                      "Tailscale not found — it's the supported mobile path; install it "
                      "and enable Tailscale Serve")
    return _check("Tailscale (mobile path)", PASS,
                  "not required for desktop-local (install for phone access)")


def _cors_exposure_check(cfg) -> dict:
    """Wide-open CORS combined with remote/public access. Advisory; never FAIL."""
    origins = getattr(cfg, "CORS_ALLOWED_ORIGINS", []) or []
    wide_open = any(str(o).strip() == "*" for o in origins)
    if wide_open and _has_remote_intent(cfg):
        return _check("CORS exposure", WARN,
                      "CORS is open (*) while remote/public access is configured — "
                      "restrict cors_allowed_origins to your exact origin(s)")
    if wide_open:
        return _check("CORS exposure", PASS, "CORS open but no remote origin — fine for LAN")
    return _check("CORS exposure", PASS, "CORS restricted to specific origin(s)")


# Dependencies that must import for the server to run.
_REQUIRED_DEPS = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("dotenv", "python-dotenv"),
    ("slowapi", "slowapi"),
    ("pywebpush", "pywebpush"),
    ("PIL", "Pillow"),
    ("pillow_heif", "pillow-heif"),
    ("multipart", "python-multipart"),
]


# Checks that need NO loaded config — usable even when config can't import.

def _settings_presence_check() -> dict:
    if (ROOT / "settings.json").exists():
        return _check("settings.json", PASS, "present")
    if (ROOT / "settings.example.json").exists():
        return _check("settings.json", WARN,
                      "using settings.example.json defaults (run setup to customize)")
    return _check("settings.json", FAIL, "no settings file and no example")


def _python_deps_check() -> dict:
    missing = []
    for mod, dist in _REQUIRED_DEPS:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(dist)
    if missing:
        return _check("Python dependencies", FAIL,
                      "missing: " + ", ".join(missing) +
                      " (run: python -m pip install -r requirements.txt)")
    return _check("Python dependencies", PASS, "all required packages import")


def run_doctor(reload_config: bool = True) -> list[dict]:
    """Return a list of PASS/WARN/FAIL checks (plain-language details, no secrets).

    `reload_config=True` re-imports config so the doctor reflects edits made during
    the same setup run. Tests pass reload_config=False and monkeypatch config to
    exercise specific scenarios.

    Never crashes on a malformed settings.json: it reports a FAIL for the config
    file and continues with the checks that don't need a loaded config."""
    global config
    checks: list[dict] = []

    # 0. Configuration file parses (BOM tolerant). Done first so a broken file is
    # surfaced as a clean FAIL instead of a traceback.
    active = (ROOT / "settings.json") if (ROOT / "settings.json").exists() else (ROOT / "settings.example.json")
    cfg_file_error = validate_settings_file(active)

    if reload_config:
        new_cfg, load_error = _reload_config()
        if new_cfg is None:
            # Config could not load — degrade: report it, run only config-free
            # checks, and return. No secrets are read on this path.
            checks.append(_check("Configuration file", FAIL,
                                 cfg_file_error or f"configuration failed to load: {load_error}"))
            checks.append(_settings_presence_check())
            checks.append(_python_deps_check())
            return checks
        config = new_cfg

    cfg = config
    if cfg is None:
        checks.append(_check("Configuration file", FAIL,
                             cfg_file_error or "configuration is not loaded"))
        checks.append(_settings_presence_check())
        checks.append(_python_deps_check())
        return checks

    if cfg_file_error:
        checks.append(_check("Configuration file", FAIL, cfg_file_error))
    else:
        checks.append(_check("Configuration file", PASS, "settings parse cleanly (UTF-8/BOM ok)"))

    # 1. Token present.
    if cfg.ADAM_TOKEN:
        checks.append(_check("ADAM_TOKEN present", PASS, "a bearer token is set"))
    else:
        checks.append(_check("ADAM_TOKEN present", FAIL,
                             "no ADAM_TOKEN — run setup, or set it in .env"))

    # 2. Claude resolved (Windows-aware).
    claude_path, how = _resolve_claude(cfg)
    if claude_path and how != "configured but not found":
        ver = _claude_version_ok(claude_path)
        suffix = ("; responded to --version" if ver is True
                  else "; version check inconclusive" if ver is None
                  else "; --version returned non-zero")
        checks.append(_check("Claude executable resolved", PASS, f"{how}{suffix}"))
    else:
        checks.append(_check("Claude executable resolved", FAIL,
                             "Claude Code not found — install it, or set claude_exe "
                             "in settings.json"))

    # 2b. Claude sign-in (advisory) — --version succeeds signed-out, so this is
    # the check that catches a skipped first sign-in BEFORE the first message.
    checks.append(_claude_signin_check(cfg))

    # 3. settings.json present, or example defaults in use.
    checks.append(_settings_presence_check())

    # 4. agent_safety.mode is the safe default.
    mode = cfg.AGENT_MODE
    if mode == "draft_only":
        checks.append(_check("agent_safety.mode", PASS, "draft_only (safe default)"))
    elif mode == "controlled_write":
        checks.append(_check("agent_safety.mode", WARN,
                             "controlled_write — not the shipped default"))
    else:
        checks.append(_check("agent_safety.mode", FAIL,
                             f"{mode} — UNSAFE for a product; should be draft_only"))

    # 5. vault_path exists and is not the project root.
    vault = Path(cfg.VAULT_PATH)
    if not cfg.VAULT_PATH or str(vault.resolve()) == str(ROOT):
        checks.append(_check("vault_path", WARN,
                             "vault_path is unset / the project root — set it to your "
                             "files in settings.json"))
    elif vault.exists():
        checks.append(_check("vault_path", PASS, f"exists: {vault}"))
    else:
        checks.append(_check("vault_path", WARN, f"configured but missing: {vault}"))

    # 6. Write dirs resolve safely (not the vault root, not the filesystem root).
    bad = []
    for d in cfg.PERM_WRITE_DIRS:
        try:
            p = Path(d).resolve()
            p.mkdir(parents=True, exist_ok=True)
            if p == Path(p.anchor) or (cfg.VAULT_PATH and p == Path(cfg.VAULT_PATH).resolve()):
                bad.append(str(p))
        except Exception:
            bad.append(str(d))
    if not cfg.PERM_WRITE_DIRS:
        checks.append(_check("Write directories", WARN, "no write dirs configured"))
    elif bad:
        checks.append(_check("Write directories", WARN, f"questionable targets: {bad}"))
    else:
        checks.append(_check("Write directories", PASS,
                             f"{len(cfg.PERM_WRITE_DIRS)} dir(s) resolve under the data tree"))

    # 7. Protected patterns active.
    pats = [str(p).lower() for p in cfg.PERM_PROTECTED_FILE_PATTERNS]
    need = [".env", "settings.json"]
    has_keys = any("pem" in p or "key" in p for p in pats)
    if all(n in pats for n in need) and has_keys:
        checks.append(_check("Protected file patterns", PASS,
                             "secrets/keys/config are write-protected"))
    elif pats:
        checks.append(_check("Protected file patterns", WARN,
                             "protection is weakened from the safe default"))
    else:
        checks.append(_check("Protected file patterns", FAIL, "no protected patterns set"))

    # 8. data/state path.
    try:
        Path(cfg.STATE_DIR).mkdir(parents=True, exist_ok=True)
        checks.append(_check("data/state path", PASS, f"ready: {cfg.STATE_DIR}"))
    except Exception as e:
        checks.append(_check("data/state path", FAIL, f"cannot create state dir: {e}"))

    # 9. Port availability / server reachability.
    checks.append(_port_status(cfg))

    # 7b. The optional high-quality voice (advisory).
    checks.append(_voice_service_check(cfg))

    # 10. Python dependencies.
    checks.append(_python_deps_check())

    # 11. No secret leakage in /health output.
    secret_values = [
        cfg.ADAM_TOKEN, cfg.VAPID_PUBLIC_KEY, cfg.TWILIO_AUTH_TOKEN,
        getattr(cfg, "CALENDAR_TOKEN", ""),
        getattr(cfg, "HUNTER_TOKEN", ""),
        getattr(cfg, "GMAIL_TOKEN", ""),
        getattr(cfg, "LINKEDIN_ACCESS_TOKEN", ""),
        getattr(cfg, "LINKEDIN_CLIENT_SECRET", ""),
        os.environ.get("VAPID_PRIVATE_KEY", ""),
    ]
    if _summary_contains_secret(cfg.safe_summary(), [v for v in secret_values if v]):
        checks.append(_check("No secret leakage in /health", FAIL,
                             "a secret value appears in the health summary"))
    else:
        checks.append(_check("No secret leakage in /health", PASS,
                             "health summary exposes no token/key material"))

    # 12-14. Mobile-access diagnostics (advisory; never FAIL; intent-gated).
    checks.append(_mobile_access_check(cfg))
    checks.append(_tailscale_check(cfg))
    checks.append(_cors_exposure_check(cfg))

    # 15. Google Calendar connector (advisory; opt-in; never FAIL). A user who
    # never touches calendar passes cleanly at 0 WARN.
    checks.append(_calendar_check(cfg))

    # Hunter Tracker connector (advisory; opt-in; never FAIL).
    checks.append(_hunter_check(cfg))

    # 15a. Gmail connector (advisory; opt-in; never FAIL).
    checks.append(_email_check(cfg))

    # 15c. LinkedIn connector (advisory; opt-in; never FAIL).
    checks.append(_linkedin_check(cfg))

    # 15b. Inbound SMS poller (advisory; opt-in; never FAIL).
    checks.append(_sms_check(cfg))

    # 15d. Voicemail (missed-call) poller (advisory; opt-in; never FAIL).
    checks.append(_voicemail_check(cfg))

    # 16. Bundled Adam brain (informational; never FAIL/WARN). Confirms the
    # de-personalized brain shipped with the app and whether it's the active vault.
    checks.append(_brain_check(cfg))

    return checks


def _brain_check(cfg) -> dict:
    """Informational status of the bundled Adam brain (the de-personalized vault
    that ships with the product). Always PASS — a standalone install without the
    brain is a valid (advanced) configuration, so this never WARNs/FAILs."""
    brain = ROOT / "brain"
    if not brain.is_dir():
        return _check("Adam brain", PASS, "running without the bundled brain (standalone mode)")
    has_core = (brain / "CLAUDE.md").exists() and (brain / "BOOTSTRAP.md").exists()
    if not has_core:
        return _check("Adam brain", PASS, "brain/ present (CLAUDE.md/BOOTSTRAP.md not detected)")
    try:
        pointed = Path(cfg.VAULT_PATH).resolve() == brain.resolve()
    except Exception:
        pointed = False
    if pointed:
        return _check("Adam brain", PASS, "bundled brain present and active (vault_path -> brain/)")
    return _check("Adam brain", PASS,
                  f"bundled brain present; vault_path points elsewhere ({cfg.VAULT_PATH})")


def _calendar_check(cfg) -> dict:
    """Advisory status for the opt-in Google Calendar connector. PASS whether
    off or fully configured; WARN only if the user turned it on but hasn't
    finished setup (missing bridge URL or token). Never FAIL — calendar is
    optional and the core product never needs it."""
    enabled = bool(getattr(cfg, "CALENDAR_ENABLED", False))
    has_url = bool(getattr(cfg, "CALENDAR_BRIDGE_URL", ""))
    has_token = bool(getattr(cfg, "CALENDAR_TOKEN", ""))
    if not enabled:
        return _check("Google Calendar", PASS, "opt-in connector; not enabled")
    if has_url and has_token:
        return _check("Google Calendar", PASS, "enabled and configured (bridge URL + token set)")
    missing = []
    if not has_url:
        missing.append("bridge_url (settings.json)")
    if not has_token:
        missing.append("GOOGLE_CALENDAR_TOKEN (.env)")
    return _check("Google Calendar", WARN,
                  "enabled but setup is unfinished — missing " + ", ".join(missing))


def _hunter_check(cfg) -> dict:
    """Advisory status for the opt-in Hunter connector. PASS whether off or fully
    configured; WARN only if the user turned it on but hasn't finished setup
    (missing bridge URL or token). Never FAIL — Hunter is optional and the core
    product never needs it."""
    enabled = bool(getattr(cfg, "HUNTER_ENABLED", False))
    has_url = bool(getattr(cfg, "HUNTER_BRIDGE_URL", ""))
    has_token = bool(getattr(cfg, "HUNTER_TOKEN", ""))
    if not enabled:
        return _check("Hunter Tracker", PASS, "opt-in connector; not enabled")
    if has_url and has_token:
        return _check("Hunter Tracker", PASS, "enabled and configured (bridge URL + token set)")
    missing = []
    if not has_url:
        missing.append("bridge_url (settings.json)")
    if not has_token:
        missing.append("HUNTER_TOKEN (.env)")
    return _check("Hunter Tracker", WARN,
                  "enabled but setup is unfinished — missing " + ", ".join(missing))


def _linkedin_check(cfg) -> dict:
    """Advisory status for the opt-in LinkedIn connector. PASS whether off, in
    draft-only mode, or fully configured for the API lane; WARN only if the API
    auto-post lane is turned on but its access token or author URN is missing.
    Never FAIL — LinkedIn is optional and the core product never needs it."""
    enabled = bool(getattr(cfg, "LINKEDIN_ENABLED", False))
    api = bool(getattr(cfg, "LINKEDIN_API_ENABLED", False))
    has_token = bool(getattr(cfg, "LINKEDIN_ACCESS_TOKEN", ""))
    has_urn = bool(getattr(cfg, "LINKEDIN_AUTHOR_URN", ""))
    if not enabled:
        return _check("LinkedIn", PASS, "opt-in connector; not enabled")
    if not api:
        return _check("LinkedIn", PASS, "enabled in draft-only mode (copy/paste posting)")
    if has_token and has_urn:
        return _check("LinkedIn", PASS, "enabled; API auto-post lane configured")
    missing = []
    if not has_token:
        missing.append("LINKEDIN_ACCESS_TOKEN (.env)")
    if not has_urn:
        missing.append("author_urn (settings.json)")
    return _check("LinkedIn", WARN,
                  "API auto-post lane on but setup is unfinished — missing " + ", ".join(missing))


def _email_check(cfg) -> dict:
    """Advisory status for the opt-in Gmail connector. PASS whether off or fully
    configured; WARN only if the user turned it on but hasn't finished setup
    (missing bridge URL or token). Never FAIL — email is optional and the core
    product never needs it."""
    enabled = bool(getattr(cfg, "GMAIL_ENABLED", False))
    has_url = bool(getattr(cfg, "GMAIL_BRIDGE_URL", ""))
    has_token = bool(getattr(cfg, "GMAIL_TOKEN", ""))
    if not enabled:
        return _check("Email", PASS, "opt-in connector; not enabled")
    if has_url and has_token:
        return _check("Email", PASS, "enabled and configured (bridge URL + token set)")
    missing = []
    if not has_url:
        missing.append("bridge_url (settings.json)")
    if not has_token:
        missing.append("GMAIL_BRIDGE_TOKEN (.env)")
    return _check("Email", WARN,
                  "enabled but setup is unfinished — missing " + ", ".join(missing))


def _sms_check(cfg) -> dict:
    """Advisory status for the opt-in inbound-SMS poller. PASS whether off or fully
    configured; WARN only if the user turned it on but hasn't finished setup
    (missing Account SID, Auth Token, or number). Never FAIL — SMS is optional and
    the core product never needs it."""
    enabled = bool(getattr(cfg, "SMS_ENABLED", False))
    has_sid = bool(getattr(cfg, "TWILIO_ACCOUNT_SID", ""))
    has_token = bool(getattr(cfg, "TWILIO_AUTH_TOKEN", ""))
    has_number = bool(getattr(cfg, "TWILIO_NUMBER", ""))
    if not enabled:
        return _check("SMS", PASS, "opt-in connector; not enabled")
    if has_sid and has_token and has_number:
        return _check("SMS", PASS, "enabled and configured (Account SID + Auth Token + number set)")
    missing = []
    if not has_sid:
        missing.append("TWILIO_ACCOUNT_SID (.env)")
    if not has_token:
        missing.append("TWILIO_AUTH_TOKEN (.env)")
    if not has_number:
        missing.append("TWILIO_NUMBER (.env)")
    return _check("SMS", WARN,
                  "enabled but setup is unfinished — missing " + ", ".join(missing))


def _voicemail_check(cfg) -> dict:
    """Advisory status for the opt-in voicemail (missed-call) poller. PASS whether
    off or fully configured; WARN only if the user turned it on but hasn't finished
    setup (missing Account SID, Auth Token, or number — shared with SMS). Never FAIL —
    voicemail is optional and the core product never needs it."""
    enabled = bool(getattr(cfg, "VOICEMAIL_ENABLED", False))
    has_sid = bool(getattr(cfg, "TWILIO_ACCOUNT_SID", ""))
    has_token = bool(getattr(cfg, "TWILIO_AUTH_TOKEN", ""))
    has_number = bool(getattr(cfg, "TWILIO_NUMBER", ""))
    if not enabled:
        return _check("Voicemail", PASS, "opt-in connector; not enabled")
    if has_sid and has_token and has_number:
        return _check("Voicemail", PASS, "enabled and configured (Account SID + Auth Token + number set)")
    missing = []
    if not has_sid:
        missing.append("TWILIO_ACCOUNT_SID (.env)")
    if not has_token:
        missing.append("TWILIO_AUTH_TOKEN (.env)")
    if not has_number:
        missing.append("TWILIO_NUMBER (.env)")
    return _check("Voicemail", WARN,
                  "enabled but setup is unfinished — missing " + ", ".join(missing))


def doctor_has_failure(checks: list[dict]) -> bool:
    return any(c["status"] == FAIL for c in checks)
