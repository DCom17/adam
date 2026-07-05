"""
Adam — setup doctor (re-runnable health check).

    python scripts/doctor.py

Prints plain-language PASS / WARN / FAIL for the things a fresh install needs:
token, Claude resolution, settings/defaults, draft_only, vault_path, write dirs,
protected patterns, state dir, port/reachability, dependencies, and that /health
leaks no secrets. Read-only. Exit code 0 if no FAIL, 1 if any FAIL.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import onboarding  # noqa: E402


def main() -> int:
    print("\nAdam — setup doctor\n" + "=" * 36)
    checks = onboarding.run_doctor()
    for c in checks:
        print(f"  {c['status']:4}  {c['name']}: {c['detail']}")
    print("=" * 36)
    n_fail = sum(1 for c in checks if c["status"] == "FAIL")
    n_warn = sum(1 for c in checks if c["status"] == "WARN")
    print(f"  {len(checks)} checks · {n_fail} FAIL · {n_warn} WARN\n")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
