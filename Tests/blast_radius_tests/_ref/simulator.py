"""Reference blast radius simulator.

Traces what depends on a column, table or metric, answers each drift category in its
own shape, and returns nodes carrying a risk level plus the edges between them.
Reads only: nothing here touches a warehouse except through an adapter's read path.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .resolver import NOT_FOUND, ONTOLOGY, WAREHOUSE, resolve_model
from .risk import CERTIFIED, CONSUMER, DEPENDANT, RiskLevel, classify_node_risk, report_risk

ADDITION = "ADDITION"
DELETION = "DELETION"
EXPRESSION_DRIFT = "EXPRESSION_DRIFT"
DESCRIPTION_DRIFT = "DESCRIPTION_DRIFT"

_CATEGORY_CHANGE = {
    ADDITION: "ADD",
    DELETION: "DROP",
    EXPRESSION_DRIFT: "MODIFY_EXPRESSION",
    DESCRIPTION_DRIFT: "MODIFY_DESCRIPTION",
}

DEFAULT_MAX_DEPTH = 5
DEFAULT_MAX_NODES = 300


@dataclass
class ImpactNode:
    name: str
    node_type: str          # column | table | metric | consumer_asset
    item_type: str          # dependant | certified_query | consumer_asset | context
    risk: RiskLevel
    impact_level: str       # DIRECT | TRANSITIVE
    details: str = ""
    source: Optional[str] = None


@dataclass
class ImpactReport:
    target: str
    target_type: str
    change_type: str
    category: Optional[str]
    status: str = "ok"                  # ok | not_found
    model_source: str = ONTOLOGY
    risk: RiskLevel = RiskLevel.INFO
    nodes: List[ImpactNode] = field(default_factory=list)
    edges: List[Dict[str, str]] = field(default_factory=list)
    warehouse_references: List[str] = field(default_factory=list)
    messages: Dict[str, str] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def total(self) -> int:
        return len(self.nodes)


def _ident_in(expr: Optional[str], ident: str) -> bool:
    """True when ident appears as a whole identifier, not inside a longer word."""
    if not expr or not ident:
        return False
    pattern = r"(?<![A-Za-z0-9_%$#])" + re.escape(ident) + r"(?![A-Za-z0-9_%$#])"
    return re.search(pattern, expr, re.IGNORECASE) is not None


@dataclass
class Metric:
    """A metric in the model being traced."""

    name: str
    expression: str = ""
    description: str = ""
    derives_from: List[str] = field(default_factory=list)
    reads_descriptions: bool = False


class BlastRadiusSimulator:
    def __init__(
        self,
        metrics: Optional[List[Metric]] = None,
        certified_queries: Optional[List[Dict[str, Any]]] = None,
        truth_cases: Optional[List[Dict[str, Any]]] = None,
        consumer_resolver=None,
        adapter=None,
        platform: Optional[str] = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
    ):
        self.metrics = list(metrics or [])
        self.certified_queries = list(certified_queries or [])
        self.truth_cases = list(truth_cases or [])
        self.consumer_resolver = consumer_resolver
        self.adapter = adapter
        self.platform = platform
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    # ------------------------------------------------------------------ helpers
    def _by_name(self) -> Dict[str, Metric]:
        return {m.name.casefold(): m for m in self.metrics}

    def _direct_dependants(self, target: str, target_type: str) -> List[Metric]:
        hits = []
        for m in self.metrics:
            if m.name.casefold() == target.casefold():
                continue
            if _ident_in(m.expression, target):
                hits.append(m)
        return hits

    def _consumers(self, view_name: Optional[str]) -> tuple:
        """Returns (assets, reason). An absent resolver is a reason, not silence."""
        if self.consumer_resolver is None:
            return [], "no consumer resolver for this platform"
        try:
            assets = list(self.consumer_resolver(view_name) or [])
        except Exception as exc:  # noqa: BLE001
            return [], f"consumers could not be listed: {exc}"
        if not assets:
            return [], "no downstream assets found"
        return assets, None

    # ------------------------------------------------------------------ entry
    def simulate(
        self,
        target: str,
        target_type: str = "METRIC",
        category: Optional[str] = None,
        change_type: Optional[str] = None,
        expression: Optional[str] = None,
        table: Optional[str] = None,
        view_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ImpactReport:
        target = str(target or "").strip()
        if not target:
            raise ValueError("target is required")

        cat = (category or "").strip().upper() or None
        change = (change_type or _CATEGORY_CHANGE.get(cat or "", "MODIFY_EXPRESSION")).upper()

        # An addition is not in the deployed model by definition.
        if cat == ADDITION or change == "ADD":
            return self._addition(target, target_type, expression, table)

        resolved = resolve_model(
            target_name=target,
            ontology_objects=self.metrics,
            adapter=self.adapter,
            view_name=view_name,
            platform=self.platform,
            target_type=target_type,
        )
        if not resolved.found:
            return ImpactReport(
                target=target, target_type=target_type, change_type=change, category=cat,
                status=NOT_FOUND, model_source=NOT_FOUND, risk=RiskLevel.INFO,
                messages={"summary": resolved.reason or f"'{target}' is not deployed."},
                suggestions=[],
            )

        if cat == DESCRIPTION_DRIFT or change == "MODIFY_DESCRIPTION":
            return self._description(target, target_type, change, cat, resolved.source, view_name)

        return self._structural(target, target_type, change, cat, resolved.source, view_name)

    # ------------------------------------------------------------------ shapes
    def _addition(self, target, target_type, expression, table) -> ImpactReport:
        refs: List[str] = []
        edges: List[Dict[str, str]] = []
        if table:
            refs.append(f"table {table}")
            edges.append({"from": table, "to": target, "relationship": "warehouse_addition"})
        for ref in sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b",
                                         expression or ""))):
            refs.append(f"column {ref}")
            edges.append({"from": ref, "to": target, "relationship": "computed_from"})

        return ImpactReport(
            target=target, target_type=target_type, change_type="ADD", category=ADDITION,
            status="ok", model_source=WAREHOUSE, risk=RiskLevel.INFO,
            nodes=[], edges=edges, warehouse_references=refs,
            messages={
                "summary": f"'{target}' is not deployed; it exists only in the warehouse. "
                           f"Nothing in the deployed model depends on it.",
                "warehouse_references": ", ".join(refs) if refs else "none recorded",
            },
            suggestions=[],
        )

    def _description(self, target, target_type, change, cat, model_source, view_name) -> ImpactReport:
        nodes: List[ImpactNode] = []
        edges: List[Dict[str, str]] = []
        messages: Dict[str, str] = {}

        referencing = [
            m for m in self.metrics
            if m.name.casefold() != target.casefold()
            and (_ident_in(m.description, target) or _ident_in(m.expression, target))
        ]
        for m in referencing:
            lvl = classify_node_risk(change, DEPENDANT, reads_descriptions=m.reads_descriptions)
            nodes.append(ImpactNode(m.name, "metric", DEPENDANT, lvl, "DIRECT",
                                    f"References '{target}'."))
            edges.append({"from": target, "to": m.name, "relationship": "references"})
        if not referencing:
            messages["referencing_metrics"] = f"No other object references '{target}'."

        for q in self.certified_queries:
            if _ident_in(str(q.get("sql") or ""), target) or _ident_in(str(q.get("question") or ""), target):
                lvl = classify_node_risk(change, CERTIFIED)
                name = str(q.get("question") or q.get("name") or "certified query")
                nodes.append(ImpactNode(name, "consumer_asset", CERTIFIED, lvl, "TRANSITIVE",
                                        f"Certified query uses '{target}'."))
                edges.append({"from": target, "to": name, "relationship": "certified_query_uses"})
        if not any(n.item_type == CERTIFIED for n in nodes):
            messages["certified_queries"] = f"No certified query uses '{target}'."

        for c in self.truth_cases:
            if str(c.get("metric_name") or "").casefold() == target.casefold():
                lvl = classify_node_risk(change, CERTIFIED)
                name = str(c.get("question") or c.get("metric_name"))
                nodes.append(ImpactNode(name, "consumer_asset", CERTIFIED, lvl, "TRANSITIVE",
                                        f"Truth case covers '{target}'."))
                edges.append({"from": target, "to": name, "relationship": "truth_case_covers"})

        assets, reason = self._consumers(view_name)
        for a in assets:
            lvl = classify_node_risk(change, CONSUMER)
            nodes.append(ImpactNode(str(a), "consumer_asset", CONSUMER, lvl, "TRANSITIVE",
                                    "Downstream asset."))
            edges.append({"from": target, "to": str(a), "relationship": "consumed_by"})
        if reason:
            messages["consumers"] = reason

        if not nodes:
            messages["summary"] = f"Nothing depends on '{target}'."

        return ImpactReport(
            target=target, target_type=target_type, change_type=change, category=cat,
            status="ok", model_source=model_source,
            risk=report_risk(n.risk for n in nodes),
            nodes=nodes, edges=edges, messages=messages,
        )

    def _structural(self, target, target_type, change, cat, model_source, view_name) -> ImpactReport:
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
            nodes.append(ImpactNode(m.name, "metric", DEPENDANT, lvl, "DIRECT",
                                    f"Computes from '{target}'."))
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
                derives = any(d.casefold() == parent.name.casefold() for d in m.derives_from)
                if derives or _ident_in(m.expression, parent.name):
                    seen.add(key)
                    lvl = classify_node_risk(change, DEPENDANT)
                    nodes.append(ImpactNode(m.name, "metric", DEPENDANT, lvl, "TRANSITIVE",
                                            f"Depends on '{parent.name}'."))
                    edges.append({"from": parent.name, "to": m.name,
                                  "relationship": "derives_from"})
                    queue.append((m, depth + 1))

        impacted = {n.name.casefold() for n in nodes} | {target.casefold()}
        for q in self.certified_queries:
            sql = str(q.get("sql") or "")
            if any(_ident_in(sql, name) for name in impacted):
                lvl = classify_node_risk(change, CERTIFIED)
                name = str(q.get("question") or q.get("name") or "certified query")
                nodes.append(ImpactNode(name, "consumer_asset", CERTIFIED, lvl, "TRANSITIVE",
                                        "Certified query uses an impacted object."))
                edges.append({"from": target, "to": name, "relationship": "certified_query_uses"})

        assets, reason = self._consumers(view_name)
        for a in assets:
            lvl = classify_node_risk(change, CONSUMER)
            nodes.append(ImpactNode(str(a), "consumer_asset", CONSUMER, lvl, "TRANSITIVE",
                                    "Downstream asset."))
            edges.append({"from": target, "to": str(a), "relationship": "consumed_by"})
        if reason:
            messages["consumers"] = reason

        if not nodes:
            messages["summary"] = f"Nothing depends on '{target}'."

        return ImpactReport(
            target=target, target_type=target_type, change_type=change, category=cat,
            status="ok", model_source=model_source,
            risk=report_risk(n.risk for n in nodes),
            nodes=nodes, edges=edges, messages=messages, truncated=truncated,
        )


def build_graph(report: ImpactReport) -> Dict[str, Any]:
    """Nodes and edges for the graph view, with the target included."""
    nodes = [{"id": report.target, "type": report.target_type.lower(),
              "risk": RiskLevel.INFO.value, "is_target": True}]
    for n in report.nodes:
        nodes.append({"id": n.name, "type": n.node_type,
                      "risk": n.risk.value, "is_target": False})
    return {"nodes": nodes, "edges": list(report.edges)}
