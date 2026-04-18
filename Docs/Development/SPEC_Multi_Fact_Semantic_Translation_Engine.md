# Spec: Multi-Fact Semantic Translation Engine

## Objective
Upgrade SemaBridge translation so complex multi-fact Microsoft Fabric/Power BI semantic models are emitted as valid Databricks Unity Catalog Metric View YAML artifacts without dummy-table failures, SQL fan-out, or loss of core aggregation logic.

Primary users:
- Data engineers migrating enterprise semantic models from Fabric to Databricks.
- BI engineers validating metric parity after migration.
- Platform engineers maintaining connector reliability and deployment safety.

Success definition:
- Dummy measure-only tables are detected and removed from deployable output.
- Measures from dummy tables are relocated to their correct fact anchors.
- Multi-fact models produce separate fact-anchored YAML outputs (fact constellation), not one monolithic fan-out-prone artifact.
- Standard aggregation DAX translations (for example, SUM, COUNT, DISTINCTCOUNT patterns supported by current translator) remain preserved in generated SQL/YAML.
- Ambiguous or high-risk measures are queued for review instead of producing invalid SQL.

## Scope
In scope:
- Dummy table identification and measure distribution.
- DAX expression parsing to infer primary fact anchor.
- Multi-fact splitting into one Databricks Metric View YAML per fact table.
- Conformed dimension join mapping in each per-fact YAML.
- Strict SML -> OSI -> Databricks compilation path (no DAX intermediate output mode).
- Persistent state tracking for mappings/versioning using PostgreSQL ORM backend.
- Local/transient parsing support using DuckDB staging.
- Edge-case handling for orphaned measures and circular dependencies.

Out of scope:
- Full automatic translation for advanced time-intelligence DAX requiring custom UDF behavior.
- Redesign of non-Databricks connector pipelines.
- New frontend workflows in this change.

## Assumptions
ASSUMPTIONS I AM MAKING:
1. The Fabric extraction layer already returns enough metadata to distinguish physical source columns from synthetic measure-only table definitions.
2. Existing DAX translator utilities can be extended to expose measure lineage/fact anchors without replacing the full translator stack.
3. The current Databricks publisher can emit multiple YAML artifacts per model with deterministic naming.
4. PostgreSQL ORM persistence is available in environments where long-lived mapping/version state is required.
5. Existing OSI/SML model classes are the canonical intermediate contract and should remain the single source of truth.

If any assumption is incorrect, update this spec before implementation.

## Constraints
- Must avoid breaking existing single-fact or single-artifact flows unless an explicit behavior flag enables the new mode.
- Must preserve deterministic identifier sanitization and naming conventions used by Databricks emitters.
- Must not silently downgrade unsupported complex DAX into incorrect aggregations.
- Must preserve deployment diagnostics (status, reasons, skipped details).
- Must keep implementation incremental, test-backed, and reviewable.

## Tech Stack
- Python 3.11+
- Pydantic-based SML/OSI intermediate models
- Databricks connector and YAML/SQL generation pipeline
- SQLAlchemy ORM (PostgreSQL) for persistent state
- DuckDB for localized transient staging
- Pytest for verification

## Commands
Environment and validation commands for this spec:

- Install dependencies:
  - uv sync
- Run targeted translation and publisher tests:
  - uv run pytest Tests/test_databricks_publisher.py -k "metric_view or combined or multi_table"
  - uv run pytest Tests/test_metric_view_joins.py
  - uv run pytest Tests/test_metric_view_yaml_joins.py
- Run mapping/translation diagnostics tests:
  - uv run pytest Tests/test_mapping_identifier_diagnostics.py
  - uv run pytest Tests/test_mapping_metric_visibility_fallback.py
- Run full test suite before merge:
  - uv run pytest Tests/
- Run lint/type checks (recommended):
  - uv run ruff check src Tests
  - uv run mypy src/semabridge

## Project Structure
Primary touch points expected:

- src/semabridge/connectors/
  - Fabric extraction and model resolution logic
  - Databricks publisher logic for per-fact artifact generation
- src/semabridge/converter/
  - DAX parsing and measure anchor inference
  - SML/OSI transformation and measure relocation
- src/semabridge/intermediate/
  - Intermediate model fields for relocated measures and review-queue annotations
- src/semabridge/repository/
  - ORM-backed state/version mapping persistence
- Config/behavior.yaml
  - Behavior flags for multi-fact splitting and strict handling modes
- Tests/
  - Databricks publisher, join generation, mapping diagnostics, and DAX translation regressions

## Functional Requirements

### FR1: Dummy Table Resolution and Measure Distribution
1. Detect dummy measure tables:
  - A table is classified as dummy when it has measures but no physical source columns.
2. Parse each dummy-table measure DAX to infer target fact anchor.
3. Relocate each measure into the inferred fact table metadata in SML state.
4. If dummy table becomes empty after relocation, drop it from downstream translation/deployment artifacts.
5. Record relocation mapping and reason codes in persistent state for traceability.

### FR2: Multi-Fact Splitting (Fact Constellation)
1. Detect fact tables as many-side anchors in one-to-many relationships.
2. Generate one Databricks Metric View YAML per fact table.
3. Include conformed/shared dimensions in each fact YAML joins block.
4. Prevent direct fact-to-fact fan-out joins in generated SQL/YAML.
5. Preserve stable naming for generated per-fact artifacts.

### FR3: Intermediate Format Standardization
1. Enforce Fabric -> SML -> OSI -> Databricks path for this flow.
2. Do not expose DAX as an intermediate output format for this pipeline mode.
3. Compile final Databricks YAML only from finalized SML/OSI state.

### FR4: State Management
1. Persist measure relocation decisions, versioned model mapping, and translator outcomes in PostgreSQL.
2. Use DuckDB only for transient/local staging during parse/transform phases.
3. Ensure transactional consistency for relocation + split decisions per model version.

## Edge Cases and Error Handling

### EC1: Orphaned Measures
- If a measure references multiple fact tables without a deterministic primary anchor:
  - Do not auto-place into any fact YAML.
  - Add to a review-required queue with lineage diagnostics.
  - Mark deployment summary with explicit review-required counts.

### EC2: Circular Dependencies
- Detect cycles in relationship graph before YAML join generation.
- Break cycles using deterministic policy (for example, remove lowest-confidence edge first) and log decision.
- If cycle cannot be safely broken, fail that fact artifact with actionable diagnostics.

### EC3: Unsupported Complex DAX
- If DAX pattern is outside supported translation envelope:
  - Keep measure metadata.
  - Mark translation as manual-review required.
  - Do not emit misleading generic replacement aggregations unless explicitly configured fallback exists.

## Non-Functional Requirements
- Deterministic outputs: same input model and config produce byte-stable logical YAML output ordering.
- Observability: publish summary includes relocated measures, review queue size, skipped artifacts, and cycle decisions.
- Performance: splitting logic should not introduce unbounded graph traversal; relationship analysis must be linear or near-linear in model edge count.
- Safety: no secrets in logs; diagnostics should reference object names and IDs only.

## Code Style
Guidelines:
- Keep translation decisions in small, testable helper methods.
- Keep behavior gating explicit and centrally configured.
- Preserve existing logger patterns and deploy reason conventions.
- Avoid broad refactors outside the required translation path.

Style example:

```python
if self._is_dummy_measure_table(table):
    relocated, unresolved = self._relocate_dummy_table_measures(table, model)
    self._record_measure_relocation(table.name, relocated, unresolved)
```

## Testing Strategy
Test framework:
- Pytest (existing repository configuration)

Required tests:

1. Unit tests
- Dummy table detection with and without physical columns.
- DAX anchor inference for single-fact and ambiguous multi-fact measures.
- Dummy table purge behavior after relocation.

2. Integration-like translation tests
- Multi-fact input model yields N fact-anchored YAML artifacts for N detected facts.
- Conformed dimensions appear consistently in each fact artifact joins block.
- No generated SQL path contains unsafe fact-to-fact fan-out joins.

3. Regression tests
- Existing single-fact model behavior remains unchanged when feature flag is disabled.
- Existing Databricks YAML generation tests remain green.
- Translation diagnostics remain populated and actionable.

4. State management tests
- ORM writes are transactional for relocation/splitting decisions.
- DuckDB staging artifacts are not persisted as authoritative state.

## Boundaries
Always:
- Preserve correctness over aggressive auto-translation.
- Surface ambiguous translation decisions explicitly.
- Add or update tests for each new behavior branch.

Ask first:
- Changing default behavior from single-artifact mode to multi-fact split mode.
- Altering existing OSI/SML schema contracts.
- Adding new external dependencies.

Never:
- Emit SQL that knowingly introduces fact-to-fact fan-out.
- Replace unsupported complex DAX with deceptive placeholder logic.
- Commit secrets or environment credentials in code/docs.

## Risks
1. Anchor inference ambiguity can route measures to wrong facts.
2. Relationship quality issues in source models can degrade split quality.
3. Increased artifact counts can impact deploy orchestration and monitoring.
4. Backward compatibility risk if multi-fact mode leaks into legacy defaults.
5. Cycle-breaking heuristics may hide genuine modeling defects if diagnostics are weak.

## Risk Mitigation
- Feature flag multi-fact splitting with safe default-off rollout.
- Add confidence scoring and explicit unresolved queue for anchor inference.
- Emit detailed per-artifact diagnostics and summary counters.
- Add regression coverage for legacy behavior and known fan-out failure signatures.
- Enforce deterministic cycle handling with logged decision trace.

## Acceptance Criteria
Functional:
1. Dummy measure-only tables are detected and excluded from final Databricks artifacts after relocation.
2. Measures from dummy tables are relocated to inferred fact anchors when confidence is sufficient.
3. Multi-fact models generate separate YAML artifacts per fact table.
4. Generated joins preserve conformed dimensions without creating fact-to-fact fan-out.
5. Ambiguous measures are queued for manual review and not auto-emitted as invalid SQL.
6. Final Databricks YAML is generated from SML/OSI finalized state only.

Quality:
1. New and updated tests for translation/splitting behavior pass.
2. Existing relevant Databricks and mapping tests remain green.
3. No new lint/type issues in touched files.

Operational:
1. Deployment summary reports relocated measure counts, review queue counts, and per-fact artifact outcomes.
2. Persistent state contains versioned relocation/splitting decisions with traceable identifiers.

## Planning Handoff (Next Workflow: /plan)
Workstream A: Feature contract and flags
- Define behavior flags and defaults in behavior/config contracts.

Workstream B: Dummy table and measure relocation
- Implement detection, DAX anchor inference, relocation, purge.

Workstream C: Fact constellation splitting
- Implement fact anchor detection and per-fact YAML generation.

Workstream D: Graph safety and edge handling
- Add fan-out protection, orphan queue handling, cycle detection/breaking.

Workstream E: Persistent state and observability
- Add ORM persistence, version linking, diagnostics counters, and summaries.

Workstream F: Test hardening and regression safety
- Add targeted + regression tests; verify legacy path behavior.

Dependency order:
1. A
2. B and E (in parallel where possible)
3. C
4. D
5. F

## Open Questions
1. Should multi-fact split mode be opt-in initially or enabled by default for Databricks targets?
2. What confidence threshold should be required to auto-relocate a measure from a dummy table?
3. Should review-required measures block the full model deployment, or only skip affected fact artifacts?
4. What naming convention should be canonical for per-fact YAML files in UC environments?
5. Should cycle-breaking be strict-fail by default in production and warn-only in development?