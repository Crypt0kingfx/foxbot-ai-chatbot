# FoxBot Native Blaze Compatibility

This upgrades FoxBot to be compatible with the same Blaze-style flow used by BLAZEIAN-BOT, without editing BLAZEIAN-BOT.

## What FoxBot now supports

- BLAZEIAN-style event payload parsing
- Chat command ingest
- Follow/vote/sub/gift/raid event recognition scaffold
- Blaze chat send function
- Native websocket listener scaffold
- Public FoxBot Connect profile instructions

## Important URLs

Public start page:

/foxbot-connect-start

Connected Creators:

/connected-creators

Test Panel:

/foxbot-connect-test

Native status:

/api/blaze/native/status

Native event ingest:

POST /api/blaze/native/event

Native send test:

POST /api/blaze/native/send

Native listener start:

POST /api/blaze/native/start

Native listener stop:

POST /api/blaze/native/stop

## Required Render Env Vars For Live Blaze

These are required before the native listener can truly go live:

BLAZE_CLIENT_ID
BLAZE_ACCESS_TOKEN
BLAZE_CHANNEL_ID

Recommended:

BLAZE_BOT_USER_ID
BLAZE_APP_ACCESS_TOKEN
BLAZE_REFRESH_TOKEN
FOXBOT_BLAZE_PROFILE_HANDLE
FOXBOT_BLAZE_NATIVE_ENABLED=true

Send replies automatically only when ready:

FOXBOT_BLAZE_AUTO_SEND=true

Keep FOXBOT_BLAZE_AUTO_SEND=false while testing if you do not want FoxBot posting into real chat yet.

## Profile Connection Flow

1. Create a dedicated FoxBot Blaze profile.
2. Set FOXBOT_BLAZE_PROFILE_HANDLE to that handle in Render.
3. Users follow that profile.
4. Users type !connect in supported Blaze chat.
5. FoxBot saves them as connected creators.
6. Follow events can mark follow_status as verified_public_follow.

## Safety

Never put passwords into code.

FoxBot should never ask users for Blaze passwords, private keys, seed phrases, or login codes.
