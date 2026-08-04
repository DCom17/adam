"""Shared rate limiter. Lives in its own module so the router modules and
server.py can use the same Limiter without a circular import.

Security posture (OWASP API4:2023 — Unrestricted Resource Consumption):

  * BASELINE — every route gets DEFAULT_LIMITS, applied by SlowAPIMiddleware
    (wired in server.py). Nothing is silently unlimited any more.
  * TIGHTER — expensive routes (/ask, /speak, …) keep their own
    @limiter.limit decorators. slowapi's _should_exempt() skips the middleware
    default for any route that carries a decorator, so the two never stack and
    the decorated value is the one that applies.
  * BUCKETS — keyed per credential AND per client address (see _client_key),
    so a phone burning its allowance can't lock the desktop out, and a LAN
    peer without the token can't consume the owner's budget.

Sizing: a live turn costs roughly 55 req/min (a /poll every 2.5s plus its
sendSeen heartbeat, session sync every 25s, approvals every 30s). The default
below leaves ~10x headroom on top of that so normal use — including several
devices on one token — never sees a 429, while a wedged client loop or a
malicious page hammering localhost still gets cut off.
"""

import hashlib

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

# Per-minute stops runaway loops; per-hour stops slow-drip abuse that would
# stay under the minute ceiling. Both are per-key, not global.
DEFAULT_LIMITS = ["600/minute", "20000/hour"]


def _client_key(request: Request) -> str:
    """Bucket key: bearer-token fingerprint + client address.

    Adam is single-user software, so "user" means "whoever holds the token".
    Keying on the token alone would put every device in one bucket (the phone
    could starve the desktop); keying on IP alone would merge every caller
    behind one NAT or Tailscale exit. The pair gives per-device isolation.

    The token is SHA-256'd and truncated before it is ever used as a key, so
    the raw secret never reaches limiter storage, logs, or an error payload.
    """
    ip = get_remote_address(request) or "unknown"
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        raw = auth[7:].strip()
        if raw:
            fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            return f"{fingerprint}:{ip}"
    # Unauthenticated callers (static shells, /ping, a probe on the LAN) share
    # a per-address bucket — they can't spend an authenticated caller's budget.
    return f"anon:{ip}"


# headers_enabled surfaces X-RateLimit-Limit/Remaining/Reset on every response,
# so a client can back off BEFORE it gets cut off rather than discovering the
# ceiling by hitting it (OWASP: fail visibly, not silently).
limiter = Limiter(
    key_func=_client_key,
    default_limits=DEFAULT_LIMITS,
    headers_enabled=True,
)


def rate_limit_handler(request: Request, exc) -> JSONResponse:
    """Graceful 429: a plain-language message plus a real Retry-After.

    MUST stay synchronous. SlowAPIMiddleware calls handlers through
    sync_check_limits(), which silently falls back to slowapi's own bare
    handler if the registered one is a coroutine — an async version here would
    look wired up and never actually run.
    """
    retry_after = 60
    try:
        # exc.limit.limit is a limits.RateLimitItem; GRANULARITY.seconds is the
        # window (60 for "/minute", 3600 for "/hour").
        retry_after = int(exc.limit.limit.GRANULARITY.seconds)
    except Exception:  # noqa: BLE001 — a missing attribute just means "use 60"
        pass

    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            # Plain ASCII: this string ends up in logs and consoles whose
            # encoding we don't control.
            "detail": (
                "Too many requests in a short window. This is a safety valve, "
                f"not a fault. Try again in about {retry_after} seconds."
            ),
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )
