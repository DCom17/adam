# SPEC — Two modes: Normal + Operator (terminal-parity Claude Code)

Status: **APPROVED (owner, 2026-10-02) — D23** · built + verified on a sandbox (see "Verification") · not yet released.

## Owner decisions (2026-10-02)

- **Three modes → two.** **Normal** (today's voice mode, capability tiers unchanged) and
  **Operator** (today's Claude Code mode, made terminal-equivalent). Today's middle "Operator"
  (`work`, restricted + extra dirs) is retired.
- **Operator = full power**, shipped to all users (not owner-only).
- **Phone access is acceptable as-is** for Operator for now (single bearer token over the
  user's own tailnet; Funnel stays forbidden).
- Must-haves, all five: **slash commands · Claude asks me mid-task · I steer mid-task ·
  context carries over between modes · I see the full output.**

## Why today's code mode can't do it

Every code-mode message is a separate one-shot `claude -p --resume` run. A one-shot run has
no channel back to the user (so no questions, no plan approval), no way to accept input
while working (no steering), and its output is reduced to a status line. Switching into code
mode changes the working folder (agent workspace → vault), and Claude Code stores
transcripts per folder, so the resume misses and the chat starts fresh.

## Architecture: one live Claude Code session per Operator chat

Spawn `claude` once per Operator chat and keep it alive, talking the CLI's own host protocol
(the one the Agent SDK uses) over stdin/stdout JSON lines:

```
claude --input-format stream-json --output-format stream-json --verbose
       --permission-prompt-tool stdio --permission-mode bypassPermissions
       [--resume <sid>] [--model ...] [--add-dir ...] --append-system-prompt-file <f>
```

- **No new Python dependency.** The protocol is plain JSON lines; Adam speaks it directly in
  a new `operator_session.py`. (The SDK pulls ~20 packages incl. pywin32 and the updater
  never installs new requirements, so an SDK dependency would break every updated install.)
- The existing job model stays: `/ask_async` → job → `/poll`. An Operator job ends at the
  CLI's `result` event; the **process stays up** for the next message.
- Idle Operator processes are reaped after `operator_idle_minutes` (default 30); the next
  message respawns with `--resume`. A server restart loses the process, never the transcript.
- Concurrency cap `operator_max_sessions` (default 4) — the least-recently-used idle one is
  reaped to make room.
- `untrusted` input (inbound SMS / voicemail) can never reach an Operator session.

### Verified against the installed CLI (2.1.288), 2026-10-02 — scratch probes, not assumptions

| Capability | Result |
|---|---|
| Built-in slash commands typed as a message (`/model`, `/usage`, `/cost`, `/compact`, `/clear`) | ✅ real output; `/clear` returns a **new session id** |
| Custom commands + skills | ✅ listed in the `initialize` response (`commands`) |
| `AskUserQuestion` under `bypassPermissions` | ✅ arrives as `control_request can_use_tool`; answer via `updatedInput.answers` |
| `ExitPlanMode` (plan approval) under `bypassPermissions` | ✅ also arrives at the host |
| Message sent mid-turn (steer) | ✅ picked up at the next tool boundary, same turn ("I see you've changed plan mid-turn") |
| Resume a session from a different cwd | ❌ empty result — **unless** the transcript `.jsonl` is copied into the target cwd's project dir first, then ✅ full context |

## The five features

1. **Slash commands.** Text starting with `/` is sent through verbatim. The `initialize`
   response's command list feeds an autocomplete menu in the composer. Commands that are
   interactive TUI screens (`/config`, `/mcp` UI, …) return whatever text the CLI gives; the
   menu marks them as terminal-only. A `result.session_id` change (`/clear`) is adopted by
   the chat like any session id.
2. **Claude asks mid-task.** `can_use_tool` for `AskUserQuestion` / `ExitPlanMode` parks the
   question on the job; `/poll` returns `ask: {id, kind, questions|plan}`. The app shows a
   card (options as buttons, "Other" free text; plan = Approve / Keep planning). Answer =
   `POST /jobs/{id}/answer`. Unanswered asks time out after `operator_ask_timeout_minutes`
   (default 30) as a deny with "user didn't answer". Questions also fire a push notification
   when the app is backgrounded. Every other tool is allowed (full power).
3. **Steer mid-task.** While a job runs, the composer stays live: a send becomes
   `POST /jobs/{id}/steer` → a user message written to the live session. **Stop** becomes the
   CLI's `interrupt` (session survives, context kept); hard kill stays as the fallback.
4. **Context carries over.** A deliberate mode switch keeps the chat's session id. Before
   resuming in the other mode's folder, the server copies the transcript into that folder's
   project dir (`~/.claude/projects/<munged cwd>/<sid>.jsonl`). The stale-phone mode-authority
   guard stays for non-deliberate mismatches; the client marks deliberate switches.
5. **Full output.** Each Operator job keeps an event log (assistant text, every tool call
   with its full input, tool results capped at 64 KB each, questions/answers, steers).
   `/poll?since=N` returns new events; the chat renders them as an expandable live
   transcript (tool cards collapsed by default, tap to expand). The log lives with the job
   history (same retention, `job_history_ttl_days`).

## Modes in the app

- Mode button: **Normal ⇄ Operator** (tap). The amber "hot" styling + badge stay for Operator.
- Voice: "switch to operator mode" / "back to normal" → `<<SET_MODE>>`, same as today.
- Legacy chats: `work` → Normal; `code` → Operator.
- First switch to Operator on an install shows a one-time consent sheet (what full power
  means: real edits, real shell, no approval step). Stored server-side so it covers phone + PC.
- Settings: `agent_safety.allow_code_mode` default flips to **true**; setting it `false`
  still hides Operator entirely (403 server-side, unchanged).

## Build order

1. `operator_session.py` (process manager + protocol + event log) + `run_claude` routing for
   `mode == "code"` + `/jobs/{id}/answer` · `/jobs/{id}/steer` · interrupt stop · `/poll` events
   + transcript migration. Unit tests with a fake CLI; live probe on the sandbox instance.
2. PWA: ask cards, live composer (steer), full-output transcript, slash autocomplete,
   two-state mode button, consent sheet, keep-sid on switch.
3. Retire `work`: normalize, help hub + capability note + CHANGELOG, test sweep, release.

## As built (deltas from the plan above)

- Off switch is a NEW key, `agent_safety.operator_mode` (default `true`); the retired
  `allow_code_mode` is ignored on purpose — every install copied it as `false` from the old
  template (D23).
- The live console (`#opLive`) and full-output sheet (`#opSheet`) sit OUTSIDE `#transcript`:
  chat history is synced across devices, and tool output (up to 64 KB per result) must never
  ride in it. A finished turn leaves one `.opsum` line that reopens the transcript from
  `/jobs/{id}/events`.
- A deliberate switch closes the chat's live session, so the next Operator turn resumes from
  the transcript on disk (which includes any Normal turns in between).
- Every new client behavior is gated on `operator_live` from `/ui-prefs`; against an older
  server the old three-mode UI runs unchanged. With `operator_mode: false` the mode button is
  hidden (there is only Normal).
- **Reattach:** `GET /operator/running` lists turns in flight (from acceptance, with chat key +
  open question). The app re-attaches on load, on return to the foreground, and on a question
  notification tap — iOS reloads suspended PWAs, which used to orphan a waiting question.
- **Question push:** when a question/plan arrives and the app isn't on screen, a `kind:"ask"`
  push (own tag `adam-ask`, never replayed as a reply) opens the app on that chat.
- A second message to a busy Operator chat gets an immediate 409 at `/ask_async`; Operator
  turns never clear the chat's resume id on failure (the server recovers stale sessions).
- Consent is a styled sheet (focus-trapped, Esc = not now), stored server-side.

## Verification (2026-10-02)

- `python -m pytest` → **200 passed** (incl. `tests/test_operator_session.py`, 15 cases against
  `tests/fake_claude_operator.py`, a real subprocess speaking the protocol).
- Sandbox instance (:8011, temp `ADAM_CONFIG_ROOT`, throwaway vault, Haiku), **real CLI**:
  HTTP end-to-end **17/17** (carry-over both ways, `/model`, question card round-trip, steer,
  interrupt + continue, full output); headless-Chromium UI pass **22/22** (tap to Operator,
  consent, live console, question card by tap, summary + sheet, steer from composer, slash
  menu + Tab, back to Normal with context).
- Round 2 (2026-10-03): `pytest` + 2 new cases; API e2e **23/23** (adds two Operator chats at
  once + busy-chat 409); headless UI **39/39** (consent sheet, reload mid-question → reattach,
  STOP with a question open, switch chats mid-turn, console header/✓, slash, sheet, desktop
  1440px); Operator-off sandbox: button hidden, Normal works, `/ask` code → 403.
- NOT verifiable from here: the owner's real iPhone (PWA install, push tap, iOS reload).

## Open / later

- **Voice answers + voice steering.** Questions and steers are typed or tapped today; the mic
  is off while a turn runs.
- Per-chat working folder ("cd into a repo").
- Image/file attachments into a live session (today's attachment block is reused as text).
- Streaming partial text (`--include-partial-messages`) for a typing effect in the console.

