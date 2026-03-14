"""
Fabric Notebook: SemaBridge Measure Sync

Reusable module for synchronizing DAX measures from Fabric Semantic Models to Snowflake
using Semantic Link (SemPy). This notebook can be imported as a module or run standalone.

Deploy this notebook to Microsoft Fabric and call the sync functions from your pipelines.

Requirements:
    - Microsoft Fabric workspace with Semantic Link enabled
    - Snowflake account with write access
    - sempy, snowflake-connector-python packages

Usage:
    # Import and use as module
    from semabridge_fabric_notebook import MeasureSyncPipeline
    
    pipeline = MeasureSyncPipeline(
        workspace_id="your-workspace-id",
        dataset_name="Your Semantic Model",
        snowflake_config={...}
    )
    results = pipeline.sync_all_measures()

Author: SemaBridge Team
Version: 1.0.0
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("semabridge.fabric")


# =============================================================================
# Configuration Classes
# =============================================================================

@dataclass
class SnowflakeConfig:
    """Snowflake connection configuration."""
    account: str
    user: str
    password: str
    warehouse: str
    database: str
    schema_name: str
    role: str = "SYSADMIN"

@dataclass
class MeasureConfig:
    """Configuration for a single measure sync."""
    name: str
    group_by_dimensions: list[str] = field(default_factory=list)
    partition_dimension: Optional[str] = None
    partition_values: Optional[list] = None
    use_fallback_pattern: bool = False
    filters: dict[str, Any] = field(default_factory=dict)

@dataclass 
class SyncResult:
    """Result of a measure sync operation."""
    measure_name: str
    status: str  # "success", "failed", "skipped", "empty"
    rows: int = 0
    error: Optional[str] = None
    duration_sec: float = 0.0


# =============================================================================
# Core Sync Engine
# =============================================================================

class MeasureSyncPipeline:
    """
    Main pipeline for syncing DAX measures from Fabric to Snowflake.
    
    Uses Semantic Link (SemPy) for DAX evaluation which provides:
    - No API row limits (unlike REST API's 100k limit)
    - Direct Spark DataFrame output
    - Better error handling and debugging
    - Access to DirectLake mode
    """
    
    def __init__(
        self,
        workspace_id: str,
        dataset_name: str,
        snowflake_config: SnowflakeConfig,
        default_dimensions: list[str] = None,
    ):
        """
        Initialize the sync pipeline.
        
        Args:
            workspace_id: Fabric workspace GUID
            dataset_name: Name of the semantic model
            snowflake_config: Snowflake connection details
            default_dimensions: Default dimensions for measure evaluation
        """
        self.workspace_id = workspace_id
        self.dataset_name = dataset_name
        self.sf_config = snowflake_config
        self.default_dimensions = default_dimensions or ["'Date'[Year]"]
        
        # Lazy-loaded connections
        self._fabric = None
        self._spark = None
        
    @property
    def fabric(self):
        """Lazy-load Fabric/SemPy module."""
        if self._fabric is None:
            try:
                import sempy.fabric as fabric
                self._fabric = fabric
                logger.info("Semantic Link (sempy) loaded successfully")
            except ImportError:
                raise ImportError(
                    "sempy not available. This notebook must run in Microsoft Fabric. "
                    "Install with: pip install semantic-link"
                )
        return self._fabric
    
    @property
    def spark(self):
        """Get Spark session."""
        if self._spark is None:
            try:
                from pyspark.sql import SparkSession
                self._spark = SparkSession.builder.getOrCreate()
            except:
                self._spark = None
        return self._spark
    
    # =========================================================================
    # DAX Evaluation Methods
    # =========================================================================
    
    def evaluate_measure(
        self,
        measure_config: MeasureConfig,
    ) -> "DataFrame":
        """
        Evaluate a DAX measure using Semantic Link.
        
        Uses SUMMARIZECOLUMNS by default, falls back to ADDCOLUMNS(SUMMARIZE())
        pattern if context transition errors occur.
        
        Returns:
            Spark DataFrame with dimension columns + Value column
        """
        dimensions = measure_config.group_by_dimensions or self.default_dimensions
        dim_string = ", ".join(dimensions)
        measure_ref = f"[{measure_config.name}]"
        
        if measure_config.use_fallback_pattern:
            # Fallback pattern using ADDCOLUMNS + SUMMARIZE + CALCULATE
            base_table = dimensions[0].split('[')[0].strip("'") if dimensions else "Date"
            dax_query = f"""
EVALUATE
ADDCOLUMNS(
    SUMMARIZE('{base_table}', {dim_string}),
    "Value", CALCULATE({measure_ref})
)
"""
        else:
            # Standard SUMMARIZECOLUMNS pattern
            dax_query = f"""
EVALUATE
SUMMARIZECOLUMNS(
    {dim_string},
    "Value", {measure_ref}
)
"""
        
        # Add filters if specified
        if measure_config.filters:
            filter_clauses = []
            for col, val in measure_config.filters.items():
                if isinstance(val, str):
                    filter_clauses.append(f'{col} = "{val}"')
                else:
                    filter_clauses.append(f'{col} = {val}')
            
            if filter_clauses:
                filter_str = " && ".join(filter_clauses)
                # Inject filter into query
                dax_query = dax_query.replace(
                    "SUMMARIZECOLUMNS(",
                    f"SUMMARIZECOLUMNS(FILTER(ALL({dimensions[0]}), {filter_str}), "
                )
        
        logger.debug(f"Executing DAX: {dax_query[:200]}...")
        
        try:
            # Use SemPy's evaluate_dax for direct Spark DataFrame output
            df = self.fabric.evaluate_dax(
                dataset=self.dataset_name,
                dax_string=dax_query,
                workspace=self.workspace_id
            )
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # Check for context transition errors - retry with fallback
            if not measure_config.use_fallback_pattern and (
                "summarizecolumns" in error_msg or 
                "context" in error_msg or
                "cannot be used" in error_msg
            ):
                logger.warning(f"Context error for {measure_config.name}, using fallback pattern")
                fallback_config = MeasureConfig(
                    name=measure_config.name,
                    group_by_dimensions=measure_config.group_by_dimensions,
                    use_fallback_pattern=True,
                    filters=measure_config.filters,
                )
                return self.evaluate_measure(fallback_config)
            
            raise
    
    def evaluate_measure_partitioned(
        self,
        measure_config: MeasureConfig,
    ) -> "DataFrame":
        """
        Evaluate a measure with partitioning for large result sets.
        
        Splits the query by partition_dimension values and unions results.
        Useful for Time Intelligence measures or very large datasets.
        
        Returns:
            Combined Spark DataFrame
        """
        if not measure_config.partition_dimension:
            return self.evaluate_measure(measure_config)
        
        # Get partition values if not provided
        partition_vals = measure_config.partition_values
        if not partition_vals:
            partition_vals = self._get_dimension_values(measure_config.partition_dimension)
        
        if not partition_vals:
            logger.warning(f"No partition values found, falling back to single query")
            return self.evaluate_measure(measure_config)
        
        logger.info(f"Partitioning {measure_config.name} by {len(partition_vals)} values")
        
        combined_df = None
        for val in partition_vals:
            partition_config = MeasureConfig(
                name=measure_config.name,
                group_by_dimensions=measure_config.group_by_dimensions,
                use_fallback_pattern=measure_config.use_fallback_pattern,
                filters={**measure_config.filters, measure_config.partition_dimension: val}
            )
            
            try:
                df = self.evaluate_measure(partition_config)
                if combined_df is None:
                    combined_df = df
                else:
                    combined_df = combined_df.union(df)
                    
            except Exception as e:
                logger.error(f"Partition {val} failed: {e}")
                raise
        
        return combined_df
    
    def _get_dimension_values(self, dimension: str) -> list:
        """Get distinct values from a dimension for partitioning."""
        dax = f"""
EVALUATE
DISTINCT({dimension})
ORDER BY {dimension}
"""
        try:
            df = self.fabric.evaluate_dax(
                dataset=self.dataset_name,
                dax_string=dax,
                workspace=self.workspace_id
            )
            col_name = dimension.split('[')[1].rstrip(']')
            return [row[col_name] for row in df.collect()]
        except Exception as e:
            logger.warning(f"Failed to get dimension values: {e}")
            return []
    
    # =========================================================================
    # Snowflake Write Methods
    # =========================================================================
    
    def write_to_snowflake(
        self,
        df: "DataFrame",
        table_name: str,
        mode: str = "overwrite"
    ) -> int:
        """
        Write a Spark DataFrame to Snowflake.
        
        Uses the Snowflake Spark Connector for efficient bulk loading.
        
        Args:
            df: Spark DataFrame to write
            table_name: Target table name (will be prefixed with MEASURES_)
            mode: "overwrite" or "append"
            
        Returns:
            Number of rows written
        """
        safe_table = f"MEASURES_{table_name.upper().replace(' ', '_')}"
        
        # Configure Snowflake options
        sf_options = {
            "sfURL": f"{self.sf_config.account}.snowflakecomputing.com",
            "sfUser": self.sf_config.user,
            "sfPassword": self.sf_config.password,
            "sfDatabase": self.sf_config.database,
            "sfSchema": self.sf_config.schema_name,
            "sfWarehouse": self.sf_config.warehouse,
            "sfRole": self.sf_config.role,
            "dbtable": safe_table,
        }
        
        row_count = df.count()
        
        logger.info(f"Writing {row_count} rows to {self.sf_config.database}.{self.sf_config.schema_name}.{safe_table}")
        
        df.write \
            .format("snowflake") \
            .options(**sf_options) \
            .mode(mode) \
            .save()
        
        logger.info(f"Successfully wrote {row_count} rows to Snowflake")
        return row_count
    
    # =========================================================================
    # High-Level Sync Methods
    # =========================================================================
    
    def sync_measure(
        self,
        measure_config: MeasureConfig,
        write_mode: str = "overwrite"
    ) -> SyncResult:
        """
        Sync a single measure from Fabric to Snowflake.
        
        Args:
            measure_config: Configuration for the measure
            write_mode: "overwrite" or "append"
            
        Returns:
            SyncResult with status and row count
        """
        start_time = datetime.now()
        
        try:
            # Evaluate measure using SemPy
            if measure_config.partition_dimension:
                df = self.evaluate_measure_partitioned(measure_config)
            else:
                df = self.evaluate_measure(measure_config)
            
            if df is None or df.count() == 0:
                return SyncResult(
                    measure_name=measure_config.name,
                    status="empty",
                    rows=0,
                    duration_sec=(datetime.now() - start_time).total_seconds()
                )
            
            # Write to Snowflake
            rows = self.write_to_snowflake(df, measure_config.name, write_mode)
            
            return SyncResult(
                measure_name=measure_config.name,
                status="success",
                rows=rows,
                duration_sec=(datetime.now() - start_time).total_seconds()
            )
            
        except Exception as e:
            logger.error(f"Failed to sync measure {measure_config.name}: {e}")
            return SyncResult(
                measure_name=measure_config.name,
                status="failed",
                error=str(e),
                duration_sec=(datetime.now() - start_time).total_seconds()
            )
    
    def sync_measures(
        self,
        measures: list[MeasureConfig],
        write_mode: str = "overwrite",
        stop_on_error: bool = False,
    ) -> list[SyncResult]:
        """
        Sync multiple measures from Fabric to Snowflake.
        
        Args:
            measures: List of measure configurations
            write_mode: "overwrite" or "append"
            stop_on_error: If True, stop on first failure
            
        Returns:
            List of SyncResult for each measure
        """
        results = []
        
        logger.info(f"Starting sync of {len(measures)} measures")
        
        for i, config in enumerate(measures):
            logger.info(f"[{i+1}/{len(measures)}] Syncing: {config.name}")
            
            result = self.sync_measure(config, write_mode)
            results.append(result)
            
            if result.status == "failed" and stop_on_error:
                logger.error(f"Stopping due to error: {result.error}")
                break
        
        # Summary
        success = sum(1 for r in results if r.status == "success")
        failed = sum(1 for r in results if r.status == "failed")
        total_rows = sum(r.rows for r in results)
        
        logger.info(f"Sync complete: {success} success, {failed} failed, {total_rows} total rows")
        
        return results
    
    def discover_measures(self) -> list[dict]:
        """
        Discover all measures in the semantic model.
        
        Uses INFO.MEASURES() DMV to list available measures.
        
        Returns:
            List of measure metadata dictionaries
        """
        dax = """
EVALUATE
SELECTCOLUMNS(
    INFO.MEASURES(),
    "Name", [Name],
    "Table", [TableID],
    "Expression", [Expression],
    "IsHidden", [IsHidden],
    "FormatString", [FormatString]
)
"""
        try:
            df = self.fabric.evaluate_dax(
                dataset=self.dataset_name,
                dax_string=dax,
                workspace=self.workspace_id
            )
            
            measures = []
            for row in df.collect():
                measures.append({
                    "name": row["Name"],
                    "table": row["Table"],
                    "expression": row["Expression"],
                    "is_hidden": row["IsHidden"],
                    "format_string": row["FormatString"]
                })
            
            logger.info(f"Discovered {len(measures)} measures in {self.dataset_name}")
            return measures
            
        except Exception as e:
            logger.error(f"Failed to discover measures: {e}")
            return []


# =============================================================================
# Utility Functions
# =============================================================================

def analyze_dax_complexity(expression: str) -> dict:
    """
    Analyze a DAX expression and return sync recommendations.
    
    Returns:
        Dict with complexity tier, requirements, and recommendations
    """
    import re
    
    if not expression:
        return {"tier": 0, "syncable": False, "reason": "Empty expression"}
    
    upper_expr = expression.upper()
    
    # Time Intelligence patterns
    time_intel_funcs = [
        "TOTALYTD", "TOTALMTD", "TOTALQTD", 
        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "DATEADD"
    ]
    
    # Unsupported patterns
    unsupported = [
        r"CALCULATE\s*\([^)]+,\s*FILTER\s*\(",
        r"SUMX\s*\(\s*FILTER\s*\(",
        r"EARLIER\s*\(",
        r"RANKX\s*\(",
    ]
    
    result = {
        "tier": 1,
        "syncable": True,
        "requires_time_intel": False,
        "recommended_dimensions": [],
        "partition_by": None,
        "use_fallback": False,
        "reason": None
    }
    
    # Check for unsupported patterns
    for pattern in unsupported:
        if re.search(pattern, upper_expr):
            result["tier"] = 4
            result["syncable"] = False
            result["reason"] = "Contains unsupported DAX pattern"
            return result
    
    # Check for Time Intelligence
    for func in time_intel_funcs:
        if func in upper_expr:
            result["tier"] = 3
            result["requires_time_intel"] = True
            result["recommended_dimensions"] = ["'Date'[Date]", "'Date'[Year]"]
            result["partition_by"] = "'Date'[Year]"
            break
    
    # Check for CALCULATE
    if "CALCULATE" in upper_expr:
        result["tier"] = max(result["tier"], 3)
        result["use_fallback"] = True
    
    return result


def create_measures_from_discovery(
    discovered: list[dict],
    default_dimensions: list[str] = None
) -> list[MeasureConfig]:
    """
    Convert discovered measures to MeasureConfig objects.
    
    Filters hidden measures and analyzes complexity for sync settings.
    """
    configs = []
    default_dimensions = default_dimensions or ["'Date'[Year]"]
    
    for m in discovered:
        if m.get("is_hidden"):
            continue
            
        analysis = analyze_dax_complexity(m.get("expression", ""))
        
        if not analysis["syncable"]:
            logger.warning(f"Skipping unsyncable measure: {m['name']} - {analysis['reason']}")
            continue
        
        config = MeasureConfig(
            name=m["name"],
            group_by_dimensions=analysis["recommended_dimensions"] or default_dimensions,
            partition_dimension=analysis["partition_by"],
            use_fallback_pattern=analysis["use_fallback"],
        )
        configs.append(config)
    
    return configs


# =============================================================================
# Main Entry Point (for standalone execution)
# =============================================================================

def main():
    """
    Example usage when running as standalone notebook.
    
    Configure your settings below and run the cell.
    """
    
    # Configuration - UPDATE THESE VALUES
    WORKSPACE_ID = "your-workspace-id"
    DATASET_NAME = "Your Semantic Model"
    
    SNOWFLAKE_CONFIG = SnowflakeConfig(
        account="your-account",
        user="your-user",
        password="your-password",  # Use notebookutils.credentials in production
        warehouse="your-warehouse",
        database="your-database",
        schema_name="your-schema",
        role="SYSADMIN"
    )
    
    # Initialize pipeline
    pipeline = MeasureSyncPipeline(
        workspace_id=WORKSPACE_ID,
        dataset_name=DATASET_NAME,
        snowflake_config=SNOWFLAKE_CONFIG,
        default_dimensions=["'Date'[Year]", "'Date'[Month]"]
    )
    
    # Discover measures
    discovered = pipeline.discover_measures()
    print(f"Found {len(discovered)} measures")
    
    # Convert to configs (auto-analyze complexity)
    measure_configs = create_measures_from_discovery(discovered)
    print(f"Syncable measures: {len(measure_configs)}")
    
    # Sync all measures
    results = pipeline.sync_measures(measure_configs)
    
    # Print summary
    for r in results:
        status_icon = "✅" if r.status == "success" else "❌" if r.status == "failed" else "⏭️"
        print(f"{status_icon} {r.measure_name}: {r.status} ({r.rows} rows, {r.duration_sec:.1f}s)")


if __name__ == "__main__":
    # In Fabric notebooks, cells run individually so this won't auto-execute
    # Call main() explicitly in a cell to run
    pass
