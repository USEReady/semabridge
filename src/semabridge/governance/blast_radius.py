"""Semantic Blast Radius & Downstream Impact Simulator.

Traces the dependency graph from physical data layer columns/tables/metrics to:
1. Directly impacted and transitively derived metrics
2. Verified AI queries referencing impacted metrics (from verified query benchmarks)
3. Real BI bindings when declared, without fabricating placeholder downstream items
"""

from __future__ import annotations

from collections import deque
import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from semabridge.intermediate.models import OSIModel
from semabridge.ontology.models import DomainConcept, DomainMetric, DomainOntology, FederatedEnterpriseOntology
from semabridge.utils.reserved_prefixes import expand_column_aliases, normalize_separator_ident

logger = logging.getLogger("semabridge.governance.blast_radius")

_VALID_TARGET_TYPES = frozenset({"COLUMN", "TABLE", "METRIC"})
_VALID_CHANGE_TYPES = frozenset({"DROP", "RENAME", "MODIFY_TYPE", "MODIFY_EXPRESSION"})


class ImpactedItem(BaseModel):
    name: str
    item_type: str  # 'METRIC', 'BI_REPORT', 'AI_TOOL', 'ONTOLOGY_CONCEPT'
    impact_level: str  # 'DIRECT', 'TRANSITIVE'
    steward: Optional[str] = None
    details: str
    # Structured dependency source: 'column' / 'table' / 'target' for DIRECT hits, or the
    # expression kind(s) that carry the dependency ('SQL', 'DAX', 'derives_from', 'verified_query').
    source: Optional[str] = None


class BlastRadiusReport(BaseModel):
    target_object: str
    target_type: str  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str  # 'DROP', 'RENAME', 'MODIFY_TYPE', 'MODIFY_EXPRESSION'
    severity: str     # 'BREAKING', 'DEGRADED', 'SAFE', 'NOT_FOUND'
    status: str = "ok"  # 'ok' | 'not_found'
    suggestions: List[str] = Field(default_factory=list)
    total_impacted_items: int
    affected_metrics: List[ImpactedItem] = Field(default_factory=list)
    affected_bi_reports: List[ImpactedItem] = Field(default_factory=list)
    affected_ai_tools: List[ImpactedItem] = Field(default_factory=list)
    remediation_steps: List[str] = Field(default_factory=list)
    dependency_graph_edges: List[Dict[str, str]] = Field(default_factory=list)


def _norm_ident(name: Optional[str]) -> str:
    """Delegate separator normalization to the canonical identifier helper."""
    return normalize_separator_ident(name)


def _ident_in_expr(expr: str, ident: str, table_filter: Optional[str] = None) -> bool:
    """Return True when ``ident`` appears as a standalone identifier (exact suffix/symbol match)."""
    if not expr or not ident:
        return False
    escaped = re.escape(ident)
    # Use identifier boundary including %, $, # so 'amt' never hits 'amt%' or 'amt_net'
    pat = r"(?<![A-Za-z0-9_%$#])" + escaped + r"(?![A-Za-z0-9_%$#])"
    matches = list(re.finditer(pat, expr, re.IGNORECASE))
    if not matches:
        return False
    if not table_filter:
        return True
    tbl_cf = table_filter.casefold()
    for m in matches:
        prefix = expr[: m.start()].rstrip()
        # If qualified as OTHER_TABLE.col, require table_filter match
        qual_m = re.search(r'(?:"([^"]+)"|([A-Za-z0-9_]+))\s*\.\s*"?$', prefix)
        if qual_m:
            q_tbl = (qual_m.group(1) or qual_m.group(2) or "").strip()
            if q_tbl.casefold() == tbl_cf:
                return True
        else:
            return True
    return False


class BlastRadiusSimulator:
    """Analyzes downstream blast radius across ontology metrics, dependencies, and verified AI queries."""

    def __init__(
        self,
        osi_model: Optional[OSIModel] = None,
        ontology: Optional[FederatedEnterpriseOntology] = None,
        verified_queries: Optional[List[Dict[str, Any]]] = None,
        certified_metrics: Optional[Set[str]] = None,
    ):
        self.osi_model = osi_model
        self.ontology = ontology
        self.verified_queries = list(verified_queries or [])
        self.certified_metrics = {str(m).strip().casefold() for m in (certified_metrics or set()) if str(m).strip()}

    @staticmethod
    def _concept_matches_table(concept: Optional[DomainConcept], table_name: str) -> bool:
        if not concept or not table_name:
            return False
        t_cf = table_name.casefold()
        t_norm = _norm_ident(table_name)
        candidates = [
            concept.name,
            getattr(concept, "logical_name", None),
            getattr(concept, "physical_table", None),
            *(getattr(concept, "maps_to", None) or []),
        ]
        return any(
            (str(c or "").strip().casefold() == t_cf or _norm_ident(c) == t_norm)
            for c in candidates
            if c
        )

    def simulate(
        self,
        target_name: Optional[str] = None,
        target_type: str = "METRIC",
        change_type: str = "MODIFY_EXPRESSION",
        target_object: Optional[str] = None,
        object_type: Optional[str] = None,
        table_context: Optional[str] = None,
        project_id: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> BlastRadiusReport:
        """Alias for simulate_impact accepting alternative keyword argument names."""
        name = target_name or target_object or ""
        ttype = object_type or target_type
        if not str(name).strip():
            return BlastRadiusReport(
                target_object="",
                target_type=ttype,
                change_type=change_type,
                severity="SAFE",
                status="ok",
                total_impacted_items=0,
            )
        return self.simulate_impact(
            target_name=name,
            target_type=ttype,
            change_type=change_type,
            table_context=table_context,
            allowed_domains=allowed_domains,
        )

    def simulate_impact(
        self,
        target_name: str,
        target_type: str = "COLUMN",
        change_type: str = "DROP",
        table_context: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
    ) -> BlastRadiusReport:
        """Calculate direct and transitive downstream impact for a column, table, or metric change."""
        target_clean = str(target_name or "").strip()
        if not target_clean:
            raise ValueError("Target name cannot be empty.")

        ttype_upper = str(target_type or "").strip().upper()
        if ttype_upper not in _VALID_TARGET_TYPES:
            raise ValueError(f"Invalid target_type '{target_type}'. Must be one of {sorted(_VALID_TARGET_TYPES)}.")

        ctype_upper = str(change_type or "").strip().upper()
        if ctype_upper not in _VALID_CHANGE_TYPES:
            raise ValueError(f"Invalid change_type '{change_type}'. Must be one of {sorted(_VALID_CHANGE_TYPES)}.")

        tbl_clean = (table_context or "").strip()
        # Support 'Table.Column' dot notation when explicit table_context is omitted
        if ttype_upper == "COLUMN" and "." in target_clean and not tbl_clean:
            parts = target_clean.split(".", 1)
            tbl_clean = parts[0].strip().strip('"')
            target_clean = parts[1].strip().strip('"')

        affected_metrics: List[ImpactedItem] = []
        affected_bi: List[ImpactedItem] = []
        affected_ai: List[ImpactedItem] = []
        edges: List[Dict[str, str]] = []
        seen_metric_keys: Set[str] = set()
        direct_metric_keys: Set[str] = set()

        allowed_cf = {d.casefold() for d in (allowed_domains or []) if d}

        # Collect active (non-stale) ontology metrics and concepts scoped by allowed_domains
        scoped_domains: List[DomainOntology] = []
        if self.ontology and self.ontology.domains:
            for d_obj in self.ontology.domains.values():
                if allowed_cf and d_obj.domain.casefold() not in allowed_cf and (d_obj.id or "").casefold() not in allowed_cf:
                    continue
                scoped_domains.append(d_obj)

        concept_map: Dict[str, DomainConcept] = {}
        attr_names_lower: Set[str] = set()
        all_metrics: List[DomainMetric] = []

        if scoped_domains:
            for dom in scoped_domains:
                for c in dom.concepts:
                    concept_map[c.name.casefold()] = c
                    if getattr(c, "logical_name", None):
                        concept_map[c.logical_name.casefold()] = c
                    for a in c.attributes:
                        attr_names_lower.add(a.name.casefold())
                for m in dom.metrics:
                    if not getattr(m, "stale", False):
                        all_metrics.append(m)
        elif self.ontology:
            for c in self.ontology.concepts.values():
                if allowed_cf and c.domain and c.domain.casefold() not in allowed_cf:
                    continue
                concept_map[c.name.casefold()] = c
                for a in c.attributes:
                    attr_names_lower.add(a.name.casefold())
            for m in self.ontology.metrics.values():
                if allowed_cf and m.domain and m.domain.casefold() not in allowed_cf:
                    continue
                if not getattr(m, "stale", False):
                    all_metrics.append(m)

        # Normalize OSI metrics into the active scan if supplied
        osi_metrics = []
        if self.osi_model:
            osi_metrics = [m for m in self.osi_model.metrics if not getattr(m, "stale", False)]

        def _metric_belongs_to_table(m: DomainMetric, tbl: str) -> bool:
            if not tbl:
                return True
            if m.concept and (m.concept.casefold() == tbl.casefold() or _norm_ident(m.concept) == _norm_ident(tbl)):
                return True
            c_obj = concept_map.get((m.concept or "").casefold())
            if self._concept_matches_table(c_obj, tbl):
                return True
            return False

        target_norm = _norm_ident(target_clean)
        active_concepts: List[DomainConcept] = []
        if scoped_domains:
            for dom_obj in scoped_domains:
                active_concepts.extend(getattr(dom_obj, "concepts", []) or [])
        elif self.ontology:
            for c_obj in self.ontology.concepts.values():
                if allowed_cf and c_obj.domain and c_obj.domain.casefold() not in allowed_cf:
                    continue
                active_concepts.append(c_obj)

        attr_exists_in_schema = False
        table_exists_in_schema = False

        # 1. Identify DIRECTLY impacted metrics
        if ttype_upper == "COLUMN":
            # Expand physical/logical/reserved-prefix column aliases within matching concepts
            target_aliases: Set[str] = {target_clean}
            for c_obj in active_concepts:
                if tbl_clean and not self._concept_matches_table(c_obj, tbl_clean):
                    continue
                table_col_pool: List[str] = []
                for attr in getattr(c_obj, "attributes", []) or []:
                    if getattr(attr, "name", None):
                        table_col_pool.append(attr.name)
                    if getattr(attr, "logical_name", None):
                        table_col_pool.append(attr.logical_name)
                expanded = expand_column_aliases(target_clean, table_col_pool)
                if len(expanded) > 1 or any(_norm_ident(c) == target_norm for c in table_col_pool):
                    attr_exists_in_schema = True
                for attr in getattr(c_obj, "attributes", []) or []:
                    a_name = getattr(attr, "name", None)
                    a_log = getattr(attr, "logical_name", None)
                    a_norms = {_norm_ident(a_name), _norm_ident(a_log)} - {""}
                    exp_norms = {_norm_ident(x) for x in expanded}
                    if a_norms & exp_norms:
                        attr_exists_in_schema = True
                        if a_name:
                            target_aliases.add(a_name)
                        if a_log:
                            target_aliases.add(a_log)
                target_aliases.update(expanded)

            for m in osi_metrics:
                if tbl_clean and getattr(m, "dataset", None) and _norm_ident(m.dataset) != _norm_ident(tbl_clean):
                    continue
                expr = m.expression or ""
                if any(_ident_in_expr(expr, alias, tbl_clean or None) for alias in target_aliases):
                    key = m.unique_name.casefold()
                    if key not in seen_metric_keys:
                        seen_metric_keys.add(key)
                        direct_metric_keys.add(key)
                        affected_metrics.append(
                            ImpactedItem(
                                name=m.unique_name,
                                item_type="METRIC",
                                impact_level="DIRECT",
                                steward=getattr(m, "business_owner", None),
                                details=f"Calculation relies on column '{target_clean}': {expr}",
                                source="SQL",
                            )
                        )
                        edges.append({"from": target_clean, "to": m.unique_name, "relationship": "computed_from"})

            for m in all_metrics:
                expr = m.sql_expression or ""
                dax_expr = getattr(m, "dax_expression", None) or ""
                if tbl_clean and m.concept and not _metric_belongs_to_table(m, tbl_clean):
                    # When table_context is specified, only match another table's metric if it explicitly qualifies tbl_clean.col
                    if not any(
                        re.search(
                            r'(?:"?' + re.escape(tbl_clean) + r'"?\s*\.\s*"?' + re.escape(alias) + r'"?)',
                            expr,
                            re.IGNORECASE,
                        )
                        for alias in target_aliases
                    ):
                        continue
                sql_hit = any(_ident_in_expr(expr, alias, tbl_clean or None) for alias in target_aliases)
                dax_hit = any(_ident_in_expr(dax_expr, alias, tbl_clean or None) for alias in target_aliases)
                if sql_hit or dax_hit:
                    key = m.name.casefold()
                    if key not in seen_metric_keys:
                        seen_metric_keys.add(key)
                        direct_metric_keys.add(key)
                        affected_metrics.append(
                            ImpactedItem(
                                name=m.name,
                                item_type="METRIC",
                                impact_level="DIRECT",
                                steward=m.data_steward,
                                details=f"Formula relies on column '{target_clean}': {expr}",
                                source=" + ".join(t for t, hit in (("DAX", dax_hit), ("SQL", sql_hit)) if hit),
                            )
                        )
                        edges.append({"from": target_clean, "to": m.name, "relationship": "computed_from"})

        elif ttype_upper == "TABLE":
            table_exists_in_schema = any(self._concept_matches_table(c, target_clean) for c in active_concepts)
            for m in osi_metrics:
                if getattr(m, "dataset", None) and _norm_ident(m.dataset) == target_norm:
                    table_exists_in_schema = True
                    key = m.unique_name.casefold()
                    if key not in seen_metric_keys:
                        seen_metric_keys.add(key)
                        direct_metric_keys.add(key)
                        affected_metrics.append(
                            ImpactedItem(
                                name=m.unique_name,
                                item_type="METRIC",
                                impact_level="DIRECT",
                                steward=getattr(m, "business_owner", None),
                                details=f"Metric belongs to table '{target_clean}'.",
                                source="table",
                            )
                        )
                        edges.append({"from": target_clean, "to": m.unique_name, "relationship": "computed_from"})

            for m in all_metrics:
                if _metric_belongs_to_table(m, target_clean) or re.search(
                    r'(?:"?' + re.escape(target_clean) + r'"?\s*\.)', m.sql_expression or "", re.IGNORECASE
                ):
                    table_exists_in_schema = True
                    key = m.name.casefold()
                    if key not in seen_metric_keys:
                        seen_metric_keys.add(key)
                        direct_metric_keys.add(key)
                        affected_metrics.append(
                            ImpactedItem(
                                name=m.name,
                                item_type="METRIC",
                                impact_level="DIRECT",
                                steward=m.data_steward,
                                details=f"Metric depends on table '{target_clean}'.",
                                source="table" if _metric_belongs_to_table(m, target_clean) else "SQL",
                            )
                        )
                        edges.append({"from": target_clean, "to": m.name, "relationship": "computed_from"})

        elif ttype_upper == "METRIC":
            for m in all_metrics:
                m_candidates = [m.name, m.label, getattr(m, "logical_name", None)]
                if any(
                    cand and (cand.casefold() == target_clean.casefold() or _norm_ident(cand) == target_norm)
                    for cand in m_candidates
                ):
                    key = m.name.casefold()
                    if key not in seen_metric_keys:
                        seen_metric_keys.add(key)
                        direct_metric_keys.add(key)
                        affected_metrics.append(
                            ImpactedItem(
                                name=m.name,
                                item_type="METRIC",
                                impact_level="DIRECT",
                                steward=m.data_steward,
                                details=f"Target metric '{m.name}' modified/dropped directly.",
                                source="target",
                            )
                        )
                        edges.append({"from": target_clean, "to": m.name, "relationship": "target"})

            for m in osi_metrics:
                m_candidates = [m.unique_name, getattr(m, "label", None)]
                if any(
                    cand and (cand.casefold() == target_clean.casefold() or _norm_ident(cand) == target_norm)
                    for cand in m_candidates
                ):
                    key = m.unique_name.casefold()
                    if key not in seen_metric_keys:
                        seen_metric_keys.add(key)
                        direct_metric_keys.add(key)
                        affected_metrics.append(
                            ImpactedItem(
                                name=m.unique_name,
                                item_type="METRIC",
                                impact_level="DIRECT",
                                steward=getattr(m, "business_owner", None),
                                details=f"Target metric '{m.unique_name}' modified/dropped directly.",
                                source="target",
                            )
                        )
                        edges.append({"from": target_clean, "to": m.unique_name, "relationship": "target"})

        # 2. Build metric dependency graph (derives_from + unqualified [Metric] refs in SQL/DAX; bare idents if non-column)
        metric_by_key: Dict[str, DomainMetric] = {}
        downstream_adj: Dict[str, List[DomainMetric]] = {}
        upstream_deps: Dict[str, Set[str]] = {}
        dep_sources: Dict[Tuple[str, str], Set[str]] = {}
        for m in all_metrics:
            metric_by_key[m.name.casefold()] = m
            if getattr(m, "logical_name", None):
                metric_by_key[m.logical_name.casefold()] = m
            if m.label:
                metric_by_key[m.label.casefold()] = m

        # Unqualified [Metric] references skip qualified table columns like Table[Col] or 'Table'[Col]
        unqual_bracket_re = re.compile(r"(?<![A-Za-z0-9_'\.\]])\[([^\[\]]+)\]")
        for m in all_metrics:
            mk = m.name.casefold()
            deps_with_src: Dict[str, Set[str]] = {}

            def _add_dep(raw_key: str, src_tag: str) -> None:
                deps_with_src.setdefault(raw_key, set()).add(src_tag)

            for d in getattr(m, "derives_from", None) or []:
                _add_dep(d.casefold(), "derives_from")
            sql_expr = m.sql_expression or ""
            dax_expr = getattr(m, "dax_expression", None) or ""
            for br in unqual_bracket_re.findall(sql_expr):
                _add_dep(br.strip().casefold(), "SQL")
            for br in unqual_bracket_re.findall(dax_expr):
                _add_dep(br.strip().casefold(), "DAX")
            # Bare identifiers only when matching a known metric AND NOT a physical attribute name
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql_expr):
                tk = tok.casefold()
                if tk in metric_by_key and tk not in attr_names_lower:
                    _add_dep(tk, "SQL")
            resolved_up: Set[str] = set()
            for dep_k, src_tags in deps_with_src.items():
                dep_m = metric_by_key.get(dep_k)
                if dep_m and dep_m.name.casefold() != mk:
                    dep_key = dep_m.name.casefold()
                    resolved_up.add(dep_key)
                    dep_sources.setdefault((dep_key, mk), set()).update(src_tags)
                    children = downstream_adj.setdefault(dep_key, [])
                    if all(c.name.casefold() != mk for c in children):
                        children.append(m)
            if resolved_up:
                upstream_deps[mk] = resolved_up

        # Reclassify derived metrics whose SQL was inlined from impacted base metrics cleanly as TRANSITIVE
        if ttype_upper == "COLUMN":
            leaf_direct = {k for k in direct_metric_keys if not (upstream_deps.get(k, set()) & direct_metric_keys)}
            demote_keys = {k for k in direct_metric_keys if k not in leaf_direct and (upstream_deps.get(k, set()) & direct_metric_keys)}
            if leaf_direct and demote_keys:
                direct_metric_keys -= demote_keys
                seen_metric_keys -= demote_keys
                affected_metrics = [item for item in affected_metrics if item.name.casefold() not in demote_keys]
                edges = [
                    e for e in edges
                    if not (e.get("relationship") == "computed_from" and str(e.get("to", "")).casefold() in demote_keys)
                ]

        # 3. Traverse TRANSITIVE downstream metrics with cycle-safe visited tracking (B8)
        queue: deque[Tuple[str, str]] = deque(
            (item.name.casefold(), item.name) for item in affected_metrics
        )
        while queue:
            cur_key, cur_name = queue.popleft()
            for child in downstream_adj.get(cur_key, []):
                ck = child.name.casefold()
                if ck in seen_metric_keys:
                    continue
                seen_metric_keys.add(ck)
                src_tags = dep_sources.get((cur_key, ck), {"derives_from"})
                src_label = " + ".join(sorted(src_tags))
                affected_metrics.append(
                    ImpactedItem(
                        name=child.name,
                        item_type="METRIC",
                        impact_level="TRANSITIVE",
                        steward=child.data_steward,
                        details=f"Transitively depends on '{cur_name}' [Source: {src_label}]: {child.sql_expression or ''}",
                        source=src_label,
                    )
                )
                edges.append({"from": cur_name, "to": child.name, "relationship": "derives_from", "source": src_label})
                queue.append((ck, child.name))

        # 4. Downstream AI Tools: match against real saved verified queries whose SQL references impacted metrics
        impacted_metric_objs = [metric_by_key[k] for k in seen_metric_keys if k in metric_by_key]
        impacted_search_tokens: List[Tuple[str, str]] = []
        for m_obj in impacted_metric_objs:
            impacted_search_tokens.append((m_obj.name, m_obj.name))
            if getattr(m_obj, "logical_name", None):
                impacted_search_tokens.append((m_obj.name, m_obj.logical_name))
            if getattr(m_obj, "label", None):
                impacted_search_tokens.append((m_obj.name, m_obj.label))

        seen_vq: Set[str] = set()
        for vq in self.verified_queries:
            vq_sql = str(vq.get("sql") or "")
            vq_q = str(vq.get("question") or vq.get("name") or "")
            for m_name, token in impacted_search_tokens:
                if _ident_in_expr(vq_sql, token):
                    vq_key = vq_q.casefold()
                    if vq_key and vq_key not in seen_vq:
                        seen_vq.add(vq_key)
                        affected_ai.append(
                            ImpactedItem(
                                name=vq_q,
                                item_type="AI_TOOL",
                                impact_level="TRANSITIVE",
                                details=f"Verified AI query uses impacted metric '{m_name}'.",
                                source="verified_query",
                            )
                        )
                        edges.append({"from": m_name, "to": vq_q, "relationship": "verified_query_uses"})
                    break

        total_impact = len(affected_metrics) + len(affected_bi) + len(affected_ai)

        if total_impact > 0 and ctype_upper in ("DROP", "RENAME"):
            severity = "BREAKING"
        elif total_impact > 0:
            severity = "DEGRADED"
        else:
            severity = "SAFE"

        # Identify affected join relationships in remediation guidance (without altering edge-per-item count invariant)
        affected_rels: List[str] = []
        all_rels = (
            [r for d in scoped_domains for r in getattr(d, "relationships", []) or []]
            if scoped_domains
            else list(getattr(self.ontology, "relationships", []) or []) if self.ontology else []
        )
        for rel in all_rels:
            rel_label = rel.name or f"{rel.from_concept} -> {rel.to_concept}"
            if ttype_upper == "TABLE" and (
                self._concept_matches_table(concept_map.get(rel.from_concept.casefold()), target_clean)
                or self._concept_matches_table(concept_map.get(rel.to_concept.casefold()), target_clean)
                or rel.from_concept.casefold() == target_clean.casefold()
                or rel.to_concept.casefold() == target_clean.casefold()
            ):
                affected_rels.append(rel_label)
            elif ttype_upper == "COLUMN" and rel.join_expression and _ident_in_expr(
                rel.join_expression, target_clean, tbl_clean or None
            ):
                affected_rels.append(rel_label)

        impacted_certified = [
            m.name
            for m in affected_metrics
            if m.name.casefold() in self.certified_metrics
            or (
                m.name.casefold() in metric_by_key
                and (
                    (getattr(metric_by_key[m.name.casefold()], "label", None) or "").casefold() in self.certified_metrics
                    or (getattr(metric_by_key[m.name.casefold()], "logical_name", None) or "").casefold() in self.certified_metrics
                )
            )
        ]

        # Check whether target exists in scoped domains/schema before declaring SAFE
        target_exists = True
        if total_impact == 0 and not affected_rels:
            if ttype_upper == "COLUMN":
                target_exists = attr_exists_in_schema
            elif ttype_upper == "TABLE":
                target_exists = table_exists_in_schema
            elif ttype_upper == "METRIC":
                target_exists = False

        suggestions: List[str] = []
        status_str = "ok"
        if not target_exists:
            status_str = "not_found"
            severity = "NOT_FOUND"
            if ttype_upper == "METRIC":
                pool = sorted({m.label or m.name for m in all_metrics} | {m.name for m in all_metrics} | {m.unique_name for m in osi_metrics})
            elif ttype_upper == "TABLE":
                pool = sorted({c.name for c in active_concepts} | {m.dataset for m in osi_metrics if getattr(m, "dataset", None)})
            else:
                col_pool: Set[str] = set()
                for c in active_concepts:
                    if tbl_clean and not self._concept_matches_table(c, tbl_clean):
                        continue
                    for attr in getattr(c, "attributes", []) or []:
                        if getattr(attr, "name", None):
                            col_pool.add(attr.name)
                        if getattr(attr, "logical_name", None):
                            col_pool.add(attr.logical_name)
                pool = sorted(col_pool)
            norm_map: Dict[str, str] = {}
            for cand in pool:
                norm_map.setdefault(_norm_ident(cand), cand)
            close_norm = difflib.get_close_matches(target_norm, list(norm_map.keys()), n=3, cutoff=0.45)
            suggestions = [norm_map[k] for k in close_norm]
            if not suggestions:
                suggestions = difflib.get_close_matches(target_clean, pool, n=3, cutoff=0.45)

        remediations: List[str] = []
        if status_str == "not_found":
            sug_note = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            remediations.append(
                f"NOT FOUND: no {ttype_upper.lower()} matching '{target_clean}' in the selected domains.{sug_note}"
            )
        else:
            if impacted_certified and total_impact > 0:
                remediations.append(
                    f"Re-certification needed for certified metric(s): {', '.join(impacted_certified)}."
                )
            if severity == "BREAKING":
                remediations.append(
                    f"Create a backward-compatible view alias for '{target_clean}' before dropping."
                )
                remediations.append(
                    f"Update the following {len(affected_metrics)} metrics to use the replacement definition: "
                    f"{', '.join(m.name for m in affected_metrics[:3])}."
                )
                if affected_rels:
                    remediations.append(
                        f"Update affected relationship join(s): {', '.join(list(dict.fromkeys(affected_rels))[:5])}."
                    )
                remediations.append(
                    "Notify data stewards of the affected metrics prior to running deployment."
                )
            elif severity == "DEGRADED":
                remediations.append("Run Golden Query Verifier to test numerical tolerances on updated metrics.")
            else:
                remediations.append(f"No metric dependencies found for '{target_clean}'. Safe to proceed.")

        return BlastRadiusReport(
            target_object=target_clean,
            target_type=ttype_upper,
            change_type=ctype_upper,
            severity=severity,
            status=status_str,
            suggestions=suggestions,
            total_impacted_items=total_impact,
            affected_metrics=affected_metrics,
            affected_bi_reports=affected_bi,
            affected_ai_tools=affected_ai,
            remediation_steps=remediations,
            dependency_graph_edges=edges,
        )
