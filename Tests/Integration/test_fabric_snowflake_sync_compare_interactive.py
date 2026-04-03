"""
Fabric -> Snowflake Sync Comparison (Simplified)

Lists available models and compares them between Fabric and Snowflake
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def list_fabric_models():
    """List all semantic models in Fabric"""
    print("\n" + "="*80)
    print("[FABRIC] AVAILABLE SEMANTIC MODELS")
    print("="*80)
    
    try:
        settings = get_settings()
        extractor = FabricExtractor(settings.fabric)
        
        models = extractor.list_semantic_models()
        
        if not models:
            print("[INFO] No models found")
            return
        
        print(f"[OK] Found {len(models)} models:\n")
        
        for i, model in enumerate(models, 1):
            name = model.get("displayName", "N/A")
            model_id = model.get("id", "N/A")
            desc = model.get("description", "")
            print(f"{i}. {name}")
            print(f"   ID: {model_id}")
            if desc:
                print(f"   Desc: {desc}")
            print()
        
        return models
        
    except Exception as e:
        print(f"[ERROR] Failed to list Fabric models: {e}")
        logger.error(f"Fabric error: {e}", exc_info=True)
        return []


def list_snowflake_tables():
    """List all tables in Snowflake"""
    print("\n" + "="*80)
    print("[SNOWFLAKE] AVAILABLE TABLES")
    print("="*80)
    
    try:
        settings = get_settings()
        extractor = SnowflakeExtractor(settings.snowflake)
        
        # Test connection
        extractor.test_connection()
        print(f"[OK] Connected to database: {settings.snowflake.database}.{settings.snowflake.schema_name}\n")
        
        # Extract metadata
        metadata = extractor.extract_all()
        
        tables = metadata.get("tables", {})
        if not tables:
            print("[INFO] No tables found")
            return metadata
        
        print(f"[OK] Found {len(tables)} tables:\n")
        
        for table_name, table_info in sorted(tables.items()):
            col_count = len(metadata.get("columns", {}).get(table_name, []))
            row_count = table_info.get("row_count", "N/A")
            print(f"- {table_name}")
            print(f"  Columns: {col_count}, Rows: {row_count}")
        
        print()
        return metadata
        
    except Exception as e:
        print(f"[ERROR] Failed to list Snowflake tables: {e}")
        logger.error(f"Snowflake error: {e}", exc_info=True)
        return {}


def compare_model(model_id: str, model_name: str):
    """Compare a Fabric model with Snowflake"""
    print("\n" + "="*80)
    print(f"[COMPARISON] {model_name}")
    print("="*80)
    
    try:
        settings = get_settings()
        fabric_extractor = FabricExtractor(settings.fabric)
        
        # Extract Fabric model TMSL
        print(f"\n[EXTRACTING] Fabric model: {model_name}")
        tmsl = fabric_extractor.get_model_definition(model_id)
        
        if not tmsl:
            print("[ERROR] Empty TMSL definition")
            return
        
        # Parse Fabric TMSL
        print("[PARSING] Fabric model...")
        model_obj = tmsl.get("model", {})
        
        fabric_tables = set()
        fabric_columns = {}
        fabric_metrics = []
        
        if "tables" in model_obj:
            for table in model_obj["tables"]:
                table_name = table.get("name", "")
                
                # Skip system tables
                if table_name.startswith(("LocalDateTable", "DateTableTemplate", "_")):
                    continue
                
                fabric_tables.add(table_name)
                
                # Columns
                columns = set()
                if "columns" in table:
                    for col in table["columns"]:
                        columns.add(col.get("name", ""))
                fabric_columns[table_name] = columns
                
                # Metrics
                if "measures" in table:
                    for measure in table["measures"]:
                        measure_name = measure.get("name", "")
                        if not measure_name.startswith("_"):
                            fabric_metrics.append(f"{table_name}.{measure_name}")
        
        print(f"[OK] Parsed {len(fabric_tables)} tables, {len(fabric_metrics)} metrics")
        
        # Extract Snowflake tables
        print("\n[EXTRACTING] Snowflake metadata...")
        sf_extractor = SnowflakeExtractor(settings.snowflake)
        metadata = sf_extractor.extract_all()
        
        snowflake_tables = set(metadata.get("tables", {}).keys())
        snowflake_columns = metadata.get("columns", {})
        
        print(f"[OK] Found {len(snowflake_tables)} tables")
        
        # Compare
        print("\n" + "="*80)
        print("[RESULTS]")
        print("="*80)
        
        synced = fabric_tables & snowflake_tables
        missing = fabric_tables - snowflake_tables
        extra = snowflake_tables - fabric_tables
        
        print(f"\n[TABLES]")
        print(f"  Total in Fabric: {len(fabric_tables)}")
        print(f"  Total in Snowflake: {len(snowflake_tables)}")
        print(f"  Synced: {len(synced)} ({len(synced)/len(fabric_tables)*100:.1f}%)")
        print(f"  Missing: {len(missing)}")
        print(f"  Extra: {len(extra)}")
        
        if synced:
            print(f"\n[SYNCED TABLES]:")
            for table in sorted(synced):
                f_cols = len(fabric_columns.get(table, set()))
                s_cols = len(snowflake_columns.get(table, []))
                match = "[OK]" if f_cols == s_cols else "[MISMATCH]"
                print(f"  {match} {table} (Fabric: {f_cols} cols, Snowflake: {s_cols} cols)")
        
        if missing:
            print(f"\n[NOT SYNCED - IN FABRIC ONLY]:")
            for table in sorted(missing):
                col_count = len(fabric_columns.get(table, set()))
                print(f"  [MISSING] {table} ({col_count} columns)")
        
        if extra:
            print(f"\n[EXTRA IN SNOWFLAKE]:")
            for table in sorted(extra):
                print(f"  [EXTRA] {table}")
        
        # Column details for synced tables
        print(f"\n[COLUMN DETAILS]")
        for table in sorted(synced):
            f_cols = fabric_columns.get(table, set())
            s_cols = set(col.get("name", "") for col in snowflake_columns.get(table, []))
            
            cols_only_fabric = f_cols - s_cols
            cols_only_snowflake = s_cols - f_cols
            
            if cols_only_fabric or cols_only_snowflake:
                print(f"\n{table}:")
                if cols_only_fabric:
                    print(f"  [NOT SYNCED] Only in Fabric:")
                    for col in sorted(cols_only_fabric):
                        print(f"    - {col}")
                if cols_only_snowflake:
                    print(f"  [EXTRA] Only in Snowflake:")
                    for col in sorted(cols_only_snowflake):
                        print(f"    - {col}")
        
        # Metrics
        if fabric_metrics:
            print(f"\n[METRICS] {len(fabric_metrics)} found in Fabric:")
            for metric in sorted(fabric_metrics)[:10]:  # Show first 10
                print(f"  - {metric}")
            if len(fabric_metrics) > 10:
                print(f"  ... and {len(fabric_metrics) - 10} more")
        
    except Exception as e:
        print(f"[ERROR] Comparison failed: {e}")
        logger.error(f"Comparison error: {e}", exc_info=True)


def main():
    """Main menu"""
    print("\n" + "="*80)
    print("FABRIC <-> SNOWFLAKE SYNC COMPARISON TOOL")
    print("="*80)
    
    try:
        # List Fabric models
        fabric_models = list_fabric_models()
        
        if not fabric_models:
            print("[ERROR] No Fabric models available")
            return
        
        # Ask user to select
        while True:
            try:
                choice = input("\nSelect a model to compare (number) or 'q' to quit: ").strip()
                if choice.lower() == 'q':
                    print("Goodbye!")
                    return
                
                idx = int(choice) - 1
                if 0 <= idx < len(fabric_models):
                    selected = fabric_models[idx]
                    model_id = selected.get("id")
                    model_name = selected.get("displayName")
                    break
                else:
                    print("[ERROR] Invalid selection")
            except ValueError:
                print("[ERROR] Please enter a number")
        
        # List Snowflake tables
        list_snowflake_tables()
        
        # Compare
        compare_model(model_id, model_name)
        
    except KeyboardInterrupt:
        print("\n\nInterrupted!")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
