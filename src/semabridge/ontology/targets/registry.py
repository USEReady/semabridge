"""Registry: one read-only ontology adapter per target platform."""

from __future__ import annotations

from typing import Dict

from semabridge.ontology.targets.base import OntologyTargetAdapter


class UnsupportedOntologyTarget(KeyError):
    """No ontology adapter for this target."""


def _adapters() -> Dict[str, OntologyTargetAdapter]:
    from semabridge.ontology.targets.databricks import DatabricksOntologyAdapter
    from semabridge.ontology.targets.snowflake import SnowflakeOntologyAdapter

    return {"databricks": DatabricksOntologyAdapter(), "snowflake": SnowflakeOntologyAdapter()}


def ontology_adapter_for(target: str) -> OntologyTargetAdapter:
    key = str(target or "").strip().lower()
    adapter = _adapters().get(key)
    if adapter is None:
        raise UnsupportedOntologyTarget(f"no ontology target adapter for {target!r}")
    return adapter
