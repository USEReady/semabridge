# SemaBridge Databricks App (template)

**Status: template. Not yet validated against a Databricks workspace.**

Databricks Apps run source code, not images. `app.yaml` starts
`semabridge.platform.launch`, which serves the prebuilt web app and the API on
`DATABRICKS_APP_PORT`. Users are signed in by Databricks (the app trusts
`X-Forwarded-Email` via `/auth/platform-login`). Targets: the workspace's
Unity Catalog only.

## Steps
1. Build the web app into the source tree: `cd frontend && npm ci && npm run build`.
2. Create a Lakebase (PostgreSQL) instance and a secret scope `semabridge` with
   `database-url` (`postgresql+psycopg2://...?sslmode=require`), `jwt-secret-key`,
   `credential-encryption-key` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
3. `databricks bundle deploy -t prod --var warehouse_id=<id>` from this folder,
   then `databricks bundle run semabridge`.

## Validate before client use
- Platform login with `X-Forwarded-Email`; admins via `SEMABRIDGE_PLATFORM_ADMINS`.
- The Lakebase connection string: if the workspace issues short-lived OAuth
  database tokens instead of a password, the database URL needs a token
  refresher (follow-up).
- Deploying metric views with the app's service principal (or the user's
  on-behalf-of token) instead of a stored PAT (follow-up).
