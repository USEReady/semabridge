"""
Phase 3: Bidirectional Sync Validation Script.

Simulates change detection in Snowflake and reverse translation 
back to Fabric DAX measures.
"""

from semabridge.bidirectional.snowflake_model_monitor import SnowflakeModelMonitor
from semabridge.bidirectional.reverse_translator import ReverseTranslator
import json

def test_bidirectional_sync():
    print("=== Phase 3: Bidirectional Sync Validation ===\n")
    
    # 1. Simulate States
    last_known_state = {
        "views": {
            "SALES_TOTAL": "SELECT SUM(AMOUNT) FROM FACT"
        }
    }
    
    # New state detected in Snowflake (e.g., someone added a 10% tax adjustment)
    current_snowflake_state = {
        "views": {
            "SALES_TOTAL": "SELECT SUM(AMOUNT * 1.1) FROM FACT",
            "NEW_MARGIN": "SELECT AVG(MARGIN) FROM FACT"
        }
    }
    
    # 2. Detect Changes
    monitor = SnowflakeModelMonitor()
    changes = monitor.detect_changes(current_snowflake_state, last_known_state)
    
    print(f"Detected {len(changes)} changes in Snowflake metadata.\n")
    
    # 3. Reverse Translate to DAX
    rev_translator = ReverseTranslator()
    
    for change in changes:
        print(f"Change Type: {change.change_type.upper()}")
        print(f"Snowflake Object: {change.object_name}")
        print(f"Snowflake SQL: {change.new_definition}")
        
        # Convert back to DAX
        dax_measure = rev_translator.translate_to_dax(change.new_definition)
        
        print(f"Generated DAX Measure: {dax_measure}")
        print("-" * 40)

if __name__ == "__main__":
    test_bidirectional_sync()
