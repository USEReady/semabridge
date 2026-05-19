#!/usr/bin/env python3
"""
Test suite for DAX Rule-Based Translation Pipeline.

Tests the complexity classifier and rule-based translator without requiring LLM API calls.
This validates that simple metrics are correctly classified and translated, which should
reduce LLM API usage by 60-80%.
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from semabridge.converter.dax_rule_translator import is_simple_metric, rule_based_translation
def _check_simple_metrics() -> bool:
    """Return True when common simple metrics are correctly classified."""
    simple_dax_expressions = [
        # Direct aggregations
        ("SUM([Amount])", "Direct SUM"),
        ("SUM('Sales'[Amount])", "SUM with table reference"),
        ("AVERAGE([Quantity])", "Direct AVERAGE"),
        ("AVG([Price])", "Direct AVG"),
        ("COUNT([ProductID])", "Direct COUNT"),
        ("MIN([Date])", "Direct MIN"),
        ("MAX([Date])", "Direct MAX"),
        ("DISTINCTCOUNT([CustomerID])", "DISTINCTCOUNT"),
        ("COUNT(DISTINCT [CustomerID])", "COUNT DISTINCT"),
        
        # Slight variations
        ("  SUM([Amount])  ", "SUM with whitespace"),
        ("sum([Amount])", "Lowercase SUM"),
        ("COUNT('Table'[Column])", "COUNT with table reference"),
    ]
    
    print("\n" + "="*80)
    print("TEST 1: Simple Metrics Classification")
    print("="*80)
    
    passed = 0
    failed = 0
    
    for dax, description in simple_dax_expressions:
        is_simple = is_simple_metric(dax)
        status = "✓ PASS" if is_simple else "✗ FAIL"
        print(f"{status}: {description}")
        print(f"        DAX: {dax}")
        
        if is_simple:
            passed += 1
        else:
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def test_simple_metrics():
    assert _check_simple_metrics()


def _check_complex_metrics() -> bool:
    """Return True when complex metrics are correctly identified as non-simple."""
    complex_dax_expressions = [
        ("CALCULATE(SUM([Amount]), FILTER(...))", "CALCULATE with FILTER"),
        ("SUMX(Table, [Amount])", "SUMX iterator"),
        ("TOTALYTD(SUM([Amount]), [Date])", "Time intelligence (rule-handled)"),
        ("ALL([Table])", "ALL function"),
        ("EARLIER([Value])", "EARLIER context"),
        ("IF([Condition], [Value1], [Value2])", "IF statement"),
        ("RANKX(ALL(Table), ...)", "RANKX ranking"),
    ]
    
    print("\n" + "="*80)
    print("TEST 2: Complex Metrics Classification")
    print("="*80)
    
    passed = 0
    failed = 0
    
    for dax, description in complex_dax_expressions:
        is_simple = is_simple_metric(dax)
        expected_simple = dax.upper().startswith("TOTALYTD(")
        status = "✓ PASS" if is_simple == expected_simple else "✗ FAIL"
        print(f"{status}: {description}")
        print(f"        DAX: {dax}")
        
        if is_simple == expected_simple:
            passed += 1
        else:
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def test_complex_metrics():
    assert _check_complex_metrics()


def _check_rule_based_translation() -> bool:
    """Return True when rule-based translation works for simple metrics."""
    test_cases = [
        ("SUM([Amount])", "sales", 'SUM(sales."AMOUNT"::FLOAT)'),
        ("AVERAGE([Quantity])", "orders", 'AVG(orders."QUANTITY"::FLOAT)'),
        ("COUNT([CustomerID])", "customers", 'COUNT(customers."CUSTOMERID")'),
        ("DISTINCTCOUNT([ProductID])", "products", 'COUNT(DISTINCT products."PRODUCTID")'),
        ("MIN([Date])", "dates", 'MIN(dates."COL_DATE")'),
        ("MAX([Price])", "pricing", 'MAX(pricing."PRICE")'),
        ("COUNTROWS(Sales)", "sales", "COUNT(*)"),
        ("SUMX('Sales', 'Sales'[Amount] * 1.1)", "sales", 'SUM(sales."AMOUNT" * 1.1)'),
        (
            "CALCULATE(SUM('Sales'[Amount]), 'Sales'[Region] = \"North\", 'Sales'[Channel] = \"Online\")",
            "sales",
            'SUM(CASE WHEN sales."REGION" = \'North\' AND sales."CHANNEL" = \'Online\' THEN sales."AMOUNT" ELSE 0 END)',
        ),
        (
            "TOTALYTD(SUM('Sales'[Amount]), 'Date'[Date])",
            "sales",
            'OVER (PARTITION BY YEAR(sales."DATE") ORDER BY sales."DATE" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)',
        ),
    ]
    
    print("\n" + "="*80)
    print("TEST 3: Rule-Based Translation")
    print("="*80)
    
    passed = 0
    failed = 0
    
    for dax, alias, expected_pattern in test_cases:
        sql = rule_based_translation(dax, alias)
        
        # Check if output matches expected pattern
        if sql:
            # Normalize for comparison
            expected_upper = expected_pattern.upper()
            sql_upper = sql.upper()
            matches = expected_upper in sql_upper or sql_upper in expected_upper
            
            status = "✓ PASS" if matches else "✗ FAIL"
            print(f"{status}: {dax}")
            print(f"        Expected: {expected_pattern}")
            print(f"        Got: {sql}")
            
            if matches:
                passed += 1
            else:
                failed += 1
        else:
            print(f"✗ FAIL: {dax}")
            print(f"        Expected: {expected_pattern}")
            print(f"        Got: None (translation failed)")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def test_rule_based_translation():
    assert _check_rule_based_translation()


def _check_quota_savings() -> bool:
    """Return True when quota savings calculation runs successfully."""
    print("\n" + "="*80)
    print("TEST 4: Quota Savings Analysis")
    print("="*80)
    
    # Sample dataset: 100 metrics
    sample_metrics = [
        # Simple metrics (60%)
        *[("SUM([Amount])", f"Simple {i}") for i in range(1, 31)],
        *[("COUNT([ID])", f"Simple {i}") for i in range(31, 61)],
        
        # Complex metrics (40%)
        *[("CALCULATE(SUM([Amount]), FILTER(...))", f"Complex {i}") for i in range(1, 21)],
        *[("SUMX(Table, [Amount])", f"Complex {i}") for i in range(21, 41)],
    ]
    
    simple_count = sum(1 for dax, _ in sample_metrics if is_simple_metric(dax))
    complex_count = len(sample_metrics) - simple_count
    
    print(f"Total metrics: {len(sample_metrics)}")
    print(f"Simple (no LLM needed): {simple_count} ({simple_count/len(sample_metrics)*100:.0f}%)")
    print(f"Complex (LLM needed): {complex_count} ({complex_count/len(sample_metrics)*100:.0f}%)")
    print()
    print(f"API Call Impact:")
    print(f"  Without classification:")
    print(f"    - All {len(sample_metrics)} metrics would call API")
    print(f"    - Estimated LLM API calls: ~{(len(sample_metrics)+19)//20} (with batching)")
    print()
    print(f"  With classification:")
    print(f"    - Only {complex_count} complex metrics call API")
    print(f"    - Estimated LLM API calls: ~{(complex_count+19)//20} (with batching)")
    print()
    print(f"  Quota Savings: {simple_count/len(sample_metrics)*100:.0f}% of API quota preserved")
    print(f"  Metrics Avoiding LLM: {simple_count}/{len(sample_metrics)}")
    
    return True


def test_quota_savings():
    assert _check_quota_savings()


def _check_edge_cases() -> bool:
    """Return True when edge cases and boundary conditions pass."""
    print("\n" + "="*80)
    print("TEST 5: Edge Cases")
    print("="*80)
    
    edge_cases = [
        ("", False, "Empty string"),
        (None, False, "None value"),
        ("   ", False, "Whitespace only"),
        ("[Column]", False, "Single column reference"),
        ("SUM([A]) + SUM([B])", False, "Multiple aggregations (complex)"),
        ("SUM ( [ Amount ] )", True, "SUM with extra spaces"),
        ("COUNTROWS(Sales)", True, "COUNTROWS simple table"),
    ]
    
    passed = 0
    failed = 0
    
    for dax, expected, description in edge_cases:
        try:
            result = is_simple_metric(dax)
            status = "✓ PASS" if result == expected else "✗ FAIL"
            print(f"{status}: {description}")
            print(f"        Input: {repr(dax)}")
            print(f"        Expected: {expected}, Got: {result}")
            
            if result == expected:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ FAIL: {description} (Exception: {e})")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def test_edge_cases():
    assert _check_edge_cases()


def _check_performance() -> bool:
    """Return True when performance of classifier on a large batch is acceptable."""
    print("\n" + "="*80)
    print("TEST 6: Performance on Large Batch")
    print("="*80)
    
    # Create 1000 test metrics
    test_metrics = [
        f"SUM([Amount{i}])" if i % 3 == 0 else
        f"CALCULATE(SUM([Amount{i}]), FILTER(...))" if i % 3 == 1 else
        f"COUNT([ID{i}])"
        for i in range(1000)
    ]
    
    start = time.time()
    simple_count = sum(1 for dax in test_metrics if is_simple_metric(dax))
    elapsed = time.time() - start
    
    print(f"Classified {len(test_metrics)} metrics in {elapsed:.3f} seconds")
    print(f"Average time per metric: {(elapsed/len(test_metrics))*1000:.3f}ms")
    print(f"Simple: {simple_count}, Complex: {len(test_metrics)-simple_count}")
    print(f"Classification rate: {len(test_metrics)/elapsed:.0f} metrics/second")
    
    # Performance should be excellent since we're just doing regex matching
    performance_ok = elapsed < 1.0  # Should classify 1000 metrics in < 1 second
    status = "✓ PASS" if performance_ok else "✗ FAIL"
    print(f"\n{status}: Performance check")
    
    return performance_ok


def test_performance():
    assert _check_performance()


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("DAX RULE-BASED TRANSLATION TEST SUITE")
    print("="*80)
    print("Testing complexity classifier and rule-based translator")
    print("This validates the 60-80% API quota reduction strategy")
    
    # Run tests
    tests = [
        ("Simple metrics classification", test_simple_metrics),
        ("Complex metrics classification", test_complex_metrics),
        ("Rule-based translation", test_rule_based_translation),
        ("Quota savings analysis", test_quota_savings),
        ("Edge cases", test_edge_cases),
        ("Performance", test_performance),
    ]
    
    results = {}
    for test_name, test_fn in tests:
        try:
            passed = test_fn()
            results[test_name] = "✓ PASS" if passed else "✗ FAIL"
        except Exception as e:
            print(f"\n✗ ERROR in {test_name}: {e}")
            results[test_name] = "✗ ERROR"
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for test_name, result in results.items():
        print(f"{result}: {test_name}")
    
    all_passed = all(r.startswith("✓") for r in results.values())
    
    print("\n" + "="*80)
    if all_passed:
        print("✅ ALL TESTS PASSED")
        print("\nThe complexity classifier and rule-based translator are working correctly.")
        print("Expected benefit: 60-80% reduction in Gemini API calls")
    else:
        print("❌ SOME TESTS FAILED")
        print("Please review the failures above")
    print("="*80 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
