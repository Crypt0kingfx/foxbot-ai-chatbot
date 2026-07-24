# FoxBot Studio — UX Simplification Spec

**Scope:** Connection wizard rebuild + admin dashboard information architecture
**Stack:** FastAPI · Neon Postgres · Render · Windows/PowerShell workflow
**Brand:** Vice City neon — `#B026FF` purple (primary), `#FF007F` pink (accent)

---

## Part 1 — Connection Wizard

Replaces the current single-page `/connect`. Four screens, one job each. The user should never see the word "OAuth" or "scope" unless they go looking for it.

### Copy rules for this flow

Applied throughout, worth stating up front so new screens stay consistent:

- Buttons say what happens: "Connect Blaze account", not "Submit" or "Continue".
- An action keeps its name end-to-end. The button that says "Connect" produces a screen that says "Connected".
- Errors state what broke and the fix. Never a raw exception, never an apology.
- Name things the user controls. "FoxBot posts as @foxbotai", not "sender identity resolved".

---

### Screen 1 — Start

**Route:** `GET /connect`
**State:** no session, or session with no linked Blaze account

| Element | Copy |
|---|---|
| Eyebrow | `Step 1 of 3` |
| Headline | `Connect FoxBot to your stream` |
| Subhead | `Takes about 30 seconds. You'll sign in with Blaze and approve FoxBot once.` |
| Primary CTA | `Connect Blaze account` |
| Disclosure link | `What can FoxBot do with my account?` |

**Disclosure panel** (collapsed by default, expands inline — no modal, no new page):

> FoxBot needs permission to:
> - Read your channel info and viewer list
> - Read and post messages in your chat
> - Time out or remove messages when you ask it to
> - Stay connected while you're offline, so it's ready when you go live
>
> FoxBot never posts from your personal account. It posts as its own bot profile.

That maps to `users.read`, `channel.moderate`, `users.bot`, `offline.access` respectively — but the user doesn't need the scope strings, so don't show them.

**Progress indicator:** three dots or bars, step 1 filled. Persist across all screens.

---

### Screen 2 — Authorizing

**Route:** OAuth redirect out to Blaze, then `GET /connect/callback`

While the callback is exchanging the code:

| Element | Copy |
|---|---|
| Eyebrow | `Step 2 of 3` |
| Headline | `Setting up FoxBot` |
| Body | `Linking your account and waking up the bot. This takes a few seconds.` |

Show an indeterminate progress state in brand purple. Do **not** show a percentage — it's a lie and users notice.

**Timeout:** if the callback hasn't resolved in 15s, fall through to error state E4 rather than spinning forever.

---

### Screen 3 — Connected

**Route:** `GET /connect/done`
**Trigger:** successful token exchange + persistence to Postgres

| Element | Copy |
|---|---|
| Eyebrow | `Step 3 of 3` |
| Headline | `FoxBot is connected` |
| Body | `FoxBot is watching your chat now.` |
| Verify CTA | `Test connection` |
| Secondary CTA | `Go to dashboard` |

**The "Test connection" button is the most important element on this screen.** It answers the question every user has at this moment: *did that actually work?*

On click → `GET /api/foxbot/sender-identity`

| Response | Display |
|---|---|
| Bot profile returned, matches expected bot account | Green check · `Connected. FoxBot will post as @{bot_handle}.` |
| Bot profile returned, but resolves to the user's personal account | Amber warning · `FoxBot is set to post as your personal account, not the bot. Reconnect to fix this.` + `Reconnect` button |
| Endpoint errors or times out | See error state E3 |

That middle case is the regression guard for the bug where FoxBot posted from the personal account instead of the bot profile. Surfacing it here means it gets caught at connect time by the user, not three days later in a stream.

**Next steps block**, below the verify result — three items max:

1. `Type !help in your chat` — confirms FoxBot is reading and responding
2. `Turn on your first reward` — deep link to Engagement → Rewards
3. `Set up a stream event` — deep link to Stream Tools → Stream Events

---

### Error states

Every one of these needs a real screen. Right now the failure paths are where people are most likely to give up.

**E1 — User declined authorization on Blaze**
> **Connection cancelled**
> You didn't approve FoxBot on Blaze, so nothing was connected. No changes were made to your account.
> `[Try again]`

**E2 — Token expired or revoked** (surfaced from anywhere in the app, not just `/connect`)
> **FoxBot lost access to your account**
> Your Blaze connection expired. Reconnecting takes about 10 seconds and FoxBot will pick up where it left off.
> `[Reconnect]`

**E3 — Verification failed**
> **Couldn't check the connection**
> FoxBot connected, but the test didn't come back. This usually clears on its own.
> `[Test again]` `[Go to dashboard]`

Important: do not tell the user the connection failed here. It didn't — the *check* failed. Overstating this sends people into a reconnect loop for no reason.

**E4 — Token exchange failed / timeout**
> **Connection didn't complete**
> Blaze didn't finish the handshake. This is usually temporary.
> `[Try again]`

Log the underlying exception server-side with the correlation ID. Show the ID in small mono text at the bottom of E4 only — it's useful in support DMs and invisible to everyone else.

---

## Part 2 — Admin Dashboard Information Architecture

Fourteen flat tabs is the core complexity problem. No one builds a mental model of fourteen peers. Collapsing to five groups makes the same surface area feel roughly a third the size.

### Proposed structure

```
HOME
├── Overview          (embedded /foxbot-control + Getting Started checklist)
└── Connections       (existing /connections dashboard)

ENGAGEMENT
├── Rewards
├── Streaks
├── Quests
└── Boss Battle

STREAM TOOLS
├── Stream Events
├── Commands
└── Moderation

AI STUDIO
├── [Generator 1]
└── [Generator 2]

SETTINGS
├── Bot profile
├── Account & tokens
└── Advanced
```

That accounts for the tabs I know about. You have fourteen — slot the remainder into the group where a user would *look* for them, not where they live in the codebase. If a tab doesn't obviously belong to a group, that's usually a signal it belongs under Settings or should be merged into a sibling.

### Implementation notes

- **Sidebar, not a tab row.** Fourteen tabs can't fit horizontally without wrapping or scrolling, both of which hide options. A vertical sidebar with collapsible groups shows everything at a glance.
- **Groups collapse, and the state persists** per user. Someone who never touches AI Studio should be able to fold it away permanently.
- **Only one group expands by default** on first load — Home. Everything else starts collapsed.
- **One-line description at the top of every tab.** Sentence case, plain verb, says what the tab does. `Set up rewards viewers can claim with channel points.` Extends the tooltip system already in place.
- **Route compatibility:** keep existing tab routes working and redirect to the new nested paths, so any links already shared in Discord don't break.

### The "Advanced" toggle

Each system's settings page splits into two zones: what a new user needs, and everything else. Default view shows the first zone only, with a single `Show advanced settings` toggle at the bottom. Toggle state persists per user, per page.

The test for which zone something belongs in: **would a user need to change this before the feature works?** If no, it's advanced. Most configuration fails this test — good defaults mean the feature works untouched and users tune it later, if ever.

---

## Part 3 — Getting Started Checklist

Lives at the top of Home → Overview. This is what replaces "wall of tabs" with "here's your path."

### Items

| # | Label | Complete when | Deep link |
|---|---|---|---|
| 1 | Connect your Blaze account | Valid token in DB for this user | `/connect` |
| 2 | Confirm FoxBot's bot profile | `sender-identity` returns bot account, not personal | `/settings/bot-profile` |
| 3 | Turn on your first command | ≥1 command enabled | `/stream-tools/commands` |
| 4 | Set up a reward | ≥1 active reward | `/engagement/rewards` |
| 5 | Go live with FoxBot | ≥1 message posted by bot to chat | — (auto-completes) |

### Behavior

- Items auto-complete from real state. Never ask the user to tick a box themselves — it desyncs from reality immediately.
- Progress shown as `2 of 5` plus a filled bar in brand purple.
- Dismissible via `Hide this` — but **only after item 1 is complete**, so nobody buries the connection step.
- Once all five complete: show a brief completion state, then auto-hide on next load. Don't make them dismiss it.
- Persist dismissal + completion in Postgres, not localStorage. It needs to survive across devices.

### Schema sketch

```sql
CREATE TABLE onboarding_progress (
    user_id       TEXT PRIMARY KEY,
    dismissed     BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Item completion derives from existing tables rather than being stored — the only persisted state is dismissal and overall completion. Storing per-item flags creates a second source of truth that drifts.

---

## Part 4 — Build Order

**1. Connection wizard.** Highest leverage by a wide margin. If people can't get connected, nothing downstream matters. Self-contained — touches `/connect`, the callback handler, and `/api/foxbot/sender-identity`, which already exists.

**2. Sidebar regrouping.** Mostly layout refactor plus routing, minimal new logic. Big perceived-complexity win for the effort. Ship the route redirects in the same change so nothing breaks.

**3. Per-tab descriptions + Advanced toggles.** Small, incremental, can land tab by tab. Good filler work between larger pieces.

**4. Getting Started checklist.** Deliberately last — it routes new users directly into features, so every feature it links to needs to be working first. Given Rewards is mid-fix and Streaks is queued, building this now would send new users straight into the broken paths. Either wait, or ship it with items 1–3 only and add the Rewards item once that's stable.

---

## Suggested verification per stage

Consistent with the compile-verify workflow:

- **Wizard:** walk all four screens plus all four error states manually. Force E2 by revoking the token in Postgres directly. Force the personal-account warning on Screen 3 by pointing sender identity at the personal account — that path needs to be proven, not assumed.
- **Sidebar:** hit every old tab route and confirm it redirects. All 14 reachable in ≤2 clicks.
- **Checklist:** create a fresh test user, confirm items complete in order from real state, confirm dismissal survives a logout/login cycle.
