# Setup: Hunter Dashboard add-on

The dashboard is the gamified XP/stats/quests/bosses board, rendered in your own Google
Sheet (a full desktop view + a phone view). Optional — everything else in JARVIS works
without it.

## How to set it up — in the app, not here

In Jarvis Voice Local you connect the dashboard through the built-in wizard, which sets up
the Sheet bridge and token for you. You do **not** edit any config file or run a script.

1. Open the **gear menu → Settings → Add-ons** (or go to **/settings**).
2. On the **Hunter** card, click **Add**, and follow the wizard — it gives you a clean
   template Sheet to copy and walks you through pasting its Apps Script URL + token back in
   (best done on a computer).
3. When the wizard shows **Enabled**, you're done — it takes effect immediately, no restart.

## How JARVIS uses it once connected

JARVIS keeps your real state in `11_dashboard/dashboard_state.json` (the single source of
truth). When it's time to update the board — daily shutdown, "Give me credit," weekly
review, or sign-off — it builds a payload from that file and emits a `hunter.sync` action.
The server sends it to your own Sheet after you approve it. There is no `sheet_sync.json`
file and no script. Mechanism details: `INTEGRATION_EXECUTION.md`.

## Notes

- The board self-cleans from the payload each sync (it replaces the daily-quest tab), so
  only today's quests should ever be in the `hunter.sync` payload.
- Your bridge token lives only in your local `.env` (written by the wizard) and is never
  logged or shared. Your data only ever goes to *your* Sheet.
- Updating the app later never touches your dashboard data or your Sheet.
