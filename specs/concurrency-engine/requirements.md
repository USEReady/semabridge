# Concurrency Engine - Requirements

**Feature:** Parallel Model Processing and Multi-Target Broadcasting
**Status:** Not Started
**Priority:** High
**Assigned:** January 22, 2026

---

## 1. Overview

The Concurrency Engine enables high-throughput semantic model processing by utilizing multi-core parallelism. It supports parallel processing of multiple models and broadcasting a single model to multiple targets simultaneously.

---

## 2. User Stories

### US-CONC-001: Parallel Model Processing
**As a** data engineer  
**I want** to process multiple semantic models in parallel  
**So that** I can sync 50+ models in minutes instead of hours

**Acceptance Criteria:**
- System processes multiple models concurrently
- Progress is visible in real-time
- Failure of one model doesn't stop others (best-effort mode)
- System utilizes available CPU cores efficiently

### US-CONC-002: Multi-Target Broadcasting
**As a** platform engineer  
**I want** to deploy one model to multiple targets simultaneously  
**So that** I can keep Snowflake and Fabric in sync faster

**Acceptance Criteria:**
- Single source extraction feeds multiple target deployments
- Target deployments run in parallel
- Each target deployment is independent
- Failure in one target doesn't affect others

### US-CONC-003: Intelligent Retry Logic
**As a** system administrator  
**I want** transient failures to be automatically retried  
**So that** temporary network issues don't cause sync failures

**Acceptance Criteria:**
- Network timeouts are retried automatically
- API rate limits trigger exponential backoff
- Permanent errors (auth, validation) fail fast without retry
- Retry attempts are logged and visible

### US-CONC-004: Resource Management
**As a** developer  
**I want** the system to adapt to available hardware  
**So that** it doesn't freeze my machine during processing

**Acceptance Criteria:**
- System auto-detects CPU core count
- Default workers = CPU cores - 1
- User can override worker count via config/CLI
- Minimum 1 worker always guaranteed

### US-CONC-005: Live Progress Dashboard
**As a** user  
**I want** to see real-time progress of parallel operations  
**So that** I know what's happening and can estimate completion time

**Acceptance Criteria:**
- Overall progress bar shows completion percentage
- Active worker status is displayed
- Success/failure counts update in real-time
- Failed models are listed with error messages

---

## 3. Functional Requirements

### REQ-CONC-001: Multi-Model Parallelism
- System MUST process multiple models in parallel when wildcard selection matches multiple models
- Atomic unit of work MUST be one complete model (extract → convert → deploy)
- Failure of one model MUST NOT stop processing of other models (in best-effort mode)
- System MUST support strict mode where any failure aborts all processing

### REQ-CONC-002: Multi-Target Broadcasting
- System MUST support one source → multiple targets architecture
- Source extraction MUST occur only once per model
- Target deployments MUST run in parallel after source extraction
- Each target deployment MUST be independent and isolated

### REQ-CONC-003: Intelligent Retry Logic
- System MUST retry transient failures automatically
- Maximum retry attempts MUST be configurable (default: 3)
- Retry backoff MUST be exponential (1s, 2s, 4s, 8s, ...)
- Permanent errors MUST fail fast without retry:
  - Authentication errors
  - Validation errors
  - Permission errors
  - Schema errors

### REQ-CONC-004: Fail-Fast Validation
- System MUST perform connectivity checks before starting parallel processing
- System MUST validate credentials for all connectors upfront
- System MUST abort immediately if source is unreachable
- System MUST provide clear error messages for validation failures

### REQ-CONC-005: Live Status Reporting
- CLI MUST display live progress dashboard (not just scrolling logs)
- Dashboard MUST show:
  - Overall progress bar with percentage
  - Active worker status (what each worker is doing)
  - Success/failure counts
  - List of failed models with error messages
- Dashboard MUST update in real-time without flickering

### REQ-CONC-006: Hardware Awareness
- System MUST auto-detect CPU core count
- Default worker count MUST be (CPU cores - 1) to leave resources for OS
- Minimum worker count MUST be 1
- User MUST be able to override worker count via:
  - Global config file (`~/.semabridge/config.yaml`)
  - Project config file (`semabridge.yaml`)
  - CLI argument (`--workers N`)

### REQ-CONC-007: Graceful Shutdown
- System MUST handle Ctrl+C gracefully
- In-progress tasks MUST be allowed to complete or cancel cleanly
- Partial results MUST be saved before shutdown
- System MUST not leave resources in inconsistent state

---

## 4. Non-Functional Requirements

### NFR-CONC-001: Performance
- System MUST achieve near-linear speedup with additional cores (up to 8 cores)
- Overhead of parallelization MUST be < 5% of total execution time
- Worker startup time MUST be < 100ms per worker

### NFR-CONC-002: Reliability
- System MUST handle worker crashes gracefully
- Failed tasks MUST be logged with full stack traces
- System MUST continue processing remaining tasks after worker failure

### NFR-CONC-003: Observability
- All parallel operations MUST be logged with worker ID
- Execution timeline MUST be reconstructable from logs
- Performance metrics MUST be collected (task duration, retry counts)

### NFR-CONC-004: Resource Limits
- System MUST respect memory limits (no unbounded queues)
- System MUST limit concurrent database connections
- System MUST implement backpressure when targets are slow

---

## 5. Constraints

- Python GIL limits true parallelism for CPU-bound tasks
- Must use ProcessPoolExecutor for CPU-intensive operations
- Must use ThreadPoolExecutor for I/O-bound operations
- Shared state between workers must be minimized

---

## 6. Assumptions

- Models are independent and can be processed in any order
- Target systems can handle concurrent connections
- Network bandwidth is sufficient for parallel uploads
- Disk I/O is not a bottleneck

---

## 7. Dependencies

- Python `concurrent.futures` module
- `tenacity` library for retry logic
- `rich` library for live dashboard
- DuckDB for state management (thread-safe)

---

## 8. Open Questions

1. Should we support nested parallelism (parallel models + parallel targets)?
2. How do we handle rate limits across multiple workers?
3. Should we implement a work-stealing scheduler for load balancing?
4. How do we prioritize models when resources are constrained?
5. Should we support distributed execution across multiple machines?

---

## 9. Out of Scope

- Distributed execution across multiple machines
- GPU acceleration
- Real-time streaming updates
- Dynamic worker scaling based on load
- Priority queues for model processing
