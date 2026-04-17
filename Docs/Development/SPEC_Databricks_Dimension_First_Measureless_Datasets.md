# Spec: Databricks Dimension-First Deployment for Measure-Less Datasets

## Objective
Ensure Databricks deployment treats measure-less datasets as first-class semantic dimensions so metrics from fact datasets can always be grouped, filtered, and joined through a complete relationship graph.

Primary users:
- BI analysts using Databricks AI/BI for slice-and-dice analysis.
- Data engineers operating semantic sync pipelines.
- Platform engineers enforcing semantic consistency across environments.

Success definition:
- Dataset coverage is complete for semantic deployment, including datasets with zero explicit measures.
- Relationship paths needed for filtering and grouping are preserved in deployed artifacts.
- Dimension-only datasets produce deployable semantic artifacts with deterministic implicit metrics.

## Scope
In scope:
- Standardize default Databricks behavior to deploy all datasets, including measure-less datasets.
- Define implicit metric policy for measure-less datasets:
  - Always emit total_rows.
  - Emit distinct_count(primary_key) when a confident key is available.
- Enforce semantic graph integrity checks for dataset coverage before view generation.
- Preserve compatibility across per_model and per_dataset artifact modes.
- Add deployment diagnostics that distinguish:
  - skipped for missing source,
  - deployed as dimension-only,
  - deployed with implicit metrics.
- Add tests for coverage, implicit metrics, graph integrity, and regression safety.

Out of scope:
- Replacing existing DAX translation architecture.
- Introducing a new frontend workflow.
- Changing Snowflake or Fabric publisher behavior.
- Automatic cleanup of previously deployed Databricks artifacts.

## Assumptions
ASSUMPTIONS I AM MAKING:
1. Current Databricks runtime supports metric views used by this project and accepts generated YAML/SQL patterns.
2. Existing semantic relationships in SML are authoritative for join topology.
3. Primary-key confidence can be inferred from dataset metadata and/or relationship definitions without requiring a new external catalog.
4. A higher count of generated artifacts is acceptable when it improves semantic correctness.
5. Backward compatibility of existing behavior flags must be preserved unless explicitly changed in config.

If any assumption is wrong, update this spec before implementation.

## Constraints
- Must preserve existing public behavior controls in src/semabridge/core/behavior.py and Config/behavior.yaml.
- Must keep deterministic naming and identifier sanitization rules.
- Must avoid silent regressions in deploy metadata rows and status reasons.
- Must support both artifact modes:
  - per_dataset
  - per_model
- Must remain compatible with current test runner and CI expectations.
- Must keep changes incremental and reviewable.

## Tech Stack
- Python 3.11+
- Pydantic settings/models
- Databricks SQL Statements API integration
- Pytest for unit/regression testing

## Commands
Environment and verification commands for this spec:

- Install dependencies:
  - uv sync
- Run focused Databricks tests:
  - uv run pytest Tests/test_databricks_publisher.py -k "combined or metric_view or dataset coverage"
- Run full Databricks publisher tests:
  - uv run pytest Tests/test_databricks_publisher.py
- Run full suite (optional before merge):
  - uv run pytest Tests/
- Run lint/type checks (recommended):
  - uv run ruff check src Tests
  - uv run mypy src/semabridge

## Project Structure
Expected touch points:
- src/semabridge/connectors/databricks_publisher.py
  - coverage logic for no-metric datasets
  - implicit metric generation rules
  - graph integrity validation and diagnostics
- src/semabridge/core/behavior.py
  - behavior toggles and defaults for dimension-first policy
- Config/behavior.yaml
  - documented defaults for rollout behavior
- Tests/test_databricks_publisher.py
  - unit and regression tests
- Optional docs update:
  - Docs/MULTI_TABLE_METRIC_VIEWS.md

## Code Style
Guidelines:
- Prefer small helper methods over large branching blocks.
- Preserve existing logger and deploy-reason conventions.
- Keep mode-specific behavior explicit and feature-gated where needed.
- Do not refactor unrelated translation logic.

Style example:

```python
if not metrics:
    measure_expressions.extend(self._build_dimension_only_measures(dataset, sml_model))
```

## Testing Strategy
Test framework:
- Pytest with existing repo configuration.

Required test levels:
1. Unit tests
- Measure-less dataset in combined mode emits total_rows.
- distinct_count(primary_key) appears only when key confidence is satisfied.
- Dataset coverage counters reflect expected attempted/created/skipped values.

2. Integration-like statement generation tests
- per_model mode includes dimension-only datasets in model-level artifacts.
- per_dataset mode includes dimension-only views when policy is enabled.
- Missing source behavior still respects on_missing_source policy.

3. Regression tests
- Existing metric translation skip reasons remain stable.
- Existing cross-table join behavior is not regressed.
- Existing metadata row deployment status fields remain populated.

Coverage expectation:
- New logic paths must be directly asserted in Tests/test_databricks_publisher.py.

## Boundaries
Always:
- Keep semantic completeness as the default intent for Databricks deployment.
- Preserve deploy diagnostics quality and explicit reason codes.
- Add tests for each new behavior branch.

Ask first:
- Any behavior default change that affects existing production deployments.
- Any schema change to metadata tables.
- Any new dependency introduction.

Never:
- Remove existing deployment modes.
- Remove failing tests to make CI pass.
- Introduce silent fallback behavior that hides coverage loss.

## Risks
1. Artifact growth risk:
- Deploying measure-less datasets can increase number of generated views/artifacts.

2. Semantic ambiguity risk:
- Primary key inference may be ambiguous in some datasets, producing inconsistent distinct counts.

3. Runtime complexity risk:
- Additional dimensions and joins may increase query planning complexity.

4. Compatibility risk:
- Existing consumers may assume only metric-bearing datasets are deployed.

5. Diagnostics drift risk:
- New paths may under-report skip reasons unless explicitly covered.

## Risk Mitigation
- Use explicit behavior flags and safe defaults for rollout.
- Gate distinct_count(primary_key) on confidence checks with clear logging.
- Add deterministic key selection rules and fallback to total_rows-only when uncertain.
- Preserve and extend skipped_details/deploy metadata mappings.
- Add regression tests for both artifact modes and missing-source scenarios.

## Non-Goals
- No automatic creation of advanced business metrics beyond total_rows and distinct_count(primary_key).
- No redesign of the semantic model format.
- No optimization of all query performance characteristics in this change.
- No changes to non-Databricks connectors.

## Acceptance Criteria
Functional:
1. Measure-less datasets are deployable semantic artifacts by default in Databricks deployment paths.
2. Each measure-less dataset emits total_rows.
3. distinct_count(primary_key) is emitted only when confident primary key detection succeeds.
4. Relationship graph integrity checks detect and surface missing dataset coverage before publish.
5. Existing fact-measure deployment behavior remains intact.

Quality:
1. Tests covering new behavior pass in Tests/test_databricks_publisher.py.
2. Existing relevant Databricks publisher tests continue to pass.
3. No new lint/type errors are introduced in touched files.

Operational:
1. Deployment logs and summary include explicit counts for dimension-only artifact handling.
2. Deploy metadata exposes whether a measure was implicit or explicit where applicable.

## Implementation Plan Inputs (Planning Handoff)
Workstream A: Coverage policy and behavior contract
- Confirm default policy and any rollout flags.
- Define exact behavior for per_model and per_dataset modes.

Workstream B: Implicit metric generation
- Implement reusable helper for dimension-only metric synthesis.
- Add primary key confidence detection helper and fallback logic.

Workstream C: Graph integrity validation
- Add pre-generation validation for relationship endpoint coverage.
- Define error/warn behavior based on deployment mode and settings.

Workstream D: Observability and metadata
- Extend summary counters and skipped/deployed reason reporting.
- Ensure manifest compatibility.

Workstream E: Test and regression hardening
- Add targeted tests for all new branches.
- Re-run existing Databricks publisher regressions.

Dependency order:
1. Workstream A
2. Workstream B and C in parallel
3. Workstream D
4. Workstream E

## Open Questions
1. Should distinct_count(primary_key) be default-on globally or controlled by a dedicated behavior flag?
2. What minimum confidence threshold should be required for key inference?
3. Should graph integrity failures block deployment in all environments or only in strict mode?
4. Should dimension-only deployment markers be surfaced in UI/API status endpoints in this same change or a follow-up?