# Databricks Object Type Verification Guide

## Overview
This guide explains how to use `verify_object_type.sql` to determine whether an object synced from Microsoft Fabric is a Physical Table, Standard View, or Metric View.

---

## Quick Start

### 1. Modify the Parameters
At the top of `verify_object_type.sql`, update these values:

```sql
DECLARE @object_name STRING DEFAULT 'sales_data';    -- Object to verify
DECLARE @catalog STRING DEFAULT 'main';              -- Catalog name
DECLARE @schema STRING DEFAULT 'default';            -- Schema name
```

### 2. Run the Script
Execute the entire script (or sections as needed) in Databricks SQL editor or notebook.

### 3. Interpret Results
See the **Object Classification Table** below.

---

## Query Sections Explained

### Section 1: `information_schema.tables`
**What it does:** Shows the base `TABLE_TYPE` from Databricks metadata.

**Output columns:**
- `table_type`: Raw Databricks type (MANAGED TABLE, EXTERNAL TABLE, VIEW, MATERIALIZED_VIEW)
- `object_classification`: Human-readable classification

**What to look for:**
- **MANAGED TABLE** → Physical table (data stored in Databricks)
- **EXTERNAL TABLE** → Physical table (data stored in external location, e.g., UC Volume, S3, ADLS)
- **VIEW** → Logical view (no data stored; is a query)
- **MATERIALIZED_VIEW** → Pre-computed view (data is stored and refreshed)

---

### Section 2: `DESCRIBE FORMATTED`
**What it does:** Shows detailed table/view properties in human-readable format.

**Key properties to check:**
- **Type:** MANAGED TABLE | EXTERNAL TABLE | VIEW | MATERIALIZED VIEW
- **Location:** Indicates where data is stored (physical tables only)
- **Provider:** (e.g., DELTA, ICEBERG, CSV)
- **Table Properties:** May include Fabric-specific metadata (e.g., `fabric.metric.type`, `lake_house_id`)

**Example output for a physical table:**
```
Type                        MANAGED TABLE
Location                    dbfs:/user/hive/warehouse/sales_data
Provider                    DELTA
```

**Example output for a view:**
```
Type                        VIEW
View Text                   SELECT * FROM source_table WHERE region = 'US'
```

---

### Section 3: `sys.information_schema.tables`
**What it does:** Provides additional Databricks-specific metadata.

**Key columns:**
- `is_external`: 'true' for EXTERNAL, 'false' for MANAGED
- `num_rows`: Approximate row count
- `table_size_bytes`: Total size in bytes
- `num_files`: Number of underlying data files (helps identify if materialized)

---

### Section 4: `table_options`
**What it does:** Retrieves custom table properties set during creation.

**Important properties for Fabric sync:**
- `fabric_model_id`: Links to original Fabric semantic model
- `fabric_metric_type`: If present, indicates metric view
- `is_materialized_metric`: Flag for materialized metric views
- Any `_fabric_*` properties indicate Fabric origin

---

### Section 5: View Definition
**What it does:** For views, shows the underlying SQL query.

**Example:**
```
SELECT SUM(amount) AS total_revenue, date FROM transactions GROUP BY date
```

**Indicates:** This is a logical view computing revenue aggregates.

---

### Section 6: Column Metadata
**What it does:** Lists all columns with data types and nullability.

**Useful for:**
- Identifying computed columns (metrics often have these)
- Checking for hidden system columns (metric views may add `_metric_id`, etc.)

---

### Section 7: Materialized Views
**What it does:** Checks if the object is registered as a materialized view.

**Output:** Will show refresh schedule info if it exists.

---

### Section 8: `SHOW CREATE TABLE`
**What it does:** Displays the original CREATE statement.

**Run separately if needed:**
```sql
SHOW CREATE TABLE main.default.sales_data;
```

**Output reveals:**
- Whether it was created as `CREATE TABLE`, `CREATE VIEW`, `CREATE MATERIALIZED VIEW`
- Original column definitions
- Constraints and partitioning

---

### Section 9: Summary Classification (KEY SECTION)
**What it does:** Combines all checks into a single clear answer.

**Output columns:**
- `object_type`: Type classification
- `is_materialized`: Does it store pre-computed data?
- `notes`: Context about the object's nature

---

## Object Type Reference

| Object Type | Storage | Pre-computed? | Use Case | Fabric Origin |
|---|---|---|---|---|
| **Physical Table (Managed)** | Databricks (DBFS) | Yes | Full data copy from Fabric | Most common; synced fact/dimension tables |
| **Physical Table (External)** | External storage (UC, S3, ADLS) | Yes | Large tables; lakehouse sync | Some configurations use external volumes |
| **Standard View** | None (logical) | No | Transformations; joins; aggregations | DAX measures converted to views; filters |
| **Materialized View** | Databricks (DELTA) | Yes | Pre-computed aggregates; metrics | Fabric Metric Views or pre-computed measures |

---

## Examples

### Example 1: Checking a Synced Fact Table

**Setup:**
```sql
DECLARE @object_name STRING DEFAULT 'orders';
DECLARE @catalog STRING DEFAULT 'main';
DECLARE @schema STRING DEFAULT 'fabric_warehouse';
```

**Expected Result:**
```
object_type: Physical Table (Managed)
is_materialized: Yes - Physical Tables store actual data
notes: Physical Table for operational data
```

**Interpretation:**
- ✅ Data was successfully synced from Fabric as a full table.
- It's a working production table ready for queries.

---

### Example 2: Checking a Synced Measure as a View

**Setup:**
```sql
DECLARE @object_name STRING DEFAULT 'total_revenue';
DECLARE @catalog STRING DEFAULT 'main';
DECLARE @schema STRING DEFAULT 'fabric_warehouse';
```

**Expected Result:**
```
object_type: Standard View (Logical)
is_materialized: No - Standard Views are logical queries
notes: Physical Table for operational data (check definition)
view_definition: WITH aggregated AS (SELECT date, SUM(amount) FROM orders ...) SELECT * FROM aggregated
```

**Interpretation:**
- The measure was converted to a SQL view in Databricks.
- It's a logical query; execution depends on underlying tables.
- Check the `view_definition` to understand the measure logic.

---

### Example 3: Checking a Metric View

**Setup:**
```sql
DECLARE @object_name STRING DEFAULT 'revenue_by_region';
DECLARE @catalog STRING DEFAULT 'main';
DECLARE @schema STRING DEFAULT 'fabric_warehouse';
```

**Expected Result (if it's a Materialized Metric View):**
```
object_type: Materialized View
is_materialized: Yes - Metric/Materialized Views are pre-computed
notes: Materialized View (from Fabric Metric)
Table Properties:
  fabric_metric_type: METRIC_VIEW
  is_materialized: true
  refresh_schedule: EVERY 24 HOURS
```

**Interpretation:**
- ✅ This is a Fabric metric view synced as a materialized view.
- Data is pre-computed and refreshed on schedule.
- Queries are fast because aggregates are already calculated.

---

## Troubleshooting

### Object Not Found
**Error:** `Could not find table/view`

**Solution:**
- Verify catalog and schema names are correct: `SELECT DISTINCT table_schema FROM information_schema.tables WHERE table_schema LIKE '%fabric%'`
- Check if the object was actually synced: `SHOW OBJECTS IN main.fabric_warehouse`

### Unexpected VIEW Instead of TABLE
**Possible causes:**
1. Fabric measure was synced as SQL view (expected if it's an aggregate)
2. Sink configuration specified view creation
3. Object is a bridge/transformation view, not raw data

**Action:**
- Check the `view_definition` to understand what it queries
- Consider materializing it if query performance is poor: `CREATE TABLE table_name AS SELECT * FROM view_name`

### Materialized View Not Materialized
**Symptom:** `is_materialized = No` but you expected yes

**Possible causes:**
1. View was recently created; materialization scheduled for later
2. Sync was configured to create logical views only
3. Fabric metric was too complex to materialize

**Action:**
- Check `DESCRIBE FORMATTED` for `Location` (physical tables have locations)
- Manually materialize if needed: `CREATE MATERIALIZED VIEW ... REFRESH ON DEMAND` followed by `REFRESH MATERIALIZED VIEW`

### Missing Fabric Metadata Properties
**Symptom:** No `fabric_*` properties in table_options

**Possible causes:**
1. semabridge sync didn't preserve metadata flags
2. Properties were stripped during sync
3. Object is a new/derived view, not original Fabric object

**Action:**
- Check semabridge logs for sync errors
- Verify the source semantic model in Fabric still exists
- Re-run semabridge sync with `--preserve-metadata` or similar flag

---

## Fabric-to-Databricks Sync Expectations

### What semabridge Typically Creates:

1. **Fact/Dimension Tables** → MANAGED TABLEs (physical data)
2. **Measures (simple aggregates)** → MATERIALIZED VIEWs (pre-computed)
3. **Complex Measures** → Standard VIEWs (may use cross-references)
4. **Calculated Columns** → VIEWS or additional TABLE columns
5. **Hierarchies** → VIEWS with sorting/grouping logic

### Performance Implications:

- **Physical Tables:** Fast for scans and joins; data fully copied
- **Materialized Views:** Fast for aggregations; data pre-computed
- **Standard Views:** Fast only if underlying tables are optimized; execution depends on source freshness

---

## Performance Tips

1. **For slow queries on VIEWs:**
   - Materialize: `CREATE TABLE materialized_revenue AS SELECT * FROM total_revenue`
   - Monitor refresh: `ANALYZE TABLE materialized_revenue COMPUTE STATISTICS`

2. **For large Physical Tables:**
   - Partition by date or region for faster filtering
   - Use clustering: `CLUSTER BY region, date`

3. **For Metric Views:**
   - Check refresh schedule: `DESCRIBE EXTENDED metric_view_name`
   - Manually refresh if needed: `REFRESH MATERIALIZED VIEW metric_view_name`

---

## See Also

- [Databricks SQL Reference: information_schema](https://docs.databricks.com/en/sql/language-manual/information-schema.html)
- [Databricks Materialized Views Documentation](https://docs.databricks.com/en/sql/language-manual/sql-ref-materialized-view.html)
- semabridge Project Docs: [MeasurePipelineGuide.md](../Docs/Usage/MeasurePipelineGuide.md)
