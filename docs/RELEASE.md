# Building & Shipping a Release

How to cut a Jarvis Voice Local release ZIP for the friends-&-family beta. The goal is
a download that contains the **product and its docs — and never your secrets, your
files, or rollback backups**.

## TL;DR

```powershell
# From the product folder:
python scripts/make_release.py            # writes dist\jarvis-voice-local-vX.Y.Z.zip
# or, the wrapper:
.\scripts\make_release.ps1
```

Preview what would ship without building:

```powershell
python scripts/make_release.py --list
```

## How the builder stays secret-safe

`scripts/make_release.py` does **not** zip the folder. It ships an explicit
**allow-list** of files, then runs a **fail-closed deny guard** that aborts the build
if any staged path matches a secret / runtime / backup pattern. If a new sensitive file
ever lands in the repo, it simply isn't on the allow-list — and if it somehow were, the
guard refuses to build. `test_release.py` enforces both halves in the test suite.

### What IS included

- Product modules: `server.py`, `config.py`, `permissions.py`, `proposed_changes.py`,
  `approvals.py`, `diffs.py`, `job_store.py`, `onboarding.py`, `agent_write_probe.py`
- `requirements.txt`, `README.md`, `CHANGELOG.md`, `.gitignore`
- Templates: **`.env.example`**, **`settings.example.json`** (never the real ones)
- `web/`: `index.html`, `console.html`, `sw.js`, `manifest.json`, `icon.png`
- `scripts/`: `setup.py`, `doctor.py`, `start-jarvis.ps1` (the one-click launcher),
  `make_release.py`/`.ps1`, and the start/stop/restart/health/test dev scripts
- User-facing docs: `CONNECT_YOUR_PHONE.md`, `ADVANCED_REMOTE.md`, `SUPPORT.md`,
  `RELEASE.md`, `CONSUMER_TEST_CHECKLIST.md`
- The `test_*.py` suites (so beta users can self-verify) and an empty `data/` tree
  (`data/.gitkeep`)

### What is NEVER included

- `.env` and any `.env.*` (except `.env.example`), `settings.json` and its backups
- `*.pem`, VAPID private keys, push-subscription files, last-result files
- `data/**` runtime data (your uploads, drafts, jobs DB, logs, backups) — only
  `data/.gitkeep` ships
- `*.bak` / `*.pre-*` rollback backups, `__pycache__/`, `*.pyc`, `.git/`
- Internal process docs (`DECISION_LOG`, `ROADMAP`, `PHASE_HISTORY`,
  `NEXT_PHASE_PROMPT`, `PARKING_LOT`, `PERSONAL_FIX_PORTING_LOG`,
  `DEVICE_ACCEPTANCE_RUN_*`) — beta users get setup/support docs, not build history

## Verify a ZIP before sharing

`test_release.py` already builds a throwaway ZIP and asserts it contains no excluded
files and all the expected ones. Run the suite, or spot-check by hand:

```powershell
python test_release.py
# manual peek:
python -c "import zipfile; print('\n'.join(zipfile.ZipFile(r'dist\jarvis-voice-local-vX.Y.Z.zip').namelist()))"
```

Confirm there is **no** `.env`, `settings.json`, `data/` runtime file, or `*.bak` in
the listing.

## Distributing the beta

- Cut a **private / unlisted** GitHub release and attach the ZIP (the audience is
  prosumer + friends/family, not the public).
- Tag it to the `APP_VERSION` in `config.py`.

## Versioning & the manual update path

There is **no auto-updater** in v0.9 (by design). To update:

1. Download the new ZIP and unzip it to a **new** folder (or over the old code).
2. Your data and secrets are safe — they live in `.env`, `settings.json`, and `data/`,
   none of which are in the ZIP, so an overwrite never touches them. (If you unzip to a
   fresh folder, copy your `.env`, `settings.json`, and `data/` across.)
3. Re-run the doctor and start:

   ```powershell
   python scripts/doctor.py
   .\scripts\start-dev.ps1
   ```

If the doctor reports a new check or a FAIL after an update, follow its plain-language
guidance before starting.
