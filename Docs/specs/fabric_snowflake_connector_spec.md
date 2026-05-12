# Spec: Fabric-to-Snowflake Multi-Model Sync and Visualization Connector

## Assumptions
1. The implementation remains in this Python backend repo (`semabridge`) and extends existing API/services/connectors modules.
2. Git is the system of record for semantic metadata artifacts.
3. We will use a project-level monorepo topology (hub-and-spoke), not multi-repo or submodules.
4. Fabric semantic artifacts are managed as text-based project files (TMDL-oriented layout).
5. Existing test strategy is pytest-driven with unit and integration suites under `Tests/`.

## Objective
Build a resilient connector architecture that synchronizes Snowflake schema changes into multiple Fabric semantic models atomically, governs shared-model dependencies via versioned contracts, and provides a project UI that visualizes relationships and lineage across models, tables, and columns.

Primary users:
- Data platform engineers maintaining shared semantic models
- Analytics teams consuming shared models in project-specific workspaces
- Release and governance owners managing CI/CD and rollback safety

Success means:
- Multi-model sync is deterministic and atomic
- Shared model updates do not break downstream consumers unexpectedly
- Users can visually inspect model/table/column lineage and historical diffs

## Tech Stack
- Language: Python 3.11+
- API framework: FastAPI (existing API module layout)
- Tests: pytest (`Tests/` and `Tests/Integration/`)
- Serialization: JSON + TMDL-style text artifacts
- Version control integration: Git-backed versioning services in API layer

## Commands
- Install deps (pip): `pip install -r requirements.txt`
- Install deps (uv): `uv sync`
- Run API: `uv run python -m semabridge.api.main`
- Run all tests: `uv run pytest -q -p no:cacheprovider`
- Run focused tests (sync): `uv run pytest Tests/test_sync_execution_service.py -q -p no:cacheprovider`
- Run focused tests (versioning): `uv run pytest Tests/test_version_control_service_postgres_ready.py -q -p no:cacheprovider`
- Compile-check key modules: `uv run python -m py_compile src/semabridge/api/services/project_projects_impl.py src/semabridge/api/services/project_runs_impl.py src/semabridge/api/services/version_control_impl.py`

## Project Structure
- `src/semabridge/api/controllers/`: HTTP entrypoints for graph, projects, semantic, versioning
- `src/semabridge/api/services/`: orchestration and domain logic (sync, project, versioning, mapping)
- `src/semabridge/connectors/`: Snowflake/Fabric extraction, translation, publication, compatibility checks
- `Tests/`: unit-level and service-level tests
- `Tests/Integration/`: integration and end-to-end sync validation
- `docs/specs/`: architecture and implementation specs

## Code Style
Conventions:
- Keep service functions deterministic and side effects explicit
- Use typed dictionaries/models for structured metadata payloads
- Prefer additive schema evolution; avoid destructive key renames/removals
- Keep commits and sync updates atomic and traceable

Example style:
```python
from typing import Any

def validate_contract(payload: dict[str, Any], expected_version: str) -> None:
    schema_version = str(payload.get("contract_version", "")).strip()
    if schema_version != expected_version:
        raise ValueError(
            f"Unsupported contract_version: {schema_version}; expected {expected_version}"
        )
```

## Testing Strategy
Test layers:
- Unit tests for schema validation, dependency graph traversal, and versioning rules
- Service tests for sync orchestration and atomic update grouping
- Integration tests for Fabric-Snowflake sync behavior and generated artifact correctness

Coverage focus areas:
- Mapping schema validation and backward compatibility
- Breaking vs non-breaking shared model changes
- Atomic commit behavior for multi-model updates
- Graph payload correctness for model/table/column lineage views

Verification gates:
- Pre-merge: schema + syntax + dependency impact checks must pass
- Staging: deploy with environment-specific connection rewrite and smoke tests
- Production: promotion only after staging and contract checks pass

## Boundaries
- Always:
  - Validate mapping metadata against declared schema before sync
  - Keep sync outputs deterministic for clean Git diffs
  - Run relevant pytest suites before merge
- Ask first:
  - Introducing new external dependencies or major framework changes
  - Changing API contracts used by existing UI clients
  - Altering deployment topology (repo layout, workspace binding strategy)
- Never:
  - Commit credentials/secrets into metadata or config
  - Bypass breaking-change rules for shared models
  - Manually patch production workspace state outside tracked Git flows

## Success Criteria
1. Connector writes semantic artifacts in a Git-friendly layout and repeated syncs are idempotent.
2. A single Snowflake schema change affecting multiple models yields one atomic update set.
3. Shared model contract breaks create a new coexisting model version with deprecation metadata.
4. Downstream consumers can pin model versions and remain operational during migration windows.
5. Graph APIs support progressive disclosure from model-level to column-level lineage.
6. Commit-time visualization can render historical topology and color-coded structural diffs.
7. CI/CD enforces validation, staged deployment, production promotion, and rollback readiness.

## Non-Goals
- Replacing Fabric-native UI entirely
- Designing a generic metadata platform for non-Snowflake sources in this phase
- Supporting unmanaged manual workspace edits as a first-class workflow

## Open Questions
1. Default deprecation window for shared model versions: 30, 60, or 90 days?
2. Should initial enforcement for breaking changes be warning-only or hard-fail in CI?
3. Preferred storage format for dependency pinning in project configs (inline vs dedicated manifest)?
4. Required performance target for graph rendering at enterprise scale (node/edge count SLA)?
