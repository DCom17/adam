"""Bearer-token auth dependency, shared by every protected route.

Lives in its own module so router modules can depend on it without importing
server.py at import time (server.py re-exports it for back-compat)."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

import config


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
