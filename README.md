# FoxBot AI Chatbot

FoxBot AI Chatbot is a Blaze-connected creator assistant built for live stream engagement.

Live app: https://foxbot-ai-chatbot.onrender.com
Judge demo: https://foxbot-ai-chatbot.onrender.com/demo
Dashboard: https://foxbot-ai-chatbot.onrender.com/dashboard
Smoke test: https://foxbot-ai-chatbot.onrender.com/smoke-test
Economy dashboard: https://foxbot-ai-chatbot.onrender.com/economy

## What FoxBot Does

FoxBot connects to Blaze, listens to live chat, responds to commands, runs giveaways, tracks viewer engagement, powers a FoxCoins economy, supports custom commands, and provides OBS overlays for live streams.

The goal is to give Blaze creators a fun all-in-one engagement bot that can help keep chat active during streams.

## Main Features

- Blaze OAuth login
- Blaze live chat polling listener
- Live command replies in Blaze chat
- Giveaway system with entries and winner picking
- OBS giveaway overlay
- Viewer stats and leaderboard
- Creator socials command
- Admin-only shoutout command
- Bot personality modes: hype, chill, and pro
- Custom commands created from chat
- Stream info commands for game, title, and lurk status
- FoxBot Arcade mini games
- FoxCoins points economy
- Reward shop and redemption system
- OBS redemptions overlay
- Persistent data storage using foxbot_data.json
- Command cooldowns
- Admin economy dashboard
- Boss battle mini game
- OBS boss battle overlay
- Goodnight / end stream button
- Smoke test page for checking key commands
- Judge demo page with one-click tests

## Live Pages

Home: https://foxbot-ai-chatbot.onrender.com

Dashboard: https://foxbot-ai-chatbot.onrender.com/dashboard

Judge Demo: https://foxbot-ai-chatbot.onrender.com/demo

Features: https://foxbot-ai-chatbot.onrender.com/features

Economy Dashboard: https://foxbot-ai-chatbot.onrender.com/economy

Smoke Test: https://foxbot-ai-chatbot.onrender.com/smoke-test

Goodnight Button: https://foxbot-ai-chatbot.onrender.com/goodnight

Giveaway Overlay: https://foxbot-ai-chatbot.onrender.com/overlay/giveaway

Redemptions Overlay: https://foxbot-ai-chatbot.onrender.com/overlay/redemptions

Boss Battle Overlay: https://foxbot-ai-chatbot.onrender.com/overlay/boss

Live Proof: https://foxbot-ai-chatbot.onrender.com/proof

## Core Viewer Commands

!help
!schedule
!faq
!socials
!mode
!stats
!leaderboard
!hugs

## Giveaway Commands

!giveaway
!enter
!entries
!pickwinner

Admin only: !giveaway and !pickwinner

## FoxBot Arcade Commands

!arcade
!coinflip
!roll
!roll 20
!8ball Will FoxBot win?
!rps rock
!foxhunt

## FoxCoins Economy Commands

!daily
!balance
!points
!foxcoins
!coinleaderboard
!shop
!redeem hug
!redeem hype
!redeem flex
!redeem mysterybox
!redeem sponsor

Admin economy commands:

!givepoints username amount
!takepoints username amount
!addreward name cost message
!delreward name

## Boss Battle Commands

!boss
!bossstatus
!startboss Cyber Fox Dragon
!attack
!powerattack
!bossleaderboard
!endboss

Admin only: !startboss and !endboss

## Stream Info Commands

!game
!setgame Off The Grid
!title
!settitle Playing Off The Grid with FoxBot live
!lurk
!unlurk
!lurkers

Admin only: !setgame and !settitle

## Custom Commands

!commands
!addcmd discord Join the Discord here: your-link
!discord
!delcmd discord

Admins can create custom commands live from chat.

## Admin / Creator Tools

!shoutout username
!goodnight
!endstream
!setcooldown foxhunt 60
!clearcooldowns
!clearredeems

## OBS Browser Source Overlays

Recommended OBS browser source size:

Width: 1920
Height: 1080

Overlay URLs:

https://foxbot-ai-chatbot.onrender.com/overlay/giveaway

https://foxbot-ai-chatbot.onrender.com/overlay/redemptions

https://foxbot-ai-chatbot.onrender.com/overlay/boss

## Judge Demo Flow

1. Open /demo
2. Click !help
3. Click !daily
4. Click !foxhunt
5. Click !shop
6. Click !redeem hug
7. Open /overlay/redemptions
8. Click !startboss Cyber Fox Dragon
9. Click !attack
10. Open /overlay/boss
11. Open /overlay/giveaway
12. Open /proof
13. Open /smoke-test and run the smoke test

## Tech Stack

- Python
- FastAPI
- Requests
- Blaze API
- Blaze OAuth
- HTML / CSS / JavaScript
- Render deployment
- GitHub

## Project Goal

FoxBot is built to help Blaze creators turn their chat into an interactive community experience with live commands, rewards, overlays, games, and engagement tools.
