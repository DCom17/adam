# Beta Handoff (v0.9.x — friends & family)

Welcome, and thanks for testing. This page is the 5-minute orientation for a beta
tester. It tells you what you need, what to try first, what *not* to expect yet, and
how to report problems. For the full setup details see the [README](../README.md),
[Connect your phone](CONNECT_YOUR_PHONE.md), and [Support & scope](SUPPORT.md).

## What this is, in one line

Adam is a small assistant **backend you run on your own Windows PC**,
driven by **your own Claude Code**, against **your own files** — with a phone web app
(PWA) and a desktop operator console on top. Nothing is hosted by anyone else; the
maintainer never sees your data, your files, or your keys.

## Before you start (checklist)

- **Windows 10 or 11.**
- A **Claude account on a paid plan** (about $20/month for Claude Pro), or an
  Anthropic API key for pay-as-you-go — setup lets you pick either.
- About 15 minutes for first setup. You do **not** need Python or Claude Code
  pre-installed — the setup wizard installs both if they're missing.
- *(Only if you want phone access)* a **Tailscale** account (free) — see below.

## First-time setup (the short version)

**Double-click `SETUP.cmd`** in the extracted folder (or open `START_HERE.txt` first —
it walks you through everything). The wizard installs Python and Claude Code if they're
missing, walks you through the one-time Claude sign-in, lets you pick your AI plan
(Claude subscription or pay-as-you-go API key), configures everything, adds a desktop
icon, and opens Adam in your browser **already signed in** — no token to copy. To
open Adam again later, use the desktop icon or double-click `START.cmd`.

The server runs in a **visible** black window titled "Adam" (nothing runs
hidden — no service, no autostart). **Minimize it, don't close it** — closing it turns
Adam off. To stop on purpose: close that window or run `.\scripts\stop-dev.ps1`.

The app opens at **`http://localhost:8000`** on the **same PC** (check the port in
`/health` or the launcher output if it differs). On the PC, `localhost` is a secure
context, so **the desktop is the best-supported voice loop** (the mic works with zero
networking — see the voice note below for what the spoken reply sounds like).

<details>
<summary>Manual setup (if you'd rather use a terminal)</summary>

```powershell
python -m pip install -r requirements.txt
python scripts/setup.py      # generates your token, finds Claude, sets your files path
python scripts/doctor.py     # health check — aim for 0 FAIL
.\scripts\start-adam.ps1   # starts the server + opens your browser, already signed in
```

The launcher signs the browser in for you. If you ever need the raw token (e.g. for a
browser that didn't go through the launcher), `.\scripts\copy-token.ps1` copies it to
the clipboard — it never prints the token, only a masked hint.
</details>

If anything looks wrong, run `python scripts/doctor.py` — it gives plain-language
PASS / WARN / FAIL guidance.

## What to test first

1. **Desktop voice loop** — open `http://localhost:8000`, sign in, Activate, speak,
   confirm you get a transcript and a spoken reply. *(Robotic voice? That's the
   browser fallback — install the real voice, see "About the voice" below.)*
2. **Operator console** — open `http://localhost:8000/console`, sign in, look at
   Status / Jobs / Proposed changes / Approvals / Audit.
3. **A proposed change** — ask the assistant (in work mode) to create or edit a small
   file; confirm it shows up as a **proposed change** you review and **Approve & Apply**
   — it does **not** edit your files on its own (that's `draft_only`, see below).
4. *(Optional)* **Phone access** — set up Tailscale (next section) and repeat the voice
   test from your iPhone.

## About the voice

Out of the box, **spoken replies use your device's built-in (browser/OS)
text-to-speech** — a generic, somewhat robotic voice. That's the fallback, not a bug.

For the **real, natural Adam voice**, double-click **`INSTALL-VOICE.cmd`** in the app
folder (setup also offers it at the end) — a one-time ~340 MB download that runs
entirely on your PC and speaks on both desktop and phone. Restart Adam afterward to
hear it.

If replies still sound robotic after installing, restart Adam (close the black server
window, reopen from the desktop icon) — the app quietly falls back to the browser voice
whenever the voice service isn't running.

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

## What NOT to expect yet (v0.9.x)

- **No tray app or auto-start.** The server runs in a visible window you keep open
  (minimize it, don't close it).
- **Tailscale setup is guided, not automatic.** The in-app Connect-phone wizard shows
  each step and the exact command to run, and the sign-in QR appears once the secure
  URL exists — but installing Tailscale and signing into it is still yours to do (see
  [Connect your phone](CONNECT_YOUR_PHONE.md)).
- **Single user, single token.** No accounts, no per-device tokens.
- **Windows only.** macOS/Linux are not supported; Android isn't validated; smart
  glasses aren't supported.
- **A polished non-technical experience end-to-end.** That's the v1.0 goal. v0.9.x is
  for prosumer + friends/family who don't mind a setup guide.

## Where the app comes from / updating

You received a **release ZIP** (e.g. `adam-local-v0.9.38.zip`). It contains the
app, templates (`.env.example`, `settings.example.json`), and docs — but **never** real
secrets or anyone's data.

**Updating is one click.** When a newer version is available, the app shows a green
**"Update available"** bar — click **Update now**, then restart the server (close the
black "Adam" window and reopen Adam from the desktop icon; the new
version isn't active until you do). Your `.env`, `settings.json`, `data/`, and even
your own code customizations survive updates — the updater does a three-way merge and
backs everything up first. Double-clicking `UPDATE.cmd` in the app folder does the same
thing manually. (Maintainers: see [RELEASE](RELEASE.md) for how releases are built and
published.)

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

**Never share your `.env`, your `ADAM_TOKEN`, or your `settings.json`.** No support
question requires them.

## Safety recap

- `draft_only` is on; the server is the sole writer; changes go through
  review → approve → apply with permission checks, backups, and an audit log.
- Your token, keys, and files stay on your machine.
- The bearer-token gate is the lock on the API — keep the token long, random, and
  private, and don't expose the port to the open internet (use Tailscale).
