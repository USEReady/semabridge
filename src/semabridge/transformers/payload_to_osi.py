from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime

from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIDimension,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIRelationship,
)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_enum(enum_cls: Any, value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return enum_cls(value)
    except Exception:
        try:
            return enum_cls(str(value).replace("-", "_").lower())
        except Exception:
            return default


def payload_to_osi(payload: Dict[str, Any]) -> OSIModel:
    """Convert an official semantic payload dict into an OSIModel.

    This function is intentionally permissive: it extracts only the
    documented official fields and provides reasonable defaults for missing
    optional values.
    """
    unique_name = payload.get("model_name") or payload.get("unique_name") or "model"
    label = payload.get("label") or unique_name
    description = payload.get("description")
    version = payload.get("version") or payload.get("version_tag") or "1.0"

    datasets: List[OSIDataset] = []
    for ds in payload.get("datasets", []) or []:
        cols: List[OSIColumn] = []
        for c in ds.get("columns", []) or []:
            col = OSIColumn(
                unique_name=c.get("unique_name") or c.get("name"),
                label=c.get("label") or c.get("unique_name") or c.get("name"),
                data_type=_normalize_enum(type(OSIColumn.model_fields["data_type"].annotation), c.get("data_type") or c.get("type"), None) or _normalize_enum(
                    __import__("semabridge.intermediate.models", fromlist=["OSIDataType"]).OSIDataType,
                    c.get("data_type") or c.get("type"),
                    __import__("semabridge.intermediate.models", fromlist=["OSIDataType"]).OSIDataType.STRING,
                ),
                description=c.get("description"),
                is_key=bool(c.get("is_key", False)),
                is_hidden=bool(c.get("is_hidden", False)),
                is_measure_candidate=bool(c.get("is_measure_candidate", False)),
                default_aggregation=_normalize_enum(
                    __import__("semabridge.intermediate.models", fromlist=["OSIAggregationType"]).OSIAggregationType,
                    c.get("aggregation"),
                    None,
                ),
                format_string=c.get("format_string"),
                source_expression=c.get("source_expression"),
                synonyms=list(c.get("synonyms", []) or []),
            )
            cols.append(col)

        ds_obj = OSIDataset(
            unique_name=ds.get("unique_name") or ds.get("name"),
            label=ds.get("label") or ds.get("unique_name") or ds.get("name"),
            description=ds.get("description"),
            source_table=ds.get("source_table"),
            source_schema=ds.get("source_schema"),
            columns=cols,
            is_fact=bool(ds.get("is_fact", False)),
            is_hidden=bool(ds.get("is_hidden", False)),
        )
        datasets.append(ds_obj)

    dataset_columns_by_name: Dict[str, set[str]] = {}
    for ds_obj in datasets:
        dataset_columns_by_name[str(ds_obj.unique_name)] = {
            str(col.unique_name) for col in (ds_obj.columns or [])
        }

    metrics: List[OSIMetric] = []
    for m in payload.get("metrics", []) or []:
        metric_name = m.get("unique_name") or m.get("name")
        metric_dataset = m.get("dataset")
        metric_expression = m.get("expression") or m.get("sql_expression") or m.get("expr")
        metric_source_column = m.get("source_column")
        if not metric_source_column and not metric_expression and metric_name and metric_dataset:
            dataset_cols = dataset_columns_by_name.get(str(metric_dataset), set())
            metric_name_str = str(metric_name)
            if metric_name_str in dataset_cols:
                metric_source_column = metric_name_str

        metric = OSIMetric(
            unique_name=metric_name,
            label=m.get("label") or metric_name,
            dataset=metric_dataset,
            source_column=metric_source_column,
            expression=metric_expression,
            aggregation=_normalize_enum(
                __import__("semabridge.intermediate.models", fromlist=["OSIAggregationType"]).OSIAggregationType,
                m.get("aggregation") or m.get("aggr"),
                __import__("semabridge.intermediate.models", fromlist=["OSIAggregationType"]).OSIAggregationType.SUM,
            ),
            description=m.get("description"),
            format_string=m.get("format_string"),
            business_owner=m.get("business_owner"),
            is_hidden=bool(m.get("is_hidden", False)),
        )
        metrics.append(metric)

    dimensions: List[OSIDimension] = []
    for d in payload.get("dimensions", []) or []:
        attrs: List[OSIAttribute] = []
        for a in d.get("attributes", []) or []:
            attr = OSIAttribute(
                unique_name=a.get("unique_name") or a.get("name"),
                label=a.get("label") or a.get("unique_name") or a.get("name"),
                dataset=a.get("dataset") or d.get("dataset"),
                source_column=a.get("source_column") or a.get("dataset_column"),
                is_hidden=bool(a.get("is_hidden", False)),
            )
            attrs.append(attr)

        hierarchies: List[OSIHierarchy] = []
        for h in d.get("hierarchies", []) or []:
            levels: List[OSILevel] = []
            for l in h.get("levels", []) or []:
                lvl = OSILevel(unique_name=l.get("unique_name") or l.get("name"), label=l.get("label") or l.get("unique_name"), attribute=l.get("attribute") or l.get("source_column"))
                levels.append(lvl)
            hier = OSIHierarchy(unique_name=h.get("unique_name") or h.get("name"), label=h.get("label") or h.get("unique_name"), levels=levels)
            hierarchies.append(hier)

        dim = OSIDimension(
            unique_name=d.get("unique_name") or d.get("name"),
            label=d.get("label") or d.get("unique_name") or d.get("name"),
            description=d.get("description"),
            dataset=d.get("dataset"),
            attributes=attrs,
            hierarchies=hierarchies,
            is_hidden=bool(d.get("is_hidden", False)),
        )
        dimensions.append(dim)

    relationships: List[OSIRelationship] = []
    for r in payload.get("relationships", []) or []:
        rel = OSIRelationship(
            unique_name=r.get("unique_name") or r.get("name"),
            from_dataset=r.get("from_dataset"),
                from_columns=_as_list(r.get("from_columns") or r.get("from_column")),
            to_dataset=r.get("to_dataset"),
                to_columns=_as_list(r.get("to_columns") or r.get("to_column")),
                cardinality=_normalize_enum(
                    __import__("semabridge.intermediate.models", fromlist=["OSICardinality"]).OSICardinality,
                    r.get("cardinality") or r.get("cardinality_type"),
                    __import__("semabridge.intermediate.models", fromlist=["OSICardinality"]).OSICardinality.MANY_TO_ONE,
                ),
                cross_filter_direction=_normalize_enum(
                    __import__("semabridge.intermediate.models", fromlist=["OSICrossFilterDirection"]).OSICrossFilterDirection,
                    r.get("cross_filter") or r.get("cross_filter_direction"),
                    __import__("semabridge.intermediate.models", fromlist=["OSICrossFilterDirection"]).OSICrossFilterDirection.SINGLE,
                ),
            is_active=bool(r.get("is_active", True)),
        )
        relationships.append(rel)

    osi = OSIModel(
        unique_name=unique_name,
        label=label,
        description=description,
        version=str(version),
        datasets=datasets,
        metrics=metrics,
        dimensions=dimensions,
        relationships=relationships,
        source_platform=payload.get("source_platform"),
        created_at=datetime.utcnow(),
        metadata=payload.get("metadata") or {},
    )
    return osi
