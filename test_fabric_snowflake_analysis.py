"""
Fabric > Snowflake Sync Comparison - Quick Report

Usage:
  python test_fabric_snowflake_analysis.py <fabric_model_id> [model_name]

Example:
  python test_fabric_snowflake_analysis.py "abc-123-def-456" "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
  
Or list models first:
  python test_fabric_snowflake_analysis.py --list
"""

import sys
import json
from pathlib import Path
from typing import Dict, Set

sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def list_models():
    """List all available Fabric models"""
    print("\n" + "="*80)
    print("AVAILABLE FABRIC SEMANTIC MODELS")
    print("="*80)
    
    try:
        settings = get_settings()
        extractor = FabricExtractor(settings.fabric)
        
        models = extractor.list_semantic_models()
        
        if not models:
            print("[INFO] No models found")
            return
        
        print(f"\n[OK] Found {len(models)} models:\n")
        
        for i, model in enumerate(models, 1):
            name = model.get("displayName", "N/A")
            model_id = model.get("id", "N/A")
            desc = model.get("description", "")
            print(f"{i}. {name}")
            print(f"   ID: {model_id}")
            if desc:
                print(f"   Description: {desc}")
            print()
        
        print("\nTo compare a model, run:")
        print("  python test_fabric_snowflake_analysis.py <MODEL_ID> \"<MODEL_NAME>\"\n")
        
    except Exception as e:
        print(f"[ERROR] {e}")
        logger.error(f"Error: {e}", exc_info=True)


def analyze_sync(fabric_model_id: str, model_name: str = None):
    """Analyze sync between Fabric model and Snowflake"""
    
    if model_name is None:
        model_name = f"Model {fabric_model_id[:8]}..."
    
    print("\n" + "="*80)
    print(f"FABRIC <-> SNOWFLAKE SYNC ANALYSIS")
    print(f"Model: {model_name}")
    print(f"ModelID: {fabric_model_id}")
    print("="*80)
    
    try:
        settings = get_settings()
        
        # Extract Fabric model
        print(f"\n[STEP 1/3] Extracting Fabric model TMSL...")
        fabric_extractor = FabricExtractor(settings.fabric)
        tmsl = fabric_extractor.get_model_definition(fabric_model_id)
        
        if not tmsl:
            print("[ERROR] Failed to get Fabric model definition")
            return
        
        print("[OK] TMSL extracted")
        
        # Parse Fabric model
        print(f"\n[STEP 2/3] Parsing Fabric model structure...")
        model_obj = tmsl.get("model", {})
        
        fabric_tables: Dict[str, Set[str]] = {}
        fabric_metrics = []
        fabric_relationships = []
        
        # Extract tables
        if "tables" in model_obj:
            for table in model_obj["tables"]:
                table_name = table.get("name", "").lower()
                
                # Skip system/internal tables
                if table_name.startswith(("localdatetable", "datetabletemplate", "_")):
                    continue
                
                # Columns
                columns = set()
                if "columns" in table:
                    for col in table["columns"]:
                        col_name = col.get("name", "").lower()
                        columns.add(col_name)
                fabric_tables[table_name] = columns
                
                # Metrics/Measures
                if "measures" in table:
                    for measure in table["measures"]:
                        measure_name = measure.get("name", "")
                        if not measure_name.startswith("_"):
                            fabric_metrics.append(f"{table_name}.{measure_name.lower()}")
        
        # Relationships
        if "relationships" in model_obj:
            for rel in model_obj["relationships"]:
                fabric_relationships.append({
                    "from": rel.get("fromTable", "").lower(),
                    "to": rel.get("toTable", "").lower(),
                    "active": rel.get("isActive", True)
                })
        
        print(f"[OK] Parsed {len(fabric_tables)} tables, {len(fabric_metrics)} metrics, {len(fabric_relationships)} relationships")
        
        # Extract Snowflake metadata
        print(f"\n[STEP 3/3] Extracting Snowflake metadata...")
        sf_extractor = SnowflakeExtractor(settings.snowflake)
        sf_extractor.test_connection()
        metadata = sf_extractor.extract_all()
        
        snowflake_tables = {table_name.lower() for table_name in metadata.get("tables", {}).keys()}
        snowflake_columns = {}
        
        for table_name, cols_list in metadata.get("columns", {}).items():
            normalized_table_name = table_name.lower()
            snowflake_columns[normalized_table_name] = {col.get("name", "").lower() for col in cols_list}
        
        print(f"[OK] Found {len(snowflake_tables)} tables in Snowflake")
        
        # Comparison
        print("\n" + "="*80)
        print("COMPARISON RESULTS")
        print("="*80)
        
        synced_tables = set(fabric_tables.keys()) & snowflake_tables
        missing_tables = set(fabric_tables.keys()) - snowflake_tables
        extra_tables = snowflake_tables - set(fabric_tables.keys())
        
        # Summary stats
        total_fabric = len(fabric_tables)
        sync_count = len(synced_tables)
        sync_pct = (sync_count / total_fabric * 100) if total_fabric > 0 else 0
        
        print(f"\n[SUMMARY]")
        print(f"  Fabric Tables:     {total_fabric}")
        print(f"  Synced:            {sync_count} ({sync_pct:.1f}%)")
        print(f"  NOT Synced:        {len(missing_tables)}")
        print(f"  Extra in SF:       {len(extra_tables)}")
        print(f"  Metrics:           {len(fabric_metrics)}")
        print(f"  Relationships:     {len(fabric_relationships)}")
        
        # Synced tables
        if synced_tables:
            print(f"\n[SYNCED TABLES] ({sync_count})")
            print("-" * 80)
            for table in sorted(synced_tables):
                f_cols = len(fabric_tables.get(table, set()))
                s_cols = len(snowflake_columns.get(table, set()))
                match = "OK" if f_cols == s_cols else "MISMATCH"
                print(f"  [{match}] {table:40s} Fabric:{f_cols:3d} cols  Snowflake:{s_cols:3d} cols")
                
                # Show column diffs
                if f_cols != s_cols:
                    f_cols_set = fabric_tables.get(table, set())
                    s_cols_set = snowflake_columns.get(table, set())
                    only_fabric = f_cols_set - s_cols_set
                    only_sf = s_cols_set - f_cols_set
                    
                    if only_fabric:
                        print(f"       [NOT IN SF] {', '.join(sorted(only_fabric)[:3])}{' ...' if len(only_fabric) > 3 else ''}")
                    if only_sf:
                        print(f"       [EXTRA IN SF] {', '.join(sorted(only_sf)[:3])}{' ...' if len(only_sf) > 3 else ''}")
        
        # Missing tables
        if missing_tables:
            print(f"\n[NOT SYNCED - IN FABRIC ONLY] ({len(missing_tables)})")
            print("-" * 80)
            for table in sorted(missing_tables):
                col_count = len(fabric_tables.get(table, set()))
                print(f"  [MISSING] {table:40s} {col_count:3d} columns")
        
        # Extra tables
        if extra_tables:
            print(f"\n[EXTRA IN SNOWFLAKE] ({len(extra_tables)})")
            print("-" * 80)
            for table in sorted(extra_tables):
                col_count = len(snowflake_columns.get(table, set()))
                print(f"  [EXTRA]   {table:40s} {col_count:3d} columns")
        
        # Metrics list
        if fabric_metrics:
            print(f"\n[METRICS] ({len(fabric_metrics)})")
            print("-" * 80)
            for metric in sorted(fabric_metrics)[:10]:
                print(f"  - {metric}")
            if len(fabric_metrics) > 10:
                print(f"  ... and {len(fabric_metrics) - 10} more metrics")
        
        # Relationships
        if fabric_relationships:
            print(f"\n[RELATIONSHIPS] ({len(fabric_relationships)})")
            print("-" * 80)
            for rel in fabric_relationships:
                status = "ACTIVE" if rel.get("active") else "INACTIVE"
                print(f"  [{status}] {rel['from']:30s} -> {rel['to']}")
        
        # Summary report
        print("\n" + "="*80)
        print("ACTION ITEMS")
        print("="*80)
        
        if missing_tables:
            print(f"\n1. SYNC MISSING TABLES ({len(missing_tables)} tables)")
            for i, table in enumerate(sorted(missing_tables), 1):
                print(f"   {i}. {table}")
        
        column_mismatches = []
        for table in synced_tables:
            if len(fabric_tables[table]) != len(snowflake_columns.get(table, set())):
                column_mismatches.append(table)
        
        if column_mismatches:
            print(f"\n2. FIX COLUMN MISMATCHES ({len(column_mismatches)} tables)")
            for i, table in enumerate(sorted(column_mismatches), 1):
                f_cols = len(fabric_tables[table])
                s_cols = len(snowflake_columns.get(table, set()))
                print(f"   {i}. {table} (Fabric: {f_cols} cols, Snowflake: {s_cols} cols)")
        
        if not missing_tables and not column_mismatches:
            print("\n[SUCCESS] All Fabric tables and columns are synced to Snowflake!")
        
        print("\n" + "="*80 + "\n")
        
    except Exception as e:
        print(f"\n[ERROR] Analysis failed: {e}")
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)


def main():
    """Main entry point"""
    
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nUsage Options:")
        print("  1. List models:    python test_fabric_snowflake_analysis.py --list")
        print("  2. Analyze model:  python test_fabric_snowflake_analysis.py <MODEL_ID>")
        print("  3. With name:      python test_fabric_snowflake_analysis.py <MODEL_ID> \"Model Name\"\n")
        list_models()
        return
    
    arg = sys.argv[1]
    
    if arg.lower() == "--list":
        list_models()
    else:
        # Analyze model
        model_id = arg
        model_name = sys.argv[2] if len(sys.argv) > 2 else None
        analyze_sync(model_id, model_name)


if __name__ == "__main__":
    main()
