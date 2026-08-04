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
  string size; see `models.py`.

## Supported versions

Fixes land on the current release line only. Adam updates itself in place, so
"supported" means the latest published version at
`github.com/DCom17/adam-releases/releases/latest`.
