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


def _escape_literal(text: Any) -> str:
    """Escape free text for a single-quoted Databricks SQL literal
    (backslash first, then the quote)."""
    return " ".join(str(text or "").split()).replace("\\", "\\\\").replace("'", "\\'")


def _escape_comment_text(text: Any) -> str:
    return " ".join(str(text or "").split()).replace("*/", "* /")


class DatabricksMetricEmitter:
    """Compiles semantic models into Databricks Unity Catalog Views and Genie spaces."""

    def __init__(
        self,
        catalog: str = "prod_catalog",
        schema: str = "analytics_semantic",
    ):
        self.catalog = _sanitize(catalog)
        self.schema = _sanitize(schema)
        # Metrics left out of the last emitted view: {metric, reason, by_design}.
        self.not_converted: List[Dict[str, Any]] = []

    def emit_unity_catalog_ddl(self, model: OSIModel) -> str:
        """Generate Databricks Unity Catalog SQL script creating metric views and metadata tags.

        The view is rooted at the first dataset and LEFT JOINs every other
        dataset reachable through the model's ACTIVE relationships, so each
        column is selected from the table that owns it. Datasets that cannot
        be joined are left out, and metrics that would need them -- or that
        have no expression at all -- are NOT fabricated: they are listed in
        ``self.not_converted`` (name, reason, by_design) and noted in the
        script, never emitted as a guessed ``SUM(<metric name>)``.
        """
        self.not_converted = []
        model_name = _sanitize(model.unique_name)
        view_name = f"{model_name}_metric_view"
        fq_view = f"{self.catalog}.{self.schema}.{view_name}"
        lines: List[str] = [
            f"-- =============================================================================",
            f"-- Databricks Unity Catalog Governed Metric View: {view_name}",
            f"-- Catalog: {self.catalog} | Schema: {self.schema}",
            f"-- =============================================================================",
            f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema};",
            "",
        ]

        datasets = list(model.datasets or [])
        primary_dataset = datasets[0].unique_name if datasets else "source_table"
        alias_of: Dict[str, str] = {}
        if datasets:
            alias_of[primary_dataset] = "t0"
        join_lines: List[str] = []
        # Breadth-first over active relationships (either direction).
        rels = [r for r in (getattr(model, "relationships", None) or []) if getattr(r, "is_active", True)]
        changed = True
        while changed:
            changed = False
            for r in rels:
                a, b = r.from_dataset, r.to_dataset
                if a in alias_of and b not in alias_of:
                    known, new_ds, known_cols, new_cols = a, b, r.from_columns, r.to_columns
                elif b in alias_of and a not in alias_of:
                    known, new_ds, known_cols, new_cols = b, a, r.to_columns, r.from_columns
                else:
                    continue
                if not any(d.unique_name == new_ds for d in datasets):
                    continue
                alias_of[new_ds] = f"t{len(alias_of)}"
                on = " AND ".join(
                    f"{alias_of[known]}.{_sanitize(kc)} = {alias_of[new_ds]}.{_sanitize(nc)}"
                    for kc, nc in zip(known_cols, new_cols)
                )
                join_lines.append(
                    f"LEFT JOIN {self.catalog}.{self.schema}.{_sanitize(new_ds)} {alias_of[new_ds]} ON {on}"
                )
                changed = True

        select_clauses: List[str] = []
        tag_statements: List[str] = []
        used: set = set()
        unjoined = [d.unique_name for d in datasets if d.unique_name not in alias_of]
        for ds in datasets:
            if ds.unique_name not in alias_of:
                continue
            for col in ds.columns:
                c_name = _sanitize(col.unique_name)
                out_name = c_name if c_name not in used else f"{_sanitize(ds.unique_name)}_{c_name}"
                used.add(out_name)
                select_clauses.append(f"    {alias_of[ds.unique_name]}.{c_name} AS {out_name}")
                if col.description:
                    tag_statements.append(
                        f"ALTER VIEW {fq_view} ALTER COLUMN {out_name} COMMENT '{_escape_literal(col.description)}';"
                    )

        def _not_converted(name: str, reason: str, by_design: bool) -> None:
            self.not_converted.append({"metric": name, "reason": reason, "by_design": by_design})
            lines.append(f"-- NOT CONVERTED: {_sanitize(name)} -- {_escape_comment_text(reason)}")

        for m in model.metrics:
            m_name = _sanitize(m.unique_name)
            expr = (m.expression or "").strip()
            if not expr:
                _not_converted(m.unique_name, "metric has no SQL expression to emit (nothing is fabricated)", False)
                continue
            needs = [d for d in unjoined if re.search(rf"\b{re.escape(_sanitize(d))}\b", expr, re.IGNORECASE)]
            if getattr(m, "dataset", None) in unjoined:
                needs.append(m.dataset)
            if needs:
                _not_converted(
                    m.unique_name,
                    f"not applicable: needs dataset(s) {sorted(set(needs))} that are not connected to "
                    f"'{primary_dataset}' by an active relationship, so they cannot be joined into this view",
                    True,
                )
                continue
            select_clauses.append(f"    {expr} AS {m_name}")
            if m.description:
                tag_statements.append(
                    f"ALTER VIEW {fq_view} ALTER COLUMN {m_name} COMMENT '{_escape_literal(m.description)}';"
                )
            steward_val = _escape_literal(m.business_owner or "Enterprise")
            tag_statements.append(
                f"ALTER VIEW {fq_view} "
                f"ALTER COLUMN {m_name} SET TAGS ('metric_type' = 'GOVERNED_KPI', 'steward' = '{steward_val}');"
            )

        if not select_clauses:
            select_clauses.append("    *")

        lines.append(f"CREATE OR REPLACE VIEW {fq_view}")
        lines.append(f"COMMENT '{_escape_literal(f'SemaBridge Enterprise Governed Semantic View for {model.unique_name}')}'")
        lines.append("AS SELECT")
        lines.append(",\n".join(select_clauses))
        lines.append(f"FROM {self.catalog}.{self.schema}.{_sanitize(primary_dataset)} t0")
        lines.extend(join_lines)
        lines.append("GROUP BY ALL;")
        lines.append("")

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
