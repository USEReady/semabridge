from __future__ import annotations

import yaml

from semabridge.connectors.snowflake_emitter_parts import metric_helpers as _metric_helpers
from semabridge.connectors.snowflake_emitter_parts.yaml_utils import IndentDumper
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def generate_ddls(emitter, sml):
    if not sml.datasets:
        return []
    logger.info(
        "generate_ddls: building semantic view for model=%s datasets=%s metrics=%s",
        sml.unique_name or sml.label or "<unnamed_sml_model>",
        len(getattr(sml, "datasets", []) or []),
        len(getattr(sml, "metrics", []) or []),
    )
    return [emitter._generate_semantic_view(sml)]


def generate_cortex_yaml(emitter, sml, sample_fetcher=None):
    output = {
        "semantic_model": {
            "name": sml.unique_name,
            "node_type": "semantic_model" if emitter.behavior.features.enable_cortex_analyst else "unknown",
            "tables": [],
        }
    }
    for ds in sml.datasets:
        safe_table = emitter._safe_table_name(ds.source_table or ds.unique_name)
        table_def = {
            "name": ds.unique_name,
            "base_table": {
                "database": emitter.config.database,
                "schema": emitter.config.schema_name,
                "table": safe_table,
            },
            "dimensions": [],
            "measures": [],
        }
        for dim in sml.dimensions:
            for attr in dim.attributes:
                if attr.dataset == ds.unique_name:
                    attr_col = getattr(attr, "dataset_column", None) or getattr(attr, "source_column", None)
                    is_measure = any(m.dataset == ds.unique_name and m.source_column == attr_col for m in sml.metrics)
                    if is_measure and ds.is_fact:
                        continue
                    dim_def = {
                        "name": attr.unique_name,
                        "expr": getattr(attr, "dataset_column", None) or getattr(attr, "source_column", attr.unique_name),
                        "description": getattr(attr, "description", "") or "",
                    }
                    if sample_fetcher is not None:
                        dim_samples = sample_fetcher(ds.unique_name, attr_col)
                        if dim_samples:
                            dim_def["sample_values"] = dim_samples
                    from semabridge.utils.synonyms import lookup_attribute_synonyms
                    dim_synonyms = lookup_attribute_synonyms(attr, ds, attr_col)
                    if dim_synonyms:
                        dim_def["synonyms"] = dim_synonyms
                    table_def["dimensions"].append(dim_def)
        for metric in sml.metrics:
            if metric.dataset == ds.unique_name:
                measure_def = {
                    "name": metric.unique_name,
                    "description": metric.description or "",
                }
                if metric.sql_expression:
                    measure_def["expr"] = _metric_helpers.sanitize_sql_markdown(metric.sql_expression)
                elif metric.expression:
                    measure_def["expr"] = "NULL"
                    dax_note = (
                        f" [DAX: {metric.expression[:100]}"
                        f"{'...' if len(metric.expression) > 100 else ''}]"
                    )
                    measure_def["description"] = (measure_def["description"] + dax_note).strip()
                else:
                    continue
                # Metrics are aggregate expressions, so a general "sample value" for a
                # metric isn't well-defined. Only sample when the metric is a direct
                # pass-through aggregation of a single known source column.
                if sample_fetcher is not None and metric.source_column:
                    metric_samples = sample_fetcher(ds.unique_name, metric.source_column)
                    if metric_samples:
                        measure_def["sample_values"] = metric_samples
                if getattr(metric, "synonyms", None):
                    measure_def["synonyms"] = list(metric.synonyms)
                table_def["measures"].append(measure_def)
        output["semantic_model"]["tables"].append(table_def)
    return yaml.dump(output, sort_keys=False, Dumper=IndentDumper, allow_unicode=True, width=200)


def generate_ddls_from_osi(emitter, osi):
    if not osi.datasets:
        return []
    return [emitter._generate_semantic_view_from_osi(osi)]


def generate_cortex_yaml_from_osi(emitter, osi, sample_fetcher=None):
    output = {
        "semantic_model": {
            "name": osi.unique_name,
            "node_type": (
                "semantic_model"
                if emitter.behavior.features.enable_cortex_analyst
                else "unknown"
            ),
            "tables": [],
        }
    }

    for ds in osi.datasets:
        safe_table = emitter._safe_table_name(ds.source_table or ds.unique_name)
        table_def = {
            "name": ds.unique_name,
            "base_table": {
                "database": emitter.config.database,
                "schema": emitter.config.schema_name,
                "table": safe_table,
            },
            "dimensions": [],
            "measures": [],
        }

        for dim in osi.dimensions:
            for attr in dim.attributes:
                if attr.dataset == ds.unique_name:
                    is_measure = any(
                        m.dataset == ds.unique_name
                        and m.source_column == attr.source_column
                        for m in osi.metrics
                    )
                    if is_measure and ds.is_fact:
                        continue
                    dim_def = {
                        "name": attr.unique_name,
                        "expr": attr.source_column,
                        "description": attr.label or "",
                    }
                    if sample_fetcher is not None:
                        dim_samples = sample_fetcher(ds.unique_name, attr.source_column)
                        if dim_samples:
                            dim_def["sample_values"] = dim_samples
                    from semabridge.utils.synonyms import lookup_attribute_synonyms
                    dim_synonyms = lookup_attribute_synonyms(attr, ds, attr.source_column)
                    if dim_synonyms:
                        dim_def["synonyms"] = dim_synonyms
                    table_def["dimensions"].append(dim_def)

        for metric in osi.metrics:
            if metric.dataset == ds.unique_name:
                measure_def = {
                    "name": metric.unique_name,
                    "description": metric.description or "",
                }
                if metric.sql_expression:
                    measure_def["expr"] = _metric_helpers.sanitize_sql_markdown(metric.sql_expression)
                elif metric.expression:
                    measure_def["expr"] = "NULL"
                    dax_note = (
                        f" [DAX: {metric.expression[:100]}"
                        f"{'...' if len(metric.expression) > 100 else ''}]"
                    )
                    measure_def["description"] = (measure_def["description"] + dax_note).strip()
                else:
                    continue
                # Metrics are aggregate expressions, so a general "sample value" for a
                # metric isn't well-defined. Only sample when the metric is a direct
                # pass-through aggregation of a single known source column.
                if sample_fetcher is not None and metric.source_column:
                    metric_samples = sample_fetcher(ds.unique_name, metric.source_column)
                    if metric_samples:
                        measure_def["sample_values"] = metric_samples
                if getattr(metric, "synonyms", None):
                    measure_def["synonyms"] = list(metric.synonyms)
                table_def["measures"].append(measure_def)

        output["semantic_model"]["tables"].append(table_def)

    return yaml.dump(output, sort_keys=False, Dumper=IndentDumper, allow_unicode=True, width=200)
