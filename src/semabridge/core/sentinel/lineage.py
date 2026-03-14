"""
Sentinel Lineage Mapper.

Maps physical Snowflake objects (tables, columns) back to their
definitions in the Semantic Modeling Language (SML) files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union, NamedTuple

from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLColumn
from semabridge.formats.sml.serializer import SMLSerializer
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class PhysicalTable(NamedTuple):
    """Represents a physical table in Snowflake."""
    database: str
    schema: str
    table: str
    
    def __str__(self) -> str:
        return f"{self.database}.{self.schema}.{self.table}"


class DependencyGraph:
    """
    Links SML models to physical Snowflake objects.
    
    Allows reverse lookup: given a failed Snowflake object/query,
    find the corresponding SML dataset or column definition.
    """
    
    def __init__(self):
        self._table_map: dict[str, list[SMLDataset]] = {}
        self._column_map: dict[str, list[SMLColumn]] = {}
        self.model: Optional[SMLModel] = None
        
    def load(self, path: Union[str, Path]) -> None:
        """
        Load SML model and build dependency graph.
        
        Args:
            path: Path to SML model file or directory
        """
        logger.info(f"Loading SML model from {path} for lineage mapping...")
        try:
            self.model = SMLSerializer.load(path)
            self._build_graph()
        except Exception as e:
            logger.error(f"Failed to load SML model: {e}")
            raise
            
    def _build_graph(self) -> None:
        """Build internal lookup maps."""
        if not self.model:
            return
            
        self._table_map.clear()
        self._column_map.clear()
        
        for dataset in self.model.datasets:
            # Map Table -> Dataset
            # Key format: DATABASE.SCHEMA.TABLE (upper case)
            # Default to model source system if not specified? 
            # The SML spec says source_* are optional but usually present.
            # If missing, we might assume they match unique_name or similar (handled in model_post_init somewhat).
            
            db = dataset.source_database or ""
            schema = dataset.source_schema or ""
            table = dataset.source_table or dataset.unique_name
            
            # We strictly need at least table name. DB/Schema might be inferred from connection config in real deployment,
            # but here we rely on what's in the YAML.
            if not table:
                continue
                
            key = f"{db}.{schema}.{table}".upper().lstrip(".") # Handle missing parts gracefully
            
            if key not in self._table_map:
                self._table_map[key] = []
            self._table_map[key].append(dataset)
            
            # Map Column -> SMLColumn
            for col in dataset.columns:
                # Key format: DATABASE.SCHEMA.TABLE.COLUMN
                col_key = f"{key}.{col.unique_name}".upper() # SML usually maps unique_name to source column if not distinct
                # Note: SMLColumn doesn't strictly have a 'source_column' field in the class definition I saw?
                # Wait, I saw `SMLColumn` in `models.py`:
                # unique_name, label, data_type, source_type...
                # It does NOT have 'source_column'. 
                # Usually `unique_name` IS the source column name in simple SML.
                # Or it is inferred. Let's assume unique_name matches source column for now.
                
                if col_key not in self._column_map:
                    self._column_map[col_key] = []
                self._column_map[col_key].append(col)
                
        logger.info(f"Built dependency graph: {len(self._table_map)} tables, {len(self._column_map)} columns")

    def find_dataset(self, database: str, schema: str, table: str) -> list[SMLDataset]:
        """Find SML datasets that use the given physical table."""
        key = f"{database}.{schema}.{table}".upper()
        # Try exact match first
        if key in self._table_map:
            return self._table_map[key]
        
        # Fallback: try match without database if database was empty in SML
        partial_key = f".{schema}.{table}".upper()
        if partial_key in self._table_map:
             return self._table_map[partial_key]
             
        # Fallback: try match without db and schema
        partial_key_2 = f"..{table}".upper()
        if partial_key_2 in self._table_map:
             return self._table_map[partial_key_2]
             
        # Also try keys that might be just TABLE or SCHEMA.TABLE in the map
        # Dictionary keys are constructed from what's in SML.
        # If SML has "schema.table", key is "SCHEMA.TABLE" (starts with dot if db missing? logic above says lstrip)
        
        # Let's try constructing the key based on how we stored it (lstrip)
        candidates = [
            f"{database}.{schema}.{table}",
            f"{schema}.{table}",
            f"{table}"
        ]
        
        for c in candidates:
            k = c.upper()
            if k in self._table_map:
                return self._table_map[k]
                
        return []

    def find_column(self, database: str, schema: str, table: str, column: str) -> list[SMLColumn]:
        """Find SML columns that map to the given physical column."""
        # Similar logic to find_dataset but appending column
        
        # First get the dataset(s)
        datasets = self.find_dataset(database, schema, table)
        results = []
        
        for ds in datasets:
            # We found the dataset, now looking for the column within it
            # We assume SMLColumn.unique_name maps to source column
            for col in ds.columns:
                if col.unique_name.upper() == column.upper():
                    results.append(col)
                    
        return results
