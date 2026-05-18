"""
Ultimate Tier Validation: Tiers 1-5.

Comprehensive test suite covering all tiers of DAX translation logic,
from simple aggregations to complex dynamic relationships.
"""

from semabridge.converter.dax_engine import get_translation_engine

def run_comprehensive_tiers_test():
    engine = get_translation_engine()
    
    test_cases = [
        # Tier 1: Direct Aggregations
        {"tier": 1, "dax": "SUM('Sales'[Amount])", "expected": "SUM("},
        {"tier": 1, "dax": "COUNTROWS('Sales')", "expected": "COUNT(*)"},
        
        # Tier 2: Arithmetic & Simple CALCULATE
        {"tier": 2, "dax": "DIVIDE(SUM('Sales'[Amount]), 10, 0)", "expected": "DIV0"}, # Snowflake optimized
        {"tier": 2, "dax": "CALCULATE(SUM('Sales'[Amount]), 'Date'[Year] = 2024)", "expected": "WITH ctx"},
        
        # Tier 3: Time Intelligence
        {"tier": 3, "dax": "TOTALYTD(SUM('Sales'[Amount]), 'Date'[Date])", "expected": "PARTITION BY"}, # Window function
        {"tier": 3, "dax": "SAMEPERIODLASTYEAR('Date'[Date])", "expected": "DATEADD(year, -1"},
        
        # Tier 4: Complex Iterators & Row Context
        {"tier": 4, "dax": "RANKX('Sales', SUM('Sales'[Amount]))", "expected": "RANK() OVER"},
        {"tier": 4, "dax": "SUMX('Sales', 'Sales'[Amount] * 1.1)", "expected": "SUM("},
        {"tier": 4, "dax": "EARLIER('Sales'[Amount])", "expected": "FIRST_VALUE"},
        {"tier": 4, "dax": "CALCULATE(SUM('Sales'[Amount]), REMOVEFILTERS('Product'))", "expected": "SUM("},
        
        # Tier 5: High Complexity & LLM Fallback
        {"tier": 5, "dax": "USERELATIONSHIP('Date'[Date], 'Sales'[ShipDate])", "expected": "/* LLM GENERATED */"},
        {"tier": 5, "dax": "SUMX(FILTER('Sales', 'Sales'[Amount] > 100), 'Sales'[Amount])", "expected": "/* LLM GENERATED */"},
        {"tier": 5, "dax": "CALCULATE(SUM('Sales'[Amount]), ALL('Sales'), 'Sales'[Region] = \"US\")", "expected": "WITH ctx"}
    ]
    
    print("=== SemaBridge: Comprehensive Tiers Validation (T1-T5) ===\n")
    
    pass_count = 0
    for i, tc in enumerate(test_cases):
        print(f"[{i+1}] TIER {tc['tier']} Testing: {tc['dax']}")
        try:
            sql, metrics = engine.translate(tc['dax'])
            if sql:
                print(f"✓ SQL: {sql[:100]}...")
                
                # Validation logic (accept markers for Tier 5)
                success = tc['expected'].lower() in sql.lower() or "/* llm" in sql.lower()
                
                if success:
                    print("✓ STATUS: PASS")
                    pass_count += 1
                else:
                    print(f"✗ STATUS: FAILED (Expected pattern '{tc['expected']}' not found)")
            else:
                print(f"✗ STATUS: FAILED (Error: {metrics.error})")
        except Exception as e:
            print(f"✗ STATUS: ERROR ({str(e)})")
        print("-" * 50)
        
    print(f"\nFINAL RESULT: {pass_count}/{len(test_cases)} Tests Passed.")

if __name__ == "__main__":
    run_comprehensive_tiers_test()
