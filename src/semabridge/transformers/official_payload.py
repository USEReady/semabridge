from __future__ import annotations

from typing import Any, Dict

from semabridge.sml.models import SMLModel


def smlmodel_to_official_payload(model: SMLModel, target_platform: str = "snowflake") -> Dict[str, Any]:
    """Map an `SMLModel` to a plain dict following the official semantic
    payload shape (Datasets, Metrics, Dimensions, Relationships).

    The mapper intentionally includes only official fields and drops any
    custom/implementation-specific keys.
    """
    payload: Dict[str, Any] = {
        "model_name": model.unique_name,
        "version": model.version,
        "label": model.label,
        "description": model.description,
        "source_platform": model.source_platform.value if getattr(model, "source_platform", None) else None,
        "target_platform": target_platform,
        "datasets": [],
        "metrics": [],
        "dimensions": [],
        "relationships": [],
    }

    for ds in model.datasets:
        payload["datasets"].append(
            {
                "unique_name": ds.unique_name,
                "label": ds.label,
                "description": ds.description,
                "source_table": getattr(ds, "source_table", ""),
                "source_schema": getattr(ds, "source_schema", ""),
                "columns": [
                    {
                        "unique_name": c.unique_name,
                        "label": c.label,
                        "data_type": getattr(c.data_type, "value", str(c.data_type)),
                        "source_type": c.source_type,
                        "is_key": c.is_key,
                        "is_hidden": c.is_hidden,
                        "is_measure_candidate": c.is_measure_candidate,
                        "format_string": c.format_string,
                        "folder": c.folder,
                        "synonyms": list(c.synonyms or []),
                    }
                    for c in ds.columns
                ],
                "is_fact": ds.is_fact,
                "row_count": ds.row_count,
            }
        )

    for m in model.metrics:
        confidence = getattr(m, "confidence", None)
        payload["metrics"].append(
            {
                "unique_name": m.unique_name,
                "label": m.label,
                "description": m.description,
                "dataset": m.dataset,
                "expression": m.expression,
                "sql_expression": m.sql_expression,
                "aggregation": getattr(m.aggregation, "value", str(m.aggregation)),
                "source_column": m.source_column,
                "folder": m.folder,
                "is_hidden": m.is_hidden,
                "sync_enabled": m.sync_enabled,
                "confidence": 1.0 if confidence is None else float(confidence),
                "depends_on_measures": list(getattr(m, "depends_on_measures", [])),
            }
        )

    for d in model.dimensions:
        payload["dimensions"].append(
            {
                "unique_name": d.unique_name,
                "label": d.label,
                "description": d.description,
                "dataset": d.dataset,
                "attributes": [
                    {
                        "unique_name": a.unique_name,
                        "label": a.label,
                        "dataset": a.dataset,
                        "source_column": getattr(a, "source_column", None) or getattr(a, "dataset_column", None),
                        "is_hidden": a.is_hidden,
                    }
                    for a in d.attributes
                ],
                "hierarchies": [
                    {"unique_name": h.unique_name, "levels": [l.unique_name for l in h.levels]} for h in d.hierarchies
                ],
                "is_hidden": d.is_hidden,
            }
        )

    for r in model.relationships:
        payload["relationships"].append(
            {
                "unique_name": r.unique_name,
                "from_dataset": r.from_dataset,
                "from_columns": list(r.from_columns or []),
                "to_dataset": r.to_dataset,
                "to_columns": list(r.to_columns or []),
                "cardinality": getattr(r.cardinality, "value", str(r.cardinality)),
                "cross_filter": getattr(r.cross_filter_direction, "value", str(r.cross_filter_direction)),
                "is_active": bool(getattr(r, "is_active", True)),
            }
        )

    return payload
