# Privacy — what Adam does (and doesn't do) with your data

**Adam is an AI assistant, powered by Claude.** When you talk to Adam you are
talking to an AI, not a person; your messages are processed by Anthropic's
Claude under your own account (details below).

**The short version: the Adam software collects nothing.** There is no Adam
server, no account, no telemetry, no analytics, no crash reporting. The maintainer
never sees your files, your conversations, your token, or your keys — there is no
infrastructure that could receive them. Everything runs on your own PC.

The one exception is **buying** Adam Plus, which — like buying anything — involves
a payment processor who knows who paid. That is a separate thing from the software,
and it is spelled out under "If you buy Adam Plus" below. Using Adam for free
involves no purchase data at all.

## What stays on your machine

- **Your files** (the notes folder / `vault_path`) — read locally; written only
  through the review-and-approve flow, by the server on your PC.
- **Your conversations** — chat history and job records live in `data\state\` on
  your PC. Spoken audio is generated locally (the Kokoro voice) or by your own
  device's built-in voice.
- **Your secrets** — the access token (`.env`), settings, and any add-on keys.
  They are never logged, never returned by the API, and never included in a
  release or a diagnostics bundle (which is redacted before it leaves the app).

## The network calls Adam actually makes

Adam talks to the internet only in these ways — each one either goes to **your own
account** or is a plain software download:

1. **Anthropic (Claude)** — your questions are processed by Claude using *your*
   Claude account or *your* API key, under
   [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy). This is
   the one place your conversation content leaves your machine, and it's the
   product working as advertised: you bring your own AI.
2. **Update check (GitHub)** — the app asks GitHub's public releases endpoint
   whether a newer version exists (a standard HTTPS request; no personal data,
   no identifier beyond an ordinary web request). One-click updates download from
   the same place. Turn it off with `update_check_enabled: false` in
   `settings.json`.
3. **One-time installs** — the setup wizard can download Python (via winget) and
   Claude Code (from Anthropic); INSTALL-VOICE downloads the open-source Kokoro
   voice model from its public GitHub releases. All are ordinary downloads.
4. **Optional add-ons you configure** — Calendar/Email/Hunter bridges run inside
   *your* Google account; LinkedIn and Twilio (SMS/voicemail) use *your* API
   credentials. Nothing is proxied through anyone else. Add-ons are off until
   you set them up.
5. **Optional phone access** — over *your* Tailscale network (their
   [privacy policy](https://tailscale.com/privacy-policy)); traffic goes
   directly between your phone and your PC. If you enable Web Push
   notifications, your browser's push service delivers them (the notification
   payload is generated on your PC).

That's the complete list. If a future version ever adds a network call, it will
be documented here first.

## If you buy Adam Plus

Adam itself is free and this section does not apply to you unless you buy.

- **Who takes the payment.** Checkout is handled by **Gumroad**, our Merchant of
  Record — they are the seller on the receipt and handle billing and sales tax.
  Your card details go to them, not to us. We never see your full card number.
- **What the maintainer receives.** Limited order information: your email
  address, an order ID, your country, and which licence you bought. That is used
  to issue your licence key and to help you if you write in.
- **Your licence key contains your email address.** Keys are signed once and
  checked entirely on your own machine — there is no licence server, no
  activation call, and no phone-home. That is deliberate, and it is why the email
  is baked into the key rather than looked up. Treat the key like a receipt.
- **What does not change.** Buying does not add telemetry, an account, or any
  network call inside the app. The list above is still the complete list.

Full policy, including your rights and how long records are kept, ships with the
app as `legal/privacy.md` (also linked from the website's Privacy page).

## Your responsibilities

Because everything is yours, privacy choices are yours too: what lives in your
notes folder, whether to enable add-ons, and who can reach your PC. Keep your
token private (rotate it with `ROTATE-TOKEN.cmd` if it may have leaked), and use
the supported Tailscale path — not a public tunnel — for phone access.

*This page describes Adam v0.9.x. Maintainer contact: see SUPPORT.md.*
