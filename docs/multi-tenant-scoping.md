# Multi-Tenant FoxBot — Scoping Doc (future direction, not started)

Status: scoped only, no code written. Not scheduled for this session or the
current branch. This is an honest sizing exercise, not a plan to execute —
read the magnitude section before committing to anything.

## Goal (as stated)

Turn FoxBot from one shared admin dashboard into a real multi-tenant
product: each creator who uses FoxBot gets their own dashboard managing
their OWN economy, giveaways, rewards, bot connection, streaks, quests,
boss battles, etc. — not one shared instance everyone's data funnels into.

## 1. Global state inventory — everything that would need a creator_id

Every one of these is a **module-level global in `app.py`**, shared across
the entire process, with no creator/tenant dimension today. They're all
persisted together as **one JSON blob** (`get_persistent_snapshot()`,
app.py:491) to a single file (`foxbot_data.json`) mirrored to a single
Postgres row (state key `foxbot_data`, via `storage_paths.py`).

| Global | Line | What it holds | Keyed by |
|---|---|---|---|
| `foxcoin_economy` | 205 | balances, daily_claims, transactions | `viewer_key(name)` = lowercased username only |
| `viewer_stats` | 157 | per-viewer chat/command counts | username |
| `custom_commands` | 165 | custom chat commands | command name (global command set) |
| `stream_info` | 169 | game/title/lurkers | none — single stream |
| `arcade_stats` | 181 | coinflip/roll/8ball/rps/foxhunt counters | none — single global counters |
| `support_rewards` | 219 | reward amounts per event type | none — single config |
| `recognition_settings` / `recognition_log` | 261/273 | auto-recognition config + log | none |
| `stream_event` / `stream_event_templates` | 297/337 | active event (e.g. Golden Fox) | none — one active event globally |
| `community_quest` | 313 | quest progress/goal/claimed | none — one quest globally |
| `viewer_streaks` | 333 | per-viewer streak counters | username |
| `reward_shop` | 383 | redeemable items + costs | none — one catalog |
| `redemption_queue` | 429 | pending redemptions | none |
| `cooldown_settings` / `cooldown_tracker` | 433/459 | per-command cooldowns | command (+ username in tracker) |
| `boss_battle` | 463 | boss hp/damage_log/defeated_count | none — one boss globally |
| `giveaway_entries` / `giveaway_overlay` / `FOXBOT_STUDIO_GIVEAWAY_STATE_V3` | 101/145/3687 | **three overlapping giveaway stores**, already known-duplicated independent of tenancy | none |
| `STUDIO_STATE` (app.py + `models/studio_state.py`) | 14378 | dashboard KPI counters (followers/votes/subs/tips today) | none |
| `polling_status` / `proof_stats` | 119/1051 | listener/bot health | none — one bot |

**The critical finding:** `viewer_key()` (app.py:2711) is just
`normalize_viewer_name(name).lower()` — no channel or creator dimension at
all. If two different creators each have a viewer named `fox123` today,
they share the same FoxCoins balance, streak, and stats. This isn't a
"mostly-tenant-aware, missing a column" situation — there is currently
**zero** tenant boundary anywhere in the game-state layer.

One thing worth noting in FoxBot's favor: the multichannel polling worker
(`_foxbot_process_channel_rows_v1`, app.py:22677) **does** already carry a
`channel_key`/`creator_handle` per message during ingestion (used for
dedup and per-channel discovery-cutoff logic). That context exists at the
point a chat message is parsed — it's just discarded before reaching any of
the state above. That's a real seam to build on, not a full rewrite of
ingestion — see the incremental path below.

## 2. Bot-connection model — confirmed single-bot, not per-creator

There is **one Blaze bot token for the entire process**, by explicit
design. From `services/blaze_tokens.py`'s own docstring:

> "One shared bot account authenticates every outbound and inbound Blaze
> chat call across every connected creator's channel, so there is exactly
> one token to resolve here -- not one per creator."

`resolve_blaze_access_token()` returns exactly one token, sourced from one
file (`blaze_oauth_tokens.json`) or one env var. The OAuth save path
(`_foxbot_blaze_oauth_verify_identity_v1`, app.py:18720) actively
**rejects** a second Blaze identity from overwriting the saved token once
`BLAZE_BOT_USER_ID` is locked in — reinforcing that this was built
single-tenant on purpose, not left incomplete.

The multichannel poller (`_foxbot_multichannel_targets_v1`, app.py:22656)
reads chat across **multiple creator channels**, but does so using that
**one shared access token** — the bot's own Blaze account must already be
a member of/have access to each creator's channel. This is "one bot,
many channels it's been added to," not "many creators, each with their own
bot connection." The `connected-creators` registry (`connected_creators.json`)
is a directory of channels/viewers the shared bot has been pointed at
(self-registered via `!connect`) — it is not an OAuth-backed, per-creator
credential store. There is no code path anywhere that stores a *different*
access/refresh token per creator.

**Answer: no, this does not already support per-creator bot tokens.** A
real multi-tenant bot connection means each creator does their own
"Sign in with Blaze" (or an app-install-style grant) that produces and
stores *their own* token, and the poller/sender code has to route to N
tokens for N channels instead of 1 token for N channels. This is the same
*shape* of problem as the already-scoped dashboard-login project
(`docs/blaze-dashboard-auth-plan.md`) — new OAuth flow, new per-identity
token storage — but larger: it needs posting scopes (not just
`users.read`), per-creator refresh-token lifecycle, and the poller/sender
rewritten to be token-aware per channel instead of assuming one global
token everywhere it calls `resolve_blaze_access_token()`.

## 3. Magnitude — don't sugarcoat it

This is not a data-layer refactor. It's a near-restructure across two of
FoxBot's three layers at once, plus real product/ops decisions:

- **Data layer**: ~15+ independent global stores, each needs a creator_id
  added to its keys, and every read/write call site across a single
  22,000+ line `app.py` needs updating to pass and filter on that
  creator_id. This isn't "add a column" — some of these stores (giveaways)
  are already duplicated three ways and need consolidating *while* adding
  tenancy, not after. The persistence layer itself (one JSON blob → one
  Postgres row) needs to become genuinely partitioned per-creator storage,
  not just a bigger blob.
- **Bot-connection layer**: real per-creator Blaze OAuth (posting scopes,
  refresh lifecycle, identity storage) plus a rewrite of the multichannel
  poller/sender so it's token-routed per channel instead of single-token.
  This is arguably the harder half — it's genuinely new infrastructure, not
  a refactor of existing infrastructure.
- **Dashboard/auth layer**: every studio-v2 API route needs a creator_id
  scope threaded through, and the auth gate needs to become
  creator-session-aware so a creator can only ever see/manage their own
  data. This dovetails with (but doesn't replace) the already-scoped
  [[blaze_dashboard_auth]] plan — that plan gets someone logged in and
  allowlisted; it does not currently give them a scoped, isolated slice of
  data, because there wasn't one to scope them into.

**Realistic estimate: multi-week, not days** — this is a genuine future
initiative with its own milestones, not a sprint task. A credible range is
3-6+ weeks of focused work for a first real multi-tenant slice (one
creator-facing subsystem fully isolated + real per-creator bot connection
+ scoped dashboard access), and that's before covering the full state
inventory above. Anyone who quotes this in days is underestimating either
the call-site count in `app.py` or the bot-connection rework.

## 4. Incremental path — exists, but isn't free, and isn't independent of #2

Data isolation *can* be done one subsystem at a time behind a creator_id,
using the `channel_key`/`creator_handle` that's already available at the
ingestion layer (see #1) as the seed:

1. Pick the highest-value subsystem first — `foxcoin_economy` is the most
   central (nearly everything else awards or spends it).
2. Thread `creator_id` through its keys and every call site
   (`get_balance`, `add_points`, the transaction log).
3. Migrate existing data under a default/legacy `creator_id` so the current
   single-tenant admin's data isn't orphaned.
4. Repeat per subsystem (`viewer_stats` → `reward_shop`/`custom_commands`
   → streaks/quests/boss → collapse the three giveaway stores into one
   *while* adding tenancy, since touching that code for tenancy is the
   natural time to fix the duplication too).

This order is **not all-or-nothing** — each subsystem conversion is its
own bounded project, and the codebase doesn't have to stop shipping
between them.

**The real catch:** data isolation alone does not make this a multi-tenant
*product* if the bot connection (#2) stays single-tenant. You'd end up
with per-creator economies that all still depend on one shared Blaze bot
account being manually added to every creator's channel — not self-serve,
and a single point of failure across every tenant. If "each creator
connects their own Blaze bot" is a hard requirement (as stated), the
bot-connection rework isn't optional and isn't deferrable indefinitely —
it's just separable in sequencing: data isolation can start first and
ship value (one shared bot, but isolated per-creator economies), with the
per-creator bot-connection project landing afterward or in parallel.

**Recommendation if this is picked up later:** treat the `foxcoin_economy`
conversion as a pilot before estimating the rest. It'll surface the real
friction — call-site count, migration approach, how painful the
Postgres-partitioning change actually is — and that should inform a
firmer estimate for the remaining dozen-plus subsystems, rather than
guessing all of them up front.

## Related scoped work

- `docs/blaze-dashboard-auth-plan.md` — per-person dashboard *login*
  (identity + allowlist) is already scoped separately. That project gets
  the right *person* logged in; it does not, by itself, give them an
  isolated slice of *data* — this doc is what would need to layer on top
  of it for true multi-tenancy.
