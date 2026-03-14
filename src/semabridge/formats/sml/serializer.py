"""
SML YAML Serializer.

Handles reading and writing SML models to YAML files.
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Union

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
    CrossFilterDirection,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SMLSerializer:
    """
    Serializes and deserializes SML models to/from YAML.
    
    Supports both single-file and folder-based representations.
    """
    
    @staticmethod
    def to_yaml(model: SMLModel, indent: int = 2) -> str:
        """
        Convert SML model to YAML string.
        
        Args:
            model: SML model to serialize
            indent: Indentation level
            
        Returns:
            YAML string representation
        """
        data = SMLSerializer._model_to_dict(model)
        return yaml.dump(data, default_flow_style=False, indent=indent, sort_keys=False)
    
    @staticmethod
    def from_yaml(yaml_content: str) -> SMLModel:
        """
        Parse YAML string to SML model.
        
        Args:
            yaml_content: YAML string
            
        Returns:
            SML model instance
        """
        data = yaml.safe_load(yaml_content)
        return SMLSerializer._dict_to_model(data)
    
    @staticmethod
    def save(model: SMLModel, path: Union[str, Path]) -> Path:
        """
        Save SML model to a file.
        
        Args:
            model: SML model to save
            path: File path (single file) or directory (folder structure)
            
        Returns:
            Path to the saved file/directory
        """
        path = Path(path)
        
        if path.suffix in (".yaml", ".yml"):
            # Single file mode
            path.parent.mkdir(parents=True, exist_ok=True)
            yaml_content = SMLSerializer.to_yaml(model)
            path.write_text(yaml_content, encoding="utf-8")
            logger.info(f"Saved SML model to {path}")
            return path
        else:
            # Folder structure mode
            return SMLSerializer._save_folder(model, path)
    
    @staticmethod
    def load(path: Union[str, Path]) -> SMLModel:
        """
        Load SML model from a file or folder.
        
        Args:
            path: File path (single file) or directory (folder structure)
            
        Returns:
            SML model instance
        """
        path = Path(path)
        
        if path.is_file():
            yaml_content = path.read_text(encoding="utf-8")
            return SMLSerializer.from_yaml(yaml_content)
        elif path.is_dir():
            return SMLSerializer._load_folder(path)
        else:
            raise FileNotFoundError(f"SML path not found: {path}")
    
    @staticmethod
    def _save_folder(model: SMLModel, folder: Path) -> Path:
        """
        Save SML model to folder structure.
        
        Creates:
        - model.yaml (main model file)
        - datasets/<name>.yaml
        - dimensions/<name>.yaml
        - metrics/<name>.yaml
        """
        folder.mkdir(parents=True, exist_ok=True)
        
        # Save main model file
        model_data = {
            "unique_name": model.unique_name,
            "object_type": "model",
            "label": model.label,
            "description": model.description,
            "version": model.version,
            "source_system": model.source_system,
            "datasets": [ds.unique_name for ds in model.datasets],
            "dimensions": [dim.unique_name for dim in model.dimensions],
            "metrics": [m.unique_name for m in model.metrics],
            "relationships": [SMLSerializer._relationship_to_dict(r) for r in model.relationships],
        }
        (folder / "model.yaml").write_text(
            yaml.dump(model_data, default_flow_style=False, sort_keys=False),
            encoding="utf-8"
        )
        
        # Save datasets
        if model.datasets:
            ds_folder = folder / "datasets"
            ds_folder.mkdir(exist_ok=True)
            for ds in model.datasets:
                ds_data = SMLSerializer._dataset_to_dict(ds)
                (ds_folder / f"{ds.unique_name}.yaml").write_text(
                    yaml.dump(ds_data, default_flow_style=False, sort_keys=False),
                    encoding="utf-8"
                )
        
        # Save dimensions
        if model.dimensions:
            dim_folder = folder / "dimensions"
            dim_folder.mkdir(exist_ok=True)
            for dim in model.dimensions:
                dim_data = SMLSerializer._dimension_to_dict(dim)
                (dim_folder / f"{dim.unique_name}.yaml").write_text(
                    yaml.dump(dim_data, default_flow_style=False, sort_keys=False),
                    encoding="utf-8"
                )
        
        # Save metrics
        if model.metrics:
            metric_folder = folder / "metrics"
            metric_folder.mkdir(exist_ok=True)
            for metric in model.metrics:
                metric_data = SMLSerializer._metric_to_dict(metric)
                (metric_folder / f"{metric.unique_name}.yaml").write_text(
                    yaml.dump(metric_data, default_flow_style=False, sort_keys=False),
                    encoding="utf-8"
                )
        
        logger.info(f"Saved SML model folder structure to {folder}")
        return folder
    
    @staticmethod
    def _load_folder(folder: Path) -> SMLModel:
        """Load SML model from folder structure."""
        model_file = folder / "model.yaml"
        if not model_file.exists():
            raise FileNotFoundError(f"model.yaml not found in {folder}")
        
        model_data = yaml.safe_load(model_file.read_text(encoding="utf-8"))
        
        # Load datasets
        datasets = []
        ds_folder = folder / "datasets"
        if ds_folder.exists():
            for ds_file in ds_folder.glob("*.yaml"):
                ds_data = yaml.safe_load(ds_file.read_text(encoding="utf-8"))
                datasets.append(SMLSerializer._dict_to_dataset(ds_data))
        
        # Load dimensions
        dimensions = []
        dim_folder = folder / "dimensions"
        if dim_folder.exists():
            for dim_file in dim_folder.glob("*.yaml"):
                dim_data = yaml.safe_load(dim_file.read_text(encoding="utf-8"))
                dimensions.append(SMLSerializer._dict_to_dimension(dim_data))
        
        # Load metrics
        metrics = []
        metric_folder = folder / "metrics"
        if metric_folder.exists():
            for metric_file in metric_folder.glob("*.yaml"):
                metric_data = yaml.safe_load(metric_file.read_text(encoding="utf-8"))
                metrics.append(SMLSerializer._dict_to_metric(metric_data))
        
        # Load relationships from model file
        relationships = []
        for rel_data in model_data.get("relationships", []):
            relationships.append(SMLSerializer._dict_to_relationship(rel_data))
        
        return SMLModel(
            unique_name=model_data["unique_name"],
            label=model_data.get("label", ""),
            description=model_data.get("description", ""),
            version=model_data.get("version", "1.0"),
            source_system=model_data.get("source_system", "snowflake"),
            datasets=datasets,
            dimensions=dimensions,
            metrics=metrics,
            relationships=relationships,
        )
    
    @staticmethod
    def _model_to_dict(model: SMLModel) -> dict[str, Any]:
        """Convert SML model to dictionary."""
        return {
            "unique_name": model.unique_name,
            "object_type": "model",
            "label": model.label,
            "description": model.description,
            "version": model.version,
            "source_system": model.source_system,
            "created_at": model.created_at,
            "modified_at": model.modified_at,
            "datasets": [SMLSerializer._dataset_to_dict(ds) for ds in model.datasets],
            "dimensions": [SMLSerializer._dimension_to_dict(dim) for dim in model.dimensions],
            "metrics": [SMLSerializer._metric_to_dict(m) for m in model.metrics],
            "relationships": [SMLSerializer._relationship_to_dict(r) for r in model.relationships],
        }
    
    @staticmethod
    def _dict_to_model(data: dict[str, Any]) -> SMLModel:
        """Convert dictionary to SML model."""
        return SMLModel(
            unique_name=data["unique_name"],
            label=data.get("label", ""),
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            source_system=data.get("source_system", "snowflake"),
            created_at=data.get("created_at", ""),
            modified_at=data.get("modified_at"),
            datasets=[SMLSerializer._dict_to_dataset(ds) for ds in data.get("datasets", [])],
            dimensions=[SMLSerializer._dict_to_dimension(dim) for dim in data.get("dimensions", [])],
            metrics=[SMLSerializer._dict_to_metric(m) for m in data.get("metrics", [])],
            relationships=[SMLSerializer._dict_to_relationship(r) for r in data.get("relationships", [])],
        )
    
    @staticmethod
    def _dataset_to_dict(dataset: SMLDataset) -> dict[str, Any]:
        """Convert dataset to dictionary."""
        return {
            "unique_name": dataset.unique_name,
            "object_type": "dataset",
            "label": dataset.label,
            "description": dataset.description,
            "source_table": dataset.source_table,
            "source_schema": dataset.source_schema,
            "source_database": dataset.source_database,
            "is_hidden": dataset.is_hidden,
            "is_fact": dataset.is_fact,
            "row_count": dataset.row_count,
            "columns": [SMLSerializer._column_to_dict(col) for col in dataset.columns],
        }
    
    @staticmethod
    def _dict_to_dataset(data: dict[str, Any]) -> SMLDataset:
        """Convert dictionary to dataset."""
        return SMLDataset(
            unique_name=data["unique_name"],
            label=data.get("label", ""),
            description=data.get("description", ""),
            source_table=data.get("source_table", ""),
            source_schema=data.get("source_schema", ""),
            source_database=data.get("source_database", ""),
            is_hidden=data.get("is_hidden", False),
            is_fact=data.get("is_fact", False),
            row_count=data.get("row_count"),
            columns=[SMLSerializer._dict_to_column(col) for col in data.get("columns", [])],
        )
    
    @staticmethod
    def _column_to_dict(column: SMLColumn) -> dict[str, Any]:
        """Convert column to dictionary."""
        return {
            "unique_name": column.unique_name,
            "label": column.label,
            "data_type": column.data_type.value,
            "source_type": column.source_type,
            "description": column.description,
            "is_hidden": column.is_hidden,
            "is_key": column.is_key,
            "format_string": column.format_string,
            "folder": column.folder,
        }
    
    @staticmethod
    def _dict_to_column(data: dict[str, Any]) -> SMLColumn:
        """Convert dictionary to column."""
        return SMLColumn(
            unique_name=data["unique_name"],
            label=data.get("label", ""),
            data_type=DataType(data.get("data_type", "string")),
            source_type=data.get("source_type", ""),
            description=data.get("description", ""),
            is_hidden=data.get("is_hidden", False),
            is_key=data.get("is_key", False),
            format_string=data.get("format_string"),
            folder=data.get("folder"),
        )
    
    @staticmethod
    def _dimension_to_dict(dimension: SMLDimension) -> dict[str, Any]:
        """Convert dimension to dictionary."""
        return {
            "unique_name": dimension.unique_name,
            "object_type": "dimension",
            "label": dimension.label,
            "description": dimension.description,
            "dataset": dimension.dataset,
            "is_hidden": dimension.is_hidden,
            "attributes": [
                {
                    "unique_name": attr.unique_name,
                    "label": attr.label,
                    "dataset": attr.dataset,
                    "dataset_column": attr.dataset_column,
                    "description": attr.description,
                    "is_hidden": attr.is_hidden,
                }
                for attr in dimension.attributes
            ],
            "hierarchies": [
                {
                    "unique_name": h.unique_name,
                    "label": h.label,
                    "levels": [
                        {"unique_name": l.unique_name, "label": l.label, "attribute": l.attribute}
                        for l in h.levels
                    ],
                }
                for h in dimension.hierarchies
            ],
        }
    
    @staticmethod
    def _dict_to_dimension(data: dict[str, Any]) -> SMLDimension:
        """Convert dictionary to dimension."""
        return SMLDimension(
            unique_name=data["unique_name"],
            label=data.get("label", ""),
            description=data.get("description", ""),
            dataset=data.get("dataset", ""),
            is_hidden=data.get("is_hidden", False),
            attributes=[
                SMLAttribute(
                    unique_name=attr["unique_name"],
                    label=attr.get("label", ""),
                    dataset=attr["dataset"],
                    dataset_column=attr["dataset_column"],
                    description=attr.get("description", ""),
                    is_hidden=attr.get("is_hidden", False),
                )
                for attr in data.get("attributes", [])
            ],
            hierarchies=[
                SMLHierarchy(
                    unique_name=h["unique_name"],
                    label=h.get("label", ""),
                    levels=[
                        SMLLevel(
                            unique_name=l["unique_name"],
                            label=l.get("label", ""),
                            attribute=l["attribute"],
                        )
                        for l in h.get("levels", [])
                    ],
                )
                for h in data.get("hierarchies", [])
            ],
        )
    
    @staticmethod
    def _metric_to_dict(metric: SMLMetric) -> dict[str, Any]:
        """Convert metric to dictionary."""
        return {
            "unique_name": metric.unique_name,
            "object_type": "metric",
            "label": metric.label,
            "description": metric.description,
            "dataset": metric.dataset,
            "expression": metric.expression,
            "aggregation": metric.aggregation.value,
            "source_column": metric.source_column,
            "format_string": metric.format_string,
            "folder": metric.folder,
            "is_hidden": metric.is_hidden,
        }
    
    @staticmethod
    def _dict_to_metric(data: dict[str, Any]) -> SMLMetric:
        """Convert dictionary to metric."""
        return SMLMetric(
            unique_name=data["unique_name"],
            label=data.get("label", ""),
            description=data.get("description", ""),
            dataset=data["dataset"],
            expression=data.get("expression", ""),
            aggregation=AggregationType(data.get("aggregation", "sum")),
            source_column=data.get("source_column"),
            format_string=data.get("format_string"),
            folder=data.get("folder"),
            is_hidden=data.get("is_hidden", False),
        )
    
    @staticmethod
    def _relationship_to_dict(rel: SMLRelationship) -> dict[str, Any]:
        """Convert relationship to dictionary."""
        return {
            "unique_name": rel.unique_name,
            "object_type": "relationship",
            "from_dataset": rel.from_dataset,
            "from_columns": rel.from_columns,
            "to_dataset": rel.to_dataset,
            "to_columns": rel.to_columns,
            "cardinality": rel.cardinality.value,
            "cross_filter": rel.cross_filter.value,
            "is_active": rel.is_active,
        }
    
    @staticmethod
    def _dict_to_relationship(data: dict[str, Any]) -> SMLRelationship:
        """Convert dictionary to relationship."""
        return SMLRelationship(
            unique_name=data["unique_name"],
            from_dataset=data["from_dataset"],
            from_columns=data.get("from_columns", [data.get("from_column")]),
            to_dataset=data["to_dataset"],
            to_columns=data.get("to_columns", [data.get("to_column")]),
            cardinality=Cardinality(data.get("cardinality", "many-to-one")),
            cross_filter=CrossFilterDirection(data.get("cross_filter", "single")),
            is_active=data.get("is_active", True),
        )
