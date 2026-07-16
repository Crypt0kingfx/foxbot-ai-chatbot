# FoxBot AI Installation Guide

## Requirements

- Python 3.11 or newer
- Git
- A Blaze OAuth application
- Optional Neon PostgreSQL database for durable production state

## Clone and install

```powershell
git clone https://github.com/Crypt0kingfx/foxbot-ai-chatbot.git
Set-Location .\foxbot-ai-chatbot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` for local development:

```powershell
Copy-Item .\.env.example .\.env
```

Fill in only your own credentials. Never commit `.env`, OAuth tokens, database connection strings, or Render API keys.

Required Blaze values:

```text
BLAZE_CLIENT_ID
BLAZE_CLIENT_SECRET
BLAZE_REDIRECT_URI
BLAZE_CHANNEL_ID
BLAZE_CHANNEL_SLUG
```

Recommended production values:

```text
DATABASE_URL
FOXBOT_BLAZE_PROFILE_HANDLE
FOXBOT_SUBSCRIPTION_CHANNEL_SLUG
FOXBOT_MULTI_CHANNEL_LIMIT
FOXBOT_MULTI_CHANNEL_POLL_SECONDS
```

The live callback URI is:

```text
https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback
```

The callback configured in Blaze must match exactly.

## Verify the installation

```powershell
.\.venv\Scripts\python.exe -m py_compile `
    .\app.py `
    .\services\creator_access.py `
    .\services\storage_paths.py `
    .\services\postgres_state.py
```

## Run locally

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Connect Blaze

1. Open `/auth/blaze/login`.
2. Complete authorization in the same browser session.
3. Confirm `/api/blaze/oauth/status` reports access and refresh tokens without sharing their values.
4. Confirm `/api/foxbot/token-source` reports `saved_oauth_file`.

## Verify durable storage

With `DATABASE_URL` configured, open `/api/foxbot/storage/status` and confirm:

```json
{
  "backend": "neon_postgres",
  "database": {
    "configured": true,
    "connected": true
  }
}
```

## Render deployment

Use the following start command:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Configure all secrets in Render's Environment page. After deployment, reconnect Blaze once so fresh OAuth tokens are stored in Neon.

## Production verification

Check these routes after every deployment:

```text
/
/get-started
/admin
/demo-chat
/api/foxbot/storage/status
/api/foxbot/token-source
/api/foxbot/multichannel/targets
/api/foxbot/multichannel/status
```

On Render Free, the listener can stop when the service sleeps. The website and persistent state remain available after the service wakes.
