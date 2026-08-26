# Paywall verification — the paid path, walked end to end (2026-08-11)

**Internal doc. Not on the make_release allow-list — never ships.**

First end-to-end exercise of the money path since sell mode was armed in v0.9.62.
Run against the **published artifact**, not the repo: `adam-local-v0.9.62.zip`
downloaded from `DCom17/adam-releases`, extracted to a temp dir, served on
port 8011 with a throwaway token and an in-sandbox `settings.json`.

> **Why from an extract and never from the repo:** `licensing.py` resolves
> `LICENSE_KEY_FILE = Path("data/state/license.key")` and
> `TRIAL_FILE = Path("data/state/trial_start.txt")` **relative to CWD**, not to
> `ADAM_CONFIG_ROOT` or the configured `state_dir`. Running a paywall test from
> the repo root would read and overwrite the owner's own licence and trial clock.
> The standard sandbox recipe (temp config root + absolute data paths) is **not
> sufficient on its own** for licensing work. Verified after the run: the owner's
> `license.key` and `trial_start.txt` still carry their original 2026-07-21
> timestamps, untouched.

## Result: the paid path works. Every link verified.

| Step | Expected | Observed |
|---|---|---|
| Fresh install, `GET /license` | configured, entitled, 30-day trial starts | `configured:true entitled:true days_left:30 started:2026-08-11` ✅ |
| `GET /connect-info` in trial | 200 | 200 ✅ |
| `GET /phone-setup` in trial | 200 | 200 ✅ |
| Trial expired (`trial_start` → 2026-07-11) | unentitled | `entitled:false days_used:31 days_left:0` ✅ |
| `GET /connect-info` expired | 402 + buy link | 402, `buy_url` → site `#pricing` ✅ |
| `GET /phone-setup` expired | 402 + buy link | 402, same payload ✅ |
| Mint key (`issue_license.py --tier pro`) | signed `ADAM1-…` | minted ✅ |
| `POST /license` redeem | 200, valid, applies with no restart | 200, `valid:true tier:pro`, took effect immediately ✅ |
| Gated routes after redeem | 200 | 200 ✅ |
| Garbage key | 400, nothing stored | 400 "malformed key (expected payload.signature)" ✅ |
| Malformed base64 | 400 | 400 "malformed key encoding" ✅ |
| **Forged key, valid-looking payload** | 400, rejected on signature | 400 "signature does not match (key altered or not ours)", **no `license.key` written** ✅ |

Buyer-facing redemption surface exists and is coherent: the licence field lives in
`web/index.html` (gear → AI plan → License, `#licenseSection`, auto-shown once
`configured`), and the post-trial gate in `web/console.html:3357-3367` points the
user at exactly that path plus a "Get a license →" buy link.

**Conclusion: nothing blocks taking money.** Mint-and-email fulfilment
(`dist/GOLIVE-gumroad.md` §3) delivers a working unlock.

## One finding the owner should rule on — enforcement is thinner than the offer

`_require_entitlement()` (`routers/system.py:30`) is called from **exactly two
routes**: `/phone-setup` and `/connect-info`. Both are *setup wizard* endpoints.

Verified empirically with the trial expired **and** no licence installed —
i.e. a fully unentitled install:

```
GET  /              -> 200
GET  /manifest.json -> 200
GET  /ui-prefs      -> 200
POST /ask           -> 200   ("Pong.")
```

**The serving path is not gated at all.** So:

- A user who connects their phone during the 30-day trial keeps **fully working
  phone access forever**, without ever paying. Tailscale Serve stays configured;
  no request-path check ever asks about entitlement.
- What $24.99 actually gates today is **running the phone-setup wizard after day
  30**. Since almost every user will try phone access in week one, the gate will
  rarely fire for anyone who liked the product.

This may well be intentional — `licensing.py`'s own docstring calls it "an
honest-but-effective bar, not invasive DRM," and the enforcement comment says
"trial → feature-limit." A soft bar is a legitimate, on-brand choice for a
local-first zero-telemetry product, and hard-gating the request path would mean
the app policing its owner's own machine.

But there is a gap between *soft bar* and *the thing named on the price tag is
not enforced after setup*, and that gap is now live and taking money. **Owner
call, not a Claude call** — it touches the money model. The three options:

1. **Leave it.** Honest-bar posture; accept that trial-era setups are permanent.
   Cheapest, and arguably the most consistent with the product's ethics. If
   chosen, consider softening store copy from "unlocks phone access" toward
   "supports development / unlocks phone setup."
2. **Add a periodic entitlement check on the serving path** — e.g. non-localhost
   requests re-check `is_entitled()`. Enforces the stated offer, but starts
   policing the user's own machine and needs care not to strand a paying user
   offline (`is_entitled()` is deliberately fail-open today).
3. **Reframe the offer** so what is sold matches what is enforced.

Recommendation: **(1) plus the copy softening.** It is the only option that
changes no code on a build already in buyers' hands, and it keeps the product's
central promise — Adam does not surveil you — completely intact.

## Reproducing

```
# extract the PUBLISHED zip to a temp dir (never the repo — see the box above)
# write settings.json there: port 8011 + ABSOLUTE data paths inside the sandbox
$env:ADAM_CONFIG_ROOT=<sandbox>; $env:ADAM_TOKEN=<throwaway>
python -m uvicorn server:app --port 8011 --host 127.0.0.1     # cwd = sandbox
# auth header is  Authorization: Bearer <token>   (not X-Adam-Token)
# expire the trial: write an old date into <sandbox>/data/state/trial_start.txt
# tear down by PID from Get-NetTCPConnection -LocalPort 8011, then delete the dir
```
