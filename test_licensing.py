"""
Adam — licensing / sell-mode switch tests.

Self-contained (no network, no private signing key, no repo state touched):
monkeypatches licensing._configured / is_licensed and the trial-file path, and
toggles the ADAM_SELL_MODE env var, to prove:

    key-present != selling · enforcement is OPT-IN and defaults OFF · a
    configured-but-dormant beta build is fully entitled and never starts the
    trial clock · ADAM_SELL_MODE=1 arms the trial->paywall · an active trial is
    entitled while an expired one is not · a valid license is entitled even past
    the trial · an explicit ADAM_SELL_MODE=0 is a kill switch over
    SELL_MODE_DEFAULT=True · an unconfigured build is always entitled · and the
    SHIPPED build keeps a real public key committed (dormancy is controlled by the
    switch, not by reverting the key) · and an ARMED build always gives a gated
    user a real checkout to buy from.

Run:  python test_licensing.py   (exit code 0 = all passed)
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import licensing

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def _set_sell(val) -> None:
    """Set (or, with None, clear) the ADAM_SELL_MODE env var."""
    if val is None:
        os.environ.pop("ADAM_SELL_MODE", None)
    else:
        os.environ["ADAM_SELL_MODE"] = val


def main() -> int:
    # Isolate: never touch the repo's real data/state/trial_start.txt, start from
    # a clean env, and drive is_licensed explicitly so we never depend on the
    # private key or any stored license. The switch logic is tested against a
    # simulated "key embedded" (_configured -> True) so it holds regardless of the
    # committed key; [7]/[8] then check the real module state.
    tmp = Path(tempfile.mkdtemp(prefix="adam_lic_test_"))
    _real_configured = licensing._configured
    _real_is_licensed = licensing.is_licensed
    _real_trial_file = licensing.TRIAL_FILE
    _real_default = licensing.SELL_MODE_DEFAULT

    licensing.TRIAL_FILE = tmp / "trial_start.txt"
    licensing.is_licensed = lambda: False       # no license unless a step says otherwise
    licensing.SELL_MODE_DEFAULT = False
    _set_sell(None)

    try:
        licensing._configured = lambda: True     # pretend a real public key is embedded

        print("\n[1] Key present but selling OFF by default -> dormant")
        _set_sell(None)
        check("_sell_mode() False by default", licensing._sell_mode() is False)
        check("is_selling() False (key present, switch off)", licensing.is_selling() is False)
        check("is_entitled() True while dormant (no paywall)", licensing.is_entitled() is True)
        ts = licensing.trial_status()
        check("trial_status inert while dormant (full trial, not started)",
              ts["in_trial"] is True and ts["days_left"] == licensing.TRIAL_DAYS and ts["started"] == "")
        check("dormant build never starts the trial clock (no file written)",
              not licensing.TRIAL_FILE.exists())

        print("\n[2] ADAM_SELL_MODE=1 arms selling; a fresh trial is active")
        _set_sell("1")
        check("_sell_mode() True when env=1", licensing._sell_mode() is True)
        check("is_selling() True (key present + switch on)", licensing.is_selling() is True)
        check("is_entitled() True during an active trial", licensing.is_entitled() is True)
        check("arming + entitlement started the trial clock", licensing.TRIAL_FILE.exists())

        print("\n[3] Selling ON, trial EXPIRED, no license -> paywall (not entitled)")
        expired = (date.today() - timedelta(days=licensing.TRIAL_DAYS + 5)).isoformat()
        licensing.TRIAL_FILE.write_text(expired, encoding="utf-8")
        check("trial reports ended", licensing.trial_status()["in_trial"] is False)
        check("is_entitled() False past the trial with no license", licensing.is_entitled() is False)

        print("\n[4] A valid license is entitled even past the trial")
        licensing.is_licensed = lambda: True
        check("is_entitled() True with a valid license (trial irrelevant)", licensing.is_entitled() is True)
        licensing.is_licensed = lambda: False

        print("\n[5] The env value is a two-way override of the compiled default")
        licensing.SELL_MODE_DEFAULT = True
        _set_sell(None)
        check("default True -> selling on when env unset", licensing.is_selling() is True)
        _set_sell("0")
        check("ADAM_SELL_MODE=0 kills selling even if the default is True", licensing._sell_mode() is False)
        check("is_selling() False under the kill switch", licensing.is_selling() is False)
        check("is_entitled() True under the kill switch", licensing.is_entitled() is True)
        licensing.SELL_MODE_DEFAULT = False
        _set_sell(None)

        print("\n[6] Accepted truthy / falsey spellings")
        for v in ("1", "true", "TRUE", "on", "Yes", "  yes  "):
            _set_sell(v)
            check(f"ADAM_SELL_MODE={v!r} -> on", licensing._sell_mode() is True)
        for v in ("0", "false", "off", "no", "", "garbage"):
            _set_sell(v)
            check(f"ADAM_SELL_MODE={v!r} -> off", licensing._sell_mode() is False)
        _set_sell(None)

        print("\n[7] An unconfigured build is always entitled, regardless of the switch")
        licensing._configured = lambda: False
        _set_sell("1")
        check("is_selling() False with no key even when the switch is on", licensing.is_selling() is False)
        check("is_entitled() True on an unconfigured build", licensing.is_entitled() is True)
        _set_sell(None)
        licensing._configured = _real_configured

        print("\n[8] The SHIPPED build keeps a real key committed (dormant via the switch, not a reverted key)")
        check("LICENSE_PUBLIC_KEY_HEX is not the placeholder",
              _real_configured() is True and licensing.LICENSE_PUBLIC_KEY_HEX != "REPLACE_WITH_YOUR_PUBLIC_KEY")
        # The old assertion here was "SELL_MODE_DEFAULT is False". That was the right
        # invariant while nothing was for sale: arming a paywall with no checkout traps
        # a user with no way out. Adam Plus went on sale 2026-08-11 (Gumroad), so the
        # invariant tightens rather than disappears — an ARMED build must be one a gated
        # user can actually buy and redeem from. ADAM_SELL_MODE=0 is still the kill
        # switch, proven in section [6] above.
        if _real_default is True:
            check("armed build has a real signing key configured", _real_configured() is True)
            check("armed build sends gated users to a real checkout",
                  isinstance(licensing.BUY_URL, str) and licensing.BUY_URL.startswith("https://"))
        else:
            check("dormant build never enforces", licensing._sell_mode() is False)

    finally:
        licensing._configured = _real_configured
        licensing.is_licensed = _real_is_licensed
        licensing.TRIAL_FILE = _real_trial_file
        licensing.SELL_MODE_DEFAULT = _real_default
        _set_sell(None)
        try:
            for p in tmp.glob("*"):
                p.unlink()
            tmp.rmdir()
        except OSError:
            pass

    print(f"\n{'=' * 48}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'=' * 48}\n")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
