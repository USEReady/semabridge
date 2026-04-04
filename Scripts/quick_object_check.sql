-- Quick Reference: Databricks Object Type Checker
-- Copy-paste the section you need and modify CATALOG, SCHEMA, and OBJECT_NAME

-- ============================================================================
-- QUICK CHECK 1: One-liner classification (fastest)
-- ============================================================================
-- Run this first for instant classification
SELECT
  table_name,
  table_type,
  CASE
    WHEN table_type = 'MATERIALIZED_VIEW' THEN '📊 Metric/Materialized View'
    WHEN table_type = 'VIEW' THEN '📝 Standard View (Logical)'
    WHEN table_type = 'EXTERNAL TABLE' THEN '📦 Physical Table (External)'
    WHEN table_type = 'MANAGED TABLE' THEN '💾 Physical Table (Managed)'
    ELSE table_type
  END AS classification
FROM information_schema.tables
WHERE table_catalog = 'main'
  AND table_schema = 'default'  -- Change to your schema
  AND table_name = 'sales_data'; -- Change to your object name

-- ============================================================================
-- QUICK CHECK 2: Table with size and row count
-- ============================================================================
-- Shows size, row count, and storage type
SELECT
  tbl_name,
  CASE WHEN is_external = 'true' THEN 'External' ELSE 'Managed' END AS storage_type,
  CONCAT(ROUND(table_size_bytes / 1024 / 1024 / 1024, 2), ' GB') AS size_gb,
  num_rows,
  num_files,
  owner
FROM sys.information_schema.tables
WHERE catalog_name = 'main'
  AND schema_name = 'default'  -- Change to your schema
  AND tbl_name = 'sales_data'; -- Change to your object name

-- ============================================================================
-- QUICK CHECK 3: If it's a view, show the query
-- ============================================================================
-- Only works for views; shows the underlying SQL
SELECT view_definition
FROM information_schema.views
WHERE table_catalog = 'main'
  AND table_schema = 'default'   -- Change to your schema
  AND table_name = 'sales_data'; -- Change to your object name

-- ============================================================================
-- QUICK CHECK 4: Find all objects of a specific type in a schema
-- ============================================================================
-- List all physical tables
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_catalog = 'main'
  AND table_schema = 'default'  -- Change to your schema
  AND table_type IN ('MANAGED TABLE', 'EXTERNAL TABLE')
ORDER BY table_name;

-- List all views and materialized views
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_catalog = 'main'
  AND table_schema = 'default'  -- Change to your schema
  AND table_type IN ('VIEW', 'MATERIALIZED_VIEW')
ORDER BY table_name;

-- ============================================================================
-- QUICK CHECK 5: Check for Fabric-specific properties
-- ============================================================================
-- Shows custom properties (includes Fabric metadata)
SELECT
  prop_name,
  prop_value
FROM sys.information_schema.table_options
WHERE catalog_name = 'main'
  AND schema_name = 'default'    -- Change to your schema
  AND tbl_name = 'sales_data'    -- Change to your object name
ORDER BY prop_name;

-- ============================================================================
-- QUICK CHECK 6: Detailed DESCRIBE (copy-paste and run)
-- ============================================================================
-- Uncomment and run separately:
-- DESCRIBE FORMATTED main.default.sales_data;

-- ============================================================================
-- QUICK CHECK 7: Find object creation info
-- ============================================================================
-- Shows who created it and when
SELECT
  table_name,
  table_type,
  creator,
  created_at,
  last_modified_at
FROM sys.information_schema.tables
WHERE catalog_name = 'main'
  AND schema_name = 'default'    -- Change to your schema
  AND tbl_name = 'sales_data';   -- Change to your object name

-- ============================================================================
-- QUICK CHECK 8: Materialized View Refresh Schedule (if applicable)
-- ============================================================================
-- Only if object is a materialized view
SELECT *
FROM sys.information_schema.materialized_views
WHERE catalog_name = 'main'
  AND schema_name = 'default'    -- Change to your schema
  AND name = 'sales_data';       -- Change to your object name

-- ============================================================================
-- BATCH CHECK: All objects in a schema with their types
-- ============================================================================
-- Shows everything in a schema for overview
SELECT
  table_name,
  table_type,
  CASE
    WHEN table_type = 'MATERIALIZED_VIEW' THEN 'Metric/Materialized'
    WHEN table_type = 'VIEW' THEN 'Logical View'
    WHEN table_type = 'EXTERNAL TABLE' THEN 'Physical (External)'
    WHEN table_type = 'MANAGED TABLE' THEN 'Physical (Managed)'
  END AS class,
  ROW_NUMBER() OVER (PARTITION BY table_type ORDER BY table_name) AS seq
FROM information_schema.tables
WHERE table_catalog = 'main'
  AND table_schema = 'default'   -- Change to your schema
ORDER BY table_type, table_name;
