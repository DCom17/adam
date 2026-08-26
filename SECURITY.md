# Security Policy

Adam is local-first software from Campos Systems LLC. It runs on your own PC,
holds no accounts, and sends no telemetry — so the security surface is your
machine, not our servers. Reports still matter, and they are read by a human.

## Reporting a vulnerability

Email **security@camposenterprises.org**.

Please include what you need to describe the issue and nothing more:

- what you found and roughly how severe you think it is
- the steps to reproduce it (a minimal case is ideal)
- the Adam version (`Settings → About`, or the `version` field from `/health`)
- your OS and how you reach Adam (desktop only, or phone over Tailscale)

**Do not** open a public GitHub issue for a security problem, and please don't
include real personal data, tokens, or API keys in a report — a redacted
transcript is always enough.

You should get a reply within **5 business days**. This is a one-person
project, so please read that as a genuine commitment rather than a support SLA.
If a fix is warranted, it ships in a normal release and the advisory is noted
in `CHANGELOG.md`.

## Scope

**In scope** — this repo and the shipped app:

- the local FastAPI server (`server.py`, `routers/`)
- the PWA front end (`web/`)
- the installer, updater, and release tooling (`scripts/`)
- the licensing and token handling (`licensing.py`, `config.py`)
- the public site (`site/`, served at adam.camposenterprises.org)

**Out of scope:**

- Anthropic, Google, Twilio, Tailscale, Paddle and GitHub themselves — report
  those to the relevant vendor
- anything requiring prior physical access to an already-unlocked machine
- a user deliberately widening their own exposure (binding `0.0.0.0` on an
  untrusted network, publishing the port, or sharing their token)
- social engineering, and volumetric denial of service against a machine you
  do not own

## Security model, in brief

- **One bearer token is the gate.** Every route that returns anything requires
  it. Anonymous routes (`/ping`, minimal `/health`, static shells) carry
  nothing worth reading. Rotate the token with `ROTATE-TOKEN.cmd`.
- **Loopback by default.** The server binds `127.0.0.1`. Tailscale Serve
  proxies to loopback, so phone access needs no wider binding.
- **Secrets are write-only.** API keys and add-on credentials are stored in a
  local `.env`, never returned by the API, never logged, and stripped from the
  diagnostics bundle before it can be shared.
- **Nothing executes unasked.** The shipped product runs in safe mode: no
  shell, and external writes only through the review-and-approve flow.
- **Rate limits on every route.** See `rate_limit.py`.
- **Bounded input.** Every request model rejects unknown fields and caps
  string size; see `models.py`. Uploads are checked against the size cap while
  they are read, and their contents must match the extension they claim.
- **No interactive schema.** `/docs`, `/redoc` and `/openapi.json` are disabled;
  auth here is a per-route dependency, not middleware, so leaving them on would
  have published a route map to anyone who could reach the port.
- **Untrusted input runs clamped.** An inbound SMS is text this machine did not
  author, handed to the agent with an instruction to act on it. Those turns are
  forced into the restricted spawn and never auto-apply a change, whatever the
  configured capability tier says.
- **Subprocess output is scrubbed.** The Claude CLI echoes a rejected API key
  back on stderr, and that text reaches both the log and the client. It is
  redacted at the point it becomes a failure string, not at each call site.

## Data at rest — what Adam does not do

Adam does **not** encrypt your data on disk. Notes, trackers, conversation
history and settings sit in ordinary files and SQLite databases that anyone with
your OS account, or the drive, can read.

This is a deliberate consequence of the design rather than an oversight: the data
never leaves your machine, so there is no service holding it — and equally none
encrypting it. App-level encryption would mean a key that either lives next to
the data (protecting nothing) or can be lost (destroying everything), and it
would not protect against the realistic threat, which is someone reaching your
unlocked machine or your drive.

**Use full-disk encryption instead** — BitLocker on Windows. It is the correct
layer, it covers everything else on the disk, and it is one setting.

`Settings → Back up your data` exports what cannot be regenerated as a single
ZIP. It excludes `.env`, private keys, logs and old backups by design, so the
archive is safe to keep in cloud storage.

## Supported versions

Fixes land on the current release line only. Adam updates itself in place, so
"supported" means the latest published version at
`github.com/DCom17/adam-releases/releases/latest`.
