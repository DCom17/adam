"""Friendly-failure sentinels: usage-limit, billing (out of credit), auth.

These are the messages a nontechnical user actually sees when Claude fails —
they must trigger on the real CLI/API wordings and stay quiet on ordinary
errors (a false positive would hide the true error text).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ADAM_TOKEN", "test-token-sentinels-000000000000")

import server  # noqa: E402


def test_usage_limit_with_epoch():
    msg = server._usage_limit_message("Claude AI usage limit reached|1767225600")
    assert msg is not None
    assert "usage limit" in msg
    assert "Settings" in msg


def test_usage_limit_none_on_other_error():
    assert server._usage_limit_message("ENOENT: no such file") is None


def test_billing_on_low_credit():
    # Anthropic's real wording for an exhausted prepaid key.
    msg = server._billing_message(
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits.")
    assert msg is not None
    assert "console.anthropic.com" in msg
    assert "Settings" in msg


def test_billing_none_on_other_error():
    assert server._billing_message("Claude timed out") is None
    assert server._billing_message("") is None
    assert server._billing_message(None) is None


def test_auth_failure_unaffected():
    assert server._is_claude_auth_failure("please run /login") is True
    assert server._is_claude_auth_failure("some random crash") is False
