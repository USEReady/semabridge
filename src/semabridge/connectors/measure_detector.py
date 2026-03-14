"""
Measure Detector.

Identifies numeric columns as measure candidates and detects
fact tables based on relationship patterns.
"""

from __future__ import annotations

from typing import Any

from semabridge.sml.models import DataType, AggregationType
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MeasureDetector:
    """
    Detects measure candidates and fact tables.
    
    Uses heuristics:
    - Numeric columns in tables with many FKs are likely measures
    - Columns with names like "amount", "quantity", "total" are measures
    - Tables with multiple FK relationships are likely fact tables
    """
    
    # Column name patterns indicating measures
    MEASURE_PATTERNS = [
        "AMOUNT", "AMT", "TOTAL", "SUM",
        "QUANTITY", "QTY", "COUNT",
        "PRICE", "COST", "VALUE",
        "REVENUE", "SALES", "PROFIT", "MARGIN",
        "BALANCE", "RATE", "PERCENT", "PCT",
        "WEIGHT", "VOLUME", "UNITS",
        "DISCOUNT", "TAX", "FEE",
        "SCORE", "RATING", "RANK",
    ]
    
    # Column names to exclude from measure detection
    EXCLUDE_PATTERNS = [
        "_ID", "_KEY", "_CODE", "_NUM",
        "YEAR", "MONTH", "DAY", "DATE",
        "ORDINAL", "POSITION", "SEQUENCE",
        "ROW_", "VERSION", "BATCH",
    ]
    
    # Numeric data types
    NUMERIC_TYPES = {
        DataType.INTEGER,
        DataType.DECIMAL,
        DataType.FLOAT,
    }
    
    def __init__(
        self,
        tables: dict[str, dict[str, Any]],
        columns: dict[str, list[dict[str, Any]]],
        relationships: list[dict[str, Any]],
    ):
        """
        Initialize the detector.
        
        Args:
            tables: Dict of table_name -> table metadata
            columns: Dict of table_name -> column metadata
            relationships: List of relationship definitions
        """
        self.tables = tables
        self.columns = columns
        self.relationships = relationships
        
        # Compute FK counts for fact detection
        self._fk_counts = self._compute_fk_counts()
    
    def _compute_fk_counts(self) -> dict[str, int]:
        """Count outgoing FK relationships per table."""
        counts = {table: 0 for table in self.tables}
        for rel in self.relationships:
            from_table = rel["from_table"]
            if from_table in counts:
                counts[from_table] += 1
        return counts
    
    def detect_fact_tables(self, min_fks: int = 2) -> list[str]:
        """
        Detect fact tables based on relationship patterns.
        
        Args:
            min_fks: Minimum FK count to consider a table as fact
            
        Returns:
            List of likely fact table names
        """
        facts = []
        for table, fk_count in self._fk_counts.items():
            if fk_count >= min_fks:
                facts.append(table)
                logger.debug(f"Detected fact table: {table} ({fk_count} FKs)")
        
        # Also check for naming conventions
        for table in self.tables:
            table_upper = table.upper()
            if table not in facts:
                if table_upper.startswith(("FACT_", "F_", "FCT_")):
                    facts.append(table)
                    logger.debug(f"Detected fact table by name: {table}")
        
        logger.info(f"Detected {len(facts)} fact tables")
        return facts
    
    def detect_measures(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
        is_fact: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Detect measure candidates in a table.
        
        Args:
            table_name: Table name
            columns: Column list
            is_fact: Whether this is a fact table
            
        Returns:
            List of measure candidate definitions
        """
        measures = []
        
        for col in columns:
            col_name = col["name"]
            col_upper = col_name.upper()
            data_type = DataType.from_snowflake(col.get("data_type", "VARCHAR"))
            
            # Skip if not numeric
            if data_type not in self.NUMERIC_TYPES:
                continue
            
            # Skip if matches exclude patterns
            if any(excl in col_upper for excl in self.EXCLUDE_PATTERNS):
                continue
            
            # Check if column name suggests a measure
            is_likely_measure = False
            suggested_agg = AggregationType.SUM
            
            for pattern in self.MEASURE_PATTERNS:
                if pattern in col_upper:
                    is_likely_measure = True
                    # Adjust aggregation based on pattern
                    if pattern in ("COUNT", "QTY", "QUANTITY"):
                        suggested_agg = AggregationType.SUM
                    elif pattern in ("RATE", "PERCENT", "PCT", "AVG"):
                        suggested_agg = AggregationType.AVG
                    break
            
            # For fact tables, also include numeric columns that don't match exclude
            if is_fact and not is_likely_measure:
                is_likely_measure = True
                suggested_agg = AggregationType.SUM
            
            if is_likely_measure:
                # Include table name in measure name to ensure uniqueness across the model
                col_display = col_name.replace('_', ' ').title()
                table_prefix = table_name.replace('_', ' ').title()
                measure = {
                    "name": f"{table_prefix} - Sum of {col_display}",
                    "column": col_name,
                    "table": table_name,
                    "aggregation": suggested_agg.value,
                    "data_type": data_type.value,
                    "confidence": 0.9 if any(p in col_upper for p in self.MEASURE_PATTERNS) else 0.7,
                }
                measures.append(measure)
        
        return measures
    
    def detect_all_measures(
        self,
        classification: dict[str, str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Detect measures for all tables.
        
        Args:
            classification: Optional map of table_name -> classification (FACT/DIMENSION)
                            If provided, measures are aggressively detected in FACTS
                            and conservatively detected in DIMENSIONS.
        
        Returns:
            Dict of table_name -> list of measure candidates
        """
        # Determine fact tables either from classification or internal logic
        if classification:
            fact_tables = {t for t, c in classification.items() if c == "FACT"}
        else:
            fact_tables = set(self.detect_fact_tables())
            
        results = {}
        
        for table_name, cols in self.columns.items():
            is_fact = table_name in fact_tables
            
            # If we have classification, we can stricter with Dimensions
            # Don't try to find measures in BRIDGE or TIME tables
            if classification:
                cls = classification.get(table_name, "UNKNOWN")
                if cls in ("BRIDGE", "TIME"):
                    continue
            
            measures = self.detect_measures(table_name, cols, is_fact=is_fact)
            if measures:
                results[table_name] = measures
        
        total = sum(len(m) for m in results.values())
        logger.info(f"Detected {total} potential measures across {len(results)} tables")
        
        return results
    
    def generate_measure_definitions(
        self,
        table_name: str,
        measures: list[dict[str, Any]],
        include_agg_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Generate full measure definitions for a table.
        
        Args:
            table_name: Source table name
            measures: List of detected measure candidates
            include_agg_types: Aggregation types to include (default: ["sum"])
            
        Returns:
            List of full measure definitions with expressions
        """
        if include_agg_types is None:
            include_agg_types = ["sum"]
        
        definitions = []
        
        for measure in measures:
            col_name = measure["column"]
            base_name = col_name.replace("_", " ").title()
            
            for agg_type in include_agg_types:
                agg_enum = AggregationType(agg_type)
                
                # Generate DAX expression
                if agg_enum == AggregationType.SUM:
                    expr = f"SUM('{table_name}'[{col_name}])"
                    name = f"Total {base_name}"
                elif agg_enum == AggregationType.AVG:
                    expr = f"AVERAGE('{table_name}'[{col_name}])"
                    name = f"Avg {base_name}"
                elif agg_enum == AggregationType.COUNT:
                    expr = f"COUNT('{table_name}'[{col_name}])"
                    name = f"Count of {base_name}"
                elif agg_enum == AggregationType.COUNT_DISTINCT:
                    expr = f"DISTINCTCOUNT('{table_name}'[{col_name}])"
                    name = f"Distinct {base_name}"
                elif agg_enum == AggregationType.MIN:
                    expr = f"MIN('{table_name}'[{col_name}])"
                    name = f"Min {base_name}"
                elif agg_enum == AggregationType.MAX:
                    expr = f"MAX('{table_name}'[{col_name}])"
                    name = f"Max {base_name}"
                else:
                    continue
                
                definitions.append({
                    "name": name,
                    "table": table_name,
                    "column": col_name,
                    "expression": expr,
                    "aggregation": agg_type,
                    "folder": "Auto-Generated Measures",
                })
        
        return definitions
