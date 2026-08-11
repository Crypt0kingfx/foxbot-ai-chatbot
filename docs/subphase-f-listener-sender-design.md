# Sub-phase F — Listener/Sender Per-Creator-Token Wiring — Design

Status: **design + inventory only, no code written.** Written after Sub-phases
A-E (token storage, identity-lock rework, refresh worker, routing, bot-connect
OAuth) to scope the last piece of the Bot Connection track: making a connected
creator's bot actually run on their own channel with their own Blaze identity.
Builds directly on `docs/bot-connection-track-scoping.md` (Sections 1-4 of that
doc map this same ground at a higher level; this doc re-verifies every claim
against current code — post the 13-store `by_creator` migration and the
gitignore/token-display cleanup — and adds the rollout-safety and
build-sequence detail that doc deliberately left for later).

**Headline finding: F's real remaining scope is small.** Every piece of
infrastructure F depends on — token storage, token refresh, multi-channel
polling, per-message identity resolution, per-creator data routing — is
already built and live. What's missing is exactly one thing: the listener and
sender still authenticate with **one shared token** regardless of which
creator's channel they're talking to. F is "consume the per-creator tokens
that already exist," not "build per-creator anything from scratch."

---

## 1. What already exists (verified against current code)

### Sub-phase A — per-creator token storage: DONE

`data/blaze_oauth_tokens.json` already has a `by_creator[blaze_id]` shape.
`_foxbot_blaze_oauth_save_tokens_v1` (`app.py:20005`) routes every save
correctly by construction:

- If the OAuth-verified identity matches the configured bot identity
  (tenant-zero), it writes the flat top-level keys (unchanged legacy path)
  **and** mirrors into `by_creator[tenant_zero_id]` (`sync_tenant_zero_slot`,
  `services/blaze_tokens.py:67`).
- If the verified identity is a **different** Blaze account, it writes
  `by_creator[actual_id]` **only** — never touches the flat keys or
  tenant-zero's slot (`app.py:20033-20037`, explicit isolation guarantee
  documented in the function's own comments).

A save for identity X can only ever reach slot X — there's nothing to
clobber, by construction (this is the "identity-lock rework" the original
scoping doc's Section 1 called for; it's shipped).

### Sub-phase C — per-creator refresh: DONE

`_foxbot_blaze_oauth_refresh_worker_v1` (`app.py:21647`) already:

- Refreshes tenant-zero via the unchanged flat-path call (byte-identical to
  before Sub-phase C existed).
- **Iterates every other `creator_id` in `by_creator`** (`app.py:21714`),
  refreshing each with **their own** refresh token
  (`_foxbot_blaze_oauth_refresh_creator_v1`, `app.py:21566` — explicitly
  never falls back to the shared `BLAZE_REFRESH_TOKEN` env var for a scoped
  creator).
- Tracks status **per creator** (`blaze_oauth_refresh_status["per_creator"]`,
  `app.py:21638`), with a `try/except` **per creator_id** inside the loop
  (`app.py:21722-21737`) — one creator's expired/invalid refresh token
  cannot stop the loop from reaching the next creator, and is recorded only
  against that creator's own status entry.

This is the exact failure-isolation pattern F's own rollout safety needs to
copy (see Section 4) — it's already proven, live, and running every hour.

### Sub-phase D — identity resolution + data routing: DONE, both sides

**Chat side.** `blaze_polling_worker` (`app.py:24441`) already polls
**multiple channels per cycle** — not a new capability F needs to add. Each
cycle iterates `_foxbot_multichannel_targets_v1()` (owner channel + every
creator with active trial/subscription access, via
`services/blaze_multichannel.py`'s `build_targets()`, which reads the shared
`data/connected_creators.json` registry through `services/creator_access.py`).
`_foxbot_process_channel_rows_v1` (`app.py:24726`) resolves
`creator_handle`/`resolved_creator_id` **once per channel** (`app.py:24742`)
and threads it into `chat(message=..., username=..., creator_handle=...)`
(`app.py:24873`) — so **the "which creator is this message for" question is
already answered correctly, per message, per channel**.

**Data-store side.** `get_balance`/`add_points` already accept `creator_id`
(`app.py:3332`, `3342`) — the original scoping doc flagged 25+ call sites
needing this threading; that work is done, partly predating this doc and
partly completed in this project's own preceding sessions (the `arcade_stats`,
`recognition_log`/`recognition_settings`, `community_quest`, and
`stream_event` migrations — all 13 flat-global stores from the original
inventory are now `by_creator`-shaped with both read and write sides
threaded).

**Dashboard side.** Every studio-v2 endpoint that touches a `by_creator` store
(`/streaks`, `/community-quest`, `/stream-event`, `/api/studio/stats/live`)
already resolves `resolved_creator_id = _foxbot_resolve_creator_id_v1(blaze_id
=getattr(request.state, "blaze_id", None))` — the exact pattern the original
scoping doc's Section 3 called for, now shipped everywhere it needs to be.

**The join.** `connected_creators.json` gains a `blaze_id` field via
`_foxbot_connect_set_blaze_id_v1` (`app.py:18570`), written on a successful
dashboard login using the Blaze-verified identity — never a caller-supplied
value. This is the **same registry** `services/creator_access.py` uses for
`channel_id`/trial-access fields, so there's one unified per-handle record:
`{handle, blaze_id, channel_id, channel_slug, status, ...}`.

### Sub-phase E — per-creator bot-connect OAuth: BUILT, not yet functionally live

`/auth/bot-connect/login` + `/auth/bot-connect/callback` exist
(`app.py:21215+`), flag-gated behind `FOXBOT_BOT_CONNECT_ENABLED` (default
off), and write into `by_creator[actual_id]` via the **same**
`_foxbot_blaze_oauth_save_tokens_v1` tenant-zero's own flow uses — so once a
creator completes it, their token lands in exactly the same storage shape C
already refreshes automatically. Currently blocked at Blaze's end (redirect
URI registration issue — the three OAuth debug endpoints,
`/api/blaze/oauth/{debug,bot-connect/debug,dashboard/debug}`, exist
specifically to diagnose this).

**Net finding:** the moment E's external blocker clears and a real second
creator completes bot-connect, a real, isolated, auto-refreshing token will
already be sitting in `by_creator[their_blaze_id]` — completely unused by the
listener or sender. **That gap — consumption, not production, of an
already-working per-creator token — is F's entire remaining scope.**

---

## 2. The listener

Today's exact behavior (confirmed, not assumed): `blaze_polling_worker` →
`_foxbot_multichannel_targets_v1()` → `blaze_multichannel.build_targets()`,
which returns the owner's channel + every creator in
`creator_access.active_creators()` + the subscription-control channel. The
loop already calls `get_recent_blaze_messages(channel_id=target["channel_id"])`
once per target, per cycle. **Iterating connected creators and their channels
is not new work — it's already the live behavior.**

**Data source for "which creators are connected + their channel IDs":**
already exists, no new store needed — `data/connected_creators.json`
(`creator_access.active_creators()`), which already carries `channel_id`
(resolved once via Blaze's `/v1/channels` API and cached,
`services/blaze_multichannel.py:129-136`) and now also carries `blaze_id` (via
D's join).

**What's actually missing:** `get_recent_blaze_messages(channel_id=None)`
(`app.py:24582`) calls `resolve_blaze_access_token()` with **zero
arguments** — every channel, every cycle, authenticates with the same
resolved token, regardless of whose channel it is.

**Design:** extend `resolve_blaze_access_token()` (`services/blaze_tokens.py`)
to accept an optional `creator_id`:

- `creator_id=None` (every existing call site, unchanged) → today's exact
  behavior, byte-identical.
- `creator_id` given **and** activated (Section 4) **and** a real slot exists
  in `by_creator[creator_id]` → return that creator's own token.
- Otherwise → fall through to today's flat/env logic, exactly as if
  `creator_id` had never been passed.

The per-target loop already has `target["handle"]`/`target["channel_id"]` in
scope; it needs to also resolve `target`'s `blaze_id` (via the join, already
present on the `connected_creators.json` record) and pass that as
`creator_id` into the new resolver call. No new lookup infrastructure — the
join and the token store both already exist; this is wiring one to the other.

---

## 3. The sender

`send_blaze_chat_message(text, channel_id=None)` (`app.py:24533`) already
takes `channel_id` and is already called with the correct one per target
(`app.py:24876`, inside `_foxbot_process_channel_rows_v1`). It has the
identical gap as the listener: `resolve_blaze_access_token()` called with
zero arguments.

**Design:** same fix, same resolver, same fallback order. One design note
worth stating explicitly: **resolve the token once per target per poll
cycle, and pass that single resolved value to both the read call and any
send call that cycle** — not have each function independently
re-call the resolver. Today both functions already separately duplicate
near-identical `resolve_blaze_access_token()` + `BLAZE_CHANNEL_ID`-fallback
logic; once tokens diverge per creator, two independent resolutions in the
same cycle risk computing different answers (e.g. a refresh landing between
the read and the send) or simply drifting out of sync as one gets edited and
the other doesn't. A single per-target resolution, threaded into both calls,
removes that class of bug structurally.

---

## 4. Rollout safety

This is the part of F that actually matters most, per your framing — here's
the concrete structure.

### Per-channel activation flag

Propose `FOXBOT_BOT_CONNECT_ACTIVE_CREATOR_IDS` — a comma-separated allowlist
env var, same shape and same admin-only Render-edit rollout lever as
`ADMIN_USERNAMES`/`STUDIO_APPROVED_BLAZE_USER_IDS` already use elsewhere in
this codebase. No new admin UI, no new storage, no code deploy needed to
activate or deactivate one creator — just an env var edit and a restart,
identical in kind to how `FOXBOT_BOT_CONNECT_ENABLED` itself already gates
Sub-phase E.

**Two independent gates, both required**, so activation is never accidental:

1. Does a real token exist in `by_creator[creator_id]`? (Sub-phase E's job,
   already built.)
2. Is `creator_id` in the activation allowlist? (F's new gate.)

A creator completing bot-connect OAuth does **not** by itself turn on
per-creator listening/sending for them — it only produces a token that *sits
there*, refreshed and ready, until an admin explicitly adds their id to the
allowlist. This decouples "OAuth succeeded" from "live behavior changed,"
which is exactly the separation a high-uptime-risk feature needs.

### Shared-token/tenant-zero fallback as default

The resolver's fallback path **is** today's code, not a parallel branch that
happens to produce the same result — `creator_id=None`/not-activated/no-slot
all collapse to the identical `resolve_blaze_access_token()` call every
existing caller already makes. This is the same "additive, tenant-zero
untouched by construction" discipline every `by_creator` data migration in
this project has already shipped (arcade/recognition/community_quest/
stream_event) — F applies the identical pattern to a token instead of a data
dict.

### Per-channel failure isolation

`_FOXBOT_MULTICHANNEL_STATE_V1["last_error"]` (`app.py:24423`) is one flat
field for the whole poll cycle today — correct when one token backs every
channel (a token failure really is everyone's failure), but wrong the moment
tokens diverge. Needs to become per-target status, mirroring the **already-
shipped** `blaze_oauth_refresh_status["per_creator"]` shape and its
`try/except`-per-`creator_id` loop structure (`app.py:21714-21737`) — copy
that proven pattern, don't invent a new one. One creator's expired/revoked
token must show up **only** against that creator's own status entry, never
read as "the whole bot is down," and must never stop the loop from reaching
the next channel.

### Tenant-zero stays byte-identical — why, structurally

Tenant-zero's `creator_id` is **already excluded** from the per-creator
refresh loop (`if creator_id == tz_id: continue`, `app.py:21715`) and the new
resolver's "no slot / not activated" path **is** the flat-key path tenant-
zero already uses. Tenant-zero cannot take a different code branch than it
does today unless someone deliberately adds tenant-zero's own id to the
activation allowlist — which would be an unusual, explicit choice, not
something any normal rollout step does by accident.

---

## 5. Risk map

| Failure mode | Impact | Mitigation |
|---|---|---|
| A creator's token expires/is revoked mid-cycle | That channel's read/send fails for one cycle | Per-target `try/except` (Section 4) — skip and log, don't crash the loop or touch other targets |
| A bug in the **new resolver itself** (exception, bad lookup) | Could abort the whole cycle if called outside the per-target isolation boundary | Wrap the resolver call in the **same** per-target `try/except` as the read/send calls — it is not "safe infrastructure" exempt from isolation, it's a new failure surface like any other |
| A lookup bug sends channel A's post through creator B's token (wrong join resolution) | **Cross-tenant identity leak** — FoxBot posts into one creator's channel using a different creator's bot account. Worse than an uptime bug: a real platform-level incident, not just a degraded feature | Needs its own explicit test in the build sequence: assert token X is *never* used for a `channel_id` that doesn't belong to token X's `creator_id` — the identity analog of the cross-creator-leak tests already run for `community_quest`/`stream_event`, but higher-stakes here since it's a live outbound network call, not an in-memory dict write |
| Refresh (C) races a read/send at cycle time — F holds a token that just got rotated | Auth failure on a stale token | Low risk (refresh interval defaults to 3600s, poll interval is 2-60s), but resolve fresh each cycle, never cache across cycles — already implied by "resolve once per target per cycle" (Section 3) |
| A newly-activated creator's channel misbehaves in production | Need a fast, low-drama way back to safe | **Rollback = remove their `creator_id` from the allowlist env var and restart.** No data migration to undo, no code revert — same instant-rollback property `FOXBOT_BOT_CONNECT_ENABLED` already has |

**Highest-risk moment in any rollout:** the **first** activated creator's
**first** live poll cycle. Everything before that point is provably dormant
(byte-identical tenant-zero, by construction — Section 4). All real risk
concentrates at "does the resolver + isolation code work correctly for a
real non-tenant-zero token, for the first time, against a live channel."
This is the argument for Stage F.2 below being read-only before any send
capability ships.

---

## 6. Proposed build sequence

Same discipline as A-E: small, independently reviewable, dormant-by-default,
one thing proven before the next depends on it.

**Stage F.0 — Token resolver (infrastructure only, zero call-site changes)**
Add `resolve_blaze_access_token(creator_id=None)` to `services/blaze_tokens.py`
— extends, doesn't replace, the existing zero-arg version. Default `None`
preserves every existing caller byte-identical, since none of them pass
`creator_id` yet. Looks up `by_creator[creator_id]` only when given **and**
allowlisted; otherwise falls through to today's flat/env logic unchanged. No
caller changes. Fully dormant, fully testable offline (same verification-
script style used for the `by_creator` data-store migrations) with zero
touch on the live poller. **This is the smallest safe first step — the "A"
of Sub-phase F.**

**Stage F.1 — Per-target status isolation (infrastructure only, no token
routing yet)** Split `_FOXBOT_MULTICHANNEL_STATE_V1["last_error"]` into
per-target status entries and move the cycle's coarse `try/except` down to
per-target granularity, mirroring the already-proven per-creator refresh
pattern. Valuable and low-risk **on its own**, even before any token
diverges — should be a complete no-op against today's all-shared-token
behavior, verifiable via the existing multichannel status endpoint. Proves
the isolation boundary works before Stage F.2 depends on it.

**Stage F.2 — Read-side wiring, dry-run/logging only, allowlist empty by
default** Thread `creator_id` into `get_recent_blaze_messages` calls for
allowlisted creators; log which token source was used per cycle (masked),
without changing behavior for anyone — ships with an empty allowlist, so
it's dead code in production until a Render config change adds a real
`creator_id`. This is the true first live-uptime-risk step, but scoped to
**reads only** — a bug here degrades to "this channel sees stale/no messages
this cycle," never a wrong-identity post to a live channel.

**Stage F.3 — Send-side wiring, same allowlist gate** Thread `creator_id`
into `send_blaze_chat_message` for the same allowlisted creators, reusing
the token already resolved once per target in F.2 (per Section 3's
single-resolution design note). This is where a message first posts to a
live channel under a real per-creator identity — the actual product goal of
the whole track.

**Stage F.4 — One real creator, live verification** Once Sub-phase E is
unblocked at Blaze and a real second creator completes bot-connect, add
**exactly one** `creator_id` to the activation allowlist. Verify: their
channel is read and replied to using **their own** token (confirmed from
Blaze's side — the reply posts as the creator's own bot identity, not
FoxBot's shared account), the cross-tenant-leak test from Section 5 passes,
and tenant-zero's cycle counters (`checks`, `messages_seen`) are provably
unaffected. This is the "one creator at a time, never a cutover" moment —
everything in F.0-F.3 exists specifically to make this the *only* step where
real risk is taken, with every upstream piece already proven dormant.

---

## Open question before F.0 starts

Same one the original scoping doc flagged and left open: Sub-phase E is
still blocked at Blaze (redirect URI registration). F.0-F.3 don't strictly
need E unblocked to be built and merged dormant (empty allowlist = no
behavior change for anyone) — but F.4, the actual live verification, does.
Worth deciding whether to build F.0-F.3 now (dormant, zero risk, ready to
go) while E's blocker is still being chased, or wait until E is confirmed
working end-to-end first so F.4 can follow immediately without a gap.
