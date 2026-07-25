# FoxBot Studio — Phase 3 Spec

**Scope:** Wire the setup checklist and activity feed in `/studio-v2` to real data.
**Depends on:** Phase 1 (static shell) and Phase 2 (vitals strip) complete.
**Prototype:** `design/foxbot-studio-v2.html`

---

## Governing principles

Three rules that decide most of the implementation questions below.

**1. Derive state, don't store it.** Checklist items compute from the data that already proves them. No per-item boolean flags. A stored flag becomes a second source of truth and drifts from reality within days — the same class of problem as the three token resolvers.

**2. Name items by what's actually verified.** If a check can only prove FoxBot posted successfully, the label says that. It must not claim to verify moderator status while checking something else. An interface that overstates what it knows is how a bot sits dead behind a green checkmark for two days.

**3. Never append to a JSONB blob.** The existing `foxbot_state` table is single-row-per-key with last-write-wins concurrency. Appending events to it grows unbounded and corrupts under concurrent writes. Events get a real table.

---

## Part A — Setup checklist

Renders at Home → Overview, above the activity feed. Five items, each computed on request.

### Items and their derivations

| # | Label | Done when | Source |
|---|---|---|---|
| 1 | Register your channel | A record exists for this handle | `connected_creators` |
| 2 | FoxBot posted in your chat | ≥1 event of kind `bot_reply` for this channel | `foxbot_events` |
| 3 | Turn on your first command | ≥1 command enabled | existing command config |
| 4 | Set up a reward | ≥1 active reward | existing rewards config |
| 5 | Run your first giveaway | ≥1 giveaway completed | existing giveaway state |

### On item 2

The prototype originally labelled this "Add FoxBot as a moderator." **Do not use that label unless moderator status is verified directly.**

Investigate first: does Blaze's API expose a channel moderator list that can be queried for the bot account? If yes, verify mod status directly and the label can say so. If no, fall back to "FoxBot posted in your chat," derived from a successful post event.

The fallback is weaker as a permission check but stronger as a functional one — it proves the thing the creator actually cares about. Either way, the label must match what is checked.

**Report which path is available before implementing.**

### Items 3–5

Their exact sources depend on how commands, rewards, and giveaways currently persist. Locate the existing config for each and derive from it. If any of these systems is mid-repair (Rewards is being fixed; Streaks is queued), the item should still compute correctly from whatever config exists — the checklist reads state, it doesn't depend on the feature working end to end.

**If a system has no queryable state at all, say so rather than inventing storage for it.**

### Behavior

- Compute all five on each page load; no caching in v1
- Progress shown as `N / 5` plus the gradient fill bar in the prototype
- Dismissible via a "Hide this" control, **but only once item 1 is complete** — nobody should be able to bury the registration step
- When all five complete: show the completed state once, then auto-hide on the next load. Don't require dismissal.
- Persist dismissal in Postgres, not localStorage — it must survive across devices

### Dismissal storage

```sql
CREATE TABLE onboarding_progress (
    channel_id    TEXT PRIMARY KEY,
    dismissed     BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Only dismissal and overall completion are stored. Per-item state is always derived.

### Endpoint

```
GET /api/foxbot/onboarding?channel_id=...
```

```json
{
  "ok": true,
  "dismissed": false,
  "completed": 3,
  "total": 5,
  "items": [
    { "key": "register",  "label": "Register your channel",        "done": true },
    { "key": "posted",    "label": "FoxBot posted in your chat",   "done": true },
    { "key": "command",   "label": "Turn on your first command",   "done": true },
    { "key": "reward",    "label": "Set up a reward",              "done": false, "href": "/studio-v2/rewards" },
    { "key": "giveaway",  "label": "Run your first giveaway",      "done": false, "href": "/studio-v2/giveaways" }
  ]
}
```

`ok: false` on failure. **The UI must distinguish "checklist unavailable" from "nothing complete"** — a failed query must not render as five empty checkboxes, which would tell a fully-configured creator they've done nothing.

---

## Part B — Activity feed

The "Just happened" panel. Requires durable storage: Render's filesystem is ephemeral and the service restarts frequently, so an in-memory ring buffer would be empty most of the time. An empty feed on a working bot is worse than no feed.

### Schema

```sql
CREATE TABLE foxbot_events (
    id          BIGSERIAL PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    actor       TEXT,
    detail      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_foxbot_events_channel_time
    ON foxbot_events (channel_id, created_at DESC);
```

This is the first properly relational table in the app. That's intentional — the single-JSONB-blob pattern is what allowed the token storage problems, and event data is exactly the shape that pattern handles worst.

### Event kinds

Log these five only:

| kind | Emitted when | `actor` | `detail` |
|---|---|---|---|
| `command` | A viewer runs a chat command | viewer handle | `{"command": "!help"}` |
| `bot_reply` | FoxBot posts a message | `null` | `{"in_reply_to": "!help", "viewer": "..."}` |
| `follow` | A new follower | follower handle | `{}` |
| `reward` | A reward is claimed | viewer handle | `{"reward": "...", "cost": 40}` |
| `listener` | Listener connects or disconnects | `null` | `{"state": "connected"｜"disconnected", "reason": "..."}` |

**Do not log every chat message.** On a busy stream that's a write per message and the table grows without bound. Raw message counts stay as aggregates in `connected_creators`, which already tracks them.

`listener` events are deliberately included — a feed showing "listener disconnected" three times in a minute makes a crash loop visible, which is precisely the failure that went unnoticed this week.

### Retention

Events older than 30 days should be deleted. Simplest approach: a cheap `DELETE` on write, run probabilistically (e.g. ~1% of inserts) rather than on a scheduler, since there's no job runner in this app.

```sql
DELETE FROM foxbot_events WHERE created_at < NOW() - INTERVAL '30 days';
```

**Propose this before implementing** — if a scheduler already exists, use it instead.

### Endpoint

```
GET /api/foxbot/events?channel_id=...&limit=20
```

```json
{
  "ok": true,
  "events": [
    {
      "kind": "command",
      "actor": "viewer_88",
      "detail": { "command": "!help" },
      "created_at": "2026-07-24T19:12:04Z",
      "age_seconds": 12
    }
  ]
}
```

Include `age_seconds` server-side so the client doesn't need clock-skew handling for its relative timestamps.

### Rendering

Match the prototype's `.feed-item` markup. Relative times (`12s`, `1m`, `3m`, `11m`) in the mono face. FoxBot's own actions use `.bot-said` in magenta — on this dashboard magenta means either "broken" or "the bot speaking," and nothing else.

**Empty state:** if the feed returns zero events, that's an invitation, not an error. Something like *"Nothing yet. FoxBot logs activity here once your stream starts."* Do not show a spinner indefinitely, and do not show an error for a legitimately empty result.

---

## Part C — Instrumentation

Emitting events means touching the live chat path. Both posting systems must be covered:

- `blaze_polling_worker` and the functions it calls
- the native connector's `_foxbot_live_send_chat_v2` (`app.py` ~21458)

Both are currently live. If only one is instrumented, the feed shows a partial picture and will be misleading in a way that's hard to diagnose.

**Event writes must never break chat.** Wrap every emit in try/except; a failed insert logs and moves on. Chat continues if the events table is unavailable, full, or missing.

---

## Build order

1. `foxbot_events` table + `onboarding_progress` table (migration only, nothing reading them yet)
2. Emit events from both chat paths, wrapped in try/except
3. `GET /api/foxbot/events` + wire the feed panel
4. `GET /api/foxbot/onboarding` + wire the checklist
5. Dismissal persistence

Steps 1–2 can ship independently and start accumulating data while 3–5 are built. That's deliberate: the feed will have real history the moment it goes live rather than starting empty.

---

## Verification

- **Checklist:** create a fresh test channel, confirm items flip as each is genuinely completed. Confirm a failed `/api/foxbot/onboarding` renders as unavailable, not as zero-complete.
- **Feed:** run a command in chat, confirm both the `command` and `bot_reply` events appear. Restart the Render service, confirm the feed still shows pre-restart history — this is the whole reason it isn't in-memory.
- **Listener events:** stop and start the listener, confirm both transitions appear in the feed.
- **Failure isolation:** with the events table dropped or renamed, confirm chat still works and the feed degrades to its empty state rather than erroring.

---

## Open questions — answer before building

1. Does Blaze's API expose channel moderator status for the bot account? Determines item 2's label.
2. How do commands, rewards, and giveaways currently persist? Determines items 3–5.
3. Is there any existing scheduler or background job runner, or is probabilistic cleanup on write the only option for retention?
4. Does `channel_id` or the creator handle make the better key? The prototype assumes one channel per view; confirm against how `connected_creators` is keyed.
