-- writer revision 1

-- date_metrics  sha256=d24f2225e7a600e71f6056eda77814162295410f68b9a30f2cf4ddde47e26bb3
-- note: field date: format 'General Date' omitted: pattern not expressible exactly

CREATE VIEW `workspace`.`semabridge_dev`.`date_metrics` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: INCOMPLETE – one or more source tables are empty placeholders. Results are not verified for business use. Created by SemaBridge from model golden
source: workspace.semabridge_dev.date
fields:
- name: date
  expr: source.`date`
  display_name: Date
  synonyms:
  - Date
- name: month_no
  expr: source.`month_no`
  display_name: MonthNo
  synonyms:
  - MonthNo
- name: month_name
  expr: source.`month_name`
  display_name: MonthName
  synonyms:
  - MonthName
- name: month
  expr: source.`month`
  display_name: Month
  synonyms:
  - Month
- name: quarter
  expr: source.`quarter`
  display_name: Quarter
  synonyms:
  - Quarter
- name: year
  expr: source.`year`
  display_name: Year
  synonyms:
  - Year
measures:
- name: sum_of_monthno
  expr: SUM(source.`month_no`::DOUBLE)
  display_name: SUM_OF_MONTHNO
  synonyms:
  - SUM_OF_MONTHNO
- name: sum_of_year
  expr: SUM(source.`year`::DOUBLE)
  display_name: SUM_OF_YEAR
  synonyms:
  - SUM_OF_YEAR
$$;

-- indicators_metrics  sha256=5cf5193a57eba35322256b1a05e16ebbf0b5408ab5a8275881121a82ab8228a3

CREATE VIEW `workspace`.`semabridge_dev`.`indicators_metrics` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: INCOMPLETE – one or more source tables are empty placeholders. Results are not verified for business use. Created by SemaBridge from model golden
source: workspace.semabridge_dev.indicators
fields:
- name: sort
  expr: source.`sort`
  display_name: Sort
  synonyms:
  - Sort
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
    hide_group_separator: true
- name: status
  expr: source.`status`
  display_name: Status
  synonyms:
  - Status
- name: image
  expr: source.`image`
  display_name: Image
  synonyms:
  - Image
measures:
- name: sum_of_sort
  expr: SUM(source.`sort`::DOUBLE)
  display_name: SUM_OF_SORT
  synonyms:
  - SUM_OF_SORT
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
    hide_group_separator: true
$$;

-- product_metrics  sha256=1c427fc7c2e98a27327668eb04ecfc585e5af5c223ab74cbdedb30e1d8464d16

CREATE VIEW `workspace`.`semabridge_dev`.`product_metrics` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: INCOMPLETE – one or more source tables are empty placeholders. Results are not verified for business use. Created by SemaBridge from model golden
source: workspace.semabridge_dev.product
joins:
- name: category
  source: workspace.semabridge_dev.category
  'on': source.`segment` = `category`.`category`
- name: manufacturer
  source: workspace.semabridge_dev.manufacturer
  'on': source.`manufacturer_id` = `manufacturer`.`manufacturer_id`
fields:
- name: product_manufacturer
  expr: source.`manufacturer`
  display_name: Manufacturer
  synonyms:
  - Manufacturer
- name: product_category
  expr: source.`category`
  display_name: Category
  synonyms:
  - Category
- name: segment
  expr: source.`segment`
  display_name: Segment
  synonyms:
  - Segment
- name: product
  expr: source.`product`
  display_name: Product
  synonyms:
  - Product
- name: category_category
  expr: '`category`.`category`'
  display_name: Category
  synonyms:
  - Category
  - Product Category
- name: channel
  expr: '`category`.`channel`'
  display_name: Channel
  synonyms:
  - Channel
- name: sort
  expr: '`category`.`sort`'
  display_name: Sort
  synonyms:
  - Sort
- name: manufacturer_manufacturer
  expr: '`manufacturer`.`manufacturer`'
  display_name: Manufacturer
  synonyms:
  - Manufacturer
measures:
- name: count_of_product
  expr: COUNT(source.`product`)
  display_name: Count of Product
  synonyms:
  - Count of Product
$$;

-- sales_fact_metrics  sha256=6544671e2196bc6aa8ca8adb5066458f25537271f7eeb47a77e96bccb2ce76d7
-- note: field date_date: format 'General Date' omitted: pattern not expressible exactly
-- note: measure sales_dollars: format '\\$#,0;(\\$#,0);\\$#,0' omitted: currency symbol '$' is ambiguous (no locale tag)
-- note: measure total_category_volume: format '\\$#,0;(\\$#,0);\\$#,0' omitted: currency symbol '$' is ambiguous (no locale tag)
-- note: measure total_units_ytd_var_pct: negative/zero format sections are not carried

CREATE VIEW `workspace`.`semabridge_dev`.`sales_fact_metrics` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: INCOMPLETE – one or more source tables are empty placeholders. Results are not verified for business use. Created by SemaBridge from model golden
source: workspace.semabridge_dev.sales_fact
joins:
- name: date
  source: workspace.semabridge_dev.date
  'on': source.`date` = `date`.`date`
- name: geo
  source: workspace.semabridge_dev.geo
  'on': source.`zip` = `geo`.`zip`
- name: product
  source: workspace.semabridge_dev.product
  'on': source.`product_id` = `product`.`product_id`
  joins:
  - name: category
    source: workspace.semabridge_dev.category
    'on': '`product`.`segment` = `category`.`category`'
  - name: manufacturer
    source: workspace.semabridge_dev.manufacturer
    'on': '`product`.`manufacturer_id` = `manufacturer`.`manufacturer_id`'
fields:
- name: date_date
  expr: '`date`.`date`'
  display_name: Date
  synonyms:
  - Date
- name: month_no
  expr: '`date`.`month_no`'
  display_name: MonthNo
  synonyms:
  - MonthNo
- name: month_name
  expr: '`date`.`month_name`'
  display_name: MonthName
  synonyms:
  - MonthName
- name: month
  expr: '`date`.`month`'
  display_name: Month
  synonyms:
  - Month
- name: quarter
  expr: '`date`.`quarter`'
  display_name: Quarter
  synonyms:
  - Quarter
- name: year
  expr: '`date`.`year`'
  display_name: Year
  synonyms:
  - Year
- name: city
  expr: '`geo`.`city`'
  display_name: City
  synonyms:
  - City
- name: state
  expr: '`geo`.`state`'
  display_name: State
  synonyms:
  - State
- name: region
  expr: '`geo`.`region`'
  display_name: Region
  synonyms:
  - Region
  - Sales Region
- name: district
  expr: '`geo`.`district`'
  display_name: District
  synonyms:
  - District
- name: product_manufacturer
  expr: '`product`.`manufacturer`'
  display_name: Manufacturer
  synonyms:
  - Manufacturer
- name: product_category
  expr: '`product`.`category`'
  display_name: Category
  synonyms:
  - Category
- name: segment
  expr: '`product`.`segment`'
  display_name: Segment
  synonyms:
  - Segment
- name: product_product
  expr: '`product`.`product`'
  display_name: Product
  synonyms:
  - Product
- name: category_category
  expr: '`product`.`category`.`category`'
  display_name: Category
  synonyms:
  - Category
  - Product Category
- name: channel
  expr: '`product`.`category`.`channel`'
  display_name: Channel
  synonyms:
  - Channel
- name: sort
  expr: '`product`.`category`.`sort`'
  display_name: Sort
  synonyms:
  - Sort
- name: manufacturer_manufacturer
  expr: '`product`.`manufacturer`.`manufacturer`'
  display_name: Manufacturer
  synonyms:
  - Manufacturer
measures:
- name: total_units
  expr: SUM(source.`units`::DOUBLE)
  display_name: Total Units
  synonyms:
  - Total Units
  - Units Sold
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
- name: sales_dollars
  expr: SUM(source.`revenue`::DOUBLE)
  display_name: Sales $
  synonyms:
  - Sales $
  - Total Revenue
- name: total_units_ytd
  expr: SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)
  display_name: Total Units YTD
  synonyms:
  - Total Units YTD
  comment: 'approximate: CALCULATE filters may give a different result under some slicers; approximate: time intelligence is anchored to the current date'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
- name: total_category_volume
  expr: (SUM(source.`units`::DOUBLE))
  display_name: Total Category Volume
  synonyms:
  - Total Category Volume
  - Category Volume
- name: total_units_sply
  expr: SUM(CASE WHEN YEAR(`date`.`date`) = YEAR(ADD_MONTHS(CURRENT_DATE(), -12)) AND `date`.`date` BETWEEN ADD_MONTHS(TRUNC(CURRENT_DATE(), 'YEAR'), -12) AND ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)
  display_name: TOTAL UNITS SPLY
  synonyms:
  - TOTAL UNITS SPLY
  comment: 'approximate: CALCULATE filters may give a different result under some slicers; approximate: time intelligence is anchored to the current date'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
- name: total_units_ytd_sply
  expr: (SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END))
  display_name: Total Units YTD SPLY
  synonyms:
  - Total Units YTD SPLY
  comment: 'approximate: CALCULATE filters may give a different result under some slicers; approximate: time intelligence is anchored to the current date; approximate: built on an approximate measure'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
- name: total_units_ytd_var
  expr: CASE WHEN (SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL AND (SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL THEN NULL ELSE COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)), 0) - COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0) END
  display_name: Total Units YTD Var
  synonyms:
  - Total Units YTD Var
  comment: 'approximate: time intelligence is anchored to the current date; approximate: built on an approximate measure'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
- name: total_units_ytd_var_pct
  expr: (CASE WHEN (SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL AND (SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL THEN NULL ELSE COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)), 0) - COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0) END) / NULLIF((SUM(CASE WHEN YEAR(`date`.`date`) = YEAR(ADD_MONTHS(CURRENT_DATE(), -12)) AND `date`.`date` BETWEEN ADD_MONTHS(TRUNC(CURRENT_DATE(), 'YEAR'), -12) AND ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0)
  display_name: Total Units YTD Var %
  synonyms:
  - Total Units YTD Var %
  comment: 'approximate: time intelligence is anchored to the current date; approximate: built on an approximate measure'
  format:
    type: percentage
    decimal_places:
      type: exact
      places: 0
    hide_group_separator: true
- name: sum_of_units
  expr: SUM(source.`units`::DOUBLE)
  display_name: Sum of Units
  synonyms:
  - Sum of Units
- name: sum_of_revenue
  expr: SUM(source.`revenue`::DOUBLE)
  display_name: Sum of Revenue
  synonyms:
  - Sum of Revenue
- name: at_indicator03
  expr: CASE WHEN ((CASE WHEN (SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL AND (SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL THEN NULL ELSE COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)), 0) - COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0) END) / NULLIF((SUM(CASE WHEN YEAR(`date`.`date`) = YEAR(ADD_MONTHS(CURRENT_DATE(), -12)) AND `date`.`date` BETWEEN ADD_MONTHS(TRUNC(CURRENT_DATE(), 'YEAR'), -12) AND ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0)) < 0 THEN 1 ELSE CASE WHEN ((CASE WHEN (SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL AND (SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL THEN NULL ELSE COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)), 0) - COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0) END) / NULLIF((SUM(CASE WHEN YEAR(`date`.`date`) = YEAR(ADD_MONTHS(CURRENT_DATE(), -12)) AND `date`.`date` BETWEEN ADD_MONTHS(TRUNC(CURRENT_DATE(), 'YEAR'), -12) AND ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0)) > 0.1 THEN 3 ELSE 2 END END
  display_name: '@Indicator03'
  synonyms:
  - '@Indicator03'
  comment: 'approximate: time intelligence is anchored to the current date; approximate: built on an approximate measure'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
    hide_group_separator: true
- name: total_units_ytd_var_pct_2
  expr: FLOOR((CASE WHEN (SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL AND (SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)) IS NULL THEN NULL ELSE COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(CURRENT_DATE(), 'YEAR') AND `date`.`date` <= CURRENT_DATE() THEN source.`units`::DOUBLE ELSE NULL END)), 0) - COALESCE((SUM(CASE WHEN `date`.`date` >= TRUNC(ADD_MONTHS(CURRENT_DATE(), -12), 'YEAR') AND `date`.`date` <= ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0) END) / NULLIF((SUM(CASE WHEN YEAR(`date`.`date`) = YEAR(ADD_MONTHS(CURRENT_DATE(), -12)) AND `date`.`date` BETWEEN ADD_MONTHS(TRUNC(CURRENT_DATE(), 'YEAR'), -12) AND ADD_MONTHS(CURRENT_DATE(), -12) THEN source.`units`::DOUBLE ELSE NULL END)), 0) * 100)
  display_name: Total Units YTD Var %2
  synonyms:
  - Total Units YTD Var %2
  comment: 'approximate: time intelligence is anchored to the current date; approximate: built on an approximate measure'
$$;

-- sentiment_metrics  sha256=9c23d1a04b61b07b5dccc8afb914e2b02ea9e49ee27561ad9c5ca3b67a1b1f03
-- note: field date_date: format 'General Date' omitted: pattern not expressible exactly

CREATE VIEW `workspace`.`semabridge_dev`.`sentiment_metrics` WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: INCOMPLETE – one or more source tables are empty placeholders. Results are not verified for business use. Created by SemaBridge from model golden
source: workspace.semabridge_dev.sentiment
joins:
- name: date
  source: workspace.semabridge_dev.date
  'on': source.`date` = `date`.`date`
- name: geo
  source: workspace.semabridge_dev.geo
  'on': source.`zip` = `geo`.`zip`
- name: manufacturer
  source: workspace.semabridge_dev.manufacturer
  'on': source.`manufacturer_id` = `manufacturer`.`manufacturer_id`
fields:
- name: date_date
  expr: '`date`.`date`'
  display_name: Date
  synonyms:
  - Date
- name: month_no
  expr: '`date`.`month_no`'
  display_name: MonthNo
  synonyms:
  - MonthNo
- name: month_name
  expr: '`date`.`month_name`'
  display_name: MonthName
  synonyms:
  - MonthName
- name: month
  expr: '`date`.`month`'
  display_name: Month
  synonyms:
  - Month
- name: quarter
  expr: '`date`.`quarter`'
  display_name: Quarter
  synonyms:
  - Quarter
- name: year
  expr: '`date`.`year`'
  display_name: Year
  synonyms:
  - Year
- name: city
  expr: '`geo`.`city`'
  display_name: City
  synonyms:
  - City
- name: state
  expr: '`geo`.`state`'
  display_name: State
  synonyms:
  - State
- name: region
  expr: '`geo`.`region`'
  display_name: Region
  synonyms:
  - Region
  - Sales Region
- name: district
  expr: '`geo`.`district`'
  display_name: District
  synonyms:
  - District
- name: manufacturer_manufacturer
  expr: '`manufacturer`.`manufacturer`'
  display_name: Manufacturer
  synonyms:
  - Manufacturer
measures:
- name: sentiment
  expr: AVG(source.`score`::DOUBLE)
  display_name: Sentiment
  synonyms:
  - Sentiment
  - Brand Sentiment Score
  comment: 'approximate: CALCULATE filters may give a different result under some slicers'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
    hide_group_separator: true
- name: at_indicator04
  expr: CASE WHEN COALESCE((AVG(source.`score`::DOUBLE)), 0) < 65 THEN 1 ELSE CASE WHEN (AVG(source.`score`::DOUBLE)) > 67 THEN 3 ELSE 2 END END
  display_name: '@Indicator04'
  synonyms:
  - '@Indicator04'
  comment: 'approximate: time intelligence is anchored to the current date; approximate: built on an approximate measure'
  format:
    type: number
    decimal_places:
      type: exact
      places: 0
    hide_group_separator: true
- name: sum_of_score
  expr: SUM(source.`score`::DOUBLE)
  display_name: Sum of Score
  synonyms:
  - Sum of Score
- name: average_of_score
  expr: AVG(source.`score`::DOUBLE)
  display_name: Average of Score
  synonyms:
  - Average of Score
- name: new_demo_measure
  expr: AVG(source.`score`::DOUBLE)
  display_name: New Demo Measure
  synonyms:
  - New Demo Measure
$$;
