"""Natural Language to Semantic Query & SQL Engine.

Interprets analytical questions in plain English, resolves them against governed
domain ontologies, determines optimal join paths, and generates executable SQL.
Operates completely locally with zero Snowflake credit consumption.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from semabridge.ai.semantic_search import SearchHit, SemanticSearchIndex
from semabridge.ontology.models import (
    DomainConcept,
    DomainMetric,
    DomainRelationship,
    FederatedEnterpriseOntology,
)

logger = logging.getLogger("semabridge.ai.nl_query_engine")


class QueryResolutionPlan(BaseModel):
    """Execution plan generated from a natural language query."""
    original_prompt: str
    detected_metrics: List[str]
    detected_dimensions: List[str]
    filters: List[str] = Field(default_factory=list)
    resolved_tables: List[str]
    join_clauses: List[str] = Field(default_factory=list)
    generated_sql: str
    confidence_score: float
    explanation: str


class NLQueryEngine:
    """Interprets natural language questions and compiles them into governed SQL."""

    def __init__(self, search_index: SemanticSearchIndex, ontology: FederatedEnterpriseOntology):
        self.search_index = search_index
        self.ontology = ontology

    def resolve_query(self, prompt: str, dialect: str = "SNOWFLAKE") -> QueryResolutionPlan:
        """Parse natural language prompt, find matching metrics/dimensions, and compile SQL."""
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        clean_prompt = prompt.strip()

        # 1. Search for matching metrics
        metric_hits = self.search_index.search(
            clean_prompt,
            item_type_filter="METRIC",
            limit=5,
            min_score=0.20,
        )

        # 2. Search for matching concepts / dimensions
        concept_hits = self.search_index.search(
            clean_prompt,
            item_type_filter="CONCEPT",
            limit=5,
            min_score=0.20,
        )

        detected_metrics: List[DomainMetric] = []
        seen_m = set()
        for hit in metric_hits:
            if hit.name in self.ontology.metrics and hit.name not in seen_m:
                seen_m.add(hit.name)
                detected_metrics.append(self.ontology.metrics[hit.name])

        detected_concepts: List[DomainConcept] = []
        seen_c = set()
        for hit in concept_hits:
            if hit.name in self.ontology.concepts and hit.name not in seen_c:
                seen_c.add(hit.name)
                detected_concepts.append(self.ontology.concepts[hit.name])

        # If no metrics detected, fallback to search top overall hit
        if not detected_metrics:
            generic_hits = self.search_index.search(clean_prompt, limit=5)
            for h in generic_hits:
                if h.item_type == "METRIC" and h.name in self.ontology.metrics and h.name not in seen_m:
                    seen_m.add(h.name)
                    detected_metrics.append(self.ontology.metrics[h.name])
                elif h.item_type == "CONCEPT" and h.name in self.ontology.concepts and h.name not in seen_c:
                    seen_c.add(h.name)
                    detected_concepts.append(self.ontology.concepts[h.name])
                    detected_concepts.append(self.ontology.concepts[h.name])

        # Extract potential time filters from prompt (e.g. 2024, 2023, Q1, Q2)
        filters: List[str] = []
        year_match = re.search(r"\b(20[12][0-9])\b", clean_prompt)
        if year_match:
            filters.append(f"YEAR(date) = {year_match.group(1)}")

        quarter_match = re.search(r"\b(Q[1-4])\b", clean_prompt, re.IGNORECASE)
        if quarter_match:
            filters.append(f"QUARTER(date) = '{quarter_match.group(1).upper()}'")

        # Determine target tables and relationships
        tables: Set[str] = set()
        for m in detected_metrics:
            # Check if metric specifies a semantic view or bound dataset
            tbl = m.semantic_view_name or (m.domain.upper() + "_METRICS_VIEW" if m.domain else "ANALYTICS_VIEW")
            tables.add(tbl)

        for c in detected_concepts:
            tbl = c.physical_table or c.name
            tables.add(tbl)

        if not tables:
            tables.add("SEMANTIC_VIEW")

        # Resolve join paths from ontology relationships
        join_clauses: List[str] = []
        resolved_tables_list = list(tables)
        primary_table = resolved_tables_list[0]

        for rel in self.ontology.relationships:
            from_t = rel.from_concept
            to_t = rel.to_concept
            if (from_t in tables or to_t in tables) and rel.join_expression:
                join_clauses.append(f"LEFT JOIN {to_t} ON {rel.join_expression}")

        # 3. Assemble Governed SQL
        select_parts: List[str] = []
        group_by_parts: List[str] = []

        dim_names = [c.name for c in detected_concepts]
        for idx, dim in enumerate(dim_names, 1):
            select_parts.append(f"    {dim}")
            group_by_parts.append(str(idx))

        metric_names = []
        for m in detected_metrics:
            metric_names.append(m.name)
            expr = m.sql_expression or f"SUM({m.name})"
            select_parts.append(f"    {expr} AS {m.name}")

        if not select_parts:
            select_parts.append("    *")

        target_engine = "Snowflake Cortex Analyst & Semantic View" if dialect.upper() == "SNOWFLAKE" else "Databricks AI/BI Genie & Unity Catalog Metric View" if dialect.upper() == "DATABRICKS" else "SemaBridge Local Engine"
        sql_lines: List[str] = []
        sql_lines.append(f"-- SemaBridge AI Generated Governed Semantic Query")
        sql_lines.append(f"-- Target Engine: {target_engine}")
        sql_lines.append(f"-- Question: \"{clean_prompt}\"")
        sql_lines.append(f"-- Dialect: {dialect.upper()}")
        sql_lines.append("SELECT")
        sql_lines.append(",\n".join(select_parts))
        sql_lines.append(f"FROM {primary_table}")

        for j in join_clauses:
            sql_lines.append(f"{j}")

        if filters:
            sql_lines.append(f"WHERE {' AND '.join(filters)}")

        if group_by_parts:
            sql_lines.append(f"GROUP BY {', '.join(group_by_parts)}")

        sql_lines.append("ORDER BY 1 DESC")
        sql_lines.append("LIMIT 100;")

        generated_sql = "\n".join(sql_lines)
        if dialect.upper() == "DATABRICKS":
            # Adapt any Snowflake-specific IFF to Databricks standard IF
            generated_sql = re.sub(r"\bIFF\(", "IF(", generated_sql, flags=re.IGNORECASE)

        # 4. Compute Confidence Score & Explanation
        metric_score = 0.5 if detected_metrics else 0.1
        concept_score = 0.4 if detected_concepts else 0.1
        confidence = min(0.95, metric_score + concept_score)

        explanation_parts = []
        if detected_metrics:
            explanation_parts.append(f"Calculated metrics: {', '.join(m.name for m in detected_metrics)}")
        if detected_concepts:
            explanation_parts.append(f"Grouped by dimensions: {', '.join(c.name for c in detected_concepts)}")
        if filters:
            explanation_parts.append(f"Applied filters: {', '.join(filters)}")
        explanation = "; ".join(explanation_parts) if explanation_parts else "Resolved against semantic ontology."

        return QueryResolutionPlan(
            original_prompt=clean_prompt,
            detected_metrics=[m.name for m in detected_metrics],
            detected_dimensions=[c.name for c in detected_concepts],
            filters=filters,
            resolved_tables=resolved_tables_list,
            join_clauses=join_clauses,
            generated_sql=generated_sql,
            confidence_score=round(confidence, 2),
            explanation=explanation,
        )
