# Changelog

All notable changes are documented here. Entries before 0.9.35 use the product's
old name, Jarvis Voice Local — they are a historical record and were left as written.

## 0.9.39 - Adam looks like Adam (and updates can undo themselves)

The release where the last traces of the old name disappear and the update
system finishes its safety story. Also the first release with a Windows
installer alongside the ZIP.

- **New icon.** Adam's mark is now a bold cyan "A" (the old glowing-orb DNA,
  unmistakably Adam) — app window, browser tab, desktop shortcut, phone home
  screen. Android launchers get a properly padded icon so nothing is cropped.
  (On iPhone, remove and re-add the home-screen shortcut to see it.)
- **The home screen says ADAM.** The header above the orb still read JARVIS —
  the very last visible piece of the old name. Gone.
- **Updates now verify themselves and undo on failure.** After applying an
  update, Adam boot-checks the updated code; if anything is wrong it puts
  every file back exactly as it was and tells you — you stay on your current
  version. (Backups existed before; now they actually restore themselves.)
- **Friendlier "out of AI" messages.** Pay-as-you-go credit running out now
  says so plainly (with where to top up) instead of quoting a raw billing
  error — in every mode, including code chat. Same for plan usage limits.
- **You can't get trapped inside the ZIP anymore.** Double-clicking SETUP,
  START, or UPDATE from Explorer's ZIP preview now explains how to Extract
  All first, instead of dying with a red PowerShell error.
- **Windows installer (beta).** adam-setup-v0.9.39.exe installs Adam like a
  normal app: Start Menu + desktop shortcuts, Add/Remove Programs entry, and
  a clean uninstall that keeps your notes, token, and data. It refuses to run
  while Adam is open, backs up program files when installing over an existing
  copy, and never touches your brain\ folder. (Unsigned for now — Windows
  SmartScreen will warn until code signing lands; the ZIP flow is unchanged.)
- **Clear AI disclosure.** Setup, the docs, and the privacy page now say
  plainly: Adam is an AI assistant powered by Claude, processed under your
  own account.
- Polish: version-proof docs examples, "the real Adam voice" heading, no more
  developer-speak in launcher fallbacks, phone-guide cleanups, and the wizard
  no longer promises a desktop icon if Windows refused to make one.

## 0.9.38 - The pre-tester hardening release

The output of a full four-way product audit (cold-user UX, code production-readiness,
live verification, launch research). Everything here is aimed at one thing: a
stranger's machine.

**Fixed — cold-install blockers:**
- **Setup no longer loses your AI-plan choice on a brand-new PC.** The wizard
  recorded your choice (and a pasted API key) before installing the components the
  recorder needs — on a machine where the wizard had just installed Python, the
  choice (and key) were silently dropped. The step now runs after the install, and
  a failure tells you exactly where to re-enter the key (Settings → AI plan).
- **"Can't reach Adam" now says why.** When the app can't reach the server, it
  names the most likely cause — the black Adam window on the PC was closed — and
  how to fix it (reopen from the desktop icon), on both the phone app and the
  console sign-in. A server restart mid-question likewise now says "Adam restarted
  mid-task — ask again" instead of a generic connection error.
- **Hitting your Claude plan's usage limit is now said in plain words** (with the
  reset time when known) instead of silence or a raw error string read aloud.

**Fixed — reliability under the hood:**
- **The updater can no longer brick an install.** Every file write is atomic (a
  crash mid-update leaves the old file, never a truncated one); each update run
  keeps its own complete backup folder (undo is real now, even for 200-file
  updates); a broken auto-merge of Python code is held for review instead of
  written; files a new version removed are retired (renamed `*.removed`), never
  left to shadow their replacements; two updates can't run at once; and an update
  won't start on a disk too full to finish it.
- **Release pipeline:** the import guard now parses code properly (the old
  pattern-match missed three modules), every built ZIP is boot-tested in
  isolation before it can ship, a missing allow-list file fails the build instead
  of silently vanishing from the ZIP, publishing refuses a dirty tree or failing
  tests, and a tree-wide guard keeps ANY owner-identifying text out of releases.
- **Runtime:** very long pasted messages no longer crash the turn (Windows
  command-line limit); background jobs can't be lost to garbage collection;
  slow add-on bridges no longer freeze every request for up to 20 s; the
  proposed-changes store sheds old resolved records instead of growing forever;
  a timed-out turn kills the whole helper-process tree.

**New:**
- **`ROTATE-TOKEN.cmd`** — one double-click generates a fresh access token if
  yours may have been exposed (then re-pair devices via the Connect-phone QR).
- **`docs/PRIVACY.md`** — the complete, honest list of network calls Adam makes
  (short version: it collects nothing).
- **Uninstall / your-data / factory-reset documentation** in SUPPORT.md.
- **The doctor now checks the two things testers actually hit:** whether Claude
  is signed in (not just installed), and whether the Adam voice service is
  installed and answering. The voice service finally has its own log
  (`data\logs\tts.log`) — a crashing voice engine used to leave no trace at all.
- **Favicon** — Adam pages now have a proper tab icon, and the server window no
  longer prints a 404 line on every page load.
- Diagnostics bundle now includes the persistent log tail (survives restarts)
  and redacts the API key; the voice-service dependency set is fully pinned so
  INSTALL-VOICE keeps working months from now; CI now also tests Python 3.12
  (what setup actually installs on user machines).

## 0.9.37 - The real Adam voice heals itself (and yes, it works on your phone)

- **The server now keeps the real Adam voice alive.** If the voice service
  (Kokoro) isn't running — it crashed, or Adam was started some way other than
  the launcher — the server notices and restarts it by itself: checked at
  startup and again whenever a spoken reply fails. Replies no longer silently
  degrade to the robotic browser voice until someone restarts things by hand.
  (The first reply or two after a crash may still use the fallback voice while
  the voice model reloads.) Self-heal only manages a locally configured voice
  service, refuses partial installs, and is rate-limited so a broken install
  can never spawn-loop.
- **Docs corrected: the real Adam voice already works on the phone.** The
  phone guide claimed spoken replies on the phone were always the robotic
  built-in voice. Wrong — the voice is generated on the PC and delivered over
  the same private HTTPS connection, so with `INSTALL-VOICE.cmd` on the PC the
  phone speaks with the same natural Adam voice as the desktop. The guide now
  says so, and gained a "replies sound robotic" troubleshooting entry.
- New `tts_supervisor.py` module (shipped in the release ZIP) with its own
  test file; full suite green.

## 0.9.36 - Critical packaging fix: the 0.9.35 ZIP could not start

- **If you installed or updated to 0.9.35, take this update.** The 0.9.35 release
  ZIP was missing five internal code files (`routers/`, `security.py`, `models.py`,
  `rate_limit.py`, `usage_store.py`) — the release builder's file list was never
  updated when the server code was reorganized, so a fresh install (or an install
  updated from the ZIP) failed at startup with a module-not-found error. Installs
  running from the source repo were unaffected. 0.9.36 is the same product with a
  complete ZIP.
- **This can't happen again:** the release builder now fails closed if any staged
  code imports a local file that isn't in the ZIP, and the release test suite
  covers the guard (drop any required module and the build refuses to ship).

## 0.9.35 - Jarvis Voice Local is now Adam

- **The product is renamed Adam.** Same app, new name (the old one collided with
  a famous trademark). The app id is now `adam-local`, the release ZIP is
  `adam-local-vX.Y.Z.zip`, the launcher is `scripts\start-adam.ps1`, and updates
  ship from the `DCom17/adam-releases` repo.
- **Existing installs update seamlessly — nothing to reconfigure.**
  - Your `.env` keeps working: `JARVIS_TOKEN` is still honored (new installs get
    `ADAM_TOKEN`), so your phone stays signed in.
  - Your job history carries over: `data/state/jarvis.db` is renamed to `adam.db`
    automatically on first start.
  - Old desktop shortcuts keep working: `start-jarvis.ps1` remains as a forwarder
    to the new launcher.
  - The update check follows GitHub's redirect from the old releases repo, so
    pre-rename installs still see and apply this update.
- The phone-connection helper no longer hardcodes any particular coexisting app:
  it now reads each existing Tailscale serve's proxy target, refuses to overwrite
  any serve that isn't Adam's, and reports whose port is whose generically.
- The in-app brain template, docs, setup wizard, and web UI all speak "Adam" now.
- **Security pass.** `/health` no longer tells anonymous callers anything beyond
  "up, version X" — the full config summary (paths, public URL, which add-ons are
  on) now requires the bearer token, so a random web page probing
  `http://localhost:8000` cross-origin learns nothing. Every response carries
  baseline hardening headers (nosniff, same-origin framing, no-referrer). The
  documented one-tap sign-in is now the `#token=` fragment form, which never
  reaches the server or its console log (`?token=` links still work).

## 0.9.34 - Two doors: run Jarvis on your Claude plan OR pay as you go

- **You no longer need a Claude subscription to run Jarvis.** Setup (and the new
  Settings → AI plan control in the app) now offers two doors:
  - **Sign in with Claude** — your Claude Pro/Max plan covers usage at its flat
    rate, exactly as before. Recommended model: Claude Opus.
  - **Pay as you go** — paste an Anthropic API key instead. Like an arcade card:
    you load prepaid credit up front ($5 is roughly 200–300 conversations), it
    reloads only when you choose, and Jarvis can never spend past it. Recommended
    model: Claude Sonnet (fast and affordable); Opus and Haiku stay selectable.
- **A monthly budget Jarvis will not cross.** In pay-as-you-go mode, every turn's
  metered cost (reported by Claude itself) is recorded to a local usage store, and
  when the month's spend reaches your budget (default $10) Jarvis pauses with a
  clear "raise the budget" message instead of quietly running up a bill. Set the
  budget to 0 to remove the cap.
- **A live cost meter.** Settings → AI plan shows this month's and today's spend,
  so pay-as-you-go never feels like a taxi meter.
- **Your key can never be billed by accident — in either direction.** The API key
  lives only in `.env` and is handed only to the spawned Claude process, and only
  when you chose pay-as-you-go. On a subscription install the key (even one set
  globally on the machine) is actively stripped from the spawn, so it can never
  silently shadow your plan login.
- Model changes apply live — the next turn uses the new model, no restart.
- Under the hood: `auth_mode` / `voice_model` / `api_budget_monthly_usd` in
  settings.json (atomic + backed-up writes), `ANTHROPIC_API_KEY` in `.env`,
  new `data/state/usage.db`, new `/ai-plan` endpoints (token-gated; the key is
  write-only and never echoed), and a plan step in the setup wizard.

## 0.9.33 - JARVIS can name this chat (and open a new one, hands-free)

- **Rename the current chat by voice.** Ask JARVIS to name the conversation —
  "call this Daily Planning July 3" — and it renames the chat you're in on the spot,
  no menu, no typing. JARVIS will also offer a name when a chat clearly has a topic.
  The rename syncs to your other devices like any other chat change.
- **Open a new chat by voice — with your say-so.** JARVIS can start a fresh chat when
  you want a clean slate, but never on its own: it asks first ("Want me to start a new
  chat?") and only opens one after you say yes. The chat you were in is preserved in
  full — its last reply still lands there — and you're dropped into the new blank chat,
  ready to talk.
- JARVIS still cannot delete chats — deleting stays a manual action, by design.
- **Heads-up before escalating to Claude Code mode.** Holding the mode button into
  Claude Code now asks first — it warns that code mode starts a fresh conversation and
  the current chat's context won't carry over (code mode runs in its own workspace). Back
  out and you stay put; your existing chat is always preserved either way. Exiting code
  mode is unaffected.
- **Switch modes by voice.** Ask JARVIS to change its own mode — "go into operator
  mode", "switch to Claude Code", "back to voice" — and it flips the chat's mode for you,
  no reaching for the button. Operator and voice switch instantly; a switch into Claude
  Code goes through the very same "starts fresh" confirmation as the hold gesture, so you
  always get the heads-up before context is left behind. Code switching is only offered
  when Claude Code mode is enabled in settings.
- Under the hood: the directives JARVIS can emit in a reply (`<<RENAME_CHAT: ...>>`,
  `<<NEW_CHAT>>`, `<<SET_MODE: ...>>`) are extracted server-side and relayed to the app,
  which owns the chat list and mode toggle — the markers are always stripped so they
  never appear on screen or get read aloud. Works in every mode (voice, work, Claude
  Code). The job store carries the directive through the async/poll path (schema v2,
  migrated in place).

## 0.9.32 - Tap the orb to interrupt JARVIS mid-sentence

- **Long-query dictation.** Speaking a long request no longer chops itself into a
  premature turn when you pause for breath. The mic now accumulates what you say and
  only sends after you've *actually* stopped — a real trailing-silence commit instead of
  firing on every pause the browser calls "final."
  - The grace window is adaptive: short commands still send fast (~1.4s), but once you're
    clearly dictating a long query, mid-thought pauses get up to ~5s of room before it
    decides you're done.
  - Done early? Tap the orb to send the query immediately instead of waiting out the
    silence window. The live transcript reads back as one growing sentence so you can see
    the whole thing being captured.
  - The buffer is abandoned cleanly whenever the mic tears down — mute, JARVIS speaking,
    deactivate, or app backgrounding — so a half-spoken fragment can never fire later.

- **Barge-in.** While JARVIS is speaking, a grey "tap to interrupt" hint now sits under
  the orb. Tapping the orb cuts the readout short and drops straight into listening, so
  you can reply the moment you've heard enough instead of waiting out a long answer.
  - The tap runs inside a real user gesture, which is what lets iOS grant the mic on the
    synchronous restart — the same trick the existing tap-to-listen fallback relies on.
  - No post-speech echo-mute delay on a deliberate barge-in: the audio is cut at the
    source, so there's no speaker tail to swallow, and the text-match echo guard is
    cleared so it can't drop your opening words.
  - A soft descending "return" tick confirms the cut. Muted mid-speech settles to the
    muted state rather than forcing a listen.

## 0.9.31 - Cross-device chat deletes actually propagate

- **Deleting a chat on one device now removes it on the others.** Two independent bugs
  were keeping a delete stuck on the device that made it:
  - **Clock-skew blind spot (root cause).** The sync "what changed since I last pulled"
    cursor was a high-water mark of client `Date.now()` timestamps. Phones and PCs never
    share a clock, so a delete stamped by the device with the *slower* clock could sort
    *below* the other device's cursor and get filtered out of the pull forever — the
    tombstone was never delivered. The delivery cursor is now a server-assigned, monotonic
    `seq` (the server is the single clock), so a delete propagates regardless of device
    clock skew. Last-write-wins conflict resolution still uses `updated`; only *delivery*
    changed. Existing session databases migrate in place (seq backfilled in `updated`
    order); an upgraded client resets its cursor once and re-pulls.
  - **`session_store.py` was missing from the release.** The module that powers cross-device
    chat sync was never on the release allow-list — shipped builds contained its *test* but
    not the module, so on a clean install the server silently ran with sync disabled and no
    chat state (deletes included) ever synced. It now ships.
- New tests prove a lagging-clock delete is still delivered past a peer's advanced cursor,
  both at the store layer and end-to-end over the `/sessions` endpoints.
- **Chat text is now selectable/copyable.** The transcript (your inputs and JARVIS's
  replies) opts back out of the app-wide `user-select: none`, so text can be selected and
  copied on both desktop and mobile.

## 0.9.30 - Claude Code mode chats (opt-in, long-press escalation)

- **Hold the Operator Mode button to escalate a chat to raw Claude Code.** With
  `agent_safety.allow_code_mode` set to `true` in settings.json (default **false** — nothing
  changes unless you opt in), press and hold the Operator Mode button until the amber bar
  fills: that one chat becomes a **Claude Code mode** chat. In it, JARVIS runs as full
  Claude Code — direct file edits, shell, every tool, no proposal/approval gate — while
  every other chat keeps your normal capability tier. Voice in, voice out still works:
  long replies render on screen and only the spoken summary is read aloud.
- **Deliberate by design.** Escalation is per-chat, never global: a tap never enters or
  exits code mode (exiting takes the same long hold), a new chat never inherits it, and the
  button + sidebar badge turn amber so you always know which chat is hot. Every code-mode
  turn is recorded in the audit log.
- **Server-enforced gate.** A request claiming code mode on an install that hasn't enabled
  it is refused outright (403) — it never silently degrades or escalates.
- **Watch it work: live activity feed.** Code-mode turns stream, so while Claude works the
  status line shows each tool call as it happens — "Thinking… 84s · #12 Bash: python
  test_hunter.py" — instead of a silent spinner. Ephemeral by design: it's a live view,
  never stored.
- **■ STOP button.** A running turn can be killed mid-flight (any mode): the process tree
  dies server-side, the job records a clean "Stopped by user." (audited), and the chat
  keeps its context — just speak again to redirect. A stopped code turn may leave
  half-finished edits on disk; say "undo what you just did" and it will.
- **Room to actually code.** Code-mode turns get their own `code_claude_timeout_seconds`
  (default 60 min — the streaming + stop button make long turns supervisable) instead of
  the 10-minute async cap, and `agent_safety.code_mode_dirs` grants file-tool access to
  the repos you actually work on, beyond the vault.

## 0.9.29 - Quests always carry their XP bounty

- **No more zero-XP quests.** On the first hands-free board sync, JARVIS staged the day's
  quests with `xp_value: 0` — he over-applied the "never award XP without evidence" rule to
  the *displayed* value. The agent guidance and the brain's dashboard workflow now spell out
  the distinction: `xp_value` is the quest's planned **bounty** (what it pays if completed),
  assigned at staging and never 0; whether the XP is actually *awarded* is decided at
  shutdown review against evidence. Bounties come from the action class (micro ≈ 3,
  routine ≈ 4–6, boss_step ≈ 8, milestones per the tier table).
- **Quest `type` uses the real vocabulary.** `micro` / `routine` / `boss_step` / `milestone`
  — not "daily" — so the action class that sets the bounty survives onto the board and
  into the quest log.
- Two new prompt-guidance tests (external-actions suite now 72 checks).

## 0.9.28 - Hands-free dashboard sync

- **New "Auto-sync dashboard" gear toggle.** Mirrors the existing auto-approve-calendar
  switch: when on, any dashboard sync JARVIS stages during daily planning, shutdown, or
  "give me credit" pushes to your board **immediately**, no approval tap. He confirms it in
  past tense once it lands.
- **JARVIS now syncs the board himself.** He used to try to "run a PowerShell script" to
  sync, hit the app's no-shell sandbox, and hand you a copy-paste job. He now uses the
  built-in `hunter.sync` action lane that talks straight to your own Sheet — server-executed,
  no shell needed. The vault's dashboard + calendar workflows were updated to teach both
  runtimes (the app uses action blocks; desktop Claude Code still uses the scripts).
- **Safety unchanged.** Off by default. Only the dashboard sync auto-runs — file/code edits,
  email send, and LinkedIn posts still require approval. The dashboard bridge has no delete
  path by construction, the payload carries no secret, and every push is audited.

## 0.9.27 - The Hunter Dashboard now boots itself into view

- **New loading sequence for the Hunter Dashboard.** Instead of the spinning arc-reactor,
  the board now *assembles itself* out of the dark JARVIS field: the panel outlines trace
  themselves in from a center line, blurred placeholder text glitches into the header and
  number slots, then the card colors bleed in — a HUD "system initializing" build-up.
- **Covers the whole first screen.** The skeleton mirrors the real board top to bottom —
  title, profile, the three status cards, the notification panel, and the current-boss
  card — so there's no dead space while it loads.
- **Cuts straight to your real data.** The animation runs for a beat so it always reads,
  then the instant the sheet finishes loading it cross-fades out and your actual level,
  XP, fatigue, momentum, boss, and quests ease into place. A fast load never gets blocked;
  a slow one has something alive to watch instead of a spinner.
- **Respects reduced-motion.** If your phone has "Reduce Motion" on, the frame just appears
  without the theatrics.
- **Renamed "Hunter Dashboard" to "Operating System"** everywhere it's shown — the view tab
  and header, the view-switcher menu item, the Add-ons card, and the setup tutorial. Internal
  routes, config keys, tokens, and Apps Script function names are unchanged, so existing
  setups keep working.
- **Small board tweak:** the bottom "Rank Gate" section is now just "Rank."
- **Voice:** Jarvis no longer uses "gate" metaphors ("close that gate," etc.); it names
  things plainly.

## 0.9.23 - A pinnable JARVIS app icon for your taskbar

- **JARVIS can live on your taskbar now.** Setup creates a **JARVIS** shortcut (Desktop +
  Start Menu) with the blue-orb icon that you can pin to the Windows taskbar — right-click
  it → Show more options → **Pin to taskbar**. Windows 11 refuses to pin the old `.cmd`
  launcher, so this shortcut is built to be pinnable and keeps the orb icon when pinned.
- **One click starts *and* opens JARVIS.** Clicking the icon starts the server if it isn't
  already running (the server still opens its own visible window — nothing hidden), waits
  for it, then opens JARVIS in a **clean app window** — no browser tabs or address bar,
  already signed in. If the server's already up, it just opens the window.
- **Under the hood:** `start-jarvis.ps1` gained an `-AppWindow` switch that opens the
  signed-in URL as a Microsoft Edge *app* window (falls back to your default browser if
  Edge isn't installed); a small `scripts\jarvis-app.vbs` runs it without a console flash,
  and that's what the shortcut points at. Your existing `START.cmd` still works exactly as
  before. Setup also tidies up the older "Jarvis Voice Local" launcher shortcut so you're
  not left with two icons.

## 0.9.22 - Thinking pulse stops after two beats + tidier Approvals header

- **Approvals header buttons no longer wrap under the title.** On a phone, the **Undo /
  Refresh / Close** buttons were dropping onto a second line beneath the "APPROVALS" word.
  They're now pinned to the **top-right corner of the card** and stay there on the same
  line as the title. "Undo last" is shortened to **Undo** so the trio fits cleanly.
- **Smaller Auto-apply checkbox.** The "Auto-apply changes" toggle box is 25% smaller
  (16→12 px) so it sits more in proportion with its label.
- **The thinking heartbeat no longer drones.** It used to beat once a second the whole
  time JARVIS was working — which, on a long request, meant an endless *boom… boom…
  boom…* until the reply landed. Now it's just **two beats — boom, boom — then silence.**
  It acknowledges that the work started and gets out of the way. If the reply comes back
  before the second beat, it still stops early. Nothing else about the pulse changes
  (same calm, low tone; same **Interface sound: Off** toggle silences it).

## 0.9.21 - Real-voice nudge only shows when the voice is missing + nine more interface cues

- **The real-voice nudge now hides itself once the real voice is installed.** 0.9.20
  added a one-time "you're using the basic browser voice" banner and a **Real voice** row
  in the ⚙ menu — but the ⚙ row stayed put (showing "On") even after INSTALL-VOICE had
  run, just taking up space. Now both the banner and the ⚙ row appear **only while the
  real voice isn't detected**: the moment `GET /voice/status` reports it installed, the
  row disappears from the menu and the banner never shows. If you're still on the browser
  fallback, the nudge works exactly as before, pointing you at INSTALL-VOICE. Nothing in
  the voice pipeline changes.
- **Four new interface cues**, in the same synthesized voice as the 0.9.20 sound design
  (live on-device, no audio files, ducked under speech, silenced by the same **Interface
  sound: Off** toggle):
  - **Mute mic** — a soft low fall when you mute, answered by a brighter rise when the
    mic goes live again. Primes the audio channel, so even the first pre-activation tap
    is heard.
  - **Sessions drawer** (☰) — a quiet tick as the drawer slides in. Understated on
    purpose; it fires a lot.
  - **Settings / Approvals** — a brighter "focus" tick when the ⚙ menu or the ✓ approvals
    panel comes to the front.
  - **Hunter Dashboard** — a rising three-note ascent when you cross from JARVIS into the
    dashboard overlay. The biggest of the four, because it's a real change of scene.
  - **Settings option** — the smallest sound in the app, a dry tick when you tap a row in
    the ⚙ menu (Settings, Console, Add-ons, Capability, Auto-approve calendar, Notify).
  - **Back to JARVIS** — a soft descending "return" when you push ‹ Back out of a
    full-screen tab (Hunter Dashboard, Add-ons/Settings page, Operator Console). Only the
    tab that was actually open makes the sound.
  - **Switch chat** — a quick lateral step when you pick a different chat in the drawer.
  - **New chat** — a clean octave jump up, a fresh page, when you tap + New.
  - **Delete chat** — one low, clean fall when you confirm a delete. Deliberately calm —
    the dissonant fault tone stays reserved for real errors, not routine cleanup.

## 0.9.20 - JARVIS has a voice now — a tuned interface sound design + orb-hugging background motes

- **The machine makes sound.** JARVIS now has a small, deliberate set of interface
  cues — not a soundboard of movie effects, but one synthesized "instrument" with a
  consistent voice, so it reads as stable, futuristic equipment rather than a toy.
  Everything is generated live on-device (no audio files) through the same audio
  channel the voice already uses, so there's zero delay and it never fights the mic.
  The cues, and what each means:
  - **Boot voiceprint** on **ACTIVATE JARVIS** — a warm rising two-note "I'm here."
  - **Send whisk** whenever you send a message (button or Enter) — a quick forward tick.
  - **Reply ping** — a soft glass chime the instant a reply lands, just before the voice.
  - **Operator Mode** on/off — a bright rising "charge" engaging, a dimming fall releasing.
  - **Thinking pulse** — a slow, quiet heartbeat while JARVIS works. It stays calm and
    steady no matter how long it takes, and stops the moment the reply is ready.
  - **Save chime** — a resolved three-note "commit" when you save your sign-in.
  - **Fault tone** — the one and only dissonant sound, low and firm, reserved for a
    real error so it actually means something.
  All cues honor a duck while JARVIS is speaking so they never step on the voice.
- **An off switch.** The ⚙ menu has a new **Interface sound: On/Off** toggle. Off means
  total silence; the choice is remembered on the device.
- **The real voice is no longer a secret.** If you're still on the browser's robotic
  fallback voice, Jarvis now tells you the real voice exists: a one-time dismissible
  banner ("double-click INSTALL-VOICE, then restart Jarvis") and a new ⚙ menu row
  **Real voice: On / Get it** that's always there when you want the reminder. It only
  informs — a browser can't run the installer — and it reads a new read-only
  `GET /voice/status` (checks whether the Kokoro voice model is downloaded; no secrets).
  Before this, a friend who skipped the voice step at setup would just hear the robot
  forever with no hint the good voice was one double-click away.
- **Connecting your phone no longer means typing Tailscale commands.** The connect-phone
  helper can now do it for you: `connect-phone.py --apply` runs the recommended
  `tailscale serve` after asking you to confirm, and `--off` stops it again. It stays safe
  by construction — it runs **only** the port-scoped serve on/off, **refuses to overwrite**
  a port that's already in use (so it can never clobber another app's serve), and never runs `reset`, `funnel`, or `login`. With no flags the helper is
  still read-only and just prints the command, exactly as before.
- **Background motes now hug the orb.** The ambient dots from 0.9.19 used to scatter
  across the whole screen, including down around the text box and the bottom buttons.
  They're now anchored to the JARVIS orb and capped above the composer: ~80% cluster
  tightly around the orb and the rest drift in the band above the text box — none ever
  land in the text field or over the MUTE / mic / ACTIVATE controls. The field
  re-measures on resize, so it tracks the orb wherever the layout puts it.

## 0.9.19 - Operator Mode + the server window is now actually titled "Jarvis Voice Local"

- **Ambient background motes.** A sparse field of slow, light-blue dots now drifts
  behind the whole app — the same cyan twinkle as the orb's particle field, dialed
  way down. They cluster around the orb and thin out toward the edges, fade in and
  out of opacity, and the count is capped low (and lower on phones) so it stays
  atmospheric, never busy. Purely decorative: a fixed, non-interactive canvas behind
  the UI that pauses when the tab is hidden.
- **The approvals panel got readable.** The panel you open with the ✓ button is now
  titled **APPROVALS** (it used to say "PENDING," which didn't match the button that
  opens it). The box is now ~20% narrower,
  the header's three actions are distinct pill buttons — **↩ Undo last** (amber),
  **⟳ Refresh** (cyan), and **✕ Close** (gray) — instead of three identical cyan
  links you couldn't tell apart, the pending list now sits above the **Auto-apply
  changes (incl. overwrites/deletes)** toggle (which moved to the bottom of the
  panel), and that toggle label is pinned to a single line on mobile so it no longer
  wraps onto a second row.
- **The black server window now names itself.** The window Jarvis runs in used to open
  with the generic "Windows PowerShell" title, even though every update and setup
  instruction tells you to "close the black window **titled 'Jarvis Voice Local'**."
  That window is now titled exactly that, so the instructions finally match what you see.
- **The launcher window no longer masquerades as the server.** `START.cmd`'s own window
  used to carry the "Jarvis Voice Local" title too — confusing, because that's the
  throwaway window that says "you can close THIS one." It's now titled
  "Jarvis - Launcher (safe to close)" so there's exactly one window called
  "Jarvis Voice Local": the one you keep open (and close to stop Jarvis).
- **"Work mode" is now "Operator Mode."** The mode toggle that switches Jarvis from
  quick voice chat to the full agent (long on-screen replies, spoken summary only) was
  relabeled from "WORK: ON/OFF" to a single **OPERATOR MODE** button. The ON/OFF text
  is gone — the button lights up cyan when engaged, same as before. Purely cosmetic:
  the mode still sends `work`/`voice` under the hood, so sessions, persistence, and
  server behavior are unchanged.

## 0.9.18 - Header fix

- **Fixed the top bar.** In 0.9.17 the approve (✓) and settings (⚙) icons drifted into
  the center of the header, landing directly under the "JARVIS" title — overlapping it
  and unclickable, on both desktop and mobile. The earlier attempt to center the logo
  pulled the brand out of the header's grid flow, which left the right-hand icon cluster
  free to slide into the middle track. Each side of the header is now pinned to its own
  column, so the icons stay in the top-right corner (clickable) while the JARVIS logo
  sits dead-center on the viewport as intended.

## 0.9.17 - Self-healing self-edits + settings polish

- **The 4th safety rail: self-edits can't brick Jarvis anymore.** When Jarvis edits
  its own code (the Unrestricted tier / "Let Jarvis edit itself"), the change is now
  validated before AND after it's written:
  - **Pre-flight** — the new code is checked in isolation (Python is compiled, JSON is
    parsed) *before* the live file is touched. A change with a syntax error never
    reaches disk.
  - **Health probe** — after a code change is written, Jarvis boots a throwaway check
    that loads the whole app. If the app no longer starts (a bad import, a removed
    piece another file needed), the change is **automatically rolled back** to the last
    working version and the app is left running. Every step is recorded to the audit
    log. This is the failure mode that once required a manual re-install; it now heals
    itself.
  - The rail also covers **renames** of Jarvis's own code: renaming a source file makes
    its old name vanish, which can stop the app from starting just as surely as a bad
    edit. A rename that breaks the app is **reversed** (the file moves back, and anything
    it overwrote is restored), so no rename can brick Jarvis without auto-recovery.
  - The post-change health check boots a subprocess that can take up to ~40s; it runs
    **off the request loop**, so Jarvis stays responsive while a self-edit verifies
    itself instead of freezing until it finishes.
  - If Jarvis reverts a change but the app is *still* unhealthy, the cause was
    environmental (a moved config file, a PATH glitch, an already-broken sibling), not
    your change — and the report now **says so plainly** ("reverted — but the app was
    already unhealthy before this change, not this edit") instead of blaming the edit.
  - Like the other three rails (backup, audit log, secret protection), this stays ON at
    every capability tier and can't be turned off. It only ever engages for Jarvis's
    own source files — your vault and drafts are untouched by it.
- **Settings: you can now see which version you're running.** The ⚙ menu shows
  "Jarvis Voice Local v…" at the bottom.
- **Clearer label:** the first ⚙ menu item now reads **"Sign in"** (it only ever opened
  the sign-in screen; the old "Sign-in & settings" wording was misleading).
- **One fewer setting: "Let Jarvis edit itself" is gone — the capability tier now owns it.**
  Self-edit was redundant with the tier system (Unrestricted already turns it on, Safe and
  Powerful keep it off), and having both invited confusion. The standalone gear toggle and
  its `/self-edit` endpoints were removed; self-edit is now governed solely by the tier.
  Raise the tier to **Unrestricted** to let Jarvis change its own code. (Power users on
  Custom mode can still set `permissions.allow_app_self_edit` in `settings.json`.)
- **Jarvis knows its own tiers now.** It's aware of which capability tier is active and
  what it can and can't do at each level — so if you ask "what can you do?" or "what mode
  am I in?", it answers accurately instead of guessing, and it won't offer to do something
  the current tier forbids.

### Mobile UI polish
- **The "JARVIS" title is centered again.** On narrow phone screens the two right-hand
  header buttons (✓ approvals, ⚙ settings) took a wider slot than the single left menu
  button, which nudged the centered brand to the left. It's now anchored to the true
  center of the screen regardless of the side buttons' width.
- **The Hunter dashboard is zoomed out on mobile** so it's less wide and chunky —
  everything's a bit smaller and easier to take in at a glance (board zoom 0.82 → 0.68).

## 0.9.16 - Capability tiers: choose how much Jarvis can do on its own

- **New gear control: Capability — Safe / Powerful / Unrestricted.** One dial that
  sets how autonomous Jarvis is. Default is **Safe** (unchanged behavior: Jarvis
  proposes changes and you approve them).
  - **Powerful** — Jarvis auto-applies non-destructive changes across your whole
    vault and uses your connectors without asking; destructive changes (delete /
    overwrite) still ask first.
  - **Unrestricted** — full power: Jarvis can edit its own code, run shell commands,
    write across your vault and the app, and it auto-approves everything. Turning it
    on requires an explicit confirmation.
- **The safety rails stay ON at every tier — including Unrestricted — and cannot be
  turned off:** every change is backed up before it's written (so it's undoable),
  every action is recorded to the audit log, and your secrets (`.env`, keys,
  `settings.json`) are never touched. Full power stays recoverable.
- Built entirely on the server-mediated write path (the server stays the sole
  writer), so the rails always apply. The old raw "legacy_direct" mode is *not* a
  tier, precisely because it would bypass them.
- The tier takes effect immediately (no restart) and is remembered across restarts.
  Existing installs that haven't picked a tier keep their current behavior ("custom").

## 0.9.15 - Setup no longer aborts on a harmless pip warning

- **Fixes the wizard and voice installer dying mid-step on a cosmetic pip message.**
  On some PCs `pip` prints a harmless notice to its error stream (e.g. "Cache entry
  deserialization failed, entry ignored", or "a new release of pip is available").
  The install scripts run with PowerShell's `$ErrorActionPreference = "Stop"` and piped
  pip's output through `2>&1`, which turned that one notice into a **terminating**
  `NativeCommandError` — so SETUP quit at Step 3 ("Jarvis's building blocks") and
  INSTALL-VOICE quit before downloading the voice, both showing a red error and
  "You can close this window" even though nothing was actually wrong.
- Fix: the pip calls now relax the error preference to `Continue` for the duration of
  the install only. The real success test is unchanged — the scripts still verify the
  components by actually importing them (`fastapi`/`uvicorn`/…, and the Kokoro engine),
  so a genuine install failure is still caught. Only pip's harmless chatter is no longer
  fatal. No product/runtime behavior changes; installer scripts only.

## 0.9.14 - Cross-device chats now show your input, not just the reply

- **Fixes the "answer with no question above it" bug.** A turn you ran on one device
  (say your iPhone) could show up on another device (the PC) as just JARVIS's reply,
  with your own input missing. Cause: each device has two ways to reconstruct a chat —
  the full transcript sync (which *does* include your input) and a faster "pull the
  latest reply" path (`/push/last`, which stored **only the reply**). The reply-only
  path ran first on open/foreground/activate, so it painted a reply-only chat before
  the full transcript arrived.
- Two-part fix: (1) the server now stores **your prompt alongside the reply**, so a
  device surfacing a reply shows the question above the answer even before the full
  transcript syncs; (2) devices now **reconcile the full chat list first, then** pull
  any server-initiated reply — so a turn another device ran lands from its complete
  transcript (input + reply) instead of as a separate reply-only chat. A 2s cap keeps
  a slow sync from delaying a genuine SMS/voicemail reply.
- SMS and voicemail replies are unchanged (they have no in-app input bubble).

### Add-ons turn on instantly — no restart, no "not added" confusion

- **Enabling Calendar, Email, Hunter, or LinkedIn now takes effect immediately.**
  Before, clicking "Enable" wrote your settings correctly but the add-on kept showing
  as **not added** until you fully restarted the server (closing the black window) —
  so it looked like setup had failed even when it hadn't. The server now reloads the
  integration config in place the moment you enable, so the add-on flips to **Enabled**
  and works right away. (SMS and Voicemail still need a restart — they run a background
  poller that starts at launch.)
- **Clearer Gmail setup walkthrough.** The Apps Script authorization steps now match what
  you actually see: the grey **Advanced → Go to (project) (unsafe)** links at the bottom-
  left, the second pop-up where you tick the Gmail permission box (with a note that Jarvis
  only ever reads and drafts — it cannot send without your approval, and cannot delete,
  archive, or trash), guidance to paste the token into the box in step 8, and a note that
  an "unable to display page" pop-up after deploying is harmless (close it with the red ×
  and the Web app URL appears).

## 0.9.13 - A missing/half-applied module can no longer brick the server

- **Resilience fix.** If an update is interrupted (e.g. the black server window is
  closed while files are still being written) a brand-new module can be left missing,
  and the server then refused to boot at all (`ModuleNotFoundError: session_store`).
  The optional cross-device-sync module is now imported defensively: if it's missing,
  the server logs a clear warning, **disables chat sync, and starts normally** instead
  of failing to launch. The `/sessions` endpoints report `enabled: false` in that case.
- If you hit the boot error on 0.9.12: re-extract the 0.9.12 (or 0.9.13) zip over your
  install so the missing file is restored — your `.env`, `settings.json`, and `data/`
  are never in the zip, so nothing of yours is touched.
- Reminder surfaced by this incident: after an update, let it finish — **don't close
  the black "Jarvis Voice Local" window until it says it's done**, or the update can be
  applied only halfway.

## 0.9.12 - Cross-device chat continuity + iPhone notification fix

### Cross-device chat sync (new)
- **Your chats now follow you across devices.** Until now every chat lived only in
  that one device's browser storage, so a conversation on the PC never showed up on
  the phone (and an SMS reply landed on whichever device happened to surface it). The
  server now keeps **one authoritative copy of your chats + transcripts**, and every
  signed-in device reads and writes the same list.
- How it works: a new server-side session store (SQLite, like the job store) plus two
  token-gated endpoints — `GET /sessions?since=<cursor>` (pull what changed) and
  `POST /sessions` (push your local changes). Merge is **last-write-wins per chat** by
  a millisecond `updated` stamp; deletes are tombstones so removing a chat on one
  device removes it everywhere; renames, modes, and transcripts all propagate. Devices
  reconcile on open, on Activate, on every foreground, after each turn, and every ~25s.
- SMS/notification chats use a **deterministic id** (`srv-<session>`) so both devices
  converge on the *same* chat instead of creating duplicates.
- Empty, untitled placeholder chats don't propagate (so each device's first-run "Chat 1"
  doesn't multiply across the fleet).
- It's your own self-hosted server, so the history stays on your machine. Toggle off with
  `"session_sync_enabled": false` in `settings.json` to keep chats device-local.
- Tests: `test_session_store.py` (17) — LWW upsert, cursor filtering, delete tombstones,
  server-side transcript cap, token-gated endpoints, and a cross-device push→pull→edit
  round-trip proving a newer edit wins and a stale one is rejected.

### iPhone: the SMS chat shows up even when the tap is lost

- **Fixes the iPhone case where the texted reply never appeared.** 0.9.11 made tapping
  a notification open the chat on PC, but on iPhone it still depended on the service
  worker's tap handoff (`postMessage`/`openWindow`) reaching the page — and iOS
  routinely drops that when it has frozen or killed the installed PWA. Result: the tap
  landed on the idle screen and no chat was ever created, with nothing to recover it
  after a reload.
- **The app now pulls the reply itself instead of waiting to be told.** It checks the
  server (`/push/last`) for a new server-initiated reply on **app open, on Activate,
  and on every return to the foreground** — exactly the moments a notification tap
  brings the PWA forward — and surfaces it into its own chat. A per-device, reload-proof
  high-water mark (`localStorage`) means a reply is shown once and the app never
  auto-jumps back to one you've already seen. No dependence on the flaky tap at all.
- Foreground checks are throttled (3s) so bursty iOS focus/visibility/pageshow events
  don't hammer the endpoint.
- Note: this makes the SMS reply appear independently on each device. Full chat-history
  continuity *between* devices (a conversation started on PC visible on iPhone) is a
  separate, larger feature — chats are still stored per-device for now.

## 0.9.11 - Tapping a notification opens the chat

- **The notification → conversation handoff now works.** With 0.9.10 push *delivery*
  started working, but tapping a notification dropped you into JARVIS in standby and
  the texted reply never appeared as a chat — not even in the drawer after activating.
  Root cause: a server-initiated reply (SMS/push) was only turned into a chat *inside*
  the audio-playback path, which is gated behind the iOS "▶ TAP TO HEAR" gesture — so
  if you didn't tap that, the chat was never created. Showing the chat is now
  **decoupled from speaking it**: the instant you tap the notification the reply is
  surfaced into its own new chat (text needs no audio gesture), and the tap-to-hear
  prompt is only for the spoken audio. Fixes "the notification worked but it didn't
  take me to the new chat" on both iPhone and PC.
- Guarded against minting a duplicate chat for the same reply — it reuses the chat by
  session id, or by timestamp for the empty-session-id error pushes.
- **New add-on: Voicemail (AI missed-call message-taker).** When a call you don't
  answer is forwarded to your Twilio number, Twilio answers it in a calm British voice
  and records a message; JARVIS then **polls** Twilio for the recording — no public
  webhook, no tunnel, no exposed address, the same private outbound-poll model the SMS
  add-on uses — transcribes it, and pushes you the caller and transcript. It's a
  message-taker, not a chatbot: it never holds a conversation and never acts on anything
  a caller says. New `/setup-voicemail` wizard with the usual one-click bar — a **Test**
  button (verifies your Twilio creds, saves nothing) and an **Enable** button (the server
  writes its own `settings.json` + `.env`, no file editing), plus a **Wire-my-number**
  button that points the number's Voice webhook at your TwiML Bin via the API, a guided
  paste of the answer TwiML (deep British Polly voice — needs no audio hosting), per-carrier
  call-forwarding codes, and a **Hear-it-in-JARVIS's-voice** preview rendered with the
  bundled Kokoro voice. Shares the Twilio number/credentials with the SMS add-on (one
  number does both). Reads + transcribes only — never originates a call, never deletes a
  recording. Off by default; the core product never needs it.

## 0.9.10 - Push notifications actually work

- **Web Push now delivers (it never did on a fresh install).** Notifications — SMS
  replies, briefings, server-initiated results — were silently dropped because the
  app shipped a VAPID public key with **no matching private key**, so every push
  short-circuited (`_send_push` returns when the private PEM is absent). The server
  now **auto-generates a matched P-256 VAPID keypair on first start** (private PEM
  under `data/state/`, public key cached beside it) and serves the matching key from
  `/push/key`. A hand-set `.env` keypair (public key + its PEM) is still honored.
- **A visible "🔔 Notifications" control in the ⚙ gear menu** with live status:
  *On* / *Off — tap to enable* / *Blocked in settings* / *Add to Home Screen first*
  (iOS) / *Not supported here*. Tapping it requests permission and subscribes this
  device. No more silent failure with no way to tell if it's on.
- **iOS guidance baked in.** iPhone only delivers Web Push to an installed PWA, so on
  iOS-not-standalone the control says *"Add to Home Screen first"* and explains the
  Share → Add to Home Screen → open-from-icon → enable flow instead of failing mute.
- **Key-rotation safe.** If the server's VAPID key changes (e.g. the first real key is
  generated), the client drops its stale subscription and re-subscribes with the new
  key — so an old subscription tied to the orphaned key can't keep swallowing pushes.
- Tests: `test_push.py` (21) — keygen generates a matched, py_vapid-signable pair;
  idempotent (never rotates an existing pair); `/push/key` token-gated + reports the
  generated key; `/push/subscribe` stores + dedupes; `_send_push` degrades gracefully.

## 0.9.9 - (in progress)

- **Email wizard: the same detailed Apps Script walkthrough as calendar/hunter.** It had
  the old terse steps (the exact friction that bit calendar/hunter — identical Google
  flow): now it spells out save-the-project-first, the function picker + Run button, the
  full first-run authorization path (Review permissions → Advanced → Go to (unsafe) →
  Continue), "copy everything after the `=`", the detailed deploy (blue Deploy → New
  deployment → gear next to Select type → Web app → authorize again → copy the 2nd link).
- **LinkedIn wizard: token note corrected** to say the Enable button saves your token /
  client secret to `.env` for you (its portal + OAuth flow is different from the Apps
  Script add-ons, so the save/function-picker steps don't apply).
- **Email + LinkedIn now have the one-click "Enable" button too** — every add-on is
  now consistent (no hand-editing `.env`/`settings.json` for any of them).
  - Email (`POST /integrations/email/enable`): re-validates the bridge, then writes the
    gmail block + `GMAIL_BRIDGE_TOKEN`. Enables draft-only (sending stays a separate opt-in).
  - LinkedIn (`POST /integrations/linkedin/enable`): "Enable draft lane" writes just the
    settings (no secrets); "Enable auto-post lane" re-validates the token, resolves your
    author URN, and writes the settings block + `LINKEDIN_ACCESS_TOKEN` (and client secret
    if given). Tokens are written only to the local `.env`, never logged or echoed.

## 0.9.8 - Hunter dashboard polish + SMS one-click enable

- **SMS wizard: an owner-phone field + one-click "Enable SMS".** Added a "your phone
  number" step (only texts from it are ever acted on) and the same one-click enable as
  the other add-ons — the server writes the `settings.json` sms block and all four `.env`
  values (`TWILIO_ACCOUNT_SID`, `TWILIO_NUMBER`, `TWILIO_AUTH_TOKEN`, `OWNER_PHONE`) for
  you (`POST /integrations/sms/enable`), re-validating the Twilio creds first. No
  hand-edited files; the Auth Token is never logged or echoed. Finishes with a "text
  your Twilio number" try-it prompt.

- **Hunter dashboard: less zoomed-in on phones.** The in-app board scaled everything
  for a large status window; added a proportional zoom-out so it fits the screen with
  breathing room instead of pushing the edges.
- **Hunter dashboard: a "How To Use This" tutorial** (tap-to-expand, under Refresh
  Status) that introduces the daily flow and the exact commands — "Good morning" →
  "Start my day" / "Run daily planning" in the morning, "Give me credit" through the
  day, "How'd we do" / "See you tomorrow" at night, "Big picture me" weekly. New users
  were never formally shown how daily planning fills the board.

## 0.9.7 - Phone setup + Calendar & Hunter setup/approval overhaul

### Hunter dashboard wizard — same hand-holding + one-click enable
- `/setup-hunter` now walks the whole Apps Script flow: paste → **save the project**
  (functions don't appear until you do) → run `setupHunterDashboard` (function picker +
  Run button + the full Review permissions → Advanced → Go to (unsafe) → Continue auth
  path) → run `setHunterToken` ("copy everything after the `=` sign") → Deploy (blue
  Deploy → New deployment → gear next to Select type → Web app → authorize again → copy
  the second link, the `/exec` URL).
- **One-click "Enable Hunter"** writes `settings.json` + `.env` for you (new
  `POST /integrations/hunter/enable`), same as calendar — no hand-edited JSON. Manual
  path kept, collapsed. Finishes with a "try it" prompt.

### Brain memory + self-edit now work by voice (not just work mode)
- **Fixed: a proposed change leaked into the chat and saved nothing in voice mode.** The
  agent's safe-write lanes (propose-a-change, brain memory updates, self-edit) were gated
  to "work mode", so a `<<PROPOSE>>` block emitted while you were talking to Jarvis showed
  up as raw text, parked nothing, and offered nothing to approve. These lanes now work in
  BOTH voice and work mode. New brain files save automatically; edits to existing files
  still ask for approval (overwrite safety), but now actually appear in the ✓ panel.



### Calendar setup wizard — much more hand-holding + one-click enable
- **Step-by-step Apps Script walkthrough** in `/setup-calendar`: save the project
  first (the floppy icon / Ctrl+S — functions don't appear until you do), the function
  picker + Run button locations, and the FULL first-run authorization path (Authorization
  required → Review permissions → Advanced → "Go to (project) (unsafe)" → Continue), then
  where the token shows up in the Execution log.
- **Detailed deploy steps**: the blue Deploy button (top-right) → New deployment → the
  gear next to "Select type" → Web app → Execute as Me / Anyone, and "copy the SECOND
  link (the Web app URL, ends in /exec)".
- **One-click "Enable calendar"**: after the connection tests green, Jarvis writes its
  OWN `settings.json` block and `.env` token for you — no hand-editing JSON (the step
  that broke installs with a stray comma/bracket). New `integration_config.py` writes
  valid JSON + a clean `.env` line, atomically, backing up both first; new
  `POST /integrations/calendar/enable` re-validates the bridge, then saves. The error-
  prone manual path is still available, collapsed, with descriptive .env instructions.
- **"Try it" prompt** on completion + a clear "restart the black window to finish".

### Calendar adds/edits now work by voice — and can run without a tap
- **Fixed: calendar actions did nothing in voice mode.** The whole stage-an-action lane
  was gated to "work mode", so asking by voice produced a phantom "tap approve" the user
  could never find. Actions (and self-edit) now work in BOTH voice and work mode.
- **Opt-in "Auto-run calendar actions"** (gear menu, OFF by default): when on, a calendar
  add/edit the assistant stages runs immediately instead of waiting for an approval tap —
  the phone-friendly posture. Still server-executed, still no delete, every run audited.
  Code/file changes, email send, and LinkedIn posts always stay gated.
- Fixed a latent bug: the action prompt example used `summary` for an event; the bridge
  needs `title`, so created events would have been dropped as "missing title".
- **Fixed: TTS read the raw action block aloud.** In voice mode the spoken text is the
  whole reply, and the `<<ACTION>>{json}<<END_ACTION>>` block was only stripped of
  `<<SPEAK>>` markers — so Jarvis spoke the JSON, ending in "end action". Spoken text is
  now cleaned of ACTION *and* PROPOSE blocks (only the human sentence is read).

### "Change Jarvis itself" now points at the right switch
- When the "Let Jarvis edit itself" setting is OFF, the assistant used to invent a
  nonexistent "folder access" pop-up or point at Settings. It now gives the ONE correct
  answer: turn on the gear-menu toggle, then ask again. Self-edit also works by voice now.

### Phone setup

- **Fixed a sign-in detection gap** in the phone wizard: it checked only whether
  `tailscale status` printed anything, but a LOGGED-OUT Tailscale still prints output —
  so a new user with Tailscale installed but not signed in would wrongly get the serve
  command. It now reads `BackendState` (only "Running" counts as signed in) and shows
  the sign-in step otherwise.
- **Added "Add to home screen" instructions** to Connect-phone (iPhone Safari Share →
  Add to Home Screen; Android Chrome ⋮ → Install), so the phone gets a real app icon.
- **Added re-auth guidance**: the installed home-screen app has its own private storage
  and may ask for the token again — the section now explains how to sign it in using the
  Copy-token QR.

## 0.9.6 - Phone-connect fixes (black screen, localhost URL, success state)

- **Fixed the infinite black screen** when connecting a phone. The URL detector used
  to guess `https://<your-machine>.ts.net` from the MagicDNS name even when no
  Tailscale Serve was running there — so the QR pointed the phone at a dead port. Now
  ONLY a real serve mapping to this app counts as a usable HTTPS URL; without one, the
  wizard keeps showing the `tailscale serve` command until it's actually run.
- **Two-step "Step 1" now shows the phone-reachable URL**, not localhost (the display
  refreshes after detection).
- **The setup wizard now confirms success** ("✓ Phone access is ready — <url>")
  instead of silently disappearing on Re-check.

## 0.9.5 - In-app Tailscale setup wizard + clearer restart instructions

- **Connect-phone now guides Tailscale setup instead of dead-ending.** When there's no
  HTTPS address a phone can reach, the Connect-phone section shows a step-by-step
  wizard: install Tailscale (link), sign in, then the EXACT `tailscale serve …` command
  to run (with a Copy button) and a Re-check button. Driven by the read-only
  `connect-phone.py` helper via `GET /phone-setup` — it inspects and instructs, never
  runs Tailscale for you. The QR appears automatically once a secure URL exists.
- **Clearer "restart to finish" instructions** after an update (in-app green bar,
  `UPDATE.cmd`, and START_HERE): they now say to close the BLACK server window
  (titled "Jarvis Voice Local"), not just the browser app — the common reason an
  update "didn't take" is the server kept running the old code.

## 0.9.4 - Phone awareness, clearer sign-in errors, QR fix, add-on setup hints

Dogfooding fixes from a real friend-style install:

- **Connect-phone QR no longer encodes localhost.** It now uses a phone-reachable
  URL the server detects — the Tailscale Serve HTTPS URL (matched to this app's port,
  works anywhere) or a LAN IP fallback (with a note that iPhone voice needs HTTPS).
  New `phone_link.py` + `GET /connect-info`.
- **"Use me on your phone" is now a capability the assistant knows about** and offers
  when asked what it can do / about add-ons (it wasn't a registry add-on, so it was
  invisible before).
- **Clearer error when Claude isn't signed in.** A turn that failed because Claude
  Code needed `/login` showed a bare "connection error"; it now shows explicit sign-in
  steps (open a terminal, run `claude`, then `/login`).
- **Add-on cards now recommend WHERE to set up** (computer vs phone). All current
  add-ons say "set up on a computer (keys / Google scripts are painful on a phone) —
  use it from anywhere after." The assistant says the same when it suggests one.

## 0.9.3 - Updates via GitHub Releases + one-click in-app update

Replaced the fragile update delivery (a single pinned Google Drive file you had to
swap in place; a moved/deleted file broke every install) with **GitHub Releases**:

- **Added** `updater.py` — reads the releases repo's permanent "latest release"
  endpoint (public, no auth, no file IDs), compares versions, and applies the
  attached build with the smart 3-way updater. Publishing a release ships an update;
  deleting/replacing one never breaks an install.
- **Added** an in-app **"Update available → Update now"** banner (`web/index.html`):
  Jarvis checks on open and one click downloads + applies the update. Friends never
  touch a file or `UPDATE.cmd`. New endpoints `GET /update/check`, `POST /update/apply`.
- **Changed** `UPDATE.cmd`/`update.ps1` to use the same GitHub channel (via
  `scripts/self_update.py`) instead of the old Drive link.
- **Added** `scripts/publish-release.ps1` — maintainer publishes a release in one
  step (`gh release create`, or printed web steps if `gh` isn't installed).
- Configurable via `update_repo` (default the project's public releases repo) and
  `update_check_enabled` in settings.json.

## 0.9.2 - Fix: every turn failed on current Claude Code CLI

**Fixed** a bug where every conversation turn died with "connection error" (the
background turn exited in ~2s). The agent-safety deny list named three tools that
**current** Claude Code versions no longer have — `MultiEdit`, `BashOutput`,
`KillShell` — and the CLI hard-errors a whole turn if `--disallowedTools` names a
tool it doesn't know. Installs on an older CLI were unaffected, which is why it only
showed up on freshly-installed machines. The deny list is now `Write`, `Edit`,
`NotebookEdit`, `Bash` — all current tool names, still blocking every file write and
shell command. No change to the safety model; only the (now-valid) tool names.

## 0.9.1 - Smart Update (AI-merge)

You can now pull a bug fix with **UPDATE** instead of re-downloading the whole app —
and your own customizations survive it. The old update blind-copied the maintainer's
build over your install (fine before self-edit, lossy after it). The new path does a
per-file **three-way compare** against a pristine baseline kept under `data/baseline/`:

- **Added** `update_engine.py` — classifies every program file (new / unchanged /
  take-incoming / keep-local / conflict / no-baseline) and applies safely: files you
  never touched are updated (backed up first), files only you changed are **kept**,
  and nothing is ever silently overwritten. Deletions are never performed.
- **Added** `merge.py` — a deterministic line-based **diff3**. When you and an update
  both change the same file in *different* spots, they're **auto-merged**; only a true
  same-line clash becomes a conflict (your version is kept, the update's copy saved).
- **Agent-assisted conflict resolution** — held conflicts are surfaced to the work-mode
  agent (with self-edit on); "merge the update conflicts" has Jarvis read the three
  versions and propose a reconciled edit through the normal review/approve/backup flow.
  The conflict clears automatically once your merge is applied. New `GET /update-conflicts`.
- **Changed** `scripts/update.ps1` to apply via the engine (`scripts/apply_update.py`)
  instead of robocopy; it now backs up before every overwrite and reports what it kept.
- **Added** a first-launch baseline seed so a fresh install has a clean merge ancestor.

Safe-by-default and additive: `.env`, `settings.json`, and `data/` are never touched by
an update; conflict resolution is gated on the (default-off) self-edit setting.

## 0.9.0 - Mobile Access & Packaging

Makes the product reachable from a phone **securely** and packageable for a
friends-&-family beta — without becoming hosted SaaS, weakening the safe-by-default
model, or routing anything through anyone else's account. The supported mobile path is
**Tailscale + Tailscale Serve HTTPS**; desktop-local (`localhost`) is the floor that
works with zero networking; a public **cloudflared + Cloudflare Access** path is
documented as advanced/limited-support. Shipped in three reviewed slices.

### Added — Slice 2 (diagnostics + secure-context banner)
- **Intent-gated access diagnostics** in `onboarding.py` (3 new doctor checks: Mobile
  access (HTTPS), Tailscale (mobile path), CORS exposure). Advisory **WARN/PASS only —
  never FAIL**, never block startup; the only subprocess (`tailscale status`) is
  short-timeout and failure-safe. A desktop-local user with no remote config stays at
  **0 WARN**; warnings fire only when remote/mobile intent is detected (a public base
  URL or a non-loopback CORS origin).
- **Secure-context warning banner** in `web/index.html` (PWA) and `web/console.html`,
  via a self-contained `window.__jvlSecCtxWarn(protocol, hostname)`: shows only on
  `http:` + a non-loopback host; hidden on https / localhost / 127.0.0.1 / ::1 / [::1].
  Fixed-position overlay (no layout reflow), no app-state coupling, no network call, no
  remote asset, no secret. Explains that iPhone mic/voice/PWA need HTTPS and points to
  Tailscale Serve.

### Added — Slice 3 (packaging + docs)
- **`scripts/make_release.py`** (+ a thin `scripts/make_release.ps1` wrapper) — an
  **allow-list** release builder (ships only named files, never a directory walk) with a
  **fail-closed deny guard** that aborts before writing if any staged path matches a
  secret / runtime / rollback-backup / internal-doc pattern. Pure stdlib; output to
  `dist/`.
- **User-facing docs:** `docs/CONNECT_YOUR_PHONE.md` (Tailscale runbook + the
  HTTPS-secure-context rationale + troubleshooting), `docs/ADVANCED_REMOTE.md`
  (cloudflared + **mandatory Cloudflare Access**; public bearer-token-only exposure is
  not acceptable), `docs/SUPPORT.md` (scope/requirements + explicit "not yet" list),
  `docs/RELEASE.md` (secret-safe ZIP flow + manual update path).
- **`README.md`** — a "Documentation & mobile access" links block (quickstart kept lean).
- **`docs/CONSUMER_TEST_CHECKLIST.md`** — a v0.9 **manual Tailscale verification**
  section (PC + iPhone device round-trip; marked PENDING — needs real hardware).
- **`.gitignore`** — ignores `*.bak`, `*.pre-*`, and `dist/` (backup/build hygiene).

### Changed — Slice 1 (security/auth hardening)
- **`server.py` `require_token`** now uses a **constant-time** comparison
  (`hmac.compare_digest`) with safe header parsing (require the `Bearer` scheme + a
  non-empty token). Same 403/allow outcomes as before — only the comparison timing
  changed — important before any tunnel/remote exposure. The change is confined to
  `require_token`; **no new endpoint, no new write path** (POST routes 17 = 17).
- The existing `cors_allowed_origins` setting was confirmed honored end-to-end
  (allowed origin echoed, disallowed not); the permissive `*` default is unchanged.
- `APP_VERSION` → `0.9.0`.

### Tests
- Full suite green: `test_permissions` (43) + `test_job_store` (47) +
  `test_proposed_changes` (68) + `test_onboarding` (57) + `test_console` (79) +
  `test_audit` (39) + `test_auth_cors` (25, new) + `test_pwa_banner` (38, new) +
  `test_release` (67, new) = **463/463**.
- Doctor on real config: **15 checks, 0 FAIL, 0 WARN**, `agent_safety.mode` still
  `draft_only` (the 3 new access checks PASS on a desktop-local box).
- Headless Chrome (CDP) verified the banner rule for the required host/protocol cases
  and that the live banner is hidden over 127.0.0.1; `test_release` builds a real ZIP
  and asserts it is secret-free and complete.

### Safety posture
- `draft_only` default; server is the sole writer; review → approval → apply,
  permissions, approvals, backups, audit, and conflict detection all unchanged. The
  only server-code change all phase is the constant-time token compare. No new endpoint,
  no new write path. No secret printed, logged, rendered, or shipped. The live personal
  rig (`scripts/voice_server`) was not touched.

### Known limitation / caveat
- The **manual PC + iPhone Tailscale verification** (`CONSUMER_TEST_CHECKLIST.md`) is
  **PENDING** — it needs real hardware and can't be automated. v0.9.0 **proves and
  documents** the access path (constant-time auth, diagnostics, banner, secret-safe
  packaging); the real-device round-trip should be closed before public-beta confidence.
  It is non-gating for this checkpoint.
- The **non-technical consumer wrapper** (installer, bundled Python, connect-phone QR
  wizard, tray/auto-start) is deferred to **v1.0**.

## 0.8.2 - Operator Console: Action Layer

Lets the desktop Operator Console **act** on what it shows — approve / deny / apply /
refresh proposed changes and approve / deny parked approvals — by calling the same
token-gated endpoints the phone PWA already uses. This **deliberately retires the
console's strictly-read-only posture** (see DECISION_LOG D18). It is safe because it
adds **no new server endpoint and no new write path**: `server.py` is unchanged, and
apply still runs the server's permission re-check → stale/conflict gate → backup →
post-apply hash → audit. `agent_safety.mode` stays `draft_only`; the server stays the
sole writer; Claude still never writes directly.

### Added (web/console.html only — no server change)
- **`postApi()` helper** — a single POST sink that sends the bearer token only in the
  Authorization header (never the body or the DOM) and calls **only** the existing
  `/proposed-changes/{id}/{approve,deny,apply,refresh-diff}` and
  `/approvals/{id}/{approve,deny}` endpoints.
- **State-aware action bar** in the change-detail panel (built with `createElement` +
  `addEventListener` — **never** `innerHTML`):
  - **pending** → `[Approve & Apply]` `[Deny]` `[Refresh]`
  - **approved & not stale** → `[Apply]` `[Refresh]` `[Deny]`
  - **approved & stale** → `[Refresh]` `[Deny]` (Apply is not offered)
  - **conflict / failed** → `[Refresh]` `[Deny]` (no Apply, no Approve & Apply)
  - **applied / denied / expired** → no mutation buttons.
- **One-tap Approve & Apply** (matching the PWA): POSTs the existing approve endpoint,
  then the existing apply endpoint. On a `409` (not approved / conflict / stale) it
  surfaces the reason inline and **stops — never retries or forces**.
- **Confirmation prompts** naming the action + target on every write path
  (Approve & Apply, Apply, and both Deny actions). Approve-only and Refresh do not
  write and are not gated by a modal.
- **Approvals queue actions** — `[Approve]` `[Deny]` on **pending** rows only.
- **Inline result messaging** (ok / conflict-warn / error) and **refresh-after-action**
  (reloads the change list, status, audit, and the detail from fresh server state).

### Changed
- `APP_VERSION` → `0.8.2`.
- `test_console.py` — grown 39 → **79**: the read-only guard `[12]` is replaced by an
  action-layer contract (a single POST sink; every `postApi` target is an approved
  proposed-changes/approvals path; never POSTs the create endpoint or any
  non-action route; still **no `innerHTML` assignment**), plus `[13]` lifecycle
  (approve→apply writes; apply-before-approve → 409, not written; deny never writes;
  refresh resets to pending), `[14]` conflict (409, no overwrite) + protected/blocked
  rejected, and `[15]` no-secret in any rendered surface.

### Tests
- Full suite green: `test_permissions` (43) + `test_job_store` (47) +
  `test_proposed_changes` (68) + `test_onboarding` (40) + `test_console` (79) +
  `test_audit` (39) = **316/316**.
- Doctor on real config: **12 checks, 0 FAIL, 0 WARN**, `agent_safety.mode`
  still `draft_only`.
- Headless Chrome (CDP) against an **isolated sandbox server** (no real vault data
  touched): button-state matrix matched all six states; Approve & Apply via the UI
  button → `applied` + file written; Deny via the UI button → `denied` + file **not**
  written; conflict state exposes no Apply; an `<img src=x onerror=…>` summary renders
  as **literal text** (0 injected nodes); approvals rows show Approve/Deny; the token
  never appears in the DOM.

### Safety posture
- `server.py` **unchanged** (POST routes 17 = 17; only the v0.8.1 `/audit` GET beyond
  the pre-v0.8.1 baseline) — **no new endpoint, no new write path.** apply-time
  validation, stale/conflict detection, backups, post-apply hash, and audit are all
  intact (those files were not modified). `draft_only` default; server is the sole
  writer; no secret printed or rendered; no `innerHTML`. The live personal rig
  (`scripts/voice_server`) was not touched.

### Note — security-posture change
- This release **ends the console's strictly-read-only property** on purpose. The
  console can now trigger state changes — but **only** through the existing
  server-enforced approve → gate → backup → audit lifecycle, never a new path. The
  read-only Audit Viewer (v0.8.1) and the read-only sections (Status / Jobs / Audit)
  are unchanged. See DECISION_LOG D18.

## 0.8.1 - Operator Console: Read-Only Audit Viewer

Adds a **read-only** audit viewer to the Operator Console. The console stays
strictly visibility-only — no action controls, no new write surface. This is the
first of two halves of the old "Slice 2": the audit viewer (read-only, this
release) is intentionally separated from the action layer (approve/deny/apply
controls — deferred to v0.8.2, behind its own review/approval gate) precisely
because the action layer would change the console's security posture and the
audit viewer does not. `agent_safety.mode` stays `draft_only`; the server stays
the sole writer.

### Added
- **`GET /audit`** in `server.py` — a token-gated (`Depends(require_token)`),
  read-only tail of the structured audit log. Reads **only**
  `config.AUDIT_LOG_FILE` — **no path parameter, no traversal surface**, and it
  never exposes the raw server log. Newest-first. `?limit=` defaults to 100 and is
  clamped to `[1, 500]`. Reads a **bounded tail** (a fixed-size `deque`, so memory
  stays bounded on a large file). Each line is parsed defensively — a malformed
  line (or any non-object JSON) becomes a safe `{"_parse_error": true}` marker and
  its **raw text is never echoed**; the malformed count is returned. Returned
  fields are **whitelisted** to a fixed set of known-safe keys (ts, action_type,
  target, change_action, allowed, requires_approval, approved, risk, destructive,
  reason, session_id, job_id, proposed_change_id, backup_path, original_hash,
  current_hash); every other key — including any secret-like field — is dropped.
  Missing/disabled audit → `200` with `available: false` and empty entries; an
  empty file → `200` with `available: true` and empty entries.
- **`web/console.html`** — a read-only **Audit log** section (Time / Event /
  Target / Risk / Approved / Reason), a limit selector (50/100/200/500), and a
  Refresh button. Uses `GET /audit` only, renders with `textContent` (no
  `innerHTML`), surfaces a note for the disabled/empty/malformed states, and adds
  **no** action/mutation controls.
- **`test_audit.py`** — 39 FastAPI `TestClient` checks: auth matrix; missing /
  disabled / empty file; valid JSONL newest-first; limit honored + clamped to
  [1, 500]; malformed line → marker with no raw echo; extra + secret-like fields
  dropped; no `JARVIS_TOKEN` / VAPID / TWILIO value in the body.
- **`test_console.py`** — extended (+12): the served page exposes the Audit
  section + limit selector + references `/audit`, still leaks no secret, and a
  read-only guard asserts the page contains **no** approve/deny/apply/refresh-diff
  markers and **never** assigns `innerHTML`.

### Changed
- `APP_VERSION` → `0.8.1`.

### Tests
- Full suite green: `test_permissions` (43) + `test_job_store` (47) +
  `test_proposed_changes` (68) + `test_onboarding` (40) + `test_console` (51) +
  `test_audit` (39) = **288/288**.
- Doctor on real config: **12 checks, 0 FAIL, 0 WARN**, `agent_safety.mode`
  still `draft_only`.
- Live (server restarted to load the route): `/audit` no token → 403, wrong token
  → 403, valid token → 200 (88 real entries, malformed 0, no token leak, limit
  clamp verified); `/console` serves the Audit section.
- Manual: the signed-in console was rendered end-to-end in headless Chrome (the
  page's own JS against the live server) and screenshot-verified — Audit section
  visible, 88 rows newest-first, the six columns readable, limit 50/100/200/500
  works (switching to 50 → 50 rows), Refresh works, no secret in the DOM, no raw
  JSON dump, and only Refresh/Sign-out buttons (no mutation controls). All other
  sections (Status/Jobs/Proposed changes/Approvals) still render.

### Safety posture
- Unchanged and re-verified. `draft_only` default; server is the sole writer;
  approval/backup/audit/conflict behavior untouched; **no new write surface** (the
  audit viewer is a pure read; the console gained only a read view); no secret
  printed or rendered. The live personal rig (`scripts/voice_server`) was not
  touched.

### Deferred to v0.8.2 (not in this release, requires separate approval)
- The console **action layer**: wiring the existing approve / deny / apply /
  refresh-diff controls into the console. It would reuse the existing
  server-enforced approve → gate → backup → audit lifecycle, but it retires the
  console's strictly-read-only property, so it gets its own review/approval gate.
- Out of scope entirely: settings / permissions / safety-mode editors, auto-apply
  toggle, raw server-log viewer.

## 0.8.0 - Local Operator Console (read-only MVP) — Slice 1

A desktop-friendly **read-only** window into the durable runtime the back end
already has — job history, the proposed-change/approval queue, and proposed-change
diffs — served by the same app and gated by the same token. This is **Slice 1**:
it surfaces state and contains **no action controls** (no approve/deny/apply, no
settings/permissions/safety-mode editors, no audit viewer). The agent still runs
`draft_only`, the server is still the sole writer, and proposed changes still
require review → approval → apply through the existing flow. Slice 2 (wiring the
existing approve/apply controls + a read-only audit viewer) is deferred.

### Added
- **`web/console.html`** — a dependency-free desktop operator console. Paste the
  bearer token once (kept in `localStorage`, never re-displayed); it then renders
  **Status** (`/health` + `/ping` + `/ui-prefs`), **Jobs** (`/jobs` list with
  status/limit/problems filters + `/jobs/{id}` detail), **Proposed changes**
  (`/proposed-changes` + read-only colorized `/diff` + `/status`), and the
  **Approvals** queue. All data is rendered with `textContent` only (no HTML
  injection). **No action controls of any kind** — the page is provably read-only.
- **`GET /console`** in `server.py` — serves `web/console.html` as a static page,
  `Cache-Control: no-store`, **no auth** on the page shell itself (exactly like
  `/`); every data call the page makes is the existing token-gated API. This is the
  **only** server change in this release.
- **`test_console.py`** — 39 FastAPI `TestClient` smoke tests: the `/console`
  route serves and is no-store, the page carries no action controls / no secrets,
  and the data paths behave correctly under the auth matrix.
- **`requirements.txt`** — pins `httpx==0.28.1` (test-only; `TestClient`
  dependency).

### Changed
- `APP_VERSION` → `0.8.0`.

### Tests
- Full suite green: `test_permissions` (43) + `test_job_store` (47) +
  `test_proposed_changes` (68) + `test_onboarding` (40) + `test_console` (39) =
  **237/237**.
- Doctor on real config: **12 checks, 0 FAIL, 0 WARN**, `agent_safety.mode`
  still `draft_only`.
- Manual: console opened in Chrome/Edge — render, token sign-in, Status card,
  Jobs table + detail + problems filter, Proposed-changes table + diff render,
  Approvals section, read-only contract (no action controls), secrets not
  rendered, usable layout — all PASS. HTTP/source sanity (server restart, `/health`,
  `/console` load, auth correct/wrong/missing, all data paths, read-only contract,
  secrets-not-rendered) — all PASS.

### Safety posture
- Unchanged and re-verified. `draft_only` default; server is the sole writer;
  approval/backup/audit/conflict behavior untouched; **no new write surface** (the
  console is read-only and adds no action endpoint); no secret printed or rendered
  by the console or `/health`. The live personal rig (`scripts/voice_server`) was
  not touched.

### Deferred to Slice 2 (not in this release)
- Wiring the **existing** approve / deny / apply controls into the console.
- A **read-only** `GET /audit` audit-log viewer over `data/logs/audit.jsonl`.
- No settings / permissions / safety-mode editors, no auto-apply toggle, no raw
  server-log viewer — those remain out of scope for the console entirely.

## 0.7.0 - First-Run Onboarding & Setup Doctor

Makes a fresh install self-guiding and resilient to the most common consumer
config traps. No product behavior changes and no new write surface: the agent
still runs `draft_only`, the server is still the sole writer, and proposed
changes still require review → approval → apply. This release is two slices.

### Added — Slice 1 (guided first run)
- **`onboarding.py`** — importable, testable setup logic: `.env` token bootstrap
  (idempotent — generates a token only when missing, **never** overwrites a real
  one), `.env`/`settings.json` backup-before-edit, a whitelisted
  `set_settings_value` (may touch **only** `claude_exe` / `vault_path`, never
  `agent_safety` or `permissions`), and `run_doctor()` — a PASS/WARN/FAIL health
  report that prints **no secret values**.
- **`scripts/setup.py`** — guided, idempotent, non-destructive first-run
  (`python scripts/setup.py`); a plain Python entry point that sidesteps the
  PowerShell execution-policy wall.
- **`scripts/doctor.py`** — re-runnable health check (`python scripts/doctor.py`);
  exit 0 when no FAIL, 1 on any FAIL.
- **`test_onboarding.py`** — onboarding/doctor unit tests.

### Added — Slice 1b (config robustness & friendly errors)
- **BOM-tolerant settings loading** — `config._load_settings()` (and
  `onboarding`'s settings reads) now use `utf-8-sig`, so a `settings.json` saved
  with a UTF-8 BOM by a Windows editor loads normally instead of crashing import.
- **Friendly malformed-config errors** — a broken `settings.json` now yields a
  plain-language message that names the file, the parse line/column, and the fix
  ("restore it from settings.example.json"). No file contents, no secrets.
- **`onboarding.validate_settings_file()`** — BOM-tolerant, never-raising
  validator returning `None` (clean/absent) or a friendly message (malformed).
- **Doctor/setup degrade cleanly** — `run_doctor()` adds a "Configuration file"
  check and, when config can't load, reports a FAIL and continues with the
  config-free checks instead of dumping a traceback; `scripts/setup.py` surfaces
  a "[!] Configuration problem", runs the doctor, exits non-zero, and **never
  auto-edits** a broken `settings.json`.

### Changed
- `config._load_settings()` still **raises** on malformed JSON (the server must
  fail fast on bad config) — but with the friendlier message above. Only
  doctor/setup swallow it into a clean FAIL.
- `proposed_changes.py` — **F1**: `_resolve_target` no longer doubles
  `data/drafts/data/drafts/…` (keys on `config.ROOT`). **F2**: the write target is
  validated at **create** time (raises `ValueError` → 400), as defense-in-depth;
  the **apply-time** permission gate is unchanged.
- `.gitignore` — secret/config backups (`.env.*`, `*.env.bak`,
  `settings.json.bak.*`) never ship; `!.env.example` preserved.
- `README.md` — setup now leads with `python scripts/setup.py` /
  `python scripts/doctor.py`; manual steps folded into a details block.
- `APP_VERSION` → `0.7.0`.

### Tests
- Full suite green: `test_permissions` (43) + `test_job_store` (47) +
  `test_proposed_changes` (68) + `test_onboarding` (40) = **198/198**.
- Slice 1: manual clean-copy run (throwaway folder) — fresh `.env` + generated
  token (never printed), idempotent re-run keeps the identical token, doctor
  clean report, secret backups git-ignored, `legacy_direct`→FAIL /
  `draft_only`→PASS, F1/F2 verified.
- Slice 1b: manual validation against real malformed and real BOM-prefixed
  `settings.json` — doctor → friendly FAIL (no traceback, exit 1); setup →
  "[!] Configuration problem" + exit 1, `settings.json` left byte-identical; BOM
  file → "Configuration file PASS (UTF-8/BOM ok)"; restored config → 12 checks,
  0 FAIL, 0 WARN, `agent_safety.mode` still `draft_only`.

### Safety posture
- Unchanged and re-verified. `draft_only` default; server is the sole writer;
  approval/backup/audit/conflict behavior untouched; no new write surface; no
  secret printed by setup, doctor, or `/health`. The live personal rig
  (`scripts/voice_server`) was not touched.

### Known limitation / follow-up
- The **interactive TTY prompt path** of `scripts/setup.py` (the live
  `vault_path` / `claude_exe` questions) was **not** human-tested in this
  checkpoint — it can't be driven from the non-TTY automation harness. The
  underlying logic is unit-tested and the non-interactive path is validated; a
  short manual TTY checklist is the optional follow-up. Not a v0.7.0 blocker.

## 0.6.0 - PWA Parity & Device-Readiness

Brings the product PWA (`web/`) to verified code parity with the personal rig's
device-tested voice loop. The product `web/` was a snapshot that *predated* the
rig's on-device dogfood pass, so it was missing 9 voice/audio bug fixes and 2
features. This release ports all of them — without weakening any Phase 2–5 safety.
No server-side logic changed: `draft_only` stays the default, the server stays the
sole writer, auth stays fail-closed, and no secret reaches the frontend or logs.

### Added (ported from the rig's device-verified frontend)
- **Self-echo rejection on iOS** — the recognizer is now aborted+rebuilt per reply
  on iOS too (was desktop-only), so the phone never transcribes and re-submits its
  own spoken reply over a long conversation.
- **Dynamic audio-session switching** (`setAudioSession`) — `playback` while
  speaking (audible on silent mode, full-quality voice) vs `play-and-record` while
  listening (mic-compatible route). No-ops gracefully where the API is absent.
- **Replay transport controls** — Replay is now a transport: tap = replay /
  pause / resume; **press-and-hold (≥500 ms) = restart from the top**, with a CSS
  progress sweep. Replaces the old one-shot replay.
- **Overlapping-audio guard** — a `speakGen` generation token + `stopTtsSources()`
  invalidate and stop a prior readout when a new one starts, so audio can always be
  stopped (no orphaned `AudioBufferSource`s).
- **Switch-chat hard-stop** (`stopSpeaking`) — switching chats mid-speech stops the
  old chat's readout (and re-arms the mic for the new chat) instead of freezing on
  "Speaking".
- **Activation gating** (`body.engaged`) — the composer / work-replay row /
  attachment chips are hidden until JARVIS is activated, so a typed turn can't fire
  into a non-polling (dead) state.
- **Cold-start audio warm-ups** — a 300 ms warm-up before the greeting clip and a
  450 ms warm-up before a notification cold-launch reply, so the first clip isn't
  rendered silent on a freshly-created `AudioContext` (Replay remains the fallback).
- **Mid-render watchdog fix** (`streaming` flag) — the speak watchdog no longer
  settles (flipping the orb to Listening) while sentences are still being scheduled.
- **Bluetooth kickoff retry** (`KICKOFF_TRIES`) — a transient network blip when
  Bluetooth/CarPlay connects mid-turn retries the `/ask_async` kickoff instead of
  killing the turn (network/abort errors only — a real HTTP response is handled
  once, so no duplicate jobs).
- **Session list no longer reorders on view** — `switchTo` no longer bumps a
  session's `used` time; a separate `touch()` promotes a chat only on real
  interaction (prompt sent / reply received).
- **Server-initiated reply → its own chat** — a push/SMS reply whose `sid` the
  device never saw opens a new session instead of hijacking the open conversation.

### Changed
- `web/index.html` rebuilt from the rig's device-verified frontend with the
  product's Phase 3 proposed-changes / approvals UI re-spliced verbatim (header
  `approvalsBtn`, `approvalsModal`, approvals `<script>`). Four rig-flavored *code
  comments* were generalized for the product (Cloudflare→proxy timeout,
  `"Start my day"` example removed, "morning briefing" → "push notification").
- `APP_VERSION` → `0.6.0`.

### Tests
- Unit suites unchanged and green: `test_permissions` (43) + `test_proposed_changes`
  (61) + `test_job_store` (47) = **151/151**.
- Headless server smoke (FastAPI TestClient): **20/20** — auth matrix (no/ wrong/
  correct token on `/jobs` + `/proposed-changes`), `/health` reports `draft_only`
  and leaks no token/private key, proposed-change create→list→diff→approve→apply
  with the server as sole writer, protected target refused (422).

### Not done / deferred
- **Real-device pass not run** — iPhone / Bluetooth / car Bluetooth / live Claude
  turns are **READY-FOR-HUMAN-PASS**, not passed. See `docs/CONSUMER_TEST_CHECKLIST.md`.
- No installer / tray / updater / licensing / job-history console (Phase 7).
- `legacy_direct` remains opt-in and unsafe-for-product; default unchanged.

## 0.5.0 - Persistent Runtime State

Makes Jarvis Voice Local behave like real local desktop software: async jobs and
their history survive a server restart, crash, or machine sleep. Before this, the
job table was an in-process dict — a restart vanished every in-flight and finished
job, so the phone's `/poll` got a 404 and the turn was lost. No Phase 2–4 safety
was weakened; the default remains `agent_safety.mode = draft_only`.

### Added
- **`job_store.py`** — a SQLite-backed job store (stdlib `sqlite3`, **no new
  dependency**) at `data/state/jarvis.db`. One `jobs` table plus a
  `schema_version` table. Thread-safe (shared connection + lock), WAL mode for
  crash durability. Full lifecycle fields per job: `job_id`, `created_at`,
  `updated_at`, `started_at`, `completed_at`, `interrupted_at`, `status`, `mode`,
  `session_id`, `input_summary` (truncated), `result`, `spoken`, `error`,
  `message`, `ts`, `pid`, `proposed_changes`/`proposed_change_ids`,
  `notification_status`, `delivered`.
- **Canonical statuses**: `queued · running · complete · failed · interrupted ·
  cancelled`, mapped to the existing wire vocabulary by `to_wire()`
  (`complete`→`done`, `failed`/`interrupted`/`cancelled`→`error`,
  `queued`/`running`→`running`) so the PWA poll loop is **unchanged**.
- **Startup recovery** (`recover_interrupted`, run at import before the app serves
  traffic): jobs left `queued`/`running` by a previous process are marked
  **`interrupted`** (never silently complete), keep their last-known info, get an
  `interrupted_at` stamp + human reason, and emit a `job_interrupted_on_startup`
  audit event. Interrupted jobs are **not** auto-rerun. A `/poll` of one returns
  `error` + reason, so the PWA resets cleanly instead of seeing a 404.
- **Job-history endpoints** (bearer-token gated): `GET /jobs`
  (recent, newest-first, `?status=` + `?limit=`) and `GET /jobs/{id}` (full
  record). For the future desktop companion/tray and a PWA history view.
- **State schema versioning** — `schema_version` table, a minimal forward-
  migration function, and a clear startup log line. No heavyweight framework.
- **History retention** — `sweep()` retires terminal jobs older than
  `job_history_ttl_days` (default 7); active jobs are never swept. New config:
  `job_history_ttl_days`, `job_input_summary_max_chars`.
- **`test_job_store.py`** — 47 checks: create/get, status updates
  (complete/fail), list + status filter, **restart simulation marks a running job
  interrupted**, completed/failed jobs survive a restart, wire mapping, delivered
  bookkeeping, schema versioning, and sweep (active never swept).

### Changed
- **`/ask_async`** now persists the job (status `running`, truncated input
  summary, pid) **before** starting the background task — same request/response
  contract. **`/poll/{id}`** reads from the DB and returns the same shape, but no
  longer deletes the job (it's kept for history); a terminal result is marked
  `delivered` instead. Re-polling is idempotent (the PWA dedupes by `ts`).
- **`_run_job`** writes outcomes to the job store (`complete_job` / `fail_job`)
  instead of an in-memory dict; it also records whether the result was pushed or
  suppressed (foreground).
- `/health` now reports `jobs_db` and `state_schema_version`.
- App version `0.4.0` → `0.5.0`.

### Push / last-result
- **No change needed.** Push subscriptions (`data/state/push_sub.json`) and the
  last finished result (`data/state/last_result.json`) were **already persisted**
  to disk before Phase 5; they were not in memory. Left exactly as-is — the
  inspection confirmed migrating them would be risk for no benefit.

### Storage decision
- SQLite for **jobs only** — they need status updates, query-by-id, list-recent,
  and durable history. The already-working JSON stores (approvals,
  proposed_changes, push, last_result) were **intentionally not migrated**;
  Phase 5 deliberately avoids overbuilding a full state database.

### Safety (unchanged, re-verified)
- Default remains `agent_safety.mode = draft_only`; `--disallowedTools`, the
  sandbox cwd, `permissions.py`, and the proposed-change review flow are
  untouched. **No secrets, tokens, or push keys are written to the job DB**; only
  a truncated input summary is stored (the full prompt is not). The full result
  is stored, consistent with the pre-existing `last_result.json`. Phase 2 (43)
  and Phase 3/4 (61) test suites still pass. Nothing in the live personal rig at
  `scripts/voice_server` was touched.

### Known limitations
- A job that was **mid-flight** at restart cannot resume — it's marked
  `interrupted`, not re-run. Resumable jobs are deliberately future work.
- Single-user assumptions remain (one push-sub list, one last-result, one job DB).
- Inbound-SMS turns (`/sms`) are still fire-and-forget and are **not** tracked as
  jobs (unchanged behavior).

## 0.4.0 - Trustworthy Change Review

Makes proposed changes safe and understandable enough for real product use: the
user sees exactly what a change does, and the server refuses to overwrite work
that changed since the proposal was made.

### Added
- **`diffs.py`** — dependency-free diff + hash helpers. SHA-256 over raw file
  bytes (so binary files still get a baseline for conflict detection); unified
  diffs for UTF-8 text within the size cap, with line endings normalized so a
  content change doesn't show as every-line-changed on Windows (CRLF vs LF).
  Binary / too-large / unreadable / missing targets degrade to a diff marked
  *unavailable* with a human-readable reason instead of crashing.
- **Diff + hash capture at proposal time** (`proposed_changes.create`): each
  record now stores `original_hash/size`, `proposed_hash/size`, `diff`,
  `diff_available`, `diff_unavailable_reason`, `diff_created_at`, plus
  `risk_reason` and apply/conflict bookkeeping fields
  (`current_hash/size`, `stale_checked_at`, `conflict_reason`, `applied_by`,
  `apply_error`).
- **Staleness / conflict detection** in `apply()`: before backup/write, the
  current on-disk hash is compared to the baseline captured at proposal time. A
  mismatch (file changed, deleted, or unexpectedly created) moves the record to
  **`conflict`**, audits a `proposed_change_conflict` event, and returns an
  error — the file is **never** silently overwritten. Records created before
  Phase 4 (no baseline hash) skip the check gracefully and behave as before.
- **`refresh_diff()`** — re-baselines a change against the file as it is now and
  regenerates the diff (proposed content unchanged), resetting it to `pending`
  so it must be re-approved. The recovery path out of a conflict.
- **`status_preview()`** — a read-only live staleness check (does not mutate the
  record), so the UI can warn "this file changed" before Apply.
- **New endpoints**: `GET /proposed-changes/{id}/diff`,
  `GET /proposed-changes/{id}/status`,
  `POST /proposed-changes/{id}/refresh-diff`. `…/apply` now returns **409** on a
  conflict (body carries `status=conflict` + `conflict_reason`).
- **PWA review panel** now shows, per change: type, target (and rename
  destination), risk, status, a colorized unified diff (or the unavailable
  reason), a conflict/stale warning, the backup path once applied, and
  state-appropriate Approve / Deny / Apply / **Refresh** buttons. It surfaces
  conflict and failed changes (not just pending) so they can be recovered.
- **Tests**: `test_proposed_changes.py` grows from 30 to 61 checks — diff for
  new/replace, diff-unavailable for binary/too-large, hashes/sizes stored,
  stale-conflict blocks apply, refresh recovery, apply-on-match, audit
  conflict/apply events, legacy-record graceful apply, non-mutating status
  preview. All Phase 2 (43) checks still pass.

### Changed
- List responses (`GET /proposed-changes`) omit the (potentially large) `diff`
  blob; fetch it per change via `…/diff`. Per-change content is still omitted
  from lists as before.
- App version `0.3.0` → `0.4.0`.

### Safety (unchanged, re-verified)
- Default remains `agent_safety.mode = draft_only`; `--disallowedTools` and the
  sandbox cwd are untouched. Apply still requires approval, re-runs the full
  permission check (blocked/protected/allow-list), and backs up before
  overwrite. Denied/unapproved changes cannot apply. Nothing in the live rig at
  `scripts/voice_server` was touched.

### Known limitations
- Conflict detection is whole-file hash equality, not a 3-way merge: if a file
  changed, the proposal is blocked, not auto-merged — the user refreshes and
  re-approves. No force-apply was added (deliberately).
- Diffs are line-based UTF-8 only; binary and very large files show as
  unavailable. Hashes still protect them from blind overwrite.

## 0.3.0 - Phase 3: Safe Agent Mode + Proposed-Change Flow

Closes the real Level 3 gap: Claude Code itself can no longer write the user's
files directly in the default mode. Enforcement is at the CLI, not the prompt.

### Safety inventory (verified empirically, not assumed)
- The pre-Phase-3 spawn used `cwd = vault`, `--add-dir` for work dirs, and **no
  permission flags** — so Claude's write/shell capability was governed entirely
  by the customer's own Claude Code permission config.
- `scripts/agent-write-probe.ps1` confirmed: with an auto-accept permission mode,
  Claude **does** write to its working directory directly. With
  `--disallowedTools Write Edit … Bash`, the same request is **blocked** — the
  denial overrides the permission mode. That is the enforcement lever.

### Added
- **`agent_safety` settings block** with conservative defaults:
  `mode: draft_only`, `allow_direct_claude_writes: false`,
  `force_outputs_to_drafts: true`, `require_server_applied_writes: true`,
  and a `denied_tools` list. Three modes: `draft_only` (safe default),
  `controlled_write` (reserved), `legacy_direct` (old behavior, marked unsafe).
- **Spawn hardening** in `run_claude`: in safe modes Claude is launched with
  `--disallowedTools` (mutators + shell denied) and `cwd` set to a throwaway
  `data/agent_workspace`, with the vault/work dirs added as **read-only context**.
  `legacy_direct` preserves the original cwd=vault, full-tools behavior.
- **`proposed_changes.py`** — the controlled-write flow: create / list / get /
  approve / deny / **apply**. `apply()` (the only writer) re-runs the permission
  check, backs up before overwrite, refuses blocked/protected/out-of-allow-list
  targets, gates destructive actions on approval, and audits the lifecycle.
  Statuses: pending → approved | denied → applied | failed.
- **Proposed-change endpoints**: `GET/POST /proposed-changes`,
  `GET/POST /proposed-changes/{id}`, `…/approve`, `…/deny`, `…/apply`.
- **`<<PROPOSE>>` extraction** — work-mode replies in safe modes have proposed
  file changes parsed out and recorded automatically (routed into `data/drafts`);
  the cleaned reply reaches the screen/speaker. `/ask`, `/poll` now return a
  `proposed_changes` summary.
- **PWA pending panel** — a `✓ Pending` header button + modal listing pending
  proposed changes and approvals with target/risk and Approve/Deny/Apply buttons,
  plus a pending-count badge.
- **`agent_safety` block in `/health`**; `data/agent_workspace`, `data/drafts`,
  `data/outputs` created at startup.
- **Tests**: `test_proposed_changes.py` (30 checks — create/approve/deny/apply,
  backup-on-overwrite, denied-not-applied, blocked/protected refused,
  destructive-requires-approval, default-mode-safe, extraction). Manual
  `agent_write_probe.py` + `scripts/agent-write-probe.ps1`.

### Changed
- `run_claude` spawn now depends on `agent_safety.mode` (see above). Default
  `draft_only` means work mode proposes changes instead of editing directly, and
  Claude's `cwd` is a sandbox rather than the vault.
- App version `0.2.0` → `0.3.0`.

### Notes
- Preserved: `/ping`, `/health`, PWA serving, token auth, `/ask`, `/ask_async`,
  `/poll`, `/speak`, uploads, push, SMS. Voice mode is unaffected (it never wrote
  files). Phase 2's 43-check suite still passes.
- The live personal rig in `scripts/voice_server` is untouched.
- **Remaining risk:** enforcement is `--disallowedTools` + sandbox cwd, not a
  kernel sandbox. A future tool outside `denied_tools`, or flipping to
  `legacy_direct`, reopens direct writes. Keep `denied_tools` current.

## 0.2.0 - Phase 2: Level 3 Permission + Approval Foundation

The safety foundation for a controlled-action assistant. Deny-by-default with an
explicit, operator-configurable allow-list. No existing behavior changed — the
permission layer is added around writes, and the approval flow is new.

### Added
- **`permissions.py`** — the permission manager: path normalization/validation
  (resolves `..` traversal away), read/write allow-list checks, blocked-path and
  protected-filename (secret/key/config) refusal, conservative rule-based
  destructive-action detection (natural language + command syntax), risk
  classification, approval policy, backup-before-write, structured audit logging,
  and a high-level `evaluate()` decision entry point.
- **`approvals.py`** — persisted pending-approval store (create / list / get /
  approve / deny) with 24h expiry. Records carry id, timestamps, status, action
  summary, risk level, target, and session/job id. Backed by
  `data/state/approvals.json`.
- **Approval endpoints** (all bearer-token gated):
  `GET /approvals`, `POST /approvals`, `POST /approvals/{id}/approve`,
  `POST /approvals/{id}/deny`.
- **`permissions` block in settings** (`settings.example.json` + `settings.json`)
  with conservative defaults: read the vault, write only to `data/outputs` +
  `data/drafts`, shell off, destructive off, writes need approval, backup before
  overwrite, audit on.
- **Audit log** at `data/logs/audit.jsonl` — one JSON line per permission
  decision and approval transition. No secrets.
- **Backups** at `data/backups/` — existing files are copied (timestamped)
  before being overwritten; the backup path is audited.
- **`test_permissions.py`** + **`scripts/test-permissions.ps1`** — 43-check suite
  exercising the whole layer against a throwaway sandbox (never touches real
  `data/`).
- `GET /health` now includes a non-secret `permissions` snapshot.
- `config.py`: permission policy resolution + `permission_summary()`;
  `data/backups` and the configured write dirs are created at startup.

### Changed
- **`/upload`** now runs through the permission layer: the target is
  permission-checked, backed up if it would overwrite, and audited. (Uploads are
  server infrastructure writes, so they don't require human approval — but they
  use the same machinery every future content write will.)
- App version `0.1.0` → `0.2.0`.

### Notes
- Preserved unchanged: `/ping`, `/health`, PWA serving, token auth, `/ask`,
  `/ask_async`, `/poll`, `/speak`, uploads, push, SMS, work mode, and the Claude
  subprocess flow.
- The live personal rig in `scripts/voice_server` (inside the vault) is untouched.
- **Scope:** the layer governs writes the *server* performs. Claude's own edits
  inside the Claude Code subprocess use Claude Code's separate permission system;
  bridging those into this layer is Phase 3.

## 0.1.0 - Phase 1 Packaging

First reproducible packaging of the personal rig into a clone-and-configure
project. No new product features — the goal was to remove machine-specific
assumptions and make setup predictable.

### Added
- `config.py` — single configuration layer (`.env` secrets + `settings.json`
  app settings + built-in defaults). Auto-detects `claude.exe` on `PATH`.
- `.env.example` and `settings.example.json` templates.
- `GET /health` — status + non-secret config sanity (claude/vault configured,
  model, push/twilio enabled). `/ping` retained as a bare liveness check.
- PowerShell scripts: `start-dev`, `stop-dev`, `restart-dev`, `health-check`.
- Rotating file log under `data/logs/`, with errors/job failures logged.
- `README.md` (Windows setup, configuration, run, troubleshooting, limitations).
- `.gitignore` excluding `.env`, `settings.json`, `*.pem`, and `data/`.

### Changed
- All hardcoded paths/values moved out of `server.py` into `config.py`:
  Claude exe, vault path, model, TTS URL, work dirs, upload/log/state dirs,
  host/port, CORS origins, timeouts, Twilio webhook.
- CORS origins are now configurable (default still open — the token is the gate).
- Runtime data relocated to a configurable `data/` tree (uploads, logs, state,
  including the VAPID private key) instead of scattered script-dir / AppData paths.
- Frontend moved to `web/`.
- `requirements.txt` now lists all 7 real dependencies, version-pinned (previously
  4, with `pywebpush`, `Pillow`, `pillow-heif` missing).

### Notes
- Behavior of `/ask`, `/ask_async`, `/poll`, `/speak`, `/upload`, push, SMS, and
  work mode is unchanged — only the configuration and structure around them.
- The original personal rig (in the JARVIS vault) is untouched and still running.
