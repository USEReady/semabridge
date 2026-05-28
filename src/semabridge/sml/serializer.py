"""
SML YAML Serializer.

Handles reading and writing SML models to YAML files.
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any, Union

from semabridge.intermediate.models import OSIModel


class SMLSerializer:
    """Compatibility serializer that reads/writes YAML using the canonical OSI model.

    This keeps the public `semabridge.sml.serializer` API intact while ensuring
    payloads conform to the official OSI/Intermediate schema.
    """

    @staticmethod
    def to_yaml(model: OSIModel, indent: int = 2) -> str:
        data = model.model_dump()
        return yaml.dump(data, default_flow_style=False, indent=indent, sort_keys=False)

    @staticmethod
    def from_yaml(yaml_content: str) -> OSIModel:
        data = yaml.safe_load(yaml_content)
        return OSIModel.model_validate(data)

    @staticmethod
    def save(model: OSIModel, path: Union[str, Path]) -> Path:
        path = Path(path)
        if path.suffix in (".yaml", ".yml"):
            path.parent.mkdir(parents=True, exist_ok=True)
            yaml_content = SMLSerializer.to_yaml(model)
            path.write_text(yaml_content, encoding="utf-8")
            return path
        else:
            # Folder-structure support can be added later if required.
            raise NotImplementedError("Folder-style SML save not implemented in compatibility shim.")

    @staticmethod
    def load(path: Union[str, Path]) -> OSIModel:
        path = Path(path)
        if path.is_file():
            yaml_content = path.read_text(encoding="utf-8")
            return SMLSerializer.from_yaml(yaml_content)
        raise FileNotFoundError(f"SML path not found: {path}")
    
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
            "is_measure_candidate": column.is_measure_candidate,
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
            is_measure_candidate=data.get("is_measure_candidate", False),
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
            "cross_filter": rel.cross_filter_direction.value if hasattr(rel.cross_filter_direction, "value") else str(rel.cross_filter_direction),
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
