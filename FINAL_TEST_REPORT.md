# FoxBot AI Final Test Report

Test date: 2026-07-17

Target: <https://foxbot-ai-chatbot.onrender.com>

## Result

- Passed: 44
- Warnings: 1
- Failed: 0

## Verified areas

- Core Python files compile successfully
- `psycopg` PostgreSQL driver imports successfully
- Git worktree was clean before testing
- Public website, onboarding, Studio, demos, status pages, overlays, and APIs returned HTTP 200
- No common mojibake markers were detected on the public website, onboarding, or Studio pages
- Neon PostgreSQL was configured and connected
- OAuth compatibility state was restored after a Render redeploy
- The active OAuth token source was `saved_oauth_file`
- Blaze client ID, client secret, and refresh token were available
- Seven-day trial and $5 monthly subscription configuration matched the product website
- Owner and `foxbotai` subscription-control channels resolved
- No multi-channel targets were unresolved

## Route coverage

The following routes returned HTTP 200:

```text
/
/get-started
/admin
/demo-chat
/connected-creators
/project-status
/smoke-test
/proof
/overlay/giveaway
/overlay/redemptions
/overlay/boss
/api/foxbot/v1/status
/api/blaze/native/status
/api/blaze/oauth/status
/api/blaze/listener/status
/api/foxbot/storage/status
/api/foxbot/token-source
/api/foxbot/multichannel/status
/api/foxbot/multichannel/targets
/api/foxbot/subscription/config
/api/connected-creators
```

## Warning

The multi-channel listener was not running at the moment of the diagnostic. No listener error was present. This is expected on Render Free because the service can sleep or restart after inactivity. Durable creator and OAuth state remained available through Neon.

## Conclusion

FoxBot passed the application, route, persistence, OAuth, access, encoding, and multi-channel target checks with no failures. The remaining warning is a hosting-tier availability limitation rather than an application or data-integrity failure.
