-- Databricks Object Type Verification for Fabric Sync
-- Specifically for semabridge Fabric-to-Databricks pipeline
-- See: Docs/Usage/MeasurePipelineGuide.md for more context

-- ============================================================================
-- FABRIC-TO-DATABRICKS OBJECT MAPPING
-- ============================================================================
-- This script helps verify what was created for each Fabric object type:
--
-- Fabric Object Type          → Databricks Object Type
-- ─────────────────────────────────────────────────────
-- Dimension Table             → MANAGED TABLE
-- Fact Table                  → MANAGED TABLE
-- Measure (Aggregate)         → MATERIALIZED VIEW
-- Measure (Complex/DAX)       → VIEW (Logical)
-- Calculated Column           → VIEW or TABLE column
-- Hierarchy                   → VIEW (sorted/grouped)
-- Role-playing Dimension      → Multiple VIEWs (aliases)
-- Semantic Relationship       → FK constraints or JOIN logic
-- ============================================================================

-- ============================================================================
-- Check 1: Verify Tables (Facts & Dimensions)
-- ============================================================================
-- Facts and Dimensions typically become MANAGED TABLES
-- Run this to confirm raw data was synced successfully

SELECT
  tbl_name,
  CASE WHEN is_external = 'true' THEN 'External' ELSE 'Managed' END AS table_type,
  CONCAT(ROUND(table_size_bytes / 1024 / 1024, 2), ' MB') AS size_mb,
  num_rows,
  CAST(last_alter_time AS DATE) AS last_synced
FROM sys.information_schema.tables
WHERE catalog_name = 'main'
  AND schema_name = 'fabric_warehouse'        -- Change to your schema
  AND tbl_name NOT LIKE '%_view'              -- Exclude views
ORDER BY last_alter_time DESC
LIMIT 20;

-- ============================================================================
-- Check 2: Verify Measures (Aggregates & Metrics)
-- ============================================================================
-- Measures from Fabric become either:
-- - MATERIALIZED VIEWs (pre-computed; fast aggregates)
-- - VIEWs (logical; computed on query)
--
-- This query shows measure views and their source tables

WITH measure_views AS (
  SELECT
    table_name,
    table_type,
    CASE
      WHEN table_type = 'MATERIALIZED_VIEW' THEN 'Pre-computed (Fast)'
      WHEN table_type = 'VIEW' THEN 'Logical (Computed on Query)'
      ELSE table_type
    END AS measure_type
  FROM information_schema.tables
  WHERE table_catalog = 'main'
    AND table_schema = 'fabric_warehouse'     -- Change to your schema
    AND table_type IN ('VIEW', 'MATERIALIZED_VIEW')
)
SELECT
  table_name,
  measure_type,
  table_type
FROM measure_views
ORDER BY table_name;

-- ============================================================================
-- Check 3: Check Measure Definitions (which base tables they reference)
-- ============================================================================
-- For VIEW-based measures, see the underlying query
-- For MATERIALIZED_VIEW-based measures, check refresh schedule

SELECT
  v.table_name AS measure_name,
  SUBSTRING(v.view_definition, 1, 150) AS query_preview,
  LENGTH(v.view_definition) AS query_length
FROM information_schema.views v
WHERE v.table_catalog = 'main'
  AND v.table_schema = 'fabric_warehouse'    -- Change to your schema
  AND v.table_name LIKE '%revenue%'           -- Search for measures with pattern
ORDER BY v.table_name;

-- ============================================================================
-- Check 4: Identify Materialized Metrics with Refresh Info
-- ============================================================================
-- Shows which measures are materialized and their refresh schedules

SELECT
  m.name AS materialized_metric_name,
  m.catalog_name,
  m.schema_name,
  COALESCE(m.refresh_schedule, 'On Demand') AS refresh_mode,
  CAST(m.created_at AS DATE) AS created_date
FROM sys.information_schema.materialized_views m
WHERE m.catalog_name = 'main'
  AND m.schema_name = 'fabric_warehouse'     -- Change to your schema
ORDER BY m.created_at DESC;

-- ============================================================================
-- Check 5: Verify Data Completeness
-- ============================================================================
-- Summary of what was synced from Fabric
-- Shows table/view counts and total rows

WITH object_stats AS (
  SELECT
    table_type,
    COUNT(*) AS count,
    SUM(num_rows) AS total_rows
  FROM sys.information_schema.tables
  WHERE catalog_name = 'main'
    AND schema_name = 'fabric_warehouse'    -- Change to your schema
  GROUP BY table_type
)
SELECT
  table_type,
  count,
  total_rows,
  CASE
    WHEN table_type = 'MANAGED TABLE' THEN 'Fact/Dimension Tables (Physical Data)'
    WHEN table_type = 'EXTERNAL TABLE' THEN 'External Tables (UC/Volumes)'
    WHEN table_type = 'VIEW' THEN 'Logical Measures (Computed)'
    WHEN table_type = 'MATERIALIZED_VIEW' THEN 'Materialized Measures (Pre-computed)'
    ELSE table_type
  END AS object_classification
FROM object_stats
ORDER BY count DESC;

-- ============================================================================
-- Check 6: Find Objects Missing Metadata
-- ============================================================================
-- Checks if semabridge preserved Fabric metadata properties
-- Missing properties might indicate partial sync

SELECT
  tbl_name,
  COUNT(prop_name) AS property_count,
  COLLECT_LIST(prop_name) AS properties
FROM sys.information_schema.table_options
WHERE catalog_name = 'main'
  AND schema_name = 'fabric_warehouse'      -- Change to your schema
GROUP BY tbl_name
HAVING COUNT(prop_name) < 3  -- Tables with few properties (might be incomplete)
ORDER BY property_count ASC;

-- ============================================================================
-- Check 7: Verify Relationships (Foreign Keys)
-- ============================================================================
-- Fabric relationships map to FK constraints (if enabled)
-- Shows defined constraints and relationships

SELECT
  constraint_name,
  table_name,
  column_name,
  ordinal_position
FROM information_schema.constraint_column_usage
WHERE table_catalog = 'main'
  AND table_schema = 'fabric_warehouse'     -- Change to your schema
ORDER BY table_name, ordinal_position;

-- ============================================================================
-- Check 8: Detect Sync Issues
-- ============================================================================
-- Identifies potential problems in the Fabric→Databricks sync:
-- - Missing tables (not in information_schema)
-- - Empty tables (0 rows)
-- - Missing views (expected measures not created)
-- - Misclassified objects

SELECT
  tbl_name,
  CASE
    WHEN num_rows IS NULL THEN '⚠️  Unknown row count'
    WHEN num_rows = 0 THEN '⚠️  Empty table'
    WHEN num_rows < 100 THEN '⚠️  Small table (possible issue)'
    ELSE '✅ OK'
  END AS status,
  num_rows,
  CASE
    WHEN is_external = 'true' THEN 'External'
    ELSE 'Managed'
  END AS storage
FROM sys.information_schema.tables
WHERE catalog_name = 'main'
  AND schema_name = 'fabric_warehouse'      -- Change to your schema
ORDER BY
  CASE
    WHEN num_rows IS NULL THEN 1
    WHEN num_rows = 0 THEN 2
    WHEN num_rows < 100 THEN 3
    ELSE 4
  END;

-- ============================================================================
-- Check 9: Verify Calculated Columns
-- ============================================================================
-- Shows computed/derived columns (from Fabric calculated columns or DAX)
-- These may have special naming patterns or metadata

SELECT
  table_name,
  column_name,
  data_type,
  column_comment
FROM information_schema.columns
WHERE table_catalog = 'main'
  AND table_schema = 'fabric_warehouse'     -- Change to your schema
  AND (
    column_comment LIKE '%calculated%'
    OR column_comment LIKE '%DAX%'
    OR column_comment LIKE '%measure%'
    OR column_name LIKE '%_calc%'
  )
ORDER BY table_name, ordinal_position;

-- ============================================================================
-- Check 10: Post-Sync Validation Checklist
-- ============================================================================
-- Run this comprehensive validation after Fabric sync completes

WITH table_summary AS (
  SELECT
    'Total Objects' AS metric,
    COUNT(*)::STRING AS value
  FROM sys.information_schema.tables
  WHERE catalog_name = 'main'
    AND schema_name = 'fabric_warehouse'    -- Change to your schema

  UNION ALL

  SELECT
    'Physical Tables',
    COUNT(*)::STRING
  FROM sys.information_schema.tables
  WHERE catalog_name = 'main'
    AND schema_name = 'fabric_warehouse'    -- Change to your schema
    AND tbl_type IN ('MANAGED TABLE', 'EXTERNAL TABLE')

  UNION ALL

  SELECT
    'Logical Views',
    COUNT(*)::STRING
  FROM sys.information_schema.tables
  WHERE catalog_name = 'main'
    AND schema_name = 'fabric_warehouse'    -- Change to your schema
    AND tbl_type = 'VIEW'

  UNION ALL

  SELECT
    'Materialized Metrics',
    COUNT(*)::STRING
  FROM sys.information_schema.tables
  WHERE catalog_name = 'main'
    AND schema_name = 'fabric_warehouse'    -- Change to your schema
    AND tbl_type = 'MATERIALIZED_VIEW'

  UNION ALL

  SELECT
    'Total Rows (All Tables)',
    SUM(num_rows)::STRING
  FROM sys.information_schema.tables
  WHERE catalog_name = 'main'
    AND schema_name = 'fabric_warehouse'    -- Change to your schema
    AND tbl_type IN ('MANAGED TABLE', 'EXTERNAL TABLE')
)
SELECT * FROM table_summary;

-- ============================================================================
-- How to Use These Queries
-- ============================================================================
-- 1. Replace 'fabric_warehouse' with your actual schema name
-- 2. Run individual CHECKs as needed, or run all for full validation
-- 3. Use results to verify:
--    ✅ All expected tables were synced
--    ✅ All measures became views or materialized views
--    ✅ Data volumes are reasonable (not 0 rows)
--    ✅ Metadata properties are preserved
--    ✅ Relationships/FKs are defined (if supported)
-- 4. If issues found, check:
--    - semabridge logs: Scripts/debug_settings.py or Config/semabridge.yaml
--    - Fabric semantic model availability and permissions
--    - Databricks workspace connectivity and table access
