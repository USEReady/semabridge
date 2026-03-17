#!/usr/bin/env python3
"""
Measure Dictionary - Central source of truth for all measure definitions.

Maintains a map of:
  measure_name -> MeasureDefinition
  measure_name -> resolved SQL expression
  
Handles:
  - Building from DAX definitions
  - Dependency resolution
  - Column mapping to real schema
  - Caching resolved expressions
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple, Set
from semabridge.utils.logger import get_logger
from semabridge.converter.dax_parser import (
    MeasureDefinition,
    DaxExpressionParser,
    MeasureDefinitionExtractor,
)

logger = get_logger(__name__)


@dataclass
class ColumnMapping:
    """Maps DAX column references to real schema columns."""
    dax_column: str                      # Column name in DAX (e.g., "Units", "Amount")
    schema_column: str                   # Actual column name in schema (e.g., "UNITS", "AMOUNT")
    confidence: float = 1.0              # How confident in this mapping (0-1)
    
    def __hash__(self):
        return hash(self.dax_column)
    
    def __eq__(self, other):
        if isinstance(other, ColumnMapping):
            return self.dax_column == other.dax_column
        return False


class MeasureDictionary:
    """
    Central source of truth for all measure definitions and their SQL equivalents.
    
    Usage:
        dict = MeasureDictionary(schema_columns=['UNITS', 'AMOUNT', 'PRODUCT_ID'])
        dict.add_measure(measure_def)
        dict.resolve_all()  # Resolve dependencies
        sql = dict.get_sql("Total Units")  # Get resolved SQL
    """
    
    def __init__(self, schema_columns: Optional[List[str]] = None):
        """
        Initialize measure dictionary.
        
        Args:
            schema_columns: List of available columns in Snowflake schema
        """
        self.measures: Dict[str, MeasureDefinition] = {}
        self.resolved_sql: Dict[str, Optional[str]] = {}
        self.column_mappings: Dict[str, ColumnMapping] = {}
        self.schema_columns = set(schema_columns or [])
        self.resolution_order: List[str] = []
        self.resolution_errors: Dict[str, str] = {}
        
        logger.info(f"Initialized MeasureDictionary with {len(self.schema_columns)} schema columns")
    
    def add_measure(self, measure: MeasureDefinition) -> None:
        """
        Add a measure definition to the dictionary.
        
        Args:
            measure: MeasureDefinition to add
        """
        key = measure.measure_name
        self.measures[key] = measure
        logger.debug(f"Added measure: {key}")
    
    def add_measures(self, measures: List[MeasureDefinition]) -> None:
        """
        Add multiple measure definitions.
        
        Args:
            measures: List of MeasureDefinition objects
        """
        for measure in measures:
            self.add_measure(measure)
        logger.info(f"Added {len(measures)} measures to dictionary")
    
    def add_column_mapping(self, mapping: ColumnMapping) -> None:
        """
        Add a column mapping.
        
        Args:
            mapping: ColumnMapping specifying DAX → Schema column
        """
        self.column_mappings[mapping.dax_column.upper()] = mapping
        logger.debug(f"Added mapping: {mapping.dax_column} → {mapping.schema_column}")
    
    def add_column_mappings(self, mappings: List[ColumnMapping]) -> None:
        """
        Add multiple column mappings.
        
        Args:
            mappings: List of ColumnMapping objects
        """
        for mapping in mappings:
            self.add_column_mapping(mapping)
        logger.info(f"Added {len(mappings)} column mappings")
    
    def get_schema_columns(self) -> Set[str]:
        """Get set of available schema columns."""
        return self.schema_columns
    
    def set_schema_columns(self, columns: List[str]) -> None:
        """
        Set the available schema columns.
        
        Args:
            columns: List of column names from schema
        """
        self.schema_columns = set(columns or [])
        logger.info(f"Set schema columns: {len(self.schema_columns)} columns")
    
    def resolve_all(self) -> Dict[str, Optional[str]]:
        """
        Resolve all measures in dependency order.
        
        Returns:
            Dict of measure_name -> resolved_sql (or None if unresolvable)
        """
        logger.info(f"Resolving {len(self.measures)} measures...")
        
        # Build dependency graph
        graph = self._build_dependency_graph()
        
        # Resolve in topological order
        self.resolution_order = self._topological_sort(graph)
        
        # Resolve each measure
        for measure_name in self.resolution_order:
            try:
                sql = self._resolve_measure(measure_name)
                self.resolved_sql[measure_name] = sql
                
                if sql:
                    logger.debug(f"✓ Resolved {measure_name}: {sql[:60]}...")
                else:
                    logger.warning(f"✗ Could not resolve {measure_name} deterministically")
                    self.resolution_errors[measure_name] = "Could not generate SQL"
                    
            except Exception as e:
                logger.error(f"✗ Error resolving {measure_name}: {e}")
                self.resolved_sql[measure_name] = None
                self.resolution_errors[measure_name] = str(e)
        
        logger.info(
            f"Resolved {sum(1 for v in self.resolved_sql.values() if v)} / "
            f"{len(self.measures)} measures"
        )
        
        return self.resolved_sql
    
    def get_sql(self, measure_name: str, table_alias: str = "fact") -> Optional[str]:
        """
        Get resolved SQL for a measure.
        
        Args:
            measure_name: Name of measure
            table_alias: SQL table alias to use
            
        Returns:
            SQL expression or None if not resolved
        """
        if measure_name not in self.resolved_sql:
            logger.warning(f"Measure not resolved: {measure_name}")
            return None
        
        sql = self.resolved_sql[measure_name]
        if sql:
            # Replace table alias placeholder if needed
            sql = sql.replace("fact.", f"{table_alias}.")
        
        return sql
    
    def get_measure(self, measure_name: str) -> Optional[MeasureDefinition]:
        """Get MeasureDefinition for a measure."""
        return self.measures.get(measure_name)
    
    def get_resolution_error(self, measure_name: str) -> Optional[str]:
        """Get error message if measure failed to resolve."""
        return self.resolution_errors.get(measure_name)
    
    def get_statistics(self) -> Dict:
        """Get statistics about the dictionary."""
        resolved = sum(1 for v in self.resolved_sql.values() if v)
        failed = len(self.measures) - resolved
        
        return {
            "total_measures": len(self.measures),
            "resolved": resolved,
            "failed": failed,
            "resolution_rate": f"{(resolved/len(self.measures)*100):.1f}%" if self.measures else "N/A",
            "column_mappings": len(self.column_mappings),
            "schema_columns": len(self.schema_columns),
        }
    
    # ======================================================================================
    # Internal resolution logic
    # ======================================================================================
    
    def _build_dependency_graph(self) -> Dict[str, Set[str]]:
        """
        Build dependency graph for measures.
        
        Returns:
            Dict of measure -> set of measures it depends on
        """
        graph = {}
        
        for name, measure in self.measures.items():
            deps = set()
            
            # Add dependencies from parsed expression
            for dep in measure.dependencies:
                if dep in self.measures:
                    deps.add(dep)
            
            graph[name] = deps
        
        logger.debug(f"Built dependency graph with {len(graph)} nodes")
        return graph
    
    def _topological_sort(self, graph: Dict[str, Set[str]]) -> List[str]:
        """
        Topological sort of measures by dependency.
        
        Returns:
            List of measure names in resolution order (dependencies first)
        """
        visited = set()
        visiting = set()
        result = []
        
        def visit(node):
            if node in visited:
                return
            if node in visiting:
                logger.warning(f"Circular dependency detected involving: {node}")
                return
            
            visiting.add(node)
            for dep in graph.get(node, set()):
                visit(dep)
            visiting.remove(node)
            visited.add(node)
            result.append(node)
        
        for measure in graph:
            visit(measure)
        
        return result
    
    def _resolve_measure(self, measure_name: str, table_alias: str = "fact") -> Optional[str]:
        """
        Resolve a single measure to SQL.
        
        Args:
            measure_name: Name of measure to resolve
            table_alias: SQL table alias
            
        Returns:
            SQL expression or None
        """
        measure = self.measures.get(measure_name)
        if not measure:
            return None
        
        # If not simple, cannot resolve deterministically
        if not measure.is_simple:
            logger.debug(f"Measure {measure_name} is not deterministic - skipping")
            return None
        
        # Try to resolve dependencies first
        for dep in measure.dependencies:
            if dep not in self.resolved_sql:
                logger.warning(f"Unresolved dependency for {measure_name}: {dep}")
                return None
        
        # Generate SQL from DAX
        from semabridge.converter.dax_sql_generator import DeterministicSQLGenerator
        
        generator = DeterministicSQLGenerator(
            column_mappings=self.column_mappings,
            schema_columns=self.schema_columns,
            measure_dict={name: self.resolved_sql.get(name) for name in self.measures},
        )
        
        try:
            sql = generator.translate(
                measure.expression.raw,
                table_alias=table_alias,
                measure_name=measure_name
            )
            return sql
        except Exception as e:
            logger.error(f"Failed to generate SQL for {measure_name}: {e}")
            return None
    
    def export_mappings(self) -> Dict:
        """Export measure mappings for debugging."""
        return {
            name: {
                "dax": measure.expression.raw,
                "sql": self.resolved_sql.get(name),
                "simple": measure.is_simple,
                "dependencies": measure.dependencies,
                "columns": measure.expression.columns,
                "error": self.resolution_errors.get(name),
            }
            for name, measure in self.measures.items()
        }


# ============================================================================
# Builders
# ============================================================================

class MeasureDictionaryBuilder:
    """Builder for constructing a MeasureDictionary from various sources."""
    
    @staticmethod
    def from_dax_text(dax_text: str, schema_columns: Optional[List[str]] = None) -> MeasureDictionary:
        """
        Build dictionary from DAX text containing MEASURE statements.
        
        Args:
            dax_text: Text containing MEASURE 'Table'[Name] = ... statements
            schema_columns: Available columns in schema
            
        Returns:
            MeasureDictionary with all measures extracted
        """
        extractor = MeasureDefinitionExtractor()
        measures = extractor.extract_all(dax_text)
        
        dictionary = MeasureDictionary(schema_columns)
        dictionary.add_measures(measures)
        
        logger.info(f"Built measure dictionary from DAX text: {len(measures)} measures")
        return dictionary
    
    @staticmethod
    def from_dictionary_list(measures_data: List[Dict]) -> MeasureDictionary:
        """
        Build dictionary from list of measure dictionaries.
        
        Each dict should have:
          - measure_name
          - table_name
          - dax_definition
          - (optional) schema_columns
        
        Args:
            measures_data: List of measure data dictionaries
            
        Returns:
            MeasureDictionary
        """
        parser = DaxExpressionParser()
        dictionary = MeasureDictionary()
        
        # Extract schema columns if provided
        for data in measures_data:
            if 'schema_columns' in data:
                dictionary.set_schema_columns(data['schema_columns'])
        
        # Add measures
        for data in measures_data:
            expr = parser.parse(data.get('dax_definition', ''))
            measure = MeasureDefinition(
                measure_name=data.get('measure_name', ''),
                table_name=data.get('table_name', ''),
                dax_definition=data.get('dax_definition', ''),
                expression=expr,
                is_simple=expr.is_deterministic,
            )
            dictionary.add_measure(measure)
        
        return dictionary


__all__ = [
    'MeasureDictionary',
    'MeasureDictionaryBuilder',
    'ColumnMapping',
]
