"""Native pytest coverage for the shared secret scrubber (security.redact_secrets).

The Claude CLI relays whatever it printed on stderr into our job errors, and
those strings reach BOTH voice_server.log and the client's 502 body. A rejected
API key is echoed back verbatim by the CLI, so that path has to be scrubbed at
the source. These tests pin:

- exact configured values are removed, longest-first;
- secret-SHAPED strings this install was never told about are removed too;
- the scrub happens inside _claude_failure_text, so every current and future
  call site inherits it;
- scrubbing does NOT eat the English failure markers the recovery ladder
  matches on (a scrub that broke "Please run /login" would trade a leak for a
  broken login prompt).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Sandbox config BEFORE anything imports it: clean example defaults, no live
# settings.json/.env from the dev machine.
os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_redact_cfg_"))

import config  # noqa: E402

TOKEN = "redact-test-token-" + "y" * 32
if not config.ADAM_TOKEN:
    config.ADAM_TOKEN = TOKEN
else:  # pragma: no cover — sandboxed config should have no token
    TOKEN = config.ADAM_TOKEN
if not config.CLAUDE_EXE:
    config.CLAUDE_EXE = sys.executable

import server  # noqa: E402
from security import redact_secrets  # noqa: E402


def test_scrubs_configured_bearer_token():
    out = redact_secrets(f"boom: {config.ADAM_TOKEN} was rejected")
    assert config.ADAM_TOKEN not in out
    assert "***" in out


def test_scrubs_anthropic_key_shape_not_in_config():
    """The key that gets echoed back is usually the WRONG one — a typo, or a
    stale key from another machine. It is never in config, so the exact-value
    pass cannot catch it and the shape pass has to."""
    stray = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8" * 2
    out = redact_secrets(f"Invalid API key: {stray}")
    assert stray not in out
    assert "***" in out


def test_scrubs_other_vendor_key_shapes():
    for stray in (
        "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2,
        "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
        "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
    ):
        out = redact_secrets(f"leaked {stray} here")
        assert stray not in out, f"{stray[:12]}... survived the scrub"


def test_empty_and_non_string_input_are_safe():
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None
    assert "***" not in redact_secrets("nothing secret here")


def test_claude_failure_text_scrubs_stderr():
    """The real leak path: a key on the CLI's stderr becoming a 502 body."""
    stray = "sk-ant-api03-" + "Z9y8X7w6V5u4T3s2" * 2
    err = server._claude_failure_text("", f"API error: invalid x-api-key {stray}")
    assert stray not in err
    assert "***" in err


def test_claude_failure_text_scrubs_stdout_json_result():
    """The not-signed-in path puts its reason in stdout JSON, not stderr."""
    stray = "sk-ant-api03-" + "Q1w2E3r4T5y6U7i8" * 2
    payload = '{"is_error": true, "result": "auth failed for %s"}' % stray
    err = server._claude_failure_text(payload, "")
    assert stray not in err
    assert "***" in err


def test_scrub_preserves_recovery_markers():
    """Scrubbing must not break the failure ladder's phrase matching."""
    err = server._claude_failure_text(
        '{"is_error": true, "result": "Not logged in \\u00b7 Please run /login"}', ""
    )
    assert "Please run /login" in err
    assert server._is_claude_auth_failure(err)

    gone = server._claude_failure_text("", "No conversation found with session id abc123")
    assert server._is_session_not_found(gone)


def test_longest_secret_wins_no_readable_tail():
    """A short secret that is a substring of a longer one must not leave the
    longer one's tail readable in the output."""
    config.HUNTER_TOKEN = "abcd1234"
    config.CALENDAR_TOKEN = "abcd1234EXTRATAIL5678"
    try:
        out = redact_secrets("value=abcd1234EXTRATAIL5678 end")
        assert "EXTRATAIL" not in out
    finally:
        config.HUNTER_TOKEN = ""
        config.CALENDAR_TOKEN = ""
