# Blaze Dashboard Auth — Scoping Doc (future project, not started)

Status: scoped only, no code written. Not scheduled for this session or the
current branch. Written so a future session can pick this up cold without
re-deriving the analysis.

## Getting started next session

1. Verify the `/oauth/blaze/callback` (registered with Blaze) vs
   `/auth/blaze/callback` (in app.py) path discrepancy — see "Blocker —
   RESOLVED" below.
2. Add the dashboard redirect URI in Blaze's OAuth app config (5 slots
   available, 1 currently used).
3. Build the separate OAuth flow (`/auth/dashboard/login` +
   `/auth/dashboard/callback`, minimal `users.read` scope, distinct PKCE
   cookie names).
4. Build the allowlist, with **a real Blaze account as the first approved
   user** (see "First user to approve" below).
5. Build the dual-mode gate (Basic Auth AND Blaze-auth both work); verify
   the full login round-trip from a second browser before ever dropping
   Basic Auth.

## First user to approve

A real Blaze account belonging to a person already waiting for dashboard
access is the test case for the allowlist once it's built. Use them to
verify the end-to-end flow (login, allowlist match, dashboard access granted) before
onboarding anyone else.

## Goal

Replace the shared Basic Auth password on the Studio dashboard with
per-person login via Blaze OAuth: approved people sign in with their own
Blaze account ("Sign in with Blaze"), instead of everyone sharing one
STUDIO_ADMIN_USER/STUDIO_ADMIN_PASSWORD.

Blaze OAuth proves *identity*. It does not prove *permission* — a new
allowlist is required on top of it (see below).

## Why the existing OAuth flow can't be reused

`/auth/blaze/login` and `/auth/blaze/callback` (app.py) are single-tenant:
built to fetch and store one shared bot-posting credential
(`blaze_oauth_tokens.json`), not to log in arbitrary dashboard visitors.

- `_foxbot_blaze_oauth_verify_identity_v1` (app.py:18720) actively **rejects**
  a login from any Blaze account other than the one already saved once
  `BLAZE_BOT_USER_ID` is set. A login flow needs the opposite: many
  different Blaze identities must be able to complete it.
- Scopes requested (`users.read, offline.access, channel.moderate,
  users.bot`) are bot-posting scopes. A login-only flow only needs
  `users.read` — enough to read the authenticated user's identity once.
  No `offline.access` (no refresh token to keep), no moderate/bot scopes.
- No session-cookie concept exists in that flow — it runs once and writes a
  token file, not something meant to be hit repeatedly by different
  visitors.

**Conclusion:** build a separate, parallel OAuth flow:
- New routes: `/auth/dashboard/login`, `/auth/dashboard/callback`.
- Minimal scope: `users.read` only.
- Distinct PKCE state cookie names — the existing flow uses
  `foxbot_oauth_state` / `foxbot_oauth_verifier` / `foxbot_oauth_redirect`;
  reusing those names would let a concurrent bot re-auth and a dashboard
  login stomp each other in the same browser.
- The existing HTTP helpers (`_foxbot_blaze_oauth_post_json_v1`, the
  generate-auth-url / token-exchange calls) are generic and can be reused
  as-is.
- Decide at build time whether the dashboard ever needs to act as the
  logged-in user (call Blaze API on their behalf) or only confirm identity
  once at login. If it's identity-only, discard the per-user access/refresh
  token immediately after the one profile-verification call — nothing
  long-lived to store or refresh per user, smaller attack surface.

## Allowlist — needs to be new

Nothing existing is usable:

- `ADMIN_USERNAMES` / `is_admin()` (app.py:3622) matches a **self-reported
  chat username string**, case-folded, with no identity verification behind
  it. Built for in-chat command permission checks, not web auth.
- Connected-creators registry is keyed by a client-supplied handle via the
  public self-registration endpoint (`!connect` /
  `/api/connected-creators/connect`), has no Blaze user ID, and must stay
  public/unverified per existing code comments. Using it as an allowlist
  would mean anyone who types `!connect` grants themselves dashboard access.

**New allowlist must key on Blaze USER ID, not handle** (handles can change,
IDs can't — same reasoning as the bot's own `BLAZE_BOT_USER_ID`
identity-lock).

Two viable storage shapes, decide at build time:
- **Env var** (e.g. `STUDIO_APPROVED_BLAZE_USER_IDS`, comma-separated) —
  matches the existing `ADMIN_USERNAMES` pattern, simplest to build, but
  adding someone means editing Render env vars + a restart.
- **State-backed JSON file** (same pattern as `connected_creators.json` /
  `blaze_oauth_tokens.json`, mirrored to Postgres via `storage_paths.py`),
  with a small admin-only endpoint to add/remove entries. More code, but
  editable live without a redeploy.

Whatever endpoint adds to this list must itself be gated at least as
strictly as the dashboard — an unprotected self-approve endpoint here would
be the same class of bug as the foxcoins hole fixed in `6a7429a`.

## Rollout — dual-mode gate, never a hard cutover

Run Basic Auth and Blaze-auth in parallel during rollout. A request passes
if *either* valid Basic Auth *or* (valid Blaze session **and** session's
Blaze user ID is on the allowlist) succeeds.

- Add a mode switch, e.g. `STUDIO_AUTH_MODE=basic|blaze|both`, defaulting to
  `both` during rollout.
- Confirm the owner's own Blaze account is on the allowlist and the full
  login round-trip works from a **second browser/incognito session** while
  Basic Auth still guards the real dashboard, before dropping Basic Auth.
- Only switch to `blaze`-only once verified solid, as a deliberate follow-up
  change — not bundled into the same commit that builds the flow.
- Gated API calls (studio-v2's `fetch()` calls) currently rely on the
  browser auto-sending cached Basic Auth credentials. A session cookie
  replaces that transparently for same-origin fetches, but the gate's 401
  response for page loads (redirect to login) vs API calls (JSON 401, no
  redirect) needs to branch on something like `Accept`/`X-Requested-With` so
  a fetch() 401 doesn't try to navigate the whole page.

## Key risks

- **Lockout** — mitigated by the dual-mode gate above; don't skip that step.
- **Forged session** — the session cookie must be HMAC-signed with a
  server-side secret (new `SESSION_SECRET` / `STUDIO_SESSION_SECRET` env
  var), not a plain JSON blob a client could edit to claim any Blaze user
  ID. No signing infra exists yet (`itsdangerous` / Starlette
  `SessionMiddleware` are not in `requirements.txt` or used in `app.py`) —
  use stdlib `hmac`/`hashlib`, no new dependency needed.
- **Self-approve hole** — the allowlist-management endpoint must be gated
  at least as strictly as the dashboard itself (chicken-and-egg: use Basic
  Auth to manage the allowlist while both modes coexist).
- **Stale authorization** — if someone's removed from the allowlist, their
  existing session cookie shouldn't keep working indefinitely. Needs a
  short session TTL (12-24h) rather than a long-lived or non-expiring
  cookie.
- **Cookie-name collisions** with the bot's existing OAuth PKCE cookies if
  not distinctly named (see above).

## Blocker — RESOLVED

Confirmed on Blaze's developer console: Blaze supports up to **5 OAuth
redirect URIs** per client ID, with an Add button. Currently only 1 slot is
used (`https://foxbot-ai-chatbot.onrender.com/oauth/blaze/callback`), so
there's room to register the dashboard callback on the same client ID —
no second app registration needed. The dashboard-login project is viable;
no external blocker remains.

**Verify at build time:** the registered redirect URI above is
`/oauth/blaze/callback`, but the existing code registers the route at
`/auth/blaze/callback` (app.py:19050 — `@app.get("/auth/blaze/callback")`).
Confirm which path is actually correct/live before wiring the new
`/auth/dashboard/callback` flow alongside it — if the registered URI and
the code's route path don't match, the *existing* bot OAuth flow may
already be broken (or there's a proxy/redirect making up the difference
that isn't visible in the code), and that should be understood first so
the new flow doesn't get built next to a similarly-mismatched path.

## Effort estimate

Roughly 2-4 days of focused work:
- New OAuth routes reusing existing HTTP helpers: ~0.5-1 day
- Signed session cookie (stdlib hmac/hashlib): ~0.5 day
- Allowlist storage + admin add/remove: ~0.5 day
- Dual-mode gate rewrite + redirect-vs-JSON handling across ~15 gated
  paths: ~0.5-1 day
- Manual verification (including the second-browser check above): ~0.5 day

The Blaze-side redirect-URI/app-registration blocker is resolved (see
above); the `/oauth/` vs `/auth/` path check is now the main thing to
confirm before starting.
