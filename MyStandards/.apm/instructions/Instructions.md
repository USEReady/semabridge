---
name: semabridge-architecture
description: Core architectural rules and coding standards for SemaBridge
---

# SemaBridge Architecture & Coding Standards

## Stack

- **Runtime**: Python 3.10+
- **Database**: DuckDB (default embedded OLAP backend) — all access goes through the ORM abstraction layer
- **ORM Layer**: SQLAlchemy/SQLModel — **mandatory for all DB operations**, not optional. Install via `uv add semabridge[orm]`
- **Alternative Backends**: SQLite is permitted if explicitly requested by the user; must be wired through the ORM layer — never via raw `sqlite3.connect()`
- **Data Validation**: Pydantic v2 (BaseModel, BaseSettings)
- **API Framework**: FastAPI + Uvicorn (async-first)
- **CLI Framework**: Typer + Rich Console
- **Auth**: MSAL (Microsoft Entra ID / Azure AD)
- **HTTP Client**: httpx (async), requests (sync with retry/backoff)
- **PBIX Parsing**: zipfile + json (native stdlib)
- **Configuration**: YAML (pyyaml) + pydantic-settings + .env files
- **Testing**: pytest + pytest-cov + pytest-asyncio
- **Logging**: Python stdlib logging + Rich console + RotatingFileHandler
- **Frontend**: React (Vite) — located in `Frontend/`

---

## Mandatory Rules

### 1. OSI Intermediate Model First
All conversions must follow the pipeline: `Source → OSI → Target`.  
Direct source-to-target conversion is **forbidden** — there are no exceptions.  
OSI model definitions live in: `src/semabridge/intermediate/models.py`  
All data must pass through Pydantic validation at the OSI layer.

### 2. ORM-First Database Access
- **All** database operations — DuckDB, SQLite, or any future backend — must go through the ORM abstraction layer in `src/semabridge/repository/db.py`.
- **NEVER** use raw `duckdb.connect()` or `sqlite3.connect()` directly outside the ORM/repository layer.
- The default backend is DuckDB (embedded OLAP). SQLite is a permitted alternative if the user explicitly requests it.
- DuckDB uses MVCC — wrap writes in a single-writer lock pattern when operating outside of ORM-managed transactions.
- Use `JSON` column type for semantic model snapshots (not `TEXT`).
- Leverage DuckDB's analytical SQL features where appropriate: window functions, CTEs, `FULL OUTER JOIN`.
- Adding a new backend means implementing it inside the repository abstraction — not scattering raw connections across modules.

### 3. No Inline Secrets
- **NEVER** accept passwords or tokens as function parameters.
- Secret config keys must carry the `_env` suffix (e.g., `client_secret_env`).
- Use `pydantic.SecretStr` for all secret-valued fields in settings.
- Credentials must be read from `os.environ` inside methods — never hardcoded or passed as arguments.

### 4. Local PBIX Parsing
When working with `.pbix` files:
- Use `zipfile.ZipFile` to open the archive.
- Parse `DataModelSchema` (JSON) for tables, columns, measures, and relationships.
- Parse `Connections.json` for composite model references.
- Handle BOM encoding with `utf-8-sig`.
- Connector lives at: `src/semabridge/connectors/local_pbix_connector.py`

### 5. Custom Exceptions Only
Only raise exceptions defined in `semabridge.core.exceptions`:
- `ConnectorError`, `ConversionError`, `ValidationError`
- `RepositoryError`, `MigrationError`, `PBIXParsingError`, `RateLimitError`

**NEVER** raise a bare `Exception`. If a new error category is needed, add it to `core/exceptions.py` first.  
Always include context in error messages (e.g., model name, workspace ID, file path).

### 6. Logging Standards
- Always import via: `from semabridge.utils.logger import get_logger` at module level.
- Every log line automatically includes `[threadName]` and function name.
- `CredentialRedactionFilter` scrubs passwords and tokens from all output.
- Warnings auto-dispatch to the UI via WebSocket (Toast notifications).
- **NEVER** use `print()` in production code. Use `logger.debug()` for development traces.
- `logger.py` — hierarchical logging with credential redaction (general use).
- `enterprise_logger.py` — structured operational logging for audit/compliance output.

### 7. Type Safety
- All function signatures must be fully annotated — no bare untyped parameters.
- Always include `from __future__ import annotations` as the first line of every module.
- Avoid `Any`. Use `TypedDict`, `Protocol`, or Pydantic models to express shape.
- Use `Optional[X]` or `X | None` for nullable types.
- Return types must always be explicit, including `-> None`.

### 8. Async Conventions
- FastAPI route handlers and all I/O-bound operations should use `async def`.
- CPU-bound or blocking operations must be offloaded via `asyncio.to_thread()` or a `ThreadPoolExecutor` — do not block the event loop.
- WebSocket alert handlers (in `src/semabridge/api/websocket_alerts.py`) follow the same async-first rule.
- Multi-workspace Fabric operations use `ThreadPoolExecutor` for concurrent workspace processing — keep this pattern consistent.
- Avoid mixing sync and async code in the same call chain without an explicit bridge.

### 9. Fabric API Resilience
- Handle HTTP 429 with exponential backoff — respect the `Retry-After` header.
- Use `continuationToken` for all paginated Fabric REST API results.
- Enforce composite primary key: `artifact_id + workspace_id`.
- Multi-workspace: process workspace arrays concurrently using `ThreadPoolExecutor`.
- Use `RateLimitError` for 429 scenarios — never swallow them silently.

### 10. Plugin Architecture Contract
- All plugins must be discovered and loaded through `src/semabridge/plugins/` — do not import plugin code directly from other modules.
- A plugin must implement the interface defined in `src/semabridge/core/interfaces.py`.
- Plugins are registered at runtime via the loader; they must not modify global state or monkey-patch existing modules.
- Plugin dependencies must be declared as optional extras in `pyproject.toml`.

---

## File Structure

```
src/semabridge/
├── core/
│   ├── exceptions.py                     # Custom exception hierarchy
│   ├── interfaces.py                     # BaseConnector, BaseConverter ABCs
│   └── settings.py                       # Pydantic Settings (Snowflake, Fabric, Model configs)
├── connectors/
│   ├── fabric_extractor.py               # Fabric REST API extraction
│   ├── local_pbix_connector.py           # Air-gapped .pbix parsing
│   ├── multi_workspace_orchestrator.py   # Multi-workspace concurrent API
│   └── snowflake_extractor.py            # Snowflake metadata extraction
├── formats/
│   └── composite_models.py               # Report↔Model many-to-many resolver
├── converter/                            # TMSL↔OSI transformation logic
├── intermediate/
│   └── models.py                         # Pydantic OSI models
├── repository/
│   ├── db.py                             # ORM abstraction (DuckDB + SQLAlchemy)
│   ├── duckdb_manager.py                 # Core versioning engine
│   ├── duckdb_migrator.py                # SQLite → DuckDB migration utility
│   └── semantic_version_manager.py       # Semantic versioning
├── cli/                                  # Typer CLI commands
├── api/
│   ├── main.py                           # App entry point + REST endpoints
│   ├── repo_router.py                    # Repository browser API
│   └── websocket_alerts.py              # Real-time UI notifications (Toast)
├── plugins/                              # Plugin loader and extensibility layer
├── sml/                                  # Object graph assemblers and serializers
├── ui/                                   # Terminal or native UI helpers
└── utils/
    ├── logger.py                         # Hierarchical logging + credential redaction
    └── enterprise_logger.py              # Structured operational/audit logging
```

**Hard rules:**
- Do NOT place files outside the defined directories.
- Do NOT create files with vague names (`temp`, `misc`, `test2`, `utils2`).
- Do NOT leave `print()` or debug statements in production code.

---

## Naming Conventions

| Element           | Convention         | Example                       |
|-------------------|--------------------|-------------------------------|
| Files             | `snake_case`       | `fabric_connector.py`         |
| Classes           | `PascalCase`       | `FabricExtractor`             |
| Variables         | `snake_case`       | `model_name`                  |
| Constants         | `UPPER_SNAKE_CASE` | `MAX_RETRY_COUNT`             |
| Auth/secret keys  | `*_env` suffix     | `client_secret_env`           |
| Test files        | `test_<subject>`   | `test_fabric_connector.py`    |
| Test classes      | `Test<Subject>`    | `TestFabricConnector`         |

---

## Standard Import Pattern

Every module should follow this import order:

```python
from __future__ import annotations

# Standard library
import json
from typing import Dict, List, Optional

# Third-party
from pydantic import BaseModel

# Internal
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
```

- Always include `from __future__ import annotations` as the first line.
- Do not import `Any` unless absolutely unavoidable — see Rule 7.
- Do not import `duckdb` directly outside the repository layer — use the ORM abstraction.

---

## Testing

- **Run tests**: `uv run pytest Tests/ -v --cov=src/semabridge --cov-report=term-missing`
- **Target directory**: `Tests/` (capital T, per repository structure standards)
- Minimum **80% coverage** for all new code
- Current baseline: 374 tests, all passing
- **Always mock** external APIs (Fabric, Snowflake) and DB connections in unit tests — never call real endpoints or open real DB files
- Use `conftest.py` fixtures for DuckDB test databases
- Integration tests that require live connections belong in `Tests/Integration/` and must be explicitly opted-in via a pytest marker or environment flag
- New connectors or repository backends must ship with corresponding tests in `Tests/Connectors/` or `Tests/Models/` respectively

---

## Key Commands

| Command | Purpose |
|---------|---------|
| `uv run pytest Tests/ -v` | Run all tests |
| `uv run semabridge` | Start the application |
| `uv run python -m semabridge.api.main` | Start FastAPI server directly |
| `uv run semabridge validate` | Test all connections |
| `uv run semabridge semantic-sync` | Run sync pipeline |
| `uv run semabridge history -d <id>` | View version history |
| `uv run semabridge rollback -d <id> --tag <tag>` | Rollback to a tagged version |

---

## Dependency Management

- Use `uv` for all dependency operations (`uv add`, `uv run`, `uv sync`)
- Do not use `pip install` directly in development workflows
- Optional feature groups are declared as extras in `pyproject.toml` (e.g., `semabridge[orm]`, `semabridge[snowflake]`)
- Lock file (`uv.lock`) must always be committed and kept up to date