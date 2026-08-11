"""
Adam — offline license verification (paid-product gating).

Adam is sold on the "bring your own Anthropic API key" model. THIS module is about a
different key: a **license key** that proves the user bought Adam. It is verified entirely
offline — no license server, no phone-home — matching Adam's local-first promise.

How it works:
  * The vendor holds an Ed25519 PRIVATE key (scripts/vendor/, NEVER shipped, NEVER committed).
  * On purchase, `scripts/vendor/issue_license.py` signs a small payload (email, tier,
    issue/expiry dates, order id) into a license key and emails it to the buyer.
  * Adam ships with only the matching PUBLIC key (below) and verifies the signature here.
    A key can't be forged without the private key, but there is no tracking and no network
    call — an honest-but-effective bar, not invasive DRM.

Enforcement policy (trial length, whether to hard-gate or just nag) is DELIBERATELY not
decided here — see `is_licensed()` / `LicenseInfo` and wire the policy at the call site.
Read-only, pure-stdlib + `cryptography` (already a dependency). Never raises to callers.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# --- The product's license-verification public key --------------------------------
# Replace this placeholder with the hex printed by scripts/vendor/make_signing_keys.py.
# While it is the placeholder, licensing is treated as NOT CONFIGURED (every key reports
# unlicensed rather than crashing), so a build without a real key still runs — it just
# can't validate anyone. The private half must live only on the vendor's machine.
LICENSE_PUBLIC_KEY_HEX = "7050605891d83ce58a91edf4a06b8fcd9b6b858de8dc412dc9bfe6a6719edde8"

PRODUCT = "adam"
KEY_PREFIX = "ADAM1-"          # human-recognizable, version-tagged
VALID_TIERS = ("personal", "pro", "lifetime")

# Where an activated key is stored on the user's machine. Read by _stored_key() and
# written by install_license_key(); the server runs with CWD = project root.
LICENSE_KEY_FILE = Path("data/state/license.key")


@dataclass(frozen=True)
class LicenseInfo:
    """Result of verifying a license key. `valid` is the only field a gate should trust."""
    valid: bool
    reason: str = ""           # why invalid (for logs / support), never a secret
    tier: str = ""
    email: str = ""            # buyer email embedded at issue time
    issued: str = ""           # YYYY-MM-DD
    expires: str = ""          # YYYY-MM-DD or "" for perpetual
    order_id: str = ""

    def as_public_dict(self) -> dict:
        """Safe to surface in /license or the settings UI (carries no signature/secret)."""
        return asdict(self)


def _configured() -> bool:
    return bool(LICENSE_PUBLIC_KEY_HEX) and LICENSE_PUBLIC_KEY_HEX != "REPLACE_WITH_YOUR_PUBLIC_KEY"


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_license_key(key: str, public_key_hex: Optional[str] = None) -> LicenseInfo:
    """Verify a license key offline. Returns a LicenseInfo; `valid` is False for any problem
    (unconfigured, malformed, bad signature, wrong product, expired). Never raises."""
    pk_hex = public_key_hex if public_key_hex is not None else LICENSE_PUBLIC_KEY_HEX
    if not pk_hex or pk_hex == "REPLACE_WITH_YOUR_PUBLIC_KEY":
        return LicenseInfo(False, "licensing not configured (no public key)")
    if not key or not isinstance(key, str):
        return LicenseInfo(False, "no license key")

    raw = key.strip()
    if raw.startswith(KEY_PREFIX):
        raw = raw[len(KEY_PREFIX):]
    if raw.count(".") != 1:
        return LicenseInfo(False, "malformed key (expected payload.signature)")
    payload_b64, sig_b64 = raw.split(".", 1)

    try:
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pk_hex))
    except (binascii.Error, ValueError):
        return LicenseInfo(False, "malformed key encoding")

    try:
        pub.verify(signature, payload_bytes)   # signature covers the exact payload bytes
    except InvalidSignature:
        return LicenseInfo(False, "signature does not match (key altered or not ours)")

    try:
        p = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return LicenseInfo(False, "signed payload is not valid JSON")

    if p.get("p") != PRODUCT:
        return LicenseInfo(False, f"license is for a different product ({p.get('p')!r})")

    expires = p.get("x") or ""
    if expires:
        try:
            if date.today() > datetime.strptime(expires, "%Y-%m-%d").date():
                return LicenseInfo(False, f"license expired on {expires}", tier=p.get("t", ""),
                                   email=p.get("e", ""), issued=p.get("i", ""),
                                   expires=expires, order_id=p.get("o", ""))
        except ValueError:
            return LicenseInfo(False, "license has a malformed expiry date")

    return LicenseInfo(
        valid=True, reason="ok",
        tier=str(p.get("t", "")), email=str(p.get("e", "")),
        issued=str(p.get("i", "")), expires=expires, order_id=str(p.get("o", "")),
    )


def _stored_key() -> str:
    """Where Adam looks for the user's license key, in priority order. All optional; a fresh
    install simply has none (unlicensed / trial). Kept flexible so the settings UI can write
    to whichever the product standardizes on."""
    env = os.environ.get("ADAM_LICENSE_KEY", "").strip()
    if env:
        return env
    # a plain file under the state dir (written by install_license_key, like the token)
    for candidate in (LICENSE_KEY_FILE, Path("license.key")):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return ""


def current_license() -> LicenseInfo:
    """Verify whatever license key is installed on this machine (env → state file)."""
    return verify_license_key(_stored_key())


def is_licensed() -> bool:
    """Convenience gate. NOTE: enforcement policy (trial grace, hard-gate vs nag) belongs at
    the call site — this only answers 'is there a valid paid license right now?'."""
    return current_license().valid


def is_configured() -> bool:
    """True once the product's license public key is set (not the placeholder). The UI hides
    the license field until this is true, so an unconfigured build shows nothing confusing."""
    return _configured()


# --- Selling switch (beta ships dormant even with a real key committed) -------------
# Having a real public key (above) lets Adam VERIFY license keys. It does NOT, by itself,
# mean this build should ENFORCE the trial→paywall. We keep the two separate on purpose:
# the signing key stays committed so every build can verify keys (and the vendor stops
# hand-reverting it), but enforcement stays OFF until we deliberately turn it on — so a
# beta build cut for advertising never arms a 30-day paywall on installs that have no way
# to buy yet (checkout still pending). Enforcement is opt-in and defaults off; until it is
# on, every install is fully entitled, exactly like an unconfigured build.
#
# Turn selling ON either way (either is sufficient):
#   * set env  ADAM_SELL_MODE=1  (or true/on/yes) at runtime, or
#   * flip SELL_MODE_DEFAULT below to True in the build you cut to start selling.
# An explicit env value always wins, so ADAM_SELL_MODE=0 is also a kill switch that forces
# enforcement off even when the compiled default is True.
SELL_MODE_DEFAULT = True   # ARMED 2026-08-11 — Adam Plus is on sale (Gumroad).


def _sell_mode() -> bool:
    """Whether THIS build should enforce the trial→paywall. Opt-in, default off. An explicit
    ADAM_SELL_MODE env value (on OR off) always overrides the compiled default."""
    env = os.environ.get("ADAM_SELL_MODE")
    if env is not None:
        return env.strip().lower() in ("1", "true", "on", "yes")
    return SELL_MODE_DEFAULT


def is_selling() -> bool:
    """True only when this build actively sells: a real public key IS embedded AND selling is
    switched on. This is the enforcement predicate — trial, paywall, and the license UI all
    follow it, so a configured-but-dormant beta build behaves exactly like an unconfigured one."""
    return _configured() and _sell_mode()


# --- Trial + entitlement -----------------------------------------------------------
# Model: a free trial, then feature-limit — core stays usable, premium capabilities
# (phone/remote access) need a license. Safety rule: an UNCONFIGURED build (no public
# key set) is ALWAYS fully entitled, so the current beta and the vendor's own machine
# are never gated. Enforcement only applies once the product is configured to sell.
TRIAL_DAYS = 30
TRIAL_FILE = Path("data/state/trial_start.txt")
# Where a post-trial, unlicensed user is sent to buy. Update when the store is live.
BUY_URL = "https://adam.camposenterprises.org/#pricing"


def _trial_start() -> Optional[date]:
    """The date the trial began (first run), creating it on first call. None only if the
    state dir can't be written — callers then treat the trial as active (never lock out)."""
    try:
        if TRIAL_FILE.is_file():
            return date.fromisoformat(TRIAL_FILE.read_text(encoding="utf-8").strip()[:10])
    except (OSError, ValueError):
        pass
    try:
        TRIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        today = date.today()
        TRIAL_FILE.write_text(today.isoformat(), encoding="utf-8")
        return today
    except OSError:
        return None


def trial_status() -> dict:
    """Days used/left in the free trial and whether it is still active. Never raises. When this
    build isn't selling (unconfigured or dormant beta), reports a full untouched trial and does
    NOT start the clock — so merely polling /license during a free beta never begins a trial."""
    if not is_selling():
        return {"in_trial": True, "days_used": 0, "days_left": TRIAL_DAYS, "started": ""}
    start = _trial_start()
    if start is None:
        return {"in_trial": True, "days_used": 0, "days_left": TRIAL_DAYS, "started": ""}
    used = max(0, (date.today() - start).days)
    left = max(0, TRIAL_DAYS - used)
    return {"in_trial": left > 0, "days_used": used, "days_left": left, "started": start.isoformat()}


def is_entitled() -> bool:
    """May the user use premium (phone/remote) features right now? True for a build that isn't
    selling (unconfigured OR a dormant beta), a valid license, or an active trial. Fail-open —
    never wrongly lock a paying user out."""
    if not is_selling():
        return True
    if is_licensed():
        return True
    return trial_status()["in_trial"]


def install_license_key(key: str) -> LicenseInfo:
    """Verify a key and, if valid, persist it so it takes effect immediately (no restart).
    Invalid keys are NOT stored. Never raises — returns a LicenseInfo either way."""
    info = verify_license_key(key)
    if info.valid:
        try:
            LICENSE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            LICENSE_KEY_FILE.write_text((key or "").strip() + "\n", encoding="utf-8")
        except OSError as e:
            return LicenseInfo(False, f"license verified but could not be saved: {e}")
    return info


def remove_license() -> None:
    """Deactivate: delete the stored license key if present. Never raises."""
    try:
        LICENSE_KEY_FILE.unlink()
    except OSError:
        pass


# --- EULA assent (clickwrap) -------------------------------------------------------
# Our warranty disclaimer (§16), liability cap (§17), and arbitration clause (§22) are
# only enforceable if the user actually assented to the EULA. Courts enforce clickwrap
# (affirmative "I agree" to conspicuous terms) and refuse browsewrap (a passive footer
# link). So we present the EULA at first run and after any material EULA change, require
# an "I agree" click, and record it here — locally, append-only, no phone-home, the same
# way the license key is handled. Spec: legal-drafts/shared/ASSENT_AND_CLICKWRAP_SPEC.md.
#
# EULA_VERSION is the "Last updated:" date at the top of the shipped EULA
# (web/legal/eula.html). Bump it whenever the EULA changes materially so the gate
# re-presents the terms and records a fresh acceptance.
EULA_VERSION = "2026-07-16"
ASSENT_FILE = Path("data/state/assent.json")


def _read_assent_records() -> list:
    """Every acceptance ever recorded (append-only history, newest last). Returns [] if
    none or the file is missing/unreadable. Never raises."""
    try:
        if ASSENT_FILE.is_file():
            data = json.loads(ASSENT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
    except (OSError, ValueError):
        pass
    return []


def record_assent(version: str, method: str = "install-clickwrap", order_id: str = "") -> dict:
    """Append one assent record and persist it. Returns the record written, or {} if it
    could not be saved. Append-only — we never overwrite prior acceptances, so the trail
    survives version bumps. Never raises."""
    rec = {
        "product": PRODUCT,
        "document": "EULA",
        "version": (version or "").strip(),
        "accepted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": method,
        "order_id": order_id or "",
    }
    try:
        ASSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        records = _read_assent_records()
        records.append(rec)
        ASSENT_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
        return rec
    except OSError:
        return {}


def assent_status() -> dict:
    """Has the user accepted the CURRENT EULA version? `needs_assent` is what the first-run
    gate checks. A fresh install (no records) or a stale acceptance (older version) both
    report needs_assent=True. Never raises."""
    accepted = [r for r in _read_assent_records()
                if r.get("document") == "EULA" and r.get("version") == EULA_VERSION]
    latest = accepted[-1] if accepted else None
    return {
        "current_version": EULA_VERSION,
        "accepted": bool(latest),
        "needs_assent": latest is None,
        "accepted_at": (latest or {}).get("accepted_at", ""),
        "accepted_version": (latest or {}).get("version", ""),
    }
