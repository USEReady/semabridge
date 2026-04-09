-- Databricks join-key repair script for semabridge.public
-- Use this when combined metric view deployment fails with UNRESOLVED_COLUMN
-- due to missing join columns in Fact or BU.

USE CATALOG semabridge;
USE SCHEMA public;

-- ---------------------------------------------------------------------------
-- Fact: ensure join-key columns exist
-- ---------------------------------------------------------------------------
ALTER TABLE semabridge.public.fact ADD COLUMNS (
  `YearPeriod` STRING COMMENT 'Join key used for calendar relationships',
  `Customer_Key` STRING COMMENT 'Join key used for customer relationships',
  `Scenario_Key` STRING COMMENT 'Join key used for scenario relationships',
  `Product_Key` STRING COMMENT 'Join key used for product relationships'
);

-- ---------------------------------------------------------------------------
-- BU: Create BU_Key as an alias for the existing BU column
-- This handles cases where the relationship expects BU_Key but the source
-- table only has BU or Division
-- ---------------------------------------------------------------------------
-- Note: Databricks currently has BU and Division columns, not BU_Key
-- We create BU_Key by using an ALTER TABLE to add it as a copy
-- Option 1: If BU exists and should be the key, add BU_Key as a clone
ALTER TABLE semabridge.public.bu ADD COLUMNS (
  `BU_Key` STRING COMMENT 'Join key used for BU relationships (mapped from BU column)'
);

-- Then update BU_Key to match BU values (run this after adding the column)
-- UPDATE semabridge.public.bu SET `BU_Key` = `BU` WHERE `BU_Key` IS NULL;

-- ---------------------------------------------------------------------------
-- Optional rename statements if legacy columns exist under different names.
-- Uncomment only the ones that match the current table schema.
-- ---------------------------------------------------------------------------
-- ALTER TABLE semabridge.public.fact RENAME COLUMN `Customer Key` TO `Customer_Key`;
-- ALTER TABLE semabridge.public.fact RENAME COLUMN `Scenario Key` TO `Scenario_Key`;
-- ALTER TABLE semabridge.public.fact RENAME COLUMN `Product Key` TO `Product_Key`;
-- ALTER TABLE semabridge.public.bu RENAME COLUMN `BU Key` TO `BU_Key`;

-- ---------------------------------------------------------------------------
-- Validation: verify the columns are present
-- ---------------------------------------------------------------------------
DESCRIBE TABLE semabridge.public.fact;
DESCRIBE TABLE semabridge.public.bu;

-- If you want a compact schema-only check, run these as well:
-- DESCRIBE TABLE EXTENDED semabridge.public.fact;
-- DESCRIBE TABLE EXTENDED semabridge.public.bu;