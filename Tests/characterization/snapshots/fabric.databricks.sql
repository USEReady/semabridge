-- views created: 1, skipped: 3
-- skipped: VanArsdel_Units: CROSS_TABLE_NOT_SUPPORTED
-- skipped: Units_YTD: VALIDATION_FAILED
-- skipped: Product_Count: CROSS_TABLE_NOT_SUPPORTED
CREATE OR REPLACE VIEW `main`.`analytics`.`mv_Characterization_Model_Sales` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Semabridge: Characterization Model - Sales"
source: |
  SELECT CAST(NULL AS BIGINT) AS `sale_saleskey`, CAST(NULL AS BIGINT) AS `saleskey`, CAST(NULL AS BIGINT) AS `sale_productkey`, CAST(NULL AS BIGINT) AS `productkey`, CAST(NULL AS BIGINT) AS `sale_datekey`, CAST(NULL AS BIGINT) AS `datekey`, CAST(NULL AS BIGINT) AS `sale_geokey`, CAST(NULL AS BIGINT) AS `geokey`, CAST(NULL AS BIGINT) AS `sale_units`, CAST(NULL AS BIGINT) AS `units`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_revenue`, CAST(NULL AS DECIMAL(38, 10)) AS `revenue`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_discount`, CAST(NULL AS DECIMAL(38, 10)) AS `discount`, CAST(NULL AS STRING) AS `sale_status`, CAST(NULL AS STRING) AS `status`

dimensions:
  - name: "Units"
    expr: "`sale_units`"
  - name: "Revenue"
    expr: "`sale_revenue`"
  - name: "Discount"
    expr: "`sale_discount`"
  - name: "Status"
    expr: "`sale_status`"

measures:
  - name: "Total_Units"
    expr: "sum(sale_units)"
    display_name: "Total Units"
  - name: "Total_Revenue"
    expr: "sum(sale_revenue)"
    display_name: "Total Revenue"
  - name: "Net_Revenue"
    expr: "any_value(case when (sum(sale_revenue)) is null and (sum(sale_discount::float)) is null then null else coalesce((sum(sale_revenue)), 0) - coalesce(sum(sale_discount::float), 0) end)"
    display_name: "Net Revenue"
  - name: "Margin_Pct"
    expr: "any_value((case when (sum(sale_revenue)) is null and (sum(sale_discount::float)) is null then null else coalesce((sum(sale_revenue)), 0) - coalesce(sum(sale_discount::float), 0) end) / nullif((sum(sale_revenue)), 0))"
    display_name: "Margin %"
  - name: "Valid_Units"
    expr: "sum(case when not coalesce((sale_status in ('Cancelled', 'Returned')), false) then sale_units else null end)"
    display_name: "Valid Units"
  - name: "SUM_OF_UNITS"
    expr: "sum(sale_units)"
  - name: "SUM_OF_REVENUE"
    expr: "sum(sale_revenue)"
  - name: "SUM_OF_DISCOUNT"
    expr: "sum(sale_discount)"
$$
