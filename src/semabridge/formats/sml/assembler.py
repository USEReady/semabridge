"""
SML Assembler.

Builds SML models from extracted Snowflake metadata, including
detected relationships, hierarchies, and measures.
"""

from __future__ import annotations

from typing import Any, Optional

from semabridge.formats.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLDimension,
    SMLAttribute,
    SMLHierarchy,
    SMLLevel,
    SMLMetric,
    SMLRelationship,
    DataType,
    AggregationType,
    Cardinality,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SMLAssembler:
    """
    Assembles SML models from extracted metadata.
    
    Takes extracted Snowflake metadata (tables, columns, relationships)
    and assembles a complete SML model with:
    - Datasets from tables
    - Dimensions from non-fact tables
    - Metrics from numeric columns in fact tables
    - Relationships from detected foreign keys
    """
    
    def __init__(
        self,
        model_name: str,
        description: str = "",
        source_database: str = "",
        source_schema: str = "",
        normalize_names: bool = True,
    ):
        """
        Initialize the assembler.
        
        Args:
            model_name: Name for the semantic model
            description: Model description
            source_database: Source Snowflake database
            source_schema: Source Snowflake schema
            normalize_names: Whether to normalize dataset names to title case
        """
        self.model_name = model_name
        self.description = description
        self.source_database = source_database
        self.source_schema = source_schema
        self.normalize_names = normalize_names
        
        self._datasets: list[SMLDataset] = []
        self._dimensions: list[SMLDimension] = []
        self._metrics: list[SMLMetric] = []
        self._relationships: list[SMLRelationship] = []
    
    def _normalize_name(self, name: str) -> str:
        """Normalize name to Title Case to match Fabric conventions."""
        if not self.normalize_names:
            return name
        # Example: CUSTOMER -> Customer, DEVICE_INVENTORY -> Device Inventory
        if not name: return name
        return name.replace("_", " ").title()

    def add_table(
        self,
        table_name: str,
        columns: list[dict[str, Any]],
        description: str = "",
        is_fact: bool = False,
        row_count: Optional[int] = None,
    ) -> SMLDataset:
        """
        Add a table as a dataset.
        
        Args:
            table_name: Table name
            columns: List of column metadata dicts
            description: Table description
            is_fact: Whether this is a fact table
            row_count: Optional row count
            
        Returns:
            Created SMLDataset
        """
        normalized_name = self._normalize_name(table_name)
        
        sml_columns = []
        for col in columns:
            sml_col = SMLColumn(
                unique_name=col["name"],
                label=col.get("label", col["name"]),
                data_type=DataType.from_snowflake(col.get("data_type", "VARCHAR")),
                source_type=col.get("data_type", "VARCHAR"),
                description=col.get("description", ""),
                is_hidden=col.get("is_hidden", False),
                is_key=col.get("is_key", False),
                format_string=col.get("format_string"),
                folder=col.get("folder"),
            )
            sml_columns.append(sml_col)
        
        dataset = SMLDataset(
            unique_name=normalized_name,
            label=normalized_name,
            description=description,
            source_table=table_name,
            source_schema=self.source_schema,
            source_database=self.source_database,
            columns=sml_columns,
            is_fact=is_fact,
            row_count=row_count,
        )
        
        self._datasets.append(dataset)
        logger.debug(f"Added dataset: {normalized_name} (from {table_name})")
        return dataset
    
    def add_relationship(
        self,
        name: str,
        from_table: str,
        from_column: str,
        to_table: str,
        to_column: str,
        cardinality: str = "many-to-one",
        is_active: bool = True,
    ) -> SMLRelationship:
        """
        Add a relationship between tables.
        
        Args:
            name: Relationship name
            from_table: Source (many) table name
            from_column: Source column name
            to_table: Target (one) table name
            to_column: Target column name
            cardinality: Relationship cardinality
            is_active: Whether relationship is active
            
        Returns:
            Created SMLRelationship
        """
        # Map cardinality string to enum
        cardinality_map = {
            "one-to-one": Cardinality.ONE_TO_ONE,
            "one-to-many": Cardinality.ONE_TO_MANY,
            "many-to-one": Cardinality.MANY_TO_ONE,
            "many-to-many": Cardinality.MANY_TO_MANY,
        }
        
        rel = SMLRelationship(
            unique_name=name,
            from_dataset=self._normalize_name(from_table),
            from_columns=[from_column],
            to_dataset=self._normalize_name(to_table),
            to_columns=[to_column],
            cardinality=cardinality_map.get(cardinality, Cardinality.MANY_TO_ONE),
            is_active=is_active,
        )
        
        self._relationships.append(rel)
        logger.debug(f"Added relationship: {from_table}.{from_column} -> {to_table}.{to_column}")
        return rel
    
    def add_dimension(
        self,
        name: str,
        dataset: str,
        attributes: list[dict[str, str]],
        hierarchies: Optional[list[dict[str, Any]]] = None,
        description: str = "",
    ) -> SMLDimension:
        """
        Add a dimension with attributes and hierarchies.
        
        Args:
            name: Dimension name
            dataset: Source dataset name
            attributes: List of attribute dicts with 'name' and 'column' keys
            hierarchies: Optional list of hierarchy definitions
            description: Dimension description
            
        Returns:
            Created SMLDimension
        """
        # Normalize dataset name to match Fabric conventions
        dataset = self._normalize_name(dataset)
        
        sml_attrs = []
        for attr in attributes:
            sml_attr = SMLAttribute(
                unique_name=attr["name"],
                label=attr.get("label", attr["name"]),
                dataset=dataset,
                dataset_column=attr["column"],
                description=attr.get("description", ""),
            )
            sml_attrs.append(sml_attr)
        
        sml_hierarchies = []
        for h in (hierarchies or []):
            levels = []
            for level in h.get("levels", []):
                sml_level = SMLLevel(
                    unique_name=level["name"],
                    label=level.get("label", level["name"]),
                    attribute=level["attribute"],
                )
                levels.append(sml_level)
            
            sml_h = SMLHierarchy(
                unique_name=h["name"],
                label=h.get("label", h["name"]),
                levels=levels,
            )
            sml_hierarchies.append(sml_h)
        
        dimension = SMLDimension(
            unique_name=name,
            label=name,
            description=description,
            dataset=dataset,
            attributes=sml_attrs,
            hierarchies=sml_hierarchies,
        )
        
        self._dimensions.append(dimension)
        logger.debug(f"Added dimension: {name} ({len(sml_attrs)} attributes, {len(sml_hierarchies)} hierarchies)")
        return dimension
    
    def add_metric(
        self,
        name: str,
        dataset: str,
        source_column: str,
        aggregation: str = "sum",
        expression: Optional[str] = None,
        description: str = "",
        format_string: Optional[str] = None,
        folder: Optional[str] = None,
    ) -> SMLMetric:
        """
        Add a metric (measure).
        
        Args:
            name: Metric name
            dataset: Source dataset name
            source_column: Source column name
            aggregation: Aggregation type (sum, count, avg, min, max)
            expression: Optional DAX expression (overrides auto-generation)
            description: Metric description
            format_string: Display format
            folder: Display folder
            
        Returns:
            Created SMLMetric
        """
        agg_map = {
            "sum": AggregationType.SUM,
            "count": AggregationType.COUNT,
            "count_distinct": AggregationType.COUNT_DISTINCT,
            "avg": AggregationType.AVG,
            "min": AggregationType.MIN,
            "max": AggregationType.MAX,
            "none": AggregationType.NONE,
        }
        
        metric = SMLMetric(
            unique_name=name,
            label=name,
            description=description,
            dataset=self._normalize_name(dataset),
            source_column=source_column,
            aggregation=agg_map.get(aggregation.lower(), AggregationType.SUM),
            expression=expression or "",
            format_string=format_string,
            folder=folder,
        )
        
        self._metrics.append(metric)
        logger.debug(f"Added metric: {name} ({aggregation}({source_column}))")
        return metric
    
    def auto_generate_metrics(self, min_columns: int = 1) -> list[SMLMetric]:
        """
        Auto-generate metrics for numeric columns in fact tables.
        
        Args:
            min_columns: Minimum numeric columns for a table to generate metrics
            
        Returns:
            List of generated metrics
        """
        generated = []
        
        for dataset in self._datasets:
            if not dataset.is_fact:
                continue
            
            numeric_cols = dataset.get_numeric_columns()
            if len(numeric_cols) < min_columns:
                continue
            
            for col in numeric_cols:
                # Skip key columns
                if col.is_key:
                    continue
                
                # Generate SUM metric
                metric_name = f"Sum of {col.label}"
                metric = self.add_metric(
                    name=metric_name,
                    dataset=dataset.unique_name,
                    source_column=col.unique_name,
                    aggregation="sum",
                    description=f"Sum of {col.label}",
                    folder="Auto-Generated Measures",
                )
                generated.append(metric)
        
        logger.info(f"Auto-generated {len(generated)} metrics")
        return generated
    
    def auto_generate_dimensions(self) -> list[SMLDimension]:
        """
        Auto-generate dimensions for non-fact tables.
        
        Returns:
            List of generated dimensions
        """
        generated = []
        
        for dataset in self._datasets:
            if dataset.is_fact:
                continue
            
            # Create attributes from all non-hidden columns
            attributes = []
            for col in dataset.columns:
                if not col.is_hidden:
                    attributes.append({
                        "name": col.unique_name,
                        "column": col.unique_name,
                        "label": col.label,
                    })
            
            if attributes:
                dim = self.add_dimension(
                    name=f"{dataset.unique_name} Dimension",
                    dataset=dataset.unique_name,
                    attributes=attributes,
                    description=f"Dimension for {dataset.label}",
                )
                generated.append(dim)
        
        logger.info(f"Auto-generated {len(generated)} dimensions")
        return generated
    
    def build(self) -> SMLModel:
        """
        Build the complete SML model.
        
        Returns:
            Complete SMLModel instance
        """
        model = SMLModel(
            unique_name=self.model_name,
            label=self.model_name,
            description=self.description,
            datasets=self._datasets,
            dimensions=self._dimensions,
            metrics=self._metrics,
            relationships=self._relationships,
        )
        
        logger.info(
            f"Built SML model: {model.dataset_count} datasets, "
            f"{model.dimension_count} dimensions, "
            f"{model.metric_count} metrics, "
            f"{model.relationship_count} relationships"
        )
        
        return model
    
    def reset(self) -> None:
        """Reset the assembler to build a new model."""
        self._datasets = []
        self._dimensions = []
        self._metrics = []
        self._relationships = []
