"""
Adam — known-callers list for voicemail.

Maps phone numbers to names so a voicemail push can say "Voicemail from Mom" instead
of a bare +1 number. Optional: with no file, callers are shown by number.

Source: data/voicemail_contacts.json (under the app's DATA_DIR). Either form works:
  {"+19285551234": "Maria", "+16025559876": "Mom"}
or:
  [{"number": "+19285551234", "name": "Maria"}, ...]

Numbers match forgivingly on their last 10 digits, so +1/area-code/format differences
between the list and Twilio's caller-ID don't matter. Loaded fresh on each lookup, so
the file can be edited without a restart.

Harvested from the standalone adam-call-relay (app/contacts.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import config


def _digits(s) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())


def _key(num) -> str:
    d = _digits(num)
    return d[-10:] if len(d) >= 10 else d


def _path() -> Path:
    base = getattr(config, "DATA_DIR", None) or "."
    return Path(base) / "voicemail_contacts.json"


def load() -> dict[str, str]:
    try:
        # utf-8-sig tolerates a BOM that Notepad may add when you edit the file.
        data = json.loads(_path().read_text("utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = _key(k)
            if key:
                out[key] = str(v).strip()
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                key = _key(item.get("number"))
                if key:
                    out[key] = str(item.get("name", "")).strip()
    return out


def lookup(number: str) -> str:
    """Name for a caller number, or '' if unknown."""
    return load().get(_key(number), "")
