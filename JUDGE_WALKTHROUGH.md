# FoxBot AI Judge Walkthrough

This walkthrough demonstrates FoxBot's product experience and working backend without requiring access to private credentials.

## Recommended five-minute flow

### 1. Public product website

Open [FoxBot AI](https://foxbot-ai-chatbot.onrender.com).

Review the creator-focused product explanation, real feature set, onboarding flow, command examples, pricing, and FAQ. The website does not display invented creator, revenue, uptime, viewer, or command statistics.

### 2. Creator onboarding

Open [Get Started](https://foxbot-ai-chatbot.onrender.com/get-started).

Review the guided Blaze connection and creator setup experience.

### 3. FoxBot Studio

Open [FoxBot Studio](https://foxbot-ai-chatbot.onrender.com/admin).

Review:

- Bot Control
- Recognition
- Economy and FoxCoins
- Rewards and redemptions
- Giveaways
- Boss Battles
- Community Quests
- Stream Events and streaks
- OBS overlays
- Analytics
- Diagnostics

### 4. Safe command demonstration

Open [Live Chat Demo](https://foxbot-ai-chatbot.onrender.com/demo-chat) or the [Judge Demo](https://foxbot-ai-chatbot.onrender.com/demo).

Recommended non-destructive commands:

```text
!help
!balance
!shop
!profile
!bossstatus
!leaderboard
```

### 5. OBS overlays

Open the browser-source views:

- [Giveaway overlay](https://foxbot-ai-chatbot.onrender.com/overlay/giveaway)
- [Redemptions overlay](https://foxbot-ai-chatbot.onrender.com/overlay/redemptions)
- [Boss Battle overlay](https://foxbot-ai-chatbot.onrender.com/overlay/boss)

Recommended OBS size: 1920 by 1080.

### 6. Live backend proof

Open:

- [Project status](https://foxbot-ai-chatbot.onrender.com/project-status)
- [Live proof](https://foxbot-ai-chatbot.onrender.com/proof)
- [Storage health](https://foxbot-ai-chatbot.onrender.com/api/foxbot/storage/status)
- [Multi-channel targets](https://foxbot-ai-chatbot.onrender.com/api/foxbot/multichannel/targets)
- [Subscription configuration](https://foxbot-ai-chatbot.onrender.com/api/foxbot/subscription/config)

Expected storage result: Neon configured and connected. Expected channel targets: the FoxBot owner channel and the `foxbotai` subscription-control channel.

## Creator access model

1. A creator types `!joinfox` in the FoxBot Blaze profile chat.
2. FoxBot starts a seven-day free trial.
3. The creator can check the trial with `!access`.
4. The creator subscribes at `blaze.stream/foxbotai` for continued access.
5. The creator types `!verify` to activate subscription access.

## Known hosting limitation

The demonstration uses Render Free. Render can sleep after inactivity, so the background listener may need to be started after the service wakes. Creator and OAuth state remains durable in Neon PostgreSQL.
