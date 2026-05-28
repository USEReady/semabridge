#!/usr/bin/env python3
"""
Quick test for specific DAX patterns
"""
import sys
import os
import json
# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))


test_cases = [
    {
        "name": "Simple SUM",
        "dax": "SUM('SalesFact'[Units])",
        "expected_pattern": "SUM(SALESFACT.UNITS)"
    },
    {
        "name": "YTD",
        "dax": "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        "expected_pattern": "MAX_DATE"
    },
    {
        "name": "VanArsdel Units",
        "dax": "CALCULATE(SUM('SalesFact'[Units]), 'Product'[isVanArsdel] = \"Yes\")",
        "expected_pattern": "CASE WHEN"
    },
    {
        "name": "Market Share",
        "dax": "DIVIDE([VanArsdel Units], [Total Units], 0)",
        "expected_pattern": "COALESCE"
    },
    {
        "name": "Sentiment Gap",
        "dax": "CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"No\") - CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"Yes\")",
        "expected_pattern": "AVG"
    }
]

from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer

def _get_cached_or_translate(translator, dax: str, metric_name: str):
    # A simplified version for this test script
    if not hasattr(translator, '_llm_cache'):
        translator._llm_cache = {}
        cache_file = '.llm_dax_cache.json'
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                translator._llm_cache = json.load(f)
    
    # Look for a key that matches the metric name and dax
    for key, value in translator._llm_cache.items():
        if key.startswith(f"{metric_name}|") and key.endswith(f"|{dax}"):
             return value
    
    # Fallback for older cache format
    cache_key = f"{metric_name}|{dax}"
    return translator._llm_cache.get(cache_key)


sanitizer = IdentifierSanitizer(force_uppercase=True)
translator = MetricExpressionTranslator(identifier_sanitizer=sanitizer)

for test in test_cases:
    print(f"\n📝 Testing: {test['name']}")
    print(f"   DAX: {test['dax']}")
    
    # Try to get from cache or translate
    sql = _get_cached_or_translate(translator, test['dax'], test['name'])
    
    if sql:
        print(f"   ✅ SQL: {sql[:100]}...")
        if test['expected_pattern'].upper() in sql.upper():
            print(f"   ✅ Contains expected pattern: {test['expected_pattern']}")
        else:
            print(f"   ❌ Missing expected pattern: {test['expected_pattern']}")
    else:
        print(f"   ❌ No translation found in cache")
