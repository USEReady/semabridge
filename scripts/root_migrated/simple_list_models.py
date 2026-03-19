"""
List all Fabric semantic models and Snowflake tables for comparison
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor  
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

print("\n" + "="*80)
print("FABRIC SEMANTIC MODELS")
print("="*80)

try:
    settings = get_settings()
    extractor = FabricExtractor(settings.fabric)
    models = extractor.list_semantic_models()
    
    if models:
        print(f"\nFound {len(models)} models:\n")
        for model in models:
            print(f"ID: {model.get('id', 'N/A')}")
            print(f"Name: {model.get('displayName', 'N/A')}")
            desc = model.get('description', '')
            if desc:
                print(f"Description: {desc}")
            print()
    else:
        print("No models found")
except Exception as e:
    print(f"Error listing models: {e}")

print("\n" + "="*80)
print("SNOWFLAKE TABLES")
print("="*80)

try:
    settings = get_settings()
    extractor = SnowflakeExtractor(settings.snowflake)
    extractor.test_connection()
    
    metadata = extractor.extract_all()
    tables = metadata.get("tables", {})
    
    if tables:
        print(f"\nFound {len(tables)} tables:\n")
        for table_name in sorted(tables.keys())[:20]:
            col_count = len(metadata.get("columns", {}).get(table_name, []))
            print(f"- {table_name} ({col_count} columns)")
        if len(tables) > 20:
            print(f"... and {len(tables) - 20} more tables")
    else:
        print("No tables found")
except Exception as e:
    print(f"Error listing tables: {e}")

print("\n" + "="*80)
