# FoxBot Blaze OAuth Setup

FoxBot has its own Blaze OAuth routes.

## Render Env Vars Needed First

BLAZE_CLIENT_ID
BLAZE_CLIENT_SECRET
BLAZE_REDIRECT_URI=https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback

## Login URL

After Render redeploys, open:

https://foxbot-ai-chatbot.onrender.com/auth/blaze/login

Log in as the official FoxBot Blaze profile.

## Callback

Blaze redirects to:

https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback

The callback page will show:

BLAZE_ACCESS_TOKEN
BLAZE_REFRESH_TOKEN

Add those to Render Environment.

## Safety

Do not paste tokens into chat.
Do not share access tokens publicly.
Keep FOXBOT_BLAZE_AUTO_SEND=false until event reading is verified.
