# Setup: Calendar add-on

This lets JARVIS put events on your Google Calendar (creates only — the product has no
delete or edit-on-sign-off). Optional; the rest of JARVIS works without it.

## How to set it up — in the app, not here

In Jarvis Voice Local you connect Calendar through the built-in wizard, which creates the
bridge and handles the token for you. You do **not** edit any config file or run a script.

1. Open the **gear menu → Settings → Add-ons** (or go to **/settings**).
2. On the **Calendar** card, click **Add**, and follow the step-by-step wizard
   (it walks you through pasting a small Google Apps Script and pasting back the URL +
   token — best done on a computer).
3. When the wizard shows **Enabled**, you're done — it takes effect immediately, no restart.

## How JARVIS uses it once connected

JARVIS stages your day in `06_calendar/` (packet → preview) and, on sign-off, emits a
`calendar.create` action. By default that waits for your one-tap approval on screen; if you
turn on **auto-create on sign-off** (the calendar auto-run toggle in Settings), creates run
immediately. Mechanism details: `INTEGRATION_EXECUTION.md`.

## Safety

- **Creates only.** The connector has no delete, and edits are deliberate one-off actions —
  never autonomous. Nothing reaches your calendar without either your approval tap or the
  auto-run setting you chose.
- Your bridge token lives only in your local `.env` (written by the wizard) and is never
  logged or shared. Only the events you approve are sent, to your own calendar.
