#!/usr/bin/env python3
"""
Analyze the 37 user measures to classify as TIER1 (local) or TIER2 (LLM)
"""

import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from semabridge.converter.dax_rule_translator import is_simple_metric

# The 37 measures from the user
measures = {
    "Total Units": "SUM([Units])",
    "Sales $": "SUM([Revenue])",
    "Sentiment": "AVERAGE(Sentiment[Score])",
    "Total Units YTD": "TOTALYTD([TOTAL UNITS], 'Date'[Date])",
    "Total VanArsdel Units YTD": "CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))",
    "Total OTHER Units YTD": "CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"No\"))",
    "Total VanArsdel Units": "CALCULATE([Total Units], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))",
    "Total OTHER Units": "CALCULATE([Total Units], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"No\"))",
    "% Units Market Share": "IF([Total VanArsdel Units]=0, 0, DIVIDE([Total VanArsdel Units], [Total Units], 0))",
    "Total Category Volume": "CALCULATE([Total Units])",
    "Total Compete Volume": "CALCULATE([Total Units], FILTER('Product', Product[isVanArsdel]=\"No\"))",
    "% Category Compete Share": "INT(DIVIDE([Total Compete Volume], [Total Category Volume])*100)",
    "TOTAL UNITS SPLY": "CALCULATE([TOTAL UNITS],SAMEPERIODLASTYEAR('Date'[Date]))",
    "Total Units YTD SPLY": "CALCULATE([Total Units YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
    "Total VanArsdel Units YTD SPLY": "CALCULATE([Total VanArsdel Units YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
    "Total OTHER Units YTD SPLY": "CALCULATE([Total OTHER Units YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
    "% Units Market Share YTD": "DIVIDE([Total VanArsdel Units YTD], [Total Units YTD])",
    "% Market Share SPLY YTD": "CALCULATE([% Units Market Share YTD], SAMEPERIODLASTYEAR('Date'[Date]))",
    "% Unit Market Share YOY Change": "[% Units Market Share]-[% Units Market Share SPLY]",
    "% Units Market Share SPLY": "CALCULATE([% Units Market Share], SAMEPERIODLASTYEAR('Date'[Date]))",
    "Total Units R12Ms": "CALCULATE(SUM([Units]), FILTER(ALL('Date'), 'Date'[MonthIndex]<=MAX('Date'[MonthIndex])&& 'Date'[MonthIndex]> MAX('Date'[MonthIndex])-12))",
    "Total VanArsdel Units R12M": "CALCULATE([Total Units R12Ms], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))",
    "Total OTHER Units R12M": "CALCULATE([Total Units R12Ms], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"No\"))",
    "% Units Market Share R12M": "IF([Total VanArsdel Units R12M]=0, 0, DIVIDE([Total VanArsdel Units R12M], [Total Units R12Ms], 0))",
    "Sentiment Gap": "IF(ISBLANK(CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"No\"))||ISBLANK(CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"Yes\")), BLANK(), CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"No\") - CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"Yes\"))",
    "Total Units YTD Var": "[Total Units YTD]-[Total Units YTD SPLY]",
    "Total Units YTD Var %": "DIVIDE([Total Units YTD Var],[TOTAL UNITS SPLY])",
    "Sum of Units": "SUM('SalesFact'[Units])",
    "Sum of Revenue": "SUM('SalesFact'[Revenue])",
    "@Indicator01": "IF('SalesFact'[% Category Compete Share]<0.55,1,IF('SalesFact'[% Category Compete Share]>0.6,3,2))",
    "@Indicator02": "IF('SalesFact'[% Unit Market Share YOY Change]<0,1,IF('SalesFact'[% Unit Market Share YOY Change]>.2,3,2))",
    "#Space01": "\"\"",
    "#Space02": "\"\"",
    "@Indicator03": "IF('SalesFact'[Total Units YTD Var %]<0,1,IF('SalesFact'[Total Units YTD Var %]>0.1,3,2))",
    "@Indicator04": "IF('SalesFact'[Sentiment]<65,1,IF('SalesFact'[Sentiment]>67,3,2))",
    "@Indicator05": "IF('SalesFact'[Sentiment Gap]<15,1,IF('SalesFact'[Sentiment Gap]>25,3,2))",
    "@Indicator04A": "IF('SalesFact'[@Indicator04]=1,\"Low Sentiment Rate\",IF('SalesFact'[@Indicator04]=3,\"High Sentiment Rate\",\"Medium Sentiment Rate\"))",
    "@Indicator05A": "IF('SalesFact'[@Indicator05]=1,\"Low Sentiment Gap\",IF('SalesFact'[@Indicator05]=3,\"High Sentiment Gap\",\"Medium Sentiment Gap\"))",
    "Total Units YTD Var %2": "INT(DIVIDE([Total Units YTD Var],[TOTAL UNITS SPLY])*100)",
}

print("="*100)
print("USER MEASURE CLASSIFICATION ANALYSIS")
print("="*100)
print()

tier1 = []
tier2 = []
tier3 = []

print("CLASSIFYING EACH MEASURE:")
print("-"*100)

for measure_name, dax in sorted(measures.items()):
    is_simple = is_simple_metric(dax)
    
    # Additional heuristic: check for time intelligence keywords
    has_time_intel = any(keyword in dax.upper() for keyword in [
        "TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", 
        "SAMEPERIODLASTMONTH", "DATEADD"
    ])
    
    if has_time_intel:
        tier = "TIER3 (Time Intelligence)"
        tier3.append(measure_name)
    elif is_simple:
        tier = "TIER1 (Local SQL)"
        tier1.append(measure_name)
    else:
        tier = "TIER2 (LLM)"
        tier2.append(measure_name)
    
    # Show first 80 chars of DAX
    dax_preview = dax[:75] + ("..." if len(dax) > 75 else "")
    print(f"  {measure_name:.<35} {tier:.<20} | {dax_preview}")

print()
print("="*100)
print("SUMMARY BY TIER")
print("="*100)
print()

print(f"TIER1 - Local SQL (No LLM needed): {len(tier1)} measures")
print("-"*100)
for m in tier1:
    print(f"  [LOCAL] {m}")

print()
print(f"TIER2 - LLM Translation (Batched): {len(tier2)} measures")
print("-"*100)
for m in tier2:
    print(f"  [LLM] {m}")

print()
print(f"TIER3 - Time Intelligence (Window Functions): {len(tier3)} measures")
print("-"*100)
for m in tier3:
    print(f"  [TIME] {m}")

print()
print("="*100)
print("DEPLOYMENT METRICS")
print("="*100)
print()
print(f"Total Measures: {len(measures)}")
print(f"  - TIER1 (instant, no LLM): {len(tier1)} measures ({100*len(tier1)//len(measures)}%)")
print(f"  - TIER2 (batch LLM): {len(tier2)} measures ({100*len(tier2)//len(measures)}%)")
print(f"  - TIER3 (included as-is): {len(tier3)} measures ({100*len(tier3)//len(measures)}%)")
print()

# LLM efficiency
if tier2:
    batches_needed = (len(tier2) + 7) // 8  # 8 measures per batch
    print(f"LLM Efficiency:")
    print(f"  - Individual API calls (old method): {len(tier2)} calls")
    print(f"  - Batched API calls (new method): {batches_needed} batches (8 measures per batch)")
    print(f"  - API reduction: {100 * (1 - batches_needed / len(tier2)):.0f}%")
    print(f"  - Cost savings: ~{100 * (1 - batches_needed / len(tier2)):.0f}% of LLM budget")
else:
    print(f"No LLM calls needed!")

print()
print("="*100)
print("ANSWER TO YOUR QUESTION")
print("="*100)
print()
print(f"Of your 37 measures:")
print(f"  {len(tier1):2d} measures use LOCAL CONVERTERS (TIER1) - instant translation")
print(f"  {len(tier2):2d} measures need LLM help (TIER2) - but batched efficiently")
print(f"  {len(tier3):2d} measures use TIME INTELLIGENCE (TIER3) - window functions")
print()
print(f"So: {len(tier1)+len(tier3)} would be fast & free, {len(tier2)} would need LLM (but in {batches_needed} batches, not {len(tier2)} calls)")
print()
