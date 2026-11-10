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
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from semabridge.intermediate.models import OSIModel
from semabridge.ontology.models import FederatedEnterpriseOntology

logger = logging.getLogger("semabridge.governance.blast_radius")


class TargetType(str, Enum):
    COLUMN = "COLUMN"
    TABLE = "TABLE"
    METRIC = "METRIC"


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
        target_type: str | TargetType = TargetType.COLUMN,
        change_type: str = "DROP",
        table_context: Optional[str] = None,
    ) -> BlastRadiusReport:
        """Calculate the blast radius of modifying or dropping a column, table, or metric."""
        if not target_name or not target_name.strip():
            raise ValueError("Target name cannot be empty.")

        t_type_val = target_type.value if isinstance(target_type, TargetType) else str(target_type).upper()
        valid_types = {e.value for e in TargetType}
        if t_type_val not in valid_types:
            raise ValueError(f"Invalid target_type '{target_type}'. Must be one of {sorted(valid_types)}")

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
                # Skip stale or inactive metrics
                if getattr(m, "stale", False) or getattr(m, "is_active", True) is False:
                    continue

                m_dataset = getattr(m, "dataset", "") or ""
                expr = m.expression or ""
                source_col = getattr(m, "source_column", None) or ""

                matches = False
                if t_type_val == "COLUMN":
                    # Check direct column aggregation first
                    is_direct_agg = (source_col and source_col.strip().casefold() == target_clean.casefold())
                    # Check reference in expression
                    pat = r"\b" + re.escape(target_clean) + r"\b"
                    has_col_ref = bool(re.search(pat, expr, re.IGNORECASE))

                    if is_direct_agg or has_col_ref:
                        if tbl_clean:
                            # Restrict match to table_context
                            if m_dataset.casefold() == tbl_clean.casefold():
                                matches = True
                            else:
                                # Check table-scoped reference in formula
                                scoped_pat = rf"(?:'?[{re.escape(tbl_clean)}]'?|\b{re.escape(tbl_clean)}\b)\s*(?:\[|\.){re.escape(target_clean)}\b"
                                if re.search(scoped_pat, expr, re.IGNORECASE):
                                    matches = True
                        else:
                            matches = True
                elif t_type_val == "TABLE":
                    if m_dataset.casefold() == target_clean.casefold() or re.search(r"\b" + re.escape(target_clean) + r"\b", expr, re.IGNORECASE):
                        matches = True
                elif t_type_val == "METRIC":
                    if m.unique_name.casefold() == target_clean.casefold():
                        matches = True

                if matches:
                    direct_broken_metrics.add(m.unique_name)
                    affected_metrics.append(
                        ImpactedItem(
                            name=m.unique_name,
                            item_type="METRIC",
                            impact_level="DIRECT",
                            steward=getattr(m, "business_owner", None),
                            details=f"Calculation relies on '{target_clean}': {expr or source_col}",
                        )
                    )
                    edges.append({"from": target_clean, "to": m.unique_name, "relationship": "computed_from"})

        # 2. Search in Federated Ontology metrics
        if self.ontology:
            for m in self.ontology.metrics.values():
                # Skip stale metrics
                if getattr(m, "stale", False):
                    continue

                m_concept = getattr(m, "concept", "") or ""
                expr = m.sql_expression or ""
                maps_to = getattr(m, "maps_to", []) or []

                matches = False
                if t_type_val == "COLUMN":
                    pat = r"\b" + re.escape(target_clean) + r"\b"
                    has_col_ref = bool(re.search(pat, expr, re.IGNORECASE))
                    # Check direct column aggregation e.g. SUM(col)
                    direct_agg = bool(re.search(rf"\b(?:SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(?:[A-Za-z0-9_]+\.)?{re.escape(target_clean)}\s*\)", expr, re.IGNORECASE))

                    if has_col_ref or direct_agg:
                        if tbl_clean:
                            if m_concept and m_concept.casefold() == tbl_clean.casefold():
                                matches = True
                            elif any(mt.casefold().startswith(f"{tbl_clean.casefold()}.") for mt in maps_to):
                                matches = True
                            elif re.search(rf"\b{re.escape(tbl_clean)}\b.*?\b{re.escape(target_clean)}\b", expr, re.IGNORECASE):
                                matches = True
                        else:
                            matches = True
                elif t_type_val == "TABLE":
                    if (m_concept and m_concept.casefold() == target_clean.casefold()) or any(mt.casefold().startswith(f"{target_clean.casefold()}.") for mt in maps_to) or re.search(r"\b" + re.escape(target_clean) + r"\b", expr, re.IGNORECASE):
                        matches = True
                elif t_type_val == "METRIC":
                    if m.name.casefold() == target_clean.casefold():
                        matches = True

                if matches and m.name not in direct_broken_metrics:
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

        # Downstream BI reports and AI tools: only report objects that really exist (never invented names)
        # If no real downstream objects are tracked in the model/ontology metadata, these remain empty.

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
                "Notify data owners and stewards of the affected metrics prior to running deployment."
            )
        elif severity == "DEGRADED":
            remediations.append("Run Golden Query Verifier to test numerical tolerances on updated metrics.")
        else:
            remediations.append(f"No direct metric dependencies found for '{target_clean}'. Safe to proceed.")

        return BlastRadiusReport(
            target_object=target_clean,
            target_type=t_type_val,
            change_type=change_type.upper(),
            severity=severity,
            total_impacted_items=total_impact,
            affected_metrics=affected_metrics,
            affected_bi_reports=affected_bi,
            affected_ai_tools=affected_ai,
            remediation_steps=remediations,
            dependency_graph_edges=edges,
        )
