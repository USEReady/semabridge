"""dbt Semantic Layer Ingestion Connector.

Parses dbt Core and dbt Semantic Layer (MetricFlow) manifests
(semantic_manifest.json / manifest.json) and normalizes semantic models,
dimensions, entities, and metrics into canonical OSI intermediate models.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from semabridge.intermediate.models import (
    OSIAggregationType,
    OSICardinality,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)

logger = logging.getLogger("semabridge.connectors.dbt_connector")

_DBT_AGG_MAP: Dict[str, OSIAggregationType] = {
    "sum": OSIAggregationType.SUM,
    "count": OSIAggregationType.COUNT,
    "count_distinct": OSIAggregationType.COUNT_DISTINCT,
    "average": OSIAggregationType.AVG,
    "avg": OSIAggregationType.AVG,
    "min": OSIAggregationType.MIN,
    "max": OSIAggregationType.MAX,
}


class DbtSemanticConnector:
    """Connector for dbt semantic manifests."""

    def __init__(self, manifest_data: Dict[str, Any] | str):
        if isinstance(manifest_data, str):
            try:
                self.manifest = json.loads(manifest_data)
            except Exception:
                with open(manifest_data, "r", encoding="utf-8") as f:
                    self.manifest = json.load(f)
        else:
            self.manifest = manifest_data

    def extract_osi_model(self, model_name: Optional[str] = None) -> OSIModel:
        """Parse dbt semantic models and metrics into an OSIModel."""
        datasets: List[OSIDataset] = []
        metrics: List[OSIMetric] = []
        relationships: List[OSIRelationship] = []

        semantic_models = self.manifest.get("semantic_models", [])
        dbt_metrics = self.manifest.get("metrics", [])

        # 1. Parse Semantic Models (Entities & Dimensions -> Datasets & Columns)
        measure_to_dataset: Dict[str, tuple[str, str, OSIAggregationType]] = {}

        for sm in semantic_models:
            sm_name = sm.get("name", "").upper()
            label = sm.get("label") or sm_name
            description = sm.get("description")
            node_rel = sm.get("node_relation", {})
            source_table = node_rel.get("relation_name") or sm_name

            cols: List[OSIColumn] = []

            # Entities (Primary & Foreign Keys)
            for entity in sm.get("entities", []):
                ent_name = entity.get("name", "").upper()
                is_pk = entity.get("type") == "primary"
                cols.append(
                    OSIColumn(
                        unique_name=ent_name,
                        label=ent_name,
                        data_type=OSIDataType.STRING,
                        is_key=True,
                    )
                )

            # Dimensions
            for dim in sm.get("dimensions", []):
                dim_name = dim.get("name", "").upper()
                dim_type_str = dim.get("type", "categorical")
                osi_type = OSIDataType.DATETIME if dim_type_str == "time" else OSIDataType.STRING
                cols.append(
                    OSIColumn(
                        unique_name=dim_name,
                        label=dim.get("label") or dim_name,
                        data_type=osi_type,
                        description=dim.get("description"),
                    )
                )

            # Measures defined within the semantic model
            for measure in sm.get("measures", []):
                m_name = measure.get("name", "").upper()
                agg_str = str(measure.get("agg", "sum")).lower()
                agg_type = _DBT_AGG_MAP.get(agg_str, OSIAggregationType.SUM)
                expr = measure.get("expr") or m_name
                measure_to_dataset[m_name.lower()] = (sm_name, expr, agg_type)

            datasets.append(
                OSIDataset(
                    unique_name=sm_name,
                    label=label,
                    description=description,
                    source_table=source_table,
                    columns=cols,
                )
            )

        # 2. Parse Metrics
        for dbt_m in dbt_metrics:
            m_name = dbt_m.get("name", "").upper()
            m_label = dbt_m.get("label") or m_name
            desc = dbt_m.get("description")
            type_params = dbt_m.get("type_params", {})
            measure_ref = str(type_params.get("measure", "")).lower()

            target_ds = datasets[0].unique_name if datasets else "DEFAULT"
            target_expr = m_name
            agg_type = OSIAggregationType.SUM

            if measure_ref in measure_to_dataset:
                target_ds, target_expr, agg_type = measure_to_dataset[measure_ref]

            metrics.append(
                OSIMetric(
                    unique_name=m_name,
                    label=m_label,
                    dataset=target_ds,
                    expression=f"{agg_type.value.upper()}({target_expr})",
                    aggregation=agg_type,
                    description=desc,
                )
            )

        return OSIModel(
            unique_name=(model_name or "DBT_SEMANTIC_MODEL").upper(),
            label=model_name or "dbt Semantic Model",
            source_platform="dbt",
            datasets=datasets,
            metrics=metrics,
            relationships=relationships,
        )
