# Retention Policy Feature - Complete Implementation

## Overview

The retention policy feature allows users to configure automatic snapshot pruning per project with **safe-delete logic** that never removes snapshots still referenced by any run.

## Database Schema

### Table: `retention_policies`

```sql
CREATE TABLE retention_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    strategy TEXT NOT NULL CHECK (strategy IN ('count', 'days', 'unlimited')) DEFAULT 'unlimited',
    max_snapshots_per_connector INTEGER,  -- used when strategy = 'count'
    max_age_days INTEGER,                 -- used when strategy = 'days'
    prune_manual_snapshots BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Updated: `snapshots` table

Added columns for per-connector tracking:
- `connector_id TEXT` - Identifier for the connector that created this snapshot
- `trigger TEXT` - Source trigger type ('manual', 'scheduled', 'auto')

## Pruning Logic - Safe Delete

The implementation ensures **CRITICAL safety**:

```python
def _get_all_referenced_snapshot_ids(session):
    """
    Collect every snapshot ID referenced anywhere in the runs table.
    
    Checks:
    - before_src_snapshot_id
    - restore_snapshot_id
    - before_tgt_snapshots (JSON array)
    - after_tgt_snapshots (JSON array)
    """
```

**Never removes snapshots that are:**
1. Referenced by any run (via FK columns or JSONB arrays)
2. Marked as manual (if `prune_manual_snapshots=False`)
3. Already deleted (`deleted_at` is set)

## Strategies

### 1. Count Strategy
Keep only the most recent N snapshots per connector.

```python
# Set policy
set_retention_policy(
    session, project_id,
    strategy="count",
    max_snapshots_per_connector=10,
    prune_manual_snapshots=False,
)
```

### 2. Days Strategy
Prune snapshots older than N days.

```python
set_retention_policy(
    session, project_id,
    strategy="days",
    max_age_days=30,
    prune_manual_snapshots=False,
)
```

### 3. Unlimited Strategy (Default)
No automatic pruning.

```python
set_retention_policy(
    session, project_id,
    strategy="unlimited",
)
```

## API Endpoints

### Set Retention Policy
```http
PUT /api/version-control/retention-policy?project_id=xxx&strategy=count&max_snapshots=10&prune_manual=false
```

### Get Retention Policy
```http
GET /api/version-control/retention-policy?project_id=xxx
```

### Apply Retention Policy (Run Pruning)
```http
POST /api/version-control/retention?project_id=xxx&days_to_keep=30
```

### Get Storage Stats
```http
GET /api/version-control/stats?project_id=xxx
```

## Usage Examples

### Example 1: Set Count-Based Policy
```python
from semabridge.api.services.retention_service import set_retention_policy, apply_retention_policy
from semabridge.repository.orm.session_factory import db_manager

session = db_manager._session()

# Configure policy: keep 10 snapshots per connector, protect manual snapshots
set_retention_policy(
    session, 
    "project-123",
    strategy="count",
    max_snapshots_per_connector=10,
    prune_manual_snapshots=False,
)

# Apply the policy (run pruning)
result = apply_retention_policy(session, "project-123")
print(f"Pruned {result['pruned']} snapshots, kept {result['kept']}")
```

### Example 2: Set Time-Based Policy
```python
# Configure policy: prune snapshots older than 30 days
set_retention_policy(
    session,
    "project-123",
    strategy="days",
    max_age_days=30,
    prune_manual_snapshots=True,  # Also prune manual snapshots
)
```

### Example 3: Per-Connector Pruning
```python
# Policy applies per connector automatically
set_retention_policy(
    session,
    "project-123",
    strategy="count",
    max_snapshots_per_connector=5,  # 5 per connector, not 5 total
)

# If project has 3 connectors, total kept = 5 * 3 = 15
```

## Implementation Details

### File Structure
```
src/semabridge/
├── api/
│   ├── controllers/
│   │   └── versioning_controller.py    # API endpoints
│   └── services/
│       ├── retention_service.py        # Core logic
│       └── version_control_impl.py     # Backend wrapper
├── repository/
│   ├── orm/
│   │   └── models.py                   # SQLAlchemy models
│   └── migrations/
│       └── add_retention_policy.py     # Database migration
```

### Key Functions

#### `apply_retention_policy(session, project_id, connector_id)`
Main pruning function that:
1. Fetches policy for the project
2. Gets all snapshots (filtered by connector if provided)
3. Collects all referenced snapshot IDs from runs
4. Filters out protected snapshots (manual, referenced)
5. Applies strategy (count or days)
6. Soft-deletes by setting `deleted_at`

#### `_get_all_referenced_snapshot_ids(session)`
Safety function that scans the `runs` table for:
- Direct FK columns (`before_src_snapshot_id`, `restore_snapshot_id`)
- JSON array fields (`before_tgt_snapshots`, `after_tgt_snapshots`)

Returns a set of snapshot IDs that must **never** be pruned.

## Testing

Run the test suite:
```bash
pytest Tests/test_retention_policy.py -v
```

Tests cover:
- Policy CRUD operations
- Safe pruning (never removes referenced snapshots)
- Manual snapshot protection
- Per-connector pruning
- Strategy handling (count, days, unlimited)

## Migration

Apply the database migration:
```bash
python -m src.semabridge.repository.migrations.add_retention_policy semabridge.db
```

This will:
1. Create `retention_policies` table
2. Add `connector_id` and `trigger` columns to `snapshots`
3. Create necessary indexes

## Scale Considerations

- **Background Job**: Pruning runs as a background job post-sync, never in the critical path
- **Per-Connector**: Policies apply per connector to handle large-scale deployments
- **Soft Delete**: Uses `deleted_at` timestamp, allowing for recovery if needed
- **Indexing**: Indexes on `connector_id`, `trigger`, and `deleted_at` for performance

## Safety Guarantees

1. ✅ **Referenced snapshots are NEVER deleted** - Checked before every prune operation
2. ✅ **Manual snapshots protected by default** - Unless explicitly enabled
3. ✅ **Soft delete only** - Can recover if needed
4. ✅ **Per-connector isolation** - Policies don't affect unrelated connectors
5. ✅ **Transaction safe** - All operations in a transaction, rollback on error
