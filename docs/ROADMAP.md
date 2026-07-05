# Adam — Master Plan 2 (to v1.0 and "real product")

**Date:** 2026-07-05 · **Baseline:** v0.9.37 shipped (latest in DCom17/adam-releases)
**Internal doc — deny-globbed by make_release.py, never ships.**

> **STATUS UPDATE 2026-07-06 (overnight autonomous run):** **P0 — ALL DONE**,
> **P2a/P2b/P2c/P2d — ALL DONE**, P3 items 4 (privacy — PRIVACY.md ships; EULA
> draft in dist/), 7 (no crash reporting; diagnostics completed) and 8 (consumer
> stories + ROTATE-TOKEN.cmd) — DONE. All committed as **v0.9.38** (f1c805c,
> tagged; ZIP built + boot-verified, 217 files, leak-swept clean; full suite
> green; release tests 117→129; updater tests 24→41). **NOT yet pushed or
> published — run `dist\finish-v0.9.38.ps1`.** Owner materials ready:
> `dist\tester-invite-draft.md`, `dist\phone-acceptance-runbook.md`,
> `dist\EULA-draft-v1.0.md`. The live 8000 server still runs 0.9.37 — restart it
> (close the Adam window, reopen from the desktop icon). Remaining v1.0 work =
> P1 (signing enrollment, name search, testers, phone acceptance — all
> owner-gated) + P3 items 1–3, 6 (installer, winget, icon art, site).
>
> **P3-3 (icon art) DONE 2026-07-05:** new Adam "A" mark (cyan gradient +
> glow on navy — brand-continuous with the old orb). web/icon.png (512²,
> full-bleed), NEW web/icon-maskable.png (safe-zone padded, own manifest
> entry + /icon-maskable.png route + SW cache, cache bumped to
> adam-shell-v2), web/icon.ico regenerated multi-size (16–256, rounded).
> Owner's desktop JARVIS.lnk replaced by Adam.lnk via add-app-shortcut.ps1.
> Phones: Android launcher updates on SW refresh; iOS home-screen icon needs
> remove + re-add. Remaining P3: items 1, 2, 6 (installer, winget, site).
>
> **P3-1 (installer) BUILT 2026-07-05, signing pending:** scripts/
> adam-installer.iss + scripts/build-installer.ps1 (payload = the guarded
> ZIP, all release guards fail-closed first). Per-user, no-UAC install to
> %LOCALAPPDATA%\Programs\Adam; /VERYSILENT works (winget-ready); uninstall
> keeps brain/ + data/ + .env. Acceptance run on v0.9.38: silent install →
> `import server` boots → reinstall preserves edited brain file → silent
> uninstall removes program, keeps user data. Still due: SignTool= once the
> Azure cert lands; ship the installer alongside the ZIP from v0.9.39 on.
> Remaining P3: 2 (winget, after signing) + 6 (site).
>
> **P3-6 (site) BUILT 2026-07-05, publish pending owner go-ahead:** site/
> in the adam repo — index.html (download w/ GitHub-API direct-link
> upgrade, quick start, no-middleman security story, SmartScreen note) +
> privacy.html (mirrors shipped PRIVACY.md) + icon. D21-guardrail branding
> ("Adam Local", powered-by-Claude non-affiliation footer). Verified
> excluded from the release ZIP (allow-list). To publish: push a gh-pages
> branch with site/ contents at root (auto-publishes to
> dcom17.github.io/adam) — owner must approve first. Also fix the brand
> header note: index.html home screen said "J A R V I S" (letter-spaced,
> grep-proof) until 9c9faea. Remaining P3: winget only (after signing).

Sources: four-way deep audit run 2026-07-05 — (A) cold-user product/UX audit, (B)
production-readiness code audit, (C) live verification (49/49 suite, 117/117 release
tests, doctor 0 FAIL, shipped ZIP boots clean + auth matrix correct), (D) launch-
requirements research (signing / distribution / legal / category norms, with sources).
Scope decisions D19 (Tailscale stays manual, permanent) and D20 (visible console
window accepted for v1.0) in DECISION_LOG.md still stand and shaped this plan.

**Verification status of findings:** the wizard ordering bug (P0-1) was re-verified by
hand (wizard.ps1: Set-AiPlan at :229/:238 runs in STEP 2, pip install at :272 in STEP
3); the tailnet-hostname leak (P0-3) was independently confirmed by two agents against
the shipped ZIP + live `tailscale status`. Other findings are single-audit; re-verify
line numbers before large refactors.

---

## The picture in one paragraph

The core product is in good shape: tests all green, the shipped ZIP boots and enforces
auth, the setup funnel is genuinely consumer-grade writing, branding is clean, and the
safety architecture (server-as-sole-writer, backups, audit, fail-closed release
builder) is strong. What stands between this and a *real product* clusters in four
places: (1) a handful of cold-user blockers that must land **before tester invites**,
(2) the **updater's crash/rollback story** — the highest-stakes consumer code path,
currently able to brick an install and unable to actually roll back a real update,
(3) **packaging** — a raw ZIP reads as developer tool; the 2026 category norm is a
signed installer + winget + a small site with privacy/security pages, and (4) two
**external-lead-time** items (Azure Artifact Signing enrollment, a trademark knockout
search on the name "Adam") that should start immediately because they can't be
expedited.

---

## P0 — Before cold-tester invites go out (ship as v0.9.38)

These break or embarrass the product on exactly the machines the beta targets.

1. **Wizard loses the AI-plan choice / API key on a fresh machine (BLOCKER, verified).**
   `Set-AiPlan` (wizard.ps1:183–259, called :229/:238 in STEP 2) runs
   `import integration_config` → `import config` → `from dotenv import load_dotenv`
   *before* STEP 3 installs requirements (:261–296). On a machine where the wizard just
   installed Python, both calls throw `No module named 'dotenv'`; a pasted API key is
   silently dropped and Adam demands the Claude sign-in the user chose to avoid.
   Fix: move dependency install before the plan step, or stash `$door`/`$apiKey` and
   apply after STEP 3.
2. **D20 unreachable-server message (BLOCKER, one string).** The PWA says "Connection
   error — session reset." (web/index.html:2001) when the server is unreachable — it
   must name the likely cause per D20: "Can't reach Adam. The Adam window on your PC
   may be closed — reopen it from the desktop icon (on your phone, also check
   Tailscale on both devices)." Same for console.html:3478.
3. **Scrub the real tailnet hostname (confirmed leak).** `chainforge.tail4a86a8.ts.net`
   ships as fixture data in test_connect_phone.py (:50–61,130), test_phone_link.py
   (:35–75), test_console.py (:454). Replace with a fake (`examplebox.tail0000.ts.net`)
   and **extend the release content guard beyond brain/** so identifier classes are
   caught tree-wide.
4. **START.cmd wrong-order fallback speaks developer.** start-adam.ps1:74–81 tells a
   consumer to run `python scripts\setup.py`; say "double-click SETUP in this folder"
   instead (also fix the stale header comment :10).
5. **README refresh:** version says 0.9.35 twice (:12, :585); `/health` example shows
   the pre-gating full body (:577–593); "What's next" still promises tray/auto-start,
   contradicting D20 (:16–17, :699).
6. **Small polish sweep (cheap, same commit):** launcher "mode )" glitch
   (start-adam.ps1:177–186 polls /health unauthenticated); broken anchor
   CONNECT_YOUR_PHONE.md:148; stale `scripts/voice_server` bullet ADVANCED_REMOTE.md:39;
   "a Adam" grammar (START_HERE.txt:85, wizard.ps1:359); favicon — add
   `<link rel="icon">` + a `/favicon.ico` route (kills the 404 noise in the visible
   console window); surface the poll-404 diagnosis ("Adam restarted mid-task — ask
   again") instead of the generic connection error.

**Then:** cut v0.9.38, publish, send tester invites.

## P1 — Start now (external lead time, runs in parallel with everything)

1. **Azure Artifact Signing enrollment.** Reopened to US individuals Jan 2026;
   $9.99/mo (5,000 sigs, Basic), needs a paid Azure sub; identity validation is via
   Entra Verified ID in the Authenticator app (gov ID + face check), typically days,
   **cannot be expedited**, three attempts. Cert subject = legal name. EV is pointless
   now (Microsoft: EV no longer bypasses SmartScreen; reputation attaches to the cert
   and carries across releases). Fallback if validation fails: SSL.com IV ($129/yr +
   $379 token). Certum "Open Source" doesn't qualify (proprietary license).
2. **Name decision: USPTO knockout search on "Adam".** The space is crowded (adam.ai —
   funded meeting-AI SaaS with active trademark posture; Adam by Tyms; adam.new CAD
   copilot). Search tmsearch.uspto.gov; if contested, rename NOW while the user base is
   ~1 (the Claudia→opcode precedent shows it's painless pre-1.0), or de-risk with a
   distinctive compound + domain. **Go/no-go before printing the name on 1.0.**
3. **Recruit 2–3 cold testers** (invites go out the moment P0/v0.9.38 ships).
4. **Phone-half acceptance pass** on the shipped ZIP (fresh extract, Tailscale on a
   spare port, checklist §"v0.9 mobile access" + on-device listen to the real Adam
   voice — the one 0.9.37 piece never heard on-device).

## P2 — Reliability hardening (the updater above all)

The updater is the one consumer code path that can destroy an install. Current state
(audit B): download is genuinely fail-closed, but apply is not.

**P2a — Updater (treat as one work package, with tests for each brick scenario):**
1. Atomic per-file writes — `*.tmp` + `os.replace()` in update_engine.py:175–181 and
   merge.py:236. Today a crash mid-copy leaves a truncated server.py that the next run
   classifies as a *user customization* and preserves — permanent brick, no self-heal.
2. Real rollback — per-run backup folder (`data/backups/update-<ver>/<relpath>`)
   exempt from pruning. Today prune_backups (keep 50, permissions.py:286,293) eats the
   update's own backups mid-run (~210-file releases), and basename-only backup names
   already collide — "fully undoable" (apply_update.py:57) is currently false.
3. Syntax-check diff3-merged `.py` (`compile()`; fall back to conflict-hold) —
   merge.py:225–238 can write a syntactically broken merge and then baptize it as the
   user's own file forever.
4. Removed-file handling — ship `removed_files.txt`, rename matches to `.removed`
   (update_engine.py:150–152). The 0.9.36 routers/ restructure shows the shadowing
   hazard.
5. Apply lock file + UPDATE.cmd self-overwrite fix (copy to %TEMP% and run from
   there) + free-disk pre-check + post-apply `import server` verify.

**P2b — Release pipeline (close the 0.9.35 recurrence paths):**
1. **Import guard regex → AST (latent BLOCKER).** make_release.py:211–215 misses
   parenthesized imports (`from models import (` — used in server.py:70) and indented
   lazy imports; empirically, dropping models.py / tts_supervisor.py / phone_link.py
   still builds green. Replace with `ast.parse()` walk + add those three drop-cases to
   test_release.py.
2. **Boot-the-ZIP test** — extract to temp, `python -c "import server"` with repo off
   sys.path. Would have caught 0.9.35 outright; subsumes most static gaps.
3. Publish gating — publish-release.ps1 must refuse on dirty tree / failing
   test_release.py. Missing allow-list entries must **raise**, not silently skip
   (make_release.py:152–176). Build-time check: CHANGELOG has `## {APP_VERSION}`.

**P2c — Runtime (top items from audit B):**
1. Claude result `is_error`/`subtype`/usage-limit handling (server.py:1449–1457) — a
   Pro tester hitting their 5-hour cap mid-session currently gets silence or a spoken
   raw `"Claude AI usage limit reached|<epoch>"` string. Friendly sentinel like the
   existing auth-failure pattern (server.py:424–440).
2. Windows 32K argv limit — long pasted text breaks the spawn cryptically
   (server.py:1317–1375; no input cap in models.py). Pipe message via stdin or
   friendly 413.
3. Fire-and-forget `create_task` (routers/chat.py:149, server.py:985,994) — GC can
   wedge a job in `running` forever. Keep refs + done-callback (pattern already in
   voice_push.py:24–26).
4. Blocking connector HTTP on the event loop (routers/integrations.py:100,126,179,196)
   — a slow Apps Script bridge freezes every route up to 20 s. `asyncio.to_thread`
   (pattern already in SMS routes).
5. proposed_changes.json / approvals.json never shed terminal records (full content +
   200K diffs), parsed on the event loop **every turn** (proposed_changes.py:212–217,
   server.py:247). Drop terminal records >30 days in `_expire()`.
6. Job terminal-state write failure wedges `running` (server.py:1594,1620–1622) —
   wrap in own try/except. Code-mode stream overflow orphans a bypassPermissions
   claude.exe (server.py:1402–1410) — `except Exception: _kill_proc_tree`.

**P2d — Diagnosability (what cold-tester support actually needs):**
1. TTS sidecar logging — tts_supervisor.py:86–95 DEVNULLs everything; a crash-looping
   voice engine leaves zero trace. Redirect stderr to append-mode `data/logs/tts.log`.
2. Doctor gaps: (a) Claude installed-but-not-signed-in passes (onboarding.py:274–287
   only runs `--version`; docs claim otherwise — START_HERE.txt:145–148); (b) never
   pings TTS :8001; (c) never exercises the phone path (reuse connect-phone.py logic).
   These are the three likeliest cold-tester reports, all invisible today.
3. `/diagnostics` should tail LOG_FILE through `_redact()` (ring-only forgets
   everything pre-restart, routers/system.py:132) + add ANTHROPIC_API_KEY to redaction
   (:81–89).
4. TTS venv pinning — scripts/tts_server/requirements.txt has 6 of 7 deps unpinned;
   INSTALL-VOICE builds it cold on user machines; pin from the working venv's freeze.
   Add explicit `cryptography` + `pydantic` pins to main requirements. CI: add a 3.12
   job (wizard installs 3.12; CI tests only 3.14).

## P3 — Packaging: the "real product" layer

Category norms (Jan, Msty, AnythingLLM, Happy — audit D): signed installer + winget +
small site + disclosed-or-absent telemetry + public changelog + Discord/Issues support.
Adam already beats the field on telemetry (zero) and matches on auto-update.

1. **Signed Inno Setup installer** (silent `/VERYSILENT` support) replacing raw ZIP +
   SmartScreen-unblock docs. Gives Start Menu entries, Add/Remove Programs presence,
   an uninstaller (winget requires silent uninstall), and moves the download-and-run
   logic into one signed executable surface (fixes the AV-heuristic exposure of
   .cmd/.ps1 bootstrap too). GitHub Releases stays the artifact host + updater feed
   (2 GiB/file, no bandwidth caps — fine forever).
2. **winget submission** (`ZacharyCampos.Adam` or post-rename equivalent) once the
   signed installer exists. `winget install adam` is real credibility, free.
3. **Icon art + favicon** — known-size job: exactly 2 files (web/icon.png 512² +
   web/icon.ico) referenced from manifest.json:11–12, index.html:37, sw.js:18/59/85–86,
   routers/system.py:205–210, add-app-shortcut.ps1:16, test_release.py:90. Add a padded
   maskable PNG variant (current maskable = same file; Android crops). Note SW cache +
   shortcut icon cache mean users need SW update + shortcut re-creation to see it.
4. **Legal pages:** one-page privacy policy (nothing collected; enumerate the exact
   network calls: Anthropic, GitHub releases update check, optional Tailscale) + short
   proprietary EULA (as-is/liability caps — matters more with no LLC; "your Anthropic
   account, your responsibility"; note Anthropic may change subscription terms and
   API-key mode is the supported alternative). Finalize LICENSE (current beta terms
   promise this before v1.0) and link it from README/START_HERE.
5. **Anthropic-terms posture (from research, keep durable):** orchestrating the
   user's OWN local Claude Code is the supported pattern (their help center explicitly
   contemplates third-party apps authenticating via the user's plan); embedding
   claude.ai login in the product is what needs approval — don't ever build that.
   Keep API-key mode first-class (hedge against subscription-terms flux — Anthropic
   paused a June 2026 change but reserved the right). Phone access stays single-user
   (account sharing is a user-side violation). Branding: never present "Claude Code"
   as ours; "powered by Claude" framing.
6. **Small product site** (GitHub Pages is fine): download, quick-start, changelog,
   privacy, and a security explainer — "what leaves your machine and what doesn't;
   phone access rides YOUR tailnet, nothing transits my servers" (stronger than
   Happy's relay model — say so).
7. **Support channels:** GitHub Issues (templates exist) + contact email + optionally
   Discord (de-facto solo-dev channel in this niche). **No crash reporting, ever** —
   the in-app redacted /diagnostics copy button is the answer; P2d makes it complete.
8. **Consumer stories in docs (audit A):** Uninstall (delete folder + shortcuts +
   `tailscale serve off`; what stays; what dies with data/), "Where is my stuff"
   (Documents\Adam Files, data/state, data/uploads, data/backups; reconcile the two
   vault "defaults" — settings.example.json:14 says brain/, wizard says Documents\Adam
   Files), token rotation (ROTATE-TOKEN.cmd or console action — today a leaked token
   has no recovery short of hand-editing .env), and a factory-reset recipe.

## P4 — The v1.0 gate

Cut v1.0 when ALL of:
- [ ] P0 shipped (v0.9.38) and phone-half acceptance PASS on a shipped artifact
- [ ] 2–3 cold testers completed ZIP→talking-phone without maintainer intervention;
      their punch list resolved or explicitly deferred with a DECISION_LOG entry
- [ ] D20 falsification check: did testers hit the closed-window trap? If yes → the
      cheap fixes (P0-2 message + close-confirm), re-test; tray only if that fails
- [ ] Signing live (Artifact Signing cert issuing; installer + release assets signed)
- [ ] Name go/no-go done (and rename executed if no-go)
- [ ] P2a updater package + P2b release-pipeline items done (the brick-prevention set)
- [ ] P3 items 1, 3, 4, 8 done (installer, icon, legal, consumer stories); site (6)
      and winget (2) may trail v1.0 by days but should be in the announcement
- [ ] LICENSE finalized per its own promise

## Post-v1.0 / deferred (decided — do not re-litigate)

- Tray / auto-start — only if D20 falsified AND cheap fixes fail (DECISION_LOG D20)
- Automated Tailscale wizard — never (D19)
- Microsoft Store / MSIX — revisit post-1.0 (free individual accounts + free MS
  signing + no SmartScreen are tempting, but MSIX packaging of a self-updating
  Python-source app is a project; in-app updater must be disabled in a Store build)
- LLC — before monetization, not before a free 1.0
- Per-device tokens / device pairing, multi-user, hosted SaaS — post-1.0 ideas
- Crash reporting / telemetry — never; log-export instead
- Kokoro perf (synth ~1.4–1.6× slower than realtime on this CPU; inter-sentence gaps
  on long replies) — perf item, post-1.0
- Cost-meter gaps (killed turns uncounted; budget check pre-spawn only) — document,
  fix post-1.0
- audit.jsonl rotation; doctor-from-the-app button; `doctor.py --bundle` support zip
- ui_prefs read-modify-write race (single-user, low impact)

## Standing constraints (unchanged)

- draft_only default; server is sole writer; never regress the safety architecture
- Release ZIP must never carry secrets/personal data — deny-guard + release_guard.local
  (extend content scan tree-wide per P0-3)
- localStorage keys stay `jarvis_*` on purpose (migration cost > benefit)
- Supported phone path is Tailscale Serve HTTPS; plain LAN http is never the voice path

---

# Master Plan 3 — second four-way deep audit, 2026-07-05 (afternoon)

Sources: four parallel agents — (A2) cold-user UX audit, (B2) production-readiness
code audit (incl. fresh eyes on the new installer), (C2) live verification, (D2)
launch-requirements research. Run AFTER icon/installer/site landed.

**Verified state (C2, all PASS):** suite 49/49, release tests 131/131, doctor 24
checks 0 FAIL (2 WARNs are owner-config, not product), live /health + all icon
routes + "A D A M" brand header correct, fresh ZIP builds green (218 files, no
leaks), v0.9.38 published with correct asset, /update/check correct. (B2) spot-
verified 13/14 claimed P2 fixes with line evidence — the P2 work was real.

## MP3-P0 — before tester invites (tiny; ship in v0.9.39)

1. **Inside-the-ZIP guard (BLOCKER, 3 lines × 3 files).** SETUP.cmd / START.cmd /
   UPDATE.cmd: if `%~dp0scripts\wizard.ps1` (etc.) missing → "You're running this
   from inside the ZIP — right-click the ZIP → Extract All first" + pause. The #1
   cold-user move currently yields a raw red PowerShell error.
2. **Version bump discipline:** repo is ahead of the published v0.9.38 asset —
   any republish would silently differ. Next publish = v0.9.39 (icon + brand fix +
   installer + these fixes).

## MP3-P1 — the installer work package (BLOCKER-1.0; installer is winget's
default upgrade path, so these become mainline before v1.0)

1. **Upgrade-over-existing poisons the 3-way updater (B2-A).** setup.exe over an
   existing install blindly overwrites program files (user customizations lost, no
   backup) AND leaves data/baseline stale → next in-app update mass-misclassifies.
   Fix: [Code] section — detect existing install; back up; refresh baseline
   post-install (or route upgrades to the in-app updater).
2. **Collision with existing ZIP installs (B2-B).** Desktop shortcut overwrite +
   second empty install at %LOCALAPPDATA% = "my data vanished" + port-8000 fight.
   Fix: InitializeSetup pings /ping + checks known paths → warn/offer migration.
3. **Running-instance guard (B2-F):** AppMutex or ping check ("close Adam first")
   on install AND uninstall.
4. **build-installer.ps1 defects:** `$env:ProgramFiles(x86)` expands wrong — the
   default Inno install path never matches (B2-D; fix `${env:ProgramFiles(x86)}`);
   `-Zip` reuse stamps current config.py version onto possibly-stale ZIP contents
   (B2-E; assert staged config.py version == stamp).
5. **Uninstall leftovers (B2-J):** INSTALL-VOICE's ~1GB venv+model under {app} and
   updater-added files survive uninstall silently. Fix: UninstallDelete/prompt for
   the voice package + `*.removed`/`*.jvltmp` stragglers.

## MP3-P2 — product fixes before 1.0

1. **Post-apply verify + auto-restore (B2-C — the one unbuilt P2a item).** After
   apply: run the existing self_edit_guard subprocess probe; on failure restore
   from result.backup_root BEFORE snapshot_baseline. Backups currently exist but
   nothing ever restores them.
2. **Out-of-credit sentinel for API-key door (A2-2):** exhausted prepaid credit
   speaks raw Anthropic error; add sentinel mirroring _usage_limit_message. Also
   mirror the usage-limit sentinel in the code-mode stream path (B2-I,
   server.py:1254-1259).
3. **Baseline snapshot crash-safety (B2-G):** build baseline.tmp then swap.
4. **AI-disclosure line (D2 flag 4, satisfies FTC/Utah/Maine cheaply):** one line
   in onboarding + privacy page: "Adam is an AI assistant powered by Claude; your
   messages are processed by Anthropic under your own account." Keep utilitarian
   framing (never "companion") — aligns with D21 guardrail 1.
5. **Site download link (D2 flag 3):** replace api.github.com fetch with the
   rate-limit-free `releases/latest/download/<asset>` redirect. Needs version-free
   asset name (e.g. adam-local-setup.exe) or bake version at deploy.
6. **LICENSE finalization from EULA draft (standing P4 gate).**
7. **Polish sweep (A2-4..11, one commit):** README /health examples → "0.9.x"
   placeholder (drifts every release); wizard hardcoded model IDs (fall back to CLI
   default / doctor check); START_HERE "THE REAL ADAM VOICE" heading; token-missing
   fallback says "Run SETUP again"; CONNECT_YOUR_PHONE drops start-dev.ps1 refs;
   tester invite: how-to-open-terminal + Tailscale-account notes; README:686 drop
   "yet" (D20 contradiction); wizard shortcut-failure prints fallback instead of
   silent catch.

## MP3-P3 — distribution mechanics (from D2 research)

1. **winget:** signing NOT required but sign first anyway (unsigned = manual-review
   purgatory); silent install already satisfied by Inno defaults; InstallerUrl must
   be the TAGGED asset URL (never /latest); include PrivacyUrl (policy 1.5.1);
   wizard's runtime downloads are policy-compliant if disclosed in Description.
   First submission = human queue, days to 1-2 weeks.
2. **Azure Artifact Signing (current name; individual = self-employed OK, 3-yr
   history requirement dropped):** $9.99/mo Basic; validation 1-20 business days,
   non-expeditable, 7-day email-verify window, billing account type must be
   "Individual". Inno integration: SignTool= in [Setup] signs installer AND
   uninstaller; needs .NET 8 + Azure.CodeSigning.Dlib + timestamp.acs.microsoft.com.
3. **Anthropic posture (D2 flag 5, watch-item):** June 15 2026 metering of
   headless/`claude -p` was PAUSED, not cancelled — subscription orchestration
   currently fine, expect a reworked proposal with notice. Keep API-key mode
   first-class + documented as the guaranteed path; never touch OAuth tokens.
4. **Legal:** EULA + privacy page sufficient for free beta; LLC before wide
   distribution recommended, required before monetizing (D21 guardrail 4 already
   covers attorney).

> **STATUS 2026-07-05 (same-day autonomous run):** **MP3-P0 DONE, MP3-P1 DONE,
> MP3-P2 items 1-5 + 7 DONE — all committed and pushed; v0.9.39 BUILT + TAGGED
> (ZIP 218 files + adam-setup-v0.9.39.exe; suite 54 / release 131 / updater 50
> green). NOT yet published — owner runs `dist\finish-v0.9.39.ps1` (pushes the
> tag, opens the release page; attach BOTH the ZIP and the installer), then
> restarts the live server.** Installer acceptance matrix passed live:
> guard-refusal with real server running → fresh silent install → upgrade
> (pre-backup + baseline reset + brain survives) → uninstall (voice package
> gone, user data kept). Found+fixed live: a [Code] MsgBox ignores
> /SUPPRESSMSGBOXES and hung silent installs (now gated on WizardSilent).
> Remaining MP3-P2: item 6 only (LICENSE finalization — owner reviews the EULA
> draft). Remaining overall: owner-gated items (signing, phone acceptance,
> testers, site publish go-ahead) + winget after signing.

## MP3 — the updated v1.0 gate (supersedes P4 list where they overlap)

- [ ] MP3-P0 shipped as v0.9.39 → tester invites out
- [ ] Phone-half acceptance PASS (owner, runbook in dist/)
- [ ] 2-3 cold testers complete ZIP→talking-phone unaided; punch list resolved
- [ ] Signing live → installer + assets signed → MP3-P1 done → winget submitted
- [ ] MP3-P2 items 1-6 done; LICENSE final
- [ ] Site published (owner go-ahead) with corrected download link
- Deferred to post-1.0 unless testers hit them: update-channel SHA256 (B2-H),
  per-device tokens, B2-J beyond the voice package.
