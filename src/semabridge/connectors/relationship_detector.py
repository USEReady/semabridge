"""
Relationship Detector.

Detects table relationships through:
1. Foreign key constraints from Snowflake
2. Column naming conventions (e.g., product_id -> products.id)
3. Common patterns (e.g., _fk, _id suffixes)
"""

from __future__ import annotations

import re
from typing import Any, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import RelationshipNameTracker

logger = get_logger(__name__)


class RelationshipDetector:
    """
    Detects relationships between tables.
    
    Uses multiple strategies:
    1. Explicit foreign keys from metadata
    2. Naming conventions (column_id -> column table)
    3. Common prefix/suffix patterns
    """
    
    # Common suffix patterns that indicate foreign keys
    FK_SUFFIXES = ["_id", "_key", "_fk", "_code", "id", "key"]
    
    # Common table name patterns to strip when matching
    TABLE_SUFFIXES_TO_STRIP = ["s", "es", "ies"]
    
    def __init__(
        self,
        tables: dict[str, dict[str, Any]],
        columns: dict[str, list[dict[str, Any]]],
        primary_keys: dict[str, list[str]],
        explicit_fks: Optional[list[dict[str, Any]]] = None,
    ):
        """
        Initialize the detector.
        
        Args:
            tables: Dict of table_name -> table metadata
            columns: Dict of table_name -> list of column metadata
            primary_keys: Dict of table_name -> list of PK column names
            explicit_fks: Optional list of explicit FK relationships
        """
        self.tables = tables
        self.columns = columns
        self.primary_keys = primary_keys
        self.explicit_fks = explicit_fks or []
        
        # Build lookup for faster matching
        self._table_names_upper = {t.upper(): t for t in tables.keys()}
        self._column_lookup = self._build_column_lookup()
    
    def _build_column_lookup(self) -> dict[str, dict[str, str]]:
        """Build a lookup of table.column -> original names."""
        lookup = {}
        for table_name, cols in self.columns.items():
            lookup[table_name.upper()] = {
                col["name"].upper(): col["name"]
                for col in cols
            }
        return lookup
    
    def detect_all(self) -> list[dict[str, Any]]:
        """
        Detect all relationships.
        
        Returns:
            List of relationship dicts with:
            - name: Relationship name
            - from_table: Source (many) table
            - from_column: Source column
            - to_table: Target (one) table
            - to_column: Target column
            - source: How the relationship was detected
            - confidence: Detection confidence (1.0 for explicit, lower for inferred)
        """
        relationships = []
        seen = set()  # Track (from_table, from_col, to_table, to_col)
        name_tracker = RelationshipNameTracker()
        
        # 1. Add explicit foreign keys first (highest confidence)
        for fk in self.explicit_fks:
            key = (
                fk["from_table"].upper(),
                fk["from_column"].upper(),
                fk["to_table"].upper(),
                fk["to_column"].upper(),
            )
            if key not in seen:
                relationships.append({
                    "name": name_tracker.next_name(fk["from_table"], fk["from_column"], fk["to_table"], fk["to_column"]),
                    "from_table": fk["from_table"],
                    "from_column": fk["from_column"],
                    "to_table": fk["to_table"],
                    "to_column": fk["to_column"],
                    "source": "explicit_fk",
                    "confidence": 1.0,
                })
                seen.add(key)
        
        # 2. Detect by naming convention
        for from_table, cols in self.columns.items():
            for col in cols:
                col_name = col["name"]
                
                # Skip if column is a PK
                if col_name in self.primary_keys.get(from_table, []):
                    continue
                
                # Try to match to another table
                match = self._match_column_to_table(from_table, col_name)
                if match:
                    to_table, to_column = match
                    key = (
                        from_table.upper(),
                        col_name.upper(),
                        to_table.upper(),
                        to_column.upper(),
                    )
                    if key not in seen:
                        relationships.append({
                            "name": name_tracker.next_name(from_table, col_name, to_table, to_column),
                            "from_table": from_table,
                            "from_column": col_name,
                            "to_table": to_table,
                            "to_column": to_column,
                            "source": "naming_convention",
                            "confidence": 0.8,
                        })
                        seen.add(key)
        
        logger.info(f"Detected {len(relationships)} relationships "
                   f"({len(self.explicit_fks)} explicit, {len(relationships) - len(self.explicit_fks)} inferred)")
        
        return relationships
    
    def _match_column_to_table(
        self,
        from_table: str,
        column_name: str,
    ) -> Optional[tuple[str, str]]:
        """
        Try to match a column to a potential target table.
        
        Args:
            from_table: Source table name
            column_name: Column name to analyze
            
        Returns:
            Tuple of (target_table, target_column) or None
        """
        col_upper = column_name.upper()
        
        # Check for common FK suffixes
        for suffix in self.FK_SUFFIXES:
            suffix_upper = suffix.upper()
            if col_upper.endswith(suffix_upper):
                # Extract the base name (e.g., CUSTOMER from CUSTOMER_ID)
                base = col_upper[:-len(suffix_upper)].rstrip("_")
                if not base:
                    continue
                
                # Try to find matching table
                target_table = self._find_matching_table(base)
                if target_table and target_table.upper() != from_table.upper():
                    # Find the PK column in target table
                    target_pk = self._get_likely_pk(target_table)
                    if target_pk:
                        return (target_table, target_pk)
        
        return None
    
    def _find_matching_table(self, base_name: str) -> Optional[str]:
        """Find a table that matches the base name."""
        base_upper = base_name.upper()
        
        # Direct match
        if base_upper in self._table_names_upper:
            return self._table_names_upper[base_upper]
        
        # Try plural forms
        for suffix in self.TABLE_SUFFIXES_TO_STRIP:
            # Add suffix for singular -> plural
            candidate = base_upper + suffix.upper()
            if candidate in self._table_names_upper:
                return self._table_names_upper[candidate]
            
            # Remove suffix for plural -> singular mismatch
            if base_upper.endswith(suffix.upper()):
                candidate = base_upper[:-len(suffix)]
                if candidate in self._table_names_upper:
                    return self._table_names_upper[candidate]
        
        # Try with common prefixes removed
        for prefix in ["DIM_", "FACT_", "D_", "F_", "TBL_"]:
            # Check if target has prefix
            candidate = prefix + base_upper
            if candidate in self._table_names_upper:
                return self._table_names_upper[candidate]
        
        return None
    
    def _get_likely_pk(self, table_name: str) -> Optional[str]:
        """Get the most likely primary key column for a table."""
        # First, check explicit PKs
        pks = self.primary_keys.get(table_name, [])
        if pks:
            return pks[0]  # Return first PK column
        
        # Fallback: look for common PK patterns
        table_cols = self._column_lookup.get(table_name.upper(), {})
        
        # Common PK column names in order of preference
        pk_patterns = [
            "ID",
            f"{table_name.upper()}_ID",
            f"{table_name.upper()}ID",
            "KEY",
            f"{table_name.upper()}_KEY",
        ]
        
        for pattern in pk_patterns:
            if pattern in table_cols:
                return table_cols[pattern]
        
        return None
    
    def get_relationships_for_table(
        self,
        table_name: str,
        relationships: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Get all relationships involving a specific table."""
        table_upper = table_name.upper()
        return [
            rel for rel in relationships
            if rel["from_table"].upper() == table_upper
            or rel["to_table"].upper() == table_upper
        ]
