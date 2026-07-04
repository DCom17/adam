"""
Jarvis Voice Local — LinkedIn connector (opt-in, off by default).

This is the server-side client for the user's OWN LinkedIn presence. It has two
lanes, both opt-in:

  Lane 1 — DRAFT (default, the real product):
    Jarvis writes a post (or profile text) and packages it as a draft. The draft
    routes through the normal approval/drafts flow and the user copies it into
    LinkedIn by hand. NO network, NO credentials, NO automation. draft_post()
    is the whole surface — it just shapes text.

  Lane 2 — API AUTO-POST (optional, advanced, OFF):
    Bring-your-own LinkedIn Developer App + a member OAuth access token with the
    `w_member_social` scope. create_post() publishes to the user's OWN feed only,
    and MUST be routed through the approval flow at the call site — it is never
    agent-called and the server exposes no agent posting tool. This is transport
    only; it does NOT itself decide policy.

Trust model (mirrors the calendar/secret pattern):
  * We never hold a LinkedIn password. The only credential is the user's own
    member access token (BYO), which lives in .env as LINKEDIN_ACCESS_TOKEN and
    is NEVER logged, never returned in /health, never echoed in an error string.
  * The token is sent only in the Authorization header to LinkedIn's own API.
  * There is deliberately no maintainer/shared app: a bundled client secret would
    violate both the no-secret-in-ZIP rule and LinkedIn's terms, so the API lane
    is fully BYO.

Honest constraints (surfaced in the wizard, not oversold):
  * No useful READ surface — the self-serve API will not return the user's feed
    or other people's posts. Jarvis cannot "read your LinkedIn."
  * The member token is short-lived (~60 days, no long-lived PAT). The user
    re-pastes it when it expires.
  * Account/profile *creation* is NOT automated (it violates LinkedIn's User
    Agreement). "Profile text" here means Jarvis drafting copy the user pastes in
    manually — same draft lane, no automation, no scraping, no headless browser,
    no cookie automation, ever.

Capabilities, on purpose:
  * draft  — draft_post()        (no network; always available with the flag on)
  * probe  — probe_credentials() (read-only /v2/userinfo; resolves the author URN)
  * write  — create_post()       (MUST be gated by approval at the call site)
  * delete — NOT IMPLEMENTED. There is deliberately no delete/unpost function, so
            removing a post is impossible through this product by construction.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import config

# LinkedIn API surface (the user's own member context only).
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"

# Draft-lane shaping bounds — kept generous; the post body is the user's to edit.
_LENGTHS = {"short": 1, "medium": 2, "long": 4}  # rough paragraph budget


class LinkedInError(RuntimeError):
    """A LinkedIn call failed. The message never contains the access token."""


class LinkedInNotConfigured(LinkedInError):
    """The LinkedIn connector is disabled, or the API lane lacks its token/URN."""


# --- configuration access (tolerant of config not yet wiring these in) ------

def _enabled() -> bool:
    return bool(getattr(config, "LINKEDIN_ENABLED", False))


def _api_enabled() -> bool:
    return bool(getattr(config, "LINKEDIN_API_ENABLED", False))


def _client_id() -> str:
    return str(getattr(config, "LINKEDIN_CLIENT_ID", "") or "").strip()


def _client_secret() -> str:
    return str(getattr(config, "LINKEDIN_CLIENT_SECRET", "") or "").strip()


def _access_token() -> str:
    return str(getattr(config, "LINKEDIN_ACCESS_TOKEN", "") or "").strip()


def _author_urn() -> str:
    return str(getattr(config, "LINKEDIN_AUTHOR_URN", "") or "").strip()


def _timeout() -> int:
    return int(getattr(config, "LINKEDIN_TIMEOUT_SECONDS", 20))


def is_configured() -> bool:
    """Whether the connector can do anything.

    The DRAFT lane needs only the connector enabled (no credentials at all). The
    API auto-post lane additionally needs the access token AND a resolved author
    URN. So: enabled is enough for drafting; enabled + api_enabled + token + URN
    is required to actually publish."""
    if not _enabled():
        return False
    if not _api_enabled():
        return True  # draft lane: copy/paste, no credential needed
    return bool(_access_token() and _author_urn())


def safe_status() -> dict:
    """Non-secret status for /health and the setup wizard. Booleans only — never
    the token, the client secret, or the author URN value."""
    return {
        "enabled": _enabled(),
        "api_enabled": _api_enabled(),
        "client_id_configured": bool(_client_id()),
        "client_secret_configured": bool(_client_secret()),
        "token_configured": bool(_access_token()),
        "author_urn_configured": bool(_author_urn()),
        "draft_ready": bool(_enabled()),
        "api_ready": bool(_enabled() and _api_enabled() and _access_token() and _author_urn()),
        "delete_supported": False,
    }


# --- draft lane (no network; always the default path) -----------------------

def draft_post(topic: str, tone: str = "professional", length: str = "medium") -> dict:
    """Shape a LinkedIn post draft from a topic. NO network, NO credential — this
    only packages text for the draft-file / approval path. The returned dict is a
    spec the caller writes into data/drafts/; the user copies the body into
    LinkedIn by hand. (Profile-text drafting uses this same path — Jarvis never
    writes a LinkedIn profile.)"""
    topic = (topic or "").strip()
    if not topic:
        raise LinkedInError("draft_post requires a topic.")
    tone = (tone or "professional").strip().lower() or "professional"
    length = (length or "medium").strip().lower()
    if length not in _LENGTHS:
        length = "medium"
    return {
        "platform": "linkedin",
        "kind": "post_draft",
        "topic": topic,
        "tone": tone,
        "length": length,
        "paragraph_budget": _LENGTHS[length],
        # The body is filled by the agent's writing; this is the envelope the
        # approval flow displays. No network call is or will be made here.
        "instructions": (
            f"Write a {length} LinkedIn post in a {tone} tone about: {topic}. "
            "Plain text, no hashquote spam, ready for the user to copy in by hand."
        ),
    }


# --- transport (API lane only) ----------------------------------------------

def _request(method: str, url: str, token: str, body: dict | None = None,
             timeout: int | None = None) -> dict:
    """Low-level LinkedIn API call with an EXPLICIT bearer token. Raises
    LinkedInError on any failure. The token is placed in the Authorization header
    only — never in the URL, a log line, or an exception message. LinkedIn's own
    error text carries no token. Used by both probe_credentials() and the
    config-based create_post()."""
    if not token:
        raise LinkedInNotConfigured("a LinkedIn access token is required.")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": "Bearer " + token,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=(timeout or _timeout())) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
            post_id = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id")
    except urllib.error.HTTPError as e:
        # e.code/e.reason carry no token; do NOT echo the request body/headers.
        raise LinkedInError(f"LinkedIn API HTTP {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise LinkedInError(f"LinkedIn API unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        # malformed URL, socket timeout, etc. — surface without the token.
        raise LinkedInError(f"LinkedIn API request failed: {e}") from None
    parsed: dict = {}
    if raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                parsed = loaded
        except json.JSONDecodeError:
            raise LinkedInError("LinkedIn API returned a non-JSON response.") from None
    parsed.setdefault("_status", status)
    if post_id:
        parsed.setdefault("_post_id", post_id)
    return parsed


def probe_credentials(token: str, timeout: int | None = None) -> dict:
    """Validate an arbitrary member access token WITHOUT saving anything or
    touching global config — the wizard's 'Test connection' green-check. Calls
    GET /v2/userinfo (a read; never posts), which proves the token is live and
    has at least sign-in scope, and resolves the author URN (urn:li:person:{sub})
    the API lane needs. Returns the resolved URN + display name only; raises
    LinkedInError (token-free message) on failure."""
    token = (token or "").strip()
    if not token:
        raise LinkedInError("token is required.")
    res = _request("GET", _USERINFO_URL, token, body=None, timeout=timeout)
    sub = str(res.get("sub", "") or "").strip()
    if not sub:
        raise LinkedInError("LinkedIn did not return a member id for this token.")
    name = str(res.get("name", "") or "").strip()
    return {
        "ok": True,
        "author_urn": f"urn:li:person:{sub}",
        "name": name,
    }


# --- write (MUST be gated by approval at the call site) ---------------------

def create_post(text: str, author_urn: str | None = None) -> dict:
    """Publish a text post to the USER's OWN LinkedIn feed. WRITE.

    The caller is responsible for routing this through the approval flow — it is
    invoked only by an explicit operator action against an already-approved
    draft, NEVER by the agent (the server exposes no agent posting tool). This
    function is purely the transport and decides no policy.

    Requires the API lane fully configured (enabled + api_enabled + token +
    author URN). Posts with PUBLIC visibility to the member's own feed via the
    ugcPosts endpoint. There is no delete counterpart."""
    text = (text or "").strip()
    if not text:
        raise LinkedInError("create_post requires non-empty text.")
    if not (_enabled() and _api_enabled()):
        raise LinkedInNotConfigured(
            "the LinkedIn API auto-post lane is not enabled "
            "(set integrations.linkedin.enabled + api_enabled)."
        )
    token = _access_token()
    urn = (author_urn or _author_urn()).strip()
    if not token or not urn:
        raise LinkedInNotConfigured(
            "the LinkedIn API lane needs LINKEDIN_ACCESS_TOKEN (.env) and a "
            "resolved author URN (integrations.linkedin.author_urn)."
        )
    payload = {
        "author": urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    res = _request("POST", _UGC_POSTS_URL, token, body=payload)
    return {"ok": True, "post_id": res.get("_post_id") or res.get("id", "")}

# NOTE: there is intentionally no delete_post()/unpost(). Removing a post is
# unsupported by design, so it cannot happen through Jarvis.
