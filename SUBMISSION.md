# FoxBot AI - Submission Summary

## Project

**FoxBot AI: The Creator Command Center for Blaze**

- Live application: <https://foxbot-ai-chatbot.onrender.com>
- GitHub repository: <https://github.com/Crypt0kingfx/foxbot-ai-chatbot>
- Judge walkthrough: [JUDGE_WALKTHROUGH.md](JUDGE_WALKTHROUGH.md)
- Final test report: [FINAL_TEST_REPORT.md](FINAL_TEST_REPORT.md)

## Short description

FoxBot AI is a Blaze-connected creator platform that combines live chat automation, viewer recognition, FoxCoins and rewards, giveaways, community events, OBS overlays, creator analytics, onboarding, subscription access, and operational diagnostics in one FastAPI application.

## What was built

- A polished public SaaS-style website
- Guided creator onboarding
- Blaze OAuth and refreshable token persistence
- A multi-channel Blaze chat listener
- FoxBot Studio creator dashboard
- Recognition for community events
- FoxCoins economy, rewards, and redemptions
- Giveaways, boss battles, quests, streaks, and arcade games
- OBS giveaway, redemption, and boss overlays
- Creator trial and Blaze subscription verification
- Durable creator and OAuth state in Neon PostgreSQL
- Live status, proof, smoke-test, and diagnostic pages

## Best pages to review

| Page | URL |
|---|---|
| Public website | <https://foxbot-ai-chatbot.onrender.com> |
| Creator onboarding | <https://foxbot-ai-chatbot.onrender.com/get-started> |
| FoxBot Studio | <https://foxbot-ai-chatbot.onrender.com/admin> |
| Live chat demo | <https://foxbot-ai-chatbot.onrender.com/demo-chat> |
| Judge demo | <https://foxbot-ai-chatbot.onrender.com/demo> |
| Project status | <https://foxbot-ai-chatbot.onrender.com/project-status> |
| Smoke test | <https://foxbot-ai-chatbot.onrender.com/smoke-test> |
| Live proof | <https://foxbot-ai-chatbot.onrender.com/proof> |

## Recommended commands

Use the demo interface for a safe walkthrough:

```text
!help
!balance
!shop
!profile
!bossstatus
!leaderboard
```

Creator access commands:

```text
!join
!access
!verify
```

## Creator access model

- Seven-day free trial
- Trial starts with `!join` in the FoxBot Blaze profile chat
- Continued access through a $5 monthly Blaze subscription
- Subscription verification with `!verify`
- Owner access remains active without a subscription

## Technical architecture

- Backend: Python and FastAPI
- Frontend: HTML, CSS, and JavaScript
- Platform integration: Blaze OAuth and Blaze chat APIs
- Persistent state: Neon PostgreSQL with local JSON fallback
- Hosting: Render
- Streaming integration: OBS browser-source overlays

## Evidence of completion

The final read-only diagnostic produced:

- 44 passing checks
- 1 expected hosting-tier warning
- 0 failures

It verified the local build, public routes, UI encoding, Neon connectivity, OAuth restoration, token priority, creator access configuration, multi-channel targets, overlays, and creator APIs.

## Known limitation

The live demonstration uses Render Free. The instance can sleep after inactivity, stopping background polling until the service wakes. Durable OAuth and creator access data remains available through Neon PostgreSQL.

## Why FoxBot matters

FoxBot turns Blaze chat into an interactive creator community. Instead of offering only command replies, it provides a unified product for automation, recognition, rewards, events, overlays, onboarding, creator access, and diagnostics.
