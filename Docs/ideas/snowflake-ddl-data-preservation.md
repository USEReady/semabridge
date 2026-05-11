# Snowflake DDL: Safe Table Lifecycle Management

## Problem Statement
When Semabridge syncs a semantic model to Snowflake, it must manage physical source tables (which may contain user data). Today, 7 code paths emit `CREATE OR REPLACE TABLE`, atomically destroying all user data. How might we safely check if a table exists, validate schema compatibility, and only create tables that don't exist—aborting syncs when incompatible schemas would cause data loss?

---

## Recommended Direction

**Two-phase fix: Patch the vulnerabilities first, then refactor for sustainability.**

### Phase 1: Safe DDL Pattern (Immediate)
Replace all `CREATE OR REPLACE TABLE` logic with an **existence-check-first pattern**:

```
1. Check if table exists
   ├─ No → CREATE TABLE IF NOT EXISTS
   └─ Yes → validate schema compatibility
      ├─ Compatible → use existing table (no action)
      ├─ Incompatible → ABORT with clear error
```

This pattern fixes the **7 high-risk locations:**
- `schema_manager.py`: `_generate_create_or_replace_table_ddl`, `_drop_extra_columns`
- `table_management.py`: `generate_create_or_replace_table_ddl`
- `measure_sync.py` (both class and module versions): `write_mode="overwrite"` path
- `schema_evolution.py`: CTAS+SWAP pipeline (add row-count validation)
- `aggregate_advisor.py`: AGG_ table creation

**Success metric:** No code path emits `CREATE OR REPLACE TABLE` for any table that could contain user data.

### Phase 2: Centralized DDL Factory (Follow-up)
Create a single `SnowflakeTableDDL` class that all modules call:

```python
class SnowflakeTableDDL:
    def ensure_table(
        self, 
        cursor, 
        table_name: str, 
        columns: list[ColumnDef],
        strategy: TableDDLStrategy = TableDDLStrategy.CREATE_IF_NOT_EXISTS
    ) -> EnsureResult:
        """Single point of truth for all table creation/validation."""
```

**Benefits:** One place to enforce safe patterns, add audit logging, add dry-run support, prevent future regressions.

---

## Key Assumptions to Validate

- [ ] **Schema compatibility validation is feasible:** INFORMATION_SCHEMA.COLUMNS queries suffice to detect join-column and measure-column incompatibilities. *Test:* Mock a mismatched schema and verify the validator catches it.
  
- [ ] **Users accept sync abort on incompatibility:** When an existing table has a schema that doesn't match the model's relationships/measures, aborting is better than data loss. *Test:* User interview — confirm this is preferred over auto-migration or silently using wrong table.

- [x] **CTAS+SWAP row-count validation is active:** A `COUNT(*)` comparison is executed before SWAP and aborts on mismatch. *Code evidence:* `schema_manager.py` performs `COUNT(*)` on source and `__FIXED` tables and raises `ConnectorError` if counts differ.

- [ ] **All 7 DDL sites can unify under one abstraction:** No hidden special cases (e.g., transactions, permissions, types) that force each site to diverge. *Test:* Attempt factory design and check for "escape hatches."

---

## MVP Scope

**In:**
- Replace `CREATE OR REPLACE TABLE` with `CREATE TABLE IF NOT EXISTS` in all 7 locations
- Implement column-level schema compatibility validation (join columns, measure columns, data types)
- For existing tables: use as-is if compatible; abort if incompatible
- Add row-count validation to CTAS+SWAP pipeline
- Update Snowflake behavior config to remove dead `ddl_strategy` field

**Out (do in Phase 2):**
- Centralized factory refactoring (ships after MVP is tested)
- Auto-migration of existing table schemas
- Dry-run mode
- Audit logging for all DDL operations

---

## Not Doing (and Why)

- **Auto-migration of existing table schemas** — Safer to let users make the decision when conflicts occur. Can add later if demand is high.

- **CREATE OR REPLACE for any reason** — Idempotency is not worth data loss. Use `IF NOT EXISTS` + alter instead. If you need true idempotency (re-run without errors), build it via the factory pattern in Phase 2, not via destructive DDL.

- **Substring-based DDL filtering** — Current code uses `if table_name in ddl_upper` which is fragile. In MVP, fix the most critical 7 paths. Refactor filtering in Phase 2 when centralizing.

- **Special handling for measure sync** — Don't treat MEASURES_ tables as different. Apply the same safe pattern everywhere.

---

## Open Questions

1. **What happens if a user manually alters a table's column types in ways that break joins?** Do we detect this and abort, or silently use the broken table? (Recommend: detect and abort with a clear message guiding the user to either fix the schema or drop the table.)

2. **How do we handle backward compatibility?** Are there existing syncs running today that depend on `CREATE OR REPLACE` behavior? (Recommend: add a feature flag `preserve_existing_tables=true` by default in v2.x, with clear migration guide.)

3. **(Resolved) CTAS+SWAP safety check timing:** Row-count validation is implemented in the active pipeline before SWAP. Remaining work is centralization/refactoring, not adding the safety check itself.

4. **How do we test this without risking production tables?** (Recommend: integration tests against a Snowflake dev schema; mock Snowflake's INFORMATION_SCHEMA in unit tests.)

---

## Where the Process Diverges (Implemented Fix)

The largest divergence between this planning document and current runtime behavior is that the previously identified CTAS+SWAP row-count gap has already been patched.

- **Documented diagnosis (historical):** The CTAS+SWAP path had no row-count safety net before table swap.
- **Observed implementation (current):** The pipeline validates `COUNT(*)` for both original and `__FIXED` tables immediately before `ALTER TABLE ... SWAP WITH ...`.
- **Operational impact:** The cast-failure data-loss vector described as a P1 risk in the diagnosis is mitigated for runs executing the current code path.

In short, architecture and execution still align on COPY mode + CTAS+SWAP + Semantic View replacement, but runtime evidence and source code show the pre-swap count validation recommendation is active.

---

## Implementation Notes

- **Priority:** P0 for measure sync (high likelihood + impact); P1 for other 6 paths
- **Testing:** Unit test schema validator against mock schemas; integration test all 7 call sites with a Snowflake dev schema
- **Rollout:** Feature-flagged behind `preserve_existing_tables` config, defaulting to enabled
- **Docs:** Update user guide to explain the abort-on-incompatible behavior and how to resolve conflicts
