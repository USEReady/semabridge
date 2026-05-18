# Spec: Parallel TMSL and TMDL Metadata Engine Migration

## Assumptions
1. Backend implementation is Python and should remain Python-first.
2. Existing TMSL-to-SML behavior is production-stable and must remain default during migration.
3. SML JSON is the canonical internal representation used by sync logic.
4. Snapshot persistence is already handled by SQLAlchemy/Alembic-backed models and migrations.
5. Initial rollout targets internal/staged environments before broad production enablement.

If any assumption is wrong, this spec should be updated before implementation starts.

## Objective
Introduce a non-breaking metadata architecture that supports both TMSL and TMDL in parallel using a shared connector interface. The system must preserve current sync behavior while enabling safe validation and phased adoption of TMDL.

Primary user outcomes:
- Platform engineers can ingest either TMSL or TMDL into identical SML output.
- Operators can toggle engines without redeploying major code paths.
- Teams can trace snapshot provenance and rollback to known-good TMSL snapshots quickly.

## Tech Stack
- Language: Python 3.x
- API/runtime: existing `src/semabridge/api` and `src/semabridge/core` modules
- Metadata conversion: existing `src/semabridge/converter` and `src/semabridge/connectors`
- Persistence: SQLAlchemy ORM + Alembic migrations
- Testing: `pytest` (unit + integration tests in `Tests/`)

## Commands
- Run unit tests:
`uv run pytest -q -p no:cacheprovider`
- Run targeted migration tests:
`uv run pytest Tests/test_tmsl_transformer.py Tests/test_snapshot_loading_regression.py -q -p no:cacheprovider`
- Compile key modules:
`uv run python -m py_compile src/semabridge/connectors src/semabridge/converter src/semabridge/repository/orm/models.py`
- Run alembic migration (project convention):
`uv run alembic upgrade head`

## Project Structure
- `src/semabridge/connectors/` -> source adapters and extractors for metadata formats
- `src/semabridge/converter/` -> format transforms to/from SML
- `src/semabridge/core/` -> orchestration, settings, source format routing
- `src/semabridge/repository/orm/` -> ORM models for snapshot persistence
- `src/semabridge/migrations/versions/` -> alembic migration files
- `Tests/` -> unit and integration verification
- `Docs/specs/` -> implementation specs and architecture decisions

## Code Style
Follow existing typed Python style with small explicit interfaces and dependency injection at orchestration boundaries.

```python
from typing import Protocol, Any

class IFabricMetadataConnector(Protocol):
    def extract(self, source_ref: str) -> dict[str, Any]:
        ...

    def parse_to_sml(self, raw_metadata: dict[str, Any]) -> dict[str, Any]:
        ...

    def build_from_sml(self, sml_payload: dict[str, Any]) -> dict[str, Any]:
        ...
```

Conventions:
- Keep adapters thin; reuse existing converter logic where possible.
- Avoid side effects in parse/build functions.
- Keep format-specific behavior behind connector implementations, not core orchestration.

## Testing Strategy
- Unit tests:
  - Connector contract tests for `TmslConnector` and `TmdlConnector`.
  - Golden parity tests: same dataset via TMSL and TMDL yields equivalent normalized SML JSON.
  - Serializer round-trip tests for TMDL folder hierarchy generation.
- Integration tests:
  - Shadow mode execution paths run TMSL as source of truth and log TMDL diffs.
  - Snapshot persistence includes `source_format` and supports rollback filtering.
- Regression tests:
  - Existing TMSL pipeline tests remain green with no behavioral regressions.

## Boundaries
- Always:
  - Keep TMSL as default execution path until rollout gate is explicitly approved.
  - Persist snapshot provenance (`source_format`) for every new snapshot.
  - Add tests for every new adapter path and toggle behavior.
- Ask first:
  - Any DB schema changes beyond `source_format` addition.
  - Any changes to public API payload shapes.
  - Enabling `USE_TMDL_ENGINE=true` outside staging.
- Never:
  - Remove or bypass legacy TMSL path during migration phases.
  - Auto-promote TMDL results into sync execution without parity checks.
  - Delete rollback or diagnostic metadata needed for incident recovery.

## Architecture and Rollout Plan
1. Define connector interface:
   - Add `IFabricMetadataConnector` protocol/base abstraction.
   - Wrap current TMSL logic in `TmslConnector` implementing the interface.
2. Add TMDL adapter in parallel:
   - Implement `TmdlConnector` extraction from folder structure.
   - Parse TMDL files into canonical SML JSON in memory.
   - Build TMDL folder/files from SML payload.
3. Add snapshot provenance:
   - Add `source_format` column (`TMSL`, `TMDL`) to snapshot ORM + migration.
   - Backfill existing rows to `TMSL` unless known otherwise.
4. Add shadow mode:
   - Introduce config toggle (default false): `USE_TMDL_ENGINE`.
   - When false: execute sync using TMSL; run TMDL in comparison-only path and log differences.
5. Validate collaboration workflow:
   - Ensure repository-sourced TMDL changes can be parsed to SML and stored as snapshots.
6. Phased rollout:
   - Enable `USE_TMDL_ENGINE=true` in staging first.
   - Promote to production after parity and stability criteria are met.
   - Deprecate TMSL only after sustained healthy operation window.

## Success Criteria
1. Both `TmslConnector` and `TmdlConnector` implement the same interface contract.
2. For agreed fixture datasets, normalized SML output from TMSL and TMDL is equivalent.
3. Snapshot records include `source_format` with zero nulls for newly created snapshots.
4. Shadow mode logs TMDL parity results without affecting current sync outputs.
5. Toggle rollback (`USE_TMDL_ENGINE=false`) restores legacy behavior immediately.
6. Existing TMSL regression suite passes unchanged.

## Implementation Notes (2026-05-18 additions)

- Added automated golden-parity unit tests: `Tests/test_tmdl_golden_parity.py`.
- Added focused unit tests for connector edge cases: `Tests/test_tmdl_connector_edgecases.py`.
- Added integration shadow test to validate parity artifact emission and snapshot persistence: `Tests/Integration/test_tmdl_shadow_integration.py`.
- Added CI workflow to run parity tests: `.github/workflows/tmdl-parity-tests.yml`.

These tests exercise the following runtime behaviors:
- TMDL shadow parsing in parallel with TMSL (parity artifacts written to `SEMABRIDGE_TMDL_PARITY_DIR`).
- Snapshot persistence records `source_format` on commit and can be queried for rollbacks.

Run locally:

```bash
python -m pytest Tests/test_tmdl_golden_parity.py Tests/test_tmdl_connector_edgecases.py Tests/Integration/test_tmdl_shadow_integration.py -q -p no:cacheprovider
```


## Open Questions
1. Exact TMDL scope for phase 1 parity:
   - Tables/measures/relationships only, or include roles/calculation groups/perspectives now?
2. Canonical diff strategy:
   - Structural JSON diff only, or semantic equivalence rules (ordering, formatting normalization)?
3. Rollout guardrails:
   - Required healthy run count and time window before production default flip?
4. Snapshot schema naming:
   - Reuse existing enum/types in `core/source_format.py` or add dedicated snapshot enum?

