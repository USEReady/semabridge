"""
Phase 2 Validation Script.

Exercises the new specialized translators (CALCULATE, Row Context, Iterators)
and verifies the generated SQL.
"""

from semabridge.converter.dax_engine import DaxTranslationEngine

def test_phase_2_translations():
    engine = DaxTranslationEngine(cache_enabled=False)
    
    test_cases = [
        {
            "name": "Nested CALCULATE with Filter",
            "dax": "CALCULATE(SUM('Sales'[Amount]), 'Date'[Year] = 2024, 'Product'[Category] = \"Electronics\")",
            "expected_contains": "WITH ctx_lvl_1"
        },
        {
            "name": "Row Context (EARLIER)",
            "dax": "EARLIER('Sales'[Amount])",
            "expected_contains": "FIRST_VALUE"
        },
        {
            "name": "Iterator (SUMX)",
            "dax": "SUMX('Sales', 'Sales'[Amount] * 1.1)",
            "expected_contains": "SUM("
        },
        {
            "name": "Complex Iterator (RANKX)",
            "dax": "RANKX('Sales', SUM('Sales'[Amount]))",
            "expected_contains": "RANK() OVER"
        },
        {
            "name": "Tier 4: Nested CALCULATE with REMOVEFILTERS",
            "dax": "CALCULATE(SUM('Sales'[Amount]), REMOVEFILTERS('Product'), 'Date'[Year] = 2024)",
            "expected_contains": "ctx_lvl_1"
        },
        {
            "name": "Tier 5: High Complexity Iterator & LLM",
            "dax": "SUMX(FILTER('Sales', 'Sales'[Amount] > CALCULATE(AVERAGE('Sales'[Amount]), ALL('Sales'))), 'Sales'[Amount])",
            "expected_contains": "/* LLM FALLBACK */"
        },
        {
            "name": "Tier 5: Dynamic Relationship (USERELATIONSHIP)",
            "dax": "CALCULATE(SUM('Sales'[Amount]), USERELATIONSHIP('Date'[Date], 'Sales'[ShipDate]))",
            "expected_contains": "/* LLM GENERATED */"
        }
    ]
    
    print("=== Phase 2 Translation Validation ===\n")
    
    for tc in test_cases:
        print(f"Testing: {tc['name']}")
        print(f"DAX: {tc['dax']}")
        
        try:
            sql, metrics = engine.translate(tc['dax'])
            
            if sql:
                print(f"✓ SQL Generated:\n{sql}")
                
                # Flexible pattern match
                expected = tc['expected_contains']
                success = False
                
                if expected == "/* LLM FALLBACK */":
                    # Accept both FALLBACK and GENERATED markers for Tier 5
                    if "/* LLM FALLBACK */" in sql or "/* LLM GENERATED */" in sql:
                        success = True
                elif expected in sql:
                    success = True
                
                if success:
                    print("✓ Pattern Match: SUCCESS")
                else:
                    print(f"✗ Pattern Match: FAILED (Expected {expected})")
            else:
                print(f"✗ Translation Failed: {metrics.error}")
        except Exception as e:
            print(f"✗ Error: {str(e)}")
            
        print("-" * 40)

if __name__ == "__main__":
    test_phase_2_translations()
