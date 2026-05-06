# REST API

Semabridge exposes a FastAPI service with OpenAPI docs.

## API Docs
- Swagger UI: /docs
- ReDoc: /redoc

## Core Routes

### Sync Jobs
Base path: /sync
- POST /sync/jobs
- GET /sync/jobs
- GET /sync/jobs/{job_id}
- POST /sync/jobs/{job_id}/cancel
- POST /sync/jobs/{job_id}/resume
- GET /sync/jobs/{job_id}/conflicts
- POST /sync/conflicts/{conflict_id}/resolve
- GET /sync/mappings
- GET /sync/schema/{model_name}/history

### Authentication
Base path: /auth
- POST /auth/register
- POST /auth/login
- POST /auth/refresh
- GET /auth/me
- POST /auth/credentials/{service}
- GET /auth/credentials
- DELETE /auth/credentials/{service}/{key}

### Settings
Base path: /api/settings
- GET /api/settings/local-folders
- POST /api/settings/local-folders
- GET /api/settings/secrets
- POST /api/settings/secrets

### Accounts
Base path: /api/accounts
- POST /api/accounts
- GET /api/accounts
- PATCH /api/accounts/project/{project_id}/link-account
- DELETE /api/accounts/{account_id}

### Semantic Comparator
Base path: /api/comparator
- POST /api/comparator/parse
- POST /api/comparator/compare
- POST /api/comparator/compare-semantic

Additional endpoints are registered under src/semabridge/api/controllers/ and
can be explored in the OpenAPI docs.
