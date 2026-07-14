# FoxBot AI Live Test Report

Test date: July 14, 2026  
Environment: Render production  
Base URL: https://foxbot-ai-chatbot.onrender.com

## Automated Route Smoke Test

| Route | Status | Result |
|---|---:|---|
| `/` | 200 | PASS |
| `/get-started` | 200 | PASS |
| `/admin` | 200 | PASS |
| `/demo-chat` | 200 | PASS |
| `/connected-creators` | 200 | PASS |
| `/project-status` | 200 | PASS |
| `/smoke-test` | 200 | PASS |
| `/proof` | 200 | PASS |
| `/overlay/giveaway` | 200 | PASS |
| `/overlay/redemptions` | 200 | PASS |
| `/overlay/boss` | 200 | PASS |
| `/api/blaze/oauth/status` | 200 | PASS |
| `/api/blaze/listener/status` | 200 | PASS |
| `/api/connected-creators` | 200 | PASS |

## Result

All tested public pages, OBS overlay routes, and status APIs returned HTTP 200 in production.

## Scope

This report verifies route availability. OAuth completion, live chat mutations, administrative actions, and OBS broadcast rendering require controlled manual testing with an authorized Blaze account.