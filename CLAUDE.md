# CLAUDE.md — Adam (dev repo)

Source repo for **Adam** (`github.com/DCom17/adam`), a local-first AI command
assistant shipped to real users. Releases publish to `DCom17/adam-releases`.
This working copy doubles as the owner's **live install** (the server on port
8000 runs from here) — treat runtime state accordingly.

This file holds durable facts about the repo. Current project status lives in
Claude Code project memory and `docs/ROADMAP.md`; decisions are D-numbered in
`docs/DECISION_LOG.md` — check there before re-litigating a settled question
(e.g. D21: the product name stays "Adam"; the "Atam" respelling was rejected).

## Layout

- `server.py` + `routers/` — the FastAPI app (chat, integrations, reviews, system, voice_push)
- `web/` — PWA front end (index/console/settings + setup pages, sw.js, manifest)
- `brain/` — the **de-personalized shipping vault template**. `brain/CLAUDE.md`
  is the *product's* operating instructions (what end users' Adam runs on),
  not a dev config. Never put personal data here; the release content guards
  are a backstop, not a license.
- `data/baseline/` — the updater's 3-way-merge baseline (the last-shipped file
  set). **Never hand-edit.** It is maintained by the update engine
  (`update_engine.py`, `merge.py`); editing it corrupts user-edit detection.
- `scripts/` — setup, doctor, launchers, release tooling
- `dist/` — build output + one-off publish scripts
- `docs/` — user-facing docs (shipped per the release allow-list) + internal
  process docs (ROADMAP, DECISION_LOG, RELEASE)
- `site/` — landing page; republish with `dist\publish-site.ps1`
  (→ adam.camposenterprises.org via gh-pages)
- `packaging/winget/` — winget manifest kit (blocked on code signing)
- Version: `APP_VERSION` in `config.py`; history in `CHANGELOG.md`

## Commands

- **Tests:** `python -m pytest` — collects `tests/` only. The root `test_*.py`
  files are legacy standalone scripts (each `main() -> int`, exit 0 = pass),
  executed one-per-test by `tests/test_legacy_suite.py`. Don't collect them
  directly and don't "modernize" them without updating that harness.
- **Restart the live app:** `wscript scripts\adam-app.vbs` (preferred). TTS
  runs on port 8001 and is self-healed by the Adam server — don't babysit it.
- **Build a release ZIP:** `python scripts/make_release.py` — strict
  **allow-list** builder (never a directory walk) with fail-closed deny guards
  for secrets/runtime/backups. A new file ships only if added to the lists in
  that script.
- **Installer:** `scripts/build-installer.ps1` (Inno Setup; `-Sign` once the
  Azure code-signing cert lands). **Publish:** `scripts/publish-release.ps1`
  → `DCom17/adam-releases`. Process: `docs/RELEASE.md`; acceptance:
  `docs/CONSUMER_TEST_CHECKLIST.md` — run it on a cold extract, not in-repo.

## Hard rules

- Never commit `.env`, the real `settings.json`, or anything under `data/`.
  The allow-list protects the ZIP; git hygiene protects the repo. Both matter.
- localStorage keys stay `jarvis_*` **on purpose** (pre-rename installs keep
  their state). Do not rename them to `adam_*`.
- The old-name shims (`start-jarvis.ps1`, `jarvis-app.vbs`) stay shipped —
  pre-rename desktop/taskbar shortcuts point at them.
- The shipped product is **safe mode**: no shell, external writes only via
  ACTION blocks. Don't add brain-template instructions that assume a shell.
- Owner's personal vault is `My Drive\JARVIS` — a separate live system, never
  a test fixture. Its `vault_path` must never be blanked or pointed here.
- Verify before claiming: after any change to server/web/brain, exercise the
  affected flow (restart + hit the endpoint or page), and report test output
  as it actually ran. A release claim needs the published asset verified
  (download or API check), not just a local build.
