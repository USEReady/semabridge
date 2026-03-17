#!/usr/bin/env python3
"""
Test the markdown sanitization fix
"""
import sys
import time

# Test markdown sanitization function
from src.semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from src.semabridge.core.settings import SnowflakeConfig

# Create a minimal config
config = SnowflakeConfig(
    account="test",
    user="test",
    password="test",
    database="test",
    schema_name="test",
    warehouse="test"
)

emitter = SnowflakeEmitter(config)

# Test cases
test_cases = [
    ("```sql\nSELECT SUM(amount)\n```", "SELECT SUM(amount)"),
    ("```\nSUM(amount)\n```", "SUM(amount)"),
    ("```python\nSUM(amount)\n```", "SUM(amount)"),
    ("SELECT SUM(amount)", "SELECT SUM(amount)"),  # Already clean
    ("  ```sql\n  SELECT * FROM table  \n  ```  ", "SELECT * FROM table"),  # With spaces
    ("", ""),  # Empty string
]

print("Testing markdown sanitization function...")
print("=" * 60)

all_passed = True
for input_sql, expected in test_cases:
    result = emitter._sanitize_sql_markdown(input_sql)
    passed = result == expected
    all_passed = all_passed and passed
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status}")
    print(f"  Input:    {repr(input_sql[:50])}")
    print(f"  Expected: {repr(expected[:50])}")
    print(f"  Got:      {repr(result[:50])}")
    print()

print("=" * 60)
if all_passed:
    print("✅ All markdown sanitization tests PASSED!")
    sys.exit(0)
else:
    print("❌ Some tests FAILED")
    sys.exit(1)
