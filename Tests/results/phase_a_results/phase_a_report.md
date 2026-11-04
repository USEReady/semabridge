# Phase A relationship report

Models: 12 | traces loaded: 0 | log files: 0

## Summary

| Model | Source | DDL | Emitted | Missing | Key missing | Clean |
| --- | --- | --- | --- | --- | --- | --- |
| 2020SU09_Blog_Demo_-_September | 0 | 0 | 0 | 0 | 0 | yes |
| Adventure_Works_DW_2020 | 13 | 13 | 13 | 0 | 0 | yes |
| Artificial_Intelligence_Sample | 12 | 12 | 12 | 0 | 0 | yes |
| Competitive_Marketing_Analysis | 8 | 8 | 8 | 0 | 0 | yes |
| Corporate_Spend | 7 | 7 | 7 | 0 | 0 | yes |
| Employee_Hiring_and_History | 8 | 8 | 3 | 1 | 0 | NO |
| Human_Resources_Sample_PBIX | 8 | 8 | 7 | 0 | 0 | NO |
| Life_expectancy_v202009 | 9 | 9 | 9 | 0 | 0 | yes |
| Power_BI_Visuals | 1 | 0 | 0 | 1 | 0 | NO |
| Revenue_Opportunities | 7 | 7 | 7 | 0 | 0 | yes |
| Sales_Returns_Sample_v201912 | 8 | 8 | 8 | 0 | 1 | NO |
| Supply_Chain_Sample | 2 | 2 | 2 | 0 | 0 | yes |

## Problem relationships and root-cause stage

### Employee_Hiring_and_History: Employee[date] -> Date[Date]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### Power_BI_Visuals: Financials[Date] -> Date[Date]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### Sales_Returns_Sample_v201912: Association[RightItemSetId] -> Associated Product[ProductID]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'ASSOCIATED_PRODUCT' has no PRIMARY KEY/UNIQUE on ['PRODUCTID'].

## Changes vs baseline

- Adventure_Works_DW_2020: Sales[SalesTerritoryKey] -> Sales Territory[SalesTerritoryKey]: EMITTED/NO -> EMITTED/yes
- Adventure_Works_DW_2020: Sales[SalesOrderLineKey] -> Sales Order[SalesOrderLineKey]: EMITTED/NO -> EMITTED/yes
- Adventure_Works_DW_2020: Sales[OrderDateKey] -> Date[DateKey]: EMITTED/NO -> EMITTED/yes
- Adventure_Works_DW_2020: Sales[DueDateKey] -> Date[DateKey]: MISSING/- -> EMITTED/yes
- Adventure_Works_DW_2020: Sales[ShipDateKey] -> Date[DateKey]: MISSING/- -> EMITTED/yes
- Adventure_Works_DW_2020: Currency Rate[DateKey] -> Date[DateKey]: EMITTED/NO -> EMITTED/yes
- Adventure_Works_DW_2020: Sales Reason Bridge[SalesReasonKey] -> Sales Reason[SalesReasonKey]: EMITTED/NO -> EMITTED/yes
- Adventure_Works_DW_2020: Sales Reason Bridge[SalesOrderLineKey] -> Sales Order[SalesOrderLineKey]: EMITTED/NO -> EMITTED/yes
- Revenue_Opportunities: Sales[Sales Stage ID] -> SalesStages[Sales Stage ID]: EMITTED/NO -> EMITTED/yes
- Revenue_Opportunities: Sales[Closed Date] -> Dates[Date]: MISSING/- -> EMITTED/yes

## DDL files without a matching .pbix

- output\debug\debug_generated_sql_COVID-19_US_Tracking_Sample.sql
- output\debug\debug_generated_sql_Client_Data.sql
- output\debug\debug_generated_sql_PerformanceAnalyzerExportReport.sql
- output\debug\debug_generated_sql_Shared_Quantity_Model.sql
- output\debug\debug_generated_sql_feature_tests.sql
