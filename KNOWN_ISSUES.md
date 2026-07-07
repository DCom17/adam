# Known Issues — bug ledger

Each entry: what was reported, the real cause, the fix, and how it was verified.
Newest first. A repeat of an already-listed symptom means the real bug is
upstream of both fixes — say so rather than adding a parallel entry.

---

## 2026-07-07 — Voice cuts off after ~2 sentences on the first reply (FIXED v0.9.41)

- **Reported:** "When you return a longer response you'll say two sentences and
  then stop — but Replay says the full response like you should have." First
  reply only, longer replies only, orb freezes ~1s. Seen on desktop.
- **Root cause:** `speak()` renders the reply sentence-by-sentence (Kokoro) and
  bails its scheduling loop the instant `speakGen` is bumped (`web/index.html`,
  the `if (myGen !== speakGen) return;` guard). The mid-render watchdog already
  refused to settle during rendering, but the **two automatic settle paths never
  got the same guard**: the 1s ctx-stall backstop and `recoverForeground`
  (focus/pageshow/visibilitychange). On the first reply after idle the cold
  Kokoro model lags on later sentences; during that render gap either path
  force-settled, bumped `speakGen`, and the loop bailed after whatever was
  already scheduled. Replay works because the model is warm — no gap, nothing
  trips.
- **Fix:** Added a gen-keyed `speakRenderGen` flag, armed only while `speak()`
  is actively scheduling this readout's clips and disarmed on every exit/abort
  path. The ctx-stall backstop now treats a frozen clock as a cold render gap
  (not a wedge) while it's armed; `recoverForeground` only force-settles on an
  actual background transition (`bgInterrupted`). User-initiated cuts (barge-in,
  chat-switch, Stop) are untouched.
- **Verify:** Owner retest — first long reply after the app sits idle a minute
  should read to the end. Static HTML change: hard-refresh, no restart.

## 2026-07-07 — First reply returns "Connection error, sir.", retry works (FIXED v0.9.41)

- **Reported:** "I reply and it says connection error sir; I reply again and it
  goes through. The initial reply doesn't work."
- **Root cause:** A turn resumes its chat with `--resume <session_id>`
  (`server.py run_claude`). When that session no longer exists in the current
  working directory — it aged out of the CLI's store, or was created in a
  different workspace — the CLI exits non-zero with "No conversation found…",
  and the server surfaced it as a generic `502 Claude failed`, which the client
  speaks as "Connection error, sir." The retry only worked because the client's
  `fail()` clears the chat's resume id, so the *second* turn started fresh.
  (Related to the mode-desync bug below — a cross-workspace resume is one way to
  reach this — but distinct: that one silently wipes context, this one hard-errors.)
- **Fix:** `run_claude` now detects the session-not-found error class
  (`_is_session_not_found`) and, when a resume was attempted, transparently
  re-runs the turn **once** as a fresh session (`session_id=None`, which can't
  loop) instead of raising. The user's first reply lands as a fresh conversation
  rather than erroring. Scope: the voice/work (non-code) path plus a defensive
  twin on the exit-0 error path. Code-mode's streamed path keeps today's
  behavior (its resume ids are cleared on mode-cross, so it rarely resumes a
  gone session).
- **Verify:** Send a turn carrying a bogus/expired `session_id`; server log
  shows "resume … not found — retrying once as a fresh session" and a normal
  reply comes back (no 502). Needs the server restarted onto v0.9.41.

## 2026-07-07 — Phone/computer mode desync wipes chat context (FIXED v0.9.41)

- **Reported:** Computer was in code mode; phone still showed regular (voice).
  Replying from the phone answered in regular and cleared the conversation's
  context.
- **Root cause:** Mode is per-chat and syncs across devices, but with lag. Modes
  map to working directories (`code` = vault; voice/work = agent sandbox), and a
  Claude session only exists in the cwd it was born in. A stale device replying
  in the wrong mode made the server `--resume` a session from the wrong cwd →
  silent fresh session → context wiped, answered in the wrong mode.
- **Fix (server-authoritative mode):** New server-owned `session_modes` table
  (`session_store.py`) records the mode/cwd each session actually lives under.
  On resume, if an incoming turn's mode would run the session from a different
  cwd bucket (`_cwd_bucket`), the server honors the session's **origin** mode
  instead of blindly resuming into the wrong project. The effective mode rides
  back in the reply (`job.mode`); the client adopts it and repaints its toggle,
  and clears the resume id when a chat deliberately crosses the code↔safe
  boundary so that toggle starts a clean session by design.
- **Verify:** Owner retest across two devices — flip mode on one, reply from the
  stale other; context should survive and both toggles converge. Needs the
  server restarted onto v0.9.41.
