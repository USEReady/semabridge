# Semabridge Authentication & Authorization Architecture

**Version:** 1.0  
**Last Updated:** 2026-05-11  
**Status:** Accepted  
**Author:** Semabridge Engineering

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture Layers](#2-architecture-layers)
3. [Layer 1 — User Authentication (JWT)](#3-layer-1--user-authentication-jwt)
4. [Layer 2 — Connector Credential Management](#4-layer-2--connector-credential-management)
5. [Layer 3 — Multi-Tenant Isolation](#5-layer-3--multi-tenant-isolation)
6. [Token Lifecycle & Refresh](#6-token-lifecycle--refresh)
7. [Frontend Integration](#7-frontend-integration)
8. [API Endpoint Reference](#8-api-endpoint-reference)
9. [Environment Variables](#9-environment-variables)
10. [Security Model](#10-security-model)
11. [Extending Auth for New Modules](#11-extending-auth-for-new-modules)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Overview

Semabridge implements a **three-layer authentication architecture** that separates concerns between:

1. **User Identity** — Who is making the request? (JWT-based)
2. **Connector Credentials** — What external system credentials does this user own? (Encrypted credential bundles)
3. **Tenant Isolation** — Can this user see/modify this data? (PostgreSQL RLS + application-level ownership checks)

This design follows the same patterns used by Airbyte, dbt Cloud, and Prefect — where user identity and connector credentials are decoupled, and each pipeline run receives an isolated credential scope.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (React)                         │
│  AuthContext.jsx → localStorage JWT → Bearer header on all API  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Authorization: Bearer <JWT>
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Layer 1: AuthMiddleware                        │
│  Validates JWT · Sets request.state.user_id · Skips public paths│
└──────────────────────────┬──────────────────────────────────────┘
                           │ request.state.user_id
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              Layer 2: Credential Resolution                      │
│  Account → decrypt bundle → build_*_config() → Pydantic config  │
│  Thread-safe · No os.environ mutation · Per-request isolation    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Scoped config object
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              Layer 3: Database-Level Isolation                   │
│  PostgreSQL RLS on accounts table · SET LOCAL app.current_user_id│
│  Defense-in-depth: DB refuses other users' rows even if app bugs│
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| JWT (HS256) for user auth | Stateless, no session store needed, standard RFC 7519 |
| Fernet (AES-128-CBC) for credential encryption | Symmetric encryption suitable for server-side secrets |
| Refresh token rotation (one-time-use) | Prevents replay attacks on stolen refresh tokens |
| Thread-safe credential builders (no `os.environ`) | Enables concurrent multi-user pipeline execution |
| PostgreSQL RLS as defense-in-depth | Even buggy app code cannot leak cross-tenant data |
| `AUTH_ENABLED` feature flag | Allows progressive rollout without breaking dev workflow |

---

## 2. Architecture Layers

### File Map

```
src/semabridge/auth/
├── __init__.py                    # Public API: hash_password, verify_password, create/decode tokens
├── passwords.py                   # bcrypt hashing (12 rounds)
├── tokens.py                      # JWT creation, validation, refresh token generation
├── deps.py                        # FastAPI Depends(): get_current_user, get_current_admin
├── middleware.py                   # AuthMiddleware — global JWT gate
├── schemas.py                     # Pydantic request/response models
├── encryption.py                  # Fernet encrypt/decrypt for credential storage
├── credential_builder.py          # Thread-safe config builders (Snowflake, Fabric, Databricks)
├── account_credential_resolver.py # Legacy env-var injection + scoped_account_env context manager
├── token_refresher.py             # Auto-refresh for Fabric (MSAL), Databricks (OAuth), Snowflake (OAuth)
├── fabric_validator.py            # MSAL JWT validation with JWKS caching
└── user_credentials.py            # Per-user credential injection (env-var bridge)

src/semabridge/api/
├── auth_router.py                 # REST endpoints: register, login, refresh, logout, credentials
├── deps.py                        # get_db, get_current_user, get_scoped_db (RLS)
├── app_setup.py                   # Startup: RLS policies, credential migration, schema fixes
└── account_router.py              # Account CRUD with ownership enforcement

src/semabridge/repository/orm/
└── models.py                      # ORM: User, UserCredential, Account, RefreshToken
```

---

## 3. Layer 1 — User Authentication (JWT)

### 3.1 How It Works

Semabridge uses **HS256-signed JWTs** for user authentication. The signing key is read from the `JWT_SECRET_KEY` environment variable at runtime — never hardcoded.

**Token Types:**

| Token | Lifetime | Storage | Purpose |
|-------|----------|---------|---------|
| Access Token | 15 minutes | `localStorage` (`semabridge-token`) | Carried in `Authorization: Bearer` header |
| Refresh Token | 7 days | HttpOnly cookie (`semabridge_refresh_token`) | One-time-use, rotated on each exchange |

**JWT Claims:**

```json
{
  "sub": "1",           // User ID (string per RFC 7519)
  "username": "admin",  // Display name
  "role": "admin",      // Authorization role
  "exp": 1715443200,    // Expiry timestamp
  "type": "access"      // Token type discriminator
}
```

### 3.2 Authentication Flow

```
┌──────┐          ┌──────────┐          ┌────────┐
│Client│          │ Backend  │          │   DB   │
└──┬───┘          └────┬─────┘          └───┬────┘
   │ POST /auth/login  │                    │
   │ {username, pass}  │                    │
   │──────────────────>│                    │
   │                   │ SELECT user WHERE  │
   │                   │ username = ?       │
   │                   │───────────────────>│
   │                   │    User row        │
   │                   │<──────────────────│
   │                   │ bcrypt.verify()    │
   │                   │                    │
   │                   │ INSERT refresh_token│
   │                   │───────────────────>│
   │                   │                    │
   │ {access_token}    │                    │
   │ + Set-Cookie:     │                    │
   │   refresh_token   │                    │
   │<──────────────────│                    │
   │                   │                    │
   │ GET /api/projects │                    │
   │ Authorization:    │                    │
   │   Bearer <JWT>    │                    │
   │──────────────────>│                    │
   │                   │ AuthMiddleware:    │
   │                   │ decode JWT →       │
   │                   │ set user_id in     │
   │                   │ request.state      │
   │                   │───────────────────>│
   │   200 OK          │                    │
   │<──────────────────│                    │
```

### 3.3 Middleware: The Global Gate

**File:** `src/semabridge/auth/middleware.py`

The `AuthMiddleware` runs on every request. It is toggled by the `AUTH_ENABLED` environment variable.

**Public paths (no JWT required):**

| Path | Reason |
|------|--------|
| `/auth/register`, `/auth/login`, `/auth/auto-login` | Authentication endpoints themselves |
| `/auth/refresh`, `/auth/logout` | Session management |
| `/api/health` | Health checks |
| `/api/discovery/*` | Discovery uses connector credentials, not user JWT |
| `/api/connections/*` | OAuth flows (device code, callbacks) |
| `/ws/*` | WebSocket upgrades |
| `/docs`, `/redoc`, `/openapi.json` | API documentation |

**For all other paths:** the middleware extracts the `Bearer` token, decodes it, and sets `request.state.user_id` and `request.state.user_role` for downstream handlers.

### 3.4 Dev Mode: Auto-Login

When `AUTH_ENABLED` is not `true`, the frontend automatically calls `POST /auth/auto-login` which creates a `dev` user (if not exists) and issues a JWT — zero friction for local development.

### 3.5 Roles & Authorization

| Role | Access Level |
|------|-------------|
| `admin` | Full access to all endpoints |
| `viewer` | Read-only access (write endpoints return 403) |

The first user registered is automatically promoted to `admin`. Use `get_current_admin` dependency to enforce admin-only routes.

---

## 4. Layer 2 — Connector Credential Management

### 4.1 The Account Model

Each external connection (Fabric, Snowflake, Databricks) is stored as an **Account** row:

```
accounts table
├── id                  (UUID)
├── connector_type      (FABRIC | SNOWFLAKE | DATABRICKS)
├── tag                 (user-friendly label, e.g. "production-sf")
├── identity_email      (user identity, e.g. "admin@corp.com")
├── encrypted_token     (Fernet-encrypted JSON credential bundle)
├── refresh_token       (Fernet-encrypted refresh token)
├── token_expires_at    (UTC expiry timestamp)
├── auth_type           (e.g. "oauth", "key_pair", "password")
├── owner_id            (FK → users.id — tenant isolation)
├── status              (Active | Expired | Revoked)
└── is_default          (boolean — default account for this connector)
```

### 4.2 Credential Storage & Encryption

**File:** `src/semabridge/auth/encryption.py`

All credentials are encrypted at rest using **Fernet symmetric encryption** (AES-128-CBC + HMAC-SHA256):

```python
# Key derivation
secret = os.environ.get("SEMABRIDGE_ENCRYPTION_KEY", "default-insecure-dev-key")
key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
fernet = Fernet(key)

# Encrypt
encrypted = fernet.encrypt(plaintext.encode())

# Decrypt
plaintext = fernet.decrypt(encrypted.encode())
```

> **IMPORTANT:** Set `SEMABRIDGE_ENCRYPTION_KEY` to a strong random value in production. The default key is insecure and intended only for local development.

### 4.3 Credential Bundle Format

The `encrypted_token` field stores a JSON bundle (not a bare token):

```json
{
  "access_token": "eyJ0eXAi...",
  "refresh_token": "0.AToA...",
  "tenant_id": "d6e5bfe7-...",
  "client_id": "04b07795-...",
  "workspace_id": "abc123-...",
  "expires_at": "1715443200"
}
```

This allows a single encrypted column to carry all connector-specific credentials.

### 4.4 Thread-Safe Credential Injection (API Path)

**File:** `src/semabridge/auth/credential_builder.py`

For multi-user API execution, credentials are injected via **Pydantic config objects** — not environment variables:

```python
from semabridge.auth.credential_builder import build_snowflake_config

# Returns a NEW SnowflakeConfig — no os.environ touched
config = build_snowflake_config(account, db_session, base_config)
extractor = SnowflakeExtractor(config=config)
```

**Available builders:**

| Function | Returns | Used By |
|----------|---------|---------|
| `build_snowflake_config()` | `SnowflakeConfig` | Snowflake sync pipeline |
| `build_fabric_config()` | `(FabricConfig, access_token)` | Fabric extraction pipeline |
| `build_databricks_config()` | `DatabricksConfig` | Databricks deployment pipeline |

**Layered merge strategy (same as dbt Profile merge):**

```
Auth fields (user, password, oauth_*)     → from Account bundle (user-specific)
Structural fields (warehouse, database)   → from base_config (semabridge.yaml)
Passthrough fields (deployment_method)    → always from base_config
```

### 4.5 Legacy Credential Injection (CLI Path)

**File:** `src/semabridge/auth/account_credential_resolver.py`

For CLI (single-user) execution, credentials are injected via `os.environ` using a context manager that restores the original values on exit:

```python
from semabridge.auth.account_credential_resolver import scoped_account_env

with scoped_account_env(account, db) as access_token:
    # os.environ now has this account's credentials
    extractor = FabricExtractor(settings.fabric)
    ...
# os.environ is restored to original state
```

> **WARNING:** `scoped_account_env` is NOT thread-safe. Use `build_*_config()` for the API path.

---

## 5. Layer 3 — Multi-Tenant Isolation

### 5.1 Application-Level Ownership

Every `Account` row has an `owner_id` column (FK → `users.id`). API routes filter by ownership:

```python
accounts = db.execute(
    select(Account).where(Account.owner_id == current_user.id)
).scalars().all()
```

### 5.2 Database-Level RLS (Defense-in-Depth)

**File:** `src/semabridge/api/app_setup.py` → `_apply_rls_policies()`

PostgreSQL Row Level Security is enabled on the `accounts` table as a second layer:

```sql
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts FORCE ROW LEVEL SECURITY;

CREATE POLICY account_owner_isolation ON accounts
FOR ALL
USING (
    current_setting('app.current_user_id', true) = ''
    OR current_setting('app.current_user_id', true) IS NULL
    OR owner_id IS NULL
    OR owner_id = current_setting('app.current_user_id', true)::integer
);
```

The session variable `app.current_user_id` is set per-request via `get_scoped_db()`:

```python
from semabridge.api.deps import get_scoped_db

@router.get("/accounts")
def list_accounts(db: Session = Depends(get_scoped_db)):
    # RLS automatically filters — only current user's accounts returned
    return db.execute(select(Account)).scalars().all()
```

### 5.3 Isolation Matrix

| Scenario | Application Filter | RLS Filter | Result |
|----------|-------------------|------------|--------|
| User A queries own accounts | ✅ `owner_id = A` | ✅ RLS passes | ✅ Sees own data |
| User A queries User B's accounts | ❌ Blocked by app | ✅ RLS blocks anyway | ❌ No data returned |
| Bug: app forgets `owner_id` filter | ❌ Missing | ✅ RLS catches it | ❌ No cross-tenant leak |
| CLI (no auth) | N/A | ✅ RLS allows (no user_id set) | ✅ Sees all data |

---

## 6. Token Lifecycle & Refresh

### 6.1 Connector Token Refresh

**File:** `src/semabridge/auth/token_refresher.py`

Before every pipeline execution, `ensure_valid_token()` checks if the connector token is expired (or within a 5-minute grace window) and refreshes transparently:

```python
from semabridge.auth.token_refresher import ensure_valid_token

token = ensure_valid_token(account, db)
# Returns a valid access_token, refreshing if needed
```

**Refresh strategies by connector:**

| Connector | Auth Type | Refresh Method | Token Source |
|-----------|-----------|----------------|--------------|
| Fabric | Interactive (Device Code) | MSAL `acquire_token_by_refresh_token()` | Microsoft Entra ID |
| Fabric | Service Principal | Client credentials grant | Microsoft Entra ID |
| Databricks | U2M (Interactive) | OAuth2 `refresh_token` grant | Databricks OIDC `/oidc/v1/token` |
| Databricks | M2M (Service Principal) | OAuth2 `client_credentials` grant | Databricks OIDC `/oidc/v1/token` |
| Snowflake | OAuth (External) | OAuth2 `client_credentials` grant | External IdP token endpoint |
| Snowflake | Key-Pair / Password | No refresh needed | Stored credentials (non-expiring) |

### 6.2 User JWT Refresh

The frontend proactively refreshes the JWT at 80% of its TTL (12 minutes into a 15-minute token) to prevent expiry during active use. The refresh flow:

```
1. Timer fires at 80% TTL
2. POST /auth/refresh (sends HttpOnly cookie)
3. Backend: verify cookie hash → revoke old → issue new pair
4. Frontend: stores new JWT, schedules next refresh
5. Fallback: if cookie expired → POST /auth/auto-login (dev mode)
```

### 6.3 Error: TokenExpiredError

When both access and refresh tokens are expired, the system raises `TokenExpiredError`. The frontend shows a re-authentication prompt directing the user to Settings → Connections.

---

## 7. Frontend Integration

### 7.1 AuthContext Provider

**File:** `frontend/src/context/AuthContext.jsx`

Wraps the entire app and provides:

```jsx
const { user, token, isAuthenticated, login, logout, register } = useAuth();
```

### 7.2 API Interceptor

**File:** `frontend/src/utils/api.js`

Every API call goes through `authFetch()` which:
1. Injects `Authorization: Bearer <JWT>` from `localStorage`
2. Injects `X-Fabric-Context: <workspace_id>` if available
3. On 401: coalesces concurrent retries via singleton `tryRefreshToken()`
4. On recovery failure: dispatches `semabridge:auth-expired` event

### 7.3 Token Storage

| Key | Storage | Purpose |
|-----|---------|---------|
| `semabridge-token` | `localStorage` | User JWT access token |
| `semabridge_refresh_token` | HttpOnly cookie | Refresh token (not accessible to JS) |
| `semabridge-fabric-token` | `localStorage` | Fabric MSAL access token |
| `semabridge-fabric-token-expires` | `localStorage` | Fabric token expiry timestamp |

---

## 8. API Endpoint Reference

### Public Endpoints (No JWT Required)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auth/register` | Create user account. First user → admin |
| `POST` | `/auth/login` | Authenticate → JWT + refresh cookie |
| `POST` | `/auth/auto-login` | Dev mode: create dev user + JWT |
| `POST` | `/auth/refresh` | Exchange refresh cookie → new JWT pair |
| `POST` | `/auth/logout` | Revoke refresh token + clear cookie |

### Protected Endpoints (JWT Required)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/auth/me` | Current user profile |
| `POST` | `/auth/credentials/{service}` | Save credential key/value |
| `GET` | `/auth/credentials` | List credential keys (values masked) |
| `GET` | `/auth/credentials/{service}` | List keys for a specific service |
| `DELETE` | `/auth/credentials/{service}/{key}` | Delete a credential |

---

## 9. Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET_KEY` | **Yes** (production) | — | HS256 signing key for JWTs |
| `SEMABRIDGE_ENCRYPTION_KEY` | **Yes** (production) | `default-insecure-dev-key` | Fernet key for credential encryption |
| `AUTH_ENABLED` | No | `false` | Set to `true` to enforce JWT on all routes |

---

## 10. Security Model

### Encryption at Rest

| Data | Algorithm | Key Source |
|------|-----------|------------|
| User passwords | bcrypt (12 rounds) | N/A (one-way hash) |
| Connector tokens | Fernet (AES-128-CBC + HMAC-SHA256) | `SEMABRIDGE_ENCRYPTION_KEY` |
| Refresh tokens (DB) | SHA-256 hash | N/A (one-way hash) |

### Security Boundaries

| Boundary | Enforcement |
|----------|-------------|
| No secrets in code | Credentials read from `os.environ` or encrypted DB columns |
| No secrets in logs | Log filter redacts credential patterns |
| No plaintext tokens in DB | All tokens Fernet-encrypted; refresh tokens SHA-256 hashed |
| No cross-tenant data access | Application ownership + PostgreSQL RLS |
| No concurrent credential bleed | Thread-safe config builders (no `os.environ` mutation in API) |

---

## 11. Extending Auth for New Modules

### Adding a New Protected Route

```python
from semabridge.auth.deps import get_current_user
from semabridge.repository.orm.models import User

@router.get("/my-new-endpoint")
def my_endpoint(user: User = Depends(get_current_user)):
    # user.id, user.username, user.role available
    return {"hello": user.username}
```

### Adding a New Admin-Only Route

```python
from semabridge.auth.deps import get_current_admin

@router.delete("/dangerous-action")
def admin_only(user: User = Depends(get_current_admin)):
    # Only admin role reaches here; viewers get 403
    ...
```

### Adding a New Connector

1. Add credential keys to `_CONNECTOR_ENV_MAP` in `account_credential_resolver.py`
2. Add env-var mapping to `_ENV_MAP` in `user_credentials.py`
3. Create `build_<connector>_config()` in `credential_builder.py`
4. Add refresh logic to `token_refresher.py` → `ensure_valid_token()`
5. Register the connector type in `Account.connector_type` choices

### Using Tenant-Scoped Queries

```python
from semabridge.api.deps import get_scoped_db

@router.get("/tenant-data")
def get_data(db: Session = Depends(get_scoped_db)):
    # PostgreSQL RLS automatically filters by current user
    return db.execute(select(Account)).scalars().all()
```

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `JWT_SECRET_KEY environment variable is not set` | Missing `.env` entry | Add `JWT_SECRET_KEY=<random-string>` to `.env` |
| `Invalid or expired token` on every request | `AUTH_ENABLED=true` but no login | Either set `AUTH_ENABLED=false` or login first |
| Credentials from wrong user appearing | Using `scoped_account_env` in API (thread-unsafe) | Switch to `build_*_config()` for API paths |
| `TokenExpiredError` during sync | Both access + refresh tokens expired | Re-authenticate via Settings → Connections |
| Fabric 401 after machine sleep | MSAL token expired during sleep | Auto-refresh handles this; if persists, re-login |
| `Failed to decrypt token` in logs | `SEMABRIDGE_ENCRYPTION_KEY` changed | All encrypted tokens become invalid; users must re-authenticate |
| RLS blocks all queries | `app.current_user_id` not set | Use `get_scoped_db()` dependency, not raw `get_db()` |

---

## Appendix: Database Schema (Auth Tables)

```sql
-- Users
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20) NOT NULL DEFAULT 'viewer',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ
);

-- Per-user credentials
CREATE TABLE user_credentials (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    service    VARCHAR(50) NOT NULL,
    key        VARCHAR(100) NOT NULL,
    value      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ,
    UNIQUE (user_id, service, key)
);

-- Connector accounts (with RLS)
CREATE TABLE accounts (
    id               VARCHAR(36) PRIMARY KEY,
    connector_type   VARCHAR(50) NOT NULL,
    tag              VARCHAR(255) NOT NULL,
    identity_email   VARCHAR(255),
    encrypted_token  TEXT,
    refresh_token    TEXT,
    token_expires_at TIMESTAMPTZ,
    auth_type        VARCHAR(50),
    owner_id         INTEGER REFERENCES users(id),
    status           VARCHAR(50) NOT NULL DEFAULT 'Active',
    is_default       BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (owner_id, tag)
);

-- JWT refresh tokens (one-time-use rotation)
CREATE TABLE refresh_tokens (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
