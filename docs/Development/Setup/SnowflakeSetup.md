# Snowflake Adapter Setup Guide

This guide covers the setup and configuration for using SemaBridge with Snowflake.

## Prerequisites

- Snowflake account (any edition)
- uv run 3.9+
- SemaBridge installed

## Connection Configuration

### Environment Variables

Set the following environment variables:

```bash
# Required
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=your_warehouse
SNOWFLAKE_DATABASE=your_database
SNOWFLAKE_SCHEMA=your_schema

# Optional
SNOWFLAKE_ROLE=your_role  # Defaults to user's default role
```

### Account Format

The account identifier should include the region:
- `myaccount.us-east-1`
- `myaccount.eu-west-1`

## Role & Permission Requirements

The Snowflake user needs the following permissions:

### For Forward Sync (Snowflake â†’ Fabric)

```sql
-- Read metadata
GRANT USAGE ON DATABASE your_database TO ROLE your_role;
GRANT USAGE ON SCHEMA your_database.your_schema TO ROLE your_role;
GRANT SELECT ON ALL TABLES IN SCHEMA your_database.your_schema TO ROLE your_role;
GRANT SELECT ON FUTURE TABLES IN SCHEMA your_database.your_schema TO ROLE your_role;
```

### For Reverse Sync (Fabric â†’ Snowflake)

```sql
-- Create and manage semantic views
GRANT CREATE VIEW ON SCHEMA your_database.your_schema TO ROLE your_role;
GRANT CREATE TABLE ON SCHEMA your_database.your_schema TO ROLE your_role;

-- If using Cortex Analyst (optional)
GRANT CREATE SEMANTIC VIEW ON SCHEMA your_database.your_schema TO ROLE your_role;
```

## Rollback Configuration

Snowflake adapter supports full rollback functionality.

### How Rollback Works

1. **Snapshot Creation**: Before any deployment, a snapshot of the current semantic state is created
2. **Version Tracking**: All versions are tracked in DuckDB with unique version IDs
3. **Non-Destructive**: Rollback creates a *new* version, preserving history
4. **Deployment**: Rolled-back semantic views are re-deployed to Snowflake

### Rollback Commands

```bash
# Preview what rollback will change
uv run -m semabridge semantic rollback-preview --adapter snowflake --to-version v20260119_103700_snowflake_001

# Execute rollback
uv run -m semabridge rollback --dataset-id YOUR_DATASET_ID --tag v1.0 --deploy
```

## Testing Procedures

### Test Connection

```bash
uv run -m semabridge validate
```

### Test Rollback

1. Deploy initial version:
```bash
uv run -m semabridge reverse-sync -d DATASET_ID --deploy --tag v1.0
```

2. Make changes and deploy new version:
```bash
uv run -m semabridge reverse-sync -d DATASET_ID --deploy --tag v2.0
```

3. Rollback to v1.0:
```bash
uv run -m semabridge rollback -d DATASET_ID --tag v1.0 --deploy
```

4. Verify in Snowflake:
```sql
SHOW VIEWS IN SCHEMA your_database.your_schema;
```

## Troubleshooting

### Common Issues

#### "User authentication failed"
- Verify username and password
- Check if user account is locked
- Ensure account identifier includes region

#### "Warehouse does not exist"
- Verify warehouse name
- Check if user has USAGE permission on warehouse

#### "Schema does not exist"
- Create the schema first
- Verify user has USAGE permission

#### Rollback fails with "Version not found"
- Check version IDs with: `uv run -m semabridge history -d DATASET_ID`
- Ensure you're using the correct dataset ID

### Debug Mode

Enable verbose logging:
```bash
uv run -m semabridge -v reverse-sync -d DATASET_ID --deploy
```

### View Logs

```bash
uv run -m semabridge semantic logs-show operations --lines 50
uv run -m semabridge semantic logs-show errors --lines 20
```

## Performance Tuning

### Snapshot Operations

- Snapshots are stored as JSON files
- For large models (100+ tables), consider:
  - Increasing warehouse size during deployment
  - Using staging schema for testing

### Best Practices

1. **Version Tagging**: Always use descriptive tags (e.g., `v1.0-initial`, `v1.1-added-revenue-measure`)
2. **Pre-Deploy Snapshots**: Automatic, but verify with `semantic snapshot-list`
3. **Retention Policy**: Configure cleanup for old snapshots (default: 90 days)

## Schema for Semantic Versioning

SemaBridge uses DuckDB for version control. The schema is automatically created:

```sql
-- Version control tables (in semabridge_state.db)
projects (project_id, name, workspace_id, last_updated)
snapshots (snapshot_id, project_id, timestamp, version_tag, sml_blob)
changes (change_id, snapshot_id, object_type, diff_type, old_value, new_value)
```

No additional setup in Snowflake is required for version control.

