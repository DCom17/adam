"""audit.jsonl is size-bounded.

It records a line per write, per policy decision and per approval. voice_server.log
has rotated since it existed (2 MiB x 5); this file never did, so on a long-lived
install it grew without limit. Rotation must also stay best-effort — a
housekeeping failure can never be allowed to drop the event or fail the action
that produced it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ADAM_CONFIG_ROOT", tempfile.mkdtemp(prefix="jvl_audit_cfg_"))

import config  # noqa: E402
import permissions  # noqa: E402


def _sandbox(tmp_path: Path) -> Path:
    log = tmp_path / "audit.jsonl"
    config.AUDIT_LOG_FILE = log
    config.PERM_AUDIT_LOG_ENABLED = True
    return log


def test_event_is_appended_as_one_json_line(tmp_path: Path):
    log = _sandbox(tmp_path)
    permissions.record_audit_event({"action_type": "unit_test", "target": "x"})
    lines = log.read_text("utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["action_type"] == "unit_test"
    assert rec["ts"]  # stamped automatically


def test_rotation_rolls_at_the_cap_and_keeps_a_bounded_set(tmp_path: Path):
    log = _sandbox(tmp_path)
    real_cap = permissions.AUDIT_MAX_BYTES
    permissions.AUDIT_MAX_BYTES = 2048
    try:
        for i in range(400):
            permissions.record_audit_event({"action_type": "spam", "i": i,
                                            "pad": "x" * 100})
        gens = sorted(p.name for p in tmp_path.glob("audit.jsonl*"))
        # Current file plus at most AUDIT_BACKUPS generations, and nothing beyond.
        assert "audit.jsonl" in gens
        assert len(gens) <= permissions.AUDIT_BACKUPS + 1, gens
        assert f"audit.jsonl.{permissions.AUDIT_BACKUPS + 1}" not in gens
        # Every retained generation is still valid JSONL — rotation never splits a line.
        for name in gens:
            for ln in (tmp_path / name).read_text("utf-8").strip().splitlines():
                json.loads(ln)
    finally:
        permissions.AUDIT_MAX_BYTES = real_cap


def test_the_newest_events_survive_rotation(tmp_path: Path):
    log = _sandbox(tmp_path)
    real_cap = permissions.AUDIT_MAX_BYTES
    permissions.AUDIT_MAX_BYTES = 1024
    try:
        for i in range(200):
            permissions.record_audit_event({"action_type": "spam", "i": i, "pad": "y" * 80})
        permissions.record_audit_event({"action_type": "the_last_one"})
        assert "the_last_one" in log.read_text("utf-8")
    finally:
        permissions.AUDIT_MAX_BYTES = real_cap


def test_rotation_failure_never_loses_the_event(tmp_path: Path):
    """If rolling breaks, the line still has to be written."""
    log = _sandbox(tmp_path)
    real = permissions._rotate_audit_if_needed

    def boom():
        raise OSError("rotation exploded")

    permissions._rotate_audit_if_needed = boom
    try:
        permissions.record_audit_event({"action_type": "still_recorded"})
    finally:
        permissions._rotate_audit_if_needed = real
    assert "still_recorded" in log.read_text("utf-8")


def test_disabled_audit_writes_nothing(tmp_path: Path):
    log = _sandbox(tmp_path)
    config.PERM_AUDIT_LOG_ENABLED = False
    try:
        permissions.record_audit_event({"action_type": "should_not_appear"})
        assert not log.exists()
    finally:
        config.PERM_AUDIT_LOG_ENABLED = True
