"""Databricks Unity Catalog Metric Emitter & Genie Space Compiler.

Compiles OSI models and federated enterprise ontologies into:
1. Databricks Unity Catalog Metric Views with column tags and governance comments.
2. Databricks Genie AI Semantic Space configurations for conversational analytics.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
import yaml

from semabridge.intermediate.models import OSIModel

logger = logging.getLogger("semabridge.connectors.databricks_metric_emitter")


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name).lower()


class DatabricksMetricEmitter:
    """Compiles semantic models into Databricks Unity Catalog Views and Genie spaces."""

    def __init__(
        self,
        catalog: str = "prod_catalog",
        schema: str = "analytics_semantic",
    ):
        self.catalog = _sanitize(catalog)
        self.schema = _sanitize(schema)

    def emit_unity_catalog_ddl(self, model: OSIModel) -> str:
        """Generate Databricks Unity Catalog SQL script creating metric views and metadata tags."""
        model_name = _sanitize(model.unique_name)
        view_name = f"{model_name}_metric_view"
        lines: List[str] = [
            f"-- =============================================================================",
            f"-- Databricks Unity Catalog Governed Metric View: {view_name}",
            f"-- Catalog: {self.catalog} | Schema: {self.schema}",
            f"-- =============================================================================",
            f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema};",
            "",
        ]

        # Gather metrics and dimensions
        primary_dataset = model.datasets[0].unique_name if model.datasets else "source_table"

        select_clauses: List[str] = []
        tag_statements: List[str] = []

        # Add dimension columns
        for ds in model.datasets:
            for col in ds.columns:
                c_name = _sanitize(col.unique_name)
                select_clauses.append(f"    {c_name}")
                if col.description:
                    escaped_col_desc = col.description.replace("'", "''")
                    tag_statements.append(
                        f"ALTER VIEW {self.catalog}.{self.schema}.{view_name} "
                        f"ALTER COLUMN {c_name} COMMENT '{escaped_col_desc}';"
                    )

        # Add metric calculations
        for m in model.metrics:
            m_name = _sanitize(m.unique_name)
            expr = m.expression or f"SUM({m_name})"
            select_clauses.append(f"    {expr} AS {m_name}")
            if m.description:
                escaped_m_desc = m.description.replace("'", "''")
                tag_statements.append(
                    f"ALTER VIEW {self.catalog}.{self.schema}.{view_name} "
                    f"ALTER COLUMN {m_name} COMMENT '{escaped_m_desc}';"
                )
            steward_val = m.business_owner or "Enterprise"
            tag_statements.append(
                f"ALTER VIEW {self.catalog}.{self.schema}.{view_name} "
                f"ALTER COLUMN {m_name} SET TAGS ('metric_type' = 'GOVERNED_KPI', 'steward' = '{steward_val}');"
            )

        if not select_clauses:
            select_clauses.append("    *")

        lines.append(f"CREATE OR REPLACE VIEW {self.catalog}.{self.schema}.{view_name}")
        lines.append(f"COMMENT 'SemaBridge Enterprise Governed Semantic View for {model.unique_name}'")
        lines.append("AS SELECT")
        lines.append(",\n".join(select_clauses))
        lines.append(f"FROM {self.catalog}.{self.schema}.{_sanitize(primary_dataset)}")
        lines.append("GROUP BY ALL;")
        lines.append("")

        # Add tags and comments
        if tag_statements:
            lines.append("-- Column Comments and Governance Tags")
            lines.extend(tag_statements)
            lines.append("")

        return "\n".join(lines)

    def emit_genie_space_spec(self, model: OSIModel) -> Dict[str, Any]:
        """Compile model into Databricks Genie Space specification for LLM analytics."""
        model_name = _sanitize(model.unique_name)
        tables = []
        for ds in model.datasets:
            cols = []
            for c in ds.columns:
                cols.append({
                    "name": c.unique_name,
                    "description": c.description or "",
                    "synonyms": c.synonyms,
                })
            tables.append({
                "table_name": f"{self.catalog}.{self.schema}.{_sanitize(ds.unique_name)}",
                "description": ds.description or "",
                "columns": cols,
            })

        measures = []
        for m in model.metrics:
            measures.append({
                "metric_name": m.unique_name,
                "formula": m.expression,
                "description": m.description or "",
                "synonyms": m.synonyms,
                "steward": m.business_owner or "",
            })

        return {
            "version": "1.0",
            "space_name": f"{model.unique_name} Genie Space",
            "description": f"Governed semantic space for {model.unique_name} generated by SemaBridge",
            "catalog": self.catalog,
            "schema": self.schema,
            "tables": tables,
            "measures": measures,
        }
