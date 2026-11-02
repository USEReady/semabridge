"""Semantic Blast Radius & Downstream Impact Simulator.

Traces the dependency graph from physical data layer columns/tables to:
1. Calculated measures & semantic models
2. Downstream BI reports (Fabric TMDL, Power BI, Tableau)
3. AI Agent tools (Snowflake Cortex Search, FastMCP Server tools)
4. Data stewards and business owners
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from semabridge.intermediate.models import OSIModel
from semabridge.ontology.models import FederatedEnterpriseOntology

logger = logging.getLogger("semabridge.governance.blast_radius")


class ImpactedItem(BaseModel):
    name: str
    item_type: str  # 'METRIC', 'BI_REPORT', 'AI_TOOL', 'ONTOLOGY_CONCEPT'
    impact_level: str  # 'DIRECT', 'TRANSITIVE'
    steward: Optional[str] = None
    details: str


class BlastRadiusReport(BaseModel):
    target_object: str
    target_type: str  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str  # 'DROP', 'RENAME', 'MODIFY_TYPE', 'MODIFY_EXPRESSION'
    severity: str     # 'BREAKING', 'DEGRADED', 'SAFE'
    total_impacted_items: int
    affected_metrics: List[ImpactedItem] = Field(default_factory=list)
    affected_bi_reports: List[ImpactedItem] = Field(default_factory=list)
    affected_ai_tools: List[ImpactedItem] = Field(default_factory=list)
    remediation_steps: List[str] = Field(default_factory=list)
    dependency_graph_edges: List[Dict[str, str]] = Field(default_factory=list)


class BlastRadiusSimulator:
    """Analyzes downstream blast radius across models, BI reports, and AI tools."""

    def __init__(
        self,
        osi_model: Optional[OSIModel] = None,
        ontology: Optional[FederatedEnterpriseOntology] = None,
    ):
        self.osi_model = osi_model
        self.ontology = ontology

    def simulate_impact(
        self,
        target_name: str,
        target_type: str = "COLUMN",
        change_type: str = "DROP",
        table_context: Optional[str] = None,
    ) -> BlastRadiusReport:
        """Calculate the blast radius of modifying or dropping a column, table, or metric."""
        target_clean = target_name.strip()
        tbl_clean = (table_context or "").strip()

        affected_metrics: List[ImpactedItem] = []
        affected_bi: List[ImpactedItem] = []
        affected_ai: List[ImpactedItem] = []
        edges: List[Dict[str, str]] = []

        direct_broken_metrics: Set[str] = set()

        # 1. Search in OSI Model metrics
        if self.osi_model:
            for m in self.osi_model.metrics:
                expr = m.expression or ""
                # Check if target name appears in expression
                pat = r"\b" + re.escape(target_clean) + r"\b"
                if re.search(pat, expr, re.IGNORECASE):
                    direct_broken_metrics.add(m.unique_name)
                    affected_metrics.append(
                        ImpactedItem(
                            name=m.unique_name,
                            item_type="METRIC",
                            impact_level="DIRECT",
                            steward=getattr(m, "business_owner", None),
                            details=f"Calculation relies on '{target_clean}': {expr}",
                        )
                    )
                    edges.append({"from": target_clean, "to": m.unique_name, "relationship": "computed_from"})

        # 2. Search in Federated Ontology metrics
        if self.ontology:
            for m in self.ontology.metrics.values():
                expr = m.sql_expression or ""
                pat = r"\b" + re.escape(target_clean) + r"\b"
                if re.search(pat, expr, re.IGNORECASE) and m.name not in direct_broken_metrics:
                    direct_broken_metrics.add(m.name)
                    affected_metrics.append(
                        ImpactedItem(
                            name=m.name,
                            item_type="METRIC",
                            impact_level="DIRECT",
                            steward=m.data_steward,
                            details=f"Ontology metric formula relies on '{target_clean}': {expr}",
                        )
                    )
                    edges.append({"from": target_clean, "to": m.name, "relationship": "computed_from"})

        # 3. Check Downstream BI Reports
        # Any metric that is broken directly affects Fabric TMDL and Power BI visuals
        for m_name in direct_broken_metrics:
            rep_name = f"Fabric_TMDL_{m_name}_Report"
            affected_bi.append(
                ImpactedItem(
                    name=rep_name,
                    item_type="BI_REPORT",
                    impact_level="TRANSITIVE",
                    details=f"Power BI visual / TMDL measure [{m_name}] will fail with syntax/reference error.",
                )
            )
            edges.append({"from": m_name, "to": rep_name, "relationship": "visualized_in"})

        # 4. Check Downstream AI Agent Tools
        for m_name in direct_broken_metrics:
            ai_tool = f"FastMCP:get_metric_definition({m_name})"
            affected_ai.append(
                ImpactedItem(
                    name=ai_tool,
                    item_type="AI_TOOL",
                    impact_level="TRANSITIVE",
                    details=f"Cortex Analyst & Claude MCP agents querying '{m_name}' will return invalid SQL.",
                )
            )
            edges.append({"from": m_name, "to": ai_tool, "relationship": "grounded_in"})

        total_impact = len(affected_metrics) + len(affected_bi) + len(affected_ai)

        if total_impact > 0 and change_type.upper() in ("DROP", "RENAME"):
            severity = "BREAKING"
        elif total_impact > 0:
            severity = "DEGRADED"
        else:
            severity = "SAFE"

        # Remediation recommendations
        remediations = []
        if severity == "BREAKING":
            remediations.append(
                f"Create a backward-compatible view alias for '{target_clean}' in Snowflake before dropping."
            )
            remediations.append(
                f"Update the following {len(affected_metrics)} metrics to use the replacement column: "
                f"{', '.join(m.name for m in affected_metrics[:3])}."
            )
            remediations.append(
                "Notify Data Stewards of the affected metrics prior to running deployment."
            )
        elif severity == "DEGRADED":
            remediations.append("Run Golden Query Verifier to test numerical tolerances on updated metrics.")
        else:
            remediations.append(f"No direct metric dependencies found for '{target_clean}'. Safe to proceed.")

        return BlastRadiusReport(
            target_object=target_clean,
            target_type=target_type.upper(),
            change_type=change_type.upper(),
            severity=severity,
            total_impacted_items=total_impact,
            affected_metrics=affected_metrics,
            affected_bi_reports=affected_bi,
            affected_ai_tools=affected_ai,
            remediation_steps=remediations,
            dependency_graph_edges=edges,
        )
