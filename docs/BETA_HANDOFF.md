# Beta Handoff (v0.9.0 — friends & family)

Welcome, and thanks for testing. This page is the 5-minute orientation for a beta
tester. It tells you what you need, what to try first, what *not* to expect yet, and
how to report problems. For the full setup details see the [README](../README.md),
[Connect your phone](CONNECT_YOUR_PHONE.md), and [Support & scope](SUPPORT.md).

## What this is, in one line

Jarvis Voice Local is a small assistant **backend you run on your own Windows PC**,
driven by **your own Claude Code**, against **your own files** — with a phone web app
(PWA) and a desktop operator console on top. Nothing is hosted by anyone else; the
maintainer never sees your data, your files, or your keys.

## Before you start (checklist)

- **Windows 10 or 11.**
- **Claude Code already installed** and working (`claude` runs from a terminal).
- **Python 3** installed and on `PATH`.
- About 15 minutes for first setup.
- *(Only if you want phone access)* a **Tailscale** account (free) — see below.

## First-time setup (the short version)

```powershell
python -m pip install -r requirements.txt
python scripts/setup.py      # generates your token, finds Claude, sets your files path
.\scripts\copy-token.ps1     # copies your JARVIS_TOKEN to the clipboard (no .env editing)
python scripts/doctor.py     # health check — aim for 0 FAIL
.\scripts\start-jarvis.ps1   # one-click launcher: starts the server + opens your browser
```

`start-jarvis.ps1` is the easy way: it checks Python, reminds you to run `setup.py`
first if needed, starts the server in a **visible** window (nothing runs hidden — no
service, no autostart), opens the app, and won't start a duplicate if it's already
running. (The manual equivalent is `.\scripts\start-dev.ps1`.)

The launcher opens **`http://localhost:8010`** on the **same PC** (check the port in
`/health` or the launcher output if it differs). On the PC, `localhost` is a secure
context, so **the desktop is the best-supported voice loop** (the mic works with zero
networking — see the voice note below for what the spoken reply sounds like).

**Signing in (no manual `.env` editing):** run `.\scripts\copy-token.ps1` and it copies
your `JARVIS_TOKEN` to the clipboard — then just **paste** it into the app's settings
field. (The helper never prints the token; it shows only a masked hint so you can confirm
it. If you'd rather, the token is the `JARVIS_TOKEN=` line in `.env`.) To stop: close the
server window or run `.\scripts\stop-dev.ps1`.

If anything looks wrong, run `python scripts/doctor.py` — it gives plain-language
PASS / WARN / FAIL guidance.

## What to test first

1. **Desktop voice loop** — open `http://localhost:8010`, sign in, Activate, speak,
   confirm you get a transcript and a spoken reply. *(The spoken reply uses your
   browser's built-in voice in this beta — see "About the voice" below.)*
2. **Operator console** — open `http://localhost:8010/console`, sign in, look at
   Status / Jobs / Proposed changes / Approvals / Audit.
3. **A proposed change** — ask the assistant (in work mode) to create or edit a small
   file; confirm it shows up as a **proposed change** you review and **Approve & Apply**
   — it does **not** edit your files on its own (that's `draft_only`, see below).
4. *(Optional)* **Phone access** — set up Tailscale (next section) and repeat the voice
   test from your iPhone.

## About the voice (please read, set your expectations)

In this beta, **spoken replies use your device's built-in (browser/OS) text-to-speech** —
a generic, somewhat robotic default voice. **That is expected, not a bug.** Both the
desktop and the phone speak with this fallback voice.

- This release **does not include a high-quality local voice backend.** The app can
  *optionally* speak through a local TTS service (e.g. Kokoro on `http://127.0.0.1:8001`)
  **if you run one yourself**, but none is bundled and none is required.
- The maintainer's own polished "Jarvis voice" runs on a separate personal voice backend
  that is **not part of this ZIP** and is **not** something you receive here.
- The **desktop is the best-supported voice loop** (the mic works on `localhost` with no
  setup) — but "best-supported" means the *loop* is most reliable there, **not** that you
  get a nicer voice. The spoken output is still the browser/default voice on both desktop
  and phone.
- A first-class, real Jarvis voice is a **final-product goal**, not a v0.9.0 beta feature.

So: judge the assistant's *answers, speed, and the propose→approve flow* in this beta. The
robotic voice is the placeholder — don't read it as broken.

## Phone access: use Tailscale (and why)

iPhone Safari only allows the **microphone, voice, and Add-to-Home-Screen** on a secure
(`https://`) page. Plain `http://<your-pc-ip>` on the phone is **not** secure — the mic
silently does nothing and the app shows an **insecure-connection banner**. **Tailscale
Serve** gives your PC a real HTTPS address on your private network, which fixes this
**without exposing anything to the public internet**. Full steps:
[Connect your phone](CONNECT_YOUR_PHONE.md).

**Please don't put the backend on the public internet** to get phone access. The
supported, safe path is Tailscale. (There is an advanced public option via cloudflared,
but it **requires Cloudflare Access** and you own that risk — see
[Advanced remote access](ADVANCED_REMOTE.md). Most testers should just use Tailscale.)

## What "draft_only" means (plain English)

The assistant runs in **draft_only** mode by default. It can **read** your files and
**propose** changes, but it **cannot write to your files directly**. Every file change
becomes a **proposed change** that you review (with a diff) and explicitly approve and
apply. The server — not the assistant — is the only thing that writes, and it re-checks
permissions, makes a backup, and logs an audit entry on every apply. You are always in
the loop.

## What NOT to expect yet (v0.9.0)

- **No installer, tray app, or auto-start.** You run it from a terminal; updates are
  manual (download a new ZIP — see [RELEASE](RELEASE.md)).
- **No full phone-pairing wizard.** Tailscale setup is still the manual runbook. (There
  *is* a convenience: once you're on the Tailscale HTTPS URL, the desktop console can show
  **two local QRs — one for the URL, one for the token** — so your phone Camera opens
  Jarvis and copies the token without retyping it (see
  [Connect your phone](CONNECT_YOUR_PHONE.md)). It's a handoff, not auto-pairing.)
- **A polished "real Jarvis voice."** Spoken replies use the browser/default
  text-to-speech (robotic) — see [About the voice](#about-the-voice-please-read-set-your-expectations).
  A bundled high-quality voice is a final-product goal, not in this ZIP.
- **Single user, single token.** No accounts, no per-device tokens.
- **Windows only.** macOS/Linux are not supported; Android isn't validated; smart
  glasses aren't supported.
- **A polished non-technical experience.** That's the v1.0 goal. v0.9.0 is for
  prosumer + friends/family who don't mind a setup guide.

## Where the app comes from / updating

You received a **release ZIP** (e.g. `jarvis-voice-local-v0.9.0.zip`). It contains the
app, templates (`.env.example`, `settings.example.json`), and docs — but **never** real
secrets or anyone's data. To update later: download the new ZIP, unzip it, and re-run
`python scripts/doctor.py`. Your `.env`, `settings.json`, and `data/` are never inside
the ZIP, so updating can't overwrite them. (Maintainers: see [RELEASE](RELEASE.md) for
how the ZIP is built.)

## If something fails — what to report

Run `python scripts/doctor.py` first; it often names the fix. If you still need to
report, include:

- **What you did** and **what happened** (and what you expected).
- The **doctor output** (it prints no secrets — safe to share).
- Whether it was on the **PC (localhost)** or the **phone (Tailscale)**.
- The relevant tail of **`data/logs/voice_server.log`** (scan it for anything sensitive
  first — but it should not contain your token).
- Whether the **insecure-connection banner** was showing (that means you're on http,
  not the HTTPS Tailscale URL).

**Never share your `.env`, your `JARVIS_TOKEN`, or your `settings.json`.** No support
question requires them.

## Safety recap

- `draft_only` is on; the server is the sole writer; changes go through
  review → approve → apply with permission checks, backups, and an audit log.
- Your token, keys, and files stay on your machine.
- The bearer-token gate is the lock on the API — keep the token long, random, and
  private, and don't expose the port to the open internet (use Tailscale).
