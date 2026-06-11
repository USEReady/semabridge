"""Shared utilities for TMDL/TMSL converters to eliminate duplication."""

from typing import Dict, List, Any, Optional
import re

class FabricModelUtils:
    """Shared utilities for parsing Fabric/Tabular models."""
    
    @staticmethod
    def normalize_column_name(name: str) -> str:
        """Normalize column names consistently across converters."""
        # Remove special characters
        normalized = re.sub(r'[^\w\s]', '', name)
        # Convert to uppercase with underscores
        normalized = re.sub(r'\s+', '_', normalized.strip())
        return normalized.upper()
    
    @staticmethod
    def map_data_type(fabric_type: str) -> str:
        """Map Fabric data types to SML types consistently."""
        type_map = {
            'int64': 'INTEGER',
            'int32': 'INTEGER',
            'double': 'DECIMAL',
            'decimal': 'DECIMAL',
            'string': 'VARCHAR',
            'boolean': 'BOOLEAN',
            'datetime': 'TIMESTAMP',
            'date': 'DATE',
        }
        return type_map.get(fabric_type.lower(), 'VARCHAR')
    
    @staticmethod
    def extract_relationship_parts(relationship: Dict) -> Dict:
        """Extract relationship parts consistently from TMDL/TMSL."""
        return {
            'from_table': relationship.get('fromTable', relationship.get('fromTableName')),
            'to_table': relationship.get('toTable', relationship.get('toTableName')),
            'from_column': relationship.get('fromColumn', relationship.get('fromColumnName')),
            'to_column': relationship.get('toColumn', relationship.get('toColumnName')),
            'is_active': relationship.get('isActive', True),
        }
