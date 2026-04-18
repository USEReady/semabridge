# Spec: Unified Fact-Centric Semantic Translation

## Objective
Restructure SemaBridge Databricks publishing to a unified fact-centric architecture that generates one unified metric view per fact root, with recursive nested joins, role-playing dimension aliases, and context-aware measure allocation.

Primary user:
- Data Engineer operating semantic model translation and Databricks deployment.

Success definition:
- Complex source models are compiled into fact-anchored unified metric views that avoid fan-out by construction.
- Measures are distributed only to eligible fact contexts (root fact or reachable joined dimensions), not broadcast globally.
- Connector Config is the single deployment path and surfaces compile transparency (source tables vs generated unified views).

## Scope
In scope:
- Fact table identification from parsed SML model.
- Directed recursive relationship traversal for join graph assembly.
- Nested joins for snowflake schemas (Fact -> Dimension -> Sub-dimension).
- Role-playing dimension alias handling for repeated dimension relationships.
- Context-aware measure distribution into per-fact unified views.
- Databricks YAML generation loop per fact root (source, joins, dimensions, measures).
- ORM-backed persistent graph/model state and traversal execution support.
- Connector Config integration for deployment and progress summary UX.

Out of scope:
- Full automatic translation of advanced time-intelligence DAX requiring custom Databricks UDFs.
- Re-architecture of non-Databricks target connectors.
- Broad UI redesign beyond Connector Config flow integration and progress transparency.

## Assumptions
ASSUMPTIONS I AM MAKING:
1. Parsed SML relationship metadata is sufficient to construct recursive directed join graphs without introducing a new external metadata service.
2. Existing Databricks publisher paths can be incrementally extended to fact-centric output instead of fully rewritten from scratch.
3. Default mode policy is enabled for new projects while preserving backward-safe behavior for existing projects via feature/config controls.
4. Ambiguous measure anchors should skip affected measures/artifacts and continue deployment with explicit review-required diagnostics.
5. Connector Config UI integration is launch-blocking for this feature family (backend and UI both required for release).

If any assumption is incorrect, update this spec before implementation.

## Constraints
- SML/OSI is the only intermediate contract for this flow; DAX must not appear as a selectable intermediate output mode.
- Relationship versioning and persistent model state must use PostgreSQL ORM storage.
- DuckDB use is limited to transient/local parse-time evaluation only.
- Fan-out-safe SQL generation is mandatory; no knowingly unsafe fact-to-fact join plans may be emitted.
- New behavior must remain deterministic and testable with explicit diagnostics for skipped/review-required items.

## Tech Stack
- Python 3.11+
- SML/OSI Pydantic model layer
- Databricks publisher and YAML/SQL generation stack
- SQLAlchemy ORM with PostgreSQL for persistent graph/model state
- DuckDB for local transient parse-state operations only
- FastAPI backend + existing React/Vite frontend Connector Config experience
- Pytest for unit and integration-style regression validation

## Commands
Environment and validation commands for this spec:

- Install dependencies:
  - uv sync
- Run focused Databricks publisher tests:
  - uv run pytest Tests/test_databricks_publisher.py -k "metric_view or per_model or joins or dummy"
- Run relationship and YAML join tests:
  - uv run pytest Tests/test_metric_view_joins.py
  - uv run pytest Tests/test_metric_view_yaml_joins.py
- Run mapping and diagnostics regressions:
  - uv run pytest Tests/test_mapping_identifier_diagnostics.py
  - uv run pytest Tests/test_mapping_metric_visibility_fallback.py
- Run full backend suite before merge:
  - uv run pytest Tests/
- Run lint/type checks:
  - uv run ruff check src Tests
  - uv run mypy src/semabridge
- Run frontend checks for Connector Config integration:
  - cd frontend
  - npm run lint
  - npm run build

## Project Structure
Primary touch points expected:

- src/semabridge/connectors/
  - databricks_publisher.py: per-fact unified view generation loop and YAML assembly
  - relationship/join helpers for nested and role-playing joins
- src/semabridge/converter/
  - SML-based measure routing logic and anchor resolution
- src/semabridge/sml/
  - relationship model usage and alias metadata consumption
- src/semabridge/repository/
  - ORM entities/repositories for persistent graph/version traversal state
- src/semabridge/api/
  - Connector Config deployment orchestration and compile summary API payloads
- frontend/src/
  - Connector Config deployment step integration and progress summary rendering
- Config/behavior.yaml
  - rollout toggles and strictness controls
- Tests/
  - publisher, join graph, mapping diagnostics, and integration regressions

## Functional Requirements

### FR1: Fact Identification
1. Identify fact tables from SML by either:
  - many-side participation in one-to-many relationships, or
  - explicit base aggregation presence.
2. Build a deterministic fact_tables list for output iteration.

### FR2: Directed Recursive Join Graph Resolution
1. For each fact root, resolve direct one-to-many connected dimensions.
2. Recursively resolve nested dimension chains.
3. Prevent cyclic traversal loops using visited-edge/visited-node safety checks.
4. Support role-playing dimensions by aliasing repeated dimension table paths.

### FR3: Context-Aware Measure Distribution
1. A measure is included in a fact view only when its anchor lineage targets:
  - the fact root dataset, or
  - a dataset reachable from the fact root join graph.
2. Measures from dummy/measure-only datasets must be relocated only when deterministic anchor inference exists.
3. Ambiguous measures are queued as review-required and excluded from emitted SQL/YAML for affected facts.

### FR4: Unified Fact-Centric YAML Output
For each fact root, emit one YAML artifact containing:
1. source: fact root source table
2. joins: recursive hierarchical join tree (including role aliases)
3. dimensions: selected fields from reachable joined datasets with qualified expressions
4. measures: filtered context-eligible metric list

### FR5: UI Integration and Transparency
1. Connector Config is the only launch path for this deployment mode.
2. During compilation, present summary:
  - source table count
  - generated unified metric view count
  - skipped/review-required measure count
3. Preserve established minimal dark-themed visual language in the flow.

### FR6: State and Traversal Execution
1. Persist SML graph and versioned traversal state in PostgreSQL ORM.
2. Use ORM-backed state for recursive traversal and version diffs.
3. Keep DuckDB transient only for local parse-time computations.

## Non-Functional Requirements
- Determinism: same model/config yields stable view naming, join ordering, and measure ordering.
- Safety: fan-out prevention and ambiguity handling must fail safe (skip + diagnose, not emit invalid SQL).
- Observability: deployment summary includes counts for compiled views, skipped measures, unresolved anchors, and cycle handling events.
- Performance: traversal complexity scales near-linearly with model graph size for typical enterprise semantic models.
- Backward compatibility: legacy behavior remains available through explicit configuration for existing projects.

## Code Style
Guidelines:
- Keep traversal, measure filtering, and YAML assembly in small focused helpers.
- Prefer deterministic ordering and explicit reason codes over implicit fallback behavior.
- Keep feature gating centralized in behavior/config objects.
- Avoid broad refactors unrelated to unified fact-centric output.

Style example:

```python
if self._is_measure_anchor_reachable(fact_root, measure_anchor, join_graph):
    fact_measures.append(measure)
else:
    review_required.append(self._build_unresolved_measure_record(measure, reason="unreachable_anchor"))
```

## Testing Strategy
Test framework:
- Pytest (backend) and existing frontend lint/build checks.

Required tests:

1. Unit tests
- Fact identification from relationship/cardinality patterns.
- Recursive traversal resolution including nested joins.
- Role-playing alias generation and collision handling.
- Measure context filter inclusion/exclusion logic.

2. Integration-like generation tests
- Multi-table source compiles into per-fact unified YAML outputs.
- Each output contains source + joins + dimensions + scoped measures.
- Known fan-out scenarios are rejected or re-routed safely.

3. Regression tests
- Existing per-model/per-dataset behaviors remain stable behind config boundaries.
- Existing Databricks publisher tests remain green.
- Existing mapping/diagnostic tests remain green.

4. UI integration tests
- Connector Config successfully triggers compile/deploy flow.
- Progress summary renders expected counts and status states.

## Boundaries
Always:
- Keep SML/OSI as the single intermediate source of truth for this pipeline.
- Preserve transparent diagnostics for all skipped or unresolved entities.
- Add tests for each new branching behavior before merging.

Ask first:
- Any default behavior change affecting existing project rollout semantics.
- Any schema migration that changes persisted ORM model compatibility.
- Any new third-party dependency.

Never:
- Emit SQL with known unsafe fan-out joins.
- Re-introduce DAX as a selectable intermediate format in UI for this pipeline.
- Persist long-lived traversal/model state in DuckDB.

## Risks
1. Relationship graph quality in source models may be incomplete or inconsistent.
2. Measure anchor inference may be ambiguous for complex cross-fact formulas.
3. Recursive traversal with role aliases may produce naming collisions without strict normalization.
4. UI launch-blocking scope may delay release if backend and frontend cadence diverge.
5. Default-on for new projects could increase support burden if diagnostics are insufficient.

## Risk Mitigation
- Add strict validation of relationship endpoint integrity before generation.
- Enforce confidence-gated anchor inference and review-required queues.
- Use deterministic alias generation with collision suffixing and explicit diagnostics.
- Phase backend contract first, then wire UI with a stable summary payload.
- Roll out with canary projects and monitor compile failure/review-required rates.

## Non-Goals
- Building a full symbolic DAX engine for all advanced time-intelligence semantics in this release.
- Replacing all existing Databricks generation modes in one cutover.
- Redesigning global frontend navigation/theme beyond Connector Config integration needs.
- Implementing cross-target (Fabric/Snowflake) unified fact-centric generation in this phase.

## Acceptance Criteria
Functional:
1. For a multi-fact source model, generation emits one unified metric view YAML per detected fact root.
2. Each generated artifact contains source, recursive joins, dimensions, and context-filtered measures.
3. Role-playing dimensions appear as distinct aliased joins in output.
4. Ambiguous/unreachable measures are excluded from affected outputs and recorded as review-required.
5. DAX is not exposed as selectable intermediate format in this translation flow.
6. Connector Config initiates and completes deployment without requiring separate workspace settings pages.

Quality:
1. New and updated tests for traversal, measure filtering, and YAML output pass.
2. Existing Databricks publisher and mapping diagnostics regressions remain green.
3. No new lint/type errors in touched files.

Operational:
1. UI progress summary shows source table count vs unified view count.
2. Deployment diagnostics include unresolved anchor and cycle/skip counters.
3. ORM state captures versioned traversal context and generation outcomes.

## Planning Handoff (Next Workflow: /plan)
Workstream A: ORM graph/state schema readiness
- Add/version ORM entities for traversal state and generation outcomes.

Workstream B: Fact and traversal engine
- Implement fact detection + recursive join graph resolution + role aliases.

Workstream C: Measure routing engine
- Implement context-aware measure distribution and review-required queue.

Workstream D: Publisher output loop
- Implement per-fact unified YAML generation pipeline.

Workstream E: Connector Config integration
- Integrate compile/deploy route and progress transparency payload/UI.

Workstream F: Validation and rollout
- Add tests, diagnostics, canary rollout guardrails, and migration guidance.

Dependency order:
1. A
2. B
3. C
4. D
5. E
6. F

## Open Questions
1. What exact confidence threshold should be default for auto-anchoring measures from dummy datasets?
2. Should review-required overflow (above threshold) soft-fail the full deployment?
3. Should default-on-for-new-projects be controlled by project creation timestamp or explicit config marker?
4. Which minimal UI payload fields are mandatory to consider Connector Config integration launch-complete?
