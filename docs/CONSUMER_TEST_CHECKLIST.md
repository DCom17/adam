# Adam — Consumer Test Checklist

**Version:** 1.8 (acceptance checklist for app v0.9.0)
**Date:** 2026-06-24
**Status:** living document — **real-device pass CLOSED** (Phase 5 trust core), and the
**v0.9 manual PC + iPhone Tailscale round-trip is now PASS (owner-verified 2026-06-24)**
— the supported mobile path is confirmed on real hardware (section below). The headless/back-end half passed 2026-06-22 (zero blockers), the PWA
reached voice-loop parity in Phase 6 (v0.6.0), and the **real-device pass ran on a real
iPhone 2026-06-23 (119 PASS · 0 FAIL).** Remaining deferrals are non-iPhone / by-design.
v0.7.0 added guided onboarding + a setup doctor; v0.8.0–v0.8.2 added the Operator Console
(read views + audit + action layer); v0.9.0 added mobile access & packaging (Tailscale
Serve path, constant-time auth, access diagnostics, secure-context banners, secret-safe
release builder) — all validated below except the manual Tailscale round-trip.
**v1.8:** recorded the v1.0 consumer slices in the status boundary — Slice 1 (launcher)
ACCEPTED, Slice 2 (connect-phone helper) ACCEPTED, Slice 3 (Local QR Handoff) ACCEPTED
(full suite 558/558, doctor 15/0/0, QR programmatically decoded in headless tests); and
captured the preserved v1.0 product requirements (first-class Adam voice; Tailscale
HTTPS as the phone path).
**v1.7:** added the v0.9 manual Tailscale verification section (PENDING) and recorded the
v0.9.0 automated validation (463/463 suite incl. auth/CORS, banner, and release tests;
doctor 15/0/0).
**v1.6:** recorded the v0.8.2 action-layer validation (full suite + headless-Chrome
rendered action checks; see the v0.8.2 block below).
**v1.5:** recorded the v0.8.1 audit-viewer validation (API + headless-Chrome rendered
manual browser check; see the v0.8.1 block below).
**v1.4:** recorded the v0.8.0 operator-console HTTP/source + visual-browser validation
(see the v0.8.0 block below).
**v1.3:** recorded the v0.7.0 setup/doctor manual validation (see the v0.7.0 block
below); updated header status to device-pass-closed.
**v1.2:** added the Phase 6 device-readiness map; marked audio/voice rows code-complete
(parity ported) but device-unverified.
**v1.1:** added §7b push/last-result, §11 regression smoke test, tiered Alpha/Beta/Ship gates,
Android (D7) rows.

**Release-gate tally (2026-06-23):** **119 PASS · 0 FAIL · 11 DEFERRED · 8 N/A · 0 untested.**
iPhone/client acceptance pass complete (AUTH-1/2, UP-5, JOB-5 verified on the installed
PWA this pass; JOB-12 and REL-9 closed as logic-verified / N/A-by-design, non-gating).
Remaining deferrals are non-iPhone: §9 desktop sanity, D7 Android (no device), §10 DEF
glasses/desktop/by-design rows.

## v0.9 / v1.0 status boundary (2026-06-24)

Where the product stands for beta, and the line between what's shipped and what's not:

- **v0.9.0 — ACCEPTED.** Mobile access & packaging: constant-time auth, intent-gated
  access diagnostics, secure-context banners (PWA + console), Tailscale Serve documented
  as the supported mobile path, desktop-local floor, secret-safe release builder,
  user-facing docs. Automated validation: full suite **470/470**, doctor **15/0/0**,
  `draft_only`, release ZIP secret-free.
- **Post-checkpoint Patch 1 — ACCEPTED.** `make_release` default version label fixed
  (`python scripts/make_release.py` → `adam-local-v0.9.0.zip`); `--version`
  override intact; release tests expanded.
- **Post-checkpoint Patch 2 — ACCEPTED.** Doctor now detects Tailscale via PATH **and**
  the standard Windows install paths (real doctor: "Tailscale detected and up");
  desktop-local-with-no-Tailscale still 0 WARN; `test_onboarding` → 65.
- **v1.0 Slice 1 — ACCEPTED.** Simple one-click launcher
  `scripts/start-adam.ps1` (visible window, no service/autostart, no duplicate, opens
  the browser), setup-guidance + README/BETA_HANDOFF consumer polish, launcher added to
  the release manifest. No runtime/endpoint/write-path/auth/CORS change.
- **v1.0 Slice 2 — ACCEPTED.** Read-only `scripts/connect-phone.py` (+ `.ps1`) helper:
  detects Tailscale + any existing serve on `:443`, recommends a free HTTPS
  port (`:8443`), prints the exact `tailscale serve` command + resulting phone URL +
  on-phone checklist + safe port-scoped teardown. Print-only — never mutates Tailscale,
  never `reset`/Funnel, no token in output. `test_connect_phone.py` 38 checks.
- **v1.0 Slice 3 — ACCEPTED (Local QR Handoff).** New **Connect phone** section in the
  Operator Console (after Status). Fully client-side: click **Show phone QR** to render
  a QR of the plain-text payload `Adam / URL: <https url> / TOKEN: <token>`
  to a `<canvas>`. No `?token=` param, no auto-consume, no browser history, token from
  `localStorage` at runtime only, never in served HTML, never sent to the server for QR,
  never logged, never saved as an image. Token hidden by default; **Reveal token** is
  explicit; **Hide** clears the canvas and re-masks/clears the token. Encoder is an
  inlined, **vendored MIT** `qrcode-generator` copy (pinned to a commit, no CDN, no
  remote asset, no new static-file route, no endpoint). Validation: `test_console.py`
  **79 → 116** checks (static + optional headless render/decode); full suite **558/558**;
  doctor **15/0/0**; release ZIP carries no real token / no `.env`. **In headless tests
  the rendered QR was screenshotted and decoded to the exact payload (programmatically
  scannable); a quick on-device iPhone scan is still recommended as real-world confirm.**
  - **ON-DEVICE iPhone gate (2026-06-24): PARTIAL / BLOCKED — not unsafe, not broken, but
    the convenience goal is not met on the stock iPhone Camera.** Finding: the single
    URL+TOKEN plain-text QR contains a real `https://` URL, so iOS Camera extracts ONLY the
    URL (linkifies it) and hides the `TOKEN:` line; its Copy returns just the URL. The
    automated decoder confirms the token IS encoded, but a normal iPhone user with the
    default Camera cannot get the token — so the single-QR design does not actually solve
    the 48-char token mis-paste friction on iOS. Owner-verified on-device: URL is correct
    `:8443` and clean (no `?token=`); "already logged in" was cached session, not the QR
    (URL grants nothing on its own); Hide clears; Reveal works on the PC console. **DO NOT
    market/claim QR token-handoff as working on iPhone, and DO NOT move toward invites on
    that claim, until the split-QR patch (URL-only QR + token-only QR) is implemented and
    re-tested on a stock iPhone.** APP_VERSION stays 0.9.0.
  - **Slice 3.1 SPLIT-QR patch — IMPLEMENTED + automated-verified 2026-06-24 (still
    PARTIAL/BLOCKED pending owner on-device retest).** `web/console.html` now shows a 3-step
    Connect phone layout with TWO separate QRs: **Show URL QR** (payload = the Adam HTTPS
    URL only) and **Show token QR** (payload = the BARE token only — no `https`, no `URL:`,
    no title — so iOS Camera reads it as copyable plain text). Single **Hide** clears both
    canvases + re-masks the token; **Reveal token** stays explicit. Same vendored MIT
    encoder, no `?token=`, no endpoint/write-path/auth/CORS/Tailscale change. Tests:
    `test_console.py` **116 → 127** — the optional headless block now screenshots and
    **decodes BOTH QRs**, asserting the URL QR == the URL exactly (no token) and the token
    QR == the bare token exactly (no `http`/`https`/`URL:`/title). Full suite **569/569**;
    doctor **15/0/0**; the rebuilt `dist/adam-local-v0.9.0-beta.1.zip` ships the
    split-QR `console.html`, carries no real token, and reports APP_VERSION 0.9.0.
  - **Slice 3 on-device iPhone gate — PASS (owner-confirmed 2026-06-24).** The stock-iPhone
    retest of the split-QR design passed: URL QR opens the Adam `:8443` URL; token QR is
    surfaced by iOS Camera as copyable text (token obtained without typing); token not in
    the URL; no auto-login from the URL alone; Hide clears both QRs; phone setup completed
    without manually typing the token. **Slice 3 (Local QR Handoff, split-QR form) is now
    ACCEPTED / PASS.** The F&F beta is **unblocked**; remaining steps (publish the
    private/unlisted release + send invites) are owner-gated outward-facing actions, not yet
    taken. APP_VERSION stays 0.9.0.
- **Manual PC + iPhone Tailscale round-trip — PASS (owner-verified 2026-06-24).** The
  supported mobile path works end-to-end on real hardware (Safari over Tailscale Serve
  HTTPS → token → mic/voice → response; banner hidden on HTTPS, shown on plain LAN http;
  desktop-local floor intact). Details in the section below. (Two retry hiccups were a
  token mis-paste and a not-on-same-LAN issue — not product bugs.)
- **DEFERRED to v1.0+ (NOT built):** the non-technical consumer wrapper — **installer**,
  bundled Python, a full **connect-phone wizard** (auto-drives Tailscale install + Serve +
  pairing), **tray / auto-start**. (The read-only connect-phone helper (Slice 2) and the
  local QR token handoff (Slice 3) are now DONE; the *automated* wizard remains deferred.)
- **PRESERVED v1.0 product requirements (not yet met — do not regress):** (1) the **correct
  Adam voice** is a first-class final-product requirement — generic/robot/browser TTS is
  acceptable only as a fallback/beta limitation, never the final experience; (2) **phone
  access away from the same Wi-Fi** is required — supported path is **Tailscale + Tailscale
  Serve HTTPS**; plain LAN http is not the phone voice path, and public bearer-token-only
  exposure is not acceptable (a public cloudflared path, if used, requires Cloudflare
  Access and stays advanced/limited support). Slice 3 layers onto the already-verified
  Tailscale HTTPS path and changes neither the transport nor voice quality.
- **NOT built (deliberately):** per-device tokens / device pairing, tunnel automation,
  multi-user / accounts, hosted SaaS, auto-updater.

**v0.7.0 setup/doctor validation (2026-06-23):** the onboarding/config-robustness work
was validated outside the device-tally rows above (it ships no behavior change to the
runtime). Confirmed: `python scripts/doctor.py` and `python scripts/setup.py` on a
**malformed** `settings.json` → friendly message naming the file + parse line/column,
no traceback, exit 1, file left **byte-identical** (no auto-edit); on a real
**BOM-prefixed** valid `settings.json` → "Configuration file PASS (UTF-8/BOM ok)";
restored real config → 12 checks, 0 FAIL, 0 WARN, `agent_safety.mode = draft_only`.
Unit suite **198/198** (permissions 43 + job_store 47 + proposed_changes 68 +
onboarding 40). **Not human-tested:** the interactive TTY prompt path of `setup.py`
(live `vault_path` / `claude_exe` questions) — optional manual follow-up, non-gating.

**v0.8.0 operator-console validation (2026-06-23):** the read-only Local Operator
Console (`GET /console`, `web/console.html`) was validated outside the device-tally
rows above (it ships no runtime behavior change and no new write surface).

HTTP/source sanity (all PASS): server restart; `/health` after restart; `/console`
loads (static, `Cache-Control: no-store`, no auth on the page shell like `/`); auth
matrix on the data calls — correct token serves, wrong/missing token rejected;
Status/Jobs/Proposed-changes/Approvals data paths return expected data; **read-only
contract** — the page exposes no approve/deny/apply/edit control and no write
endpoint; **secrets not rendered** — no token or secret value appears in page source
or responses. Backed by `test_console.py` (**39** TestClient smokes).

Visual browser check, Chrome/Edge (all PASS): page renders; token sign-in (pasted
once, stored in `localStorage`, never re-displayed); Status card; Jobs table; Job
detail; Problems filter; Proposed-changes table; Diff render; Approvals section;
read-only controls (no action buttons); secrets not visible; layout usable.

Unit suite **237/237** (permissions 43 + job_store 47 + proposed_changes 68 +
onboarding 40 + console 39); doctor on real config 12 checks, 0 FAIL, 0 WARN,
`agent_safety.mode = draft_only`. **Deferred (Slice 2):** action controls
(approve/deny/apply) and a read-only `GET /audit` viewer — not in this release.

**v0.8.1 audit-viewer validation (2026-06-23):** the read-only Audit Viewer
(`GET /audit` + a read-only Audit section in `/console`) was validated outside the
device-tally rows above (it adds no new write surface and no runtime behavior change).

API / route (all PASS): `GET /audit` no token -> 403, wrong token -> 403, valid token
-> 200; reads only `config.AUDIT_LOG_FILE` (no path parameter); newest-first; `?limit=`
default 100, clamped to [1, 500]; bounded `deque` tail; missing/disabled file ->
`available:false` + empty entries, empty file -> `available:true`; malformed line ->
safe `{"_parse_error":true}` marker with **no raw content echoed**; field **whitelist**
drops extra + secret-like keys; `ADAM_TOKEN`/VAPID/TWILIO never in the body. Live on
:8010: 88 real entries, malformed 0, no token leak, clamp verified.

Manual browser check (headless Chrome via the DevTools Protocol — the page's own JS
rendered the signed-in console against the live server, then screenshot-verified; all
PASS): Console still loads · Status section · Jobs section · Proposed changes section ·
Approvals section · Audit section visible · Audit rows render (88) · newest-first order
· columns readable (Time/Event/Target/Risk/Approved/Reason) · limit selector
(50/100/200/500 — switching to 50 yielded 50 rows) · Refresh · no secrets visible · no
raw server logs · no mutation controls (only Refresh/Sign-out buttons present) · layout
usable. Empty/disabled/malformed states render a safe note (unit-verified in
`test_audit.py`) and do not break layout.

Unit suite **288/288** (permissions 43 + job_store 47 + proposed_changes 68 +
onboarding 40 + console 51 + audit 39); doctor on real config 12 checks, 0 FAIL, 0
WARN, `agent_safety.mode = draft_only`. **Deferred (v0.8.2, separate approval gate):**
the console action layer (approve/deny/apply/refresh controls). Out of scope entirely:
settings/permissions/safety-mode editors, auto-apply toggle, raw server-log viewer.

**v0.8.2 action-layer validation (2026-06-23):** the console gained Approve & Apply /
Apply / Deny / Refresh on proposed changes and Approve / Deny on approvals — via the
**existing** token-gated endpoints only. `server.py` unchanged (POST routes 17 = 17),
**no new endpoint, no new write path**; this deliberately retired the console's
strictly-read-only posture (DECISION_LOG D18).

Unit (`test_console.py` 51 → 79, all PASS): single POST sink; every `postApi` target is
an approved proposed-changes/approvals path; never POSTs create or any non-action
route; no `innerHTML` assignment; approve→apply writes; apply-before-approve → 409 + not
written; deny never writes; refresh → pending; conflict → 409 + no overwrite;
protected/blocked create rejected; no secret in any rendered surface.

Manual browser check (headless Chrome via the DevTools Protocol against an **isolated
sandbox server** — no real vault data touched; all PASS): button-state matrix matched
all six states (pending → Approve & Apply/Deny/Refresh; approved → Apply/Refresh/Deny;
approved+stale → Refresh/Deny, no Apply; conflict/failed → Refresh/Deny, no Apply;
applied/denied → no buttons); Approve & Apply via the UI button → `applied` + file
written; Deny via the UI button → `denied` + file **not** written; conflict state
exposes no Apply; an `<img src=x onerror=…>` summary rendered as **literal text** (0
injected `<img>` nodes); approvals rows show Approve/Deny; the token never appears in
the DOM; confirm() fires on every write path naming action + target.

Full suite **316/316** (permissions 43 + job_store 47 + proposed_changes 68 +
onboarding 40 + console 79 + audit 39); doctor on real config 12 checks, 0 FAIL, 0
WARN, `agent_safety.mode = draft_only`. The operator console is now feature-complete.

---

## v0.9 mobile access — manual Tailscale verification (cannot be automated)

This section is the real proof of the v0.9 supported mobile path. It needs a physical
PC + iPhone and a live Tailscale tailnet, so it is a **manual** checklist (the unit
suite covers the doctor diagnostics and the secure-context banner rule; only the live
device round-trip is manual).

**Status: PASS — owner-verified 2026-06-24** on the maintainer's real Windows PC +
iPhone (Safari), same tailnet. Because this machine already had another app served
on the tailnet's 443, Adam was served on a **separate HTTPS port (8443)**
(`tailscale serve --https=8443 http://127.0.0.1:8010`) so that serve stayed intact; the
generic single-app case would use 443. The full round-trip worked end-to-end.

Setup:
- [x] Tailscale installed on the **PC** and signed in.
- [x] Tailscale installed on the **iPhone** and signed into the **same tailnet**.
- [x] Both devices visible in the Tailscale admin console.
- [x] Backend running on the PC; `doctor.py` all PASS/no FAIL.
- [x] `tailscale serve` exposes the backend over the HTTPS tailnet URL (here on `:8443`,
      verified with `tailscale serve status`).

Mobile round-trip (iPhone Safari at the HTTPS tailnet URL):
- [x] Page loads over the HTTPS tailnet URL.
- [x] **Secure-context banner is HIDDEN** (proper secure context confirmed).
- [x] Token auth works — `ADAM_TOKEN`, no 403.
- [x] Microphone permission granted.
- [x] Voice **input** transcribes ("able to hear me").
- [x] Spoken **reply** plays back (browser TTS voice — the local Kokoro TTS is PC-only
      and unreachable from the phone, so the browser voice is the expected fallback).
- [x] **Add to Home Screen** works; the installed PWA opens and authenticates. (An
      initial "auth error" was a **token mis-paste**, not a storage issue — correct
      paste authenticated and voice worked from the Home Screen app.)

Negative / floor checks:
- [x] Desktop-local still works: `http://localhost:8010` on the PC, voice OK, NO banner.
- [x] Plain LAN HTTP on the phone (`http://192.168.1.26:8010`, phone on the **same
      Wi-Fi**) **shows** the secure-context banner and voice is unavailable — exactly as
      intended. (First attempt didn't load because the phone wasn't on the same LAN; it
      passed once on the same Wi-Fi.)
- [x] `doctor.py` reflects the configured state (mobile-access / Tailscale / CORS checks).

**Result:** the supported v0.9 mobile path (Tailscale Serve HTTPS → iPhone Safari/PWA →
token → mic/voice → response) is confirmed on real hardware, the secure-context banner
behaves correctly (hidden on HTTPS, shown on plain LAN http), and desktop-local still
works. No product bug surfaced; the two initial hiccups were a token mis-paste and a
not-on-same-LAN reachability issue, both resolved on retry.

---

## Phase 6 device-readiness map (2026-06-23, v0.6.0)

After Phase 6 the product PWA is at **code parity** with the rig's device-verified
voice loop, and all server-side behavior is headless-verified. What remains is
genuinely device-only. Use this map to plan the human pass.

### A. Code-complete & headless-verified (no device needed to trust the logic)
These passed in the 2026-06-22 headless run and/or the Phase 6 server smoke. Re-confirm
opportunistically, but they are **not** the device gate.
- **Auth / config:** PRE-1…7, SET-2/3, AUTH-2/4/5/6, CFG-1/2/3.
- **Async jobs / recovery:** JOB-1, JOB-4…13 (logic), REL-1…7.
- **Safe agent (trust core):** SAFE-1…14 (proposed-change lifecycle, diff, conflict,
  backup, audit, protected refusal) — server-verified.
- **Review panel logic:** REV-1…8 render/act against real endpoints.
- **Push (no-secret) logic:** PUSH-1, PUSH-7, PUSH-8.

### B. Require a real **iPhone** (D1/D2) — primary device gate
Voice loop, audio, and PWA behaviors that only a real iOS device exercises:
- **PWA shell:** PWA-1…12 (install, service worker, safe-area, offline, cache-bust).
- **Voice in/out:** PWA-7, AUD-1, AUD-5/6 (TTS fallback), AUD-7/8/9, AUD-14.
- **Self-echo (key Phase 6 port):** **AUD-15** — 5+ turns, never re-submits its own reply.
- **Silent-mode playback (Phase 6 `setAudioSession`):** **AUD-2**.
- **Replay transport + hold-to-restart (Phase 6 feature):** **AUD-10/11/12**.
- **Cold-start audio (Phase 6 warm-ups):** **AUD-17**, AUD-16 (+ DEF-3 watch).
- **Notification UX:** PUSH-2…6, and server-initiated-reply-opens-its-own-chat
  (Phase 6) — confirm a push/SMS reply does **not** hijack the open chat.
- **Activation gating (Phase 6):** confirm composer/replay/attach are hidden until
  ACTIVATE, so a typed turn can't fire into a dead state (relates to PWA-6).
- **Uploads from a phone:** UP-1/2/3 (HEIC), UP-10/11.

### C. Require **Bluetooth earbuds** (D3)
- **AUD-3** (route to earbuds, mic works, no echo), **AUD-13** (mid-session switch).

### D. Require **car Bluetooth** (D4)
- **AUD-4** — route to car speakers; **mid-turn kickoff does not drop** (Phase 6
  `KICKOFF_TRIES` retry); hands-free turn completes.

### E. Deferred / optional (not gating this pass)
- §9 desktop full pass (DESK-1…4 / DEF-2), §10 DEF-1 (glasses), DEF-3 (~4s silent
  background-return — watch; Phase 6 warm-up may reduce it), DEF-4/5/6, D7 Android
  (mark `NOT RUN` if no device).

> **Verdict rule (unchanged):** do not mark the product device-passed until sections
> B–D are `[P]` on the relevant devices. Phase 6 made them *runnable and expected to
> pass*; it did **not** run them.

---

## Purpose

Prove that Adam — a **local-first desktop companion + mobile voice
client** — works correctly across every state, transition, and failure path
**before** any new feature work begins. The quality bar is the one set by the
personal-rig dogfood pass: correctness over speed. A test that "usually works" is
a **fail** until it works every time.

This checklist is the de-personalized, product-line version of the internal
master checklist. It assumes a **fresh customer install**: the customer's own PC,
their own files, their own Claude account, a token they generated. It contains no
private vault, domain, repo, routine, or family-archive assumptions.

## Scope

In scope: setup/config, mobile PWA, audio/speech, async jobs + polling, safe
agent behavior (draft-only proposed changes), uploads, the PWA pending/review
panel, reliability/recovery, and a desktop-browser sanity pass.

Out of scope (and intentionally absent): any specific personal command or
routine, any private knowledge base, any pre-wired cloud tunnel/domain, any
multi-user or family-archive behavior. The product ships configured by the
customer via `.env` + `settings.json`; this checklist tests the **product**, not
any one operator's data.

---

## Test environment

Fill this in for each run.

- **App version:** ______ (from `GET /health` → `version`)
- **OS / browser host:** ______ (Windows 10/11 expected for the server)
- **Server URL under test:** `http://<host>:<port>` (default `http://<host>:8000`)
- **Devices in this pass** (mark which ship):
  - **D1 — iPhone, installed PWA** (Add to Home Screen, standalone). *Primary.*
  - **D2 — iPhone Safari tab** (not installed) — catches PWA-vs-tab differences.
  - **D3 — Bluetooth earbuds + phone** — mic/speaker routing path.
  - **D4 — Car Bluetooth** — hands-free routing path.
  - **D5 — Desktop Chrome / Edge** — sanity only (see §9).
  - **D6 — Smart glasses + phone** — *optional, deferred (see §10).*
  - **D7 — Android Chrome** — *secondary; mark `NOT RUN` if no Android device this pass.*
- **TTS service:** running? ☐ yes ☐ no (fallback path tested either way)
- **Push configured?** ☐ yes (VAPID keys set) ☐ no (push tests N/A)
- **Tester / date:** ______

### Where the truth lives (instrumentation)

A phone has no dev console, so failures are captured server-side. **Note the
local time before each session** so you can find your run.

- **Server log:** `data/logs/` (rotating). Boot line, errors, and job failures
  land here.
- **Health:** `GET /health` (status + non-secret config sanity: claude/vault
  configured, model, push/twilio enabled, `jobs_db`, `state_schema_version`,
  `agent_safety`, `permissions`). `GET /ping` = bare liveness (200 = up).
- **Audit log:** `data/logs/audit.jsonl` — one JSON line per permission decision
  and approval/proposed-change transition. No secrets.
- After any test that "did nothing visible," **check the log before marking pass.**

### Marking

`[ ]` untested · `[P]` pass · `[F]` fail (note device, steps, and the log line) ·
`[N/A]` not applicable to this install (e.g. push disabled).

---

## Pre-flight (run before the checklist)

| ID | Step | Expected | ✅ |
|----|------|----------|----|
| PRE-1 | Fresh clone/folder; `.env` and `settings.json` **not** yet created | App refuses to start (or `/health` reports unconfigured); a clear message names the missing file — no silent boot with no token | [P] 2026-06-23 — empty `ADAM_TOKEN` → `config.validate()` raises "ADAM_TOKEN missing. Copy .env.example to .env and set it." at import (server can't boot) |
| PRE-2 | Copy `.env.example`→`.env`, set `ADAM_TOKEN` to a long random value | `.env` present; token is not the placeholder string | [P] 2026-06-23 — instance configured; token len 48, validates |
| PRE-3 | Copy `settings.example.json`→`settings.json`, set `claude_exe` (or rely on PATH auto-detect) and `vault_path`/work dirs as desired | `settings.json` present; paths are project-relative or absolute and valid | [P] 2026-06-23 — `settings.json` present, valid paths (via /health) |
| PRE-4 | Start the server (`scripts/start-dev.ps1`) | Server boots; log shows a clean boot line + `state_schema_version` | [P] 2026-06-23 (assistant-verified, server-side) |
| PRE-5 | `GET /ping` | 200 | [P] 2026-06-23 (via /health 200) |
| PRE-6 | `GET /health` | 200; `version` correct; `claude` configured true; push/twilio flags reflect your `.env`; `agent_safety.mode = draft_only` | [P] 2026-06-23 — version 0.6.0, claude true, push/twilio false, draft_only |
| PRE-7 | Confirm default safety posture | `/health` shows `agent_safety.mode = draft_only`, writes limited to `data/outputs` + `data/drafts`, approval required for writes | [P] 2026-06-23 — draft_only, writes→data/outputs+drafts, approval required |

---

## 1. Setup, configuration & auth

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| SET-1 | Default local URL reachable | `http://<host>:8000/` serves the PWA shell | [P] 2026-06-23 — server serves the PWA shell (verified all session via the HTTPS tunnel) |
| SET-2 | Health check script (`scripts/health-check.ps1`) | Reports up + config sanity, exits clean | [DEFERRED] PS execution policy blocks scripts in this shell; `/health` itself verified — run the script on the user's machine |
| SET-3 | Custom `port` in `settings.json` | Server binds the configured port; `/health` reflects it | [P] 2026-06-23 — runs on custom port 8010 from `settings.json`; `/health` reports `port:8010` |
| AUTH-1 | First open, no token stored on device | Settings modal auto-opens; orb is Standby | [P] 2026-06-23 — iPhone Safari private tab (fresh device): modal auto-opened, orb in Standby |
| AUTH-2 | Save with empty token | Alert "paste your token"; modal stays open | [P] 2026-06-23 — empty Save → alert "Paste your auth token to sign in.", modal stayed open, not signed in |
| AUTH-3 | Paste the **correct** `ADAM_TOKEN`, Save | Modal closes; a turn now works | [P] 2026-06-23 — signed in and ran many turns this session (one-tap + stored token) |
| AUTH-4 | Paste a **wrong** token, then send a turn | Server returns **403**; UI shows "Auth rejected. Check your token." + auto-deactivate; no turn result | [P] 2026-06-23 — wrong token + malformed header → 403; correct → 200 (UI message string verified by inspection) |
| AUTH-5 | Protected endpoint with **no** token (`/ask_async`, `/jobs`, `/proposed-changes`) | **403 Forbidden** on every protected route | [P] 2026-06-23 — `/jobs` no-token 403, wrong-token 403, correct-token 200 (assistant-verified) |
| AUTH-6 | Token with leading/trailing whitespace | Trimmed before use; auth still succeeds | [P] 2026-06-23 — `config.py` `.strip()`: `"  paddedtoken  "`→`"paddedtoken"` |
| AUTH-7 | Reload / reopen after sign-in | Token persists; settings modal does **not** reopen | [P] 2026-06-23 — reopened the app repeatedly all session without re-signing in |
| AUTH-8 | One-tap login via `…/?token=XXX` (if supported) | Token stored; address bar stripped of the token; nothing left in URL/history | [P] 2026-06-23 — used the one-tap URL to sign in; `consumeLoginParams()` strips token via `history.replaceState` (inspection) |
| CFG-1 | **Missing-config failure**: start with `.env` absent or `ADAM_TOKEN` blank | App fails fast with a clear message; it does **not** boot wide-open with no auth | [P] 2026-06-23 — empty token → `config.validate()` raises at import; server can't boot (see PRE-1) |
| CFG-2 | Invalid `claude_exe` path and not on PATH | Startup or `/health` surfaces "claude not configured/found" rather than failing cryptically on first turn | [P] 2026-06-23 — `config.validate()` raises "Claude executable not found…" when `CLAUDE_EXE` unresolved (same fail-fast path; inspection) |
| CFG-3 | `vault_path` / `work_extra_dirs` empty (default) | App runs; work mode simply has no extra context dirs — no crash, no hardcoded path assumed | [P] 2026-06-23 — no `validate()` gate on `vault_path`; used only as read context, absent → no extra dirs, no crash (inspection) |

---

## 2. Mobile PWA basics

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| PWA-1 | Load on iPhone Safari (`http://<host>:8000`) | Page loads; orb renders; layout respects safe-area/notch | [P] 2026-06-23 (Safari, HTTPS tunnel <your-tunnel-hostname>) |
| PWA-2 | **Add to Home Screen** | Installs standalone; app icon + name correct; launches with no Safari chrome | [P] 2026-06-23 |
| PWA-3 | Service worker registers on load | Registers and controls the page | [P] 2026-06-23 |
| PWA-4 | Refresh / reopen the installed app | Reopens to a clean idle state; token + sessions restored from local storage | [P] 2026-06-23 |
| PWA-5 | Local server connection from phone | Phone on same network reaches the host URL; turns succeed | [P] 2026-06-23 (via HTTPS tunnel) |
| PWA-6 | **Text input** | Type a prompt, send; You bubble appears | [P] 2026-06-23 |
| PWA-7 | **Voice input** | Activate, speak; interim text shows live, final paints, turn fires | [P] 2026-06-23 (Safari + HTTPS — needs secure context; see note) |
| PWA-8 | **Response display** | Reply renders as a readable bubble (markdown/symbols not shown raw) | [P] 2026-06-23 |
| PWA-9 | **Conversation continuity** | A follow-up turn keeps context (same session resume id) within the session window | [P] 2026-06-23 |
| PWA-10 | Stale cached frontend after a redeploy | Reopen loads new code; document the cache-bust step (a stale client is a known trap) | [P] 2026-06-23 — header/SW edits propagated on online relaunch; SW nav is network-first by design so online launches always fetch fresh HTML (no stale-client trap) |
| PWA-11 | Manifest correctness | Name, icon, `display=standalone`, theme color all correct | [P] 2026-06-23 |
| PWA-12 | Offline launch | Shell loads if cached; turns fail with a clear error, not a blank hang | [P] 2026-06-23 — **device-verified on installed iOS standalone PWA**: in Airplane Mode the cached shell renders (no blank "not connected" page). Root cause of the earlier blank was a stale/un-installed SW from Safari-vs-installed-app origin confusion, NOT an iOS limitation; once the new SW installed via the correct HTTPS URL it works. SW precaches shell + network-first nav with offline fallback. |
| PWA-13 | **Android Chrome** load + install (D7) | Page loads; "Install app" / Add to Home Screen works; turns succeed. Mark `NOT RUN` if no Android device | [N/A] 2026-06-23 — no Android device this pass |

> **Voice-input requirement (shipping constraint, found 2026-06-23):** the Web Speech
> API (mic → transcription) requires a **secure context** — HTTPS, or `localhost` on
> the host. Plain LAN HTTP (`http://<ip>:<port>`) does **not** expose it, so voice
> input is unavailable there by browser design (verified: needed an HTTPS tunnel to
> pass PWA-7). This is the **only** trigger for the text-only fallback — it is **not**
> an installed-PWA vs Safari distinction. Device-verified 2026-06-23: an **installed
> iOS standalone PWA added from the HTTPS URL is a secure context and voice input
> works there** (an earlier "(b)" text-only result came from a home-screen app added
> from the old HTTP origin, i.e. insecure → API absent). Voice works in both iOS
> Safari and the installed PWA, as long as the origin is HTTPS. Product implication:
> customers need HTTPS (or localhost) for voice — that's the single requirement. The
> app must never dead-end when the API is absent — it engages text-only (fixed
> 2026-06-23).

---

## 3. Audio & speech

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| AUD-1 | Normal reply via TTS | Speaks the reply; orb pulses to real amplitude; gapless between sentences | [P] 2026-06-23 (installed PWA, HTTPS) |
| AUD-2 | **Silent-mode playback** (phone ringer/silent switch ON) | Reply is still **audible** (audio-session switching keeps playback on the media route) | [P] 2026-06-23 |
| AUD-3 | **Bluetooth earbuds** | Audio routes to earbuds; mic input works; no echo loop | [P] 2026-06-23 (device) |
| AUD-4 | **Car Bluetooth** | Routes to car speakers; mid-turn kickoff does not drop (kickoff retry); hands-free turn completes | [P] 2026-06-23 (device) |
| AUD-5 | TTS service **down** | Browser-voice fallback speaks the whole reply; no silent turn | [P] 2026-06-23 (user-reported; browser-voice fallback observed) |
| AUD-6 | TTS dies **mid-reply** | Remaining sentences finish via fallback; nothing lost | [P] 2026-06-23 (user-reported) |
| AUD-7 | Markdown/symbols in reply (`*`, `#`, `—`, list dashes) | Not read literally; dashes become pauses | [P] 2026-06-23 |
| AUD-8 | Empty/whitespace reply | Speaks a safe fallback line; no crash | [P] 2026-06-23 — empty reply coerced to "No response, sir." in all 3 reply paths; `/speak` rejects empty/whitespace/symbols-only with clean 400, server stays up (health 200); client sanitize + browser fallback covers symbols-only |
| AUD-9 | Speak path wedges (no end event) | A watchdog force-settles back to Listening; never stuck on "Speaking" | [P] 2026-06-23 — `speakWatchdog`→`settle()`→`afterSpeak()` at `SPEAK_WEDGE_CAP_MS` (45s); plus the `audioCtx.currentTime`-stall poll backstop. Real wedge (background-return) was device-verified via AUD-16 |
| AUD-10 | **Replay controls** | After a reply, Replay is enabled and replays the spoken text; disabled when nothing has been spoken | [P] 2026-06-23 |
| AUD-11 | **Hold-to-restart Replay** | Press-and-hold restarts the replay from the beginning (transport-style control) | [P] 2026-06-23 |
| AUD-12 | Replay pause/resume | Replay acts as a transport control (pause/resume), not a one-shot only | [P] 2026-06-23 |
| AUD-13 | **Mic/speaker switching** | Switching output (phone ↔ BT ↔ car) mid-session re-routes cleanly; no permanent silence | [P] 2026-06-23 (device) |
| AUD-14 | **Interruption behavior** (deactivate/mute while speaking) | TTS stops promptly; state settles to Standby/Muted; no orphaned audio | [P] 2026-06-23 |
| AUD-15 | Self-echo rejection | Over a 5+ turn conversation, the app never transcribes + submits its own spoken reply as a new turn (mic aborts during playback) | [P] 2026-06-23 (passed at 1200ms mute; **re-test after mic-timing change** → echo-mute now 700ms + warm-start 250ms) |
| AUD-16 | **Background-return behavior** | Background mid-TTS, return: finishes or settles cleanly; reply recoverable via Replay. **Known deferred:** a ~4s silent "Speaking" can occur when returning to an unseen reply (see §10) | [P] 2026-06-23 (fix v3, device-verified) — orb auto-settles on return + Replay speaks the reply. Journey: v1 (visibilitychange force-settle) failed; v2 (rebuild ctx on gesture — Replay worked, but orb still stuck because visibility/focus/pageshow are unreliable in iOS standalone); **v3 = event-independent poll of `audioCtx.currentTime` (frozen clock while foregrounded+"Speaking" → force-settle)**. NB: unprompted background *speech* needs Web Push (VAPID, §7b, currently N/A); iOS forbids background web-audio regardless — without push the reply settles + is Replay-able on return. |
| AUD-17 | First clip after cold audio context (iOS) | Heard the first time (audio prime/keepalive), not silent-then-second-clip | [P] 2026-06-23 |
| AUD-18 | **Mic "warming" honesty on cold start** (new, 2026-06-23) | After a reply the iOS recognizer lags ~2-3s before truly capturing (first words dropped); the orb must NOT claim "Listening" while the mic is dead. Expected: grey **"Waking…"** orb until capture is genuinely live, then "Listening". | [P] 2026-06-23 (device-verified) — v1 flipped on `onaudiostart`/`onsoundstart` but **those fire when iOS opens the stream, ~3 words before real capture** (user: counts 1-2-3 lost, "four" caught, waking icon left before "one"). v2: flip ONLY on the **first real post-echo-mute `onresult`** (proof of actual transcription); 4s silent-fallback cap. **v3 (pre-warm) was REVERTED 2026-06-23** — starting the mic during the greeting asked for permission too early, cut off the greeting, and left the mic flaky. Final: keep the v2 functional behavior (no mic during greeting) and simply **lengthen the "Waking…" floor to ~3s** (`MIN_WARMING_MS=3000`) so the orb stays on "Waking…" through the cold start and only shows "Listening" once capture is real. **RE-TEST:** "Waking…" shows ~3s after the greeting, then "Listening"; greeting plays fully; no early mic prompt. |
| AUD-19 | **Mic lifecycle on background/close** (new, 2026-06-23) | The mic must be released when the app is backgrounded/closed (no persistent iOS orange-mic indicator) and must resume capturing on return; "Listening" must actually hear input. | [P] 2026-06-23 (device-verified, boot-settle gate) — user reported (a) orange mic indicator persists after closing the app and (b) "Listening" went deaf (no input picked up). Cause: the continuous recognizer was never aborted on background/close, so iOS kept the mic open and a lingering session contended with the next one. Fix: `releaseMicForBackground()` on `visibilitychange→hidden` + `pagehide` (detach handlers + abort + flag rebuild); `recoverForeground()` rebuilds + restarts on return (guarded by `!listening`); `deactivate()` also detaches+aborts. **Follow-up (2026-06-23):** user pinpointed the deaf-mic trigger — **activating the instant the app cold-boots** wedges the recognizer (deaf even through mute/deactivate-reactivate; only a reload clears it); waiting a couple seconds before activating avoids it. Added a **boot-settle gate**: `scheduleListen` holds the FIRST `.start()` until `bootAt + BOOT_SETTLE_MS (3500ms)` — only ever delays the first listen of a fresh page, automating the user's workaround. **RE-TEST.** |

---

## 4. Async jobs & polling

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| JOB-1 | **Normal ask** | `/ask_async` → `job_id` → poll → done → painted + spoken | [P] 2026-06-23 — verified throughout the session's live turns |
| JOB-2 | **Work-mode ask** | Long full reply on screen; only the short summary is spoken | [P] 2026-06-23 (device) |
| JOB-3 | **Long job** | "Thinking… Ns" ticks; survives well past 100s without a gateway timeout (the point of async) | [P] 2026-06-23 (device) |
| JOB-4 | **Poll until complete** | Poll loop runs until terminal; result delivered exactly once | [P] 2026-06-23 — session turns + JOB-11 idempotence (dedup-by-ts) prove exactly-once |
| JOB-5 | One poll request times out but job still running | The hiccup is ignored; loop keeps polling; turn still completes | [P] 2026-06-23 — iPhone Home Screen PWA: Airplane Mode toggled mid-turn, poll(s) dropped, no error banner, counter kept climbing, turn completed + spoke once |
| JOB-6 | **Server restart mid-turn (v0.5.0 recovery)** | The in-flight job is marked **`interrupted`** on restart (not silently completed, not auto-rerun); next `/poll` returns an error+reason, and the PWA resets cleanly instead of hanging on a 404 | [P] 2026-06-23 — fired async job, killed server while `running`, restarted → status `interrupted`; `/poll`→`{status:error, error:"Interrupted…"}` (HTTP 200); not auto-rerun |
| JOB-7 | Completed jobs **survive a restart** | A job that finished before the restart is still retrievable; history is intact | [P] 2026-06-23 — job from 11:02 survived multiple server restarts this session |
| JOB-8 | **Job-history endpoint** `GET /jobs` (token-gated) | Returns recent jobs newest-first; honors `?status=` and `?limit=`; **no secrets / no full prompt** in the records (only a truncated input summary) | [P] 2026-06-23 — no-token→403; newest-first; `?status=`/`?limit=` honored; records carry `input_summary` (truncated), no token/secret |
| JOB-9 | `GET /jobs/{id}` (token-gated) | Returns the full record for a known id | [P] 2026-06-23 — known id→200, unknown→404 |
| JOB-10 | **Failed job behavior** | A Claude/job failure surfaces as an error turn with a reason; UI recovers (no wedge); status is terminal `failed` | [P] 2026-06-23 — `_WIRE_STATUS` maps `failed→error` (same path as the live-verified `interrupted` turn); unit tests green |
| JOB-11 | `/poll` idempotence | A terminal job can be polled again without double-delivery (marked delivered, deduped by ts) | [P] 2026-06-23 — polled a complete job twice → `done` + same `ts` both times |
| JOB-12 | Overall client cap | A turn exceeding the cap ends with a clear "timed out — try again," not an infinite spinner | [DEFERRED — logic-verified, non-gating] 2026-06-23 — 10-min `CLIENT_CAP_MS` fires the same `fail(timedOut)` reset path JOB-6 passed on-device; user-facing strings confirmed in code (`index.html:1114-1115`). Not worth a temporary cap change to simulate the same path. No code touch. Beta-safe. |
| JOB-13 | History retention sweep | Terminal jobs older than `job_history_ttl_days` are swept; active jobs are never swept | [P] 2026-06-23 — unit test "old terminal job swept" + "active job never swept" pass; swept on every `/jobs` |

---

## 5. Safe agent behavior (draft-only)

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| SAFE-1 | **Default is `draft_only`** | `/health` shows `agent_safety.mode = draft_only` out of the box | [P] 2026-06-23 (via /health) |
| SAFE-2 | **No direct writes to your files** | A work-mode request that would edit a file does **not** modify it directly; Claude runs in a throwaway workspace with your dirs read-only | [P] 2026-06-23 — file only appeared after panel Apply, never from the turn itself |
| SAFE-3 | **Proposed change created** | Such a request produces a **proposed change** record instead of an edit | [P] 2026-06-23 — `<<PROPOSE>>` → record `ac8df920dc79` |
| SAFE-4 | **Pending approval visible** | The proposed change appears in `GET /proposed-changes` and in the PWA pending panel with target + risk | [P] 2026-06-23 — badge + panel showed target+risk+diff |
| SAFE-5 | **Approve → Apply flow** | Approve, then Apply: the server (the sole writer) applies it, re-running the full permission check first | [P] 2026-06-23 — `Adam_test.MD` status `applied`, `applied_by: server`. **UX:** Approve+Apply collapsed to ONE button (was a confusing two-tap). |
| SAFE-6 | **Deny flow** | Deny: the change is never applied; status terminal `denied` | [P] 2026-06-23 — `Adam_test2.MD` status `denied`, no file written |
| SAFE-7 | **Diff review** | `GET /proposed-changes/{id}/diff` returns a unified diff (or a clear "unavailable" reason for binary/oversize); the PWA shows it colorized | [P] 2026-06-23 — colorized diff shown in panel |
| SAFE-8 | **Stale / conflict detection** | If the target file changed since the proposal, Apply returns **409 conflict** and does **not** overwrite; status → `conflict` | [P] 2026-06-23 — modified file on disk → apply 409 "target file changed since proposed"; file not overwritten |
| SAFE-9 | Conflict recovery | `POST …/refresh-diff` re-baselines and resets to pending (no force-apply); re-approve to proceed | [P] 2026-06-23 — conflict→refresh-diff reset to `pending` (stale=False), re-approve+apply then wrote "proposed v2"; no force-apply |
| SAFE-10 | **Backup before write** | Applying a change that overwrites an existing file creates a timestamped backup in `data/backups/` first; the backup path is reported | [P] 2026-06-23 — overwrite created `data/backups/20260623_111104_Adam_test.MD` |
| SAFE-11 | Blocked / protected target | A change targeting a protected pattern (`.env`, `*.pem`, `*.key`, `settings.json`, …) or outside the write allow-list is **refused** on apply | [P] 2026-06-23 — apply on `settings.json` → 422 "not in an allowed write directory (or blocked/protected)"; file untouched |
| SAFE-12 | Destructive action gate | A destructive action requires approval before it can apply | [P] 2026-06-23 — delete while pending → 409 "not approved (status=pending)"; file intact |
| SAFE-13 | **Audit-log event** | Each decision (create/approve/deny/apply/conflict) writes one JSON line to `data/logs/audit.jsonl`; no secrets in it | [P] 2026-06-23 — `proposed_change_created`/`_denied`/`auto_apply_setting_changed` lines verified, no secrets |
| SAFE-14 | `legacy_direct` is opt-in only | Direct writes are only possible if the operator deliberately sets `agent_safety.mode = legacy_direct`; it is documented as unsafe for product use | [P] 2026-06-23 — default `draft_only`; `/health` shows `unsafe_legacy_mode:false`; legacy path only via deliberate `agent_safety.mode` change (inspection) |
| SAFE-16 | **Auto-apply mode** (new, 2026-06-23, opt-in) | A toggle in the Pending panel lets the user auto-apply proposed changes (incl. overwrites/deletes). Must be **OFF by default**, fail-closed (token-gated), and must STILL enforce every server guardrail on each write: allow-list, protected-path refusal, staleness/conflict check, backup, audit (`proposed_change_auto_applied` + `auto_apply_setting_changed` events). | [P] 2026-06-23 (device-verified) — `/ui-prefs` GET/POST token-gated (no-token→403; default `false`). `run_claude` approves+applies via the same `apply()` (all guardrails intact); per-write + toggle audit events. PWA toggle works. |
| SAFE-17 | **Proposal-outcome context** (new, 2026-06-23, default-on) | The agent is told, at the start of a turn, which proposed changes the user resolved (applied/denied/conflict) since the last turn — so it has continuity and never re-pitches a denied change — without being able to see/operate the panel. A per-marker prevents repeating an outcome. | [P-pending] built 2026-06-23 — `proposed_changes.resolved_since(ts)` + `resolved_at_ts` stamp; `_proposal_outcome_note()` injected into the prompt every turn, marker (`proposals_reported_ts`) advances so nothing repeats; `DRAFT_MODE_NOTE` reconciled. [P] 2026-06-23 (device-verified) — user asked "did we ever approve note 68" and Adam correctly answered "68.MD came back denied, not written" (job record confirms). |
| SAFE-15 | **Agent narration honesty** (new, 2026-06-23) | The LLM must NOT claim it approved/applied/saved a change or that it's "on its way" (it can't see/operate the panel), and must not nag for approval in chat or chase a prior proposal in later turns | [P-pending] fix 2026-06-23 — user hit exactly this: Adam said "approved, on its way to the server" and re-asked to approve a change the user had **denied**. `DRAFT_MODE_NOTE` now tells Claude it cannot approve/apply, must only say it *proposed*, must not ask for in-chat approval, and must not chase prior proposals. Server restarted to load. [P] 2026-06-23 (device-verified — no more false "approved/on its way"). |

---

## 6. Uploads / files

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| UP-1 | **Image upload** + send | Uploads on send; path injected; Claude reads it back | [P] 2026-06-23 |
| UP-2 | **File upload** (e.g. PDF) | Read natively; content referenced in the reply | [P] 2026-06-23 |
| UP-3 | **HEIC/JPEG** (iPhone photo) | HEIC converted to JPEG server-side; read succeeds | [P] 2026-06-23 (iPhone camera HEIC) |
| UP-4 | **Allowed write area** | Uploads land in the configured `data/uploads`; nothing written outside the data tree | [P] 2026-06-23 — valid upload returned path under `data\uploads\`; permission-checked + audited |
| UP-5 | **Oversize at pick time** (> `upload_max_mb`, default 25) | Rejected before transfer ("too large"); no upload sent | [P] 2026-06-23 — iPhone picker: oversize video rejected client-side ("Too large (25 MB max)"), no upload sent; multi-select verified per-file (small uploaded, big rejected) |
| UP-6 | **Server-side oversize** (bypass client) | `/upload` returns 413; surfaced as a real error; turn doesn't hang | [P] 2026-06-23 — ~27MB → 413 "File too large (25 MB max)", server stayed up |
| UP-7 | **Denied / protected path** | An upload that resolves to a protected/blocked path is refused by the permission layer (not silently written) | [P] 2026-06-23 — filename sanitized (`[^A-Za-z0-9._-]`→`_`), can't escape `UPLOAD_DIR`; traversal filename left nothing in project root/parent; `is_path_allowed_for_write` gate + audit |
| UP-8 | Unsupported type | `/upload` rejects with a clear type error; chip removed | [P] 2026-06-23 — `.exe` → 415 "Unsupported type: .exe" |
| UP-9 | Empty (0-byte) file | Rejected with "empty file" | [P] 2026-06-23 — 0-byte → 400 "Empty file" |
| UP-10 | Attachment-only send (no text) | Allowed; turn fires; You bubble notes the attachment | [P] 2026-06-23 |
| UP-11 | Multiple attachments one turn | All upload; all paths ride the turn; chips clear on success | [P] 2026-06-23 |
| UP-12 | Upload retention sweep | Uploads older than `upload_ttl_days` (default 7) are swept | [P] 2026-06-23 — planted an 8-day-old file; next upload swept it |

---

## 7. PWA pending / review panel

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| REV-1 | **Pending count badge** | The header pending button shows a count when proposed changes/approvals are waiting | [P] 2026-06-23 — red count badge appeared |
| REV-2 | **Proposed-change list** | The panel lists pending changes with type, target (and rename destination), risk, and status | [P] 2026-06-23 |
| REV-3 | **Diff display** | Each change shows a colorized unified diff (or the unavailable reason) | [P] 2026-06-23 |
| REV-4 | **Conflict / stale warning** | A changed-since-proposal target shows a conflict/stale warning before Apply | [P] 2026-06-23 — `changeCard` renders conflict/stale warn block for `status=conflict`/stale (inspection; server-side conflict proven SAFE-8/9) |
| REV-5 | **Approve / Deny / Apply buttons** | State-appropriate buttons appear and perform the action; the list re-renders | [P] 2026-06-23 — buttons worked; **Approve & Apply now one button** (collapsed the two-tap). Deny verified. |
| REV-6 | **Refresh behavior** | A Refresh action re-checks status / re-baselines a conflicted change | [P] 2026-06-23 — `/refresh-diff` reset conflict→pending live (REV-6/SAFE-9) |
| REV-7 | Surfaces non-pending states | Conflict and failed changes are visible (not just pending) so they can be recovered | [P] 2026-06-23 — list shows all statuses; `changeCard` renders conflict/failed with recovery buttons (inspection) |
| REV-8 | **Inline error handling** | An apply error (e.g. 409 conflict) shows a clear inline message, not a silent failure or a crash | [P] 2026-06-23 — `act()`→`alertFail()` surfaces the apply error (409 detail); failed card shows `apply_error` (inspection) |

---

## 7b. Push / notifications / last result

> **Mark every row `[N/A]` if push is disabled** (no VAPID keys in `.env`). The app must run
> fine with push off — these tests verify the *optional* path, never a hard dependency.

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| PUSH-1 | Push **disabled** (no VAPID keys) | App runs normally; no push UI errors; `/health` shows push not enabled; rows below = `[N/A]` | [P] 2026-06-23 — ran all session with `push_enabled:false`; no push UI errors |
| PUSH-2 | Notification **permission prompt** | Enabling notifications triggers the browser permission prompt; decline is handled gracefully | [N/A] push disabled (no VAPID keys this config) |
| PUSH-3 | **Push subscription created** | Subscribing registers the device; subscription is **persisted** (survives restart — see PERS rows) | [N/A] push disabled |
| PUSH-4 | **Notification received** | A completed async job (or test push) delivers a notification while the app is backgrounded | [N/A] push disabled |
| PUSH-5 | **Notification tap** | Tapping the notification opens/focuses the PWA to the relevant result | [N/A] push disabled |
| PUSH-6 | **Disabled-notification behavior** | With OS/browser notifications denied, the app degrades quietly (no crash, no repeated prompts); result still retrievable in-app | [N/A] push disabled (degrades quietly — verified app runs fine without push) |
| PUSH-7 | **Last-result persistence** | The last completed result is stored server-side and re-fetchable after reload/restart (`last_result` persists); not memory-only | [P] 2026-06-23 — `data/state/last_result.json` persisted across restarts; powers on-device Replay |
| PUSH-8 | No secrets in push path | VAPID **private** key never reaches the frontend or logs; only the public key is exposed where required | [P] 2026-06-23 — no VAPID private in logs (REL-7); only `/push/key` exposes the public key |

---

## 8. Reliability & recovery

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| REL-1 | **Restart the server** | Comes back up cleanly; `/ping` 200 | [P] 2026-06-23 — restarted cleanly several times this session |
| REL-2 | **Health after restart** | `/health` 200; `state_schema_version` present; config sanity intact | [P] 2026-06-23 — `/health` 200, `state_schema_version:1`, config intact |
| REL-3 | **Completed jobs survive** restart | Finished jobs still in history (`GET /jobs`) | [P] 2026-06-23 — finished jobs retrievable post-restart |
| REL-4 | **Running jobs marked interrupted** | Jobs that were queued/running are marked `interrupted` on boot (audited), not silently completed or auto-rerun | [P] 2026-06-23 — `running` job→`interrupted` on restart; audit `job_interrupted_on_startup` written; not auto-rerun |
| REL-5 | **Logs created** | `data/logs/` has a rotating server log + `audit.jsonl`; rotation works (size-capped) | [P] 2026-06-23 — `voice_server.log` + `audit.jsonl` present and writing (rotation cap not stress-tested) |
| REL-6 | **No crash on bad input** | Empty message, malformed request, junk payload → clean 4xx, server stays up | [P] 2026-06-23 — malformed JSON→422, empty body→422, server stayed up |
| REL-7 | **No secret leakage in logs** | Token, VAPID private key, Twilio token never appear in any log or job record; only a truncated prompt summary is stored | [P] 2026-06-23 — token value: 0 matches in logs; no VAPID/Twilio secrets; audit events clean |
| REL-8 | Rate limits | Hammering `/ask*` / `/speak` returns 429 without wedging the app (speak falls back to browser voice) | [P] 2026-06-23 — 30×200 then 429 on `/upload` (shared slowapi limiter); server stayed healthy. `/ask`,`/ask_async` 30/min; `/speak` 120/min |
| REL-9 | Crash relaunch (if a service/scheduled task is configured) | The configured boot/relaunch mechanism brings the server back; document whatever the customer set up | [DEFERRED — N/A for shipped product, non-gating] 2026-06-23 — no auto-relaunch service/task ships with adam-local by design (local-first, customer-run; relaunch is customer/deployment-configured — Windows Scheduled Task, NSSM, or installer/runtime wrapper). REL-1 already verified manual restart; crash/restart recovery logic proven (server returns cleanly, in-flight jobs → `interrupted` handled correctly). Registering a task just to tick this would be deployment/infra work, not iPhone acceptance. No code touch. Beta-safe. |

---

## 9. Desktop browser sanity

> **Mark `[N/A]` / not-yet-run unless actually exercised this pass.** Desktop is a
> sanity surface, not the primary product target.

| ID | Scenario | Expected | ✅ |
|----|----------|----------|----|
| DESK-1 | Load in **Chrome / Edge** | App shell loads; orb renders; no console errors that break function | [DEFERRED] desktop browser not exercised this pass (same shell verified on mobile); §9 is sanity/non-gating |
| DESK-2 | **Text request** | Type + send works; reply displays | [DEFERRED] server text path verified (SMOKE-5 `/ask_async`→done); desktop-browser render not exercised |
| DESK-3 | **Pending panel** | The proposed-change/review panel opens and renders changes + diffs | [DEFERRED] panel data + diffs verified server-side; desktop-browser render not exercised |
| DESK-4 | Desktop echo path note | On desktop the mic may hear the speakers — confirm the recognizer is paused during TTS, or document the limitation | [DEFERRED] recognizer IS aborted during TTS (`speak()` `recognition.abort()`); desktop runtime not exercised |

---

## 10. Known deferred / optional tests

These are **intentionally** not part of the release gate. Record them as deferred,
not as failures.

> **Glasses rule (binding):** smart-glasses testing is **OPTIONAL before a generic
> local beta**, but **REQUIRED before any public claim** that Adam
> *supports smart glasses* or *glasses-first hands-free* use. Do not advertise
> glasses support until DEF-1 has been run and passed on a real device.

| ID | Item | Status |
|----|------|--------|
| DEF-1 | **Smart-glasses-only pass** (mic at the ears, phone as host) | Optional — **not yet run** on the product line | [DEFERRED] needs glasses hardware |
| DEF-2 | **Desktop browser full pass** (everything in §9 plus audio/voice) | Deferred — sanity only for now | [DEFERRED] see §9 |
| DEF-3 | **~4s silent "Speaking" on background-return** to an unseen reply | Known issue, **deliberately deferred** (fragile audio path); document if it recurs | [DEFERRED] — AUD-16 v3 (`currentTime`-stall force-settle) likely mitigates this; watch for recurrence on device |
| DEF-4 | Inbound-SMS turns tracked as jobs | Out of scope — `/sms` is fire-and-forget by design | [N/A] out of scope by design |
| DEF-5 | Resumable mid-flight jobs after restart | Deferred — interrupted jobs are surfaced, not resumed (future work) | [DEFERRED] by design (interrupted surfaced, not resumed) |
| DEF-6 | Multi-user / shared install | Out of scope — single-user assumptions are intentional | [N/A] out of scope by design |
| DEF-7 | ~~Offline cold launch on iOS standalone PWA~~ | **RESOLVED 2026-06-23** — not an iOS limitation after all. PWA-12 passes once the SW installs via the correct HTTPS URL; the earlier blank page was a stale/un-installed SW from origin confusion. See PWA-12. | [P] |

---

## 11. Regression smoke test (run before EVERY release)

The 9-point pre-flight. If any line fails, the build does not ship. Should take ~10 minutes on
the primary device. Run after the full pass too, as the final gate.

| ID | Check | Expected | ✅ |
|----|-------|----------|----|
| SMOKE-1 | Server starts | Clean boot line in `data/logs/`; no traceback | [P] 2026-06-23 — startup-complete, no traceback |
| SMOKE-2 | Health OK | `GET /health` 200; `version` correct; `agent_safety.mode = draft_only` | [P] 2026-06-23 — 200, v0.6.0, draft_only |
| SMOKE-3 | Mobile opens | PWA shell loads on the primary phone; orb renders | [P] 2026-06-23 (device, earlier this session) |
| SMOKE-4 | Mic works | Activate, speak, transcription paints, turn fires | [P] 2026-06-23 (device, earlier this session) |
| SMOKE-5 | Response returns | `/ask_async` → poll → done → reply painted | [P] 2026-06-23 — ask→poll→done ("Hello to you, sir.") |
| SMOKE-6 | Audio plays | Reply is spoken aloud (TTS or fallback); heard the first time | [P] 2026-06-23 (device, earlier this session) |
| SMOKE-7 | Replay works | Replay re-speaks the last reply | [P] 2026-06-23 (device, earlier this session) |
| SMOKE-8 | Wrong-token 403 | A bad token on a protected route returns 403 (fail-closed) | [P] 2026-06-23 — bad token → 403 |
| SMOKE-9 | No secrets in logs | Scan `data/logs/` + `audit.jsonl`: no token / VAPID private key / Twilio token | [P] 2026-06-23 — 0 token matches; no VAPID/Twilio secrets |

---

## Pass / fail notes

Record every `[F]` here with enough detail to reproduce.

| ID | Device | Steps to reproduce | Log line / evidence | Severity |
|----|--------|--------------------|--------------------|----------|
|    |        |                    |                    |          |
|    |        |                    |                    |          |

---

## Release gate summary

A device/build is **consumer-release-ready** only when:

- **All sections 1–8 are `[P]`** on the primary device (D1) and the relevant
  audio devices (D3/D4) for the audio rows.
- **Section 5 (safe agent behavior) has zero `[F]`** — this is the trust core of
  the product. Default `draft_only`, no direct file writes, conflict + backup +
  audit all verified.
- **No secret leakage** (REL-7) and **fail-closed auth** (AUTH-4/5, CFG-1) are
  confirmed — these are hard blockers.
- Section 9 (desktop) and §10 items may remain deferred without blocking release,
  **as long as they are recorded as deferred**, not skipped silently.

### Required before consumer release
PRE-1…7 · §1 all · §2 PWA basics · §3 audio core (AUD-1–10, 14–15) · §4 jobs +
polling + restart recovery (JOB-1–13) · §5 **all** safe-agent rows · §6 uploads ·
§7 review panel · §8 reliability (esp. REL-4, REL-7) .

### Optional / deferred (not gating)
§9 desktop full pass · §10 DEF-1…6 · AUD-11/12 hold-to-restart & transport polish
(test if shipping that build) · push rows where push is disabled (`[N/A]`).

---

## Tiered release gates

Three staged bars. A build advances only when its tier is fully met. Earlier-tier boxes carry
forward (Beta assumes Alpha; Ship assumes Beta).

### Alpha ready (works on the bench)
- [ ] Installs locally from a clean folder (`.env` + `settings.json` from templates)
- [ ] Server starts cleanly
- [ ] Mobile (primary phone) connects and loads the PWA
- [ ] Voice **input** works (mic → transcription → turn)
- [ ] Voice **output** works (reply spoken; fallback if TTS down)
- [ ] Replay works
- [ ] Wrong-token test passes (403, fail-closed)
- [ ] No secrets in logs
- [ ] No crash on restart

### Beta ready (works across the real device matrix)
- [ ] **iPhone + Android** both tested (D1 + D7)
- [ ] Desktop browsers tested (§9, Chrome/Edge at least)
- [ ] **Bluetooth** tested (earbuds + car; D3/D4)
- [ ] Persistence tested (jobs, last result, push sub, config survive restart)
- [ ] Job history tested (`GET /jobs` token-gated, no secrets, restart-survival)
- [ ] Crash recovery tested (running jobs → `interrupted`; PWA recovers, no hang)
- [ ] Checklist **mostly green** (no open high-severity `[F]`)

### Ship ready (a stranger can install and trust it)
- [ ] Clean install **documented** (README quickstart reproduces a working server)
- [ ] Security defaults safe (draft-only agent mode, fail-closed auth, write allow-list)
- [ ] **No high-severity open bugs**
- [ ] Logs safe (no secrets; rotation works)
- [ ] Restore / restart tested
- [ ] Mobile experience reliable (audio + voice + replay solid on the primary device)
- [ ] Audio-route behavior understood (phone ↔ BT ↔ car switching; silent-mode)
- [ ] Known deferred items documented (§10)

### Sign-off

- Device: ______ · Build/version tested: ______
- Sections failed: ______ · Open log lines: ______
- Tier reached: ☐ Alpha ☐ Beta ☐ Ship
- Verdict: ☐ consumer-release-ready ☐ blockers remain
