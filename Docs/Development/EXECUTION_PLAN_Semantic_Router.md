# Execution Plan: Semantic Router for Fact-Centric Databricks Publishing

## Overview

Implement a semantic router that categorizes tables by semantic role (fact vs dimension) and generates isolated metric-view artifacts per fact. This architecture stops treating all tables equally and routes measure generation only to fact-reachable contexts, eliminating scalar-subquery and grouping-invalid expression shapes in generated YAML.

Delivery spans six sequential phases with explicit checkpoints and verification gates. All new state persists to PostgreSQL with auditability for deployment decisions.

---

## Architecture Decisions

1. **Relationship Graph as Foundation**: Build a deterministic, cycle-safe graph analyzer first. All downstream routing depends on reliable table categorization.

2. **Kill Switch Required**: Global default-on with runtime toggle via `behavior.yaml`. Rollback must not require code change.

3. **Fail-Safe Skipping**: Ambiguous measures are skipped (not auto-anchored), marked review-required, and never emitted. Better to skip than to emit invalid SQL.

4. **PostgreSQL-Only Persistence**: Routing decisions, per-fact outcomes, and traversal traces in ORM tables. DuckDB remains transient for metadata only.

5. **Vertical (Bottom-Up) Slicing**: Build foundations first (graph, categorizer), then generation loops, then measure routing, then persistence, then UI integration.

6. **Early Regression Testing**: All existing metric-view and fallback behavior must remain green throughout. Regression tests run after each phase.

---

## Foundation: Core Infrastructure

### Task 0: Add Semantic Router Feature Flag and Kill Switch

**Description:** Add behavior configuration entries in `src/semabridge/core/behavior.py` to control semantic router default mode and per-model override.

**Acceptance criteria:**
- [ ] `DatabricksBehavior` has `semantic_router_enabled: bool = True` (global default-on)
- [ ] `DatabricksBehavior` has `semantic_router_override_models: list[str] = []` (per-model disable list)
- [ ] `behavior.yaml` includes example entries for both flags
- [ ] Existing behavior configuration loading continues to work
- [ ] Pytest conftest mock includes new fields with defaults

**Verification:**
- [ ] Tests pass: `uv run pytest Tests/ -k "test_behavior"`
- [ ] No linting errors in affected files

**Dependencies:** None

**Files likely touched:**
- `src/semabridge/core/behavior.py`
- `src/semabridge/core/settings.py` (if needed for config linkage)
- `Config/behavior.yaml`
- `Tests/conftest.py`

**Estimated scope:** Small (1-2 files modified, 50 LOC)

---

## Phase A: Relationship Graph and Table Categorization

### Task 1: Build Deterministic Relationship Graph Service

**Description:** Create a graph service that builds a DAG from SML relationships, detects cycles, and normalizes cardinality to consistent many-side/one-side labels. Output should be deterministic regardless of input order.

**Acceptance criteria:**
- [ ] New class `SemanticGraph` in `src/semabridge/utils/semantic_graph.py`
- [ ] Graph construction from `SMLModel.datasets` and `SMLModel.joins`
- [ ] Cycle detection with exception on first cycle found (fail-safe)
- [ ] Cardinality normalization: MANY_TO_ONE → many-side / one-side
- [ ] Deterministic ordering by normalized dataset names
- [ ] Method `get_neighbors(table: str) -> dict[str, str]` returns `{neighbor_table: cardinality}`
- [ ] Method `get_reachable_from(table: str) -> set[str]` returns all reachable tables (BFS)
- [ ] Method `get_many_sides() -> set[str]` returns detected fact candidate tables
- [ ] Edges are stored in sorted order (determinism)

**Verification:**
- [ ] Unit tests pass: `uv run pytest Tests/test_semantic_graph.py`
- [ ] No linting errors: `uv run ruff check src/semabridge/utils/semantic_graph.py Tests/test_semantic_graph.py`
- [ ] Cycle detection test: provide circular join, verify exception raised
- [ ] Determinism test: build graph twice from same data, verify identical edge order
- [ ] Reachability test: multi-hop path from fact to dimension verified

**Dependencies:** None (pure utility)

**Files likely touched:**
- `src/semabridge/utils/semantic_graph.py` (new)
- `Tests/test_semantic_graph.py` (new)

**Estimated scope:** Small (500 LOC + tests)

---

### Task 2: Implement Table Categorizer (Fact/Dimension Detector)

**Description:** Build table categorizer that analyzes the relationship graph to classify each table as fact, dimension, or bridge (ambiguous). Use many-side detection as primary signal with tie-breaks.

**Acceptance criteria:**
- [ ] New class `TableCategorizer` in `src/semabridge/utils/table_categorizer.py`
- [ ] Method `categorize(graph: SemanticGraph) -> dict[str, str]` returns `{table: category}`
- [ ] Categories: "FACT", "DIMENSION", "BRIDGE" (many-to-many or multi-path)
- [ ] Tables with outgoing many-to-one relationships → FACT
- [ ] Tables with only incoming one-to-many relationships → DIMENSION
- [ ] Tables with both or multiple paths → BRIDGE (requires explicit routing policy)
- [ ] Confidence scores per classification: HIGH / MEDIUM / LOW
- [ ] Deterministic: same graph yields same categories and confidence scores
- [ ] Categories accessible via `get_category(table: str) -> tuple[str, str]` → (category, confidence)

**Verification:**
- [ ] Unit tests pass: `uv run pytest Tests/test_table_categorizer.py`
- [ ] No linting errors
- [ ] Test cases:
  - Simple fact-dimension pair
  - Multi-fact model with isolated measure anchors
  - Bridge table (many-to-many)
  - Single-table model (mark as FACT by default)
  - Equality test: two categorizers on same graph produce identical results

**Dependencies:** Task 1 (SemanticGraph)

**Files likely touched:**
- `src/semabridge/utils/table_categorizer.py` (new)
- `Tests/test_table_categorizer.py` (new)

**Estimated scope:** Small-Medium (400 LOC + tests)

---

### Task 3: Integrate Graph and Categorizer into DatabricksPublisher

**Description:** Add graph construction and categorization initialization to DatabricksPublisher.publish(). Wire in the kill switch so old behavior runs unless router enabled.

**Acceptance criteria:**
- [ ] `DatabricksPublisher.publish()` constructs `SemanticGraph` from `SMLModel`
- [ ] `TableCategorizer` runs on graph if `behavior.semantic_router_enabled and model not in override_list`
- [ ] If router disabled, existing code path runs unchanged
- [ ] Categorization results cached in `self._table_categories` for phase B
- [ ] Logs categorization results at debug level (table → category, confidence)
- [ ] If cycle detected in relationships, raise `DatabricksPublishError` with helpful message
- [ ] No behavior change to existing tests (all should still pass)

**Verification:**
- [ ] Existing Databricks tests pass: `uv run pytest Tests/test_databricks_publisher.py`
- [ ] No linting errors
- [ ] Manual smoke test: deploy a model with router enabled, verify categorization logs
- [ ] Kill switch test: disable router via behavior flag, verify old code path runs

**Dependencies:** Task 0 (kill switch), Task 1 (SemanticGraph), Task 2 (TableCategorizer)

**Files likely touched:**
- `src/semabridge/connectors/databricks_publisher.py` (modify `publish()` method)
- `Tests/test_databricks_publisher.py` (add router kill-switch regression test)

**Estimated scope:** Small (100 LOC modifications)

---

## Checkpoint: Phase A Complete ✓

**Phase A Verification Checklist:**
- [ ] Relationship graph constructs correctly and detects cycles
- [ ] Table categorization is deterministic and confidence-scored
- [ ] DatabricksPublisher initializes graph and categorizer
- [ ] Kill switch enables toggle between old and new behavior
- [ ] All existing Databricks tests remain green
- [ ] No new runtime errors on touch paths
- [ ] Code modified: `databricks_publisher.py`, `behavior.py`, utilities
- [ ] Code reviewed for: correctness of graph algorithms, determinism, error handling

**Sign-Off:** Before proceeding to Phase B, verify above checklist with manual testing and focused test run:
```bash
uv run pytest Tests/test_databricks_publisher.py Tests/test_semantic_graph.py Tests/test_table_categorizer.py -v
```

---

## Phase B: Per-Fact Generation Loop and Dimension Injection

### Task 4: Implement Recursive Dimension Injector

**Description:** Build service that, given a fact table and the relationship graph, recursively discovers all dimensions reachable from that fact. Must handle cycles and produce ordered, deterministic output.

**Acceptance criteria:**
- [ ] New class `DimensionInjector` in `src/semabridge/utils/dimension_injector.py`
- [ ] Method `get_dimensions_for_fact(fact: str, graph: SemanticGraph) -> list[str]`
- [ ] Returns ordered list of dimension tables reachable from fact (BFS traversal)
- [ ] Handles cycles: breadth-first, stops at first occurrence of table
- [ ] Output deterministically ordered by normalized table names
- [ ] Includes metadata: `get_traversal_trace(fact: str) -> dict` with path, depth, and cycle-breaks
- [ ] Respects a configurable max depth (default 10) to guard against infinite traversal
- [ ] Raises exception if fact table not found in graph

**Verification:**
- [ ] Unit tests pass: `uv run pytest Tests/test_dimension_injector.py`
- [ ] Test cases:
  - Simple fact → dimensions
  - Multi-hop fact → intermediate → final dimensions
  - Cyclic dimension → fact → dimension (verify cycle break)
  - Single-table model (no dimensions)
  - Determinism: same fact+graph yields same ordered list twice

**Dependencies:** Task 1 (SemanticGraph)

**Files likely touched:**
- `src/semabridge/utils/dimension_injector.py` (new)
- `Tests/test_dimension_injector.py` (new)

**Estimated scope:** Small (350 LOC + tests)

---

### Task 5: Extract Per-Fact Metric List from Model Metrics

**Description:** Build service to extract which measures belong to each fact table. A measure may reference dimensions reachable only from some facts, making per-fact filtering necessary.

**Acceptance criteria:**
- [ ] New class `MeasureFactMapping` in `src/semabridge/utils/measure_fact_mapping.py`
- [ ] Method `extract_measure_facts(metrics: list[SMLMetric], model_table: str) -> dict[str, set[str]]`
- [ ] Returns `{metric_name: {fact_tables_that_can_anchor_this_metric}}`
- [ ] A measure's anchor facts are those where all referenced dimensions are reachable
- [ ] Handles DAX expression parsing to extract table references (use regex-based heuristic)
- [ ] Caches parsed expressions to avoid re-parsing

**Verification:**
- [ ] Unit tests pass: `uv run pytest Tests/test_measure_fact_mapping.py`
- [ ] Test cases:
  - Simple measure with single table reference
  - Measure with multiple dimension references (all from same fact paths)
  - Measure with unresolvable reference (not in model)
  - Null/empty expression handling

**Dependencies:** Task 2 (TableCategorizer), Task 4 (DimensionInjector)

**Files likely touched:**
- `src/semabridge/utils/measure_fact_mapping.py` (new)
- `Tests/test_measure_fact_mapping.py` (new)

**Estimated scope:** Small-Medium (300 LOC + tests)

---

### Task 6: Implement Per-Fact Metric View Generation Loop

**Description:** Refactor metric view generation to iterate over facts instead of model-level. For each fact, generate one isolated metric-view YAML artifact containing only that fact's reachable dimensions and applicable measures.

**Acceptance criteria:**
- [ ] New method `_generate_per_fact_metric_views()` in DatabricksPublisher
- [ ] Loop structure: for each fact in categorized facts → generate one artifact
- [ ] Each artifact named `{model}_fact_{fact_name}_metric_view`
- [ ] Artifact includes only dimensions returned by `DimensionInjector.get_dimensions_for_fact()`
- [ ] Measures in artifact are filtered via mapped anchor facts (Task 5)
- [ ] Existing model-level generation disabled when router enabled
- [ ] Results stored in `self._per_fact_metric_views: dict[str, str]` (fact → artifact YAML)
- [ ] Compatible with existing metric view generation methods (reuse _generate_metric_view_yaml)

**Verification:**
- [ ] Focused tests pass: `uv run pytest Tests/test_databricks_publisher.py -k "per_fact"`
- [ ] Test case: multi-fact model with isolated measure anchors produces per-fact YAMLs
- [ ] No existing metric-view tests broken

**Dependencies:** Task 3 (router integration), Task 4 (DimensionInjector), Task 5 (MeasureFactMapping)

**Files likely touched:**
- `src/semabridge/connectors/databricks_publisher.py` (add `_generate_per_fact_metric_views()`)
- `Tests/test_databricks_publisher.py` (add per-fact generation tests)

**Estimated scope:** Medium (400 LOC)

---

## Checkpoint: Phase B Complete ✓

**Phase B Verification Checklist:**
- [ ] Dimension injector correctly traverses graph and orders deterministically
- [ ] Measure-to-fact mapping identifies applicable measures per fact
- [ ] Per-fact metric view generation produces one artifact per fact
- [ ] Each artifact contains only reachable dimensions for that fact
- [ ] Existing metric-view and join tests still pass
- [ ] No scalar-subquery/grouping-invalid expressions in generated YAMLs
- [ ] Kill switch still allows fallback to old behavior

**Sign-Off:** Before proceeding to Phase C:
```bash
uv run pytest Tests/test_databricks_publisher.py Tests/test_dimension_injector.py Tests/test_measure_fact_mapping.py -v
uv run ruff check src/semabridge/connectors/databricks_publisher.py Tests/test_databricks_publisher.py
```

---

## Phase C: Contextual Measure Routing and Review-Required Queue

### Task 7: Build Ambiguous Measure Detector

**Description:** Analyze each measure to determine if its anchor (the table it measures) is unambiguous and reachable from at least one fact. Mark ambiguous measures for skip rather than auto-anchoring.

**Acceptance criteria:**
- [ ] New class `AmbiguousMeasureDetector` in `src/semabridge/utils/ambiguous_measure_detector.py`
- [ ] Method `analyze_measure(metric: SMLMetric, graph: SemanticGraph) -> dict` returning:
  - `is_ambiguous: bool`
  - `reason: str` (one of: "NO_SOURCE_TABLE", "UNRESOLVED_REFERENCE", "MULTI_FACT_ANCHOR", "NO_FACT_REACHABLE")
  - `candidate_facts: list[str]` (facts where this measure could resolve)
- [ ] Measure is ambiguous if:
  - Its source table is not in the model
  - Its expression references unresolvable tables
  - Its expression could be resolved by multiple facts (different paths)
  - No fact in the model can reach its required dimensions
- [ ] Output reasons are deterministic and auditable

**Verification:**
- [ ] Unit tests pass: `uv run pytest Tests/test_ambiguous_measure_detector.py`
- [ ] Test cases:
  - Clear measure (single fact, all dimensions reachable)
  - Unresolved measure (table not in model)
  - Multi-anchor measure (could belong to multiple facts)
  - Unreachable measure (dimensions not reachable from any fact)

**Dependencies:** Task 2 (TableCategorizer), Task 4 (DimensionInjector)

**Files likely touched:**
- `src/semabridge/utils/ambiguous_measure_detector.py` (new)
- `Tests/test_ambiguous_measure_detector.py` (new)

**Estimated scope:** Small-Medium (350 LOC + tests)

---

### Task 8: Implement Review-Required Queue and Diagnostics

**Description:** Build a queue to track measures and joins that should be skipped with reason codes. Integrate skip logic into metric view generation so ambiguous items never emit.

**Acceptance criteria:**
- [ ] New class `ReviewRequiredQueue` in `src/semabridge/utils/review_required_queue.py`
- [ ] Queue stores items: `{item_type: "measure" | "join", name: str, reason: str, details: dict}`
- [ ] Method `add_measure(metric: SMLMetric, reason: str, details: dict)`
- [ ] Method `add_join(join: SMLJoin, reason: str, details: dict)`
- [ ] Method `is_skipped(item_type: str, name: str) -> bool`
- [ ] Method `summary() -> dict` returning counts by reason
- [ ] Integrated into `_generate_per_fact_metric_views()`: skip measures if `queue.is_skipped("measure", name)`
- [ ] No skipped measures appear in generated YAML

**Verification:**
- [ ] Unit tests pass: `uv run pytest Tests/test_review_required_queue.py`
- [ ] Integration test: ambiguous measures are added to queue and skipped from output

**Dependencies:** Task 7 (AmbiguousMeasureDetector)

**Files likely touched:**
- `src/semabridge/utils/review_required_queue.py` (new)
- `src/semabridge/connectors/databricks_publisher.py` (integrate queue into generation)
- `Tests/test_review_required_queue.py` (new)

**Estimated scope:** Small (250 LOC + tests)

---

### Task 9: Emit Review-Required Diagnostics

**Description:** Surface review-required items in publish output and logs. Make deployment outcomes auditable and actionable.

**Acceptance criteria:**
- [ ] Method `_emit_review_diagnostics()` in DatabricksPublisher
- [ ] Publish output includes `review_required_summary: dict` with:
  - Count by reason (e.g., "UNRESOLVED_REFERENCE": 3)
  - List of skipped measure names
  - List of skipped join names
- [ ] Diagnostics logged at WARN level (visible to user)
- [ ] Each skipped item includes reason and suggested action
- [ ] Summary integrated into deployment status response
- [ ] No validation failure — skips are logged but don't block deployment

**Verification:**
- [ ] Integration test: deploy model with ambiguous measures, verify diagnostics surface
- [ ] Manual test: review logs for clear skip reasons

**Dependencies:** Task 8 (ReviewRequiredQueue)

**Files likely touched:**
- `src/semabridge/connectors/databricks_publisher.py` (add diagnostics emission)
- `Tests/test_databricks_publisher.py` (add diagnostics test)

**Estimated scope:** Small (150 LOC)

---

## Checkpoint: Phase C Complete ✓

**Phase C Verification Checklist:**
- [ ] Ambiguous measure detector correctly identifies problematic measures
- [ ] Review-required queue prevents skipped items from emitting
- [ ] Diagnostics are clear, actionable, and logged
- [ ] No invalid SQL expressions in output (scalar-subquery/grouping blocked)
- [ ] Existing behavior unaffected when router disabled
- [ ] All tests pass

**Sign-Off:**
```bash
uv run pytest Tests/test_databricks_publisher.py Tests/test_ambiguous_measure_detector.py Tests/test_review_required_queue.py -v
uv run pytest Tests/test_databricks_publisher.py -k "metric_view or fallback or scalar_subquery"
```

---

## Phase D: PostgreSQL Persistence for Routing Decisions

### Task 10: Define ORM Models for Routing Persistence

**Description:** Create SQLAlchemy ORM models to persist routing decisions, categorizations, and deployment outcomes to PostgreSQL. Provides auditability and enables cross-session routing decisions.

**Acceptance criteria:**
- [ ] New file `src/semabridge/repository/semantic_routing_repository.py`
- [ ] ORM model `RouterDecision` with fields:
  - `id`, `created_at`, `updated_at`
  - `model_name`, `table_name`, `category` (FACT, DIMENSION, BRIDGE)
  - `confidence`, `reason_code`
- [ ] ORM model `MeasureAnchorDecision` with fields:
  - `id`, `created_at`, `updated_at`
  - `model_name`, `measure_name`, `anchor_table`, `confidence`, `is_ambiguous`
  - `reason_code`, `candidate_facts` (JSON)
- [ ] ORM model `FactDeploymentOutcome` with fields:
  - `id`, `created_at`, `updated_at`
  - `model_name`, `fact_table`, `artifact_name`, `deployment_status` (DEPLOYED, SKIPPED)
  - `skipped_reason`, `measure_count`, `dimension_count`
  - `traversal_trace` (JSON: reachable tables, cycle breaks)
- [ ] Create alembic migration for new tables
- [ ] Repository class `SemanticRoutingRepository` with methods:
  - `save_routing_decision(decision: RouterDecision)`
  - `save_measure_anchor(anchor: MeasureAnchorDecision)`
  - `save_deployment_outcome(outcome: FactDeploymentOutcome)`
  - `get_routing_for_model(model_name: str) -> list[RouterDecision]`

**Verification:**
- [ ] Alembic migration passes: `alembic upgrade head`
- [ ] Tables created in PostgreSQL with correct columns
- [ ] ORM model tests pass: `uv run pytest Tests/test_semantic_routing_repository.py`
- [ ] No linting errors

**Dependencies:** None (independent ORM layer)

**Files likely touched:**
- `src/semabridge/repository/semantic_routing_repository.py` (new)
- `Config/alembic/versions/` (new migration file)
- `Tests/test_semantic_routing_repository.py` (new)

**Estimated scope:** Medium (400 LOC + migration)

---

### Task 11: Wire Persistence into Routing Orchestration

**Description:** Integrate ORM persistence into DatabricksPublisher so routing decisions, measure anchors, and deployment outcomes are automatically saved after each publish.

**Acceptance criteria:**
- [ ] DatabricksPublisher initializes `SemanticRoutingRepository`
- [ ] After table categorization, save all `RouterDecision` entries
- [ ] After measure analysis, save all `MeasureAnchorDecision` entries
- [ ] After per-fact generation, save `FactDeploymentOutcome` for each fact with:
  - Artifact name
  - Measure count and dimension count
  - Traversal trace from DimensionInjector
  - Skipped reason if applicable
- [ ] Transactional: save all or none (no partial writes)
- [ ] Persist even if final deployment fails (routing decisions are independent of execution)

**Verification:**
- [ ] Integration test: deploy model, query PostgreSQL, verify entries created
- [ ] Verify traversal_trace JSON contains expected keys
- [ ] All timestamps are UTC and recent

**Dependencies:** Task 10 (ORM models), Task 6 (per-fact generation)

**Files likely touched:**
- `src/semabridge/connectors/databricks_publisher.py` (integrate repository calls)
- `Tests/test_databricks_publisher.py` (add persistence test)

**Estimated scope:** Small (200 LOC)

---

## Checkpoint: Phase D Complete ✓

**Phase D Verification Checklist:**
- [ ] ORM models correctly represent routing, measure, and deployment decisions
- [ ] Alembic migration creates PostgreSQL tables
- [ ] Persistence automatically occurs after publish
- [ ] All routing decisions auditable and queryable
- [ ] No performance regressions in publisher

**Sign-Off:**
```bash
alembic upgrade head
uv run pytest Tests/test_semantic_routing_repository.py Tests/test_databricks_publisher.py -v
```

---

## Phase E: Connector Config Payload and UI Integration

### Task 12: Extend Connector Config API Response with Routing Summary

**Description:** Add routing summary fields to Connector Config publish API response. Surface fact counts, dimension counts, review-required counts.

**Acceptance criteria:**
- [ ] API response includes new field `routing_summary: dict` with:
  - `source_table_count: int` (total tables in model)
  - `fact_table_count: int` (detected facts)
  - `dimension_table_count: int` (detected dimensions)
  - `bridge_table_count: int` (detected bridges/ambiguous)
  - `review_required_count: int` (skipped measures/joins)
  - `generated_artifact_count: int` (one per fact)
- [ ] Summary present regardless of router enabled/disabled (shows 1 artifact, 0 review if disabled)
- [ ] Backward compatible: existing clients ignore new field

**Verification:**
- [ ] API endpoint tests pass
- [ ] Manual API call returns expected summary structure
- [ ] Field is optional JSON (doesn't break older clients)

**Dependencies:** Task 9 (diagnostics), Task 10 (ORM for querying counts)

**Files likely touched:**
- `src/semabridge/api/connectors.py` (or relevant API file)
- `src/semabridge/api/models.py` (add response type)
- `Tests/test_connector_api.py`

**Estimated scope:** Small (150 LOC)

---

### Task 13: Display Routing Summary in Connector Config UI

**Description:** Add UI fields in Connector Config to show fact categorization, generated artifacts, and review-required items.

**Acceptance criteria:**
- [ ] New summary section in Connector Config showing:
  - Source table count
  - Fact table count with list of fact names
  - Dimension table count
  - Bridge table count with list of ambiguous tables
  - Review-required count with reason summary
  - Generated artifact count
- [ ] Collapsible details for each category (facts, dimensions, review items)
- [ ] Visual distinction: green (deployed), yellow (review-required), red (failed)
- [ ] Accessible from existing Connector Config view

**Verification:**
- [ ] Manual test: deploy model, observe summary in UI
- [ ] UI renders correctly on desktop and mobile
- [ ] Summary updates after each deployment

**Dependencies:** Task 12 (API payload)

**Files likely touched:**
- `frontend/src/components/ConnectorConfig.tsx` (or similar)
- `frontend/src/pages/ConnectorConfigPage.tsx`

**Estimated scope:** Medium (300 LOC + CSS)

---

## Checkpoint: Phase E Complete ✓

**Phase E Verification Checklist:**
- [ ] API response includes routing summary
- [ ] UI displays fact, dimension, and review-required counts
- [ ] Backward compatible with existing clients
- [ ] Manual deployment shows summary updates

**Sign-Off:**
```bash
# Deploy test model, verify API response
curl http://localhost:8001/api/connectors/{id}/deploy | jq .routing_summary

# Verify UI shows summary
# (manual browser test)
```

---

## Phase F: Regression Hardening and Default-On Rollout

### Task 14: Add Comprehensive Routing-Specific Unit Tests

**Description:** Expand test coverage for all routing components to ensure determinism, edge cases, and error handling.

**Acceptance criteria:**
- [ ] TestSemanticGraph: >15 test methods covering edge cases
  - Cycle detection and failure modes
  - Empty model (no tables)
  - Single table (reflexive)
  - Determinism verification
- [ ] TestTableCategorizer: >10 test methods
  - Simple fact-dimension pairs
  - Multi-fact models
  - Bridge tables
  - Confidence scoring
- [ ] TestDimensionInjector: >10 test methods
  - Multi-hop traversal
  - Cycle handling
  - Max depth limits
- [ ] TestMeasureFact Mapping: >8 test methods
  - Complex expressions
  - Unresolvable references
- [ ] TestAmbiguousMeasureDetector: >10 test methods
  - All reason codes
  - Edge cases
- [ ] All tests parametrized for multiple scenario axes
- [ ] Code coverage >85% for routing utilities

**Verification:**
- [ ] All tests pass: `uv run pytest Tests/test_semantic*.py Tests/test_dimension*.py Tests/test_ambiguous*.py Tests/test_measure*.py -v`
- [ ] Code coverage report: `uv run pytest --cov=src/semabridge/utils --cov-report=html`

**Dependencies:** All Phase A-C tasks

**Files likely touched:**
- `Tests/test_*.py` (expand existing or create new test files)

**Estimated scope:** Medium (800 LOC of test code)

---

### Task 15: Add Integration Tests for Full Router Flow

**Description:** Build end-to-end integration tests that exercise the complete router flow: graph → categorization → per-fact generation → persistence → diagnostics.

**Acceptance criteria:**
- [ ] New test file `Tests/test_semantic_routing_integration.py`
- [ ] Test 1: Simple fact-dimension model
  - Verify one metric view per fact
  - Verify dimensions scoped correctly
  - Verify persistence saved
  - Verify no invalid SQL in YAML
- [ ] Test 2: Multi-fact model with shared dimensions
  - Verify isolated artifacts per fact
  - Verify no measures leak between artifacts
  - Verify traversal traces saved
- [ ] Test 3: Model with ambiguous measures
  - Verify ambiguous measures skipped
  - Verify review-required diagnostics emitted
  - Verify counts correct in summary
- [ ] Test 4: Router disabled (kill switch)
  - Verify old code path runs
  - Verify single model-level artifact
  - Verify counts show 1 artifact, 0 review
- [ ] Test 5: Model with cycles in relationships
  - Verify cycle detected and raises error
  - Verify helpful error message
- [ ] Test 6: Determinism
  - Same input twice → identical artifacts
  - Identical persistence entries
- [ ] Test 7: Performance
  - Large model (100+ tables): publish completes <30s
- [ ] Each test runs focused Databricks publisher suite after

**Verification:**
- [ ] All integration tests pass: `uv run pytest Tests/test_semantic_routing_integration.py -v`
- [ ] No regressions in existing Databricks tests

**Dependencies:** All Phase A-E tasks

**Files likely touched:**
- `Tests/test_semantic_routing_integration.py` (new, 1000+ LOC)
- `Tests/conftest.py` (fixtures for multi-fact test models)

**Estimated scope:** Large (1000+ LOC of test code)

---

### Task 16: Regression Test Against Expression Shape Regressions

**Description:** Add specific tests to guard against scalar-subquery, grouping-invalid, and other known-bad expression shapes reappearing in generated metric view YAML.

**Acceptance criteria:**
- [ ] New test file `Tests/test_metric_view_expression_regressions.py`
- [ ] Test 1: Scalar subquery detection in YAML
  - Verify no `(SELECT ... FROM ...)` patterns in metric expressions
  - Verify no UNRESOLVED_COLUMN patterns
- [ ] Test 2: Grouping validity
  - Verify all GROUP BY columns exist in SELECT
  - Verify aggregation functions are valid
- [ ] Test 3: Window function patterns
  - Verify no invalid OVER clauses
- [ ] Test 4: Cross-table measure references
  - Verify qualified table references match joined dimensions
  - Verify no orphan table references
- [ ] Each test parameterized over multiple metric shapes (SUM, COUNT, AVG, DAX, etc.)
- [ ] Tests run against all generated per-fact artifacts

**Verification:**
- [ ] All tests pass: `uv run pytest Tests/test_metric_view_expression_regressions.py -v`
- [ ] Manual review of generated YAML confirms no suspicious patterns

**Dependencies:** Task 6 (per-fact generation)

**Files likely touched:**
- `Tests/test_metric_view_expression_regressions.py` (new)

**Estimated scope:** Medium (400 LOC)

---

### Task 17: Default-On Rollout Verification and Guardrails

**Description:** Document rollout strategy and verify guard conditions before enabling router globally.

**Acceptance criteria:**
- [ ] Rollout documentation in `Docs/Development/SEMANTIC_ROUTER_ROLLOUT.md`
  - Feature flag default value: `semantic_router_enabled = True`
  - Kill switch instructions (how to disable per model)
  - Monitoring guidance: query PostgreSQL for routing decisions
  - Rollback procedure: disable flag, restart API
- [ ] Behavior test: new models default to router enabled
- [ ] Override test: can disable router for specific models
- [ ] Monitoring query added to docs: count ambiguous measures per model
- [ ] Alert suggested: review_required_count > 10% of total measures
- [ ] Migration guide for users: what to expect if metrics appear in multiple artifacts
- [ ] Backward compatibility: existing API clients work unchanged

**Verification:**
- [ ] Documentation complete and reviewed
- [ ] Override mechanism tested and confirmed working
- [ ] Kill switch verified in code review

**Dependencies:** Task 0 (kill switch), Task 12 (API summary)

**Files likely touched:**
- `Docs/Development/SEMANTIC_ROUTER_ROLLOUT.md` (new)
- `Docs/Development/SEMANTIC_ROUTER_FAQ.md` (new, optional)

**Estimated scope:** Small (implementation), Medium (documentation, 500 LOC docs)

---

## Checkpoint: Phase F Complete ✓

**Phase F Verification Checklist:**
- [ ] Routing unit test coverage >85%
- [ ] Integration tests cover all major flows
- [ ] Expression shape regressions have guard tests
- [ ] Rollout documentation complete
- [ ] Kill switch verified and tested
- [ ] All Databricks publisher tests pass
- [ ] No performance regressions

**Sign-Off: Pre-Rollout Checklist:**
```bash
# Run full test suite
uv run pytest Tests/test_semantic_routing_integration.py Tests/test_metric_view_expression_regressions.py Tests/test_databricks_publisher.py -v --tb=short

# Verify kill switch
# (manual test: disable router in behavior.yaml, deploy model, confirm old behavior)

# Review persistence queries
# (manual: query PostgreSQL for router_decisions, measure_anchor_decisions, fact_deployment_outcomes)

# Code review
# (have human reviewer walk through routing orchestration in databricks_publisher.py)
```

---

## Final Checkpoint: Full Delivery ✓

**Complete Verification Checklist:**
- [ ] All phases A-F complete and checkpoints passed
- [ ] Routing functionality:
  - Table categorization deterministic and confidence-scored ✓
  - Per-fact metric view generation working ✓
  - Dimension injection correct and cycle-safe ✓
  - Ambiguous measures skipped and marked review-required ✓
  - No invalid SQL expressions in output ✓
- [ ] Persistence:
  - PostgreSQL tables created and migrated ✓
  - Routing decisions saved after each publish ✓
  - Queries available for audit ✓
- [ ] UI Integration:
  - API response includes routing summary ✓
  - Connector Config shows fact and review counts ✓
- [ ] Rollout Safety:
  - Global default-on enabled ✓
  - Kill switch tested and documented ✓
  - Per-model override working ✓
- [ ] Regression Coverage:
  - All existing tests pass ✓
  - New routing tests cover edge cases ✓
  - Expression shape regressions guarded ✓
  - Integration tests verify end-to-end flow ✓
- [ ] Documentation:
  - Rollout strategy documented ✓
  - FAQ and monitoring guidance included ✓
  - Code style followed (small methods, reason codes) ✓

**Ready for Production Deployment: YES ✓**

---

## Risk Mitigation Summary

| Risk | Phase | Mitigation |
|------|-------|-----------|
| Incomplete relationship metadata | A | Graph cycle detection fails fast; ORM audit trail enables manual review |
| Default-on surfaces too many skips | F | Per-model disable list and documented kill switch; monitoring queries; alert thresholds |
| Traversal complexity explodes | B | Max depth limit (10) enforced; traversal trace logged; cycle breaks explicit |
| Drift in Databricks error patterns | C | Reason codes centralized and test-covered; skip safe (no auto-anchoring) |
| Performance regression on large models | F | Integration test enforces <30s for 100+ table model; monitoring in place |
| PostgreSQL schema migration issues | D | Alembic rollback tested; migration backward-compatible |

---

## Open Questions (Resolved During Execution)

These questions should be answered before proceeding past specific checkpoints:

1. **Before Phase B (Task 4):** How many hop-depths for dimension traversal? *Answer: Enforced max depth = 10, configurable.*
2. **Before Phase C (Task 7):** What is the priority if a measure could reasonably anchor to multiple facts? *Answer: Mark ambiguous, skip, mark review-required. v1 does not auto-anchor.*
3. **Before Phase D (Task 10):** Should routing persist to DuckDB transient cache instead of PostgreSQL? *Answer: No. ORM tables only, PostgreSQL authoritative.*
4. **Before Phase E (Task 12):** Should review-required counts block deployment? *Answer: No. Log as WARN, show in summary, but never block.*
5. **Before Phase F (Task 17):** Should rollout be staged (e.g., opt-in for first month, then default-on)? *Answer: Global default-on immediately with kill switch for quick rollback.*

---

## Parallelization Opportunities

The following work can be parallelized across sessions:

**Safe to parallelize (independent):**
- Task 4 (DimensionInjector) and Task 5 (MeasureFactMapping) can be developed in parallel
- Task 10 (ORM models) can be developed while Phase B is in progress
- Task 14 (unit tests) and Task 15 (integration tests) can be written in parallel
- Documentation in Task 17 can be started early

**Must be sequential:**
- Task 0 → Task 1 → Task 2 → Task 3 (foundation chain)
- Task 3 → Task 6 (routing integration before per-fact generation)
- Task 9 → Task 12 (diagnostics before API payload)
- Task 15 must wait for Phase E complete (needs full integration)

---

## Summary

This execution plan decomposes the Semantic Router specification into 17 focused tasks across 6 phases, with explicit dependencies, acceptance criteria, and verification steps. Delivery progresses from foundation (graph and categorizer) through generation and routing, then persistence, UI integration, and finally regression hardening.

**Key principles:**
- Fail-safe: ambiguous measures skip, never auto-anchor
- Determinism: all outputs are reproducible given same input
- Auditability: routing decisions persist and queryable
- Rollback-safe: kill switch enables quick disable
- Regression-hardened: extensive test coverage before rollout

**Estimated total effort:** ~15 person-days of focused agent work (assuming 2-hour sessions per task), with sequential execution due to dependencies.

**Go/No-Go Decision Point:** After Checkpoint F, conduct full code review before setting `semantic_router_enabled = True` in production behavior.yaml.
