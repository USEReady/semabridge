from __future__ import annotations

from typing import Any, Dict, List, Optional

from semabridge.intermediate.models import (
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDimension,
    OSIAttribute,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.utils.relationship_naming import generate_relationship_name


class OSIBuilder:
    """
    Constructs intermediate OSI (Open Semantic Interchange) model components:
    - OSIDataset
    - OSIColumn
    - OSIMetric
    - OSIRelationship
    - OSIDimension
    """

    @classmethod
    def build_dataset(cls, table_name: str, raw_columns: List[Dict[str, Any]]) -> OSIDataset:
        """Build OSIDataset from raw columns."""
        columns = []
        for col in raw_columns:
            col_name = col["name"]
            raw_dt = col.get("dataType", "string")
            mapped_dt = cls.map_datatype(raw_dt)

            format_string = col.get("formatString")
            summarize_by = col.get("summarizeBy")
            default_aggregation = cls.map_summarize_by(summarize_by)
            is_measure_candidate = default_aggregation is not None

            # Primary key/Foreign key identifier heuristic
            is_key = False
            upper_name = col_name.upper()
            if upper_name.endswith("ID") or upper_name.endswith("KEY") or upper_name.startswith("PK_") or upper_name.startswith("FK_"):
                is_key = True

            columns.append(
                OSIColumn(
                    unique_name=col_name,
                    label=col_name,
                    data_type=mapped_dt,
                    description="",
                    is_hidden=col.get("isHidden", False),
                    is_measure_candidate=is_measure_candidate,
                    default_aggregation=default_aggregation,
                    format_string=format_string,
                    is_key=is_key,
                    source_expression=col.get("sourceColumn") or col.get("expression") or "",
                    synonyms=[],
                    is_enum=(mapped_dt == OSIDataType.BOOLEAN)
                )
            )

        return OSIDataset(
            unique_name=table_name,
            label=table_name,
            description="",
            is_hidden=False,
            columns=columns,
            source_table=table_name
        )

    @staticmethod
    def build_metric(measure_def: Dict[str, Any], dataset_name: str) -> OSIMetric:
        """Build OSIMetric from a parsed measure definition."""
        name = measure_def["name"]
        expr = measure_def["expression"]

        # Access modifier heuristic
        is_helper = (
            name.startswith("_")
            or any(name.lower().endswith(suffix) for suffix in (" helper", " base", " temp", " internal"))
            or measure_def.get("isHidden", False)
        )
        access_modifier = "private_access" if is_helper else "public_access"

        return OSIMetric(
            unique_name=name,
            label=name,
            dataset=dataset_name,
            expression=expr,
            aggregation=OSIAggregationType.NONE,
            description=measure_def.get("description"),
            format_string=measure_def.get("formatString"),
            is_hidden=measure_def.get("isHidden", False),
            access_modifier=access_modifier,
            synonyms=[],
        )

    @staticmethod
    def build_relationship(rel_def: Dict[str, Any]) -> OSIRelationship:
        """Build OSIRelationship from parsed relationship definition."""
        from_table = rel_def["fromTable"]
        from_column = rel_def["fromColumn"]
        to_table = rel_def["toTable"]
        to_column = rel_def["toColumn"]
        is_active = rel_def.get("isActive", True)

        return OSIRelationship(
            unique_name=generate_relationship_name(from_table, from_column, to_table, to_column),
            from_dataset=from_table,
            from_columns=[from_column],
            to_dataset=to_table,
            to_columns=[to_column],
            cardinality=OSICardinality.MANY_TO_ONE,
            cross_filter_direction=OSICrossFilterDirection.SINGLE,
            is_active=is_active,
        )

    @staticmethod
    def build_dimension_from_dataset(dataset: OSIDataset) -> Optional[OSIDimension]:
        """Build implicit OSIDimension from OSIDataset."""
        if dataset.is_hidden:
            return None

        attributes = []
        for col in dataset.columns:
            if col.is_hidden or col.is_measure_candidate:
                continue

            attr = OSIAttribute(
                unique_name=col.unique_name,
                label=col.label,
                dataset=dataset.unique_name,
                source_column=col.unique_name,
                is_hidden=col.is_hidden
            )
            attributes.append(attr)

        if not attributes:
            return None

        return OSIDimension(
            unique_name=dataset.unique_name,
            label=dataset.label,
            description=dataset.description,
            dataset=dataset.unique_name,
            attributes=attributes,
            is_hidden=dataset.is_hidden
        )

    @staticmethod
    def build_metrics_from_aggregation_columns(dataset: OSIDataset) -> List[OSIMetric]:
        """Create explicit aggregation OSIMetrics for summarizeBy columns."""
        metrics: List[OSIMetric] = []
        seen: set[str] = set()
        for col in dataset.columns:
            if col.is_hidden or not col.is_measure_candidate:
                continue
            aggregation = col.default_aggregation or OSIAggregationType.SUM
            metric_name = col.unique_name
            metric_key = metric_name.casefold()
            if metric_key in seen:
                continue
            seen.add(metric_key)
            metrics.append(
                OSIMetric(
                    unique_name=metric_name,
                    label=col.label or metric_name,
                    dataset=dataset.unique_name,
                    source_column=col.unique_name,
                    expression=None,
                    aggregation=aggregation,
                    description=col.description,
                    format_string=col.format_string,
                    is_hidden=col.is_hidden,
                    access_modifier="public_access",
                    synonyms=[],
                )
            )
        return metrics

    @staticmethod
    def map_datatype(dt_str: str) -> OSIDataType:
        """Map raw TMDL data types to OSIDataType."""
        type_map = {
            "int64": OSIDataType.INTEGER,
            "double": OSIDataType.FLOAT,
            "decimal": OSIDataType.DECIMAL,
            "boolean": OSIDataType.BOOLEAN,
            "dateTime": OSIDataType.DATETIME,
            "string": OSIDataType.STRING,
            "binary": OSIDataType.BINARY,
            "date": OSIDataType.DATE,
            "time": OSIDataType.TIME,
            # Common variants
            "bool": OSIDataType.BOOLEAN,
            "currency": OSIDataType.DECIMAL,
        }
        return type_map.get(dt_str.lower(), OSIDataType.STRING)

    @staticmethod
    def map_summarize_by(summarize_by: Optional[str]) -> Optional[OSIAggregationType]:
        """Map summarizeBy string to OSIAggregationType."""
        if not summarize_by:
            return None
        normalized = summarize_by.strip().lower()
        if normalized == "none":
            return None
        mapping = {
            "sum": OSIAggregationType.SUM,
            "average": OSIAggregationType.AVG,
            "avg": OSIAggregationType.AVG,
            "count": OSIAggregationType.COUNT,
            "distinctcount": OSIAggregationType.COUNT_DISTINCT,
            "countdistinct": OSIAggregationType.COUNT_DISTINCT,
            "min": OSIAggregationType.MIN,
            "max": OSIAggregationType.MAX,
        }
        return mapping.get(normalized, OSIAggregationType.SUM)
