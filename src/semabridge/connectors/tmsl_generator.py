"""
TMSL Generator.

Generates Fabric-compatible model.bim (Tabular Model Scripting Language)
JSON files from SML models.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLDimension,
    SMLHierarchy,
    SMLMetric,
    SMLRelationship,
    Cardinality,
    CrossFilterDirection,
)
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import RelationshipNameTracker

logger = get_logger(__name__)


class TMSLGenerator:
    """
    Generates Fabric TMSL (model.bim) from SML models.
    
    Creates JSON structure compatible with:
    - Fabric SemanticModel REST API
    - Power BI Desktop
    - Tabular Editor
    """
    
    # Compatibility level for Fabric semantic models
    COMPATIBILITY_LEVEL = 1567
    
    def __init__(
        self,
        sml_model: SMLModel,
        snowflake_server: str = "",
        snowflake_warehouse: str = "",
        snowflake_database: str = "",
        snowflake_schema: str = "",
    ):
        """
        Initialize the generator.
        
        Args:
            sml_model: Source SML model
            snowflake_server: Snowflake server for M expressions
            snowflake_warehouse: Snowflake warehouse
            snowflake_database: Snowflake database
            snowflake_schema: Snowflake schema
        """
        self.model = sml_model
        self.sf_server = snowflake_server
        self.sf_warehouse = snowflake_warehouse
        self.sf_database = snowflake_database
        self.sf_schema = snowflake_schema
    
    def generate(self) -> dict[str, Any]:
        """
        Generate the complete model.bim structure.
        
        Returns:
            TMSL JSON structure
        """
        logger.info(f"Generating TMSL for model: {self.model.unique_name}")
        
        tables = self._build_tables()
        relationships = self._build_relationships()
        
        model_bim = {
            "name": self.model.unique_name,
            "compatibilityLevel": self.COMPATIBILITY_LEVEL,
            "model": {
                "culture": "en-US",
                "dataAccessOptions": {
                    "legacyRedirects": True,
                    "fastCombine": True,
                },
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "tables": tables,
                "relationships": relationships,
                "annotations": [
                    {
                        "name": "PBI_QueryOrder",
                        "value": json.dumps([ds.unique_name for ds in self.model.datasets]),
                    },
                    {
                        "name": "__SemaBridge",
                        "value": json.dumps({
                            "generatedAt": datetime.utcnow().isoformat(),
                            "version": self.model.version,
                            "source": "semabridge",
                        }),
                    },
                ],
            },
        }
        
        logger.info(f"Generated TMSL: {len(tables)} tables, {len(relationships)} relationships")
        return model_bim
    
    def _build_tables(self) -> list[dict[str, Any]]:
        """Build table definitions."""
        tables = []
        
        # Build lookup for metrics by table
        metrics_by_table: dict[str, list[SMLMetric]] = {}
        for metric in self.model.metrics:
            if metric.dataset not in metrics_by_table:
                metrics_by_table[metric.dataset] = []
            metrics_by_table[metric.dataset].append(metric)
        
        # Build lookup for hierarchies by table
        hierarchies_by_table: dict[str, list[SMLHierarchy]] = {}
        for dim in self.model.dimensions:
            if dim.dataset and dim.hierarchies:
                if dim.dataset not in hierarchies_by_table:
                    hierarchies_by_table[dim.dataset] = []
                hierarchies_by_table[dim.dataset].extend(dim.hierarchies)
        
        for dataset in self.model.datasets:
            # Filter out auto-generated date tables to prevent deployment errors
            if dataset.unique_name.startswith("LocalDateTable_") or dataset.unique_name.startswith("DateTableTemplate_"):
                logger.warning(f"Skipping auto-generated table: {dataset.unique_name}")
                continue
                
            table = self._build_table(
                dataset,
                metrics_by_table.get(dataset.unique_name, []),
                hierarchies_by_table.get(dataset.unique_name, []),
            )
            tables.append(table)
        
        return tables
    
    def _build_table(
        self,
        dataset: SMLDataset,
        metrics: list[SMLMetric],
        hierarchies: list[SMLHierarchy],
    ) -> dict[str, Any]:
        """Build a single table definition."""
        # Build columns
        columns = []
        for col in dataset.columns:
            col_def = {
                "name": col.unique_name,
                "dataType": col.data_type.to_powerbi(),
                "sourceColumn": col.unique_name,
                # Prevent numeric columns from being treated as implicit
                # measure-like fields in Fabric/Power BI model explorers.
                "summarizeBy": "none",
            }
            
            if col.description:
                col_def["description"] = col.description
            if col.is_hidden:
                col_def["isHidden"] = True
            if col.format_string:
                col_def["formatString"] = col.format_string
            if col.folder:
                col_def["displayFolder"] = col.folder
            
            columns.append(col_def)
        
        # Build measures
        measures = []
        for metric in metrics:
            measure_def = {
                "name": metric.unique_name,
                "expression": metric.expression,
            }
            
            if metric.description:
                measure_def["description"] = metric.description
            if metric.format_string:
                measure_def["formatString"] = metric.format_string
            if metric.folder:
                measure_def["displayFolder"] = metric.folder
            if metric.is_hidden:
                measure_def["isHidden"] = True
            
            measures.append(measure_def)
        
        # Build hierarchies
        hierarchy_defs = []
        for hierarchy in hierarchies:
            levels = []
            for i, level in enumerate(hierarchy.levels):
                levels.append({
                    "name": level.unique_name,
                    "ordinal": i,
                    "column": level.attribute,
                })
            
            hierarchy_defs.append({
                "name": hierarchy.unique_name,
                "levels": levels,
            })
        
        # Build table definition
        table_def: dict[str, Any] = {
            "name": dataset.unique_name,
            "columns": columns,
            "partitions": [
                {
                    "name": f"{dataset.unique_name}_Partition",
                    "mode": "import",
                    "source": {
                        "type": "m",
                        "expression": self._build_m_expression(dataset),
                    },
                }
            ],
        }
        
        if dataset.description:
            table_def["description"] = dataset.description
        if dataset.is_hidden:
            table_def["isHidden"] = True
        if measures:
            table_def["measures"] = measures
        if hierarchy_defs:
            table_def["hierarchies"] = hierarchy_defs
        
        return table_def
    
    def _build_m_expression(self, dataset: SMLDataset) -> str:
        """Build Power Query M expression for data source.
        
        Uses Snowflake.Databases() with proper table navigation.
        """
        table_name = dataset.source_table or dataset.unique_name
        
        # Use configured Snowflake details or dataset source info
        server = self.sf_server or "{{server}}"
        warehouse = self.sf_warehouse or "{{warehouse}}"
        database = dataset.source_database or self.sf_database or "{{database}}"
        schema = dataset.source_schema or self.sf_schema or "{{schema}}"
        
        # Use Snowflake.Query for a more direct SQL approach
        m_expr = f'''let
    Source = Value.NativeQuery(Snowflake.Databases("{server}", "{warehouse}"){{[Name="{database}"]}}[Data]{{[Name="{schema}", Kind="Schema"]}}[Data], "SELECT * FROM ""{schema}"".""{table_name}""", null, [EnableFolding=true])
in
    Source'''
        
        return m_expr
    
    def _build_relationships(self) -> list[dict[str, Any]]:
        """Build relationship definitions."""
        relationships = []
        seen_endpoints = set()  # Track exact endpoints for strict deduplication
        name_tracker = RelationshipNameTracker()
        
        sorted_relationships = sorted(
            self.model.relationships,
            key=lambda r: (
                r.from_dataset.upper(),
                r.from_column.upper(),
                r.to_dataset.upper(),
                r.to_column.upper(),
            ),
        )

        for rel in sorted_relationships:
            # Filter relationships to auto-generated date tables
            if (rel.from_dataset.startswith("LocalDateTable_") or 
                rel.to_dataset.startswith("LocalDateTable_") or
                rel.from_dataset.startswith("DateTableTemplate_") or
                rel.to_dataset.startswith("DateTableTemplate_")):
                continue

            endpoint_key = (
                rel.from_dataset.upper(),
                rel.from_column.upper(),
                rel.to_dataset.upper(),
                rel.to_column.upper(),
            )
            if endpoint_key in seen_endpoints:
                logger.warning(
                    f"Skipping duplicate relationship endpoint "
                    f"{rel.from_dataset}.{rel.from_column} -> {rel.to_dataset}.{rel.to_column}"
                )
                continue
            seen_endpoints.add(endpoint_key)
                
            rel_def = {
                "name": name_tracker.next_name(rel.from_dataset, rel.from_column, rel.to_dataset, rel.to_column),
                "fromTable": rel.from_dataset,
                "fromColumn": rel.from_column,
                "toTable": rel.to_dataset,
                "toColumn": rel.to_column,
                "isActive": rel.is_active,
            }
            
            # Map cardinality
            if rel.cardinality == Cardinality.ONE_TO_ONE:
                rel_def["fromCardinality"] = "one"
                rel_def["toCardinality"] = "one"
            elif rel.cardinality == Cardinality.ONE_TO_MANY:
                rel_def["fromCardinality"] = "one"
                rel_def["toCardinality"] = "many"
            elif rel.cardinality == Cardinality.MANY_TO_ONE:
                rel_def["fromCardinality"] = "many"
                rel_def["toCardinality"] = "one"
            else:
                rel_def["fromCardinality"] = "many"
                rel_def["toCardinality"] = "many"
            
            # Map cross-filter direction
            if rel.cross_filter == CrossFilterDirection.BOTH:
                rel_def["crossFilteringBehavior"] = "bothDirections"
            else:
                rel_def["crossFilteringBehavior"] = "oneDirection"
            
            relationships.append(rel_def)
        
        return relationships
    
    def save(self, output_path: Path | str) -> Path:
        """
        Save the generated TMSL to a file.
        
        Args:
            output_path: Path for the output file
            
        Returns:
            Path to the saved file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        model_bim = self.generate()
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(model_bim, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved model.bim to {output_path}")
        return output_path
    
    def generate_definition_pbism(self) -> dict[str, Any]:
        """Generate the definition.pbism file content."""
        return {
            "version": "1.0",
            "settings": {}
        }
    
    def generate_platform_file(self) -> dict[str, Any]:
        """Generate the .platform file content."""
        return {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {
                "type": "SemanticModel",
                "displayName": self.model.label or self.model.unique_name,
            },
            "config": {
                "version": "2.0",
                "logicalId": self.model.unique_name.lower().replace(" ", "-"),
            }
        }
    
    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the generated model."""
        return {
            "model_name": self.model.unique_name,
            "tables": len(self.model.datasets),
            "total_columns": self.model.total_columns,
            "measures": len(self.model.metrics),
            "relationships": len(self.model.relationships),
            "hierarchies": sum(len(d.hierarchies) for d in self.model.dimensions),
        }
