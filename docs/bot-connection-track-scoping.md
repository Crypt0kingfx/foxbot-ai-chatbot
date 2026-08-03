# Bot Connection Track — Scoping Doc

Status: **inventory + phased plan only, no code written.** Written after
Phases 1/2/3a (the `by_creator` data-layer migrations) to scope the harder
remaining track: turning the data *shape* those phases built into real
per-creator *behavior* — a second creator logging in and running their own
FoxBot, with their own Blaze bot connection, on their own channel.

**Correction to existing docs, found while writing this:** both
`docs/blaze-dashboard-auth-plan.md` and `docs/multi-tenant-implementation-plan.md`
describe the "Blaze dashboard per-person login" phase as **not started**.
It is already fully built and live in `app.py` (`/auth/dashboard/login`,
`/auth/dashboard/callback`, `STUDIO_APPROVED_BLAZE_USER_IDS` allowlist, HMAC
-signed `foxbot_dashboard_session` cookie, dual-mode gate at
`foxbot_studio_admin_auth_gate_v1`, `app.py:1086`). This matters directly
for this track — see Section 3. Those two docs should get a status update
as a follow-up; not done here since this doc's job is the bot-connection
track itself.

---

## TL;DR

The current system already polls **multiple channels** with **one bot
identity**. The Bot Connection track is about making the identity itself
per-creator, not about teaching the poller to see more than one channel —
that part already works. Six sub-phases, roughly 2.5–4 weeks total,
sequenced so nothing touches the live tenant-zero bot until the last two
sub-phases. The design decision in Sub-phase B (what the identity-lock
becomes) is the part most likely to reopen the original vulnerability if
rushed — that's the piece to slow down on, not the piece with the most
lines of code.

---

## 1. The single-bot barrier — exactly where and how

**File:** `services/blaze_tokens.py` — the module's own docstring says it
outright:

> "One shared bot account authenticates every outbound and inbound Blaze
> chat call across every connected creator's channel, so there is exactly
> one token to resolve here -- not one per creator."

`resolve_blaze_access_token()` and `resolve_blaze_refresh_token()` are both
zero-argument functions. They read one file (`blaze_oauth_tokens.json`,
via `storage_path()`) and recursively hunt it for `accessToken`/
`refreshToken` keys (`_find_token()`) — schema-tolerant, but singular by
construction. No creator dimension exists to even ask for.

**The actual lock — `_foxbot_blaze_oauth_verify_identity_v1`
(`app.py:19000-19099`)** fires every time the bot-posting OAuth flow
(`/auth/blaze/login` → `/auth/blaze/callback`) completes and is about to
save tokens:

- Reads `BLAZE_BOT_USER_ID`/`FOXBOT_BLAZE_USER_ID` env var as the
  "expected" identity.
- **Bootstrap case** (env var unset): checks Postgres/local file for
  `already_saved`. If nothing's saved yet, lets the login through once and
  prints a one-time instruction to set the env var. If something *is*
  already saved and the env var still isn't set, it refuses outright
  (`FoxBotBlazeIdentityMismatch`) rather than silently trusting a possibly
  different identity — this is the actual anti-clobbering code path, and
  it's stricter than a simple equality check specifically to survive the
  ambiguous state a fresh env var creates.
- **Locked case** (env var set): calls Blaze's `/v1/users/profile` with the
  new token, compares the returned `userId` to the expected id
  (string-normalized both sides), and raises `FoxBotBlazeIdentityMismatch`
  on any mismatch.
- The exception is caught at the callback route (`app.py:19456`) and the
  refresh endpoint (`app.py:20174`), both returning HTTP 403 "This Blaze
  account is not the configured FoxBot bot account, so its tokens were not
  saved."

**The crux, stated precisely:** this is a single boolean gate — "is this
the one true bot, yes or no" — with no concept of "this is a *different but
equally valid* creator's bot." Its real job was never "there shall be
exactly one bot"; it's "a token save must never silently overwrite a
*different* identity's tokens sitting at the *same* storage location" (the
literal clobbering incident it was built to stop). Multi-bot support is
possible without reopening that hole **if the storage location itself
becomes keyed by identity** — one slot per Blaze user ID, `by_creator`-
shaped, same pattern Phases 1–3a already established for game-state.

Once storage is keyed per-identity, there is structurally nothing left to
clobber: a save for Blaze user ID X can only ever write to slot X. The
"verify identity" check then simplifies to "does the incoming Blaze user ID
match the slot key we're about to write" — which is true by construction —
and the *interesting* remaining question stops being an identity check and
becomes an **authorization** check: is this Blaze account allowed to
register a new bot-connection slot at all? That's a new decision this track
has to make (see Sub-phase B below); it doesn't exist today because today
there's only ever one slot to worry about.

---

## 2. Token storage per-creator

**Today:** `data/blaze_oauth_tokens.json`, one flat JSON object, mirrored
1:1 to a single Postgres row (`storage_paths.py`'s `_STATE_KEYS` maps the
filename directly to the state key `"blaze_oauth_tokens"` — a generic
key→JSON-blob mirror, not itself singular by design; it's the *file's
contents* that are singular).

**What changes:**

- File shape becomes `{"by_creator": {creator_id: {accessToken,
  refreshToken, expiresAt, blazeUserId, ...}}}` — identical pattern to
  `foxcoin_economy`/`custom_commands`/`viewer_streaks`'s existing
  `by_creator` shape. Same additive-migration recipe applies: snapshot,
  copy the current flat token into tenant-zero's slot, keep the flat keys
  live for a verification window, cleanup commit removes them later. The
  Postgres mirror mechanism doesn't need to change — it already mirrors
  whatever JSON shape is written.
- `resolve_blaze_access_token()` / `resolve_blaze_refresh_token()` go from
  zero-argument to `resolve_blaze_access_token(creator_id)`. Every current
  caller — `send_blaze_chat_message`, `get_recent_blaze_messages`, the
  refresh worker, `services/blaze_native_connector.py` — has **no
  creator_id in scope at its call site today**. Threading one in is the
  same work as Section 3 (routing) and Section 4 (listener); this is where
  those three pieces physically meet.
- The identity-lock check (Section 1) moves from "compare against one
  global env var" to "look up this Blaze user ID's slot; create one if the
  account is authorized to register a new bot connection."
- `BLAZE_BOT_USER_ID`/`FOXBOT_BLAZE_USER_ID` stop being *the* source of
  truth for "who is the bot" process-wide. At most they seed tenant-zero's
  slot for backward compatibility; each slot's own `blazeUserId` field
  becomes the real per-creator identity record.
- **Refresh worker** (`_foxbot_blaze_oauth_refresh_worker_v1`,
  `app.py:20246`): today a single loop calling
  `foxbot_blaze_oauth_refresh_v1()` once per interval for the one token,
  with a single flat `blaze_oauth_refresh_status` dict. Becomes: iterate
  every `creator_id` in the by_creator token store, refresh each
  independently, track status **per creator** — one creator's expired
  refresh token must not block or hide another's success. (Note: today's
  multichannel *poll* loop already has this exact gap even at the channel
  level — `_FOXBOT_MULTICHANNEL_STATE_V1["last_error"]` is one flat field
  for the whole cycle, not per-channel — because only one token backs
  every channel today, so it never mattered. It will start mattering the
  moment tokens diverge per creator.)

---

## 3. Routing — `_tenant_zero_id()` to real per-request creator identity

Two independent seams, one chat-side, one dashboard-side — and they don't
currently share a namespace.

### Chat side — already half-built

`_foxbot_process_channel_rows_v1(target, rows)` (`app.py:23052`) already
derives `creator_handle = target.get("handle")` per channel and passes it
into `chat(message=..., username=..., creator_handle=creator_handle)`
(`app.py:23179`). `chat()` (`app.py:3848`) receives and resolves
`creator_handle`. **The "which creator is this message for" question is
already answered, per-message, correctly**, and has been since the
multichannel poller was built.

The gap is entirely on the write side: `get_balance()`, `add_points()`,
and the custom-command lookups never accept a `creator_id`/`creator_handle`
parameter at all — they call the tenant-zero helpers directly
(`_tenant_zero_economy()`, `_tenant_zero_commands()`, etc.), ignoring the
`creator_handle` sitting one call-frame away. Confirmed 25 call sites
across `app.py` (`get_balance` at `app.py:2929`, `add_points` at
`app.py:2935`, command read/write at `app.py:6729`/`7014`, plus the
economy/streak internals). This is mechanical, not exploratory — thread
`creator_id` through those 25 sites and their callers' signatures, same
class of work Phase 1/2/3a already did when introducing `by_creator`,
except this time actually parameterizing every call site instead of
defaulting all of them to tenant-zero.

One caveat: `creator_handle` today is a **self-reported, unverified**
handle from the public `!connect` registration
(`services/creator_access.py`) — fine as a data-partition key (Phase
1-3a already accept this class of key implicitly), but not a secure
identity boundary on its own. That's adequate for *data* routing; it is
not adequate as the anchor for *bot-connection* authorization (Section 1's
new "who can register a slot" check needs something stronger — see the
join problem below).

### Dashboard side — the identity already exists, nothing reads it yet

This is the corrected finding from the top of this doc.
`foxbot_studio_admin_auth_gate_v1` (`app.py:1086`) already verifies a
signed session cookie and extracts a real, Blaze-confirmed `blaze_id`
(`_foxbot_dashboard_session_verify_v1(session_token)["blaze_id"]`,
`app.py:19794`) on every gated request, whenever `STUDIO_AUTH_MODE` is
`blaze` or `both` (default `both`). **The per-request creator identity for
the dashboard already exists at the point the gate runs — it's just
discarded immediately after the gate approves the request.** No
downstream studio-v2 endpoint reads it; every endpoint still operates on
tenant-zero data regardless of who's logged in.

What's needed: attach the resolved `blaze_id` to the request (e.g.
`request.state.creator_id`) and thread it into every studio-v2 endpoint
that touches a `by_creator` store — replacing `_tenant_zero_id()` with the
resolved per-request id, falling back to tenant-zero only when running in
`STUDIO_AUTH_MODE=basic` (no Blaze identity available) or when a
Blaze id hasn't yet been mapped to a `creator_id`.

### The join problem between the two sides

Dashboard identity is a **Blaze user ID**. Chat identity is a
**self-registered handle**. Nothing today ties a specific `blaze_id` to a
specific `creator_handle`/`channel_id` — they're different namespaces
populated by two different flows. Without a join, "my dashboard login" and
"my channel's chat messages" resolve to two different `creator_id` values
and a creator's own data silently splits in half. Fixing this needs
`connected_creators.json` to gain a `blaze_id` field, populated either the
first time that creator logs into the dashboard, or the first time they
complete the (new, Section 1/5) per-creator bot-connect OAuth — whichever
ships first becomes the join's origin point. This join has to exist before
Sub-phase D (below) can be considered complete, not just "dashboard reads
`by_creator`" and "chat writes `by_creator`" independently.

---

## 4. The listener

**Important correction to the framing in the request:** the listener
**already** listens to multiple channels. `blaze_polling_worker()`
(`app.py:22767`) runs one thread, one loop, and each cycle iterates every
target returned by `blaze_multichannel.build_targets()` — the owner's
channel plus every creator with active trial/subscription access
(`creator_access.active_creators()`) — calling
`get_recent_blaze_messages(channel_id=...)` and, on replies,
`send_blaze_chat_message(..., channel_id=...)` per channel. This is how
"connected creators" already works today: multi-channel, single identity.

What's missing is **multi-token**, not multi-channel: both
`send_blaze_chat_message` and `get_recent_blaze_messages` call
`resolve_blaze_access_token()` with **zero arguments** — every channel in
every cycle authenticates with the same one resolved token, regardless of
whose channel it is.

**What changes:**

- Both functions gain a `creator_id` (or resolved-token) parameter, sourced
  from `target` — which already carries `handle`/`channel_id`, so it just
  needs to also carry (or be able to look up) that creator's token-store
  slot from Section 2.
- The loop structure itself doesn't need to become multi-threaded to
  support this — it can stay one sequential loop, just resolving a
  different token per iteration instead of the same global one. Concurrency
  is a separate, independent scaling question:
- **Pre-existing scaling ceiling worth flagging, not fixing here:** all
  channels already share one thread's cycle time
  (`FOXBOT_MULTI_CHANNEL_POLL_SECONDS`, clamped 2–60s). More channels
  today already means slower per-channel poll latency for everyone,
  linearly. Real per-creator bots probably means more active channels
  sooner than expected — this ceiling isn't something the Bot Connection
  track needs to solve to ship its own goal, but it's likely the next
  thing to hit once a few creators are actually live.
- **Failure isolation:** `_FOXBOT_MULTICHANNEL_STATE_V1["last_error"]` is
  one flat field for the whole cycle today — acceptable when one token
  backs every channel (a token failure is everyone's failure, so one
  message is accurate). Once tokens diverge per creator, one creator's
  expired token failing must not read as "the whole bot is down," and must
  not block other channels' cycles from continuing. Needs per-channel (or
  per-creator) status tracking, not a shared field.

---

## 5. Sequencing & risk

```
Sub-phase A — Per-creator token storage shape         ~1–1.5 days   risk: low
        |     (additive migration, same recipe as Phase 1/2/3a)
        |
Sub-phase B — Identity-lock rework + authorization     ~2–4 days    risk: HIGH if rushed
        |     (per-slot gate; decide who may register a new slot)
        |
Sub-phase C — Refresh worker generalization            ~0.5–1 day   risk: low
        |     (per-creator status, depends on A+B)
        |
        +-- Sub-phase D can start here, in parallel --------------------
        |   Routing: dashboard blaze_id -> creator_id,                 |
        |   chat creator_handle -> real by_creator writes,             |
        |   plus the dashboard<->chat identity join         ~3–5 days |
        |   risk: low-medium (additive reads/writes, no auth change)  |
        +----------------------------------------------------------------
        |
Sub-phase E — New per-creator bot-connect OAuth flow   ~2–3 days    risk: medium
        |     (posting scopes, lands tokens via B's new gate)
        |
Sub-phase F — Listener/sender per-creator-token wiring ~3–5 days    risk: HIGH (live bot uptime)
              (depends on A/B/E; last, flagged, one creator at a time)
```

**Why this order:**

- **A → B → C** touch zero live chat behavior — they're pure token-storage
  infrastructure. Can run anytime, don't risk the current tenant-zero bot,
  and should be fully proven (including a self-test: tenant-zero's own
  login/refresh cycle working byte-identically through the *new* per-slot
  gate) before B ever accepts a second real identity.
- **D (routing)** is independent of B/C in practice — a dashboard or chat
  request can resolve "which creator" using the identity sources that
  *already exist today* (dashboard session's `blaze_id`, chat's
  `creator_handle`) even before any second creator has a real bot token.
  This confirms the existing multi-tenant plan doc's framing: data-routing
  and bot-connection are genuinely orthogonal tracks. They only need to
  agree on the identity join (Section 3) by the time both are done.
- **E (new OAuth flow)** is new, isolated route code — no risk to the
  existing bot until someone actually completes it. Mostly adapts the
  already-built dashboard-login flow's pattern (routes, PKCE cookies, the
  generic HTTP helpers) plus posting-scope handling.
- **F (listener/sender rewiring)** is the one step that touches the code
  path serving live chat right now. Must ship behind a per-channel flag,
  with the existing shared-token path as the default fallback, rolled out
  to one creator at a time — never a cutover. This is deliberately last.

**Live-data / security risk concentration:**

- **B is the design risk**, not a volume risk — it's the one place a
  mistake structurally reopens the exact clobbering vulnerability the
  current lock exists to prevent. The code itself is probably not large;
  the authorization decision behind it (who's allowed to connect a new
  bot slot — self-service? admin-approved? gated on an existing dashboard
  login? gated on `creator_access` subscription status?) is a real
  decision this doc is deliberately not making. **Flag this to the user as
  an open decision before Sub-phase B starts.**
- **F is the live-uptime risk** — the only sub-phase where a bug affects
  the *currently working* bot's responsiveness, not just data correctness
  or a not-yet-used feature.
- A/C/D/E risk live data or uptime only in the sense every additive
  migration in this repo already carries (snapshot-first, same as Phase
  1/2/3a's guiding rules) — nothing here is qualitatively riskier than
  those already-shipped phases.

---

## 6. Honest sizing

| Sub-phase | Effort | Long-pole dimension |
|---|---|---|
| A — token storage `by_creator` | 1–1.5 days | — |
| B — identity-lock rework + authorization model | 2–4 days | **design/decision risk**, not code volume |
| C — refresh worker generalization | 0.5–1 day | mechanical once A+B land |
| D — routing (dashboard + chat, ~25+ call sites + studio-v2 endpoints + identity join) | 3–5 days | **largest raw code-touch surface** |
| E — new per-creator bot-connect OAuth flow | 2–3 days | mostly adapts existing dashboard-login pattern |
| F — listener/sender per-creator-token wiring | 3–5 days | **largest live-bot-uptime risk** |
| **Total** | **~12–19 days (≈2.5–4 weeks)** | |

This is consistent with the existing multi-tenant plan doc's "multi-week...
the harder half" characterization, now broken into six independently
shippable, independently risk-rated pieces instead of one undifferentiated
estimate.

**Don't sugarcoat:**

- There is no single "long pole" — it depends what's being measured. By
  **code volume**, D is the long pole (echoes Phase 2+'s ~25-store scale,
  except this is wiring identity through call sites Phase 1/2/3a already
  data-migrated, not doing new migrations). By **design risk**, B is the
  long pole — getting the authorization model wrong is the one way this
  track could reintroduce the original incident. By **live-uptime risk**,
  F is the long pole — it's the only sub-phase touching the code path
  answering real chat messages right now.
- Sub-phase B cannot be estimated more precisely than "2-4 days" until the
  authorization-model decision is made; that decision is a product
  question, not something to resolve by writing code first and hoping the
  right shape falls out.
- Nothing in this plan should start before that decision is made
  explicitly, the same way Phase 1 didn't start until `creator_id`'s
  source was decided.

---

## Open questions before any sub-phase starts

1. **Sub-phase B's authorization model** (see above) — who is allowed to
   register a new per-creator bot connection slot? This blocks B, which
   blocks C, E, and F.
2. **The dashboard↔chat identity join** (Section 3) — decide whether
   `blaze_id` gets written into `connected_creators.json` at first
   dashboard login, at first bot-connect completion, or both, and which
   one wins if they disagree.
3. Confirm whether `docs/blaze-dashboard-auth-plan.md`'s still-open
   `/oauth/blaze/callback` vs `/auth/blaze/callback` path discrepancy
   (noted in that doc, not re-verified here) affects the *new* bot-connect
   OAuth flow (Sub-phase E) before building it — if the existing bot
   OAuth flow's registered redirect URI doesn't match its route, the new
   flow risks being built next to a similarly mismatched path.
4. Both `docs/blaze-dashboard-auth-plan.md` and
   `docs/multi-tenant-implementation-plan.md` need a status correction —
   the dashboard-login phase they describe as unbuilt is live. Recommend
   a short follow-up pass to update those docs so a future session doesn't
   re-scope work that's already shipped.
