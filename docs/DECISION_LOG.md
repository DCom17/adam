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

## D21 — The name stays "Adam", with four standing guardrails — 2026-07-05

**Decision (owner's, after a USPTO knockout search run live with the assistant):**
the product keeps the name **Adam**. Evidence file: `docs/NAME_DECISION_EVIDENCE.md`
(internal, never ships — full search results + the adam.ai Office-action findings).

**The situation:** ADAM.AI INC. (funded meeting-AI SaaS) has "ADAM.AI" REGISTERED
(IC 042, since ~2019) and filed for standalone **"ADAM"** (IC 009+042) on 2026-02-04
(serial 99633783). Its first Office action (2026-06-02) refused on a FORMALITY only
("iPad" wording in the goods ID) and stated **"no conflicting marks found"** — i.e.
the USPTO itself treats the dozen coexisting live ADAM marks in class 9 as
non-conflicting because each one's goods differ. Their goods, even as amended, stay
scoped to "governance and business management solutions, namely organizing and
managing workplace meetings." A personal, local-first voice assistant is a different
product for a different buyer, in the crowded-field environment where narrow ADAMs
demonstrably coexist.

**The four guardrails (standing constraints — violating any one reopens this
decision):**
1. **Never touch their lane.** Adam is always described as a "personal, local-first
   voice assistant for your own files" — never meeting, governance, or
   business-management software, in any doc, listing, or marketing surface.
2. **Compound distribution identity.** "Adam" is the assistant's name; **"Adam
   Local" / `adam-local`** is the product identity anywhere registrable or public
   (ZIP name, app id, winget ID, domains) — already true internally.
3. **Monitor serial 99633783.** Calendar checks set for 2026-09-03 and 2026-12-03
   (owner's Google Calendar): response filed? goods still meeting-scoped? published?
   Broadening toward personal assistants = reassess.
4. **Attorney clearance (~$500–1.5k) before monetizing or a major marketing push.**
   Take the evidence file. Not doing this before charging money reopens the decision.

**Accepted residual risk:** their standalone-ADAM will likely register; a
cease-and-desist is never impossible from a funded company. The evidence file is the
day-one defense record. This entry is not legal advice.

**D21 addendum — "Atam" alternative considered and REJECTED, 2026-07-05.** Proposal:
respell as "Atam" (backronym: Agentic Thought and Action Manager) to gain distance
from adam.ai's marks. Rejected on two independent grounds: (1) **zero phonetic
distance** — American English flaps intervocalic /t/ to the same sound as /d/
(latter/ladder), so "Atam" is pronounced identically to "Adam"; sound similarity is
a core likelihood-of-confusion factor, so the respelling buys no legal room against
an ADAM mark while costing a full rename. (2) **Direct collision with a registered
mark in our own field:** ATAM(R) — Architecture Tradeoff Analysis Method — is a
USPTO-registered trademark of Carnegie Mellon University (SEI), famous specifically
among software practitioners (exactly the beta audience), and CMU actively licenses
its marks. "Atam" is therefore strictly worse than "Adam": same sound as the mark we
were avoiding, plus a new head-on conflict with a university's registered mark.
D21 and its four guardrails stand unchanged.
