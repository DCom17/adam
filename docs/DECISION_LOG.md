# Decision log (internal — never ships in a release)

Records deliberate product decisions so scope is closed on paper, not by omission.
Entries D1–D18 live in the pre-rename git history
(`Jarvis-Voice-personal-archive-2026-07-03/old-git-history.git`); this file continues
the numbering. D18 (retiring the console's strictly-read-only posture for the v0.8.2
action layer) is the last archived entry and is still referenced from
`CONSUMER_TEST_CHECKLIST.md`.

---

## D19 — Tailscale install/sign-in stays manual (permanent, not deferred) — 2026-07-05

**Decision:** Adam will NOT automate installing Tailscale or driving its account
sign-in. The guided path (in-console Connect-phone wizard + `connect-phone` helper +
doctor checks + docs) is the final design, not a v1.0 stopgap. The "automated
connect-phone wizard" item deferred by the old checklist is hereby closed, removed
from every future roadmap.

**Why:** Automating a third-party installer and OAuth flow rots silently whenever
Tailscale changes their installer, login page, or consent screens — and the breakage
is ours to field but not ours to fix. Driving a user through creating an account on a
service they don't understand also undermines informed consent about their network.
Tailscale's own onboarding is good; the honest division of labor is "we tell you
exactly what to do and verify you did it," which the helper + doctor already deliver.
Phone access is optional — v1.0 delivers full desktop value without it.

## D20 — Visible console window (no tray, no auto-start) is accepted for v1.0 — 2026-07-05

**Decision:** v1.0 ships with the server running in the visible "Adam" console window
— no tray app, no auto-start, no hidden service. "Minimize it, don't close it" stays
the documented contract.

**Why:** "Nothing runs hidden" is part of the product's trust story, in the same
family as `draft_only` and the audit log: for a tool that reads your files and runs an
AI agent against them, a visible process is a feature. A tray app with auto-start is a
whole new surface (pythonw quirks, crash recovery, startup registry entries,
"is-it-running?" ambiguity) and partially contradicts that posture.

**Falsifiable by the beta:** the known failure mode is a user closing the window (or
rebooting) and reading the result as "Adam is broken," especially from the phone. If
the 2–3 cold testers hit this, the v1.x remedy is the cheap fixes first — the PWA's
can't-reach-server error should say "the Adam window on your PC may be closed — reopen
it from the desktop icon," plus possibly a close-confirmation on the console window —
NOT a tray app. A tray app is only on the table if testers prove the window is a real
adoption blocker even after those fixes.
