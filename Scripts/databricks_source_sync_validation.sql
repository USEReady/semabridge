-- Databricks source-sync validation for semabridge.public
-- Use this to confirm the base tables exist, inspect the exact schema,
-- and review the Customer Profitability metric-view definition.

USE CATALOG semabridge;
USE SCHEMA public;

-- ---------------------------------------------------------------------------
-- 1) Verify base tables exist
-- ---------------------------------------------------------------------------
SHOW TABLES IN semabridge.public LIKE 'fact';
SHOW TABLES IN semabridge.public LIKE 'bu';
SHOW TABLES IN semabridge.public LIKE 'customer';

-- Optional broader existence checks
SELECT table_catalog, table_schema, table_name, table_type
FROM semabridge.information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('fact', 'bu', 'customer')
ORDER BY table_name;

-- ---------------------------------------------------------------------------
-- 2) Inspect exact schema in Databricks
-- ---------------------------------------------------------------------------
DESCRIBE TABLE semabridge.public.fact;
DESCRIBE TABLE semabridge.public.bu;
DESCRIBE TABLE semabridge.public.customer;

-- Compact schema-only check
SELECT table_name, column_name, data_type, is_nullable, ordinal_position
FROM semabridge.information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('fact', 'bu', 'customer')
ORDER BY table_name, ordinal_position;

-- ---------------------------------------------------------------------------
-- 3) Check for the exact join columns needed by the publisher
-- ---------------------------------------------------------------------------
SELECT
  table_name,
  column_name,
  data_type
FROM semabridge.information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'fact'
  AND column_name IN ('yearperiod', 'Customer_Key', 'Product_Key', 'BU_Key', 'Scenario_Key', 'customer_key', 'product_key', 'bu_key', 'scenario_key')
ORDER BY column_name;

SELECT
  table_name,
  column_name,
  data_type
FROM semabridge.information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'bu'
  AND column_name IN ('BU', 'Division', 'BU_Key', 'bu_key')
ORDER BY column_name;

SELECT
  table_name,
  column_name,
  data_type
FROM semabridge.information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'customer'
  AND column_name IN ('customer_key', 'customer', 'id', 'name')
ORDER BY column_name;

-- ---------------------------------------------------------------------------
-- 4) Review the generated view definition
-- ---------------------------------------------------------------------------
SELECT
  table_catalog,
  table_schema,
  table_name,
  view_definition
FROM semabridge.information_schema.views
WHERE table_schema = 'public'
  AND table_name = 'mv_Customer_Profitability';

-- If you want the full DDL instead, run this separately:
-- SHOW CREATE TABLE semabridge.public.mv_Customer_Profitability;
