# FoxBot AI Chatbot

FoxBot AI Chatbot is a Blaze-connected creator assistant built for the Blaze Builder Challenge.

FoxBot helps streamers automate community engagement by responding to chat commands, managing giveaways, answering common questions, and posting directly into Blaze chat through the Blaze API.

## Live App

https://foxbot-ai-chatbot.onrender.com

## What FoxBot Does

FoxBot currently supports:

* Blaze OAuth login
* Real Blaze channel lookup
* Sending messages into Blaze chat
* Running chatbot commands from the web app
* Polling recent Blaze chat messages
* Automatically responding to commands in live chat
* Giveaway entry tracking
* Duplicate giveaway entry protection
* Random winner selection
* A creator control dashboard

## Main Commands

Viewers can type:

* `!help` — shows available commands
* `!schedule` — shows the creator schedule
* `!faq` — explains what FoxBot does
* `!giveaway` — starts a new giveaway
* `!enter` — enters the viewer into the giveaway
* `!entries` — shows current giveaway entries
* `!pickwinner` — randomly selects a giveaway winner
* `!ask` — demo AI response mode

## Dashboard

FoxBot includes a control dashboard:

https://foxbot-ai-chatbot.onrender.com/dashboard

The dashboard lets the creator:

* Login with Blaze
* Start the chat listener
* Stop the listener
* Check listener status
* Check recent chat messages
* Send a test Blaze chat message
* Run a test command

## How It Works

FoxBot connects to Blaze using OAuth. Once the creator logs in, FoxBot receives permission to read basic user data and moderate the creator’s channel.

FoxBot then uses the Blaze API to:

1. Find the creator’s Blaze channel.
2. Send messages into the creator’s Blaze chat.
3. Check recent chat messages.
4. Detect commands starting with `!`.
5. Generate a FoxBot response.
6. Post the response back into Blaze chat.

## Tech Stack

* Python
* FastAPI
* Render
* Blaze OAuth
* Blaze Chat API
* HTML, CSS, and JavaScript

## Project Goal

The goal of FoxBot is to give Blaze creators an easy chatbot assistant that can improve stream engagement, automate repeated answers, and run simple community tools like giveaways.

This version proves the core concept: FoxBot can connect to a real Blaze account, post into real Blaze chat, and respond to real chat commands.
