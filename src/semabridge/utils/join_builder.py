"""
Join tree builder for Databricks metric view YAML generation.

Converts semantic model relationships into nested join structures
for metric view 'joins' property, supporting snowflake schema patterns.
"""

from __future__ import annotations

from typing import Optional
from semabridge.sml.models import SMLModel, SMLJoin, SMLRelationship


class JoinTreeBuilder:
    """Builds join hierarchies from relationship graphs."""
    
    def __init__(self, model: SMLModel):
        """
        Initialize builder with a semantic model.
        
        Args:
            model: SMLModel containing datasets and relationships.
        """
        self.model = model
        self._visited: set[str] = set()
    
    def build_join_tree(
        self,
        primary_dataset: str,
        max_depth: int = 5,
    ) -> list[SMLJoin]:
        """
        Build a join tree from the primary dataset.
        
        Traverses outbound relationships to find all reachable tables
        and builds nested join structures for snowflake schemas.
        
        Args:
            primary_dataset: Name of the primary (fact) dataset.
            max_depth: Maximum nesting depth to prevent infinite recursion.
                      Default 5 levels.
        
        Returns:
            List of SMLJoin objects representing the join tree.
            Empty list if no outbound relationships exist.
        
        Raises:
            ValueError: If primary dataset is not found in model.
        """
        # Validate primary dataset exists
        if not self.model.get_dataset(primary_dataset):
            raise ValueError(f"Primary dataset '{primary_dataset}' not found in model")
        
        # Reset visited set
        self._visited.clear()
        self._visited.add(primary_dataset.upper())
        
        return self._traverse_relationships(primary_dataset, depth=0, max_depth=max_depth)
    
    def _traverse_relationships(
        self,
        current_dataset: str,
        depth: int = 0,
        max_depth: int = 5,
    ) -> list[SMLJoin]:
        """
        Recursively traverse relationships from current dataset.
        
        Args:
            current_dataset: Name of dataset to find outbound relationships from.
            depth: Current recursion depth.
            max_depth: Maximum depth to recurse.
        
        Returns:
            List of SMLJoin objects for this level.
        """
        if depth >= max_depth:
            return []
        
        joins = []
        current_upper = current_dataset.upper()
        
        # Find all relationships where current dataset is the source (from_dataset)
        for rel in self.model.relationships:
            if rel.from_dataset.upper() != current_upper:
                continue
            
            # Skip if target already visited (circular relationship)
            target_upper = rel.to_dataset.upper()
            if target_upper in self._visited:
                continue
            
            # Mark as visited
            self._visited.add(target_upper)
            
            # Build join condition from columns
            on_condition, using_columns = self._build_join_condition(rel)
            if not on_condition and not using_columns:
                continue
            
            # Get target dataset for source reference
            target_dataset = self.model.get_dataset(rel.to_dataset)
            if not target_dataset:
                continue
            
            # Build source reference (will be populated by caller)
            source_fq = self._build_source_reference(target_dataset)
            
            # Create join alias from dataset name
            alias = self._make_join_alias(rel.to_dataset)
            
            # Recursively build nested joins
            nested_joins = self._traverse_relationships(
                rel.to_dataset,
                depth=depth + 1,
                max_depth=max_depth,
            )
            
            join = SMLJoin(
                name=alias,
                source=source_fq,
                on=on_condition,
                using=using_columns,
                joins=nested_joins,
            )
            joins.append(join)
        
        return joins
    
    def _build_join_condition(self, rel: SMLRelationship) -> tuple[str, list[str]]:
        """
        Build a join ON condition from relationship column mappings.
        
        Args:
            rel: SMLRelationship with from/to column pairs.
        
        Returns:
            Tuple of (SQL-style ON condition, USING columns).
            USING columns are preferred when the relationship uses same-named keys.
        """
        if not rel.from_columns or not rel.to_columns:
            return "", []

        def _quote_identifier(name: str) -> str:
            sanitized = str(name or "").replace("`", "")
            return f"`{sanitized}`" if sanitized else ""

        same_named_columns: list[str] = []
        
        # Build pairs: from[0] = to[0], from[1] = to[1], etc.
        pairs = []
        for from_col, to_col in zip(rel.from_columns, rel.to_columns):
            if str(from_col or "").strip().upper() == str(to_col or "").strip().upper():
                same_named_columns.append(str(from_col).replace("`", ""))
                continue
            quoted_from = _quote_identifier(from_col)
            quoted_to = _quote_identifier(to_col)
            if not quoted_from or not quoted_to:
                continue
            pairs.append(f"{quoted_from} = {quoted_to}")

        return (" AND ".join(pairs) if pairs else "", same_named_columns)
    
    def _build_source_reference(self, dataset) -> str:
        """
        Build fully-qualified source table reference.
        
        Args:
            dataset: SMLDataset to reference.
        
        Returns:
            Fully-qualified table name, e.g., "schema.table".
        """
        # For now, use dataset name; caller may override with mappings
        parts = [
            dataset.source_database if dataset.source_database else None,
            dataset.source_schema if dataset.source_schema else None,
            dataset.source_table or dataset.unique_name,
        ]
        # Filter out None values
        parts = [p for p in parts if p]
        return ".".join(parts) if parts else dataset.unique_name
    
    def _make_join_alias(self, dataset_name: str) -> str:
        """
        Create a short, valid SQL alias for a dataset.
        
        Args:
            dataset_name: Original dataset name.
        
        Returns:
            Lowercase alias suitable for SQL expressions.
        """
        # Convert to lowercase, remove spaces/special chars
        alias = "".join(c.lower() for c in dataset_name if c.isalnum() or c == "_")
        return alias or "t" + str(hash(dataset_name) % 1000)
