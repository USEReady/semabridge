# Implementation Plan: Official Semantic Payload Migration

## Overview
Replace the custom intermediate semantic format with an official semantic contract aligned to SML and OSI, then persist the parsed model in PostgreSQL using a dedicated JSONB-backed ORM table. The migration keeps the modular emitter architecture intact while changing the Snowflake builder to consume parsed semantic payloads instead of bespoke intermediate objects.

The goal is to make the official semantic payload the single source of truth at the persistence and emitter boundary, while keeping YAML import/export deterministic and avoiding any custom or unofficial schema extensions.

## Architecture Decisions
- Use a dedicated PostgreSQL ORM model for parsed semantic models with relational fields for identity, versioning, source/target platform metadata, and a JSONB payload column.
- Treat YAML as an import/export transport only; parse to Python dictionaries before persistence and serialize from dictionaries when exporting files.
- Preserve the builder-based emitter layout, but change the Snowflake builder interface so it consumes the stored JSONB payload rather than a custom intermediate model class.
- Enforce strict official-schema mapping for Datasets, Metrics, Dimensions, and Relationships only; no custom properties, no DAX conversion layer, and no UI work in this refactor.

## Task List

### Phase 1: Persistence Foundation

### Task 1: Add JSONB semantic model ORM

**Description:** Introduce a PostgreSQL-oriented ORM model for stored semantic payloads with `id`, `model_name`, `version`, `source_platform`, `target_platform`, and `payload` as JSONB.

**Acceptance criteria:**
- [ ] The ORM model is defined in the SQLAlchemy layer and uses `JSONB` from `sqlalchemy.dialects.postgresql` for the payload column.
- [ ] The model includes basic relational metadata fields and maps cleanly to a PostgreSQL table.
- [ ] The model is isolated from cross-dialect text-JSON helpers so the existing portable models stay unchanged.

**Verification:**
- [ ] Add or update a focused ORM test for table metadata and column types.
- [ ] Validate the model imports cleanly in the ORM package.

**Dependencies:** None

**Files likely touched:**
- `src/semabridge/repository/orm/models.py`
- `src/semabridge/repository/orm/__init__.py`
- `src/semabridge/repository/orm/base.py` if a shared base helper is needed

**Estimated scope:** Small

### Task 2: Add deterministic YAML import/export helpers

**Description:** Wire a robust YAML library for reading and writing `.yml` artifacts so the semantic payload can be round-tripped as a Python dictionary before and after PostgreSQL storage.

**Acceptance criteria:**
- [ ] YAML parsing returns plain Python dictionaries suitable for JSONB storage.
- [ ] YAML serialization is deterministic enough for snapshot tests and preserves official semantic structure ordering where possible.
- [ ] The helpers do not inject custom keys or normalize into unofficial shapes.

**Verification:**
- [ ] Add a focused round-trip test for load -> dict -> dump -> load.
- [ ] Confirm existing YAML consumers still work with the helper entry points.

**Dependencies:** Task 1

**Files likely touched:**
- `src/semabridge/...` YAML utility module
- `Tests/...` YAML round-trip test module

**Estimated scope:** Small

### Phase 2: Official Payload Pipeline

### Task 3: Map ingestor output to official semantic structures

**Description:** Update the ingestor so source metadata is mapped directly into the official semantic payload shape using the standard semantic constructs only: Datasets, Metrics, Dimensions, and Relationships.

**Acceptance criteria:**
- [ ] Ingested payloads use only official semantic fields and omit custom intermediate keys.
- [ ] Source platform metadata and target platform metadata are captured without polluting the semantic object graph.
- [ ] The mapping layer has explicit validation for unsupported or unofficial fields.

**Verification:**
- [ ] Add a fixture-based ingestion test for one representative source model.
- [ ] Validate that unsupported keys are rejected or stripped according to the chosen policy.

**Dependencies:** Task 1, Task 2

**Files likely touched:**
- `src/semabridge/...` ingestor or semantic normalization module
- `Tests/...` ingestion mapping tests

**Estimated scope:** Medium

### Task 4: Refactor Snowflake builder to consume JSONB payloads

**Description:** Update the Snowflake emitter builder interface so it accepts the parsed semantic payload from storage and builds outputs from the official payload dictionary instead of the old custom intermediate model.

**Acceptance criteria:**
- [ ] The builder interface accepts the JSONB-backed payload in a typed, explicit contract.
- [ ] Builder logic reads the official semantic constructs directly from the payload.
- [ ] Existing modular emitter boundaries stay intact; only the input contract changes.

**Verification:**
- [ ] Add a focused builder unit test that feeds a parsed payload dictionary.
- [ ] Confirm Snowflake output generation still works for a representative model.

**Dependencies:** Task 3

**Files likely touched:**
- `src/semabridge/connectors/snowflake_emitter.py`
- `src/semabridge/connectors/ddl_builder.py`
- `src/semabridge/connectors/snowflake_emitter_parts/*`
- `Tests/...` Snowflake builder tests

**Estimated scope:** Medium

### Checkpoint: After Tasks 1-4
- [ ] PostgreSQL JSONB semantic model persists and loads successfully.
- [ ] YAML import/export round-trips cleanly through the helper layer.
- [ ] Ingested payloads are validated against official semantic constructs only.
- [ ] Snowflake builder accepts the new payload contract and produces expected output.

### Phase 3: Compatibility, Testing, and Rollout

### Task 5: Add migration and backfill path

**Description:** Create the database migration and any backfill logic needed to move existing stored semantic assets into the new JSONB-backed table.

**Acceptance criteria:**
- [ ] The database migration creates the new semantic payload table or columns without breaking existing data.
- [ ] A backfill path can convert legacy stored artifacts into parsed dicts for JSONB storage.
- [ ] Existing consumers can be migrated incrementally rather than in a single cutover.

**Verification:**
- [ ] Run migration tests against a PostgreSQL-targeted test database.
- [ ] Confirm legacy records can be rehydrated into the new format.

**Dependencies:** Tasks 1-4

**Files likely touched:**
- `src/semabridge/migrations/versions/*`
- `src/semabridge/repository/...` migration helpers
- `Tests/...` migration/backfill tests

**Estimated scope:** Medium

### Task 6: Add regression coverage and documentation updates

**Description:** Add tests and docs for the official semantic payload migration, including contract boundaries, unsupported-field handling, and the updated Snowflake builder interface.

**Acceptance criteria:**
- [ ] Tests cover ORM persistence, YAML round-trip, ingestion mapping, and builder consumption.
- [ ] Documentation explains the official semantic contract and the PostgreSQL JSONB storage decision.
- [ ] No frontend/UI components are introduced as part of this refactor.

**Verification:**
- [ ] Run the focused semantic-model test subset.
- [ ] Review docs for any mention of unofficial fields, DAX conversion, or removed UI surfaces.

**Dependencies:** Tasks 1-5

**Files likely touched:**
- `Docs/README.md` or a migration note under `Docs/ideas/`
- `Tests/...` regression tests

**Estimated scope:** Medium

### Checkpoint: Complete
- [ ] Official semantic payload storage is live behind the new ORM model.
- [ ] Snowflake emitter consumes the parsed JSONB payload.
- [ ] YAML import/export remains deterministic and officially shaped.
- [ ] Migration notes and tests are updated for review.

## Risks and Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Official schema interpretation drifts from source specs | High | Keep the mapping rules narrow, review against the official SML/OSI docs, and reject any unofficial field additions. |
| PostgreSQL-only JSONB storage creates cross-dialect friction | Medium | Isolate the new table from portable ORM helpers and keep the existing cross-dialect models unchanged. |
| Emitter contract changes break downstream builder code | Medium | Introduce the new payload interface behind a small adapter boundary and test one representative Snowflake flow end to end. |
| YAML serialization differences create noisy diffs | Low | Use one YAML library consistently and snapshot the canonical output. |

## Open Questions
- Which exact official document should be treated as the canonical source when SML and OSI overlap on field naming or semantics?
user can chosse the fomat in the project page creation based on that 
- Should the legacy intermediate store remain readable for one release window, or is a one-way migration acceptable?
one way 
- What is the preferred location for the new PostgreSQL migration helpers: repository layer, service layer, or a dedicated semantic storage module?
