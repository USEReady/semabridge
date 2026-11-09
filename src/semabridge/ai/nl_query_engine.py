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
    applied_policies: List[str] = Field(default_factory=list)
    derived_from_metrics: List[str] = Field(default_factory=list)
    resolved_tables: List[str]
    join_clauses: List[str] = Field(default_factory=list)
    generated_sql: str
    confidence_score: float
    explanation: str
    warnings: List[str] = Field(default_factory=list)


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

        # Extract potential time filters from prompt (e.g. 2024, 2023, Q1, Q2)
        filters: List[str] = []
        applied_policies: List[str] = []
        derived_from_metrics: List[str] = []
        warnings: List[str] = []
        year_match = re.search(r"\b(20[12][0-9])\b", clean_prompt)
        quarter_match = re.search(r"\bQ([1-4])\b", clean_prompt, re.IGNORECASE)
        if year_match or quarter_match:
            date_col = self._resolve_date_column(detected_concepts)
            if date_col is None:
                date_col = "date"
                warnings.append(
                    "No date attribute found in the ontology; time filters assume a column named 'date'."
                )
            if year_match:
                filters.append(f"YEAR({date_col}) = {year_match.group(1)}")
            if quarter_match:
                # QUARTER() returns an integer 1-4, so compare against a number, not 'Q1'.
                filters.append(f"QUARTER({date_col}) = {quarter_match.group(1)}")

        # Resolve upstream derived metrics and collect enforceable SQL policies & filter expressions
        metrics_by_key: Dict[str, DomainMetric] = {
            k.casefold(): v for k, v in self.ontology.metrics.items()
        }
        for m in detected_metrics:
            for dep in getattr(m, "derives_from", None) or []:
                dep_m = metrics_by_key.get(dep.casefold())
                dep_name = dep_m.name if dep_m else dep
                if dep_name not in derived_from_metrics:
                    derived_from_metrics.append(dep_name)

        self._collect_policy_filters(
            detected_concepts=detected_concepts,
            detected_metrics=detected_metrics,
            metrics_by_key=metrics_by_key,
            filters=filters,
            applied_policies=applied_policies,
        )

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
            expr = self._expand_metric_expression(m, metrics_by_key)
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
        if derived_from_metrics:
            explanation_parts.append(f"Derived from upstream metrics: {', '.join(derived_from_metrics)}")
        if detected_concepts:
            explanation_parts.append(f"Grouped by dimensions: {', '.join(c.name for c in detected_concepts)}")
        if applied_policies:
            explanation_parts.append(f"Enforced policies: {', '.join(applied_policies)}")
        if filters:
            explanation_parts.append(f"Applied filters: {', '.join(filters)}")
        for w in warnings:
            explanation_parts.append(f"Warning: {w}")
        explanation = "; ".join(explanation_parts) if explanation_parts else "Resolved against semantic ontology."

        return QueryResolutionPlan(
            original_prompt=clean_prompt,
            detected_metrics=[m.name for m in detected_metrics],
            detected_dimensions=[c.name for c in detected_concepts],
            filters=filters,
            applied_policies=applied_policies,
            derived_from_metrics=derived_from_metrics,
            resolved_tables=resolved_tables_list,
            join_clauses=join_clauses,
            generated_sql=generated_sql,
            confidence_score=round(confidence, 2),
            explanation=explanation,
            warnings=warnings,
        )

    @staticmethod
    def _expand_metric_expression(
        metric: DomainMetric,
        metrics_by_key: Dict[str, DomainMetric],
        _seen: Optional[Set[str]] = None,
    ) -> str:
        """Expand composite metric references (``derives_from``) into executable SQL."""
        expr = (metric.sql_expression or "").strip()
        if not expr:
            return f"SUM({metric.name})"
        deps = list(getattr(metric, "derives_from", None) or [])
        if not deps:
            return expr
        seen = set(_seen or set())
        seen.add(metric.name.casefold())

        expanded = expr
        for dep_name in deps:
            dep_key = dep_name.casefold()
            if dep_key in seen:
                continue
            dep_m = metrics_by_key.get(dep_key)
            if not dep_m or not (dep_m.sql_expression or "").strip():
                continue
            dep_sql = NLQueryEngine._expand_metric_expression(dep_m, metrics_by_key, seen)
            # Replace bracketed [dep_name] / [dep_label] first, then bare word boundary matches
            for token in filter(None, [dep_m.name, dep_m.label, dep_name]):
                expanded = re.sub(
                    r"\[" + re.escape(token) + r"\]",
                    f"({dep_sql})",
                    expanded,
                    flags=re.IGNORECASE,
                )
            expanded = re.sub(
                r"\b" + re.escape(dep_m.name) + r"\b",
                f"({dep_sql})",
                expanded,
                flags=re.IGNORECASE,
            )
        return expanded

    def _collect_policy_filters(
        self,
        detected_concepts: List[DomainConcept],
        detected_metrics: List[DomainMetric],
        metrics_by_key: Dict[str, DomainMetric],
        filters: List[str],
        applied_policies: List[str],
    ) -> None:
        """Append enforceable SQL filter expressions from concepts, metrics, and domain policies."""
        seen_filters = {f.strip().casefold() for f in filters if f.strip()}
        seen_policies = {p.casefold() for p in applied_policies}

        def add_filter(sql_pred: Optional[str], policy_label: Optional[str] = None) -> None:
            pred = (sql_pred or "").strip()
            if not pred:
                return
            if pred.casefold() not in seen_filters:
                seen_filters.add(pred.casefold())
                filters.append(pred)
            if policy_label and policy_label.casefold() not in seen_policies:
                seen_policies.add(policy_label.casefold())
                applied_policies.append(policy_label)

        def extract_inline_sql_rules(rules: List[str], owner_name: str) -> None:
            for rule in rules or []:
                m = re.match(r"^(?:SQL|FILTER)\s*:\s*(.+)$", str(rule or "").strip(), flags=re.IGNORECASE)
                if m:
                    add_filter(m.group(1), f"{owner_name} rule")

        active_names: Set[str] = set()
        active_domains: Set[str] = set()

        for c in detected_concepts:
            active_names.add(c.name.casefold())
            if c.domain:
                active_domains.add(c.domain.casefold())
            if getattr(c, "filter_expression", None):
                add_filter(c.filter_expression, f"{c.name} filter")
            extract_inline_sql_rules(c.business_rules, c.name)

        # Include detected metrics plus any upstream derived metrics
        all_involved_metrics: List[DomainMetric] = list(detected_metrics)
        for m in detected_metrics:
            for dep in getattr(m, "derives_from", None) or []:
                dep_m = metrics_by_key.get(dep.casefold())
                if dep_m and dep_m not in all_involved_metrics:
                    all_involved_metrics.append(dep_m)

        for m in all_involved_metrics:
            active_names.add(m.name.casefold())
            if m.domain:
                active_domains.add(m.domain.casefold())
            if getattr(m, "filter_expression", None):
                add_filter(m.filter_expression, f"{m.name} filter")
            extract_inline_sql_rules(m.business_rules, m.name)

        # Check domain-level and federated policies
        all_policies = list(getattr(self.ontology, "policies", {}).values())
        for dom in self.ontology.domains.values():
            for pol in getattr(dom, "policies", None) or []:
                if pol not in all_policies:
                    all_policies.append(pol)

        for pol in all_policies:
            applies = [a.strip().casefold() for a in (getattr(pol, "applies_to", None) or []) if a.strip()]
            pol_dom = (getattr(pol, "domain", None) or "").strip().casefold()
            if not applies or "*" in applies:
                if not pol_dom or pol_dom in active_domains or not active_domains:
                    add_filter(pol.filter_expression, pol.name)
            elif any(a in active_names for a in applies):
                add_filter(pol.filter_expression, pol.name)

    _DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}

    def _resolve_date_column(self, detected_concepts: List[DomainConcept]) -> Optional[str]:
        """Find the date attribute to filter on, from the ontology rather than a hard-coded name.

        Prefers date-typed attributes on the concepts the question resolved to. If none of
        those has one, uses the ontology-wide date attribute only when it is unambiguous
        (exactly one distinct name). Returns None when it cannot be determined.
        """

        def date_attrs(concepts: List[DomainConcept]) -> List[str]:
            found: List[str] = []
            for c in concepts:
                for a in c.attributes:
                    base = (a.data_type or "").split("(")[0].strip().upper()
                    if base in self._DATE_TYPES and a.name not in found:
                        found.append(a.name)
            return found

        def pick(names: List[str]) -> str:
            exact = [n for n in names if n.casefold() == "date"]
            partial = [n for n in names if "date" in n.casefold()]
            return (exact or partial or names)[0]

        local = date_attrs(detected_concepts)
        if local:
            return pick(local)

        global_names = date_attrs(list(self.ontology.concepts.values()))
        if len(global_names) == 1:
            return global_names[0]
        return None
