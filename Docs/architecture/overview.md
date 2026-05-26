# System Overview

Semabridge automates semantic model synchronization between Snowflake and Microsoft Fabric.
It uses an OSI/SML-centered pipeline to extract, transform, and emit models across platforms.

## Stack

| Layer | Technology | Version Source |
| --- | --- | --- |
| Runtime | Python | [pyproject.toml](../../pyproject.toml) (>=3.11) |
| CLI | Typer, Rich | [pyproject.toml](../../pyproject.toml) |
| API | FastAPI, Uvicorn | [pyproject.toml](../../pyproject.toml) |
| Data/ORM | SQLAlchemy, Alembic, DuckDB | [pyproject.toml](../../pyproject.toml) |
| Frontend | React, Vite, Tailwind | [frontend/package.json](../../frontend/package.json) |

## Directory Structure (Top Level)

```
Config/          Project and behavior configuration
Docs/            Legacy docs (superseded by docs/)
docs/            Current documentation
examples/        Configuration examples only
frontend/        React/Vite UI
Scripts/         Operational scripts
src/semabridge/  Backend package
Tests/           Pytest suite
```

## Key Flows

### Sync Pipeline (Snowflake <-> Fabric)
1. Extract source metadata (Snowflake or Fabric).
2. Convert to OSI/SML models.
3. Store model state and diffs in the repository.
4. Emit to target (Fabric or Snowflake).

Implementation references:
- Extractors: src/semabridge/connectors/
- Converters: src/semabridge/converter/
- Intermediate models: src/semabridge/intermediate/
- Emitters: src/semabridge/connectors/
- Repository: src/semabridge/repository/

### API-Driven Sync Jobs
- Sync orchestration is exposed via FastAPI.
- Jobs, conflicts, and mappings are managed by the sync service.

Implementation references:
- Sync API: src/semabridge/api/sync_router.py
- Sync orchestrator: src/semabridge/sync/orchestrator.py

## Configuration

Semabridge reads configuration from:
- Config/config.yaml (global runtime config)
- Config/behavior.yaml (feature flags and deployment behavior)
- Config/semabridge.yaml (project-level configuration)

See [Configuration Files](../development/configuration.md) for details.
