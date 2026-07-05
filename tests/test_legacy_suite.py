"""
Pytest bridge for the legacy standalone test scripts.

Every test_*.py at the repo root is a self-contained script with the contract
``python test_x.py`` -> exit code 0 = all checks passed. This module runs each
one as its own subprocess so pytest gives a single command (``pytest``) that
runs the whole suite, without rewriting the scripts or letting their global
config mutations bleed into each other.

New tests should be written as native pytest tests in this directory instead
of adding more root scripts.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Scripts that need anything beyond "python script.py, exit 0" get an entry
# here; everything else is discovered automatically.
PER_SCRIPT_TIMEOUT = 300  # seconds; generous, the slowest (release build) needs it

LEGACY_SCRIPTS = sorted(p.name for p in REPO_ROOT.glob("test_*.py"))


def test_legacy_scripts_discovered() -> None:
    """The glob finding nothing would silently pass the suite — fail loudly."""
    assert len(LEGACY_SCRIPTS) >= 30, f"expected the legacy suite, found {LEGACY_SCRIPTS}"


@pytest.mark.parametrize("script", LEGACY_SCRIPTS)
def test_legacy(script: str, tmp_path: Path) -> None:
    # Point config at an empty dir so every script sees the shipped example
    # defaults, never this machine's live settings.json / .env. Scripts that
    # need specific values already stub config after import.
    env = dict(os.environ)
    env["ADAM_CONFIG_ROOT"] = str(tmp_path)
    env.pop("ADAM_TOKEN", None)

    proc = subprocess.run(
        [sys.executable, script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PER_SCRIPT_TIMEOUT,
        env=env,
    )
    if proc.returncode != 0 and "brain bundle guard tripped" in (
        proc.stdout + proc.stderr
    ):
        # The release builder refuses to ship a brain/ carrying personal
        # content. On a developer machine with a lived-in vault that is the
        # guard working, not a product bug; a clean checkout (CI) must pass.
        pytest.skip(f"{script}: local brain/ has personal content (release guard)")
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.splitlines()[-40:])
        pytest.fail(
            f"{script} exited {proc.returncode}\n"
            f"--- stdout (tail) ---\n{tail}\n"
            f"--- stderr ---\n{proc.stderr[-2000:]}",
            pytrace=False,
        )
