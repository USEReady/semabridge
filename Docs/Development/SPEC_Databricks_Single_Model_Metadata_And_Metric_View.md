# Spec: Databricks Single Metadata Table and Single Metric View Per Model

## Objective
Create a Databricks deployment mode that emits exactly one metadata table and one metric view per semantic model, using a model-scoped naming scheme and supporting TPC-H-style snowflake joins.

Primary user groups:
- BI analysts (consume one stable model view)
- Data engineers (operate one predictable deploy artifact per model)
- Application/API consumers (query one canonical model view)

Success definition:
- Per model deployment creates:
  - One metadata table named <model>_metadata
  - One metric view named <model>_metric_view
- Cross-table metrics resolve through relationship joins, including:
  - orders join customer on o_custkey = c_custkey
  - customer join nation on c_nationkey = n_nationkey
- Applies across Databricks deployments, not just one model.

## Scope
In scope:
- Add a new Databricks deployment mode for model-level single-view generation.
- Standardize naming for single metadata and single metric view artifacts.
- Preserve current metadata row content semantics (row-per-object) while changing only table naming and view granularity.
- Support snowflake relationship paths for cross-table metrics in unified model view SQL/YAML generation.
- Add tests for naming, single-artifact counts, and join-based cross-table behavior.

Out of scope:
- Reworking semantic extraction pipeline.
- New UI workflows.
- Non-Databricks connector behavior.

## Assumptions
1. Databricks deployments can tolerate introduction of a new mode without forcing legacy mode removal.
2. Model names can be sanitized to valid Databricks identifiers deterministically.
3. Existing relationship graph data in SML is sufficient to build model-level join plans.
4. Metadata table schema can remain unchanged without downstream breaking changes.
5. A primary fact root can be determined explicitly or by deterministic rule for model-level view assembly.

## Constraints
- Must preserve backward compatibility by default (new mode opt-in first).
- Must not reduce existing deploy diagnostics (deploy_status, deploy_reason, translation_type).
- Must support existing behavior flags and avoid breaking current values in Config/behavior.yaml.
- Must support quote-safe SQL generation and identifier sanitization.
- Must keep implementation incremental and testable with current pytest suite.

## Data Model Reference (TPC-H Snowflake)
Required relationship support baseline:
- orders (fact)
  - key columns: o_orderkey, o_custkey, o_totalprice, o_orderdate, o_orderstatus
- customer (dimension)
  - key columns: c_custkey, c_name, c_mktsegment, c_nationkey
- nation (dimension)
  - key columns: n_nationkey, n_name, n_regionkey

Relationship edges:
- orders.o_custkey = customer.c_custkey
- customer.c_nationkey = nation.n_nationkey

## Naming Scheme
Default naming in new mode:
- Metadata table: <safe_model_name>_metadata
- Metric view: <safe_model_name>_metric_view

Notes:
- safe_model_name uses existing identifier sanitizer behavior.
- No dataset-level suffix in single-view mode.
- Existing view_prefix is ignored in single-view mode unless explicitly configured for compatibility aliasing.

## Proposed Configuration Additions
Add to DatabricksBehavior:
- model_artifact_mode: per_dataset | per_model
  - default: per_dataset (backward compatible)
- model_metadata_suffix: metadata
  - default: metadata
- model_metric_view_suffix: metric_view
  - default: metric_view
- model_fact_root: optional dataset name
  - default: empty (auto/deterministic root selection)

Behavior interactions:
- If model_artifact_mode = per_model:
  - generate one metadata table and one metric view
  - measure_view_mode combined/per_measure is bypassed by model-level generator
- If model_artifact_mode = per_dataset:
  - retain existing behavior

## Commands
Environment setup and validation commands:
- Configure environment: use workspace Python environment
- Run focused tests:
  - C:/Users/Premasai/AppData/Local/Programs/Python/Python311/python.exe -m pytest Tests/test_databricks_publisher.py -k "model_level or single_metadata or single_metric_view"
- Run broader Databricks publisher tests:
  - C:/Users/Premasai/AppData/Local/Programs/Python/Python311/python.exe -m pytest Tests/test_databricks_publisher.py
- Optional lint/type checks if configured in repo tooling.

## Project Structure
Primary files to update:
- src/semabridge/core/behavior.py
  - add new config fields and validation for model-level mode
- Config/behavior.yaml
  - add documented default values for new fields
- src/semabridge/connectors/databricks_publisher.py
  - add model-level artifact generator path
  - add model-scoped naming helpers
  - keep existing paths unchanged for per_dataset mode
- Tests/test_databricks_publisher.py
  - add and update tests for counts, naming, join behavior, and fallback
- Optional docs:
  - Docs/Development for implementation/rollout notes

## Code Style
Follow current style in publisher:
- Small helper methods for naming and statement generation.
- Existing constants and behavior fields preferred over inline literals.
- Preserve logger patterns and deploy summary structure.
- Avoid broad refactors; isolate mode-specific branching and keep legacy branch intact.

Example style target:

if self._dbx_behavior.model_artifact_mode == "per_model":
    return self._generate_model_level_artifacts(sml_model, resolved_view_type)
return self._generate_existing_artifacts(sml_model, resolved_view_type)

## Testing Strategy
Test levels:
1. Unit tests (required)
- Naming helper outputs for model metadata and model metric view.
- Mode switch behavior (per_model vs per_dataset).
- Join graph assembly for orders/customer/nation.
- Metadata table statement count and naming assertions.
- Metric view statement count and naming assertions.

2. Integration-like statement generation tests (required)
- generate_sql_statements returns one CREATE TABLE target and one CREATE VIEW target in per_model mode.
- Existing per_dataset mode snapshots remain unaffected.

3. Regression tests (required)
- Ensure prior bugs around unresolved placeholder columns remain guarded.
- Ensure deploy status metadata rows are still produced.

Acceptance test query examples for planning:
- Validate one metadata table DDL for model in statement set.
- Validate one model metric view DDL for model in statement set.
- Validate joined source relation includes customer and nation path when relationships exist.

## Boundaries
Always:
- Keep backward compatibility default behavior.
- Add/adjust tests for every logic path introduced.
- Preserve deploy diagnostics fields and logging quality.

Ask first:
- Any migration that drops or renames existing deployed artifacts automatically.
- Any change to metadata table schema columns.
- Any default mode change from per_dataset to per_model.

Never:
- Remove existing per_dataset generation path in this change.
- Introduce silent behavior changes without config guard.
- Bypass tests or remove failing tests.

## Risks
1. Ambiguous root dataset selection in complex models may generate unstable join plans.
2. Existing consumers may depend on dataset-level view names and break if switched without compatibility aliases.
3. Cross-table expression rewriting in unified model-level view could regress measure translation edge cases.
4. Large model-level views may increase SQL complexity and Databricks execution/planning latency.
5. Naming collisions with legacy artifacts if model name sanitization overlaps existing objects.

## Risk Mitigation
- Keep mode feature-flagged and default to per_dataset.
- Add optional compatibility alias mode in later phase if required.
- Add deterministic join-root selection rules and explicit model_fact_root override.
- Include regression tests for known translation and placeholder failure paths.
- Emit clear warnings when join graph is incomplete or ambiguous.

## Non-Goals
- No automatic cleanup of legacy dataset-level views in initial release.
- No redesign of metadata row schema.
- No attempt to optimize all SQL performance cases in first delivery.
- No changes to Snowflake/Fabric publishers.

## Acceptance Criteria
Functional:
1. In per_model mode, statement generation produces exactly one metadata table and one metric view per model.
2. Metadata table name follows <safe_model_name>_metadata.
3. Metric view name follows <safe_model_name>_metric_view.
4. Cross-table measures using TPC-H relationships compile into valid joined source relation where relationships exist.
5. Existing per_dataset mode behavior remains unchanged when mode not enabled.

Quality:
1. New/updated tests pass in Tests/test_databricks_publisher.py.
2. Existing relevant tests continue to pass.
3. No new syntax/lint errors in touched files.

Operational:
1. Logs indicate whether deployment used per_model or per_dataset mode.
2. Failure summaries still include affected view/table names and reasons.

## Planning Handoff: Implementation Slices
Slice 1: Config and naming contract
- Add behavior fields and defaults
- Add naming helper methods
- Add unit tests for naming

Slice 2: Model-level generation path
- Add model-level artifact statement generation
- Wire mode branching in generate_measure_view_statements or generate_sql_statements
- Add tests for single artifact counts

Slice 3: Join-aware model metric view assembly
- Build joined source relation from relationship graph
- Ensure expression rewriting and fallback handling
- Add TPC-H orders/customer/nation test coverage

Slice 4: Diagnostics and compatibility checks
- Preserve metadata rows and deploy statuses
- Verify publish summaries and warning surfaces
- Add regression tests for known unresolved-column behavior

## Open Questions
1. Should model_fact_root be mandatory in per_model mode, or optional with deterministic auto-selection?
2. Should compatibility aliases (old dataset-level view names) be generated temporarily in per_model mode?
3. Should per_model become default in a later release, and if so what migration window is required?
4. Should naming suffixes be user-configurable beyond metadata and metric_view defaults?
