"""
Jarvis Voice Local — voicemail message store + the relay record.

Each finalized voicemail produces one record appended to a JSONL log under the
app's state dir. The record is what gets pushed to the phone and what /voicemails
lists back. Storage is append-only JSONL — simple, durable, easy to tail.

Harvested from the standalone jarvis-call-relay (app/messages.py) and adapted to
Voice Local's config paths. No secret ever lands here — just caller number, name,
duration, transcript, and timestamps.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config

log = logging.getLogger("jarvis.voicemail")


def _store_file() -> Path:
    base = getattr(config, "STATE_DIR", None) or "."
    return Path(base) / "voicemails.jsonl"


@dataclass
class Voicemail:
    recording_sid: str
    call_sid: str
    from_number: str
    received_at: str                 # ISO (Twilio recording date_created)
    duration_seconds: int = 0
    caller_name: str = ""            # filled from the known-callers list if matched
    transcript: str = ""
    transcribed: bool = False        # False = transcription unavailable/timed out
    delivered: bool = False

    def who(self) -> str:
        return self.caller_name or self.from_number or "Unknown caller"

    def summary_line(self) -> str:
        """One-line banner for the push notification."""
        who = self.who()
        if self.transcript:
            snippet = self.transcript.strip()
            if len(snippet) > 140:
                snippet = snippet[:139].rstrip() + "…"
            return f"Voicemail from {who}: {snippet}"
        if self.transcribed:
            return f"Voicemail from {who} (no words transcribed)."
        return f"Voicemail from {who} — couldn't transcribe it; listen in Twilio."


def save(vm: Voicemail) -> None:
    path = _store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(vm), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    log.info("saved voicemail %s from %s", vm.recording_sid, vm.who())


def load_all() -> list[dict]:
    path = _store_file()
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
