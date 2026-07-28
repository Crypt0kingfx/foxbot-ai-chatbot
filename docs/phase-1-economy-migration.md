# Phase 1 — `foxcoin_economy` Per-Creator Migration Plan

Status: **plan only, no code written, no live data touched.** This is the
detailed design for the pilot subsystem in Phase 1 of
`multi-tenant-implementation-plan.md`. To be reviewed now; executed in a
fresh session once vetted, per Ryan's instruction.

## 1. Current shape → target shape

**Current (verified against `app.py`):**

```python
foxcoin_economy = {
    "currency_name": "FoxCoins",
    "balances": {"<viewer_key>": <int>, ...},
    "daily_claims": {"<viewer_key>": True, ...},
    "transactions": [{"viewer": ..., "amount": ..., "reason": ..., "balance": ...}, ...],  # capped at 50
}
```

One flat dict, no creator dimension. `viewer_key()` (`app.py:2755-2757`) is
just `normalize_viewer_name(name).lower()`.

**Target:**

```python
foxcoin_economy = {
    "currency_name": "FoxCoins",   # stays global -- see "Open decision" below
    "balances": {...},             # OLD SHAPE, frozen in place during transition, deleted in cleanup commit
    "daily_claims": {...},         # OLD SHAPE, same
    "transactions": [...],         # OLD SHAPE, same
    "by_creator": {
        "<creator_id>": {
            "balances": {"<viewer_key>": <int>, ...},
            "daily_claims": {"<viewer_key>": True, ...},
            "transactions": [...]   # capped at 50, per creator
        },
        ...
    }
}
```

`by_creator` nests one level deeper, mirroring the existing dict's own
shape per creator, rather than flattening keys (`f"{creator_id}:{viewer_key}"`).
This is a deliberate choice: it means `get_persistent_snapshot()`
(`app.py:504-547`) and `apply_persistent_snapshot()`'s hydration block
(`app.py:695-713`) need **zero changes** beyond one new `setdefault` —
both already treat `foxcoin_economy` as an opaque dict via `.update()`/
`globals().get()`, so a new nested key rides along automatically.

**`creator_id` value:** the owner's real Blaze user ID, now obtainable
from the Auth phase (the same ID already in `STUDIO_APPROVED_BLAZE_USER_IDS`
on Render). Read from a new env var, e.g. `FOXBOT_TENANT_ZERO_CREATOR_ID`,
set before the migration runs — **not yet known to me**, this is an input
the execution session needs, not something to hardcode or guess.

**Open decision, not resolved here:** should `currency_name` become
per-creator too? It's a cosmetic label, not viewer data. Recommend keeping
it global/shared for Phase 1 — making it per-creator is a real product
question (do creators want different currency names?) out of scope for
"prove the pattern," not a technical blocker either way. Flagging for your
call, not assuming.

## 2. Additive-migrate-then-cleanup, applied to this specific store

The generic recipe in the master plan (snapshot → write new shape
alongside old → deploy read-new-fallback-old → verify a day → cleanup
commit) was written before any specific store was mapped. Now that
`foxcoin_economy`'s call sites are mapped precisely (section 3), the
recipe simplifies in one respect worth calling out: because ~32 of the ~39
touchpoints already funnel through `get_balance()`/`add_points()`, there's
no need for a multi-deploy **dual-read fallback** layer (checking new
shape, falling back to old shape at every call site). A single deploy can
re-point all 39 touchpoints to the new shape at once, verified locally
first. The old flat shape's job isn't to stay live as a fallback — it's to
sit **frozen and unread**, as a pure rollback safety net: if a revert is
needed, the reverted code reads the old dict exactly as it was, byte for
byte, because nothing after the migration deploy ever writes to it again.

This is still the same additive-then-cleanup pattern, just right-sized:
fewer moving parts than the generic template implied, because the call
sites are genuinely concentrated rather than scattered.

## 3. Every read/write site, mapped

**Chokepoint functions (all reads/writes to `balances` funnel through
these two, confirmed by grep across the full file):**

- `get_balance(name)` — `app.py:2771-2775`
- `add_points(name, amount, reason)` — `app.py:2781-2817` (also appends to
  `transactions`, caps at 50)

**32 call sites into `add_points`/`get_balance`**, none of which touch
`foxcoin_economy` directly — migrating the two functions' internals fixes
all of them with **zero changes to any caller**:

| Category | Call sites (line refs) |
|---|---|
| Recognition (auto follow/sub/gifted-subs/vote/tip/raid/MVP/OG + surprise bonus) | 2940, 2970, 2988, 3008, 3028, 3048, 3066, 3084, 3100 |
| Boss battle (attack, power attack cost + reward, MVP bonus) | 2641, 4303, 4345, 4359, 4377 |
| Daily / daily streak checkin | 4433, 5531 |
| Community quest claim | 4825 |
| Stream event claim | 4883 |
| Chat activity / vote-token claim / follow / raid / tip / subscription / gift-sub rewards | 5001, 5275, 5321, 5337, 5353, 5399, 5415, 5461 |
| Admin `!givepoints` / `!takepoints` | 5609, 5677 |
| Redemption (shop purchase cost) | 5771, 5785 |
| Mysterybox (jackpot, prize) | 5801, 5821 |
| Generic reward path | 6071 |

**7 direct-dict-access bypasses — each needs an individual edit:**

1. `get_currency_name()` (`app.py:2763-2765`) — reads `currency_name`.
   Stays global per the open decision above; no change needed unless that
   decision changes.
2. `format_coin_leaderboard()` (`app.py:3466-3498`, `!coinleaderboard`) —
   reads `foxcoin_economy["balances"]` directly for sorting/display. →
   `foxcoin_economy["by_creator"].get(TENANT_ZERO_ID, {}).get("balances", {})`
3. `!daily`'s claim gate (`app.py:5519, 5533`) — reads/writes
   `foxcoin_economy["daily_claims"]` directly. → same nested-read pattern,
   `daily_claims` under `by_creator[TENANT_ZERO_ID]`.
4. `/foxcoins` endpoint (`app.py:9487-9519`) — direct access to
   `balances`/`daily_claims`/`transactions`; this is studio-v2's Economy
   tab's actual data source (confirmed: `templates/foxbot_studio_v2.html:2055`,
   `pollEconomy()` hits `/foxcoins`). → same pattern.
5. `/data-status` (`app.py:9981`) — `viewer_balance_count` via
   `len(foxcoin_economy.get("balances", {}))`. → same pattern.
6. `/api/studio/stats/live` (`app.py:14550`) — `foxcoins_total = sum(...)`.
   **This is the Overview hero tile's real data source** (the one built in
   the phase-2 design pass) — highest-visibility touchpoint, gets the most
   scrutiny in verification. → same pattern.
7. Persistence (`get_persistent_snapshot`/`apply_persistent_snapshot`,
   `app.py:504-547`, `695-713`) — no changes needed beyond one new line:
   `foxcoin_economy.setdefault("by_creator", {})` added to the existing
   hydration `setdefault` block, so a snapshot from before this migration
   (or a fresh empty state) doesn't `KeyError` on first read.

## 4. Migrating existing balances — the risky step, precisely

This is real granted currency your community treats as real. Detailed
end-to-end, including exactly how a rollback behaves.

**Steps, in order:**

1. **Snapshot** `foxbot_data.json` / the Postgres `foxbot_data` row to a
   timestamped backup, independent of Render's own backup policy — a
   30-second manual export, done first, before anything else.
2. **Set `FOXBOT_TENANT_ZERO_CREATOR_ID`** in Render to the owner's real
   Blaze user ID (from the Auth phase bootstrap).
3. **Code changes** (all in one commit, reviewed and tested locally before
   deploy):
   - `get_balance`/`add_points` internals rewritten to read/write
     `foxcoin_economy["by_creator"][TENANT_ZERO_ID]` instead of the flat
     dict. **Function signatures stay identical** — no caller needs to
     change, since Phase 1 doesn't yet route any real per-creator value in
     (that's Phase 2+/bot-connection territory); every call implicitly
     targets tenant zero.
   - The 6 direct-access bypasses (item 2-6 above) updated to the same
     nested-read pattern.
   - The one new persistence `setdefault` line.
   - **A one-time, idempotent migration copy**, run at hydration time:
     if `foxcoin_economy["by_creator"]` doesn't yet contain
     `TENANT_ZERO_ID` **and** the old flat `balances` dict is non-empty,
     deep-copy the old flat `balances`/`daily_claims`/`transactions` into
     `foxcoin_economy["by_creator"][TENANT_ZERO_ID]`. The idempotency
     check (`by_creator` doesn't already have this creator_id) is
     required — without it, every process restart would re-run the copy
     and silently overwrite live post-migration activity with the stale
     frozen flat-dict snapshot. This needs its own test: run the migration
     twice, confirm the second run is a no-op.
4. **Deploy during a LOW-ACTIVITY period — not mid-stream.** This is a
   direct consequence of the rollback nuance below: a revert doesn't lose
   any data, but it can orphan real activity that happened on the new
   shape while it was briefly live. The less activity accumulates between
   deploy and verification, the smaller that window is. Don't deploy this
   while a stream is live and chat is active; pick a quiet window instead.
5. **Verify immediately after deploy — before resuming normal activity,**
   not "at some point today." Run this checklist in order, right after the
   deploy finishes:
   - [ ] **Balances match pre-migration.** Total FoxCoins in circulation —
     `sum(by_creator[TENANT_ZERO_ID]["balances"].values())` — exactly
     equals the pre-migration flat-dict sum. Spot-check several known
     individual viewer balances by name against their pre-migration values.
   - [ ] **Give/take works.** Run `!givepoints` and `!takepoints` on a test
     viewer, confirm the balance change is correct and reflected
     immediately in `/foxcoins` and `!balance`.
   - [ ] **Recognition awards correctly.** Trigger (or wait for) one real
     recognition event (follow/sub/vote/tip/raid) and confirm the award
     lands on the correct viewer's `by_creator[TENANT_ZERO_ID]` balance,
     not the old flat dict.
   - [ ] **`/foxcoins` reads right.** studio-v2's Economy tab shows the
     correct total and per-viewer balances, matching pre-migration.
   - [ ] **The Overview tile reads right.** The hero tile's FoxCoins number
     (`/api/studio/stats/live`) matches the same total confirmed above —
     this is the most visible surface, worth a direct visual check, not
     just an API call.
   - [ ] `!daily`, `!coinleaderboard`, and a shop redemption all behave
     identically to pre-migration.
   
   If every box checks out, the window for orphaned-activity risk is
   effectively closed — normal activity can resume. If anything looks
   wrong, revert immediately (see rollback story below) before more
   activity accumulates on the new shape.
6. **Live-soak for a day** (per the master plan), then a **separate cleanup
   commit** removes the old flat `balances`/`daily_claims`/`transactions`
   keys and the now-unneeded migration-copy routine.

**Rollback story — the important nuance:**

Reverting the migration commit at any point **before** the cleanup commit
restores the exact pre-migration code, which reads the old flat dict —
**untouched, because nothing in the new code writes to it again once
migrated.** No balances vanish; they're all still sitting in `by_creator`
even after a revert, just not read by the reverted code.

**But:** if real activity happens *while the new code is live* (someone
claims `!daily`, an admin runs `!givepoints`, recognition fires) and *then*
a revert happens, that activity was written only to `by_creator` — the old
flat dict the reverted code reads was frozen at the pre-migration moment
and won't reflect it. A revert is not a data-loss event (nothing is
deleted, `by_creator` still holds it), but it **would orphan post-migration
activity** from what the reverted code displays, unless manually
reconciled. Mitigation: verify immediately per step 5, and if anything
looks wrong, revert fast, before real users generate much new activity —
keeping the reconciliation window small. If step 5 passes clean, there's
no rollback need at all.

## 5. Effort and safe-to-stop checkpoints

**Effort:** smaller than the master plan's generic 1.5-2.5 day estimate,
now that the call-site topology is mapped — two chokepoint functions plus
six enumerable bypasses plus one persistence line is a small, well-bounded
diff. Estimate **4-8 hours of implementation and local/mocked testing**,
plus the (mostly passive) day-long live-soak window before the cleanup
commit.

**Checkpoints:**

1. **After code is written and locally verified, before any deploy** —
   completely safe to pause here indefinitely; zero live risk, nothing
   shipped yet.
2. **After deploy, before the cleanup commit** — the safety-net window.
   Safe to pause here for as long as needed; a revert is a pure code
   action with the caveat above about post-migration activity.
3. **After the cleanup commit** — natural end state. Low-risk by
   construction: only reached after a full day of verified live behavior,
   and the flat dict being removed was already dead weight, not something
   anything still reads.

## What this plan does not cover

Per your instruction, no code was written and no live data was touched
tonight. This document is the design to review; execution happens in a
fresh session once you've vetted it, starting from step 1 above.
