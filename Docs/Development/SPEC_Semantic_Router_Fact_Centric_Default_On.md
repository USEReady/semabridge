# Spec: Semantic Router for Fact-Centric Databricks Publishing (Default-On)

## Objective
Build a semantic router in Databricks publishing that stops treating all tables equally and instead routes generation by semantic role:
1. Categorize tables into fact and dimension roles from relationship pathways.
2. Generate one isolated metric-view artifact per fact anchor.
3. Inject only dimensions reachable from that fact.
4. Route measures only to fact-reachable context.

Primary user:
- Data engineer running Connector Config deployments.

Success definition:
- Generated metric-view YAML contains zero publisher-produced scalar-subquery/grouping-invalid expressions.
- Each output artifact is fact-anchored and context-scoped.
- Ambiguous measure anchors are skipped and marked review-required.
- Routing decisions and deployment outcomes are auditable in PostgreSQL.

## Assumptions
ASSUMPTIONS I AM MAKING:
1. SML relationship metadata is complete enough to identify many-side and one-side pathways.
2. Databricks publishing can emit multiple per-model artifacts without changing external APIs.
3. PostgreSQL ORM repositories are available for persistent routing state.
4. Existing single-model flow can coexist behind a kill switch for rollback safety.
5. "Global default-on" means enabled by default for all deployments unless explicitly disabled.

If any assumption is incorrect, update this spec before implementation.

## Scope
In scope:
- ORM-level table categorization into fact and dimension sets.
- Fact-only outer generation loop for per-fact metric-view YAML artifacts.
- Recursive dimension injection per fact via relationship traversal.
- Contextual measure routing to fact-local reachable graph.
- Ambiguous measure handling as skip plus review-required diagnostics.
- PostgreSQL persistence for anchor decisions, generation outcomes, and traversal traces.
- Connector Config summary fields for source count, output count, and review-required count.

Out of scope:
- Full symbolic translation for all advanced DAX time-intelligence patterns.
- Connector redesign for non-Databricks targets.
- Broad UI redesign beyond deployment transparency fields.

## Constraints
- Default mode is global default-on, but a runtime kill switch is required.
- No known-invalid metric-view SQL/YAML expression shape may be emitted.
- No automatic anchoring for ambiguous measures in v1.
- Relationship traversal must be deterministic and cycle-safe.
- Persistent routing state must live in PostgreSQL; DuckDB remains transient only.

## Tech Stack
- Python 3.11+
- Existing SML/OSI intermediate model layer
- Databricks publisher in src/semabridge/connectors/databricks_publisher.py
- SQLAlchemy ORM repositories in src/semabridge/repository/
- Pytest for backend behavior verification

## Commands
- Focused behavior tests:
  - uv run pytest Tests/test_databricks_publisher.py -k "metric_view or fallback or scalar_subquery"
- Additional routing and join tests:
  - uv run pytest Tests/test_metric_view_joins.py
  - uv run pytest Tests/test_metric_view_yaml_joins.py
- Full Databricks publisher suite:
  - uv run pytest Tests/test_databricks_publisher.py
- Lint touched files:
  - uv run ruff check src/semabridge/connectors/databricks_publisher.py Tests/test_databricks_publisher.py
- Optional type check:
  - uv run mypy src/semabridge

## Project Structure
- src/semabridge/connectors/databricks_publisher.py
  - Fact/dimension routing orchestration
  - Per-fact YAML generation loop
  - Measure contextual routing
  - Publish diagnostics and fallback policy integration
- src/semabridge/repository/
  - ORM persistence hooks for routing decisions and outcomes
- src/semabridge/core/behavior.py
  - Feature flags, default-on controls, and kill switch fields
- Tests/test_databricks_publisher.py
  - Routing, expression-shape, fallback, and diagnostics regressions
- Docs/Development/
  - This spec and planning handoff artifacts

## Code Style
Guidelines:
- Keep routing logic in small helper methods with explicit reason codes.
- Keep ordering deterministic via normalized names and stable sorting.
- Prefer fail-safe skip paths over heuristic auto-placement.

Style example:
```python
if self._is_ambiguous_anchor(metric, graph):
    return self._mark_review_required(metric, reason="AMBIGUOUS_FACT_ANCHOR")
```

## Testing Strategy
1. Unit tests
- Table categorizer identifies many-side as fact and one-side as dimension.
- Fact-only generation loop emits one artifact per fact.
- Dimension traversal injects only reachable dimensions.
- Ambiguous measures are skipped with review-required reason.

2. Integration-like tests
- Multi-fact model emits per-fact metric views with isolated measure scopes.
- No fact artifact contains measures outside reachable context graph.
- Join traversal remains cycle-safe and deterministic.

3. Regression tests
- Existing valid metric-view and fallback behavior remains intact.
- Scalar-subquery/grouping-invalid expression regressions remain blocked.

4. Persistence tests
- Per-measure anchor decision, confidence, reason persisted.
- Per-fact generation/deploy outcome persisted.
- Traversal and cycle-break trace persisted.

## Boundaries
Always:
- Enforce fact-centric routing before YAML generation.
- Skip ambiguous anchors and emit review-required diagnostics.
- Persist routing and outcome traces for auditability.
- Run focused tests before claiming completion.

Ask first:
- Any default behavior change that removes global default-on.
- Schema migrations in ORM tables.
- New dependencies or external services.

Never:
- Emit known-invalid scalar-subquery/grouping expression shapes.
- Auto-anchor ambiguous measures without explicit policy approval.
- Store authoritative routing state only in transient stores.

## Risks
1. Incomplete or noisy relationship metadata can misclassify facts.
2. Default-on rollout may surface more review-required skips initially.
3. Traversal complexity can increase with highly connected models.
4. Drift in Databricks error text can reduce classifier precision.

## Risk Mitigation
- Add deterministic tie-break and confidence thresholds for categorization.
- Provide kill switch and per-model override to disable router quickly.
- Add traversal depth and cycle controls with explicit diagnostics.
- Keep classifier token patterns centralized and test-covered.

## Non-Goals
- Maximizing apparent measure coverage by risky auto-anchoring.
- Rebuilding the entire translation engine in one release.
- Unifying all connector targets under this router in v1.

## Acceptance Criteria
Functional:
1. For a multi-fact model, publisher emits one metric-view artifact per detected fact.
2. Each artifact contains only dimensions reachable from its fact root.
3. Measures are emitted only when anchor lineage is unambiguous and reachable.
4. Ambiguous measures are skipped and labeled review-required.
5. Publisher does not emit scalar-subquery/grouping-invalid expression shapes for targeted scenarios.

Quality:
1. New routing and diagnostics tests pass.
2. Existing Databricks publisher regressions in focused suites remain green.
3. No new runtime errors introduced in touched paths.

Operational:
1. Connector Config summary includes source table count, generated fact-view count, and review-required count.
2. PostgreSQL audit entries include anchor decision and per-fact deployment outcomes.

## Non-Functional Criteria
- Determinism: same input plus config yields stable artifact set and ordering.
- Observability: each skipped/failed routing decision has a reason code.
- Rollback safety: kill switch can disable router without code change.

## Open Questions
1. Should review-required counts block deployment above a configurable threshold?
2. What confidence threshold should gate fact anchor relocation for dummy datasets in v1 default policy?
3. Which API payload should surface per-fact audit traces to Connector Config?

## Planning Handoff
Recommended planning phases:
1. Phase A: ORM categorizer and deterministic relationship graph service.
2. Phase B: Per-fact generation loop and recursive dimension injection.
3. Phase C: Contextual measure router and review-required queue.
4. Phase D: PostgreSQL persistence for routing decisions and outcomes.
5. Phase E: Connector Config summary payload and UI integration.
6. Phase F: Regression hardening and default-on rollout guardrails.
