# SemaBridge — Antigravity Agent Instructions

## Stack & Architecture

- **Runtime**: Python 3.10+
- **CLI Framework**: Typer + Rich Console
- **API Framework**: FastAPI + Uvicorn
- **Database**: DuckDB (embedded OLAP engine, NOT SQLite)
- **ORM Fallback**: SQLAlchemy/SQLModel (optional, via `pip install semabridge[orm]`)
- **Data Validation**: Pydantic v2 (BaseModel, BaseSettings)
- **PBIX Parsing**: zipfile + json (native stdlib) for local .pbix extraction
- **Configuration**: YAML (pyyaml) + pydantic-settings + .env files
- **HTTP Client**: httpx (async), requests (sync with retry/backoff)
- **Authentication**: MSAL (Microsoft Entra ID / Azure AD)
- **Testing**: pytest + pytest-cov + pytest-asyncio
- **Logging**: Python stdlib logging + Rich console + RotatingFileHandler
- **Frontend**: React (Vite) — located in `frontend/`

---

## Core Design Principles

### 1. Intermediate Model First (MANDATORY)
All conversions go through the OSI (Open Semantic Intermediate) layer:
```
Source → OSI → Target
```
- **NEVER** convert directly from Source to Target.
- OSI definitions live in `src/semabridge/intermediate/models.py`.
- All data must pass through Pydantic validation at the OSI layer.

### 2. DuckDB — NOT SQLite
- Use `duckdb.connect()` for all database operations.
- **NEVER** use `sqlite3.connect()` or SQLite-specific syntax.
- DuckDB uses MVCC; wrap writes in a single-writer lock pattern.
- Use `JSON` column type for semantic model snapshots (not TEXT).
- Leverage analytical SQL features: window functions, CTEs, FULL OUTER JOIN.
- The DB abstraction layer is in `src/semabridge/repository/db.py`.

### 3. Security — No Inline Secrets
- **NEVER** accept passwords/tokens as function parameters.
- Configuration keys for secrets must end with `_env` suffix.
- Methods requiring credentials must read from `os.environ` internally.
- Use `pydantic.SecretStr` for secret fields in settings.

### 4. Local PBIX Parsing
When working with `.pbix` files:
- Use `zipfile.ZipFile` to open the archive.
- Parse `DataModelSchema` (JSON) for tables, columns, measures, relationships.
- Parse `Connections.json` for composite model references.
- Handle BOM encoding with `utf-8-sig`.
- Connector: `src/semabridge/connectors/local_pbix_connector.py`

---

## File Structure (MANDATORY)

```
semabridge/
├── src/semabridge/
│   ├── core/               # Exceptions, Interfaces (ABCs), Settings, Orchestrator
│   │   ├── exceptions.py   # Custom exception hierarchy
│   │   ├── interfaces.py   # BaseConnector, BaseConverter ABCs
│   │   └── settings.py     # Pydantic Settings (Snowflake, Fabric, Model configs)
│   ├── connectors/         # External system integrations
│   │   ├── fabric_extractor.py           # Fabric REST API extraction
│   │   ├── local_pbix_connector.py       # Air-gapped .pbix parsing
│   │   ├── multi_workspace_orchestrator.py # Multi-workspace concurrent API
│   │   └── snowflake_extractor.py        # Snowflake metadata extraction
│   ├── formats/            # Format definitions & schema rules
│   │   └── composite_models.py           # Report↔Model many-to-many resolver
│   ├── converter/          # TMSL↔OSI transformation logic
│   ├── intermediate/       # OSI model definitions (Pydantic)
│   │   └── models.py       # Pydantic models for OSI
│   ├── repository/         # DuckDB version control & DB abstraction
│   │   ├── db.py                         # Backend abstraction (DuckDB + ORM)
│   │   ├── duckdb_manager.py             # Core versioning engine
│   │   ├── duckdb_migrator.py            # SQLite → DuckDB migration
│   │   └── semantic_version_manager.py   # Semantic versioning
│   ├── cli/                # Typer CLI commands
│   ├── api/                # FastAPI endpoints
│   │   ├── main.py                       # App entry + all REST endpoints
│   │   ├── repo_router.py                # Repository browser API
│   │   └── websocket_alerts.py           # Real-time UI notifications
│   ├── plugins/            # Extensible plugin architecture
│   └── utils/              # Logging, helpers
│       ├── logger.py                     # Hierarchical logging + credential redaction
│       └── enterprise_logger.py          # Structured operational logging
├── tests/                  # pytest test suite (374 tests)
├── frontend/               # React (Vite) web application
├── docs/                   # Documentation
├── examples/               # Example configurations
├── scripts/                # Build/deploy utilities
├── config.yaml             # Application configuration
├── semabridge.yaml         # Sync configuration
└── pyproject.toml          # Dependencies & entry points
```

**Rules:**
- Do NOT place files outside the defined directories
- Do NOT create files with vague names (temp, misc, test2, utils2)
- Do NOT leave print() or debug statements in production code

---

## Coding Standards

### Type Safety
- **All** function signatures must have complete type annotations.
- **Avoid `Any`** — use `TypedDict`, `Protocol`, or Pydantic models.
- Use `Optional[X]` or `X | None` for nullable types.

### Naming Conventions
| Element      | Convention        | Example                    |
|--------------|-------------------|----------------------------|
| Files        | `snake_case`      | `fabric_connector.py`      |
| Classes      | `PascalCase`      | `FabricExtractor`          |
| Variables    | `snake_case`      | `model_name`               |
| Constants    | `UPPER_SNAKE_CASE`| `MAX_RETRY_COUNT`          |
| Auth keys    | `*_env` suffix    | `client_secret_env`        |

### Error Handling
- Use custom exceptions from `semabridge.core.exceptions`:
  - `ConnectorError`, `ConversionError`, `ValidationError`
  - `RepositoryError`, `MigrationError`, `PBIXParsingError`
  - `RateLimitError` (for Fabric API 429s)
- **NEVER** raise bare `Exception`.
- Include context in error messages (model name, workspace ID, etc.).

### Logging
- Use `from semabridge.utils.logger import get_logger` at module level.
- Every log line includes `[threadName]` automatically.
- `CredentialRedactionFilter` scrubs passwords/tokens from all output.
- Warnings auto-dispatch to UI via WebSocket (Toast notifications).
- **NEVER** use `print()` in production code.

### Fabric API Resilience
- Handle HTTP 429 with exponential backoff (respect `Retry-After` header).
- Use `continuationToken` for paginated results.
- Enforce composite primary key: `artifact_id + workspace_id`.
- Multi-workspace: process workspace arrays concurrently with ThreadPoolExecutor.

---

## Testing

- Minimum 80% coverage for new functionality.
- Run: `pytest tests/ -v --cov=src/semabridge --cov-report=term-missing`
- Use `conftest.py` fixtures for DuckDB test databases.
- Mock external APIs (Fabric, Snowflake) — never call real endpoints in tests.
- Current baseline: **374 tests, all passing**.

## Import Pattern
```python
# Standard library
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

# Third-party
import duckdb
from pydantic import BaseModel

# Internal
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)
```

## Key Commands
| Command | Purpose |
|---------|---------|
| `pytest tests/ -v` | Run all tests |
| `python -m semabridge.api.main` | Start FastAPI server |
| `semabridge validate` | Test connections |
| `semabridge semantic-sync` | Run sync pipeline |
| `semabridge history -d <id>` | View version history |
| `semabridge rollback -d <id> --tag <tag>` | Rollback to version |
