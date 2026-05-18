"""
LLM Flow Diagnostic Script.

Verifies that the LLM fallback is correctly triggered for 
complex Tier 5 patterns.
"""

from semabridge.converter.dax_engine import DaxTranslationEngine
import logging

# Setup logging to see the fallback warnings
logging.basicConfig(level=logging.INFO)

def check_llm_flow():
    engine = DaxTranslationEngine(cache_enabled=False)
    
    # This case is guaranteed to fail deterministic translation
    # and trigger LLM fallback
    dax = "CALCULATE(SUM('Sales'[Amount]), USERELATIONSHIP('Date'[Date], 'Sales'[ShipDate]))"
    
    print(f"Testing LLM Flow for DAX: {dax}\n")
    
    sql, metrics = engine.translate(dax)
    
    print(f"Strategy Used: {metrics.strategy.value}")
    print(f"Confidence: {metrics.confidence}")
    
    if sql and "LLM" in sql:
        print(f"\n✅ LLM is WORKING. Result received:\n{sql}")
    else:
        print(f"\n❌ LLM Flow FAILED. Result:\n{sql}")

if __name__ == "__main__":
    check_llm_flow()
