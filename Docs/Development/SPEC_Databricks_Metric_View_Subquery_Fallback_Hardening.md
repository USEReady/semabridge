# Spec: Databricks Metric-View Subquery and Fallback Hardening

## Objective
Stabilize Databricks deployment for model-level measure views by eliminating invalid scalar-subquery expressions in grouped metric-view contexts, improving error diagnostics, and making SQL fallback stateful and auditable.

Primary users:
- Data engineers running Connector Config sync/deploy to Databricks.
- Platform engineers debugging failed measure-view deployments.

Success definition:
- Native metric-view deployment no longer emits expressions that trigger SCALAR_SUBQUERY_IS_IN_GROUP_BY_OR_AGGREGATE_FUNCTION for scalar/today-style and simple aggregation measures.
- Deployment logs correctly classify semantic SQL errors vs source-mismatch errors.
- SQL fallback path produces deterministic success/failure state updates and no silent terminal cut-offs.

## Assumptions
ASSUMPTIONS I AM MAKING:
1. The current failure is reproducible through model-level metric-view generation in src/semabridge/connectors/databricks_publisher.py.
2. Databricks metric-view expressions should avoid explicit scalar SELECT wrappers when equivalent direct expressions are valid.
3. SQL fallback remains acceptable when native metric-view compilation fails.
4. Existing ORM-backed run metadata can be extended with additional state transitions without schema redesign in this patch.
5. This patch should remain backward-compatible and be gated by behavior flags where behavior changes are material.

If any assumption is incorrect, update this spec before implementation.

## Scope
In scope:
- Expression-shape fixes around scalar subquery generation in metric-view pipelines.
- Error classification/logging improvements for Databricks deployment failures.
- Transactional state updates and explicit terminal statuses for SQL fallback flow.
- Unit/integration tests for expression generation, diagnostics, and fallback state transitions.

Out of scope:
- Re-architecting the full measure translation engine.
- Connector redesign for non-Databricks targets.
- UI redesign beyond consuming clearer backend statuses.

## Commands
- Run focused Databricks publisher tests:
  - uv run pytest Tests/test_databricks_publisher.py -k "metric_view or fallback or scalar_subquery"
- Run full Databricks publisher tests:
  - uv run pytest Tests/test_databricks_publisher.py
- Run lint for touched files:
  - uv run ruff check src/semabridge/connectors/databricks_publisher.py src/semabridge/core/behavior.py Tests/test_databricks_publisher.py
- Optional type-check pass:
  - uv run mypy src/semabridge

## Project Structure
Primary touch points:
- src/semabridge/connectors/databricks_publisher.py
  - _build_scalar_subquery_aggregate_expression
  - _generate_model_level_metric_view
  - publish (fallback branch and logging)
- src/semabridge/core/behavior.py
  - behavior flags controlling fallback and timing behavior
- src/semabridge/repository/ (if needed)
  - ORM state/event persistence hooks used by run/deploy status
- Tests/test_databricks_publisher.py
  - regression coverage for expression shape, fallback diagnostics, and state outcomes

## Functional Requirements

### FR1: Scalar Subquery Emission Rules
1. For scalar function measures (example TODAY/current_date style), emit direct scalar expression in metric-view YAML, not a SELECT subquery.
2. For simple same-context aggregations (SUM/MIN/MAX/COUNT/AVG), emit direct aggregate expression when metric-view semantics allow it.
3. Use scalar SELECT wrappers only when strictly required and valid in the target context.
4. Prevent generation of expressions that are known to fail under grouped metric-view execution semantics.

### FR2: Databricks Error Classification and Logging
1. Classify Databricks deployment errors into explicit categories:
  - SQL semantic/grouping errors (including SCALAR_SUBQUERY_IS_IN_GROUP_BY_OR_AGGREGATE_FUNCTION)
  - missing entity/table/view errors
  - source mismatch errors
  - generic compilation/runtime errors
2. Replace misleading generic source-mismatch warning for semantic SQL failures with accurate reason text.
3. Include measure/view identifiers in error logs for actionable triage.

### FR3: Fallback State Integrity
1. On native metric-view failure and eligible fallback:
  - record fallback start state
  - execute SQL fallback
  - record fallback success or failure terminal state
2. Ensure fallback flow cannot exit silently without terminal status update.
3. Preserve existing compatibility behavior for explicit metric_view vs auto fallback policy unless explicit override is enabled.

## Non-Functional Requirements
- Determinism: identical inputs should produce stable expression shapes and fallback decisions.
- Observability: logs and persisted states must unambiguously explain final deployment outcome.
- Safety: no silent degradation; all fallback transitions must be explicit.
- Backward compatibility: existing successful metric-view deployments remain unchanged.

## Code Style
Guidelines:
- Keep expression-shape decisions in narrow helper methods.
- Prefer explicit reason enums/strings over broad exception messages.
- Avoid broad refactors outside Databricks publish flow.

Style example:
```python
if self._is_scalar_function_measure(metric, sql_expr):
    return self._normalize_scalar_function_expression(sql_expr)
```

## Testing Strategy
1. Unit tests
- Scalar function expression emits direct scalar form (no SELECT wrapper).
- Simple aggregate emits direct aggregate expression when allowed.
- Unsupported contexts remain safely rejected.

2. Deployment diagnostics tests
- Semantic grouping error is classified as SQL semantic error.
- Missing-entity errors map to missing-entity classification.
- Generic errors remain generic.

3. Fallback state tests
- metric-view fail -> FALLBACK_IN_PROGRESS -> FALLBACK_SUCCESS
- metric-view fail -> FALLBACK_IN_PROGRESS -> FALLBACK_FAILED
- no silent path without terminal status.

4. Regression tests
- Existing explicit metric_view no-fallback behavior remains unless override is enabled.
- Existing auto-mode fallback behavior remains intact.

## Boundaries
Always:
- Keep changes scoped to Databricks publish and related status plumbing.
- Add tests for every new branch in expression/fallback logic.
- Preserve existing feature flags and default-safe behavior.

Ask first:
- Any schema migration for ORM status storage.
- Any default behavior change for fallback policy.
- Any new dependency.

Never:
- Suppress deployment errors without logging/classification.
- Emit known-invalid metric-view SQL/YAML expressions.
- Remove fallback safety checks.

## Risks
1. Over-normalizing expressions may break valid advanced measures.
2. Error classification string matching may drift with Databricks error text changes.
3. State updates could become inconsistent under partial exceptions.

## Risk Mitigation
- Keep classification pattern lists centralized and test-covered.
- Use fail-safe defaults: reject/skip over risky rewrite ambiguity.
- Wrap fallback state transitions in guarded try/finally style blocks.

## Acceptance Criteria
Functional:
1. Repro model no longer fails with SCALAR_SUBQUERY_IS_IN_GROUP_BY_OR_AGGREGATE_FUNCTION due to publisher-generated scalar wrappers.
2. Logs identify SQL semantic grouping failures accurately (not source mismatch).
3. SQL fallback produces explicit terminal status and does not cut off silently.

Quality:
1. New tests for expression shape, diagnostics, and fallback state pass.
2. Existing databricks publisher regressions remain green.
3. No new lint/type errors in touched files.

Operational:
1. Deployment summaries clearly indicate native failure reason and fallback outcome.
2. Run/job APIs show final fallback state for support and auditability.

## Open Questions
1. Which ORM entity should own fallback states: run summary, deployment event table, or both?
2. Should scalar-function rewrites be limited to known safe built-ins first (TODAY/NOW/current_date), then expanded?
3. Should fallback failures auto-mark the run failed, or partial-success with explicit warning?
