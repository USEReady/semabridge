# Phase A relationship report

Models: 12 | traces loaded: 0 | log files: 0

## Summary

| Model | Source | DDL | Emitted | Missing | Key missing | Clean |
| --- | --- | --- | --- | --- | --- | --- |
| 2020SU09_Blog_Demo_-_September | ERROR: sequence item 0: expected str instance, NoneType found | | | | | |
| Adventure_Works_DW_2020 | 13 | 11 | 11 | 2 | 6 | NO |
| Artificial_Intelligence_Sample | ERROR: sequence item 0: expected str instance, NoneType found | | | | | |
| Competitive_Marketing_Analysis | 8 | 8 | 8 | 0 | 0 | yes |
| Corporate_Spend | 7 | 7 | 7 | 0 | 0 | yes |
| Employee_Hiring_and_History | 8 | 8 | 3 | 1 | 0 | NO |
| Human_Resources_Sample_PBIX | ERROR: sequence item 0: expected str instance, NoneType found | | | | | |
| Life_expectancy_v202009 | ERROR: sequence item 0: expected str instance, NoneType found | | | | | |
| Power_BI_Visuals | ERROR: sequence item 0: expected str instance, NoneType found | | | | | |
| Revenue_Opportunities | 7 | 6 | 6 | 1 | 1 | NO |
| Sales_Returns_Sample_v201912 | ERROR: sequence item 0: expected str instance, NoneType found | | | | | |
| Supply_Chain_Sample | 2 | 2 | 2 | 0 | 0 | yes |

## Problem relationships and root-cause stage

### Adventure_Works_DW_2020: Sales[SalesTerritoryKey] -> Sales Territory[SalesTerritoryKey]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'SALES_TERRITORY' has no PRIMARY KEY/UNIQUE on ['SALESTERRITORYKEY'].

### Adventure_Works_DW_2020: Sales[SalesOrderLineKey] -> Sales Order[SalesOrderLineKey]

- Source: active=yes, cardinality=1:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'SALES_ORDER' has no PRIMARY KEY/UNIQUE on ['SALESORDERLINEKEY'].

### Adventure_Works_DW_2020: Sales[OrderDateKey] -> Date[DateKey]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'COL_DATE' has no PRIMARY KEY/UNIQUE on ['DATEKEY'].

### Adventure_Works_DW_2020: Sales[DueDateKey] -> Date[DateKey]

- Source: active=NO, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### Adventure_Works_DW_2020: Sales[ShipDateKey] -> Date[DateKey]

- Source: active=NO, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### Adventure_Works_DW_2020: Currency Rate[DateKey] -> Date[DateKey]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'COL_DATE' has no PRIMARY KEY/UNIQUE on ['DATEKEY'].

### Adventure_Works_DW_2020: Sales Reason Bridge[SalesReasonKey] -> Sales Reason[SalesReasonKey]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'SALES_REASON' has no PRIMARY KEY/UNIQUE on ['SALESREASONKEY'].

### Adventure_Works_DW_2020: Sales Reason Bridge[SalesOrderLineKey] -> Sales Order[SalesOrderLineKey]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'SALES_ORDER' has no PRIMARY KEY/UNIQUE on ['SALESORDERLINEKEY'].

### Employee_Hiring_and_History: Employee[date] -> Date[Date]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### Revenue_Opportunities: Sales[Sales Stage ID] -> SalesStages[Sales Stage ID]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'SALESSTAGES' has no PRIMARY KEY/UNIQUE on ['SALES_STAGE_ID'].

### Revenue_Opportunities: Sales[Closed Date] -> Dates[Date]

- Source: active=NO, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

## DDL files without a matching .pbix

- output\debug\debug_generated_sql_COVID-19_US_Tracking_Sample.sql
- output\debug\debug_generated_sql_PerformanceAnalyzerExportReport.sql
