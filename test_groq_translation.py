"""
Quick smoke-test: Groq DAX → Databricks SQL translation.
Run with:  uv run python test_groq_translation.py
"""
import sys
import os

# Add src to sys.path so modules can be imported
sys.path.insert(0, os.path.abspath('src'))

from semabridge.converter.llm_dax_translator import get_llm_translator

translator = get_llm_translator()
print(f"Client : {translator.client}")
print(f"Model  : {translator.model}")
print()

# Real DAX expressions that failed in the last run
test_cases = [
    (
        "Today",
        "corporate_dsi_aggregate",
        "TODAY()",
    ),
    (
        "Corporate DSI Last Refreshed",
        "corporate_dsi_aggregate",
        "CONCATENATE(\"Last Refreshed: \", MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))",
    ),
    (
        "Source Value Total Stock",
        "corporate_dsi_aggregate",
        "SUM('Inventory Fact'[Source Value Total Stock])",
    ),
    (
        "WAC Value Total Stock",
        "corporate_dsi_aggregate",
        "SUM('Inventory Fact'[WAC Value Total Stock])",
    ),
    (
        "Corporate DSI Monthly",
        "corporate_dsi_aggregate",
        "DIVIDE([Corporate IOH], [Corporate COS] / 30)",
    ),
]

for metric_name, table_alias, dax in test_cases:
    print(f"{'='*60}")
    print(f"Metric : {metric_name}")
    print(f"DAX    : {dax}")
    result = translator.translate(
        dax=dax,
        table_alias=table_alias,
        dataset_name="Inventory Semantic Model",
        metric_name=metric_name,
    )
    status = "[PASS]" if result.is_valid else "[FAIL]"
    print(f"Status : {status}  (confidence={result.confidence:.2f}, cached={result.cached})")
    print(f"SQL    : {result.sql}")
    if result.error:
        print(f"Error  : {result.error}")
    print()