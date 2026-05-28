import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.converter.multi_model_translator import get_multi_model_translator

def test_translation():
    print("Initializing MultiModelDAXTranslator...")
    translator = get_multi_model_translator()
    
    dax_expr = "CALCULATE(SUM('Sales'[Amount]), 'Product'[Category] = \"Audio\")"
    metric_name = "Total_Audio_Sales"
    
    prompt = f"""
You are a DAX to Snowflake SQL translator.

Metric name: {metric_name}
DAX: {dax_expr}

Rules:
- Return ONLY the SQL expression, no explanation
- No SELECT, FROM, JOIN, or subqueries
- Use SUM(CASE WHEN ... THEN ... ELSE 0 END) for filtered aggregations
- Use COALESCE(expr / NULLIF(denom, 0), 0) for division

SQL:
"""
    print(f"Translating: {dax_expr}")
    sql = translator.translate_with_failover(
        dax=dax_expr,
        metric_name=metric_name,
        prompt=prompt
    )
    
    print("\nResult:")
    print(sql)

if __name__ == "__main__":
    test_translation()
