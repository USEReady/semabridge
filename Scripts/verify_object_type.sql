-- Databricks SQL Script to Verify Object Type and Metadata
-- Useful for verifying Physical Tables, Standard Views, and Metric Views from Fabric sync
-- Usage: Modify @object_name and @catalog/@schema as needed

-- Parameters (modify these values)
DECLARE @object_name STRING DEFAULT 'sales_data';
DECLARE @catalog STRING DEFAULT 'main';
DECLARE @schema STRING DEFAULT 'default';

-- ============================================================================
-- 1. Query information_schema.tables for basic object type
-- ============================================================================
SELECT
  table_catalog AS catalog_name,
  table_schema AS schema_name,
  table_name,
  table_type,
  CASE
    WHEN table_type = 'EXTERNAL TABLE' THEN 'Physical Table (External)'
    WHEN table_type = 'MANAGED TABLE' THEN 'Physical Table (Managed)'
    WHEN table_type = 'VIEW' THEN 'Standard View'
    WHEN table_type = 'MATERIALIZED_VIEW' THEN 'Materialized View'
    ELSE table_type
  END AS object_classification
FROM information_schema.tables
WHERE
  table_catalog = @catalog
  AND table_schema = @schema
  AND table_name = @object_name;

-- ============================================================================
-- 2. Get detailed table/view properties and metadata
-- ============================================================================
DESCRIBE FORMATTED ${catalog}.${schema}.${object_name};

-- ============================================================================
-- 3. Check for metrics-related properties and table owner
-- ============================================================================
SELECT
  tbl_name AS table_name,
  owner,
  CAST(creation_time AS STRING) AS created_at,
  CAST(last_access_time AS STRING) AS last_accessed_at,
  CAST(table_size_bytes AS BIGINT) AS size_bytes,
  num_rows,
  num_files,
  CASE
    WHEN is_external = 'true' THEN 'External (Physical)'
    WHEN is_external = 'false' THEN 'Managed (Physical)'
    ELSE 'Unknown'
  END AS storage_type
FROM sys.information_schema.tables
WHERE
  catalog_name = @catalog
  AND schema_name = @schema
  AND table_name = @object_name;

-- ============================================================================
-- 4. Check Table Properties for Metric View indicators
-- ============================================================================
-- Metric Views and special properties from Fabric may be stored in table properties
SELECT
  tbl_name,
  prop_name,
  prop_value
FROM sys.information_schema.table_options
WHERE
  catalog_name = @catalog
  AND schema_name = @schema
  AND table_name = @object_name
ORDER BY prop_name;

-- ============================================================================
-- 5. Check Query Definition (if it's a view)
-- ============================================================================
-- This only works for views; will error gracefully if object is a table
SELECT
  view_definition
FROM information_schema.views
WHERE
  table_catalog = @catalog
  AND table_schema = @schema
  AND table_name = @object_name;

-- ============================================================================
-- 6. Get Column-Level Metadata
-- ============================================================================
SELECT
  table_name,
  column_name,
  data_type,
  is_nullable,
  ordinal_position,
  column_comment
FROM information_schema.columns
WHERE
  table_catalog = @catalog
  AND table_schema = @schema
  AND table_name = @object_name
ORDER BY ordinal_position;

-- ============================================================================
-- 7. Check if object is a Materialized View with Refresh Schedule
-- ============================================================================
-- Materialized Views may have refresh information
SELECT
  *
FROM sys.information_schema.materialized_views
WHERE
  catalog_name = @catalog
  AND schema_name = @schema
  AND name = @object_name;

-- ============================================================================
-- 8. Alternative: Use SHOW CREATE TABLE for DDL inspection
-- ============================================================================
-- This will show the CREATE statement, revealing the object type
-- Uncomment the line below and run separately if needed:
-- SHOW CREATE TABLE ${catalog}.${schema}.${object_name};

-- ============================================================================
-- 9. Summary Check: Classification Logic
-- ============================================================================
-- Run this final query to get a clear classification
SELECT
  COALESCE(t.table_name, v.table_name, m.name) AS object_name,
  CASE
    WHEN m.name IS NOT NULL THEN 'Materialized View'
    WHEN v.table_name IS NOT NULL THEN 'Standard View (Logical)'
    WHEN t.table_type = 'EXTERNAL TABLE' THEN 'Physical Table (External/Synced)'
    WHEN t.table_type = 'MANAGED TABLE' THEN 'Physical Table (Managed)'
    ELSE 'Unknown'
  END AS object_type,
  CASE
    WHEN m.name IS NOT NULL THEN 'Yes - Metric/Materialized Views are pre-computed'
    WHEN v.table_name IS NOT NULL THEN 'No - Standard Views are logical queries'
    WHEN t.table_type IN ('EXTERNAL TABLE', 'MANAGED TABLE') THEN 'Yes - Physical Tables store actual data'
    ELSE 'Unable to determine'
  END AS is_materialized,
  CASE
    WHEN m.name IS NOT NULL THEN 'Materialized View (from Fabric Metric)'
    WHEN v.table_name IS NOT NULL AND v.view_definition LIKE '%METRIC%' THEN 'Possible Metric View (check definition)'
    WHEN t.table_type IN ('EXTERNAL TABLE', 'MANAGED TABLE') THEN 'Physical Table for operational data'
    ELSE 'Verify table properties manually'
  END AS notes
FROM information_schema.tables t
FULL OUTER JOIN information_schema.views v
  ON t.table_catalog = v.table_catalog
  AND t.table_schema = v.table_schema
  AND t.table_name = v.table_name
FULL OUTER JOIN sys.information_schema.materialized_views m
  ON COALESCE(t.table_catalog, v.table_catalog) = m.catalog_name
  AND COALESCE(t.table_schema, v.table_schema) = m.schema_name
  AND COALESCE(t.table_name, v.table_name) = m.name
WHERE
  (t.table_catalog = @catalog AND t.table_schema = @schema AND t.table_name = @object_name)
  OR (v.table_catalog = @catalog AND v.table_schema = @schema AND v.table_name = @object_name)
  OR (m.catalog_name = @catalog AND m.schema_name = @schema AND m.name = @object_name);
