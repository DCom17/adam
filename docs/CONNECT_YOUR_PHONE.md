# Connect Your Phone (supported mobile path: Tailscale)

This guide gets the Adam PWA — including **voice** — working on your
iPhone, talking to the backend on your own Windows PC. The supported v0.9 path is
**Tailscale + Tailscale Serve**, which gives your phone a real HTTPS address to your PC
without exposing anything to the public internet.

## Why HTTPS matters (read this first)

iPhone Safari only allows the **microphone**, **speech recognition**, and **Add to
Home Screen / PWA** features on a **secure context** — i.e. an `https://` page (or
`http://localhost` on the same device, which doesn't apply to a phone reaching your PC).

Plain `http://192.168.x.x` on the phone is **not** a secure context: the mic and voice
will silently do nothing, and the app will show an **insecure-connection banner**. That
is exactly why we use Tailscale Serve — it provides a valid HTTPS certificate for your
PC on your private network.

> Desktop-only? You don't need any of this. On the PC itself, open
> `http://localhost:8000` — `localhost` is already a secure context, so voice works.

## What you'll do

1. Install Tailscale on the PC and the iPhone.
2. Sign both into the **same** tailnet (same account).
3. Turn on **Tailscale Serve** so the backend is reachable at
   `https://<machine>.<tailnet>.ts.net`.
4. Open that HTTPS URL on the iPhone, sign in with your token, Add to Home Screen.
5. Verify voice.

### 1. Install Tailscale

- **Windows PC:** install from <https://tailscale.com/download/windows>, then sign in.
- **iPhone:** install Tailscale from the App Store, then sign in **with the same
  account**. (See Tailscale's official docs for the current install steps — they
  maintain the authoritative, up-to-date instructions.)

Confirm both devices appear in your tailnet (the Tailscale admin console lists them).

### 2. Start the Adam backend on the PC

```powershell
.\scripts\start-adam.ps1   # or just double-click the Adam desktop icon
python scripts\doctor.py     # should be all PASS / no FAIL
```

Note the port (default `8000`; check your `settings.json` / `/health`).

### 3. Enable Tailscale Serve (HTTPS) for the backend port

**Recommended — let the helper print the exact safe command for your machine:**

```powershell
python scripts\connect-phone.py     # or: .\scripts\connect-phone.ps1
```

By default the helper is **read-only** — it inspects your Tailscale + serve state and
prints the exact command, the resulting phone URL, an on-phone checklist, and the safe
teardown, without changing anything. Importantly, if another local app already serves on
the tailnet's `:443` (another app proxying a local port), the helper recommends
running Adam on a **separate HTTPS port (`:8443`)** so the existing serve is left intact.

**Prefer not to type the command?** Let the helper run it for you:

```powershell
python scripts\connect-phone.py --apply       # runs the serve command (asks to confirm)
python scripts\connect-phone.py --apply --yes  # ...without the prompt
python scripts\connect-phone.py --off          # stop sharing Adam again (port-scoped)
```

`--apply` runs **only** the recommended `tailscale serve …` and asks you to confirm first.
It will **refuse to overwrite** a port that's already in use, so it can never clobber
another serve (whatever app owns it), and it never runs `reset`, `funnel`, or `login`. `--off`
stops **only** the Adam serve on its port and leaves every other serve alone.

**Manual equivalent** (Tailscale Serve puts a valid-cert HTTPS front in front of your
local port, reachable only inside your tailnet — never Funnel, never public):

```powershell
# If :443 is free (use your install's port — default 8000):
tailscale serve --bg --https=443 http://127.0.0.1:8000
# If :443 is already used by another app, use a separate port:
tailscale serve --bg --https=8443 http://127.0.0.1:8000

tailscale serve status
```

`serve status` prints your HTTPS URL, e.g. `https://<machine>.<your-tailnet>.ts.net`
(or `…:8443` if you used 8443). The key outcome is an HTTPS URL that forwards to
your local app port. (Tailscale's docs cover Serve flags and any version differences.)

> ⚠ **Do not run `tailscale serve reset`** if another serve already exists — it removes
> **all** serve configs (it would drop the other app's serve on `:443`). To remove only the
> Adam serve later, use the port-scoped off:
> `tailscale serve --https=8443 off` (use the port you served Adam on).

### 4. Open it on the iPhone

- In **Safari**, go to `https://<machine>.<tailnet>.ts.net`.
- Confirm there is **no insecure-connection banner** (its absence means you're on a
  proper secure context).
- Tap the settings (gear), paste your **`ADAM_TOKEN`** (from your PC's `.env`), and
  save.
- **Add to Home Screen** (Share → Add to Home Screen) for the full app experience.

#### Faster: scan the QR codes from the Operator Console (no token typing)

Pasting a 48-character token on a phone is error-prone. The desktop **Operator
Console** can hand the phone the URL and the token via **two separate local QR codes**.
(They're split on purpose: iPhone's Camera grabs only the link out of a combined QR and
hides the token — so the token gets its own QR, which iOS then reads as plain text you
can copy.)

1. On the PC, open the console: `http://localhost:8000/console` and sign in with your
   token. (On the PC you can run `.\scripts\copy-token.ps1` to copy the token to your
   clipboard instead of opening `.env`.) Make sure the **Server** URL is your **Tailscale
   HTTPS URL** (e.g.
   `https://<machine>.<tailnet>.ts.net` or `…:8443`) — that's the address the phone
   needs from off-Wi-Fi, and it's what the URL QR will carry.
2. In the **Connect phone** panel, **Step 1 — Open Adam on phone**: click **Show URL
   QR** and scan it with the iPhone **Camera**; tap the link to open Adam on the phone.
3. **Step 2 — Copy access token**: click **Show token QR**, scan it with the **Camera**,
   then tap **Copy** (iOS shows it as plain text because this QR has no link in it).
4. **Step 3**: in the phone app's settings, **paste** the token, save, then test voice.
   (Use **Reveal token** on the console only if you ever need to read it on the PC.)
5. Click **Hide** on the console when you're done — it clears both QR codes.

> 🔒 **The token QR encodes your access token.** Both QRs are generated entirely in your
> browser — never uploaded, never saved as an image, never put in a link (`?token=`).
> Show them only to **your own** phone, don't screenshot or photograph them, and **Hide**
> when done.

The QR codes are only a convenience for handing over the URL and token. They do **not**
replace Tailscale: the phone still reaches your PC over the private Tailscale HTTPS URL,
which is what makes **voice** work off your home Wi-Fi.

### 5. Verify voice

- Open the app, tap **Activate**, and grant the **microphone** permission when asked.
- Speak; you should see a transcript and hear a spoken reply.

> **About the spoken voice:** which voice you hear depends on the **PC**, not the
> phone. If the real Adam voice is installed there (**`INSTALL-VOICE.cmd`**, one-time
> ~340 MB download), the phone speaks with the **same natural Adam voice as the
> desktop** — the audio is generated on your PC and delivered over this same private
> HTTPS connection. If it isn't installed, or its service isn't running, replies fall
> back to your phone's built-in (robotic) text-to-speech. (See
> [Beta handoff → About the voice](BETA_HANDOFF.md#about-the-voice).)

## Troubleshooting

- **The insecure-connection banner is showing** → you're on `http://` (e.g. a plain
  LAN IP), not the Tailscale HTTPS URL. Open the `https://<machine>.<tailnet>.ts.net`
  address instead. Voice/mic will not work until the banner is gone.
- **403 / "Forbidden"** → token problem. Re-paste the exact `ADAM_TOKEN` from the
  PC's `.env`; no extra spaces.
- **Can't reach the server / page won't load** → Tailscale isn't up on one device,
  they're on different tailnets, the backend isn't running, or `tailscale serve` isn't
  active. Check `tailscale status` and `serve status` on the PC, and that the
  black Adam window is open (double-click the Adam desktop icon if not).
- **Mic does nothing / no voice** → almost always an insecure context (see the banner
  note) or a denied microphone permission. Use the HTTPS URL and allow the mic in
  Safari settings for that site.
- **Reaching it over `http://<pc-ip>` works for text but not voice** → expected. Text
  posts fine over http; voice needs the HTTPS (Tailscale Serve) origin.
- **Replies sound robotic on the phone** → the real Adam voice isn't installed on the
  PC (run `INSTALL-VOICE.cmd` there), or its service was down for that reply. The
  server restarts the voice service automatically when it notices it's down, so the
  next reply usually comes back in the real voice; if it never does, restart Adam.

For a public-internet option (advanced, not the supported path), see
[`ADVANCED_REMOTE.md`](./ADVANCED_REMOTE.md). For what's supported overall, see
[`SUPPORT.md`](./SUPPORT.md).
