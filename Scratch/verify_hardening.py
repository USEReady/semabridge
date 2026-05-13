import sys
import os

# Add src to path
sys.path.append(os.path.abspath('src'))

from semabridge.converter.dax_rule_translator import is_simple_metric

test_cases = [
    "SUM(Sales[Amount])",
    "VAR x = 1 RETURN x",
    "VAR x = 1",
    "RETURN 1",
    "IF(ISBLANK([Measure]), 0, 1)",
    "CALCULATE(SUM(Sales[Amount]), ALL(Dates))"
]

print("--- Testing is_simple_metric Hardening ---")
for dax in test_cases:
    is_simple = is_simple_metric(dax)
    print(f"DAX: {dax[:40]:<40} -> Simple: {is_simple}")
