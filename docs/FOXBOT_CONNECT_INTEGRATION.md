# FoxBot Connect / Blaze Chat Bridge

FoxBot Connect lets Blaze viewers connect to FoxBot through chat commands.

## Live Render URLs

FoxBot Studio:

https://foxbot-ai-chatbot.onrender.com/admin

Connected Creators page:

https://foxbot-ai-chatbot.onrender.com/connected-creators

Connected Creators API:

https://foxbot-ai-chatbot.onrender.com/api/connected-creators

Blaze Chat Bridge:

https://foxbot-ai-chatbot.onrender.com/api/blaze/chat

Bridge browser test:

https://foxbot-ai-chatbot.onrender.com/api/blaze/chat/test?message=!connect&username=testviewer

## Chat Commands

Supported commands:

- !connect
- !profile
- !rank
- !disconnect

## Blaze Connector Flow

The Blaze-side connector should watch Blaze chat from the FoxBot bot profile or authorized creator profile.

When a viewer sends a chat message, send it to FoxBot:

POST /api/blaze/chat

Example JSON:

{
  "username": "viewername",
  "message": "!connect"
}

FoxBot returns:

{
  "ok": true,
  "handled": true,
  "username": "viewername",
  "message": "!connect",
  "reply": "🦊 @viewername is now connected to FoxBot Connect! +25 FoxCoins..."
}

The Blaze connector should post the `reply` value back into Blaze chat.

## Important Safety Rule

Do not ask viewers for Blaze passwords or private login info.

The public verification flow should be:

1. Viewer follows the FoxBot Blaze profile.
2. Viewer types !connect in Blaze chat.
3. FoxBot saves them as connected.
4. Later public follower sync can mark follow_status as verified.

## Current Status

FoxBot side is ready.

The remaining missing piece is the actual Blaze-side connector that can:

- Read Blaze chat messages
- Send messages to /api/blaze/chat
- Post FoxBot's returned reply back into Blaze chat
