# Support & Scope (v0.9 friends-&-family beta)

What this beta supports, what it requires, and what it deliberately does **not** do
yet. Adam is **local-first**: you run the backend on your own machine,
with your own Claude Code credentials, against your own files. There is no hosted
service and nothing is routed through anyone else's account.

## Audience

v0.9 is a **prosumer + friends/family** beta. The long-term goal is a non-technical
consumer experience (v1.0), but v0.9 assumes you're comfortable installing software and
following a setup guide.

## Requirements

- **Windows 10 or 11.** (macOS and Linux are not supported yet.)
- A **Claude account on a paid plan**, or an Anthropic API key for pay-as-you-go —
  chosen during setup.
- **Python** and **Claude Code** — the `SETUP.cmd` wizard installs both if they're
  missing (manual setup expects them already on the machine).
- A **single user** per backend instance.

## What works

- **Desktop / local by default.** On the PC, `http://localhost:8000` is a secure
  context, so the full app — including voice — works with zero networking setup.
- **Mobile (phone) access** via **Tailscale + Tailscale Serve HTTPS** — the supported
  path. See [`CONNECT_YOUR_PHONE.md`](./CONNECT_YOUR_PHONE.md). iPhone voice/mic/PWA
  require HTTPS; Tailscale Serve provides it privately.
- **Operator console** at `/console` (status, jobs, proposed changes, approvals, audit,
  and safe action controls).

## Limited / advanced

- **Public remote access** (cloudflared named tunnel + your domain) is **advanced and
  limited-support**, and **requires Cloudflare Access**. See
  [`ADVANCED_REMOTE.md`](./ADVANCED_REMOTE.md). A public URL guarded by only the bearer
  token is not acceptable.

## Not supported / not yet

- **Android** — not validated. It may work, but it isn't tested or supported in v0.9.
- **Smart glasses** — not advertised or supported until the hardware path is tested.
- **macOS / Linux** — not supported yet.
- **Hosted SaaS** — there is none, by design. You run it yourself.
- **Shared / maintainer Claude account** — never; you use your own Claude Code
  credentials.
- **Multi-user / accounts / per-device tokens / device pairing** — not yet (single
  static bearer token in v0.9).
- **System tray / auto-start** — by design. The server runs in a **visible** window you
  keep open: nothing about Adam runs hidden, and that's part of the trust model (like
  `draft_only` and the audit log), not a missing feature. (Setup **is** a guided wizard —
  `SETUP.cmd` — and updates **are** one-click via the in-app "Update available" bar or
  `UPDATE.cmd`; see [`RELEASE.md`](./RELEASE.md).)

## Safety model (unchanged in every access mode)

- `agent_safety.mode = draft_only` is the default and stays on.
- The **server is the sole writer**; Claude proposes changes, it does not write your
  files directly.
- File changes go through **review → approval → apply**, with permission checks,
  staleness/conflict detection, backups, and an audit log.
- Your token, keys, and files never leave your machine; nothing is routed through a
  third party.

## "Windows protected your PC" (SmartScreen)

The beta is not code-signed yet, so the first time you run `SETUP.cmd` (or `START.cmd`
/ `UPDATE.cmd`) Windows may show a blue **"Windows protected your PC"** box. That is
SmartScreen being cautious about an unsigned app from the internet — not a detection of
anything wrong. Two ways past it:

- **Best:** before extracting, right-click the downloaded ZIP → **Properties** → tick
  **Unblock** → OK. Files extracted after that run without the warning.
- **Otherwise:** on the blue box, click **More info** → **Run anyway** (needed once per
  script).

Never turn SmartScreen off system-wide for this — the per-file Unblock is all it takes.
A code-signing certificate (which removes the warning entirely) is on the v1.0 roadmap.

## Your data — where everything lives

Everything stays on your PC. The two places that are **yours**:

- **Your notes folder** — the files Adam reads and (with your approval) writes.
  Setup's default is `Documents\Adam Files`; the exact path is `vault_path` in
  `settings.json`. **This is the folder to back up.**
- **The Adam app folder** (wherever you extracted it) — inside it, `data\` holds
  Adam's own working state:
  - `data\state\` — chat/job history (`adam.db`), pending approvals
  - `data\uploads\` — files you attached from the phone (swept after ~7 days)
  - `data\backups\` — automatic pre-change backups, incl. one folder per update
  - `data\logs\` — the server log, the voice log, and the audit trail
  - `data\outputs\`, `data\drafts\` — where approved writes land by default

Your secrets never leave the machine: the token lives in `.env`, your settings in
`settings.json`, both inside the app folder, both excluded from updates.

## Changing (rotating) your token

If your token may have been exposed (someone photographed the token QR, a device was
lost), double-click **`ROTATE-TOKEN.cmd`** in the app folder. It generates a fresh
token in `.env` (backing up the old file first). Then restart Adam and sign in again
on each device — the console's **Connect phone** QR is the easy way for the phone.
Every old copy of the token stops working the moment the server restarts.

## Uninstalling Adam

Adam installs nothing hidden — no service, no registry autostart — so removal is:

1. Close the black "Adam" window (that stops the server).
2. *(Phone access only)* remove the Tailscale serve:
   `tailscale serve --https=443 off` (use the port you chose, e.g. `8443`).
3. **Copy anything you want to keep** — your notes folder (`Documents\Adam Files`
   unless you changed it) is yours and stays; `data\` inside the app folder dies
   with it.
4. Delete the app folder, and the "Adam" shortcuts on your Desktop / Start Menu.

What remains afterwards (all standard software you may use for other things, safe to
keep or uninstall via Windows Settings → Apps): **Python**, **Claude Code**, and
**Tailscale**. On your phone, remove the home-screen app like any other.

## Starting over (factory reset)

To reset Adam without uninstalling: close the server window, then delete the `data\`
folder (your chats/jobs/approvals — gone) and re-run `SETUP.cmd`. Keep `.env` to keep
your token, or delete it too for a completely fresh start. Your notes folder is never
touched by a reset.

## Getting unstuck

Run `python scripts/doctor.py` first — it gives plain-language PASS/WARN/FAIL guidance
(including Claude sign-in, the voice service, mobile-access and HTTPS advice). For
phone-specific issues, the troubleshooting section of
[`CONNECT_YOUR_PHONE.md`](./CONNECT_YOUR_PHONE.md) covers the common cases
(insecure-context banner, 403, can't-reach, mic). For "the app says it can't reach
Adam": the black Adam window on the PC is probably closed — reopen it from the
desktop icon.
