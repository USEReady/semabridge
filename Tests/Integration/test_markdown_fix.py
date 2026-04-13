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

def test_markdown_sanitization():
    # Test cases
    test_cases = [
        ("```sql\nSELECT SUM(amount)\n```", "SELECT SUM(amount)"),
        ("```\nSUM(amount)\n```", "SUM(amount)"),
        ("```python\nSUM(amount)\n```", "SUM(amount)"),
        ("SELECT SUM(amount)", "SELECT SUM(amount)"),  # Already clean
        ("  ```sql\n  SELECT * FROM table  \n  ```  ", "SELECT * FROM table"),  # With spaces
        ("", ""),  # Empty string
    ]

    for input_sql, expected in test_cases:
        result = emitter._sanitize_sql_markdown(input_sql)
        assert result == expected, f"Failed for {input_sql}: Expected {expected}, got {result}"
