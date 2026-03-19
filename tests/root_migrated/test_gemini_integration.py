#!/usr/bin/env python3
"""
Test Google Gemini DAX translator
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.converter.gemini_dax_translator import get_gemini_translator

print("=" * 80)
print("TESTING GEMINI DAX TRANSLATOR")
print("=" * 80)

translator = get_gemini_translator()

if not translator.api_key:
    print("\n❌ GEMINI_API_KEY not set - LLM translation disabled")
    print("   Check your .env file")
    sys.exit(1)

print(f"\n✅ Gemini model: {translator.model}")
print(f"   API Key: {translator.api_key[:20]}...")
print(f"   Cache file: {translator.cache_file}")

print("\n" + "=" * 80)
print("TESTING DAX TRANSLATIONS")
print("=" * 80)

test_measures = [
    {
        "name": "Simple Sum",
        "dax": "SUM([Revenue])",
        "table_alias": "sales",
        "dataset": "TestDataset"
    },
    {
        "name": "Filtered Calculation",
        "dax": "CALCULATE(SUM([Sales]), FILTER(Products, Products[Category]=\"Premium\"))",
        "table_alias": "fact",
        "dataset": "SalesDataset"
    },
    {
        "name": "Complex Conditional",
        "dax": "IF(CALCULATE(SUM([Sales]), DATESBETWEEN(Date[Date], DATE(2024,1,1), TODAY())) > 1000000, CALCULATE(AVERAGE([Price]), FILTER(Products, Products[Category]=\"Premium\")), 0)",
        "table_alias": "transactions",
        "dataset": "FinanceDataset"
    }
]

results = []
for test in test_measures:
    print(f"\n[Test] {test['name']}")
    print(f"  DAX: {test['dax'][:70]}...")
    
    try:
        result = translator.translate(
            dax=test['dax'],
            table_alias=test['table_alias'],
            dataset_name=test['dataset'],
            metric_name=test['name']
        )
        
        print(f"  Model: {result.model}")
        print(f"  Cached: {result.cached}")
        print(f"  Valid: {result.is_valid}")
        print(f"  Confidence: {result.confidence:.2f}")
        
        if result.sql:
            print(f"  SQL: {result.sql[:80]}...")
        
        if result.error:
            print(f"  Error: {result.error}")
        
        results.append({
            'test': test['name'],
            'valid': result.is_valid,
            'confidence': result.confidence
        })
    except Exception as e:
        print(f"  ❌ Exception: {str(e)[:100]}")
        results.append({'test': test['name'], 'valid': False, 'confidence': 0.0})

print("\n" + "=" * 80)
print("TEST SUMMARY")
print("=" * 80)

for r in results:
    status = "✅" if r['valid'] else "⚠️"
    print(f"{status} {r['test']}: confidence={r['confidence']:.2f}")

passed = sum(1 for r in results if r['valid'])
total = len(results)
print(f"\nPassed: {passed}/{total}")

if passed == 0:
    print("\n⚠️  Note: If all tests failed with quota errors, please check:")
    print("   1. GEMINI_API_KEY in .env file is correct")
    print("   2. Your Gemini free tier quota is not exhausted")
    print("   3. See: https://ai.google.dev/gemini-api/docs/rate-limits")
