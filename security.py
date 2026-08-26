"""Bearer-token auth dependency and the secret scrubber, shared by every
protected route.

Lives in its own module so router modules can depend on it without importing
server.py at import time (server.py re-exports it for back-compat)."""

from __future__ import annotations

import hmac
import re

from fastapi import Header, HTTPException

import config

# Secret-SHAPED strings, scrubbed even when they are not one of the configured
# values. A subprocess echoing a mistyped or stale key back at us is still
# leaking a live credential from somewhere, and the exact-value pass below
# cannot know about it. Ordered longest-prefix-first so a more specific vendor
# pattern wins before the generic sk- one.
_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),        # Anthropic
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),            # OpenAI-style
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),        # GitHub
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),      # GitHub fine-grained
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),           # Google
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack
    re.compile(r"SK[0-9a-fA-F]{32}"),                 # Twilio secret key
    re.compile(r"AC[0-9a-fA-F]{32}"),                 # Twilio account SID
)


def redact_secrets(text: str) -> str:
    """Scrub configured secrets and secret-shaped strings out of anything on its
    way to a client, a log file, or a support bundle.

    Two passes, because each catches what the other cannot:
      * exact values pulled live from config (so a test or a late setup write is
        still covered), and
      * shape patterns, for credentials this install has never been told about.

    Belt-and-suspenders by design: nothing here should be logged in the first
    place, but Claude's stderr is relayed into job errors and support bundles
    get pasted into public GitHub issues, so the scrub has to hold even when an
    upstream line slips."""
    if not text:
        return text
    if not isinstance(text, str):
        text = str(text)

    # Pass 1 — exact configured values. Longest first, so a secret that contains
    # another one as a substring can't leave a readable tail behind.
    secrets = [
        config.ADAM_TOKEN, config.CALENDAR_TOKEN, config.HUNTER_TOKEN,
        config.GMAIL_TOKEN, config.LINKEDIN_CLIENT_SECRET,
        config.LINKEDIN_ACCESS_TOKEN, config.TWILIO_AUTH_TOKEN,
        config.OWNER_PHONE,
        getattr(config, "ANTHROPIC_API_KEY", ""),
        getattr(config, "GARMIN_PASSWORD", ""),
    ]
    for s in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(s, "***")

    # Pass 2 — shapes.
    for pat in _SECRET_PATTERNS:
        text = pat.sub("***", text)

    return text


def token_matches(authorization: str) -> bool:
    """True iff the Authorization header carries the correct bearer token.
    Timing-safe and non-raising, for routes that DEGRADE without a token
    (e.g. /health) instead of refusing outright."""
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token:
        return False
    # Compare on bytes with hmac.compare_digest (constant-time; avoids str's ASCII-only
    # restriction so a non-ASCII header can't raise instead of cleanly failing).
    return hmac.compare_digest(
        token.encode("utf-8"), config.ADAM_TOKEN.encode("utf-8")
    )


def require_token(authorization: str = Header(default="")) -> None:
    """Bearer-token gate, timing-safe. Raises 403 on a missing, malformed, wrong-scheme,
    or mismatched credential. The comparison is constant-time so a 403 leaks nothing
    about how close a guess was — important before any tunnel/remote exposure. The token
    value is never logged or echoed."""
    if not token_matches(authorization):
        raise HTTPException(status_code=403, detail="Forbidden")
