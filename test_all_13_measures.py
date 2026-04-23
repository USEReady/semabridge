"""
Full smoke-test for all 13 failing DAX measures.
Run with:  uv run python test_all_13_measures.py
"""
import sys
import os

# Add src to sys.path so modules can be imported
sys.path.insert(0, os.path.abspath('src'))

from semabridge.converter.llm_dax_translator import get_llm_translator

translator = get_llm_translator()
print(f"Provider : Groq")
print(f"Model    : {translator.model}")
print(f"Client   : {'Connected' if translator.client else 'NOT CONNECTED - check GROQ_API_KEY'}")
print()

if not translator.client:
    sys.exit(1)

# All 13 failing DAX expressions extracted from raw_fabric_model.json
test_cases = [
    (
        "Today",
        "corporate_dsi_aggregate",
        "TODAY()",
    ),
    (
        "Corporate IOH",
        "corporate_dsi_aggregate",
        """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""".strip(),
    ),
    (
        "Corporate DSI Monthly",
        "corporate_dsi_aggregate",
        """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_MNTHLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""".strip(),
    ),
    (
        "Corporate DSI Quarterly",
        "corporate_dsi_aggregate",
        """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_QTD]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""".strip(),
    ),
    (
        "Corporate DSI Yearly",
        "corporate_dsi_aggregate",
        """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[DSI_YRLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""".strip(),
    ),
    (
        "Corporate COS",
        "corporate_dsi_aggregate",
        """
Var _today = [Today]
Var fiscalMonth = CALCULATE(MAX(Dates[FISCAL_YR_PERIOD]), Dates[CAL_DT] = _today)
RETURN CALCULATE(SUM('Corporate DSI Aggregate'[COS_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)
""".strip(),
    ),
    (
        "Source Value Total Stock",
        "corporate_dsi_aggregate",
        "SUM('Inventory Fact'[Source Value Total Stock])",
    ),
    (
        "Corporate DSI Last Refreshed",
        "corporate_dsi_aggregate",
        'CONCATENATE("Last Refreshed: ", MAX(\'Corporate DSI Last Refreshed\'[GL Refresh Datetime]))',
    ),
    (
        "Inventory Fact Last Refreshed",
        "corporate_dsi_aggregate",
        'CONCATENATE("Last Refreshed: ", MAX(\'Inventory Fact Last Refreshed\'[GL Refresh Datetime]))',
    ),
    (
        "Subledger Business Unit Callout",
        "corporate_dsi_aggregate",
        """VAR _sel = SELECTEDVALUE ( 'Business Units'[Business Unit] )
VAR _nl = UNICHAR(10)
VAR _b  = UNICHAR(8226)
RETURN
SWITCH (
    TRUE(),
    ISBLANK ( _sel ), "",
    _sel = "USP", _b & " Source of dashboard is SAP subledger" & _nl & _b & " Dashboard is updated daily",
    _sel = "MSH", _b & " Source of dashboard is SAP subledger via BW" & _nl & _b & " Dashboard is updated daily",
    _sel = "CMM", _b & " Source of dashboard is SAP subledger via BW" & _nl & _b & " Dashboard is updated daily",
    ""
)""",
    ),
    (
        "GL Business Unit Callout",
        "corporate_dsi_aggregate",
        """VAR _sel = SELECTEDVALUE ( 'Business Units'[Business Unit] )
VAR _nl = UNICHAR(10)
VAR _b  = UNICHAR(8226)
RETURN
SWITCH (
    TRUE(),
    ISBLANK ( _sel ), "",
    _sel = "USP", _b & " Source of dashboard is general ledger" & _nl & _b & " Data is updated monthly",
    _sel = "MSH", _b & " Source of dashboard is general ledger" & _nl & _b & " Data is updated monthly",
    _sel = "CMM", _b & " Source of dashboard is general ledger" & _nl & _b & " Data is updated monthly",
    ""
)""",
    ),
    (
        "DSI Calculation Callout",
        "corporate_dsi_aggregate",
        """VAR _nl = UNICHAR(10)
VAR _b  = UNICHAR(8226)
RETURN
    _b & " Inventory excluding LIFO & Reserve" & _nl &
    _b & " Cost of Sales excluding LIFO" & _nl &
    _b & " DSI (Monthly) = Inventory excluding LIFO & Reserve/(Cost of Sales excluding LIFO/30)" & _nl &
    _b & " DSI (Quarterly) = End Inventory within the quarter"
""",
    ),
    (
        "WAC Value Total Stock",
        "corporate_dsi_aggregate",
        "SUM('Inventory Fact'[WAC Value Total Stock])",
    ),
]

passed = 0
failed = 0
low_conf = 0

for metric_name, table_alias, dax in test_cases:
    result = translator.translate(
        dax=dax,
        table_alias=table_alias,
        dataset_name="Inventory Semantic Model",
        metric_name=metric_name,
    )

    if result.is_valid and result.confidence >= 0.55:
        status = "[PASS]"
        passed += 1
    elif result.is_valid and result.confidence > 0.4:
        status = "[LOW CONFIDENCE]"
        low_conf += 1
    else:
        status = "[FAIL]"
        failed += 1

    cached_str = " (cached)" if result.cached else ""
    print(f"{status}{cached_str}  {metric_name}")
    print(f"   confidence : {result.confidence:.2f}")
    print(f"   SQL        : {(result.sql or 'None')[:120]}")
    if result.error:
        print(f"   error      : {result.error}")
    print()

print("=" * 60)
print(f"RESULTS: {passed} passed | {low_conf} low-confidence | {failed} failed  out of {len(test_cases)}")
will_sync = passed
print(f"Measures that WILL sync to Databricks: {will_sync}/13")
print(f"Measures that need manual rewrite     : {failed}/13")