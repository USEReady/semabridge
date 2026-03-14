# Concurrency & Parallel Processing Tasks

This document translates the Architecture and Roadmap sections of the Semabridge Concurrency PRD into actionable sprints with clear Definitions of Done (DoD).

## Sprint 1: Foundation (Week 1)
**Goal:** Establish concurrency configuration, async task coordination utilities, and retry logic.

- [x] **Task 1.1: Concurrency Configuration**
  - **Description:** Introduce concurrency configuration (`max_workers`) in `pyproject.toml` and `.env`. The CLI must accept an optional `--parallel / -p` flag to enable concurrent mode (defaulting to False).
  - **Definition of Done:** 
    - Configuration is parsed successfully by the config loader.
    - CLI accepts the `--parallel` flag without errors.
    - Default behavior remains synchronous (`--parallel` is False by default).

- [x] **Task 1.2: Implement Async Task Coordinator**
  - **Description:** Implement `ThreadPoolExecutor` and connection pooling utilities in `src/semabridge/utils/concurrency.py`.
  - **Definition of Done:** 
    - `concurrency.py` module is created with a thread-pool manager.
    - Connection pooling for `requests.Session` and `snowflake.connector` (or `ThreadLocal` strategies) is implemented safely.
    - Unit test passes with 100% coverage for the new utility module.

- [x] **Task 1.3: Add Tenacity Retry Logic**
  - **Description:** Add `tenacity` for backoff/retry logic on transient network failures and HTTP 429 errors.
  - **Definition of Done:** 
    - Tenacity decorators are correctly configured with backoff limits.
    - Failed test requests correctly trigger retries before failing completely.

## Sprint 2: Refactoring Connectors (Week 2)
**Goal:** Parallelize the extraction of metadata and data partitions.

- [x] **Task 2.1: Concurrent Fabric Extraction**
  - **Description:** Update `FabricExtractor._poll_operation` and `execute_paginated_measure_sync` to use concurrent execution for date partitions using the ThreadPool.
  - **Definition of Done:** 
    - Fabric measure syncs fetch all partitions concurrently.
    - Extraction time for deeply partitioned datasets drops significantly.
    - Network timeouts during DAX extractions are prevented.

- [x] **Task 2.2: Concurrent Snowflake Extraction**
  - **Description:** Update `SnowflakeExtractor` to fetch Primary Keys, Foreign Keys, and row counts in parallel.
  - **Definition of Done:** 
    - Independent metadata queries are dispatched concurrently.
    - The `SourceFormat` model remains structurally valid and completely unified.

- [x] **Task 2.3: Safe State Context Management**
  - **Description:** Ensure the `[RunID]` context var is safely passed to the execution threads using `threading.local` and that the `RunContext` is updated immutably in the main thread.
  - **Definition of Done:** 
    - Async logs correctly include the `[RunID]`.
    - No synchronization locks or data races occur when context yields back to `ExecutionEngine`.

## Sprint 3: Emitter Concurrency (Week 3)
**Goal:** Parallelize DDL generation and table creations while respecting dependencies.

- [x] **Task 3.1: Topological Sorting of References**
  - **Description:** Add topological sorting to ensure foreign-key referenced tables (like Base dimensions) are created before their dependents (Fact tables).
  - **Definition of Done:** 
    - Given an SML schema, a graph builds and sorts dependencies correctly.
    - Cyclic dependencies are caught and throw a `ConversionError`.

- [x] **Task 3.2: Concurrent Snowflake Deployment**
  - **Description:** Modify `SnowflakeEmitter._ensure_source_tables_exist` to map table recreations across a ThreadPool.
  - **Definition of Done:** 
    - Table creation statements are executed concurrently where topologically valid.
    - Dropping deployment time for models with 100+ tables by at least 50%.
    - Rollback functionality is thoroughly tested and remains fully unaffected.

## Sprint 4: Testing & Rollout (Week 4)
**Goal:** CPU-Bound pool exploration, failure handling, and release.

- **Task 4.1: CPU-Bound Processing Pool (Optional/Exploratory)**
  - **Description:** Introduce a `ProcessPoolExecutor` for the `SmlInferenceEngine` and `MeasureDetector` to bypass the GIL for heavy JSON validation and inference scoring.
  - **Definition of Done:** 
    - Conversion phase operates safely within a ProcessPool.
    - Benchmarks show improved inference performance for large ERP schemas, or the task is documented as unnecessary due to acceptable existing speeds.

- **Task 4.2: Graceful Failure Handling**
  - **Description:** Catch and aggregate exceptions from parallel tasks into a unified `ConversionError` or `ExtractionError`.
  - **Definition of Done:** 
    - If one parallel task fails, the remaining pool is cancelled safely.
    - A unified error bubbles up to the CLI gracefully.

- **Task 4.3: Full Regression Testing & Release**
  - **Description:** Finalize the test suite, ensure backward compatibility for synchronous flow, and tag v1.1.0 release.
  - **Definition of Done:** 
    - `pytest` test suite maintains >80% code coverage.
    - Zero regression in SML validation success rate.
    - Release v1.1.0 tagged with opt-in concurrency.
