-- Databricks join-key repair script for semabridge.public
-- Use this when combined metric view deployment fails with UNRESOLVED_COLUMN.
--
-- This script is tuned for environments where Fact/BU already have key columns
-- with capitalized names (for example Customer_Key, BU_Key) and Customer is
-- missing a usable join key.

USE CATALOG semabridge;
USE SCHEMA public;

-- ---------------------------------------------------------------------------
-- 1) Required hotfix: add Customer join key on Customer table
-- ---------------------------------------------------------------------------
-- Run this first. It is the minimum required fix for Fact -> Customer joins.
ALTER TABLE semabridge.public.customer ADD COLUMN IF NOT EXISTS customer_key STRING
COMMENT 'Join key used for Fact.customer_key -> Customer.customer_key relationship';

-- Optional backfill choices (uncomment only one that matches your source schema):
-- 1) If Customer table has numeric/string business key column named customer
-- UPDATE semabridge.public.customer
-- SET customer_key = CAST(customer AS STRING)
-- WHERE customer_key IS NULL;

-- 2) If Customer table has identifier column named id
-- UPDATE semabridge.public.customer
-- SET customer_key = CAST(id AS STRING)
-- WHERE customer_key IS NULL;

-- 3) If Customer keys come from an external conformed dimension table
-- UPDATE semabridge.public.customer c
-- SET customer_key = x.customer_key
-- FROM semabridge.public.customer_key_xref x
-- WHERE c.name = x.customer_name
--   AND c.customer_key IS NULL;

-- ---------------------------------------------------------------------------
-- 2) Optional casing bridge for Fact/BU keys
-- ---------------------------------------------------------------------------
-- Use this section only if your generated SQL expects lowercase keys while
-- your physical tables store capitalized key names.

ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS customer_key STRING
COMMENT 'Lowercase key alias for Customer_Key';
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS product_key STRING
COMMENT 'Lowercase key alias for Product_Key';
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS scenario_key STRING
COMMENT 'Lowercase key alias for Scenario_Key';
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS bu_key STRING
COMMENT 'Lowercase key alias for BU_Key';

-- Backfill lowercase aliases from existing capitalized keys.
UPDATE semabridge.public.fact
SET
	customer_key = COALESCE(customer_key, CAST(Customer_Key AS STRING)),
	product_key = COALESCE(product_key, CAST(Product_Key AS STRING)),
	scenario_key = COALESCE(scenario_key, CAST(Scenario_Key AS STRING)),
	bu_key = COALESCE(bu_key, CAST(BU_Key AS STRING))
WHERE customer_key IS NULL OR product_key IS NULL OR scenario_key IS NULL OR bu_key IS NULL;

ALTER TABLE semabridge.public.bu ADD COLUMN IF NOT EXISTS bu_key STRING
COMMENT 'Lowercase key alias for BU_Key';

UPDATE semabridge.public.bu
SET bu_key = COALESCE(bu_key, CAST(BU_Key AS STRING), CAST(BU AS STRING))
WHERE bu_key IS NULL;

-- ---------------------------------------------------------------------------
-- 3) Optional rename statements to standardize physical casing
-- ---------------------------------------------------------------------------
-- ALTER TABLE semabridge.public.fact RENAME COLUMN YearPeriod TO yearperiod;
-- ALTER TABLE semabridge.public.fact RENAME COLUMN Customer_Key TO customer_key;
-- ALTER TABLE semabridge.public.fact RENAME COLUMN Scenario_Key TO scenario_key;
-- ALTER TABLE semabridge.public.fact RENAME COLUMN Product_Key TO product_key;
-- ALTER TABLE semabridge.public.fact RENAME COLUMN BU_Key TO bu_key;
-- ALTER TABLE semabridge.public.bu RENAME COLUMN BU_Key TO bu_key;

-- ---------------------------------------------------------------------------
-- 4) Validation: verify key columns are present
-- ---------------------------------------------------------------------------
DESCRIBE TABLE semabridge.public.fact;
DESCRIBE TABLE semabridge.public.bu;
DESCRIBE TABLE semabridge.public.customer;

-- Compact schema-only check
SELECT table_name, column_name, data_type
FROM semabridge.information_schema.columns
WHERE table_schema = 'public'
	AND (
		(table_name = 'fact' AND column_name IN ('Customer_Key', 'Product_Key', 'BU_Key', 'Scenario_Key', 'yearperiod', 'customer_key', 'product_key', 'bu_key', 'scenario_key'))
		OR (table_name = 'bu' AND column_name IN ('BU_Key', 'bu_key'))
		OR (table_name = 'customer' AND column_name IN ('customer_key', 'customer', 'id', 'name'))
	)
ORDER BY table_name, column_name;

-- If you want extended schema details, run these as well:
-- DESCRIBE TABLE EXTENDED semabridge.public.fact;
-- DESCRIBE TABLE EXTENDED semabridge.public.bu;
-- DESCRIBE TABLE EXTENDED semabridge.public.customer;