# Setting up Adam Kit

Full install guide. For most people this is ~10 minutes to a working core, plus
optional module setup later.

## Prerequisites

- **Claude Code** installed and signed in (your own Claude subscription or API
  access). Heavy commands use tokens — that usage is billed to you.
- **Windows** for the PowerShell helper scripts (dashboard/calendar sync). The
  core markdown system works anywhere Claude Code runs; only the sync scripts are
  Windows/PowerShell.
- **Python 3** on PATH — only needed for the graph linker and the performance
  analysis script. Core daily use doesn't require it.

## 1. Get the kit onto your machine

**Clone** the kit to a **local** folder — not inside a cloud-synced drive like
Google Drive or OneDrive (sync + live files don't mix well, and it's how secrets
leak). For example:

```
git clone <your-repo-url> C:\Users\you\adam
```

Cloning (rather than downloading a zip) is what lets **"Update Adam"** pull
future framework updates while leaving your data untouched. A zip install works
too, but can't auto-update.

## 2. Create your config

```
copy config\adam.config.example.json config\adam.config.json
copy .env.example .env
```

You can leave both mostly blank — **Bootstrap fills them in** in the next step.
`config/adam.config.json` and `.env` are gitignored; they never get committed.

## 3. Open in Claude Code and bootstrap

Open the kit folder in Claude Code and say:

> **Bootstrap Adam**

Adam interviews you (name, work, school, goals, preferences, which modules you
want) and writes your profile, preferences, config, and first goals ("bosses").
See `BOOTSTRAP.md` for what it does.

## 4. Use it

- **"Start my day"** — plan the day
- **"How'd we do"** / **"See you tomorrow"** — daily shutdown
- **"Big picture me"** — weekly review
- **"Get up to speed"** — process phone/inbox captures
- **"Save"** — checkpoint before closing

The full command set and operating rules are in `CLAUDE.md`.

## 5. Optional modules (any time)

- **Dashboard** (gamified board, Google Sheets) → `setup_dashboard.md`
- **Calendar** (Google Calendar commits) → `setup_calendar.md`
- **Voice / SMS / Night Watch** — advanced always-on stack, v1.1.

## Updating

Say **"Update Adam"** to pull the latest framework. Your personal data
(profile, memory, logs, dashboard state, tasks) is never overwritten — see
`core_manifest.txt` for exactly what an update can touch.

## Troubleshooting

- *"config/adam.config.json not found"* — you skipped step 2.
- *Sync token errors* — the token in `.env` must match the one in your Apps
  Script. Re-check `setup_dashboard.md` / `setup_calendar.md`.
- *A module command is declined* — that module isn't enabled in
  `config/adam.config.json` (`modules` block) or isn't set up yet.
