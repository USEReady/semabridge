"""Impact of a sync before it runs: what depends on the fields it removes.

Live analysis only (never demo data): the federated ontology (metrics that
build on the field), the project's verified AI queries, and its certified
measures. Attached to the sync's "fields will be removed" confirmation so the
person deciding sees what breaks.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_FIELDS = 50


def _simulator(project_id: str):
    from semabridge.governance.blast_radius import BlastRadiusSimulator
    from semabridge.ontology.store import ontology_store

    try:
        from semabridge.ai.verified_queries_service import list_verified_queries_for_project

        verified = list_verified_queries_for_project(project_id)
    except Exception:  # noqa: BLE001
        verified = []
    try:
        from semabridge.reconciliation.certification_service import get_certified_metric_names

        certified = get_certified_metric_names(project_id)
    except Exception:  # noqa: BLE001
        certified = set()
    sim = BlastRadiusSimulator(
        ontology=ontology_store.get_federated_ontology(),
        verified_queries=verified,
        certified_metrics=certified,
        mock_mode=False,
    )
    return sim, {str(c).casefold() for c in certified}


def field_impact(sim: Any, certified: set, name: str, kind: str) -> Dict[str, Any]:
    target_type = "METRIC" if str(kind or "").lower() in ("metric", "measure") else "COLUMN"
    impact: Dict[str, Any] = {"certified": name.casefold() in certified, "dependent_metrics": [], "ai_queries": []}
    try:
        report = sim.simulate_impact(target_name=name, target_type=target_type, change_type="DROP", mock_mode=False)
    except Exception as exc:  # noqa: BLE001 -- unknown field: no known dependents
        logger.debug("Impact of %s unavailable: %s", name, exc)
        return impact
    impact["severity"] = report.severity
    impact["dependent_metrics"] = [i.name for i in report.affected_metrics if i.name.casefold() != name.casefold()][:10]
    impact["ai_queries"] = [i.name for i in report.affected_ai_tools][:10]
    return impact


def annotate_removed_fields(project_id: str, findings: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Add ``impact`` to each removed field and an ``impact_summary`` to each model."""
    findings = list(findings or [])
    if not findings:
        return findings
    try:
        sim, certified = _simulator(project_id)
    except Exception as exc:  # noqa: BLE001 -- the check itself must still work
        logger.warning("Impact analysis unavailable for %s: %s", project_id, exc)
        return findings
    budget = _MAX_FIELDS
    for model in findings:
        affected = 0
        for field in model.get("removed") or []:
            if budget <= 0:
                break
            budget -= 1
            field["impact"] = field_impact(sim, certified, str(field.get("name") or ""), str(field.get("kind") or ""))
            imp = field["impact"]
            if imp["certified"] or imp["dependent_metrics"] or imp["ai_queries"]:
                affected += 1
        model["impact_summary"] = {"fields_with_dependents": affected}
    return findings
