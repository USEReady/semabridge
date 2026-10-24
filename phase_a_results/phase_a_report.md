# Phase A relationship report

Models: 14 | traces loaded: 0 | log files: 0

## Summary

| Model | Source | DDL | Emitted | Missing | Key missing | Clean |
| --- | --- | --- | --- | --- | --- | --- |
| 2020SU09_Blog_Demo_-_September | 0 | 0 | 0 | 0 | 0 | yes |
| Adventure_Works_DW_2020 | 13 | 11 | 11 | 2 | 6 | NO |
| Artificial_Intelligence_Sample | 12 | 12 | 11 | 0 | 1 | NO |
| COVID-19_US_Tracking_Sample | 1 | 1 | 1 | 0 | 1 | NO |
| Competitive_Marketing_Analysis | 8 | 8 | 8 | 0 | 0 | yes |
| Corporate_Spend | 7 | 7 | 7 | 0 | 0 | yes |
| Employee_Hiring_and_History | 8 | 8 | 3 | 1 | 0 | NO |
| Human_Resources_Sample_PBIX | 8 | 8 | 7 | 0 | 0 | NO |
| Life_expectancy_v202009 | 9 | 9 | 9 | 0 | 0 | yes |
| PerformanceAnalyzerExportReport | 4 | 1 | 1 | 3 | 0 | NO |
| Power_BI_Visuals | 1 | 0 | 0 | 1 | 0 | NO |
| Revenue_Opportunities | 7 | 6 | 6 | 1 | 1 | NO |
| Sales_Returns_Sample_v201912 | 8 | 8 | 8 | 0 | 0 | yes |
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

### Artificial_Intelligence_Sample: Accounts[Account Owner] -> Owners[Sales owner]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'OWNERS' has no PRIMARY KEY/UNIQUE on ['SALES_OWNER'].

### COVID-19_US_Tracking_Sample: COVID[State] -> StateDim[State code]

- Source: active=yes, cardinality=M:1, outcome=EMITTED
- Stage: **EMITTED WITHOUT KEY**
- Detail: referenced side 'STATEDIM' has no PRIMARY KEY/UNIQUE on ['STATE_CODE'].

### Employee_Hiring_and_History: Employee[date] -> Date[Date]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### PerformanceAnalyzerExportReport: EventEdges[event_type] -> EventTypes[event_type]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### PerformanceAnalyzerExportReport: Events[Root] -> RootActions[Root]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### PerformanceAnalyzerExportReport: EventEdges[Root] -> RootActions[Root]

- Source: active=yes, cardinality=M:1, outcome=MISSING
- Stage: **NO TRACE**
- Detail: Run a dry run through semabridge_trace.py for this model, then re-run.

### Power_BI_Visuals: Financials[Date] -> Date[Date]

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

- output\debug\debug_generated_sql_Client_Data.sql
- output\debug\debug_generated_sql_Shared_Quantity_Model.sql
