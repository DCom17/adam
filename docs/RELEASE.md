# Building & Shipping a Release

How to cut an Adam release ZIP for the friends-&-family beta. The goal is
a download that contains the **product and its docs — and never your secrets, your
files, or rollback backups**.

## TL;DR

```powershell
# From the product folder:
python scripts/make_release.py            # writes dist\adam-local-vX.Y.Z.zip
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

The authoritative allow-list lives at the top of `scripts/make_release.py`. In broad
strokes it ships:

- The product modules and `routers/`, `requirements.txt`, `README.md`, `CHANGELOG.md`,
  `LICENSE`, `.gitignore`
- The double-click entry points: `START_HERE.txt`, `SETUP.cmd`, `START.cmd`,
  `UPDATE.cmd`, `INSTALL-VOICE.cmd`
- Templates: **`.env.example`**, **`settings.example.json`** (never the real ones)
- The `web/` app, the `scripts/` wizard / launcher / updater / doctor / dev scripts,
  and the optional TTS server (`scripts/tts_server/` — code only, the model downloads
  on install)
- The de-personalized **`brain/`** starter kit (walked whole; every file passes the
  path deny-guard AND a content guard for personal/infra terms)
- User-facing docs and the `test_*.py` suites (so beta users can self-verify), plus an
  empty `data/` tree (`data/.gitkeep`)

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
python -c "import zipfile; print('\n'.join(zipfile.ZipFile(r'dist\adam-local-vX.Y.Z.zip').namelist()))"
```

Confirm there is **no** `.env`, `settings.json`, `data/` runtime file, or `*.bak` in
the listing.

## Publishing a release (this is how updates ship)

Updates are delivered through **GitHub Releases** on the public releases repo — the
`update_repo` setting, default `DCom17/adam-releases`. Every install checks its
`releases/latest` endpoint, so **publishing a release ships the update**:

1. Bump `APP_VERSION` in `config.py` and build: `python scripts/make_release.py`.
2. Publish: `.\scripts\publish-release.ps1` — it creates the release and attaches the
   ZIP via `gh release create` (or prints the manual web steps if `gh` isn't
   installed). Tag it to the `APP_VERSION`.

## How users update

Users don't re-download anything. The app checks on open and shows an
**"Update available → Update now"** bar; one click downloads and applies it.
`UPDATE.cmd` is the manual equivalent. Either way the apply path is the smart
three-way updater (`update_engine.py`): files the user never touched are updated
(backed up first), files only they changed are kept, real conflicts hold their version
and surface for review — and `.env`, `settings.json`, and `data/` are never touched.
The update takes effect after the server restarts (close the black window, reopen).

<details>
<summary>Fully manual fallback (unzip over the install)</summary>

1. Download the new ZIP and unzip it to a **new** folder (or over the old code).
2. Your data and secrets are safe — they live in `.env`, `settings.json`, and `data/`,
   none of which are in the ZIP. (If you unzip to a fresh folder, copy those across.)
3. Re-run `python scripts/doctor.py`, then start as usual. If the doctor reports a
   FAIL, follow its plain-language guidance before starting.
</details>

## Building the Windows installer (v1.0 packaging)

The installer wraps the exact guarded ZIP — every fail-closed release guard runs
before a single installer byte exists:

```powershell
.\scripts\build-installer.ps1                                    # builds the ZIP fresh, then the installer
.\scripts\build-installer.ps1 -Zip dist\adam-local-vX.Y.Z.zip    # reuse an already-built ZIP
```

Output: `dist\adam-setup-vX.Y.Z.exe` (Inno Setup 6 required:
`winget install JRSoftware.InnoSetup`).

Behavior by design (see comments in `scripts\adam-installer.iss`):
- Per-user install to `%LOCALAPPDATA%\Programs\Adam` — no admin prompt, and the
  in-app updater keeps working because the folder stays user-writable.
- First-install tool: upgrades between releases go through UPDATE.cmd / the
  in-app updater (which has merge + backup logic). A reinstall over an existing
  folder never overwrites `brain\`, `.env`, `settings.json`, or `data\`.
- Uninstall (Add/Remove Programs, or `unins000.exe /VERYSILENT`) removes program
  files and shortcuts but keeps `brain\`, `data\`, and `.env` — the user's notes,
  token, and backups survive; delete the folder by hand to remove everything.
- Silent install for winget: `adam-setup-vX.Y.Z.exe /VERYSILENT /SUPPRESSMSGBOXES`.
- Unsigned until the Azure Artifact Signing cert lands (SmartScreen will warn);
  when it does, add a `SignTool=` directive in the `.iss` — nothing else changes.
