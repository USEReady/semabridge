# V4.3 UPSERT Rollback Fix - Complete Implementation Summary

## Problem Statement

After QA testing (TC-R-01, TC-R-02, TC-R-06), rollback operations were always using "copy" mode instead of preserving the original "upsert" mode. This was because:

1. UPSERT merge logic was still active (causing incomplete rollbacks)
2. sync_mode was not persisting through the database layer
3. Rollback couldn't retrieve the original sync_mode for dynamic rollback

## Solution Overview

Implemented end-to-end sync_mode persistence from UI → Database → Rollback with complete database schema updates.

---

## Changes Made

### 1. **ORM Model Update** - [src/semabridge/repository/orm/models.py](src/semabridge/repository/orm/models.py)

**Purpose:** Add sync_mode column to the SnapshotRow ORM model

**Changes:**

- Added `sync_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="copy")` field
- Updated `__repr__` to include sync_mode in string representation
- Ensures sync_mode is persisted in database snapshots table

**Before:**

```python
trigger: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

# Relationships
project: Mapped["Project"] = relationship(back_populates="snapshots")
```

**After:**

```python
trigger: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
sync_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="copy")

# Relationships
project: Mapped["Project"] = relationship(back_populates="snapshots")
```

---

### 2. **Pydantic Schema Update** - [src/semabridge/repository/schemas.py](src/semabridge/repository/schemas.py)

**Purpose:** Add sync_mode field to Snapshot Pydantic model for API contracts

**Changes:**

- Added `sync_mode: str = "copy"` field to Snapshot class
- Added documentation comment explaining v4.3 rollback metadata

**Impact:**

- API responses now include sync_mode in snapshot data
- Enables frontend to display which sync mode was used for each snapshot
- Backward compatible (defaults to "copy")

---

### 3. **Model Repository - Data Persistence** - [src/semabridge/repository/model_repository.py](src/semabridge/repository/model_repository.py)

**Purpose:** Ensure sync_mode is actually stored when creating snapshots

**Changes A - commit_model method:**

- Added `sync_mode: str = "copy"` parameter to method signature
- Added `sync_mode=sync_mode` to SnapshotRow initialization in session.add()
- Updated logging to include sync_mode value

**Changes B - \_row_to_snapshot method:**

- Added `sync_mode=getattr(row, 'sync_mode', 'copy')` to Snapshot instantiation
- Handles gracefully if sync_mode column doesn't exist (backward compatibility)

**Code Changes:**

```python
# In commit_model - Line ~445
session.add(
    SnapshotRow(
        # ... other fields ...
        sync_mode=sync_mode,  # ← ADDED
    )
)

# In _row_to_snapshot - Line ~287
return Snapshot(
    # ... other fields ...
    sync_mode=getattr(row, 'sync_mode', 'copy'),  # ← ADDED
)
```

---

### 4. **Rollback Orchestrator - Retrieval Logic** - [src/semabridge/repository/rollback_orchestrator.py](src/semabridge/repository/rollback_orchestrator.py)

**Purpose:** Retrieve sync_mode from database during rollback to enable dynamic rollback

**Changes:**

- Added `from semabridge.repository.model_repository import ModelRepository` import
- Updated Step 1 of execute_rollback to:
  - Create ModelRepository instance
  - Call `get_snapshot()` to retrieve target snapshot from database
  - Extract sync_mode from snapshot object
  - Use retrieved sync_mode for pre/post rollback snapshots

**Code:**

```python
# Step 1: Retrieve sync_mode from target snapshot in database
model_repo = ModelRepository()
target_snapshot = model_repo.get_snapshot(target_version_id)
sync_mode = target_snapshot.sync_mode if target_snapshot and hasattr(target_snapshot, 'sync_mode') else 'copy'
logger.info(f"[{operation_id}] Rolling back to version {target_version_id} (sync_mode={sync_mode})")
```

---

### 5. **Database Migration** - [src/semabridge/repository/migrations/add_sync_mode.py](src/semabridge/repository/migrations/add_sync_mode.py)

**Purpose:** Add sync_mode column to existing snapshots table

**Script:**

- Checks if sync_mode column exists before adding
- Adds `TEXT NOT NULL DEFAULT 'copy'` column to snapshots table
- Indexes existing snapshots with default "copy" value
- Includes rollback function (with warning about SQLite limitations)
- Can be run manually: `python add_sync_mode.py <db_path>`

---

## Data Flow (Complete Path)

```
1. UI Layer (api/ui.py)
   ├─ User selects sync_mode from dropdown
   └─ Passes sync_mode parameter to start_sync_job endpoint
      │
2. Execution Engine (core/engine.py)
   ├─ Receives sync_mode from UI
   ├─ Passes through execution pipeline
   └─ Passes to finalize step
      │
3. Finalize Step (core/engine/finalize.py)
   ├─ Extracts sync_mode from context
   └─ Passes to commit_model()
      │
4. Model Repository - Persistence (repository/model_repository.py)
   ├─ commit_model() receives sync_mode parameter
   ├─ Creates SnapshotRow with sync_mode value
   └─ Inserts into database snapshots table
      │
5. Database (snapshots table)
   └─ Stores sync_mode column value
      │
6. Rollback Orchestrator - Retrieval (repository/rollback_orchestrator.py)
   ├─ Calls model_repo.get_snapshot(target_version_id)
   ├─ SnapshotRow.sync_mode is retrieved from database
   ├─ _row_to_snapshot() maps to Snapshot.sync_mode field
   └─ Rollback uses original sync_mode for pre/post snapshots
```

---

## Files Modified Summary

| File                                                                                                           | Purpose            | Change                         |
| -------------------------------------------------------------------------------------------------------------- | ------------------ | ------------------------------ |
| [src/semabridge/repository/orm/models.py](src/semabridge/repository/orm/models.py)                             | ORM Layer          | Added sync_mode Mapped column  |
| [src/semabridge/repository/schemas.py](src/semabridge/repository/schemas.py)                                   | API Contract       | Added sync_mode Pydantic field |
| [src/semabridge/repository/model_repository.py](src/semabridge/repository/model_repository.py)                 | Data Persistence   | Store & retrieve sync_mode     |
| [src/semabridge/repository/rollback_orchestrator.py](src/semabridge/repository/rollback_orchestrator.py)       | Rollback Logic     | Retrieve sync_mode from DB     |
| [src/semabridge/repository/migrations/add_sync_mode.py](src/semabridge/repository/migrations/add_sync_mode.py) | Database Migration | Add sync_mode column           |

---

## Testing

Run the comprehensive test:

```bash
python test_sync_mode_persistence.py
```

This validates:

- ✅ sync_mode persists when committed with "copy"
- ✅ sync_mode persists when committed with "upsert"
- ✅ Default sync_mode is "copy" when not specified
- ✅ Rollback context can retrieve sync_mode correctly
- ✅ sync_mode flows through database layer without loss

---

## QA Test Cases Resolution

### TC-R-01: UPSERT Rollback with Single Dataset

- **Before:** Rollback used "copy" mode, lost UPSERT metadata
- **After:** Rollback retrieves original "upsert" sync_mode from database, preserves execution context

### TC-R-02: UPSERT Rollback with Multiple Datasets

- **Before:** Rollback used "copy" mode, incomplete rollback
- **After:** sync_mode persists for each dataset under UPSERT context

### TC-R-06: Mixed Sync Modes

- **Before:** All rollbacks defaulted to "copy"
- **After:** Each snapshot remembers its sync_mode; rollbacks respect original mode

---

## Backward Compatibility

✅ **Fully Backward Compatible:**

- Default value: `sync_mode = "copy"` for all existing snapshots
- Pydantic field has default value
- ORM field uses `nullable=False, default="copy"`
- Migration script checks if column exists before adding
- Code uses `getattr(row, 'sync_mode', 'copy')` for graceful fallback

---

## Architecture Benefits

1. **Immutable Versioning:** Each snapshot now records how it was deployed
2. **Intelligent Rollback:** Rollback respects original sync mode (copy vs upsert)
3. **Auditability:** Administrators can see which sync mode was used for each version
4. **Scalability:** Minimal database schema change (1 column) with no performance impact
5. **Future-Proof:** Can add other sync modes (delta, merge, etc.) without code changes

---

## Migration Steps

For existing deployments:

1. **Apply ORM Changes:** Update models.py (automatic with next deploy)
2. **Run Migration:** `python src/semabridge/repository/migrations/add_sync_mode.py <database_path>`
3. **Redeploy:** Backend now serves sync_mode in API responses
4. **Test:** Run `python test_sync_mode_persistence.py`

---

## Verification Checklist

- [x] UPSERT merge block disabled (base.py line 114-119)
- [x] sync_mode accepted from UI (ui.py)
- [x] sync_mode flows through execution pipeline
- [x] SnapshotRow ORM model has sync_mode column
- [x] Snapshot Pydantic model has sync_mode field
- [x] commit_model() stores sync_mode in database
- [x] \_row_to_snapshot() retrieves sync_mode from database
- [x] Rollback orchestrator retrieves sync_mode for dynamic rollback
- [x] Database migration script created
- [x] Backward compatibility maintained
- [x] Test suite validates persistence layer

---

## Result

✅ **sync_mode is now fully persistent through the entire system.**

From UI selection → Database storage → Rollback retrieval, sync_mode is preserved, enabling intelligent rollback that respects the original execution mode (COPY vs UPSERT).
