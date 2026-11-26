"""Semantic Blast Radius & Downstream Impact Simulator.

Traces the dependency graph from physical data layer columns/tables/metrics to:
1. Directly impacted and transitively derived metrics
2. Verified AI queries referencing impacted metrics (from verified query benchmarks)
3. Real BI bindings when declared, without fabricating placeholder downstream items
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field, PrivateAttr

from semabridge.governance.model_resolver import resolve_model, NOT_FOUND, ONTOLOGY, WAREHOUSE

from semabridge.intermediate.models import OSIModel
from semabridge.ontology.models import DomainConcept, DomainMetric, DomainOntology, FederatedEnterpriseOntology
from semabridge.utils.reserved_prefixes import expand_column_aliases, normalize_separator_ident

logger = logging.getLogger("semabridge.governance.blast_radius")

_VALID_TARGET_TYPES = frozenset({"COLUMN", "TABLE", "METRIC", "DIMENSION"})
_VALID_CHANGE_TYPES = frozenset({
    "DROP",
    "RENAME",
    "MODIFY_TYPE",
    "MODIFY_EXPRESSION",
    "MODIFY_DESCRIPTION",
    "ADD",
    "ADD_METRIC",
    "ADDITION",
    "CREATE",
})

_CHANGE_TYPE_CANONICAL = {
    "ADD": "ADD",
    "ADD_METRIC": "ADD",
    "ADDITION": "ADD",
    "CREATE": "ADD",
    "DROP": "DROP",
    "DELETION": "DROP",
    "MISSING": "DROP",
    "MODIFY_EXPRESSION": "MODIFY_EXPRESSION",
    "EXPRESSION_DRIFT": "MODIFY_EXPRESSION",
    "MODIFY_DESCRIPTION": "MODIFY_DESCRIPTION",
    "DESCRIPTION_DRIFT": "MODIFY_DESCRIPTION",
    "RENAME": "RENAME",
    "MODIFY_TYPE": "MODIFY_TYPE",
}

def _get_mock_bi_reports() -> List[Dict[str, Any]]:
    try:
        from semabridge.demo.blast_radius_fixtures import MOCK_BI_REPORTS
        return MOCK_BI_REPORTS
    except ImportError:
        return []

from semabridge.governance.risk_classifier import (
    RiskLevel,
    classify_node_risk,
    report_risk,
    DEPENDANT,
    CERTIFIED,
    CONSUMER,
    CONTEXT,
)


@dataclass
class Metric:
    """A metric in the model being traced."""
    name: str
    expression: str = ""
    description: str = ""
    derives_from: List[str] = field(default_factory=list)
    reads_descriptions: bool = False


@dataclass
class ImpactNode:
    name: str
    node_type: str          # column | table | metric | consumer_asset
    item_type: str          # dependant | certified_query | consumer_asset | context
    risk: RiskLevel
    impact_level: str       # DIRECT | TRANSITIVE
    details: str = ""
    source: Optional[str] = None


def _get_mock_ai_tools() -> List[Dict[str, Any]]:
    try:
        from semabridge.demo.blast_radius_fixtures import MOCK_AI_TOOLS
        return MOCK_AI_TOOLS
    except ImportError:
        return []


class ImpactedItem(BaseModel):
    name: str
    item_type: str  # 'METRIC', 'BI_REPORT', 'AI_TOOL', 'ONTOLOGY_CONCEPT'
    impact_level: str  # 'DIRECT', 'TRANSITIVE'
    steward: Optional[str] = None
    details: str
    # Structured dependency source: 'column' / 'table' / 'target' for DIRECT hits, or the
    # expression kind(s) that carry the dependency ('SQL', 'DAX', 'derives_from', 'verified_query').
    source: Optional[str] = None
    risk: str = "INFO"  # 'BREAKING', 'ADVISORY', 'INFO'


class BlastRadiusReport(BaseModel):
    target_object: str
    target_type: str  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str  # 'DROP', 'RENAME', 'MODIFY_TYPE', 'MODIFY_EXPRESSION'
    severity: str     # 'BREAKING', 'DEGRADED', 'SAFE', 'NOT_FOUND'
    risk: str = "INFO"  # 'BREAKING', 'ADVISORY', 'INFO'
    status: str = "ok"  # 'ok' | 'not_found'
    suggestions: List[str] = Field(default_factory=list)
    not_found_reason: Optional[str] = None
    total_impacted_items: int
    affected_metrics: List[ImpactedItem] = Field(default_factory=list)
    affected_bi_reports: List[ImpactedItem] = Field(default_factory=list)
    affected_ai_tools: List[ImpactedItem] = Field(default_factory=list)
    remediation_steps: List[str] = Field(default_factory=list)
    dependency_graph_edges: List[Dict[str, str]] = Field(default_factory=list)
    referencing_metrics: List[Dict[str, Any]] = Field(default_factory=list)
    stated_dimension_constraints: List[Dict[str, Any]] = Field(default_factory=list)
    certified_queries: List[Dict[str, Any]] = Field(default_factory=list)
    bi_truth_cases: List[Dict[str, Any]] = Field(default_factory=list)
    source_model_difference: Optional[Dict[str, Any]] = None
    empty_messages: Dict[str, str] = Field(default_factory=dict)

    _nodes_cache: List[ImpactNode] = PrivateAttr(default_factory=list)
    _model_source: str = PrivateAttr(default="ontology")
    _warehouse_references: List[str] = PrivateAttr(default_factory=list)
    _category: Optional[str] = PrivateAttr(default=None)
    _truncated: bool = PrivateAttr(default=False)

    @property
    def target(self) -> str:
        return self.target_object

    @property
    def category(self) -> Optional[str]:
        if self._category:
            return self._category
        mapping = {
            "ADD": "ADDITION",
            "DROP": "DELETION",
            "MODIFY_EXPRESSION": "EXPRESSION_DRIFT",
            "MODIFY_DESCRIPTION": "DESCRIPTION_DRIFT",
        }
        return mapping.get(self.change_type)

    @property
    def model_source(self) -> str:
        return self._model_source

    @property
    def warehouse_references(self) -> List[str]:
        return self._warehouse_references

    @property
    def truncated(self) -> bool:
        return self._truncated

    @property
    def messages(self) -> Dict[str, str]:
        return self.empty_messages

    @property
    def total(self) -> int:
        return len(self.nodes)

    @property
    def edges(self) -> List[Dict[str, str]]:
        return self.dependency_graph_edges

    @property
    def nodes(self) -> List[ImpactNode]:
        if self._nodes_cache:
            return self._nodes_cache
        nodes_list: List[ImpactNode] = []
        for m in self.affected_metrics:
            risk_enum = RiskLevel(m.risk) if m.risk in RiskLevel.__members__ else RiskLevel.INFO
            nodes_list.append(ImpactNode(
                name=m.name,
                node_type="metric",
                item_type=DEPENDANT,
                risk=risk_enum,
                impact_level=m.impact_level,
                details=m.details,
                source=m.source,
            ))
        for b in self.affected_bi_reports:
            risk_enum = RiskLevel(b.risk) if b.risk in RiskLevel.__members__ else RiskLevel.INFO
            nodes_list.append(ImpactNode(
                name=b.name,
                node_type="consumer_asset",
                item_type=CONSUMER,
                risk=risk_enum,
                impact_level=b.impact_level,
                details=b.details,
                source=b.source,
            ))
        for a in self.affected_ai_tools:
            risk_enum = RiskLevel(a.risk) if a.risk in RiskLevel.__members__ else RiskLevel.INFO
            nodes_list.append(ImpactNode(
                name=a.name,
                node_type="consumer_asset",
                item_type=CONSUMER,
                risk=risk_enum,
                impact_level=a.impact_level,
                details=a.details,
                source=a.source,
            ))
        return nodes_list


def build_graph(report: Any) -> Dict[str, Any]:
    """Nodes and edges for the graph view, with the target included."""
    target_obj = getattr(report, "target_object", None) or getattr(report, "target", "")
    target_type = getattr(report, "target_type", "METRIC")
    nodes = [{
        "id": target_obj,
        "type": str(target_type).lower(),
        "risk": RiskLevel.INFO.value,
        "is_target": True,
    }]
    report_nodes = getattr(report, "nodes", None)
    if report_nodes:
        for n in report_nodes:
            n_name = getattr(n, "name", str(n))
            n_type = getattr(n, "node_type", None) or getattr(n, "item_type", "metric")
            n_risk = getattr(n, "risk", RiskLevel.INFO)
            risk_val = n_risk.value if hasattr(n_risk, "value") else str(n_risk)
            nodes.append({
                "id": n_name,
                "type": str(n_type).lower(),
                "risk": risk_val,
                "is_target": False,
            })
    else:
        for m in getattr(report, "affected_metrics", []):
            risk_val = getattr(m, "risk", "INFO")
            nodes.append({
                "id": m.name,
                "type": "metric",
                "risk": risk_val.value if hasattr(risk_val, "value") else str(risk_val),
                "is_target": False,
            })
        for b in getattr(report, "affected_bi_reports", []):
            risk_val = getattr(b, "risk", "INFO")
            nodes.append({
                "id": b.name,
                "type": "consumer_asset",
                "risk": risk_val.value if hasattr(risk_val, "value") else str(risk_val),
                "is_target": False,
            })
        for a in getattr(report, "affected_ai_tools", []):
            risk_val = getattr(a, "risk", "INFO")
            nodes.append({
                "id": a.name,
                "type": "consumer_asset",
                "risk": risk_val.value if hasattr(risk_val, "value") else str(risk_val),
                "is_target": False,
            })

    edges = getattr(report, "edges", None) or getattr(report, "dependency_graph_edges", [])
    return {"nodes": nodes, "edges": list(edges)}


def _norm_ident(name: Optional[str]) -> str:
    """Delegate separator normalization to the canonical identifier helper."""
    return normalize_separator_ident(name)


# Constraint extraction patterns and stop-words are declared as module configuration defaults for standard governance comments.
DEFAULT_DIMENSION_CONSTRAINT_PATTERNS = [
    r"(?:must be queried with|requires dimension[s]?|require dimension[s]?|mandatory dimension[s]?|partitioned by|query with(?: named)? dimensions?)\s*:?\s*([A-Za-z0-9_.,\s]+?)(?:\.\s|\.$|;|\n|$)",
    r"(?:only valid with|grouped by)\s*:?\s*([A-Za-z0-9_.]+(?:\s*(?:,|and)\s*[A-Za-z0-9_.]+)*)",
]

DEFAULT_DIMENSION_CONSTRAINT_STOP_WORDS = frozenset({
    "named", "dimensions", "dimension", "and", "or", "the", "with", "only", "by"
})


def extract_stated_dimension_constraints(text: Optional[str]) -> List[Dict[str, Any]]:
    """Extract mandatory query dimensions specified in a metric/column documentation comment.

    NOTE: This is a best-effort heuristic parser matching standard English phrasing.
    It will miss non-standard, multilingual, or unformatted descriptions.
    """
    if not text:
        return []
    results: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for pat in DEFAULT_DIMENSION_CONSTRAINT_PATTERNS:
        for match in re.finditer(pat, text, re.IGNORECASE):
            raw_clause = match.group(0).strip()
            dim_part = match.group(1).strip()
            tokens = re.findall(r"\b[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?\b", dim_part)
            for tok in tokens:
                if tok.lower() in DEFAULT_DIMENSION_CONSTRAINT_STOP_WORDS:
                    continue
                k = tok.casefold()
                if k not in seen:
                    seen.add(k)
                    results.append({
                        "dimension": tok,
                        "constraint_clause": raw_clause,
                        "details": f"Stated in comment as required query dimension: '{raw_clause}'",
                    })
    return results


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
        mock_mode: bool = False,
        metrics: Optional[List[Metric]] = None,
        certified_queries: Optional[List[Dict[str, Any]]] = None,
        truth_cases: Optional[List[Dict[str, Any]]] = None,
        consumer_resolver=None,
        adapter=None,
        platform: Optional[str] = None,
        max_depth: int = 5,
        max_nodes: int = 300,
        **kwargs: Any,
    ):
        self.osi_model = osi_model
        self.ontology = ontology
        self.verified_queries = list(verified_queries or [])
        self.certified_metrics = {str(m).strip().casefold() for m in (certified_metrics or set()) if str(m).strip()}
        self.mock_mode = mock_mode
        self.metrics = list(metrics or [])
        self.certified_queries = list(certified_queries or [])
        self.truth_cases = list(truth_cases or [])
        self.consumer_resolver = consumer_resolver
        self.adapter = adapter
        self.platform = platform
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def _direct_dependants(self, target: str, target_type: str) -> List[Metric]:
        hits = []
        for m in self.metrics:
            if m.name.casefold() == target.casefold():
                continue
            if _ident_in_expr(m.expression, target):
                hits.append(m)
        return hits

    def _consumers(self, view_name: Optional[str]) -> Tuple[List[Any], Optional[str]]:
        """Returns (assets, reason). An absent resolver is a reason, not silence."""
        if self.consumer_resolver is None and (self.adapter is None or not hasattr(self.adapter, "list_consumers")):
            return [], "no consumer resolver for this platform"
        if self.consumer_resolver is not None:
            try:
                assets = list(self.consumer_resolver(view_name) or [])
            except Exception as exc:  # noqa: BLE001
                return [], f"consumers could not be listed: {exc}"
            if not assets:
                return [], "no downstream assets found"
            return assets, None
        if self.adapter is not None and hasattr(self.adapter, "list_consumers"):
            try:
                assets = list(self.adapter.list_consumers(view_name) or [])
            except Exception as exc:  # noqa: BLE001
                return [], f"consumers could not be listed: {exc}"
            if not assets:
                return [], "no downstream assets found"
            return assets, None
        return [], "no consumer resolver for this platform"

    def _simulate_metrics_model(
        self,
        target: str,
        target_type: str = "METRIC",
        category: Optional[str] = None,
        change_type: Optional[str] = None,
        expression: Optional[str] = None,
        table: Optional[str] = None,
        view_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> BlastRadiusReport:
        cat = (category or "").strip().upper() or None
        change = (change_type or _CHANGE_TYPE_CANONICAL.get(cat or "", "MODIFY_EXPRESSION")).upper()

        if cat == "ADDITION" or change == "ADD":
            return self._addition_metrics(target, target_type, expression, table)

        resolved = resolve_model(
            target_name=target,
            ontology_objects=self.metrics,
            adapter=self.adapter,
            view_name=view_name,
            platform=self.platform,
            target_type=target_type,
        )
        if not resolved.found:
            rep = BlastRadiusReport(
                target_object=target,
                target_type=target_type,
                change_type=change,
                severity="NOT_FOUND",
                risk=RiskLevel.INFO.value,
                status="not_found",
                not_found_reason=resolved.reason or f"'{target}' is not deployed.",
                total_impacted_items=0,
                empty_messages={"summary": resolved.reason or f"'{target}' is not deployed."},
                suggestions=[],
            )
            rep._model_source = "not_found"
            rep._category = cat
            return rep

        if cat == "DESCRIPTION_DRIFT" or change == "MODIFY_DESCRIPTION":
            return self._description_metrics(target, target_type, change, cat, resolved.source, view_name)

        return self._structural_metrics(target, target_type, change, cat, resolved.source, view_name)

    def _addition_metrics(self, target: str, target_type: str, expression: Optional[str], table: Optional[str]) -> BlastRadiusReport:
        refs: List[str] = []
        edges: List[Dict[str, str]] = []
        if table:
            refs.append(f"table {table}")
            edges.append({"from": table, "to": target, "relationship": "warehouse_addition"})
        for ref in sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", expression or ""))):
            refs.append(f"column {ref}")
            edges.append({"from": ref, "to": target, "relationship": "computed_from"})

        messages = {
            "summary": f"'{target}' is not deployed; it exists only in the warehouse. Nothing in the deployed model depends on it.",
            "warehouse_references": ", ".join(refs) if refs else "none recorded",
            "referencing_metrics": f"Object '{target}' was never deployed; referencing metrics do not apply to additions.",
            "stated_dimension_constraints": f"Object '{target}' was never deployed; dimension constraints do not apply to additions.",
            "certified_queries": f"Object '{target}' was never deployed; certified queries do not apply to additions.",
            "bi_truth_cases": f"Object '{target}' was never deployed; truth cases do not apply to additions.",
        }
        rep = BlastRadiusReport(
            target_object=target,
            target_type=target_type,
            change_type="ADD",
            severity="SAFE",
            risk=RiskLevel.INFO.value,
            status="ok",
            total_impacted_items=0,
            dependency_graph_edges=edges,
            empty_messages=messages,
            suggestions=[],
        )
        rep._nodes_cache = []
        rep._model_source = "warehouse"
        rep._warehouse_references = refs
        rep._category = "ADDITION"
        return rep

    def _description_metrics(self, target: str, target_type: str, change: str, cat: Optional[str], model_source: str, view_name: Optional[str]) -> BlastRadiusReport:
        nodes: List[ImpactNode] = []
        edges: List[Dict[str, str]] = []
        messages: Dict[str, str] = {}

        referencing = [
            m for m in self.metrics
            if m.name.casefold() != target.casefold()
            and (_ident_in_expr(getattr(m, "description", ""), target) or _ident_in_expr(getattr(m, "expression", ""), target))
        ]
        for m in referencing:
            lvl = classify_node_risk(change, DEPENDANT, reads_descriptions=getattr(m, "reads_descriptions", False))
            nodes.append(ImpactNode(m.name, "metric", DEPENDANT, lvl, "DIRECT", f"References '{target}'."))
            edges.append({"from": target, "to": m.name, "relationship": "references"})
        if not referencing:
            messages["referencing_metrics"] = f"No other object references '{target}'."

        for q in self.certified_queries:
            if _ident_in_expr(str(q.get("sql") or ""), target) or _ident_in_expr(str(q.get("question") or ""), target):
                lvl = classify_node_risk(change, CERTIFIED)
                name = str(q.get("question") or q.get("name") or "certified query")
                nodes.append(ImpactNode(name, "consumer_asset", CERTIFIED, lvl, "TRANSITIVE", f"Certified query uses '{target}'."))
                edges.append({"from": target, "to": name, "relationship": "certified_query_uses"})
        if not any(n.item_type == CERTIFIED for n in nodes):
            messages["certified_queries"] = f"No certified query uses '{target}'."

        for c in self.truth_cases:
            if str(c.get("metric_name") or "").casefold() == target.casefold():
                lvl = classify_node_risk(change, CERTIFIED)
                name = str(c.get("question") or c.get("metric_name"))
                nodes.append(ImpactNode(name, "consumer_asset", CERTIFIED, lvl, "TRANSITIVE", f"Truth case covers '{target}'."))
                edges.append({"from": target, "to": name, "relationship": "truth_case_covers"})

        assets, reason = self._consumers(view_name)
        for a in assets:
            lvl = classify_node_risk(change, CONSUMER)
            a_name = a.get("name") if isinstance(a, dict) and "name" in a else str(a)
            a_details = a.get("details", "Downstream asset.") if isinstance(a, dict) else "Downstream asset."
            nodes.append(ImpactNode(a_name, "consumer_asset", CONSUMER, lvl, "TRANSITIVE", a_details))
            edges.append({"from": target, "to": a_name, "relationship": "consumed_by"})
        if reason:
            messages["consumers"] = reason

        if not nodes:
            messages["summary"] = f"Nothing depends on '{target}'."

        risk = report_risk(n.risk for n in nodes)
        rep = BlastRadiusReport(
            target_object=target,
            target_type=target_type,
            change_type=change,
            severity="DEGRADED" if risk == RiskLevel.ADVISORY else ("BREAKING" if risk == RiskLevel.BREAKING else "SAFE"),
            risk=risk.value,
            status="ok",
            total_impacted_items=len(nodes),
            dependency_graph_edges=edges,
            empty_messages=messages,
            suggestions=[],
        )
        rep._nodes_cache = nodes
        rep._model_source = model_source
        rep._category = cat
        return rep

    def _structural_metrics(self, target: str, target_type: str, change: str, cat: Optional[str], model_source: str, view_name: Optional[str]) -> BlastRadiusReport:
        nodes: List[ImpactNode] = []
        edges: List[Dict[str, str]] = []
        messages: Dict[str, str] = {}
        seen: Set[str] = set()
        truncated = False

        direct = self._direct_dependants(target, target_type)
        queue = deque((m, 1) for m in direct)
        for m in direct:
            seen.add(m.name.casefold())
            lvl = classify_node_risk(change, DEPENDANT)
            nodes.append(ImpactNode(m.name, "metric", DEPENDANT, lvl, "DIRECT", f"Computes from '{target}'."))
            edges.append({"from": target, "to": m.name, "relationship": "computed_from"})

        while queue:
            if len(nodes) >= self.max_nodes:
                truncated = True
                break
            parent, depth = queue.popleft()
            if depth >= self.max_depth:
                truncated = True
                continue
            for m in self.metrics:
                key = m.name.casefold()
                if key in seen or key == parent.name.casefold():
                    continue
                derives = any(d.casefold() == parent.name.casefold() for d in getattr(m, "derives_from", []))
                if derives or _ident_in_expr(getattr(m, "expression", ""), parent.name):
                    seen.add(key)
                    lvl = classify_node_risk(change, DEPENDANT)
                    nodes.append(ImpactNode(m.name, "metric", DEPENDANT, lvl, "TRANSITIVE", f"Depends on '{parent.name}'."))
                    edges.append({"from": parent.name, "to": m.name, "relationship": "derives_from"})
                    queue.append((m, depth + 1))

        impacted = {n.name.casefold() for n in nodes} | {target.casefold()}
        for q in self.certified_queries:
            sql = str(q.get("sql") or "")
            if any(_ident_in_expr(sql, name) for name in impacted):
                lvl = classify_node_risk(change, CERTIFIED)
                name = str(q.get("question") or q.get("name") or "certified query")
                nodes.append(ImpactNode(name, "consumer_asset", CERTIFIED, lvl, "TRANSITIVE", "Certified query uses an impacted object."))
                edges.append({"from": target, "to": name, "relationship": "certified_query_uses"})

        assets, reason = self._consumers(view_name)
        for a in assets:
            lvl = classify_node_risk(change, CONSUMER)
            a_name = a.get("name") if isinstance(a, dict) and "name" in a else str(a)
            a_details = a.get("details", "Downstream asset.") if isinstance(a, dict) else "Downstream asset."
            nodes.append(ImpactNode(a_name, "consumer_asset", CONSUMER, lvl, "TRANSITIVE", a_details))
            edges.append({"from": target, "to": a_name, "relationship": "consumed_by"})
        if reason:
            messages["consumers"] = reason

        if not nodes:
            messages["summary"] = f"Nothing depends on '{target}'."

        risk = report_risk(n.risk for n in nodes)
        rep = BlastRadiusReport(
            target_object=target,
            target_type=target_type,
            change_type=change,
            severity="DEGRADED" if risk == RiskLevel.ADVISORY else ("BREAKING" if risk == RiskLevel.BREAKING else "SAFE"),
            risk=risk.value,
            status="ok",
            total_impacted_items=len(nodes),
            dependency_graph_edges=edges,
            empty_messages=messages,
            suggestions=[],
        )
        rep._nodes_cache = nodes
        rep._model_source = model_source
        rep._category = cat
        rep._truncated = truncated
        return rep

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

    @staticmethod
    def _get_bi_truth_cases(project_id: Optional[str]) -> List[Dict[str, Any]]:
        try:
            from semabridge.ai.bi_truth_service import _load_suite
            data = _load_suite()
            cases = data.get("cases") or []
            if project_id:
                from semabridge.governance.drift.project_scope import matches_project
                cases = [c for c in cases if matches_project(project_id, c.get("project_id"))]
            return cases
        except Exception:
            return []

    def simulate(
        self,
        target_name: Optional[str] = None,
        target_type: str = "METRIC",
        change_type: Optional[str] = None,
        target_object: Optional[str] = None,
        object_type: Optional[str] = None,
        table_context: Optional[str] = None,
        project_id: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
        mock_mode: Optional[bool] = None,
        category: Optional[str] = None,
        table: Optional[str] = None,
        expression: Optional[str] = None,
        view_name: Optional[str] = None,
        *args: Any,
        **kwargs: Any,
    ) -> BlastRadiusReport:
        """Alias for simulate_impact accepting alternative keyword argument names."""
        name = target_name or target_object or (args[0] if args else "")
        target_clean = str(name or "").strip()
        if not target_clean:
            raise ValueError("target is required")

        ttype = (object_type or target_type or "METRIC").upper()
        cat = (category or "").strip().upper() or None
        tbl = table or table_context

        if self.metrics:
            return self._simulate_metrics_model(
                target=target_clean,
                target_type=ttype,
                category=cat,
                change_type=change_type,
                expression=expression,
                table=tbl,
                view_name=view_name,
                project_id=project_id,
            )

        return self.simulate_impact(
            target_name=target_clean,
            target_type=ttype,
            change_type=change_type or "MODIFY_EXPRESSION",
            table_context=tbl,
            allowed_domains=allowed_domains,
            expression=expression,
            project_id=project_id,
            view=view_name,
            mock_mode=mock_mode,
            category=cat,
        )

    def simulate_impact(
        self,
        target_name: str,
        target_type: str = "COLUMN",
        change_type: str = "DROP",
        table_context: Optional[str] = None,
        allowed_domains: Optional[List[str]] = None,
        expression: Optional[str] = None,
        project_id: Optional[str] = None,
        view: Optional[str] = None,
        mock_mode: Optional[bool] = None,
        category: Optional[str] = None,
    ) -> BlastRadiusReport:
        """Calculate direct and transitive downstream impact for a column, table, or metric change."""
        target_clean = str(target_name or "").strip()
        if not target_clean:
            raise ValueError("Target name cannot be empty.")

        ttype_raw = str(target_type or "").strip().upper()
        if ttype_raw not in _VALID_TARGET_TYPES:
            raise ValueError(f"Invalid target_type '{target_type}'. Must be one of {sorted(_VALID_TARGET_TYPES)}.")

        if ttype_raw in ("DIMENSION", "ATTRIBUTE", "COL"):
            ttype_upper = "COLUMN"
        elif ttype_raw in ("MEASURE",):
            ttype_upper = "METRIC"
        elif ttype_raw in ("VIEW",):
            ttype_upper = "TABLE"
        else:
            ttype_upper = ttype_raw

        ctype_raw = str(change_type or "").strip().upper()
        ctype_upper = _CHANGE_TYPE_CANONICAL.get(ctype_raw, ctype_raw)
        if ctype_upper not in _VALID_CHANGE_TYPES:
            raise ValueError(f"Invalid change_type '{change_type}'. Must be one of {sorted(_VALID_CHANGE_TYPES)}.")

        tbl_clean = (table_context or "").strip()
        # Support 'Table.Column' dot notation when explicit table_context is omitted
        if ttype_upper == "COLUMN" and "." in target_clean and not tbl_clean:
            parts = target_clean.split(".", 1)
            tbl_clean = parts[0].strip().strip('"')
            target_clean = parts[1].strip().strip('"')

        # Check for corresponding drift event strictly within caller's project scope
        matching_event = None
        if project_id and str(project_id).strip():
            try:
                from semabridge.governance.drift_service import list_drift_events
                from semabridge.governance.drift.project_scope import verify_drift_project_access
                evs = list_drift_events(project_id=str(project_id).strip())
                matching_event = next((
                    e for e in evs
                    if verify_drift_project_access(str(project_id).strip(), event_or_object=e)
                    and str(e.get("metric_name") or e.get("object_name") or e.get("name") or "").strip().casefold() == target_clean.casefold()
                ), None)
            except Exception:
                matching_event = None

        expr_clean = str(expression or "").strip()
        cat_upper = str(category or "").strip().upper()
        if matching_event:
            ev_cat = str(matching_event.get("category") or matching_event.get("drift_type") or "").upper()
            if not cat_upper:
                cat_upper = ev_cat
            if not tbl_clean:
                tbl_clean = str(matching_event.get("table") or "").strip()
            if not expr_clean:
                expr_clean = str(matching_event.get("live_value") or matching_event.get("live_expression") or "").strip()
            if any(k in ev_cat for k in ("ADDITION", "UNMANAGED", "ADD_METRIC", "ADD_COLUMN")) and ctype_upper == "MODIFY_EXPRESSION":
                ctype_upper = "ADD"

        is_addition = (ctype_upper == "ADD") or any(
            k in cat_upper for k in ("ADDITION", "UNMANAGED", "ADD_METRIC", "ADD_COLUMN")
        ) or (
            matching_event and any(k in str(matching_event.get("category") or matching_event.get("drift_type") or "").upper() for k in ("ADDITION", "UNMANAGED", "ADD_METRIC", "ADD_COLUMN"))
        )

        # For ADDITION: do not look in the ontology. Report that the object is not deployed,
        # and what it references in the warehouse.
        if is_addition:
            ctype_upper = "ADD"
            wh_refs: List[str] = []
            edges: List[Dict[str, str]] = []
            if tbl_clean:
                wh_refs.append(f"table '{tbl_clean}'")
                edges.append({"from": tbl_clean, "to": target_clean, "relationship": "warehouse_addition", "source": "warehouse"})
            if expr_clean:
                found_qualified = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b', expr_clean))
                for ref in sorted(found_qualified):
                    wh_refs.append(f"column '{ref}'")
                    edges.append({"from": ref, "to": target_clean, "relationship": "computed_from", "source": "warehouse"})
                if not found_qualified:
                    _sql_kw = {
                        "select", "from", "where", "group", "by", "order", "sum", "count", "avg", "min", "max",
                        "case", "when", "then", "else", "end", "and", "or", "not", "distinct", "as", "null",
                        "is", "like", "in", "cast", "coalesce", "true", "false", "between", "having", "join",
                        "left", "right", "inner", "outer", "on"
                    }
                    tokens = set(
                        t for t in re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', expr_clean)
                        if t.lower() not in _sql_kw and t.lower() != target_clean.lower()
                    )
                    for tok in sorted(tokens):
                        wh_refs.append(f"column '{tok}'")
                        edges.append({"from": tok, "to": target_clean, "relationship": "computed_from", "source": "warehouse"})

            remediations = [
                f"Object '{target_clean}' is not deployed: exists only as an unmanaged addition in the warehouse" + (f" ({tbl_clean})." if tbl_clean else "."),
                f"Warehouse references: {', '.join(wh_refs) if wh_refs else 'Direct warehouse calculation'}" + (f" (expression: {expr_clean})" if expr_clean else "."),
                "Zero downstream breaking changes: no deployed semantic models, BI dashboards, or AI queries depend on this unmanaged warehouse object.",
                "To manage this calculation in SemaBridge, add it to your project semantic model and redeploy. To revert, drop the object in the warehouse.",
            ]
            empty_messages = {
                "summary": f"Object '{target_clean}' is not deployed. No downstream metrics, BI dashboards, or AI queries depend on it.",
                "warehouse_references": f"Warehouse references: {', '.join(wh_refs) if wh_refs else 'None specified'}",
                "referencing_metrics": f"No referencing metrics: '{target_clean}' is an unmanaged warehouse addition not deployed in SemaBridge.",
                "stated_dimension_constraints": f"No stated constraints: '{target_clean}' has no deployed semantic metadata.",
                "certified_queries": f"No certified queries cover '{target_clean}' because it has not been deployed to SemaBridge.",
                "bi_truth_cases": f"No BI-truth cases cover undeployed object '{target_clean}'.",
            }
            return BlastRadiusReport(
                target_object=target_clean,
                target_type=ttype_upper,
                change_type="ADD",
                severity="SAFE",
                risk="INFO",
                status="ok",
                suggestions=[],
                not_found_reason=f"Object '{target_clean}' is not deployed — exists only in warehouse view",
                total_impacted_items=0,
                affected_metrics=[],
                affected_bi_reports=[],
                affected_ai_tools=[],
                remediation_steps=remediations,
                dependency_graph_edges=edges,
                referencing_metrics=[],
                stated_dimension_constraints=[],
                certified_queries=[],
                bi_truth_cases=[],
                source_model_difference=None,
                empty_messages=empty_messages,
            )

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
            # Metric comment references: if m.description references a known metric
            desc_text = getattr(m, "description", None) or ""
            if desc_text:
                for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", desc_text):
                    tk = tok.casefold()
                    if tk in metric_by_key and tk != mk and tk not in attr_names_lower:
                        _add_dep(tk, "comment")
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

        # 5. Downstream BI Reports & Mock Downstream Items
        active_mock = self.mock_mode if mock_mode is None else mock_mode
        if active_mock:
            target_upper = target_clean.upper()
            affected_metric_names = {m.name.upper() for m in affected_metrics}
            seen_bi_names: Set[str] = {b.name.casefold() for b in affected_bi}

            mock_reports = _get_mock_bi_reports()
            for report_spec in mock_reports:
                r_name = report_spec["name"]
                if r_name.casefold() in seen_bi_names:
                    continue

                spec_metrics = {m.upper() for m in report_spec["metrics"]}
                spec_tables = {t.upper() for t in report_spec["tables"]}
                spec_columns = {c.upper() for c in report_spec["columns"]}

                is_direct = (
                    (ttype_upper == "METRIC" and target_upper in spec_metrics)
                    or (ttype_upper == "TABLE" and target_upper in spec_tables)
                    or (ttype_upper == "COLUMN" and target_upper in spec_columns)
                )
                shared_metrics = affected_metric_names & spec_metrics

                if is_direct:
                    seen_bi_names.add(r_name.casefold())
                    affected_bi.append(
                        ImpactedItem(
                            name=r_name,
                            item_type="BI_REPORT",
                            impact_level="DIRECT",
                            steward=report_spec["steward"],
                            details=f"{report_spec['platform']} report directly binds to {ttype_upper.lower()} '{target_clean}': {report_spec['details']}",
                            source=report_spec["platform"],
                        )
                    )
                    edges.append({"from": target_clean, "to": r_name, "relationship": "visualized_in", "source": report_spec["platform"]})
                elif shared_metrics:
                    hit_metric = sorted(shared_metrics)[0]
                    seen_bi_names.add(r_name.casefold())
                    affected_bi.append(
                        ImpactedItem(
                            name=r_name,
                            item_type="BI_REPORT",
                            impact_level="TRANSITIVE",
                            steward=report_spec["steward"],
                            details=f"{report_spec['platform']} report uses derived metric '{hit_metric}': {report_spec['details']}",
                            source=report_spec["platform"],
                        )
                    )
                    edges.append({"from": hit_metric, "to": r_name, "relationship": "visualized_in", "source": report_spec["platform"]})

            # Mock AI Tools when verified queries are not yet registered
            if not affected_ai:
                seen_ai_names: Set[str] = set()
                for ai_spec in _get_mock_ai_tools():
                    spec_metrics = {m.upper() for m in ai_spec["metrics"]}
                    spec_tables = {t.upper() for t in ai_spec["tables"]}
                    spec_columns = {c.upper() for c in ai_spec["columns"]}

                    is_direct = (
                        (ttype_upper == "METRIC" and target_upper in spec_metrics)
                        or (ttype_upper == "TABLE" and target_upper in spec_tables)
                        or (ttype_upper == "COLUMN" and target_upper in spec_columns)
                    )
                    shared_metrics = affected_metric_names & spec_metrics

                    if is_direct or shared_metrics:
                        a_name = ai_spec["name"]
                        if a_name.casefold() not in seen_ai_names:
                            seen_ai_names.add(a_name.casefold())
                            from_node = target_clean if is_direct else sorted(shared_metrics)[0]
                            affected_ai.append(
                                ImpactedItem(
                                    name=a_name,
                                    item_type="AI_TOOL",
                                    impact_level="DIRECT" if is_direct else "TRANSITIVE",
                                    steward=ai_spec["steward"],
                                    details=ai_spec["details"],
                                    source="verified_query",
                                )
                            )
                            edges.append({"from": from_node, "to": a_name, "relationship": "verified_query_uses", "source": "verified_query"})
        else:
            # Live mode: discover downstream consumer assets via platform adapter
            target_view = view or table_context
            if self.adapter and hasattr(self.adapter, "list_consumers") and target_view:
                seen_bi_names: Set[str] = {b.name.casefold() for b in affected_bi}
                try:
                    live_assets = self.adapter.list_consumers(target_view)
                    for la in live_assets:
                        la_name = la.get("name") if isinstance(la, dict) else str(la)
                        if la_name.casefold() not in seen_bi_names:
                            seen_bi_names.add(la_name.casefold())
                            affected_bi.append(
                                ImpactedItem(
                                    name=la_name,
                                    item_type="BI_REPORT",
                                    impact_level="TRANSITIVE",
                                    steward=la.get("steward", "Platform Administrator") if isinstance(la, dict) else "Platform Administrator",
                                    details=la.get("details", f"Downstream consumer asset on {getattr(self.adapter, 'platform_name', 'warehouse')}.") if isinstance(la, dict) else "Downstream consumer asset.",
                                    source=la.get("platform", getattr(self.adapter, "platform_name", "WAREHOUSE")) if isinstance(la, dict) else "WAREHOUSE",
                                )
                            )
                            edges.append({"from": target_clean, "to": la_name, "relationship": "consumed_by", "source": "adapter"})
                except Exception as c_exc:
                    logger.debug("Failed to list live consumers from adapter: %s", c_exc)

        is_desc_change = (ctype_upper == "MODIFY_DESCRIPTION")

        # Build specialized description blast radius items
        referencing_metrics: List[Dict[str, Any]] = []
        for m in all_metrics:
            if m.name.casefold() == target_clean.casefold():
                continue
            desc_val = getattr(m, "description", None) or ""
            if desc_val and re.search(r'\b' + re.escape(target_clean) + r'\b', desc_val, re.IGNORECASE):
                referencing_metrics.append({
                    "name": m.name,
                    "kind": "METRIC",
                    "reference_type": "comment",
                    "details": f"Metric '{m.name}' description references '{target_clean}': '{desc_val.strip()}'",
                })
            sql_val = getattr(m, "sql_expression", None) or ""
            if sql_val and _ident_in_expr(sql_val, target_clean):
                referencing_metrics.append({
                    "name": m.name,
                    "kind": "METRIC",
                    "reference_type": "expression",
                    "details": f"Metric '{m.name}' expression computes from '{target_clean}' ({sql_val.strip()})",
                })

        candidate_descs = []
        if matching_event:
            if matching_event.get("baseline_description"):
                candidate_descs.append(matching_event["baseline_description"])
            if matching_event.get("live_description"):
                candidate_descs.append(matching_event["live_description"])
        t_metric_obj = next((m for m in all_metrics if m.name.casefold() == target_clean.casefold()), None)
        if t_metric_obj and t_metric_obj.description:
            candidate_descs.append(t_metric_obj.description)
        if expression:
            candidate_descs.append(expression)

        stated_dim_constraints: List[Dict[str, Any]] = []
        seen_dim_names: Set[str] = set()
        for c_text in candidate_descs:
            for item in extract_stated_dimension_constraints(c_text):
                dk = item["dimension"].casefold()
                if dk not in seen_dim_names:
                    seen_dim_names.add(dk)
                    stated_dim_constraints.append(item)

        cert_queries: List[Dict[str, Any]] = []
        for vq in self.verified_queries:
            v_sql = str(vq.get("sql") or "")
            v_q = str(vq.get("question") or vq.get("name") or "")
            if _ident_in_expr(v_sql, target_clean) or target_clean.casefold() in v_q.casefold():
                cert_queries.append({
                    "query_id": vq.get("id"),
                    "name": v_q or "Verified Query",
                    "details": f"Certified query references metric '{target_clean}' in SQL.",
                })

        bt_cases: List[Dict[str, Any]] = []
        for c in self._get_bi_truth_cases(project_id):
            c_m = str(c.get("metric_name") or "")
            c_q = str(c.get("question") or "")
            c_sql = str(c.get("expected_sql") or "")
            if c_m.casefold() == target_clean.casefold() or _ident_in_expr(c_sql, target_clean) or target_clean.casefold() in c_q.casefold():
                bt_cases.append({
                    "case_id": c.get("id"),
                    "name": c_q or c_m,
                    "details": f"BI-truth validation case covers metric '{target_clean}'.",
                })

        src_desc = t_metric_obj.description if t_metric_obj else None
        wh_desc = None
        if matching_event:
            wh_desc = matching_event.get("live_description")
        elif is_desc_change and expression:
            wh_desc = expression

        src_differs = False
        diff_reason = "No source model description found to compare."
        if src_desc is not None or wh_desc is not None:
            src_norm = (src_desc or "").strip().lower()
            wh_norm = (wh_desc or "").strip().lower()
            src_differs = (src_norm != wh_norm)
            diff_reason = (
                "Source model description differs from live warehouse description."
                if src_differs
                else "Source model description matches warehouse description."
            )
        source_model_difference = {
            "source_description": src_desc or "(no description in source model)",
            "warehouse_description": wh_desc or "(no description in warehouse)",
            "differs": src_differs,
            "details": diff_reason,
        }

        empty_messages: Dict[str, str] = {}
        if not referencing_metrics:
            empty_messages["referencing_metrics"] = f"No other metrics reference '{target_clean}' in their expressions or comments."
        if not stated_dim_constraints:
            has_nonempty_comment = bool(candidate_descs and any(str(c or "").strip() for c in candidate_descs))
            if has_nonempty_comment:
                empty_messages["stated_dimension_constraints"] = (
                    f"A description comment is present for '{target_clean}', but no standard dimension constraint phrasing was recognized (best-effort parser)."
                )
            else:
                empty_messages["stated_dimension_constraints"] = (
                    f"No description comment or required query dimensions stated for '{target_clean}'."
                )
        if not cert_queries:
            empty_messages["certified_queries"] = f"No certified or saved queries use metric '{target_clean}'."
        if not bt_cases:
            empty_messages["bi_truth_cases"] = f"No BI-truth cases cover metric '{target_clean}'."

        if is_desc_change:
            # Filter target metric itself out of affected_metrics (do not echo finding back)
            affected_metrics = [m for m in affected_metrics if m.name.casefold() != target_clean.casefold()]
            has_real_dependants = bool(
                affected_metrics or referencing_metrics or stated_dim_constraints
                or cert_queries or bt_cases
            )
            if not has_real_dependants:
                affected_bi = []
                affected_ai = []
                total_impact = 0
                severity = "SAFE"
                empty_messages["summary"] = f"Object '{target_clean}' has no dependants, dimension constraints, certified queries, or BI-truth cases."
            else:
                total_impact = (
                    len(affected_metrics) + len(referencing_metrics)
                    + len(stated_dim_constraints) + len(cert_queries)
                    + len(bt_cases) + len(affected_bi) + len(affected_ai)
                )
                severity = "DEGRADED"
        else:
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
        if is_addition:
            target_exists = True
        elif total_impact == 0 and not affected_rels:
            if ttype_upper == "COLUMN":
                target_exists = attr_exists_in_schema
            elif ttype_upper == "TABLE":
                target_exists = table_exists_in_schema
            elif ttype_upper == "METRIC":
                metric_exists_in_schema = any(
                    (m.name.casefold() == target_clean.casefold() or _norm_ident(m.name) == target_norm)
                    for m in all_metrics
                ) or any(
                    (m.unique_name.casefold() == target_clean.casefold() or _norm_ident(m.unique_name) == target_norm)
                    for m in osi_metrics
                )
                target_exists = metric_exists_in_schema

        suggestions: List[str] = []
        status_str = "ok"

        # Compute suggestions if not found OR if is_addition (to highlight potential duplicate/overlapping metrics)
        if not target_exists or is_addition:
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

        is_finding_path = bool(matching_event or category or is_addition)
        not_found_reason: Optional[str] = None
        if not target_exists:
            status_str = "not_found"
            severity = "NOT_FOUND"
            if is_finding_path:
                not_found_reason = f"Object '{target_clean}' is not deployed in SemaBridge baseline."
                suggestions = []  # Drop did-you-mean on this path
            else:
                not_found_reason = f"Object '{target_clean}' is not deployed in SemaBridge baseline."
        elif is_addition:
            status_str = "ok"
            severity = "SAFE"
            suggestions = []
            not_found_reason = f"Object '{target_clean}' is an unmanaged warehouse addition not yet deployed."
            if tbl_clean:
                edges.append({"from": tbl_clean, "to": target_clean, "relationship": "warehouse_addition", "source": "warehouse"})
            if expr_clean:
                found_refs = set(re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b', expr_clean))
                for ref in sorted(found_refs):
                    edges.append({"from": ref, "to": target_clean, "relationship": "computed_from", "source": "warehouse"})

        remediations: List[str] = []
        if status_str == "not_found":
            if is_finding_path:
                remediations.append(
                    f"NOT FOUND: '{target_clean}' is not deployed in SemaBridge baseline."
                )
                remediations.append(
                    f"Object '{target_clean}' exists only in the warehouse; it was not found in the deployed semantic model."
                )
                remediations.append(
                    f"To manage this calculation in SemaBridge, add '{target_clean}' to your project semantic model and redeploy."
                )
            else:
                sug_note = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
                remediations.append(
                    f"NOT FOUND: no {ttype_upper.lower()} matching '{target_clean}' in the selected domains.{sug_note}"
                )
                remediations.append(
                    f"If '{target_clean}' was added manually in the warehouse or is a proposed new metric, switch change type to 'ADD' to evaluate addition impact."
                )
        elif is_addition:
            remediations.append(
                f"Unmanaged addition: '{target_clean}' was added directly in the warehouse" + (f" on table '{tbl_clean}'." if tbl_clean else ".")
            )
            remediations.append(
                "Zero downstream breaking changes: no existing BI dashboards or verified AI queries depend on this new calculation yet."
            )
            if suggestions:
                remediations.append(
                    f"Potential semantic overlap: existing metric(s) '{', '.join(suggestions)}' may calculate similar measures. Verify whether this duplicates existing semantic definitions."
                )
            remediations.append(
                f"Approval Impact: Approving or accepting this change will register '{target_clean}' into the semantic model and make it available for downstream BI dashboards and Cortex AI agents."
            )
            remediations.append(
                "Remediation: If this addition was temporary testing, run 'Restore & Sync' in Drift Monitoring to drop or revert unmanaged objects from the warehouse."
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
                if affected_bi:
                    remediations.append(
                        f"Notify data stewards of {len(affected_bi)} downstream BI dashboard(s) (Power BI / Tableau) to review visual calculations."
                    )
                if affected_ai:
                    remediations.append(
                        f"Re-verify {len(affected_ai)} automated AI / Cortex query benchmark(s)."
                    )
            elif severity == "DEGRADED":
                remediations.append("Run Golden Query Verifier to test numerical tolerances on updated metrics.")
                if affected_bi:
                    remediations.append(
                        f"Review {len(affected_bi)} downstream BI dashboard(s) (Power BI / Tableau) for visual calculation impact."
                    )
            else:
                remediations.append(f"No metric dependencies found for '{target_clean}'. Safe to proceed.")

        if total_impact == 0 and not empty_messages.get("summary"):
            empty_messages["summary"] = f"Nothing depends on '{target_clean}'. No downstream metrics, BI dashboards, or AI queries are affected."

        # Assign risk per node and compute report risk level
        for m_item in affected_metrics:
            m_item.risk = classify_node_risk(ctype_upper, DEPENDANT).value
        for bi_item in affected_bi:
            bi_item.risk = classify_node_risk(ctype_upper, CONSUMER).value
        for ai_item in affected_ai:
            reads_desc = (ctype_upper == "MODIFY_DESCRIPTION")
            ai_item.risk = classify_node_risk(ctype_upper, CONSUMER, reads_descriptions=reads_desc).value

        node_risk_map = {item.name.casefold(): item.risk for item in (affected_metrics + affected_bi + affected_ai)}
        node_risk_map[target_clean.casefold()] = "INFO"
        for edge in edges:
            to_name = (edge.get("to") or "").casefold()
            edge["risk"] = node_risk_map.get(to_name, "INFO")

        all_node_risks = [item.risk for item in (affected_metrics + affected_bi + affected_ai)]
        computed_risk = report_risk(all_node_risks).value

        return BlastRadiusReport(
            target_object=target_clean,
            target_type=ttype_upper,
            change_type=ctype_upper,
            severity=severity,
            risk=computed_risk,
            status=status_str,
            suggestions=suggestions,
            not_found_reason=not_found_reason,
            total_impacted_items=total_impact,
            affected_metrics=affected_metrics,
            affected_bi_reports=affected_bi,
            affected_ai_tools=affected_ai,
            remediation_steps=remediations,
            dependency_graph_edges=edges,
            referencing_metrics=referencing_metrics,
            stated_dimension_constraints=stated_dim_constraints,
            certified_queries=cert_queries,
            bi_truth_cases=bt_cases,
            source_model_difference=source_model_difference,
            empty_messages=empty_messages,
        )
