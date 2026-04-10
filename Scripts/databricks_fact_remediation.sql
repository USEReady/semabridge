-- Databricks Fact schema remediation for Customer Profitability
-- Purpose:
--   1) Add required financial/time columns to semabridge.public.fact if missing.
--   2) Provide a deterministic place to backfill values from upstream source tables.
--
-- IMPORTANT:
-- - This script only creates columns and includes TODO backfill stubs.
-- - You must replace the UPDATE section with your real upstream join/select logic.

USE CATALOG semabridge;
USE SCHEMA public;

-- 1) Inspect current shape
DESCRIBE TABLE semabridge.public.fact;

-- 2) Add missing columns required by semantic model
--    Databricks supports IF NOT EXISTS for ADD COLUMN.
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS revenue DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS material_costs DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS labor_costs_variable DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS taxes DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS rev_for_exp_travel DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS travel_expenses DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS cost_third_party DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS subscription_revenue DOUBLE;
ALTER TABLE semabridge.public.fact ADD COLUMN IF NOT EXISTS yearperiod STRING;

-- 3) TODO: Replace this block with real upstream backfill logic.
--    Example shape only (DO NOT RUN as-is for production):
-- UPDATE semabridge.public.fact f
-- SET
--   revenue = src.revenue,
--   material_costs = src.material_costs,
--   labor_costs_variable = src.labor_costs_variable,
--   taxes = src.taxes,
--   rev_for_exp_travel = src.rev_for_exp_travel,
--   travel_expenses = src.travel_expenses,
--   cost_third_party = src.cost_third_party,
--   subscription_revenue = src.subscription_revenue,
--   yearperiod = src.yearperiod
-- FROM semabridge.public.fact_source_enriched src
-- WHERE f.customer_key = src.customer_key
--   AND f.product_key = src.product_key
--   AND f.bu_key = src.bu_key
--   AND f.scenario_key = src.scenario_key;

-- 4) Validate required columns exist
SELECT column_name, data_type
FROM semabridge.information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'fact'
  AND column_name IN (
    'customer_key', 'product_key', 'bu_key', 'scenario_key',
    'revenue', 'material_costs', 'labor_costs_variable', 'taxes',
    'rev_for_exp_travel', 'travel_expenses', 'cost_third_party',
    'subscription_revenue', 'yearperiod'
  )
ORDER BY column_name;

-- 5) Optional sanity checks
SELECT
  COUNT(*) AS total_rows,
  SUM(CASE WHEN revenue IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_revenue,
  SUM(CASE WHEN yearperiod IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_yearperiod
FROM semabridge.public.fact;
