"""
Fabric <-> Snowflake Sync Comparison Test

Compares the COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC model between:
1. Fabric (source semantic model)
2. Snowflake (target semantic view/tables)

Shows:
- Tables synced vs missing
- Dimensions synced vs missing
- Metrics synced vs missing
- Detailed delta report
"""

import sys
import json
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SyncComparison:
    """Results of comparison between Fabric and Snowflake"""
    fabric_tables: Set[str] = field(default_factory=set)
    snowflake_tables: Set[str] = field(default_factory=set)
    
    fabric_dimensions: Set[str] = field(default_factory=set)
    snowflake_dimensions: Set[str] = field(default_factory=set)
    
    fabric_metrics: Set[str] = field(default_factory=set)
    snowflake_metrics: Set[str] = field(default_factory=set)
    
    fabric_relationships: List[Dict[str, str]] = field(default_factory=list)
    snowflake_relationships: List[Dict[str, str]] = field(default_factory=list)
    
    # Columns per table
    fabric_columns: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    snowflake_columns: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))


class FabricSnowflakeSyncComparator:
    """Compares Fabric and Snowflake models"""
    
    def __init__(self, model_name: str = "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"):
        self.model_name = model_name
        try:
            self.settings = get_settings()
        except Exception as e:
            print(f"[ERROR] Failed to load settings: {e}")
            print("  Make sure .env file is configured with Fabric and Snowflake credentials")
            raise
        self.comparison = SyncComparison()
        
    def extract_fabric_model(self) -> Dict[str, Any]:
        """Extract semantic model from Fabric"""
        print(f"\n[FABRIC] EXTRACTING FROM FABRIC: {self.model_name}")
        print("=" * 80)
        
        try:
            extractor = FabricExtractor(self.settings.fabric)
            
            # Get model ID
            model_id = extractor.resolve_model_id(self.model_name)
            print(f"[OK] Model ID resolved: {model_id}")
            
            # Get TMSL definition
            tmsl = extractor.get_model_definition(model_id)
            print(f"[OK] TMSL extracted ({len(str(tmsl))} bytes)")
            
            return tmsl
            
        except Exception as e:
            print(f"[ERROR] Failed to extract from Fabric: {e}")
            logger.error(f"Fabric extraction error: {e}")
            return {}
    
    def parse_fabric_model(self, tmsl: Dict[str, Any]) -> None:
        """Parse Fabric TMSL to extract tables, dimensions, metrics"""
        print(f"\n[PARSING] PARSING FABRIC MODEL")
        print("=" * 80)
        
        try:
            if not tmsl:
                print("[ERROR] Empty TMSL definition")
                return
            
            model_obj = tmsl.get("model", {})
            
            # Extract tables
            if "tables" in model_obj:
                for table in model_obj["tables"]:
                    table_name = table.get("name", "")
                    
                    # Skip system tables
                    if table_name.startswith("LocalDateTable") or table_name.startswith("DateTableTemplate"):
                        continue
                    
                    self.comparison.fabric_tables.add(table_name)
                    
                    # Extract columns
                    columns = set()
                    if "columns" in table:
                        for col in table["columns"]:
                            col_name = col.get("name", "")
                            columns.add(col_name)
                    self.comparison.fabric_columns[table_name] = columns
                    
                    # Extract measures (metrics)
                    if "measures" in table:
                        for measure in table["measures"]:
                            measure_name = measure.get("name", "")
                            # Skip system measures
                            if not measure_name.startswith("_"):
                                metric_key = f"{table_name}.{measure_name}"
                                self.comparison.fabric_metrics.add(metric_key)
            
            # Extract relationships
            if "relationships" in model_obj:
                for rel in model_obj["relationships"]:
                    from_table = rel.get("fromTable", "")
                    to_table = rel.get("toTable", "")
                    
                    rel_info = {
                        "from": from_table,
                        "to": to_table,
                        "active": rel.get("isActive", True)
                    }
                    self.comparison.fabric_relationships.append(rel_info)
            
            print(f"[OK] Tables found: {len(self.comparison.fabric_tables)}")
            print(f"[OK] Metrics found: {len(self.comparison.fabric_metrics)}")
            print(f"[OK] Relationships found: {len(self.comparison.fabric_relationships)}")
            
            for table in sorted(self.comparison.fabric_tables):
                col_count = len(self.comparison.fabric_columns.get(table, set()))
                print(f"  • {table} ({col_count} columns)")
                
        except Exception as e:
            print(f"[ERROR] Failed to parse Fabric model: {e}")
            logger.error(f"Fabric parsing error: {e}", exc_info=True)
    
    def extract_snowflake_model(self) -> Dict[str, Any]:
        """Extract tables from Snowflake"""
        print(f"\n[SNOWFLAKE]  EXTRACTING FROM SNOWFLAKE")
        print("=" * 80)
        
        try:
            extractor = SnowflakeExtractor(self.settings.snowflake)
            
            # Test connection
            extractor.test_connection()
            print(f"[OK] Connected to Snowflake")
            
            # Extract metadata
            metadata = extractor.extract_all()
            print(f"[OK] Metadata extracted")
            
            # Also try to discover semantic views
            try:
                semantic_views = extractor.discover_semantic_views()
                if semantic_views:
                    print(f"[OK] Found {len(semantic_views)} semantic views")
                    metadata["semantic_views"] = semantic_views
            except Exception as e:
                print(f"  (Semantic views not available: {type(e).__name__})")
            
            return metadata
            
        except Exception as e:
            print(f"[ERROR] Failed to extract from Snowflake: {e}")
            logger.error(f"Snowflake extraction error: {e}", exc_info=True)
            return {}
    
    def parse_snowflake_model(self, metadata: Dict[str, Any]) -> None:
        """Parse Snowflake metadata"""
        print(f"\n[PARSING] PARSING SNOWFLAKE MODEL")
        print("=" * 80)
        
        try:
            # Extract tables
            if "tables" in metadata:
                for table_name, table_info in metadata["tables"].items():
                    self.comparison.snowflake_tables.add(table_name)
            
            # Extract columns
            if "columns" in metadata:
                for table_name, columns in metadata["columns"].items():
                    col_names = {col.get("name", "") for col in columns}
                    self.comparison.snowflake_columns[table_name] = col_names
            
            print(f"[OK] Tables found: {len(self.comparison.snowflake_tables)}")
            
            for table in sorted(self.comparison.snowflake_tables):
                col_count = len(self.comparison.snowflake_columns.get(table, set()))
                print(f"  • {table} ({col_count} columns)")
                
        except Exception as e:
            print(f"[ERROR] Failed to parse Snowflake model: {e}")
            logger.error(f"Snowflake parsing error: {e}")
    
    def generate_comparison_report(self) -> None:
        """Generate detailed comparison report"""
        print(f"\n[REPORT] SYNC COMPARISON REPORT")
        print("=" * 80)
        
        # Tables comparison
        print(f"\n[TABLES]  TABLES")
        print("-" * 80)
        fabric_only = self.comparison.fabric_tables - self.comparison.snowflake_tables
        snowflake_only = self.comparison.snowflake_tables - self.comparison.fabric_tables
        synced = self.comparison.fabric_tables & self.comparison.snowflake_tables
        
        print(f"[OK] Synced: {len(synced)}")
        for table in sorted(synced):
            fabric_cols = len(self.comparison.fabric_columns.get(table, set()))
            snowflake_cols = len(self.comparison.snowflake_columns.get(table, set()))
            match = "[OK]" if fabric_cols == snowflake_cols else "[WARNING]"
            print(f"  {match} {table}")
            if fabric_cols != snowflake_cols:
                print(f"     Fabric: {fabric_cols} cols | Snowflake: {snowflake_cols} cols")
        
        print(f"\n[ERROR] Only in Fabric (NOT synced): {len(fabric_only)}")
        for table in sorted(fabric_only):
            col_count = len(self.comparison.fabric_columns.get(table, set()))
            print(f"  [ERROR] {table} ({col_count} columns)")
        
        if snowflake_only:
            print(f"\n[WARNING]  Only in Snowflake (extra): {len(snowflake_only)}")
            for table in sorted(snowflake_only):
                col_count = len(self.comparison.snowflake_columns.get(table, set()))
                print(f"  [WARNING]  {table} ({col_count} columns)")
        
        # Columns per table comparison
        print(f"\n[COLUMNS] COLUMNS PER TABLE")
        print("-" * 80)
        for table in sorted(synced):
            fabric_cols = self.comparison.fabric_columns.get(table, set())
            snowflake_cols = self.comparison.snowflake_columns.get(table, set())
            
            cols_only_fabric = fabric_cols - snowflake_cols
            cols_only_snowflake = snowflake_cols - fabric_cols
            
            if cols_only_fabric or cols_only_snowflake:
                print(f"\n{table}:")
                if cols_only_fabric:
                    print(f"  [ERROR] Only in Fabric:")
                    for col in sorted(cols_only_fabric):
                        print(f"    • {col}")
                if cols_only_snowflake:
                    print(f"  [WARNING]  Only in Snowflake:")
                    for col in sorted(cols_only_snowflake):
                        print(f"    • {col}")
        
        # Metrics comparison
        print(f"\n[FABRIC] METRICS")
        print("-" * 80)
        print(f"[OK] Fabric metrics: {len(self.comparison.fabric_metrics)}")
        print(f"[OK] Snowflake metrics: {len(self.comparison.snowflake_metrics)}")
        
        if self.comparison.fabric_metrics:
            print(f"\n  Fabric metrics:")
            for metric in sorted(self.comparison.fabric_metrics):
                print(f"    • {metric}")
        
        # Relationships comparison
        print(f"\n[RELATIONSHIPS] RELATIONSHIPS")
        print("-" * 80)
        print(f"Fabric relationships: {len(self.comparison.fabric_relationships)}")
        for rel in self.comparison.fabric_relationships:
            status = "[ACTIVE]" if rel.get("active") else "[INACTIVE]"
            print(f"  {status} {rel['from']} -> {rel['to']}")
        
        # Summary
        print(f"\n[FABRIC] SUMMARY")
        print("=" * 80)
        total_fabric = len(self.comparison.fabric_tables)
        synced_count = len(synced)
        missing_count = len(fabric_only)
        sync_pct = (synced_count / total_fabric * 100) if total_fabric > 0 else 0
        
        print(f"Total Fabric tables: {total_fabric}")
        print(f"Synced tables: {synced_count} ({sync_pct:.1f}%)")
        print(f"Missing tables: {missing_count} ({100-sync_pct:.1f}%)")
        
        if fabric_only:
            print(f"\n[WARNING]  ACTION REQUIRED - Missing tables:")
            for i, table in enumerate(sorted(fabric_only), 1):
                col_count = len(self.comparison.fabric_columns.get(table, set()))
                print(f"  {i}. {table} ({col_count} columns)")
    
    def run_comparison(self) -> None:
        """Run full comparison"""
        print(f"\n{'='*80}")
        print(f"FABRIC <-> SNOWFLAKE SYNC COMPARISON")
        print(f"Model: {self.model_name}")
        print(f"{'='*80}")
        
        # Extract Fabric
        tmsl = self.extract_fabric_model()
        if tmsl:
            self.parse_fabric_model(tmsl)
        
        # Extract Snowflake
        metadata = self.extract_snowflake_model()
        if metadata:
            self.parse_snowflake_model(metadata)
        
        # Generate report
        self.generate_comparison_report()


def main():
    """Main entry point"""
    try:
        # You can change model name if needed
        model_name = "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"
        
        comparator = FabricSnowflakeSyncComparator(model_name=model_name)
        comparator.run_comparison()
        
    except Exception as e:
        print(f"\n[ERROR] COMPARISON FAILED: {e}")
        logger.error(f"Comparison failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
