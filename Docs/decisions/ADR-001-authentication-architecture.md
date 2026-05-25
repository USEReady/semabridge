# ADR-001: Three-Layer Authentication Architecture

## Status

Accepted

## Date

2026-03-18

## Context

Semabridge started as a single-user CLI tool. As the project evolved into a multi-user web application with a FastAPI backend and React frontend, we needed an authentication system that could:

1. Authenticate human users accessing the web UI
2. Manage connector credentials for three external platforms (Fabric, Snowflake, Databricks) — each with different auth protocols
3. Isolate user data in a multi-tenant environment where multiple users share the same PostgreSQL database
4. Support both interactive (device code, browser OAuth) and automated (scheduled sync) execution modes
5. Enable progressive rollout — the system must work with auth disabled during development

## Decision

Implement a **three-layer architecture** separating User Identity, Connector Credentials, and Tenant Isolation.

### Layer 1: JWT-based User Authentication

- HS256-signed JWTs with 15-minute access tokens and 7-day refresh tokens
- Refresh token rotation (one-time-use) to prevent replay attacks
- Global `AuthMiddleware` toggled by `AUTH_ENABLED` env var
- Auto-login endpoint for frictionless development

### Layer 2: Encrypted Credential Bundles per Account

- Each external connection stored as an `Account` row with Fernet-encrypted credential bundles
- Thread-safe credential builders that return Pydantic config objects (no `os.environ` mutation)
- Automatic token refresh before pipeline execution (MSAL for Fabric, OAuth2 for Databricks/Snowflake)

### Layer 3: PostgreSQL Row Level Security

- RLS policy on `accounts` table using `SET LOCAL app.current_user_id`
- Defense-in-depth: even if application code omits ownership filters, the database enforces isolation

## Alternatives Considered

### OAuth2 / OIDC with External IdP (Auth0, Clerk)

- **Pros:** Industry-standard, battle-tested, supports SSO/MFA out of the box
- **Cons:** Adds external dependency, requires internet connectivity, adds cost, over-engineered for current 1–5 user deployment
- **Rejected:** Will reconsider when user base exceeds 50 or enterprise SSO is required

### Session-based Authentication (Server-side sessions)

- **Pros:** Simpler to implement, no token management on client
- **Cons:** Requires sticky sessions or shared session store, doesn't work well with API-first architecture
- **Rejected:** JWT is better suited for stateless API backends with React frontends

### Per-User Environment Variables

- **Pros:** Simple, works with existing connector code that reads from `os.environ`
- **Cons:** Not thread-safe, causes credential bleed between concurrent requests
- **Rejected:** Kept as CLI-only fallback (`scoped_account_env`), but API path uses thread-safe config builders

## Consequences

- All connector code must accept Pydantic config objects (not just read from `os.environ`) for multi-user safety
- The encryption key (`SEMABRIDGE_ENCRYPTION_KEY`) becomes a critical secret — losing it invalidates all stored credentials
- PostgreSQL is required for RLS (SQLite/DuckDB paths skip this layer)
- New connectors must implement: env-var map, config builder, and token refresh handler

---

# ADR-002: Fernet Symmetric Encryption for Credential Storage

## Status

Accepted

## Date

2026-03-18

## Context

Connector credentials (OAuth tokens, passwords, private keys) must be stored encrypted in the PostgreSQL database. We needed an encryption scheme that is simple to implement, doesn't require key management infrastructure, and is suitable for server-side secret storage.

## Decision

Use **Fernet** (from the `cryptography` library) with a key derived from `SEMABRIDGE_ENCRYPTION_KEY` via SHA-256.

## Alternatives Considered

### AWS KMS / Azure Key Vault

- **Rejected:** Adds cloud dependency, overkill for current on-premise deployment model

### AES-256-GCM with manual IV management

- **Rejected:** More complex to implement correctly. Fernet provides authenticated encryption (AES-128-CBC + HMAC-SHA256) with automatic IV generation.

### HashiCorp Vault

- **Rejected:** Adds operational complexity. Will reconsider for enterprise deployments.

## Consequences

- Single encryption key for all accounts — key rotation requires re-encrypting all stored credentials
- Default dev key (`default-insecure-dev-key`) must be overridden in production
- Key stored in `.env` file, which must be excluded from version control
