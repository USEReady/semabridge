-- views created: 1, skipped: 0
CREATE OR REPLACE VIEW `main`.`analytics`.`mv_characterization_Sales_Fact` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Semabridge: characterization - Sales Fact \u2014 Sales transactions"
source: |
  SELECT CAST(NULL AS DECIMAL(38, 10)) AS `sale_sales_key`, CAST(NULL AS DECIMAL(38, 10)) AS `sales_key`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_product_key`, CAST(NULL AS DECIMAL(38, 10)) AS `product_key`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_date_key`, CAST(NULL AS DECIMAL(38, 10)) AS `date_key`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_geo_key`, CAST(NULL AS DECIMAL(38, 10)) AS `geo_key`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_units`, CAST(NULL AS DECIMAL(38, 10)) AS `units`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_revenue`, CAST(NULL AS DECIMAL(38, 10)) AS `revenue`, CAST(NULL AS DECIMAL(38, 10)) AS `sale_discount`, CAST(NULL AS DECIMAL(38, 10)) AS `discount`, CAST(NULL AS STRING) AS `sale_status`, CAST(NULL AS STRING) AS `status`

dimensions:
  - name: "SALES_KEY"
    expr: "`sale_sales_key`"
  - name: "Sales_Fact_PRODUCT_KEY"
    expr: "`sale_product_key`"
    display_name: "PRODUCT_KEY"
  - name: "Sales_Fact_DATE_KEY"
    expr: "`sale_date_key`"
    display_name: "DATE_KEY"
  - name: "Sales_Fact_GEO_KEY"
    expr: "`sale_geo_key`"
    display_name: "GEO_KEY"
  - name: "UNITS"
    expr: "`sale_units`"
  - name: "REVENUE"
    expr: "`sale_revenue`"
  - name: "DISCOUNT"
    expr: "`sale_discount`"
  - name: "STATUS"
    expr: "`sale_status`"

measures:
  - name: "SUM_OF_SALES_KEY"
    expr: "sum(sale_sales_key)"
  - name: "SUM_OF_UNITS"
    expr: "sum(sale_units)"
  - name: "SUM_OF_REVENUE"
    expr: "sum(sale_revenue)"
  - name: "SUM_OF_DISCOUNT"
    expr: "sum(sale_discount)"
$$
