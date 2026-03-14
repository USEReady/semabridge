from semabridge.core.settings import get_settings
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
import json

def debug_extraction():
    settings = get_settings()
    print(f"Connecting to Snowflake: {settings.snowflake.account}...")
    
    extractor = SnowflakeExtractor(config=settings.snowflake)
    metadata = extractor.extract_all()
    
    print("\n[Raw Snowflake Tables]")
    tables = list(metadata.get("tables", {}).keys())
    for t in tables[:10]:
        print(f" - {t}")
        
    print(f"\nTotal Tables: {len(tables)}")

if __name__ == "__main__":
    debug_extraction()
