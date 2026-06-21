# FoxBot AI Chatbot ? Builder Challenge Submission

## Project Name

FoxBot AI Chatbot

## Live App

https://foxbot-ai-chatbot.onrender.com

## GitHub

https://github.com/Crypt0kingfx/foxbot-ai-chatbot

## Short Description

FoxBot AI Chatbot is a Blaze-connected creator assistant for livestreamers. It connects to Blaze chat, responds to commands, runs giveaways, powers a FoxCoins economy, supports mini games, shows OBS overlays, and gives creators tools to keep their audience engaged.

## What I Built

I built a working Blaze chatbot that can log in with Blaze OAuth, listen to live Blaze chat, and reply to viewer commands.

I also expanded it into a full creator engagement system with:

- Giveaways
- OBS overlays
- Viewer leaderboards
- FoxCoins points economy
- Reward shop
- Redemption queue
- Boss battle game
- Arcade mini games
- Custom commands
- Stream info tools
- Admin controls
- Smoke test page
- Judge demo page

## Best Pages to Review

Judge demo page:
https://foxbot-ai-chatbot.onrender.com/demo

Smoke test page:
https://foxbot-ai-chatbot.onrender.com/smoke-test

Economy dashboard:
https://foxbot-ai-chatbot.onrender.com/economy

Live proof endpoint:
https://foxbot-ai-chatbot.onrender.com/proof

OBS overlays:
https://foxbot-ai-chatbot.onrender.com/overlay/giveaway

https://foxbot-ai-chatbot.onrender.com/overlay/redemptions

https://foxbot-ai-chatbot.onrender.com/overlay/boss

## Main Commands to Test

!help
!daily
!foxhunt
!balance
!shop
!redeem hug
!coinleaderboard
!arcade
!coinflip
!roll 20
!rps rock
!startboss Cyber Fox Dragon
!attack
!bossleaderboard
!socials
!hugs
!goodnight

## Admin Commands

!giveaway
!pickwinner
!givepoints avisi 100
!takepoints avisi 50
!addreward hydrate 25 @{username} redeemed hydrate. Drink water!
!delreward hydrate
!startboss Cyber Fox Dragon
!endboss
!setgame Off The Grid
!settitle Playing Off The Grid with FoxBot live
!shoutout avisi
!setcooldown foxhunt 60
!clearcooldowns
!clearredeems
!goodnight

## Demo Flow for Judges

1. Open the Judge Demo page.
2. Click !help to show the bot commands.
3. Click !daily and !foxhunt to earn FoxCoins.
4. Click !balance to show the user's balance.
5. Click !shop and !redeem hug to redeem a reward.
6. Open the Redemptions Overlay to see the redemption appear.
7. Start a boss battle with !startboss Cyber Fox Dragon.
8. Click !attack and open the Boss Overlay.
9. Open the Giveaway Overlay.
10. Open /proof to verify Blaze connection and listener status.
11. Open /smoke-test to check that the major commands are still working.

## Why This Matters for Blaze Creators

FoxBot gives creators more ways to keep chat active. Viewers can earn points, redeem rewards, join giveaways, fight bosses, play mini games, use custom commands, and interact with stream overlays.

Instead of only being a chatbot, FoxBot acts like a creator engagement layer for Blaze streams.

## Technical Details

- Backend: Python + FastAPI
- Hosting: Render
- API: Blaze OAuth and Blaze chat endpoints
- Frontend: HTML, CSS, JavaScript
- Storage: JSON persistence with foxbot_data.json
- OBS support: Browser-source overlay pages

## Final Status

FoxBot is deployed and working as a live Blaze-connected creator bot with multiple overlays, games, economy features, admin tools, and judge demo pages.
