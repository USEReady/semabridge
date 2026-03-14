# Semabridge Concurrency & Parallel Processing: Technical Analysis & PRD

## Part 1 — Current System Analysis

### Architecture Overview
Semabridge operates as a monolithic Python-based CLI application with a deeply decoupled, plugin-based architecture. 
It follows a clear layer separation:
- **Core Engine:** Orchestrates the 10-step sync process (`execution_engine.py`).
- **Interfaces:** Abstract Base Classes defining strict contracts (`interfaces.py`).
- **Connectors:** Isolated system integrations (Snowflake, Fabric).
- **Intermediate Format:** The canonical OSI/SML representation (`intermediate/` and `sml/`).
- **Repository:** DuckDB-based state and version control (`repository/`).

### Technology Stack
- **Language:** Python 3.9+
- **Data Validation:** Pydantic (strict typing enforced via `osi/models.py`)
- **Metadata Storage:** DuckDB (`DuckDBManager`)
- **Connectors:** `snowflake-connector-python`, `requests` (Fabric API)
- **CLI Framework:** Typer / Click 

### Current Execution Flow
The system executes a strictly sequential, 10-step pipeline (`ExecutionEngine.execute`):
1. **Load Config:** Parse options and `.env`.
2. **Init Identifiers:** Generate `project_id`, `run_id`.
3. **Resolve Auth:** Environment-based credential resolution.
4. **Extract:** Fetch metadata/data from source (Snowflake or Fabric).
5. **Validate Source:** Ensure extraction output is structurally valid.
6. **Convert to SML:** Map source format to canonical OSI/SML.
7. **Persist Artifacts:** Save SML blob and source artifact to DuckDB.
8. **Convert to Target:** Generate output artifacts (e.g., TMSL, DDLs).
9. **Deploy:** Apply target artifacts to the destination.
10. **Finalize:** Record run status and metrics.

### Synchronous vs Asynchronous Components
Currently, **100% of the architecture is synchronous**. 
- Network calls to Snowflake (`connection.cursor().execute()`) block the main thread.
- REST API calls to Fabric (`requests.post/get`) block the main thread.
- File system access (writing output files) and DuckDB commits are synchronous.
- In-memory processing (Inference Engine, Measure Detector) runs on a single continuous thread.

### Performance Bottlenecks and Blocking Operations
1. **Source Extraction (I/O Bound):**
   - *Fabric:* `execute_paginated_measure_sync` makes sequential REST API calls for each data partition. If querying 10 partitions, 10 synchronous HTTP requests are made back-to-back. `get_table_row_counts` runs a sequential DAX query.
   - *Snowflake:* Execution of metadata queries (`_extract_primary_keys`, `_extract_foreign_keys`) runs sequentially.
2. **Target Deployment (I/O Bound):**
   - *Snowflake:* `_ensure_source_tables_exist` and executing DDLs run queries sequentially. Creating 100 tables takes 100 distinct round-trips.
3. **Heuristics & Inference (CPU Bound):**
   - The `SmlInferenceEngine` and `MeasureDetector` iterate over all columns and tables sequentially. While fast for small models, a large ERP schema (1,000+ tables) causes noticeable CPU execution time.

### I/O-Bound vs CPU-Bound Tasks
- **I/O-Bound:** Fabric REST API polling, DAX query execution, Snowflake query execution (`execute()`), DuckDB `commit_model()`, File I/O (`model.bim`, `.yaml` generation).
- **CPU-Bound:** `SMLAssembler` building, JSON/Base64 serialization/deserialization, Heuristic scoring in `SmlInferenceEngine`, `MeasureDetector`.

### Thread Safety Concerns
- **DuckDB:** The current `DuckDBManager` implementation may not be thread-safe for concurrent writes across different runs if connection pooling isn't properly configured.
- **Connectors:** Instances of `requests.Session` or `snowflake.connector` are typically not thread-safe and require per-thread instantiation or connection pooling.
- **State Management:** `RunContext` object is modified concurrently across pipeline execution, meaning a shared state model would require locks or careful isolation if passed across threads.

### Resource Usage
- **CPU:** Spiky during Step 6 (Conversion) and JSON rendering.
- **Memory:** High overhead during Step 4/5 for large models, as the entire schema representation is loaded into memory as dicts/Pydantic models before conversion.
- **Network:** Long idle blocking periods waiting for external API responses.

### Scalability Limitations
As semantic models grow larger (e.g., enterprise analytical models with 500+ datasets):
1. **Execution Time:** O(N) linear growth with the number of tables extracted/deployed.
2. **Fabric API Limits:** Sequential DAX queries risk gateway timeouts on large cross-joins.

### Risks in Introducing Concurrency
- **Rate Limiting:** Heavy parallel requests might trigger Azure/Fabric API HTTP 429 (Too Many Requests) or Snowflake concurrency limits.
- **Data Consistency:** Managing distributed failure in Step 9 (Deployment). If one parallel task fails, rolling back the other concurrent operations is complex.
- **Debugging Complexity:** `[RunID]` context logging relies on sequential thread execution; debugging async logs is significantly harder.

---

## Part 2 — Concurrency Opportunities

### Identified Parallelizable Tasks
1. **Extraction (Step 4):**
   - Concurrent execution of DAX queries during `execute_paginated_measure_sync` across different date partitions.
   - Concurrent execution of independent metadata queries in Snowflake (PKs, FKs, Row Counts).
2. **Deployment (Step 9):**
   - Concurrent DDL execution (`CREATE TABLE`, `ALTER TABLE`) in Snowflake.
3. **Conversion (Step 6/8):**
   - Parallel inference engine scoring for independent table subsets.

### Independent Modules/Services
- The `Extractor` and `Emitter` components operate on clear boundaries and can be invoked asynchronously relative to the main `ExecutionEngine`.

### Recommended Concurrency Models

1. **`asyncio` + `aiohttp` / `snowflake-connector-python` (Async API)**
   - **Best For:** Fabric REST API polling and Snowflake querying.
   - **Why:** Python's native `asyncio` is perfect for network I/O-bound tasks. It solves the DAX query bottleneck without the OS thread overhead.
2. **`concurrent.futures.ThreadPoolExecutor`**
   - **Best For:** Wrapping existing synchronous SDKs (if async replacement is too expensive/complex right now).
   - **Why:** Safely allows parallel DAX queries and DDL execution in Snowflake without rewriting the entire core to be `async`.
3. **`concurrent.futures.ProcessPoolExecutor`**
   - **Best For:** Extremely large inference tasks (CPU Bound).
   - **Why:** Bypasses the GIL (Global Interpreter Lock) for heavy Pydantic validations or JSON parsing on massive models.

---

## Part 3 — Proposed Concurrency Architecture

### Updated Architecture Data Flow
```text
           [ CLI Invocation ]
                   |
            (Main Thread)
                   v
       1. Load Config & Auth
                   |
                   v
   +---------------------------------+
   |    ASYNC TASK COORDINATOR       |
   |   (ThreadPool / asyncio)        |
   +---------------------------------+
          |        |        |
    [Task 1]  [Task 2]  [Task N]  <-- Concurrent Extraction (Tables, Partitions)
          |        |        |
   +---------------------------------+
   |     SYNC/CPU BOUND POOL         | <-- ProcessPool for SML Conversion / Inference
   +---------------------------------+
                   |
            [ Validation ]
                   |
            (Main Thread)
                   v
    7. Persist to DuckDB (Thread-safe single connection)
                   |
   +---------------------------------+
   |    ASYNC EMITTER POOL           | <-- Concurrent DDL Execution / Target Creation
   +---------------------------------+
                   |
            10. Finalize Run
```

### Synchronization Strategy
- **Extraction:** Fan-out to fetch metadata, Fan-in to construct the unified `SourceFormat`.
- **Deployment:** Dependency Graph execution. Base dimension tables deploy concurrently, followed by fact tables.

### State Management
- `RunContext` remains the immutable source of truth.
- Concurrent workers yield results back to the `ExecutionEngine`, which updates the context in the main thread (avoiding lock contention).

### Failure Handling & Retries
- Implement standard retry mechanisms (`tenacity`) for HTTP 429s or transient DB locks.
- Wrap parallel execution in `ThreadPoolExecutor` context managers to ensure fail-fast cancellation.

---

## Part 4 — Product Requirements Document (PRD)

### Problem Statement
The current Semabridge execution engine operates sequentially. As users onboard enterprise-scale models (hundreds of tables/metrics), sync operations take exponentially longer. The sequential nature of REST APIs and database calls bottlenecks the entire pipeline, degrading the developer experience and system throughput.

### Goals and Success Metrics
- **Goal 1:** Reduce overall sync time by at least 50% for models exceeding 50 tables or highly partitioned datasets.
- **Goal 2:** Prevent network timeouts during deeply partitioned Fabric DAX extractions.
- **Success Metrics:**
  - `duration_ms` in `runs` table decreases by > 50% for large syncs.
  - Zero regression in SML validation success rate.
  - API Rate limits (HTTP 429) remain below 1% of total requests, handled gracefully by retries.

### Functional Requirements
1. The CLI must accept an optional `--parallel / -p` flag to enable concurrent mode (defaulting to False for V1 rollout).
2. Fabric measure syncs must fetch partitions concurrently.
3. Snowflake DDL generation and table creation must execute concurrently where topologically valid.

### Non-Functional Requirements
- **Reliability:** The system must gracefully catch and aggregate exceptions from parallel tasks into a unified `ConversionError` or `ExtractionError`.
- **Resource Management:** Concurrency limits (e.g., max 10 workers) must be configurable via `.env` (e.g., `MAX_WORKERS=5`).

### Technical Requirements
- Utilize `concurrent.futures.ThreadPoolExecutor` to wrap I/O bound synchronous network calls in `FabricExtractor` and `SnowflakeEmitter`.
- Utilize `tenacity` for backoff/retry logic.
- Introduce `ThreadLocal` or connection pooling for `requests.Session` and `snowflake.connector`.

### Risks and Mitigation
- **Risk:** Exhausting Snowflake connection limits.
  - *Mitigation:* Cap the `ThreadPoolExecutor` to a configurable maximum limit (e.g., `MAX_WORKERS=5`).
- **Risk:** Unpredictable logging context.
  - *Mitigation:* Ensure the `[RunID]` context var is safely passed to the execution threads. Use python's `logging` filters with `threading.local`.

### Acceptance Criteria
- Given a Fabric model with 20 date partitions, the sync process fetches all 20 in parallel, completing in the time it takes for the single longest query rather than the sum of all queries.
- Given a Snowflake model with 100 tables, DDL execution runs concurrently, dropping deployment time by 50%.
- The `pytest` test suite maintains >80% code coverage.
- Rollback functionality is completely unaffected by the extraction methodology.

---

## Part 5 — Implementation Roadmap

### Phase 1: Foundation (Week 1)
- **Task:** Introduce concurrency configuration (`max_workers`) in `pyproject.toml` / `.env`.
- **Task:** Implement `ThreadPoolExecutor` and connection pooling utilities in `src/semabridge/utils/concurrency.py`.
- **Task:** Add `tenacity` for retry logic on transient network failures.

### Phase 2: Refactor Connectors (Week 2)
- **Task:** Update `FabricExtractor._poll_operation` and `execute_paginated_measure_sync` to use concurrent execution for partitions.
- **Task:** Update `SnowflakeExtractor` to fetch Primary Keys and Foreign Keys in parallel (since they are independent metadata queries).

### Phase 3: Emitter Concurrency (Week 3)
- **Task:** Modify `SnowflakeEmitter._ensure_source_tables_exist` to map table recreations across a ThreadPool.
- **Task:** Add topological sorting to ensure foreign-key referenced tables are created before their dependents.

### Phase 4: Testing & Rollout (Week 4)
- **Backward Compatibility:** CLI behavior remains unchanged without the `--parallel` flag.
- **Milestone:** Release v1.1.0 with opt-in concurrency.
- **Tools Needed:** `tenacity` (retries), `concurrent.futures` (stdlib).
