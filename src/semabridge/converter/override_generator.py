"""
Override Generator — generates semantic override configurations.

Provides utilities to:
- Generate override configurations from semantic models
- Create Cortex metadata layers
- Generate semantic view definitions
- Create dynamic table configurations
- Output override YAML files
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json

from semabridge.converter.override_schema import (
    CortexMetadataLayer,
    CortexSynonym,
    DynamicTableLayer,
    SemanticMetricSpec,
    SemanticDimension,
    SemanticViewLayer,
    SQLOverrideFile,
    WindowFunctionSpec,
)


class OverrideGenerator:
    """
    Generates semantic override configurations from semantic models.
    """

    def __init__(
        self,
        database: str = "ANALYSIS_DB",
        schema: str = "SEMANTIC",
        warehouse: str = "COMPUTE_WH",
    ):
        """
        Initialize the override generator.

        Args:
            database: Target Snowflake database name
            schema: Target Snowflake schema name
            warehouse: Compute warehouse for dynamic tables
        """
        self.database = database
        self.schema = schema
        self.warehouse = warehouse

    def generate_override_file(
        self,
        model: Any,
        enable_cortex: bool = True,
        enable_semantic_views: bool = True,
        enable_dynamic_tables: bool = False,
    ) -> SQLOverrideFile:
        """
        Generate a complete override file from a semantic model.

        Args:
            model: Semantic model object
            enable_cortex: Include Cortex metadata layers
            enable_semantic_views: Include semantic view layers
            enable_dynamic_tables: Include dynamic table definitions

        Returns:
            Generated SQLOverrideFile
        """
        override_file = SQLOverrideFile(
            name=f"override_{getattr(model, 'unique_name', 'model')}",
            description=f"Override configuration for {getattr(model, 'label', 'model')}",
        )

        if enable_cortex:
            self._add_cortex_layers(override_file, model)

        if enable_semantic_views:
            self._add_semantic_views(override_file, model)

        if enable_dynamic_tables:
            self._add_dynamic_tables(override_file, model)

        return override_file

    def _add_cortex_layers(self, override_file: SQLOverrideFile, model: Any) -> None:
        """Add Cortex metadata layers from the model."""
        if not hasattr(model, 'datasets'):
            return

        for ds in model.datasets:
            ds_name = getattr(ds, 'unique_name', 'dataset')
            table_name = getattr(ds, 'source_table', f'TABLE_{ds_name}')

            # Create a Cortex layer for this dataset
            cortex_layer = CortexMetadataLayer(
                name=f"cortex_{ds_name}",
                description=f"Cortex metadata for {ds_name}",
            )

            # Add synonyms from columns
            if hasattr(ds, 'columns'):
                for col in ds.columns:
                    col_name = getattr(col, 'unique_name', 'column')
                    cortex_layer.synonyms.append(
                        CortexSynonym(
                            logical_name=col_name,
                            physical_name=col_name.upper(),
                            is_primary=False,
                        )
                    )

            override_file.add_cortex_layer(cortex_layer)

    def _add_semantic_views(self, override_file: SQLOverrideFile, model: Any) -> None:
        """Add semantic view layers from the model."""
        if not hasattr(model, 'datasets'):
            return

        for ds in model.datasets:
            ds_name = getattr(ds, 'unique_name', 'dataset')
            table_name = getattr(ds, 'source_table', f'TABLE_{ds_name}')

            # Create a basic semantic view
            semantic_view = SemanticViewLayer(
                name=ds_name,
                source_table=table_name,
                transformation_sql=f"SELECT * FROM {self.database}.{self.schema}.{table_name}",
                description=f"Semantic view for {ds_name} dataset",
            )

            override_file.add_semantic_view(semantic_view)

    def _add_dynamic_tables(self, override_file: SQLOverrideFile, model: Any) -> None:
        """Add dynamic table definitions from the model."""
        if not hasattr(model, 'datasets'):
            return

        for ds in model.datasets:
            ds_name = getattr(ds, 'unique_name', 'dataset')
            table_name = getattr(ds, 'source_table', f'TABLE_{ds_name}')

            dynamic_table = DynamicTableLayer(
                name=f"dynamic_{ds_name}",
                source_table=table_name,
                refresh_interval="1 HOUR",
                description=f"Dynamic table for {ds_name}",
            )

            override_file.add_dynamic_table(dynamic_table)

    def to_yaml(self, override_file: SQLOverrideFile) -> str:
        """
        Convert override file to YAML format.

        Args:
            override_file: Override configuration

        Returns:
            YAML string representation
        """
        try:
            import yaml
            data = {
                "name": override_file.name,
                "version": override_file.version,
                "description": override_file.description,
                "cortex_layers": [
                    {
                        "name": layer.name,
                        "description": layer.description,
                        "status": layer.status.value,
                        "synonyms": [
                            {
                                "logical_name": syn.logical_name,
                                "physical_name": syn.physical_name,
                                "is_primary": syn.is_primary,
                            }
                            for syn in layer.synonyms
                        ],
                    }
                    for layer in override_file.cortex_layers
                ],
                "semantic_views": [
                    {
                        "name": view.name,
                        "source_table": view.source_table,
                        "transformation_sql": view.transformation_sql,
                        "description": view.description,
                        "status": view.status.value,
                    }
                    for view in override_file.semantic_views
                ],
            }
            return yaml.dump(data, default_flow_style=False)
        except ImportError:
            return json.dumps(override_file.__dict__, indent=2, default=str)
