# Accessibility — Adam

Target: **WCAG 2.2 Level AA**. Scope: this repo only (`site/` public pages +
`web/` app UI). Morrow gets its own separate pass — do not fold it in here.

Written 2026-07-27.

## Why 2.2 AA

There is no DOJ technical rule and no compliance deadline for a private
business under ADA Title III. What exists is litigation risk, and courts,
consent decrees and demand letters have settled on **WCAG 2.1 AA** as the de
facto standard. WCAG 2.2 is backward-compatible, so building to 2.2 AA
satisfies 2.1 AA at no extra cost and covers the criteria the next round of
enforcement will cite (focus appearance, target size, dragging movements).

Exposure today is low — a download page with no checkout is a thin target. It
rises the day the **Paddle checkout goes live**, because a transactional
storefront is the profile serial plaintiffs actually file against. That is the
real deadline, not a regulatory one.

**EU / EAA:** enforced since 2025-06-28 and it reaches US sellers serving EU
consumers. Campos Systems LLC qualifies for the **microenterprise exemption**
(<10 employees AND <€2M turnover), which covers services including the
e-commerce surface. Documentation item, not a build item — it stops being one
past those thresholds.

**Do not buy an accessibility overlay.** accessiBe took a $1M FTC fine, UserWay
is facing a class action from a customer who was sued anyway, and overlay-
equipped sites are now over-represented in new filings. Overlays cannot fix any
structural defect, which is what all of the findings below are.

## Audit findings (2026-07-27)

Baseline was better than typical: every page has `lang="en"`, the app uses real
`<button>` elements with zero div-onclick fakes, all `<img>` carry `alt`, the
Help Hub is a proper `role="dialog" aria-modal` with a focus trap, and the
landing page honors `prefers-reduced-motion` in both CSS and JS.

| # | Finding | Criterion | Phase |
|---|---|---|---|
| 1 | Orb (`#orb`) was a `<div>` with only `title` — the app's primary control, unreachable by keyboard | 2.1.1 Keyboard (A) | 1 ✅ |
| 2 | Zero `aria-live` regions anywhere — state, errors and streaming replies all silent | 4.1.3 (AA), 3.3.1 (A) | 1 ✅ |
| 3 | `#composerInput` had a placeholder but no label | 3.3.2 (A), 4.1.2 (A) | 1 ✅ |
| 4 | No focus styling anywhere (`focus-visible` appeared 0 times in 20 files) | 2.4.7 (AA), 2.4.11 (AA) | 1 ✅ |
| 5 | `web/index.html` had no `<h1>` — only page of 20 without one | 1.3.1 (A) | 1 ✅ |
| 6 | In-text links distinguished by color alone — cyan `#21e6ff` vs body `#8fb9c6` computes to **1.39:1**, against the 3:1 required | 1.4.1 (A) | 2 |
| 7 | Nav/footer links ~16px tall against the 24×24 minimum | 2.5.8 (AA) | 2 |
| 8 | `#motes` canvas not `aria-hidden` (orb canvas is) | 1.1.1 (A) | 2 |
| 9 | `.eyebrow` contains the literal string `A D A M`, spelled out letter-by-letter by screen readers | 1.3.1 (A) | 2 |
| 10 | Logo `alt="Adam logo"` duplicates the adjacent wordmark; should be `alt=""` | 1.1.1 (A) | 2 |
| 11 | `<footer>` nested inside `<main>`, so it is not a `contentinfo` landmark | 1.3.1 (A) | 2 |
| 12 | No skip-to-content link | 2.4.1 (A) | 2 |
| 13 | `settings.html`, `health.html`, `finance.html` have no ARIA at all; `console.html` has 5 `<label>` with 0 `for=` | 1.3.1, 3.3.2 | 3 |

Text contrast passes: `--faint` on `--bg-soft` computes to 4.94:1.

## Phase 1 — Level A blockers (DONE 2026-07-27)

All in `web/index.html` unless noted.

- **Orb is a real button.** `<div class="orb">` → `<button type="button" class="orb"
  aria-label="Talk to Adam">`, inner canvas `aria-hidden="true"`. Keyboard
  reachability and Enter/Space activation come free from the element; the
  existing `orb.onclick` handler is unchanged. `button.orb` CSS strips UA chrome
  so it paints identically to the div it replaced.
- **Live regions.** `#label` → `role="status"` (announces Standby → Listening →
  Thinking → Speaking). `#err` → `role="alert"`. `#transcript` →
  `role="log" aria-live="polite" aria-relevant="additions"`.
- **Transcript mute guard.** `loadActiveSession()` replaces `transcript.innerHTML`
  wholesale on every session switch, which would make a live region read the
  entire history aloud. It now sets `aria-live="off"` for that paint and re-arms
  on the next frame. **Keep this guard if that function is ever refactored.**
- **Composer labelled** with a visually-hidden `<label for="composerInput">`.
- **Global focus ring.** `:focus-visible { outline: 2px solid var(--cyan)
  !important }`. The `!important` is deliberate: several older rules across three
  separate `<style>` blocks set `outline: none`, and a keyboard focus indicator
  must never be silently removed.
- **`<h1 class="vh">Adam</h1>`** added, plus the `.vh` visually-hidden utility.
- `web/sw.js` cache bumped v13 → v14.

Verified: 74/74 `python -m pytest` green; served HTML from the live :8000 server
carries every new attribute; HTML parser reports zero unclosed tags.
**Not yet verified: keyboard traversal and focus-ring appearance in a real
browser** — the Chrome extension was not connected during this session.

## Phase 2 — public site (`site/`)

Underline in-text prose links (nav/footer chrome can stay bare — those read as
navigation by position). Pad nav/footer anchors to a 24px minimum. Skip-to-
content link. `aria-hidden` on `#motes`. Move `<footer>` out of `<main>`. Fix
the `A D A M` eyebrow with visually-hidden text or `aria-label`. Logo `alt=""`.
Applies to `index.html`, `privacy.html`, `terms.html`, `refund.html`,
`eula.html` — the four legal pages share one stylesheet, so the link and focus
fixes land once.

## Phase 3 — remaining app surfaces

`settings.html`, `health.html`, `finance.html`, `console.html`, and the seven
`setup-*.html` pages. Same focus/target-size sweep. Confirm by eye whether
`console.html`'s five `<label>` elements wrap their inputs or are orphaned —
grep could not tell.

## Phase 4 — prove and document

1. axe-core + Lighthouse on every page.
2. Manual keyboard-only traversal of a full conversation (activate → speak →
   interrupt → switch session → open Help Hub → escape).
3. One NVDA pass. NVDA is free and is what Windows users file complaints from.
4. Publish `site/accessibility.html`: conformance target, known gaps, contact
   address. Not immunity, but it demonstrably deflects demand letters.
5. VPAT/ACR only if government or enterprise buyers ever become a lane.
