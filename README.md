# Adam

A local-first voice assistant. Your phone talks to a small backend running on
**your own computer**, which drives **your own Claude Code** against **your own
files**. Nothing is hosted by anyone else: no shared accounts, no shared API
keys, no cloud sessions. You run it, you own it.

```
Phone PWA  ->  your desktop backend (this app)  ->  your Claude Code  ->  your files
```

> **Status — v0.9.35, friends-&-family / prosumer beta.** Desktop-local works out of the
> box via the double-click **`SETUP.cmd`** wizard; phone access is supported via
> **Tailscale Serve** (HTTPS); updates arrive through a one-click in-app updater
> (GitHub Releases). Not yet done before it's a full consumer product: a tray app /
> auto-start, per-device auth (it's a single bearer token today), and platforms beyond
> Windows. Run it on your own trusted Windows machine. New here? Start with the
> **[Beta handoff](docs/BETA_HANDOFF.md)**.

---

## Documentation & mobile access

- **[Beta handoff](docs/BETA_HANDOFF.md)** — start here if you're a beta tester: what you
  need, what to test first, what not to expect yet, and how to report problems.
- **[Connect your phone](docs/CONNECT_YOUR_PHONE.md)** — get the PWA + voice working on
  iPhone via Tailscale (the supported mobile path). Explains why HTTPS is required for
  iPhone mic/voice/PWA, with a step-by-step Tailscale Serve runbook and troubleshooting.
- **[Support & scope](docs/SUPPORT.md)** — what's supported/required and what isn't yet
  (Windows-only, single-user, desktop-local by default, mobile via Tailscale).
- **[Building a release](docs/RELEASE.md)** — the secret-safe ZIP flow and the manual
  update path for the friends-&-family beta.
- **[Advanced remote access](docs/ADVANCED_REMOTE.md)** — optional public access via
  cloudflared + a domain; **requires Cloudflare Access** (advanced/limited-support).

> **Phones need HTTPS.** On the PC, `http://localhost` already works for voice. On a
> phone, plain `http://<lan-ip>` is not a secure context, so the mic/voice silently
> fail and the app shows an insecure-connection banner — use the Tailscale path above.

---

## What it does

- **Voice and text chat** with a Claude Code agent, in the **Adam** register, from a
  phone PWA or the desktop browser.
- **Safe by default (`draft_only`)** — the agent reads and **proposes** file changes; it
  cannot write your files directly. You review a diff and **approve → apply**; the
  **server is the sole writer**, with permission checks, backups, and an audit log.
- **Operator console** (`/console`) — desktop view of status, job history, the
  proposed-change/approval queue, diffs, and a read-only audit log, plus safe action
  controls (approve/deny/apply/refresh) that reuse the same gated endpoints.
- **Work mode** — a fuller Claude Code agent (tools, edits routed through the proposed-
  change flow); voice mode stays terse and vault-only.
- **Async jobs** — long commands run in the background (`/ask_async` + `/poll`) and
  **persist across restarts** (SQLite); interrupted jobs are reported, never lost.
- **File / photo uploads** — including iPhone HEIC (auto-converted to JPEG).
- **Web Push** — the installed PWA is notified when a background job finishes.
- **Inbound SMS** (optional) — text the assistant hands-free via Twilio.
- **The real Adam voice (optional)** — out of the box, spoken replies use the
  browser's built-in (robotic) voice. Double-click **`INSTALL-VOICE.cmd`** (setup also
  offers it at the end) for the high-quality local voice — a one-time ~340 MB download
  that runs entirely on your PC and serves both desktop and phone. If the voice service
  isn't installed or isn't running, the app quietly falls back to the browser voice.

---

## Prerequisites

- **Windows 10 or 11**
- A **Claude account** on a paid plan, or an Anthropic API key for pay-as-you-go
  (you pick one during setup)
- **Python 3** and **Claude Code** — `SETUP.cmd` installs both for you if they're
  missing. (Manual setup expects them on `PATH`; developed and tested on Python 3.14.)
- *(optional, for phone access)* a **Tailscale** account — see
  [Connect your phone](docs/CONNECT_YOUR_PHONE.md)
- *(optional)* the local high-quality voice — installed by `INSTALL-VOICE.cmd`

---

## Setup (Windows)

### Easiest way — double-click (recommended)

In the extracted folder, **double-click `SETUP.cmd`** (or open `START_HERE.txt` first).
A guided wizard does the rest: it installs **Python** and **Claude Code** for you if
they're missing, walks you through the **one-time Claude sign-in** (the only step it
can't do for you — it's your own account), configures everything, and opens Adam in
your browser **already signed in**. To open Adam again any time, double-click
**`START.cmd`**. No terminal, no token to copy.

The command-line steps below do the same thing manually, for those who prefer it.

### Manual setup

From the project root (`adam-local\`):

```powershell
# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. Guided setup — generates your token, finds Claude, sets your vault path,
#    then runs the setup doctor. Safe to re-run; it never overwrites an existing
#    token and never changes safety settings.
python scripts/setup.py
```

`setup.py` is a plain Python script on purpose — it sidesteps PowerShell
execution-policy restrictions that can block the `.ps1` launchers. It only ever
edits `ADAM_TOKEN` (in `.env`) and, with your confirmation, `claude_exe` and
`vault_path` (in `settings.json`). It never touches `agent_safety.mode`, the
`permissions` block, or the approval/backup/audit logic.

**Signing in is automatic.** The launcher (`scripts\start-adam.ps1`, or the
`START.cmd` double-click) opens your browser **already signed in** — it passes your
token to the app in the URL fragment, which is stored locally and stripped from the URL
immediately (it never reaches the server or its logs). You don't paste anything.

To sign in a **phone**, open the Operator Console's **Connect phone** and scan the
single sign-in QR (see [Connect your phone](docs/CONNECT_YOUR_PHONE.md)).

<details>
<summary>Getting the token by hand (rarely needed)</summary>

If you ever need the raw token (e.g., to sign in a browser that didn't go through the
launcher), run `.\scripts\copy-token.ps1`. It copies your `ADAM_TOKEN` to the
clipboard, **read-only**: it never prints/logs the token, never changes
`.env`/`settings.json`, and never sends it anywhere — it shows only a masked hint
(length + last 4 chars). If `.env` or the token is missing, it tells you to run
`setup.py`.
</details>

Re-check your install at any time:

```powershell
python scripts/doctor.py
```

Both `setup.py` and `doctor.py` are resilient to a broken `settings.json`: a file
saved with a UTF-8 BOM still loads, and a malformed file produces a clear message
naming the file and the parse location (line/column) instead of a traceback — they
report the problem and exit non-zero, and never auto-edit the file to "fix" it.

<details>
<summary>Manual setup (if you'd rather not use the script)</summary>

```powershell
# Create your secrets file, then set ADAM_TOKEN to a long random value:
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into ADAM_TOKEN=

# Create your settings file, then edit it (see "Configuration" below):
Copy-Item settings.example.json settings.json
```
</details>

### Configuration

All non-secret settings live in **`settings.json`**. Secrets live in **`.env`**.
You never need to edit `server.py` or `index.html` for normal setup.

`settings.json` keys:

| Key | What it does |
|---|---|
| `claude_exe` | Full path to `claude.exe`. Leave `""` to auto-detect on `PATH`. |
| `voice_model` | Claude model id for turns (default `claude-opus-4-8`). |
| `vault_path` | Working dir Claude runs in — **your files/projects root**. |
| `work_extra_dirs` | Extra dirs work mode may reach beyond the vault. |
| `host` / `port` | Bind address/port (default `0.0.0.0:8000`). |
| `public_base_url` | Public URL the PWA is reached at (used by Twilio + docs). |
| `cors_allowed_origins` | List of allowed origins, or `["*"]` to allow all. |
| `tts_url` / `tts_timeout_seconds` | Local TTS endpoint + timeout. |
| `*_timeout_seconds`, `job_ttl_seconds` | Turn/job timeouts. |
| `data_dir`, `upload_dir`, `log_dir`, `state_dir` | Runtime data locations (relative paths resolve under the project). |
| `upload_max_mb`, `upload_ttl_days` | Upload size cap + retention. |

`.env` keys (secrets — see `.env.example` for the full list):

| Key | Required? | What it does |
|---|---|---|
| `ADAM_TOKEN` | **Yes** | The bearer token that gates the API. |
| `VAPID_PUBLIC_KEY` / `VAPID_SUBJECT` | No | Enables Web Push. |
| `TWILIO_AUTH_TOKEN` / `OWNER_PHONE` / `TWILIO_WEBHOOK_URL` | No | Enables inbound SMS. |

---

## Permissions & the safe write boundary

Adam ships a **controlled-action permission layer** — the safety
foundation that lets the assistant read, draft, and (on your approval) modify files
without being able to touch anything you didn't approve.

The model is **deny-by-default with an explicit allow-list**, configured under
`permissions` in `settings.json`:

| Key | Default | What it does |
|---|---|---|
| `read_dirs` | `[]` → the vault | Directories the assistant may read. Empty means just `vault_path`. |
| `write_dirs` | `["data/outputs","data/drafts"]` | The **only** directories writes are allowed into. Never the vault by default. |
| `allowed_commands` | `[]` | Shell commands permitted (only consulted when `allow_shell` is on). |
| `require_approval_for_writes` | `true` | Writes must be approved before they run. |
| `require_approval_for_destructive` | `true` | Destructive actions must be approved. |
| `backup_before_write` | `true` | Existing files are copied to `data/backups/` before being overwritten. |
| `audit_log_enabled` | `true` | Every decision is recorded to `data/logs/audit.jsonl`. |
| `allow_shell` | `false` | Whether shell/command execution is permitted at all. |
| `allow_network_tools` | `false` | Reserved flag for future network-tool gating. |
| `max_file_size_mb` | `25` | Cap on write/backup size. |
| `blocked_paths` | `[]` | Paths that are never readable or writable, overriding every allow-list. |
| `protected_file_patterns` | `.env`, `*.pem`, `*.key`, `*.secret`, `id_rsa*`, `settings.json`, … | Filenames that may never be written, even inside an allowed dir. |

### What's blocked by default

- Writing **anywhere except `data/outputs` and `data/drafts`** (the vault is
  read-only out of the box).
- Writing any **secret/key/config** file (`.env`, `*.pem`, `settings.json`, …),
  even into an allowed directory.
- **Path traversal** — `..` is resolved away, so a request can't escape an
  allowed directory.
- **Shell/command execution** (`allow_shell` is `false`).
- Anything under a configured `blocked_paths` entry.

### How approvals work

When a write or destructive action would need a human yes/no, it's parked as a
**pending approval** (persisted to `data/state/approvals.json`, with a 24h
expiry). You resolve them over the API:

```
GET  /approvals                       # list (optional ?status=pending|approved|denied|expired)
POST /approvals                       # park a new pending approval
POST /approvals/{id}/approve          # approve  (idempotent; 409 if already denied/expired)
POST /approvals/{id}/deny             # deny     (404 if unknown)
```

All four are bearer-token gated. Each record carries an id, timestamps, status,
action summary, risk level, the target path/command, and any session/job id.

### Audit log

Every permission decision and approval transition is appended as one JSON line to
**`data/logs/audit.jsonl`** — timestamp, action type, target, allowed/denied,
whether approval was required and how it resolved, risk level, and the reason.
Secrets are never written to it.

### Backups

When `backup_before_write` is on and a write would overwrite an existing file,
the original is first copied to `data/backups/<timestamp>_<name>` and the backup
path is recorded in the audit log (and, for a proposed change, in its
`backup_path` field). Files above `max_file_size_mb` are not backed up.

**Recovering from a backup:** find the file's most recent backup in
`data/backups/` (they're prefixed with a sortable `YYYYMMDD_HHMMSS_` timestamp),
or read the `backup_path` from the change record / audit log, and copy it back
over the target:

```powershell
Copy-Item "data\backups\20260622_141530_notes.md" "data\drafts\notes.md" -Force
```

Backups are plain copies — no special tooling needed to restore them.

> The permission layer is wired into every write the server performs (uploads and
> applied proposed changes run through it) and the approval endpoints are live. Claude's
> own in-subprocess edits are kept off your files by **safe agent mode** (next). See
> `CHANGELOG.md` for the full history.

---

## Safe agent mode (`draft_only`)

Phase 2 made the **server** a safe write boundary. But Claude Code runs in its
own subprocess, and the server only sees the final reply — it can't intercept
Claude's individual tool calls. So if Claude can edit files directly, the
permission layer isn't the *true* boundary. Phase 3 closes that gap.

### Server-side writes vs. Claude's internal writes

- **Server-side writes** (uploads, applied proposed changes) go through
  `permissions.py` — allow-list, backup, audit. Fully controlled.
- **Claude's internal tool writes** (its `Write`/`Edit`/`Bash` tools) happen
  inside the `claude` subprocess. The server can't gate them per-call. They are
  governed by *Claude Code's* permission config — which on a customer's machine
  might be permissive (`acceptEdits`, `bypassPermissions`, or an allow-list).

We verified empirically (`scripts/agent-write-probe.ps1`) that with a permissive
permission mode, Claude **will** write to its working directory directly. Relying
on "headless can't prompt, so it's blocked" is **not** a guarantee.

### How Phase 3 enforces safety

`agent_safety.mode` in `settings.json` controls the spawn. Default is the safe
mode:

| Mode | Behavior |
|---|---|
| **`draft_only`** *(default)* | Claude is spawned with **no file-mutation or shell tools** (`--disallowedTools Write Edit … Bash`) and its **working directory is a throwaway sandbox** (`data/agent_workspace`), not your files. The vault is added as **read-only context**. Claude reads and *proposes* changes; it cannot write your files. |
| `controlled_write` | Same Claude-side restriction. Reserved for future server-mediated write automation. |
| `legacy_direct` | Old behavior: `cwd` = vault, full tools, no restriction. **Personal use only — unsafe for a product.** |

The enforcement is **`--disallowedTools` at the CLI**, which we verified overrides
even an auto-accept permission mode (the denied tools simply don't exist for the
session). The sandbox `cwd` is a second layer: even a stray write lands in
throwaway space, not your files. **This is enforcement, not a prompt** — the
prompt instruction to "propose, don't edit" is only a helpful nudge on top.

Other `agent_safety` keys:

| Key | Default | Meaning |
|---|---|---|
| `allow_direct_claude_writes` | `false` | Direct Claude writes happen only in `legacy_direct`. |
| `force_outputs_to_drafts` | `true` | Proposed file targets are routed under `data/drafts`. |
| `require_server_applied_writes` | `true` | Real writes happen only via the server's apply step. |
| `denied_tools` | `Write, Edit, MultiEdit, NotebookEdit, Bash, BashOutput, KillShell` | Tools denied in safe modes. |

### Proposed changes

In a safe mode, when Claude wants a file created/changed it emits a
`<<PROPOSE …>>` block, which the server extracts into a **proposed change** —
pending until you approve it. You can also create them directly via the API.

```
GET  /proposed-changes                  # list (content + diff omitted), ?status= filter
GET  /proposed-changes/{id}             # full record incl. proposed content + diff
POST /proposed-changes                  # create one
POST /proposed-changes/{id}/approve     # approve (does NOT apply)
POST /proposed-changes/{id}/deny        # deny
POST /proposed-changes/{id}/apply       # apply an APPROVED change to disk
GET  /proposed-changes/{id}/diff        # unified diff + hashes/sizes (Phase 4)
GET  /proposed-changes/{id}/status      # live status + staleness check (Phase 4)
POST /proposed-changes/{id}/refresh-diff# re-baseline + regenerate diff (Phase 4)
```

`apply` is the **only** path that writes the file, and it re-runs the full
permission check (allow-list + blocked + protected), checks **staleness** (see
below), **backs up** an existing file first, refuses out-of-allow-list / blocked
/ protected targets, gates destructive actions on approval, and audits the whole
lifecycle. A **denied** change is never applied.

## Change review: diffs, hashes, conflict detection

Change review is trustworthy: you see exactly what will change, and the server refuses
to overwrite work that changed since the proposal was made.

### Diffs

When a change is proposed, the server generates a **unified diff**:
- **create** → diff against empty (everything added).
- **edit / replace** → diff of the current file content vs. the proposed content.
- **delete** → a deletion diff (everything removed).
- **rename** → rename metadata (`- old` / `+ new`).

Line endings are normalized for the diff (CRLF vs LF won't show as every line
changed). If the target is **binary, too large, unreadable, or not UTF-8**, the
diff is marked *unavailable* with a clear reason — the flow never crashes.

### Hashes

Each proposal stores a **SHA-256** and byte size of the target as it was at
proposal time (`original_hash`/`original_size`) and of the proposed content
(`proposed_hash`/`proposed_size`). Hashes are computed over **raw bytes**, so even
binary files get a baseline for conflict detection.

### Staleness / conflict detection

On **apply**, the server recomputes the target's current hash and compares it to
the baseline:
- **match** → apply proceeds (backup → write → audit).
- **mismatch** (file changed, deleted, or newly created since the proposal) →
  apply is **blocked**, the change goes to **`conflict`**, and a
  `proposed_change_conflict` event is audited. Your newer work is **never**
  silently overwritten. The API returns **409** with the reason.

To recover, call **refresh-diff** (or hit **Refresh** in the PWA): it re-reads
the file, regenerates the diff against the current content, and resets the change
to `pending` so you re-review and re-approve. There is intentionally **no
force-apply** — a changed file must be re-reviewed.

Older records with no stored baseline skip the staleness check and apply as before —
backward compatible.

### Approving from the PWA

The app has a **✓ Pending** button in the header. The panel lists changes
needing attention (pending, approved, conflict, failed) and shows, per change:
type, target (and rename destination), risk, status, a colorized **unified
diff** (or the unavailable reason), a **conflict/stale warning**, the backup path
once applied, and **Approve / Deny / Apply / Refresh** buttons. A badge shows the
count needing attention. (Functional over fancy — it's the v1 surface.)

### How to test it

```powershell
.\scripts\test-permissions.ps1        # permission layer (43 checks)
python test_proposed_changes.py       # proposed-change / review flow (68 checks)
.\scripts\agent-write-probe.ps1       # MANUAL: can Claude write directly? (spawns claude)
```

The write probe spawns the real `claude` CLI twice in a temp dir (never your
vault): unrestricted (writes) vs. restricted (blocked), proving the enforcement.

#### Generating VAPID keys (optional, for push)

```powershell
python -c "from py_vapid import Vapid01; v=Vapid01(); v.generate_keys(); v.save_key('data/state/vapid_private.pem')"
```
Then derive the public key per the `py_vapid`/`pywebpush` docs and put it in
`VAPID_PUBLIC_KEY`. Leave push values blank to run without notifications.

---

## Persistent runtime state (jobs)

Async jobs and their history **survive the process** — a server restart, crash, or
machine sleep no longer loses an in-flight or finished job (which used to give the phone
a 404 and *"Connection error — session reset"*). Jobs live in SQLite so Adam behaves
like real local desktop software.

### Where state lives

| What | Where | Format |
|---|---|---|
| **Async jobs + history** *(new)* | `data/state/adam.db` | **SQLite** (stdlib `sqlite3`, no new dependency) |
| Push subscriptions | `data/state/push_sub.json` | JSON *(already persisted; unchanged)* |
| Last finished result | `data/state/last_result.json` | JSON *(already persisted; unchanged)* |
| Pending approvals | `data/state/approvals.json` | JSON *(unchanged)* |
| Proposed changes | `data/state/proposed_changes.json` | JSON *(unchanged)* |

SQLite was chosen for **jobs** specifically: they need status updates,
query-by-id, list-recent, and durable history — exactly a database's strengths,
and far more reliable than growing loose JSON as the app matures. The
already-working JSON stores were **intentionally left as-is** — migrating them
would add risk and surface for no benefit (Phase 5 deliberately does *not*
overbuild a full state database).

### What survives a restart

- **Completed jobs** — full result, spoken summary, mode, session id, timestamp,
  and any proposed-change summary. Re-polling returns the result (idempotent; the
  PWA dedupes by `ts`).
- **Failed jobs** — with their error.
- **Job history** — visible via the new `/jobs` endpoints.
- Push subscriptions and the last result (as before).

### What does **not** survive (by design)

- The OS process and its running Claude subprocess. A job that was *mid-flight*
  cannot resume — its worker died with the old process.
- The foreground heartbeat (`/push/seen`) — ephemeral; it just suppresses a
  banner while the app is on-screen.

### How interrupted jobs are handled

On startup, **before the app serves any traffic**, recovery scans for jobs left
`queued`/`running` by the previous process and marks each **`interrupted`** —
never silently complete. It preserves the job's last-known info, stamps
`interrupted_at`, sets a human reason, and writes a `job_interrupted_on_startup`
audit event. Polling such a job returns wire status `error` with that reason, so
the PWA resets the session cleanly instead of hitting a 404. **Interrupted jobs
are not auto-rerun** — that would need an explicit, deliberate design.

### Job statuses + endpoints

Canonical statuses: `queued · running · complete · failed · interrupted ·
cancelled`. The `/poll` wire vocabulary is unchanged — `complete`→`done`,
`failed`/`interrupted`/`cancelled`→`error`, `queued`/`running`→`running` — so the
PWA is untouched.

```
POST /ask_async            # unchanged contract; the job is now persisted first
GET  /poll/{id}            # unchanged response shape (running/done/error)
GET  /jobs                 # NEW — recent jobs, newest first (?status=, ?limit=)
GET  /jobs/{id}            # NEW — one job's full persistent record
```

`/jobs` and `/jobs/{id}` are **bearer-token gated** and expose the full canonical
record (for the future desktop companion/tray and PWA history). They include a
**truncated input summary** (default 200 chars) — never the full prompt. The full
result *is* stored, consistent with the existing `last_result.json`. **No secrets,
tokens, or push keys are ever written to the job DB.**

### Schema versioning

`adam.db` carries a `schema_version` table. On startup the schema is created if
missing and a minimal forward-migration function runs (a clear log line reports
the version). It's deliberately small — no heavyweight migration framework.

### Resetting local state safely

Stop the server first, then delete the DB (and WAL sidecars). A fresh one is
recreated on next start:

```powershell
.\scripts\stop-dev.ps1
Remove-Item data\state\adam.db, data\state\adam.db-wal, data\state\adam.db-shm -ErrorAction SilentlyContinue
```

This clears job history only; approvals, proposed changes, push subs, and the
last result are separate files and are untouched.

### How to test it

```powershell
python test_job_store.py              # Phase 5 persistence + recovery (47 checks)
```

Covers create/get, status updates, list+filter, **restart simulation marks a
running job interrupted**, completed/failed jobs survive a restart, the wire
mapping, delivered bookkeeping, schema versioning, and sweep.

---

## Run it

**Easiest — one launcher** (after `setup.py`): double-click or run

```powershell
.\scripts\start-adam.ps1
```

It checks Python is present, reminds you to run `setup.py` first if `.env` is missing,
starts the server in a **visible** window (nothing hidden — no service, no autostart),
and opens the app in your browser. If the server is already running it just opens the
browser instead of starting a duplicate. To stop, close that window or run
`scripts\stop-dev.ps1`.

<details>
<summary>Manual dev scripts (equivalent)</summary>

```powershell
.\scripts\start-dev.ps1        # start (foreground)
.\scripts\health-check.ps1     # check it's up (from another window)
.\scripts\stop-dev.ps1         # stop
.\scripts\restart-dev.ps1      # restart
```
</details>

Then open the app:

- On the desktop: <http://localhost:8000> (the launcher opens this for you). On the PC,
  `localhost` is a secure context, so **voice works with no networking setup**.
- On your phone: use the **Tailscale Serve HTTPS** URL — see
  [Connect your phone](docs/CONNECT_YOUR_PHONE.md). Plain `http://<your-pc-ip>:8000` is
  **not** a secure context on a phone, so the mic/voice won't work and the app shows an
  insecure-connection banner.
- In the app's settings field, set the server URL if it isn't the page origin, and paste
  your `ADAM_TOKEN`. (Tip: run `.\scripts\copy-token.ps1` to put the token on your
  clipboard instead of opening `.env` by hand.)

> The port shown above is the default in `settings.example.json`; your install may use a
> different one — check `/health` or the launcher output.

### Operator console (read-only)

A desktop-friendly operator console is served at <http://localhost:8000/console>.
Paste your `ADAM_TOKEN` once (kept in the browser's `localStorage`, never
re-displayed) to view server status, job history, the proposed-change/approval
queue, diffs, and a read-only **Audit log** view (newest-first, bounded, with a
50/100/200/500 limit selector).

As of **v0.8.2** the console can also **act** on proposed changes and approvals —
**Approve & Apply / Apply / Deny / Refresh** on a change (state-aware buttons,
with a confirmation that names the action + target before anything is written) and
**Approve / Deny** on pending approvals. These call the **same token-gated
endpoints the phone PWA already uses** — there is **no new server endpoint and no
new write path**, and apply still runs the server's permission re-check,
stale/conflict gate, backup, and audit. `agent_safety.mode` stays `draft_only` and
the server remains the sole writer. (Console added in v0.8.0; audit viewer v0.8.1;
action controls v0.8.2.)

The audit view is backed by `GET /audit`, a token-gated read of the structured
audit log (`data/logs/audit.jsonl`) only: bounded tail, defensive parsing, a fixed
field whitelist, and no secrets — it never exposes the raw server log.

### Verify `/health`

```powershell
curl http://localhost:8000/health
```
Expected:
```json
{
  "status": "ok",
  "app": "adam-local",
  "version": "0.9.35",
  "claude_configured": true,
  "vault_configured": true,
  "permissions": { "write_dirs": ["...\\data\\outputs", "...\\data\\drafts"], "...": "..." },
  "agent_safety": { "mode": "draft_only", "tools_restricted": true, "...": "..." }
}
```
No secrets are ever returned by `/health`. The `permissions` and `agent_safety`
blocks reflect the active Level 3 + safe-agent policy.

### Run the permission tests

```powershell
.\scripts\test-permissions.ps1
```
Exercises the permission layer (read/write allow-lists, blocked + protected
paths, destructive detection, backups, audit log, approval lifecycle) against a
throwaway sandbox — it never touches your real `data/` tree.

---

## Where things live

```
adam-local/
  server.py              # FastAPI backend (routes + Claude subprocess)
  config.py              # single config layer (.env + settings.json + defaults)
  permissions.py         # Level 3 permission manager (allow-lists, audit, backups)
  approvals.py           # generic pending-approval store (create/list/approve/deny)
  proposed_changes.py    # safe-agent file-change flow (create/approve/deny/apply/refresh)
  diffs.py               # Phase 4 diff + hash helpers (review baseline, staleness)
  job_store.py           # Phase 5 persistent job store (SQLite) + startup recovery
  test_permissions.py    # Phase 2 permission-system test suite
  test_proposed_changes.py # Phase 3 + 4 proposed-change / change-review test suite
  test_job_store.py      # Phase 5 persistent-job-store test suite
  agent_write_probe.py   # manual: can Claude write directly? (spawns claude)
  web/                   # the PWA (index.html) + operator console (console.html), sw.js, manifest.json, icon.png
  scripts/               # setup.py, doctor.py, make_release.py + start/stop/restart/health-check/test-permissions/agent-write-probe
  docs/                  # user-facing docs (BETA_HANDOFF, CONNECT_YOUR_PHONE, SUPPORT, RELEASE, ADVANCED_REMOTE, …)
  data/                  # runtime data (gitignored)
    uploads/             #   uploaded files
    outputs/, drafts/    #   default write targets (the only writable dirs)
    agent_workspace/     #   throwaway sandbox cwd Claude runs in (safe modes)
    backups/             #   pre-overwrite backups
    logs/                #   voice_server.log + audit.jsonl
    state/               #   adam.db (jobs), push subs, last result, approvals.json, proposed_changes.json
  .env.example           # secrets template -> copy to .env
  settings.example.json  # settings template -> copy to settings.json
  requirements.txt
  README.md
  CHANGELOG.md
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ADAM_TOKEN missing` at startup | Copy `.env.example` to `.env`, set `ADAM_TOKEN`. |
| `Claude executable not found` | Install Claude Code, or set `claude_exe` in `settings.json`. |
| `/health` shows `vault_configured: false` | Set `vault_path` to an existing folder. |
| 403 from the API | The bearer token in the app doesn't match `ADAM_TOKEN`. |
| Replies don't speak | Local TTS is down — the app falls back to browser voice. Check `tts_url`. |
| Push never arrives | VAPID not configured, or the PWA isn't installed to the Home Screen. |
| Port already in use | Run `scripts\stop-dev.ps1`, or change `port` in `settings.json`. |

Logs: `data/logs/voice_server.log` (rotating). The PWA also beacons failures
there via `/clientlog`, since the phone has no dev console.

---

## Known limitations

- Conflict detection is whole-file **hash equality**, not a 3-way merge. A
  changed target blocks the apply (conflict) — it does not auto-merge. You
  refresh and re-approve. There is **no force-apply** (by design).
- Diffs are line-based **UTF-8** only; binary / too-large / unreadable targets
  show as *diff unavailable* (hashes still protect them from blind overwrite).
- Single bearer-token auth (constant-time compared); CORS defaults to open (the token is
  the gate — restrict `cors_allowed_origins` if you expose it). No per-device tokens yet.
- Jobs persist (Phase 5), but a job that was **mid-flight** at restart cannot
  resume — it's marked `interrupted`, not re-run. Resumable jobs are future work.
- Single-user assumptions (one push-subscription list, one "last result", one
  job DB).
- Windows-only for now.
- No tray app or auto-start yet — the server runs in a visible window you keep open.
- **Safe-agent enforcement = `--disallowedTools` + sandbox `cwd`.** This blocks
  Claude's file-write/shell tools at the CLI (verified). It is not a kernel-level
  sandbox: a future Claude Code tool not in `denied_tools`, or a misconfiguration
  back to `legacy_direct`, would reopen direct writes. Keep `denied_tools` current.
- `draft_only` changes work-mode behavior: Claude proposes instead of editing,
  and `cwd` is a sandbox, so the vault's `CLAUDE.md` auto-load (cwd-based) doesn't
  apply — the vault is read context via `--add-dir` instead.
- Proposed-change extraction depends on Claude emitting the `<<PROPOSE>>` format;
  a malformed block is skipped (best-effort), not applied.
- Destructive detection is rule-based (conservative; may over-flag).

## Security note

Run it on a machine and network you trust. Keep your real `.env` and `data/` out of
version control (both are gitignored). The bearer token is what stands between the API
and your files — make it long and random (setup does this for you), and **don't expose
the port to the open internet**. For phone access use the supported **Tailscale Serve**
path (private HTTPS, no public exposure — see
[Connect your phone](docs/CONNECT_YOUR_PHONE.md)); a public tunnel is advanced and
**requires Cloudflare Access** ([details](docs/ADVANCED_REMOTE.md)). The token is
compared in constant time, and no secret is printed, logged, returned by `/health`, or
included in a release ZIP.

## What's next

The 0.9.x line delivered mobile access, packaging, the setup wizard, one-click in-app
updates, and the optional high-quality voice. The remaining road to **v1.0** is beta
hardening: tray/auto-start, per-device auth, and cold-install validation with outside
testers. See `CHANGELOG.md` for the full history.
