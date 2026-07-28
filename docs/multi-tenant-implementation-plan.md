# Multi-Tenant FoxBot — Implementation Plan

Status: sequenced, not started. Supersedes `multi-tenant-scoping.md`'s
framing (that analysis is preserved below as "Evidence" — the sizing and
code references it found are still accurate and this plan is built directly
on them, re-verified against current `app.py` before writing this doc).

This is a phased build order, not a single migration. Every phase ships on
its own; nothing here requires a big-bang cutover of live data.

## Guiding rules (apply to every phase below)

1. **Additive before destructive.** When a phase changes a data *shape* (not
   just code organization), the migration copies data into the new shape and
   leaves the old shape in place for a verification window. A separate,
   later commit removes the old shape once the new one is proven live. This
   means a same-day rollback is always "revert the code commit," never
   "restore from backup" — the old-shaped data is still sitting there
   untouched until the cleanup commit explicitly removes it.
2. **Snapshot before any migration that changes shape.** Export
   `foxbot_data.json` / the Postgres `foxbot_data` row immediately before
   running a migration step, independent of whatever Render/Postgres backup
   policy is in place — don't rely on infra-level backups for something a
   30-second manual export covers.
3. **Legacy default tenant, not a reset.** Every store migrated gets a
   `creator_id` that defaults the *current* single creator's existing data,
   not a fresh empty state. The live FoxCoins economy, streaks, etc. don't
   reset to zero at any point in this plan.
4. **Verify with a number, not a vibe.** Every phase that touches live data
   gets a before/after total that must match exactly (total FoxCoins in
   circulation, total giveaway entries, etc.) — not just "the page loads."

---

## PHASE 0 — Consolidate the giveaway triple-store

**Correction to scope:** the reward-system duplication described in earlier
analysis is already fixed (commit `4b8293f`, 2026-07-27 —
`foxbot_admin_command_send_v1` is now a pure `chat()` passthrough, the two
diverting intercepts are deleted). Verified still true in current `app.py` —
those functions no longer exist. `reward_shop`/`redemption_queue` is a
single canonical system today. Phase 0 only needs to fix **giveaways**,
which are genuinely still three stores that disagree (see
[[giveaway_state_split]]).

**What changes:**
- Merge `giveaway_entries` (list, 30 refs), `giveaway_overlay` (dict, 16
  refs), and `FOXBOT_STUDIO_GIVEAWAY_STATE_V3` (dict, 3 refs) into one store.
- Delete or redirect the early intercept block in `chat()` (`app.py:3697-3762`)
  that currently makes the older `giveaway_overlay`-touching handlers
  unreachable for `!enter`/`!giveaway`/`!entries`.
- Route every path — chat commands, `/api/studio/giveaways/*`, and the public
  `/overlay/giveaway-data` — through the one store.
- Seed data: `giveaway_entries` is the only store every path currently agrees
  on, so it's the base. `FOXBOT_STUDIO_GIVEAWAY_STATE_V3`'s prize/rules
  fields merge in as the config side; `giveaway_overlay`'s winner field
  merges in as-is (it's already correctly written by `!pickwinner`).

**Delivers:** the OBS overlay's "active"/"latest entry" state stops going
stale relative to studio-v2 actions. No `creator_id` yet — this is pure
cleanup, prerequisite because you can't cleanly key a store that's actually
three tangled stores.

**Verification:**
- Reproduce the exact staleness scenario from [[giveaway_state_split]]: start
  a giveaway via studio-v2, add entries via chat `!enter`, confirm the OBS
  overlay reflects both without lag; run `!pickwinner`, confirm the winner
  shows on the overlay.
- Existing pytest suite (16 tests) stays green.

**Safety / rollback:** low risk. No `creator_id` dimension added, so there's
no migration in the tenancy sense — this is a same-process code
consolidation. Do it as an additive merge (new unified store populated from
all three old ones, old globals kept reachable read-only for one deploy
cycle) so a code revert alone fully restores prior behavior. Do this work
while no giveaway is actively live, to keep the blast radius of a
merge-logic mistake at zero real entries.

**Effort:** ~0.5–1 day (call sites are already enumerated above and in
[[giveaway_state_split]] — this isn't exploratory, it's a known fix).

**Risk to live data:** Low.

---

## PHASE (AUTH) — Blaze per-person dashboard login

Moved ahead of Phase 1 in this plan, out of the order originally sketched —
see reasoning below.

**What changes:** exactly what's already scoped in
`docs/blaze-dashboard-auth-plan.md` — a parallel OAuth flow
(`/auth/dashboard/login` + `/auth/dashboard/callback`, `users.read` only), a
Blaze-user-id-keyed allowlist, a signed session cookie, and a dual-mode gate
(Basic Auth **and** Blaze-auth both valid) during rollout.

**Why it's here and not later:** two independent reasons converge:
1. It's useful standalone today — replaces the shared
   `STUDIO_ADMIN_USER`/`PASSWORD` with real per-person identity, with zero
   dependency on any tenancy work.
2. **It produces the exact identifier Phase 1 needs.** Phase 1 has to pick
   what `creator_id` actually *is*. A self-reported chat handle
   (`creator_handle`, already threaded through `chat()`) is weak and can
   change. A Blaze user ID is stable and unforgeable — and once this phase
   exists, the current single creator's own Blaze user ID (obtainable
   immediately — the owner already has one bot-posting OAuth grant) is
   sitting right there to use as the permanent `creator_id` for "tenant
   zero." Building Phase 1 before this phase means either inventing a
   throwaway sentinel ID now and migrating again later, or blocking on this
   work anyway. Building it first avoids a second migration.

**Delivers:** per-person login (independently shippable) + a real, stable
identity source for every phase after it.

**Verification:** as scoped in the auth doc — full login round-trip from a
second browser/incognito session while Basic Auth still guards the real
dashboard, before ever dropping Basic Auth.

**Safety / rollback:** dual-mode gate is the safety mechanism — Basic Auth
never gets removed until Blaze-auth is verified solid as a separate,
deliberate follow-up change. Lockout risk is the main concern; mitigated by
never cutting over in the same change that builds the flow.

**Effort:** 2–4 days (estimate from the existing scoping doc, unchanged).

**Risk to live data:** None — this touches identity/session handling only,
no game-state stores.

---

## PHASE 1 — The tenancy primitive, piloted on `foxcoin_economy`

**What changes:**
- Introduce `creator_id`, sourced from the Auth phase's Blaze user ID for the
  current single creator (falling back to `creator_handle`/
  `resolve_owner_handle()` only if Auth hasn't landed yet — see open question
  below).
- Change `foxcoin_economy`'s keys from `viewer_key(name)` alone to
  `(creator_id, viewer_key(name))` — e.g.
  `foxcoin_economy["balances"][creator_id][viewer_key]`.
- Thread `creator_id` through `get_balance`, `add_points`, `viewer_key`, and
  the transaction log (26 `foxcoin_economy` refs + 9 `viewer_key` refs).
  `chat()` already receives `creator_handle` as a parameter (`app.py:3662`)
  and already threads it into `add_redemption()` — this phase extends that
  same existing plumbing into the economy functions, it doesn't invent a new
  seam.
- Default every call site without an explicit `creator_id` to the tenant-zero
  constant, so nothing changes for the current admin's experience.

**Migration (additive, per rule 1 above):**
1. Snapshot `foxbot_data.json` / the Postgres row.
2. Write existing flat balances/daily_claims/transactions under the
   tenant-zero `creator_id` key, **without deleting the old flat keys.**
3. Deploy code that reads new-shape-first, old-shape-fallback.
4. Verify (see below) live for a full day.
5. Separate cleanup commit removes the old flat keys once step 4 is signed
   off.

**Delivers:** proof the pattern works end-to-end — data model, call sites,
migration, persistence — on the most central store (nearly everything else
awards or spends FoxCoins). No new UI or creator-facing feature yet; this
phase is invisible to the current single creator. Its output is a firm,
evidence-based effort estimate for Phase 2+, instead of a guess.

**Verification:**
- Automated: migration-idempotency test (running it twice doesn't double-wrap
  or duplicate balances); `get_balance`/`add_points` round-trip test under
  `tenant_zero` matches pre-migration behavior exactly.
- Manual: total FoxCoins in circulation (the number the new Overview hero
  tile displays) must match exactly before and after migration. Spot-check
  several known individual viewer balances by name.
- Existing pytest suite stays green throughout.

**Safety / rollback:** this is the first phase that changes data *shape*, so
it's the highest-risk step in the whole plan — mitigated to low by the
additive-migration pattern (rule 1): a code revert at any point before the
cleanup commit fully restores the old flat-key reads with zero data loss,
because the old keys are still there. Never run this while deploying other
unrelated changes, so a revert is unambiguous.

**Effort:** ~1.5–2.5 days (more than a naive "add a key" estimate — the
additive/dual-read scaffolding and the verification script are real work,
not just the key-shape change itself).

**Risk to live data:** Medium-high inherently (real granted currency, first
shape change) — mitigated to low by the snapshot + additive-migration +
before/after-total verification above.

---

## PHASE 2+ — Remaining stores, one shippable slice at a time

Each store gets the exact same recipe proven in Phase 1: additive migration
→ dual-read verification window → cleanup commit. Suggested order, grouping
by similarity to reduce repeated learning cost:

1. **`viewer_stats` + `viewer_streaks`** — same `viewer_key` shape as
   `foxcoin_economy`, smallest step after the pilot.
2. **`reward_shop` + `redemption_queue`** — 20 + 15 refs, moderate; already a
   single canonical system post-Phase-0-era-fix, so no extra consolidation
   burden layered on top of the tenancy work.
3. **Giveaways** — now a single store post-Phase-0, just needs `creator_id`
   added to the shape Phase 0 already unified.
4. **`community_quest`, `boss_battle`, `stream_event`** — each is "one active
   X globally" today; becomes "one active X per creator_id." Structurally
   similar to each other, can likely be batched into one slice.
5. **`custom_commands`, `cooldown_settings`/`cooldown_tracker`,
   `arcade_stats`, `recognition_settings`/`recognition_log`, `STUDIO_STATE`**
   — lower product value, more mechanical, batch a few per slice.

Each slice ships independently and gets its own before/after consistency
check appropriate to that store (total quest progress, boss damage-log
length, etc.), matching rule 4.

**Effort:** don't pre-commit a number per store before Phase 1's actual
measured effort comes back — that's the reason to pilot first rather than
estimate all ~13 remaining stores today. Ballpark, scaling off Phase 1 and
the call-site counts above: roughly 0.5–1.5 days per slice, ~2–3 more weeks
total for the full inventory. Consistent with the original scoping doc's
3–6 week whole-project estimate.

**Risk to live data:** Low-medium per slice, same mitigation pattern as
Phase 1 throughout.

---

## PHASE (BOT CONNECTION, separate track) — per-creator Blaze bot

**Does it block the data-layer work? No.** Data isolation (Phases 0–2+) ships
real value with the *current* single shared bot account — "one bot, many
channels it's been added to" continues working exactly as today while each
creator's data becomes isolated behind `creator_id`. This track is orthogonal
at the code level: it touches token resolution (`resolve_blaze_access_token()`),
the multichannel poller, and the sender — not the state stores Phases 0–2+
touch.

**The one place they connect:** the multichannel poller already extracts
`channel_key`/`creator_handle` per message (`_foxbot_process_channel_rows_v1`,
`app.py:22677`). Phase 1's `creator_id` should be chosen so that when this
track eventually lands, a channel's messages carry a real, Blaze-verified
per-creator identity instead of a self-registered handle — and the
underlying `creator_id` doesn't need a second migration, only its *source*
gets more trustworthy. This is why picking a Blaze-anchored `creator_id` in
Phase 1 (via the Auth phase) rather than an arbitrary string matters now,
not later.

**What changes (when this track runs):**
- New OAuth flow with posting scopes (not just `users.read`) — an
  app-install-style grant per creator.
- Per-creator refresh-token lifecycle and identity storage (new
  infrastructure — `resolve_blaze_access_token()` today deliberately returns
  exactly one token by design, per its own docstring).
- Poller/sender rewritten to be token-routed per channel instead of assuming
  one global token everywhere it's called.

**Verification:** existing bot must keep working throughout — this is the
one track where a mistake affects live chat responsiveness, not just data
correctness. Roll out per-creator tokens behind a per-channel flag; never
remove the shared-token fallback until every active creator has migrated.

**Effort:** multi-week on its own — this is the "harder half" per the
original scoping doc, genuinely new infrastructure rather than a refactor.
Can run in **parallel** with Phase 2+ once Phase 1 has proven the
`creator_id` shape, but shouldn't start before Phase 1 lands — this track
needs to know what identifier shape to produce/consume.

**Risk to live data:** doesn't touch economy/game-state data directly, but
directly risks live bot uptime during the poller/sender rewrite. Rollback =
keep the single-token path as the default; per-creator tokens are additive
and flagged per channel, not a cutover.

---

## Recommended sequence (revised from the original rough sketch)

```
Phase 0 (giveaway consolidation)         ~0.5–1 day    risk: low
        |
Phase Auth (Blaze dashboard login)       ~2–4 days     risk: none (data-wise)
        |
Phase 1 (foxcoin_economy pilot)          ~1.5–2.5 days risk: med-high -> low (mitigated)
        |
Phase 2+ (remaining ~13 stores)          ~2–3 weeks    risk: low-med per slice
        |
        +-- parallel, starts after Phase 1 --> Bot connection (multi-week)  risk: touches live bot uptime
```

The one change from the rough sequence given at the start: **Auth moves
ahead of Phase 1**, not after everything, because it hands Phase 1 a stable
`creator_id` source instead of a throwaway sentinel that would need
migrating again later.

## Open questions before Phase 0 starts

1. Confirm Render Postgres has point-in-time recovery or an equivalent
   backup, independent of the manual snapshot step in rule 2 — belt and
   suspenders for Phase 1's shape change specifically.
2. If Auth is deferred past Phase 1 for any reason, decide the interim
   tenant-zero `creator_id` value explicitly (e.g. `resolve_owner_handle()`'s
   current return value) so it's a deliberate choice, not an accident — and
   flag that this becomes a second migration once Auth lands.
3. Resolve the `/oauth/blaze/callback` vs `/auth/blaze/callback` path
   discrepancy noted in `blaze-dashboard-auth-plan.md` before building the
   new dashboard callback alongside it.

---

## Evidence (from the original scoping pass, re-verified against current `app.py`)

### Global state inventory

Every one of these is a **module-level global in `app.py`**, shared across
the entire process, with no creator/tenant dimension today, persisted
together as one JSON blob (`get_persistent_snapshot()`, `app.py:504`) to one
file (`foxbot_data.json`) mirrored to one Postgres row via
`services/storage_paths.py`.

| Global | What it holds | Keyed by |
|---|---|---|
| `foxcoin_economy` | balances, daily_claims, transactions | `viewer_key(name)` — lowercased username only |
| `viewer_stats` | per-viewer chat/command counts | username |
| `custom_commands` | custom chat commands | command name (global set) |
| `stream_info` | game/title/lurkers | none — single stream |
| `arcade_stats` | coinflip/roll/8ball/rps/foxhunt counters | none — single global counters |
| `support_rewards` | reward amounts per event type | none — single config |
| `recognition_settings` / `recognition_log` | auto-recognition config + log | none |
| `stream_event` / `stream_event_templates` | active event | none — one active event globally |
| `community_quest` | quest progress/goal/claimed | none — one quest globally |
| `viewer_streaks` | per-viewer streak counters | username |
| `reward_shop` | redeemable items + costs | none — one catalog (now singular — see Phase 0) |
| `redemption_queue` | pending redemptions | none |
| `cooldown_settings` / `cooldown_tracker` | per-command cooldowns | command (+ username in tracker) |
| `boss_battle` | boss hp/damage_log/defeated_count | none — one boss globally |
| `giveaway_entries` / `giveaway_overlay` / `FOXBOT_STUDIO_GIVEAWAY_STATE_V3` | three overlapping giveaway stores (Phase 0 target) | none |
| `STUDIO_STATE` (`app.py` + `models/studio_state.py`) | dashboard KPI counters | none |
| `polling_status` / `proof_stats` | listener/bot health | none — one bot |

**The critical finding, unchanged:** `viewer_key()` (`app.py:2737`) is just
`normalize_viewer_name(name).lower()` — no channel or creator dimension. Two
creators' viewers named `fox123` today share the same FoxCoins balance,
streak, and stats. There is currently **zero** tenant boundary in the
game-state layer.

The multichannel poller (`_foxbot_process_channel_rows_v1`, `app.py:22677`)
already carries `channel_key`/`creator_handle` per message during ingestion,
and `chat()` (`app.py:3662`) already accepts and threads `creator_handle`
into `add_redemption()` — a real seam already reaching into a write path, not
just ingestion.

### Bot-connection model — confirmed single-bot, not per-creator

One Blaze bot token for the entire process, by explicit design. From
`services/blaze_tokens.py`'s own docstring:

> "One shared bot account authenticates every outbound and inbound Blaze
> chat call across every connected creator's channel, so there is exactly
> one token to resolve here -- not one per creator."

`resolve_blaze_access_token()` returns exactly one token. The OAuth save path
(`_foxbot_blaze_oauth_verify_identity_v1`, `app.py:18820`) actively
**rejects** a second Blaze identity from overwriting the saved token once
`BLAZE_BOT_USER_ID` is locked in — confirmed still present in current code.

### Related scoped work

- `docs/blaze-dashboard-auth-plan.md` — the Auth phase above, in full detail.
