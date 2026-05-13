
import sys
import os
import re

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from semabridge.converter.dax_rule_translator import rule_based_translation

def test_rules():
    test_cases = [
        {
            "name": "DIVIDE_TEST",
            "dax": "DIVIDE([Sales], 10, 0)",
            "metric": "Divide_Test"
        },
        {
            "name": "YTD_TEST",
            "dax": "TOTALYTD(SUM([Amount]), [Date])",
            "metric": "YTD_Test"
        },
        {
            "name": "Corporate_DSI_Last_Refreshed",
            "dax": "CONCATENATE(\"Last Refreshed: \", MAX('Corporate DSI Last Refreshed'[GL Refresh Datetime]))",
            "metric": "Corporate_DSI_Last_Refreshed"
        },
        {
            "name": "Corporate_IOH",
            "dax": "CALCULATE(SUM('Corporate DSI Aggregate'[IOH_EXCLDNG_LIFO_AMT]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)",
            "metric": "Corporate_IOH"
        },
        {
            "name": "Corporate_DSI_Monthly",
            "dax": "CALCULATE(SUM('Corporate DSI Aggregate'[DSI_MNTHLY]), Dates[FISCAL_YR_PERIOD] < fiscalMonth)",
            "metric": "Corporate_DSI_Monthly"
        }
    ]

    print("=== SemaBridge: Hardened Rule Validation ===\n")
    for tc in test_cases:
        sql = rule_based_translation(tc["dax"], "fact", metric_name=tc["metric"])
        print(f"Name: {tc['name']}")
        print(f"DAX:  {tc['dax']}")
        print(f"SQL:  {sql}")
        print("-" * 40)

if __name__ == "__main__":
    test_rules()
