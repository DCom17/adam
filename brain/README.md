# Adam Kit

A personal command-assistant system that runs on **Claude Code** and a folder of
markdown. It plans your days, keeps a durable shared-brain memory, tracks tasks,
runs daily/weekly reviews, and (optionally) drives a gamified progress dashboard
and your Google Calendar — all in the calm, dry Adam voice.

> **Status:** v0.1.0-dev — private build. Not for redistribution (see `LICENSE`).

## What it is (and isn't)

The "AI" is **Claude Code itself**. This kit is the *operating system* it reads:
`CLAUDE.md` plus a structured vault of markdown. There's no app to install and
nothing phones home. Your data stays in your folder on your machine.

## Prerequisites

- **Claude Code** installed, with your own Claude subscription or API access.
  Heavy commands use tokens — **that usage is billed to you.**
- Windows (the scripts are PowerShell; core markdown features work anywhere
  Claude Code runs).
- Optional modules need their own free/paid accounts (Google, etc.) — set up
  only the ones you want.

## Install (5 steps)

1. Clone or unzip this kit to a local folder (not inside a cloud-synced drive).
2. Copy `config/adam.config.example.json` → `config/adam.config.json`.
3. Copy `.env.example` → `.env` (only needed if you enable a module).
4. Open the folder in Claude Code.
5. Say **"Bootstrap Adam"** — it interviews you and sets up the rest.

## Modules (all optional, off by default)

| Module | What it adds | Setup |
|---|---|---|
| Core | Planning, memory, tasks, reviews, voice persona | none |
| Dashboard | Gamified XP/stats/quests dashboard (Google Sheets) | `setup_dashboard.md` |
| Calendar | Stage & commit Google Calendar events | `setup_calendar.md` |
| Voice / SMS / Night Watch | Advanced, always-on stack | v1.1 |

## Updating

Say **"Update Adam"**. It pulls the latest framework files and leaves all your
personal data untouched. (See `core_manifest.txt` for exactly what an update
can and can't overwrite.)

## License

All rights reserved — terms TBD. See `LICENSE`. Do not share or resell.
