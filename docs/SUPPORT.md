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
- **System tray / auto-start** — not yet. The server runs in a visible window you keep
  open. (Setup **is** a guided wizard — `SETUP.cmd` — and updates **are** one-click via
  the in-app "Update available" bar or `UPDATE.cmd`; see [`RELEASE.md`](./RELEASE.md).)

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

## Getting unstuck

Run `python scripts/doctor.py` first — it gives plain-language PASS/WARN/FAIL guidance
(including mobile-access and HTTPS advice). For phone-specific issues, the troubleshooting
section of [`CONNECT_YOUR_PHONE.md`](./CONNECT_YOUR_PHONE.md) covers the common cases
(insecure-context banner, 403, can't-reach, mic).
