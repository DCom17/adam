"""
Adam — voicemail answer provisioning (the "how Twilio answers" side).

The voicemail add-on polls Twilio for recordings (twilio_voicemail.py), but SOMETHING
has to make Twilio *answer* a forwarded call and record a message in the first place.
A live call can't be answered by polling — Twilio has to fetch an answer instruction
the moment the call lands. With this product's no-public-ingress posture (no tunnel,
no public URL), that instruction can't live on the user's machine, so it lives on
Twilio: a tiny TwiML document the user pastes into a Twilio "TwiML Bin", exactly the
way the Calendar/Email add-ons have the user deploy a Google Apps Script bridge.

This module owns three jobs:

  1. build_answer_twiml()  — generate the exact TwiML the user pastes into a Bin.
     Default greeting voice is Amazon Polly's British male neural voice (Polly.Brian),
     which Twilio renders server-side with NO audio hosting — a deep British voice
     that always works. If the user hosts a Adam/Kokoro greeting clip somewhere
     publicly reachable, the TwiML <Play>s that instead (the voice upgrade).

  2. set_number_voice_url() — the automatable slice. Once the user has a Bin URL,
     this wires their Twilio number's Voice webhook to it via the REST API, so the
     wizard offers a one-click "wire my number" instead of another console hunt.

  3. render_greeting_clip() — render the greeting in Adam's own Kokoro voice
     (bm_daniel, deep British male) via the local tts_server, so the user can hear
     and save the upgrade greeting. Hosting it publicly stays the user's choice; the
     shipped default remains the no-hosting Polly voice.

Secret hygiene mirrors the rest of the add-on layer: the Auth Token rides only in
the Authorization header, is never logged, and is scrubbed from any error string.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape as _xml_escape

import config

_API_ROOT = "https://api.twilio.com/2010-04-01"

# Amazon Polly British male neural voice — Twilio renders it server-side, so the
# default greeting needs no audio hosting at all. This is the "deep British voice
# default fallback": even with no Kokoro clip hosted, the caller hears a British man.
POLLY_BRITISH_MALE = "Polly.Brian-Neural"

# Default spoken greeting. Deliberately generic + caller-instructing so one wording
# works for everyone; the owner's name (if set) personalizes it.
DEFAULT_GREETING = (
    "Hello, you've reached {owner}'s assistant. {owner} can't take the call right "
    "now, but if you leave your name, number, and a short message after the tone, "
    "I'll pass it along."
)

# How long a caller may record, in seconds. Twilio's transcription is best under two
# minutes, so this doubles as the transcription-friendly cap.
DEFAULT_RECORD_MAX_SECONDS = 120


class ProvisionError(RuntimeError):
    """A provisioning API call failed. The message never contains the Auth Token."""


# --- config access ----------------------------------------------------------

def _owner_name() -> str:
    return str(getattr(config, "OWNER_NAME", "") or "").strip() or "the owner"


def _greeting_text() -> str:
    custom = str(getattr(config, "VOICEMAIL_GREETING_TEXT", "") or "").strip()
    if custom:
        return custom
    return DEFAULT_GREETING.format(owner=_owner_name())


def _tts_url() -> str:
    return str(getattr(config, "TTS_URL", "http://127.0.0.1:8001/tts") or "").strip()


def _basic_auth(account_sid: str, auth_token: str) -> str:
    raw = f"{account_sid}:{auth_token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# --- 1. the answer TwiML ----------------------------------------------------

def build_answer_twiml(greeting_text: str | None = None, *,
                       play_url: str | None = None,
                       record_max_seconds: int | None = None,
                       voice: str = POLLY_BRITISH_MALE) -> str:
    """The exact TwiML the user pastes into a Twilio TwiML Bin (or that an automated
    deploy would serve). Plays the greeting, records the caller (with Twilio
    transcription on), and hangs up — no statusCallback, because the recording is
    pulled by polling, not pushed to a webhook.

    greeting_text — what's spoken (defaults to the configured/owner greeting).
    play_url      — if given, <Play> this hosted audio (the Kokoro upgrade) INSTEAD
                    of <Say>; the greeting text is ignored. Must be publicly reachable
                    by Twilio.
    voice         — the <Say> voice when no play_url (default: Polly British male).
    """
    text = (greeting_text if greeting_text is not None else _greeting_text()).strip()
    rec_max = int(record_max_seconds or DEFAULT_RECORD_MAX_SECONDS)

    if play_url:
        greet_el = f"  <Play>{_xml_escape(play_url.strip())}</Play>"
    else:
        greet_el = f'  <Say voice="{_xml_escape(voice)}">{_xml_escape(text)}</Say>'

    # transcribe="true" with NO transcribeCallback → Twilio still creates the
    # Transcription resource, which the poller fetches. playBeep gives the caller the
    # familiar tone. No <Gather>, no second leg — one-shot voicemail.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"{greet_el}\n"
        f'  <Record maxLength="{rec_max}" playBeep="true" transcribe="true" '
        'trim="trim-silence"/>\n'
        "  <Hangup/>\n"
        "</Response>\n"
    )


# --- 2. wire the number's Voice webhook (the automatable button) ------------

def set_number_voice_url(account_sid: str, auth_token: str, pn_sid: str,
                         voice_url: str, *, timeout: int = 20) -> dict:
    """Point the Twilio number's Voice webhook at `voice_url` (the user's TwiML Bin
    URL) via the IncomingPhoneNumbers API. This is the one-click 'wire my number'
    the wizard offers once the user has pasted their Bin. Returns {ok, voice_url};
    raises ProvisionError (token-free) on failure.

    pn_sid is the IncomingPhoneNumber SID (PN…) — twilio_voicemail.incoming_number_sid
    resolves it from the number during the wizard's Test step."""
    account_sid = (account_sid or "").strip()
    pn_sid = (pn_sid or "").strip()
    voice_url = (voice_url or "").strip()
    if not pn_sid:
        raise ProvisionError("Could not find the Twilio number on this account.")
    if not (voice_url.startswith("http://") or voice_url.startswith("https://")):
        raise ProvisionError("The voice webhook URL must start with https://.")

    url = (f"{_API_ROOT}/Accounts/{urllib.parse.quote(account_sid)}"
           f"/IncomingPhoneNumbers/{urllib.parse.quote(pn_sid)}.json")
    body = urllib.parse.urlencode({"VoiceUrl": voice_url, "VoiceMethod": "POST"}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": _basic_auth(account_sid, auth_token),
                 "Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        hint = " (check your Account SID and Auth Token)" if e.code in (401, 403) else ""
        raise ProvisionError(f"Twilio API HTTP {e.code}: {e.reason}{hint}") from None
    except urllib.error.URLError as e:
        raise ProvisionError(f"Twilio API unreachable: {e.reason}") from None
    except (ValueError, OSError) as e:
        raise ProvisionError(f"Twilio API request failed: {e}") from None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ProvisionError("Twilio API returned a non-JSON response.") from None
    return {"ok": True, "voice_url": str(parsed.get("voice_url", voice_url) or voice_url)}


# --- 3. render the Kokoro greeting (the voice upgrade) ----------------------

def render_greeting_clip(text: str | None = None, *, voice: str = "bm_daniel",
                         timeout: int = 30) -> bytes | None:
    """Render the greeting in Adam's own Kokoro voice (bm_daniel, deep British
    male) via the local tts_server. Returns WAV bytes, or None on any failure (the
    caller keeps the no-hosting Polly default). Dependency-free (urllib)."""
    text = (text if text is not None else _greeting_text()).strip()
    if not text:
        return None
    payload = json.dumps({"text": text, "voice": voice, "speed": 1.0}).encode("utf-8")
    req = urllib.request.Request(
        _tts_url(), data=payload,
        headers={"Content-Type": "application/json", "Accept": "audio/wav"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            data = r.read()
            return data or None
    except (urllib.error.URLError, OSError, ValueError):
        return None
