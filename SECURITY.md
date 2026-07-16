# FoxBot AI Security Notes

## Secret handling

Never commit or share:

- Blaze client secrets
- Blaze access or refresh tokens
- Neon `DATABASE_URL` values
- Render API keys
- `.env` files

Use environment variables locally and in Render. Public status endpoints expose only booleans, masked values, and source labels.

If a credential is pasted into a chat, issue, screenshot, commit, or log, rotate it immediately. Removing a secret from the latest file is not sufficient if it remains in Git history.

## OAuth storage

Fresh Blaze OAuth tokens are written to Neon PostgreSQL through `services/postgres_state.py`. Compatibility JSON is restored on each application start so existing integration code continues to work. Tokens are not committed to Git.

## Database access

- Use Neon's pooled TLS connection string.
- Store it only in `DATABASE_URL`.
- Rotate the database role password if the URI is exposed.
- Keep the database project and Render account protected with multi-factor authentication.

## Public demonstration deployment

FoxBot's live deployment is intended for product demonstration and judging. Avoid placing private viewer information or production financial data in the demo instance.

## Reporting a security issue

Do not open a public issue containing credentials or exploit details. Contact the repository owner privately and rotate any affected credentials immediately.
