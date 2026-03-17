#!/usr/bin/env python3
"""Quick classification of your 37 measures"""

import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from semabridge.converter.dax_rule_translator import is_simple_metric

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

tier1, tier2, tier3 = [], [], []

for name, dax in sorted(measures.items()):
    is_simple = is_simple_metric(dax)
    has_time = any(kw in dax.upper() for kw in ["TOTALYTD", "SAMEPERIODLASTYEAR", "SAMEPERIODLASTMONTH"])
    
    if has_time:
        tier3.append(name)
    elif is_simple:
        tier1.append(name)
    else:
        tier2.append(name)

print()
print("YOUR 37 MEASURES - CLASSIFICATION")
print("="*100)
print()

print(f"TIER1 (Local SQL - No LLM): {len(tier1)} measures")
for m in tier1:
    print(f"  {m}")

print()
print(f"TIER2 (LLM - Batched): {len(tier2)} measures")
for m in tier2:
    print(f"  {m}")

print()
print(f"TIER3 (Time Intelligence): {len(tier3)} measures")
for m in tier3:
    print(f"  {m}")

print()
print("="*100)
print("ANSWER TO YOUR QUESTION")
print("="*100)
print()
print(f"LOCAL (Converters):  {len(tier1) + len(tier3)} measures")
print(f"  - Direct SQL aggregations (TIER1): {len(tier1)}")
print(f"  - Time intelligence (TIER3): {len(tier3)}")
print()
print(f"LLM (Gemini/Claude):  {len(tier2)} measures")
print(f"  - Batched into {(len(tier2)+7)//8} API calls")
print()
print("Result: {}/{} measures use LOCAL converters, {}/{} use LLM".format(
    len(tier1)+len(tier3), len(measures), len(tier2), len(measures)
))
print()
