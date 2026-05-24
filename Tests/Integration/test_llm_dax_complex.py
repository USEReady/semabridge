#!/usr/bin/env python3
"""
Test Gemini DAX translator with complex measures
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.gemini_dax_translator import GeminiDAXTranslator

# Initialize translators
dax_translator = DAXTranslator()
gemini_translator = GeminiDAXTranslator()

# Test complex DAX expressions that should trigger LLM translation
test_measures = [
    {
        "name": "Complex Conditional",
        "dax": "IF(CALCULATE(SUM([Sales]), FILTER(ALL(Date), Date[Year]=2024)) > 1000000, CALCULATE(AVERAGE([Price]), FILTER(Products, Products[Category]=\"Premium\")), 0)"
    },
    {
        "name": "Time Intelligence",
        "dax": "CALCULATE(SUM([Sales]), DATESBETWEEN(Date[Date], DATE(2024,1,1), TODAY()))"
    },
    {
        "name": "Complex Aggregation",
        "dax": "VAR totalSales = SUMX(FILTER(Transactions, Transactions[Status]=\"Completed\"), Transactions[Amount] * (1 - Transactions[Discount]))\n    VAR avgDiscount = CALCULATE(AVERAGE(Transactions[Discount]))\n    RETURN IF(avgDiscount > 0.1, totalSales * 0.9, totalSales)"
    },
    {
        "name": "Nested CALCULATE",
        "dax": "CALCULATE(SUM([Sales]), ALL(Date), FILTER(Regions, NOT(Regions[Status]=\"Inactive\")), DATESBETWEEN(Date[Date], DATE(2024,1,1), TODAY()))"
    }
]

print("=" * 80)
print("TESTING GEMINI DAX TRANSLATOR WITH COMPLEX MEASURES")
print("=" * 80)

for test in test_measures:
    print(f"\n--- Test: {test['name']} ---")
    print(f"DAX: {test['dax'][:100]}...")
    
    # Try LLM translation directly (don't need standard translator for this test)
    print("\n[Gemini] Trying Gemini DAX translator...")
    try:
        result = gemini_translator.translate(
            test['dax'],
            table_alias='fact',
            dataset_name='test_dataset',
            metric_name=test['name']
        )
        print(f"    Result: {result.sql[:100] if result.sql else 'Failed'}...")
        print(f"    Model: {result.model}")
        print(f"    Confidence: {result.confidence:.2f}")
        print(f"    Valid: {result.is_valid}")
        if result.error:
            print(f"    Error: {result.error}")
    except Exception as e:
        print(f"    Exception: {str(e)[:100]}")

# Check cache
print("\n" + "=" * 80)
print("CHECKING CACHE")
print("=" * 80)

cache_file = Path(".llm_dax_cache.json")
if cache_file.exists():
    import json
    with open(cache_file) as f:
        cache = json.load(f)
    print(f"Cache entries: {len(cache)}")
    for key, value in list(cache.items())[:3]:
        print(f"  - {key[:50]}... => {value.get('sql', 'N/A')[:50]}...")
else:
    print("No cache file created yet")

print("\n[OK] Gemini Translator Test Complete")
