# Phase 3a — `custom_commands` Per-Creator Migration Plan

Status: **plan only, no code written, no live data touched.** Re-derived
fresh in this session — the doc that was apparently discussed in a prior
session was never committed to disk (confirmed: absent from the working
tree, `git log --all`, and stash). Nothing here should be assumed to match
that earlier discussion; treat this as the source of truth going forward.

Applies the same additive-migrate-then-cleanup recipe as
[Phase 1](phase-1-economy-migration.md) (`foxcoin_economy`) and Phase 2
(`viewer_stats`/`viewer_streaks`, `d3d00eb` + cleanup `83748cf`), per
`docs/multi-tenant-implementation-plan.md`.

**Note on sequencing:** the master plan's suggested Phase 2+ order put
`reward_shop`/`redemption_queue` next, with `custom_commands` batched into
a later, lower-priority slice (item 5, "lower product value, more
mechanical"). This plan jumps ahead to `custom_commands` per your
direction — flagging the reorder, not objecting to it.

---

## 1. Current shape → target shape

**Current (verified against `app.py`):**

```python
custom_commands = {}   # app.py:170
```

Flat dict, no wrapper key: `{"!discord": {"response": ..., "created_by": ...}, ...}`.
Same shape family as `viewer_streaks` (no named sub-key like
`foxcoin_economy`'s `"balances"`) — the migration's no-wrapper condition
applies here too.

**Target:**

```python
custom_commands = {
    "by_creator": {
        "<creator_id>": {
            "!discord": {"response": ..., "created_by": ...},
            ...
        },
        ...
    }
}
```

## 2. The one structural difference from every prior store: wholesale reassignment

Every store migrated so far is merged into place in `apply_persistent_snapshot`:

```python
foxcoin_economy.update(data["foxcoin_economy"])   # Phase 1
viewer_streaks.update(data["viewer_streaks"])      # Phase 2
```

`.update()` merges the loaded dict's keys **into** the existing module-level
object — the object identity is preserved, so an initial-value
`{"by_creator": {}}` set at declaration time survives.

`custom_commands` does not use `.update()`. Current code
(`app.py:600-602`):

```python
if isinstance(data.get("custom_commands"), dict):
    custom_commands = data["custom_commands"]
```

This **rebinds** the global name to a brand-new dict object loaded from
storage, discarding whatever was previously bound to it. Two consequences,
one relevant to this migration and one pre-existing:

- **Relevant here:** because rebinding discards the old object entirely, a
  module-level initial value of `custom_commands = {"by_creator": {}}`
  (line 170) provides **no protection** — the very first time this runs
  against a pre-migration stored blob (no `"by_creator"` key in it), the
  rebound object has no `"by_creator"` key at all, and every touchpoint
  reading `custom_commands["by_creator"]` directly would `KeyError`.
  **The fix:** add `custom_commands.setdefault("by_creator", {})`
  immediately after the reassignment — this line is currently *missing
  entirely* for `custom_commands` (unlike `foxcoin_economy`/`viewer_streaks`,
  which both already have it). This is the "wholesale-reassignment fix"
  to build in from the start, not discover after a `KeyError` in
  production.
- **Pre-existing, not new, not in scope to fix:** `apply_persistent_snapshot`
  is called a second time mid-process on Postgres-outage recovery
  (`app.py:841`, inside `load_persistent_data()`'s caller), not only at
  startup. On that path, the reassignment fully replaces in-memory
  `custom_commands` with whatever was last durably persisted, discarding
  any `!addcmd`/`!delcmd` edits made in-memory during the outage window.
  This is already true today, for the flat dict, before this migration —
  it's the same accepted trade-off documented at `app.py:815-830`
  ("any in-memory-only edits made during the outage window are discarded
  ... not silent, log exactly what got dropped"). Migrating to `by_creator`
  doesn't change this risk's shape or severity, so it's noted for
  completeness, not treated as a blocker.

## 3. Every read/write site, mapped

No chokepoint functions here (unlike `foxcoin_economy`'s `get_balance`/
`add_points`) — every site is a direct dict access. **9 touchpoints, all
need editing:**

| # | Site | Location | Read/Write |
|---|---|---|---|
| 1 | `format_custom_commands()` — `!commands` listing | `app.py:3611-3621` | Read |
| 2 | **Seed-trap:** `!rules`/`!giveawaylink` `setdefault`, fires on every `chat()` call | `app.py:4132-4146` | Write (see §4) |
| 3 | `!addcmd` handler | `app.py:6639-6645` | Write |
| 4 | `!delcmd` handler (existence check + delete) | `app.py:6687-6697` | Read + Write |
| 5 | `!commands` dispatch → calls #1 | `app.py:6709-6715` | (covered by #1) |
| 6 | Command dispatch — the actual `!whatever` execution | `app.py:6918-6924` | Read (highest-traffic site) |
| 7 | `/custom-commands` endpoint (studio-facing) | `app.py:8836-8844` | Read |
| 8 | `/data-status` — `custom_command_count` | `app.py:10112` | Read |
| 9 | `/api/foxbot/onboarding` — `command_added` checklist item | `app.py:23297` | Read (see open decision below) |

**Persistence (2 sites):**
- `get_persistent_snapshot()` (`app.py:503`) — `globals().get("custom_commands", {})`, no change needed, carries whatever shape is live.
- `apply_persistent_snapshot()` (`app.py:600-602`) — needs the `setdefault("by_creator", {})` fix from §2, plus the migration-copy routine (§4).

**New helper**, mirroring `_tenant_zero_economy()`/`_tenant_zero_streaks()`:

```python
def _tenant_zero_commands():
    return custom_commands["by_creator"].setdefault(_tenant_zero_id(), {})
```

## 4. The `!rules`/`!giveawaylink` seed-trap — first-class concern

Current code (`app.py:4132-4146`), inside `chat()`, runs **unconditionally
on every request**:

```python
custom_commands.setdefault("!rules", {"response": "...", "created_by": "system-default"})
custom_commands.setdefault("!giveawaylink", {"response": "...", "created_by": "system-default"})
```

Today this seeds the flat top-level dict — there's no creator dimension to
get wrong yet. Post-migration, if this is left untouched, it would keep
writing into the now-frozen flat top-level (harmless but stale) or, if
naively re-pointed to `custom_commands.setdefault(...)` without going
through `by_creator`, would silently stop working. **The fix:** re-point
both `setdefault` calls through `_tenant_zero_commands()`:

```python
_tenant_zero_commands().setdefault("!rules", {...})
_tenant_zero_commands().setdefault("!giveawaylink", {...})
```

This seeds into tenant-zero's slice — consistent with every other
touchpoint in this migration and with Phase 1/2's precedent of routing
everything through `_tenant_zero_id()` rather than the per-request
`creator_handle` `chat()` already receives (real per-request creator
routing is the separate, not-yet-started "Bot Connection" track per the
master plan — this migration doesn't pull that forward).

**Open decision, flagged not resolved:** `/api/foxbot/onboarding`'s
`command_added = bool(custom_commands)` (`app.py:23297`) will, after
re-pointing to `bool(_tenant_zero_commands())`, be **almost always true**
after the first chat message ever processed, because the seed-trap means
`!rules`/`!giveawaylink` are always present. This is a pre-existing quirk
(true today too, just against the flat dict), not something Phase 3a
introduces — but per `phase-3-spec.md`'s own principle ("name items by
what's actually verified"), a checklist item that's permanently true isn't
proving what it claims to prove. Whether to exclude
`created_by == "system-default"` entries from that truthiness check is a
real product decision, out of scope for this data-shape migration. Your
call, not assumed.

## 5. Confirmed out of scope

- **`!schedule`** (`app.py:4184-4190`) and **`!faq`** (`app.py:4194-4199`)
  — both read directly from `os.getenv(...)` with a hardcoded default
  string. No connection to `custom_commands` at all, no per-creator
  dimension today, genuinely process-wide. Out of scope, confirmed.
- **`!socials`** (`app.py:6831`) — hardcoded branch, not a `custom_commands`
  entry, listed in `reserved_commands` precisely so `!addcmd` can't
  override it. Out of scope.
- **Recognition/welcome/goodnight message templates** (e.g.
  `GOODNIGHT_MESSAGE`, `app.py:4109-4113`) — `os.getenv(...)`-configured
  with hardcoded f-string defaults, a wholly separate mechanism, no
  `custom_commands` involvement. Out of scope, confirmed — this is future
  feature work, not part of this migration.
- **`reserved_commands`** set (`app.py:6605-6613`) — a name-collision guard
  for `!addcmd`, not data. Applies identically per-creator without any
  change needed.

## 6. Migration steps, in order (mirrors Phase 1 §4 / Phase 2)

1. **Snapshot** `foxbot_data.json` / the Postgres row — same manual export
   discipline as Phase 1/2, before any code change is deployed.
2. **Code changes, one commit, reviewed and pytest-green locally first:**
   - `custom_commands = {"by_creator": {}}` initial declaration (harmless
     given §2, but keeps the module-level default honest).
   - `apply_persistent_snapshot`: add `custom_commands.setdefault("by_creator", {})`
     immediately after the existing reassignment line (the fix from §2),
     plus the idempotent migration-copy block, same `pre_migration_*_keys`
     shape as Phase 2's `viewer_streaks` (no wrapper key):
     ```python
     pre_migration_command_keys = [key for key in custom_commands if key != "by_creator"]

     if (
         FOXBOT_TENANT_ZERO_CREATOR_ID
         and FOXBOT_TENANT_ZERO_CREATOR_ID not in custom_commands["by_creator"]
         and pre_migration_command_keys
     ):
         custom_commands["by_creator"][FOXBOT_TENANT_ZERO_CREATOR_ID] = {
             key: copy.deepcopy(custom_commands[key]) for key in pre_migration_command_keys
         }
     elif not FOXBOT_TENANT_ZERO_CREATOR_ID and pre_migration_command_keys:
         print("!!! FOXBOT PHASE-3A COMMANDS MIGRATION SKIPPED !!! ...")
     ```
     (`import copy` needs re-adding — Phase 2's cleanup removed it since
     nothing else used it; check for other consumers before re-adding to
     avoid a duplicate-then-cleanup churn.)
   - `_tenant_zero_commands()` helper added alongside the other two.
   - All 9 touchpoints from §3 re-pointed, including the seed-trap fix
     from §4.
3. **Deploy during a low-activity window**, per the same rationale as
   Phase 1/2 — a revert before cleanup is lossless, but minimizes the
   orphaned-activity window if anything's wrong.
4. **Verify immediately after deploy:**
   - [ ] `!commands` output matches pre-migration (same command names).
   - [ ] Existing custom commands (added via prior `!addcmd`) still fire
     correctly through the dispatch site (#6 in §3).
   - [ ] `!addcmd`/`!delcmd` work and land in `by_creator[tenant_zero]`.
   - [ ] `!rules` and `!giveawaylink` still answer correctly (seed-trap
     fires into the right slice).
   - [ ] `/custom-commands` and `/data-status`'s `custom_command_count`
     match pre-migration.
   - [ ] `/api/foxbot/onboarding`'s `command` item still computes without
     erroring (resolves the open decision from §4 or explicitly defers it).
5. **Live-soak a full day**, then a **separate cleanup commit** removes the
   frozen flat top-level command entries' *reader* (the migration-copy
   routine) — same pattern as `0038043`/`83748cf`. The flat entries
   themselves become dead weight in storage, not actively purged, matching
   both prior cleanups.

## 7. Effort and risk

**Effort:** smaller than Phase 1, comparable to Phase 2 — no chokepoint
functions to design, but one more touchpoint than Phase 2 (9 vs. Phase 2's
combined ~9 across two stores) plus the wholesale-reassignment fix as new
surface area not present in either prior store. Estimate **3-6 hours**
implementation + local verification, plus the day-long soak.

**Risk to live data:** Low-medium, same mitigation pattern (additive
migration, frozen rollback net, before/after checklist) as Phase 1/2. The
one genuinely new risk class is the wholesale-reassignment gap in §2 —
mitigated by adding the `setdefault` this time instead of discovering its
absence via a `KeyError` in production.

**Checkpoints:** identical structure to Phase 1 §5 — safe to pause after
code is written and locally verified (zero live risk), safe to pause after
deploy before cleanup (the soak window), natural end state after cleanup.

## What this plan does not cover

No code written, no live data touched. Stage 1 (additive `by_creator`
nesting + idempotent migration) is the next deliverable, shown as a diff
for review before any deploy.
