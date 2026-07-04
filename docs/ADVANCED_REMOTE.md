# Advanced: Public Remote Access (cloudflared + Cloudflare Access)

> **Advanced / optional / limited support.** The **supported** mobile path is
> Tailscale — see [`CONNECT_YOUR_PHONE.md`](./CONNECT_YOUR_PHONE.md). Use this only if
> you specifically need a public HTTPS URL (e.g. access over cellular with no app on
> the phone) and you understand and accept the responsibility below.

## What this is

A **cloudflared named tunnel** bound to a domain you own gives the backend a public
`https://jarvis.yourdomain.com` address. It works anywhere, but it puts an endpoint on
the public internet — so it must be locked down.

## Hard requirement: Cloudflare Access

If you expose the backend publicly, you **must** put **Cloudflare Access** (SSO / OTP /
email allow-list) in front of it.

- A public URL protected by **only the bearer token is NOT acceptable.** The token is a
  single shared secret with no per-device identity, no rotation, and no MFA; a public
  endpoint guarded by it alone is a standing risk.
- Cloudflare Access authenticates the **person** before any request reaches Jarvis. The
  Jarvis token then remains as a second factor behind it.

## Your responsibilities (you own the exposure)

By choosing this path you take on:

- Buying/owning the **domain** and configuring **Cloudflare** (account, tunnel, DNS,
  and **Access** policies).
- Keeping the tunnel daemon and Access policy correct and current.
- The security consequences of a public endpoint. This is **limited support** — the
  supported, documented path is Tailscale.

## Boundaries (unchanged by this path)

- Do **not** route anything through the maintainer's Claude account — you run the
  backend on your machine with **your own** Claude Code credentials.
- Do **not** modify the maintainer's personal rig (`scripts/voice_server`); it is not
  part of this product.
- `agent_safety.mode` stays `draft_only`, the server stays the sole writer, and file
  changes still go through review → approval → apply, exactly as on the local path.
- Tighten **CORS**: with a public origin, set `cors_allowed_origins` in `settings.json`
  to your exact public origin instead of the `*` default. The setup doctor will WARN if
  CORS is wide-open while a public base URL is configured.

## Sanity checks

- `python scripts/doctor.py` should reflect your configuration (it warns on a non-HTTPS
  public base URL and on `*` CORS with a public base URL).
- Confirm Cloudflare Access actually challenges you in a fresh browser **before** the
  Jarvis token prompt appears. If you can reach the token screen without an Access
  challenge, Access is not enforced — stop and fix it.

If you don't have a strong reason for a public URL, use Tailscale instead — it gives you
the same HTTPS-for-voice without putting anything on the public internet.
