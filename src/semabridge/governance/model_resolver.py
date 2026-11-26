"""Model resolver for Target-Aware Blast Radius.

Resolves targets from ontology first, and when absent (e.g. an ADDITION or warehouse-only object),
falls back to inspecting the live warehouse adapter view definition rather than failing with not-found.
Reads only: nothing here writes to the warehouse.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ONTOLOGY = "ontology"
WAREHOUSE = "warehouse"
NOT_FOUND = "not_found"


@dataclass
class ResolvedModel:
    """Where the target was found, and the objects available to trace."""

    source: str                       # ontology | warehouse | not_found
    objects: List[Any] = field(default_factory=list)
    platform: Optional[str] = None
    reason: Optional[str] = None

    @property
    def found(self) -> bool:
        return self.source in (ONTOLOGY, WAREHOUSE)


def _contains(objects: List[Any], name: str, target_type: str) -> bool:
    """A metric is found by its name; a column or table by any expression using it."""
    if target_type in ("COLUMN", "TABLE"):
        pattern = re.compile(
            r"(?<![A-Za-z0-9_%$#])" + re.escape(name) + r"(?![A-Za-z0-9_%$#])",
            re.IGNORECASE,
        )
        return any(pattern.search(str(getattr(o, "expression", "") or "")) for o in objects)
    return any(str(getattr(o, "name", "")).strip().casefold() == name.casefold()
               for o in objects)


def resolve_model(
    target_name: str,
    ontology_objects: Optional[List[Any]] = None,
    adapter=None,
    view_name: Optional[str] = None,
    platform: Optional[str] = None,
    connection_config: Optional[Dict[str, Any]] = None,
    target_type: str = "METRIC",
) -> ResolvedModel:
    """Find the model that contains the target, ontology first."""
    name = str(target_name or "").strip()
    if not name:
        return ResolvedModel(NOT_FOUND, reason="no target name given")
    ttype = str(target_type or "METRIC").strip().upper()

    objects = list(ontology_objects or [])
    if _contains(objects, name, ttype):
        return ResolvedModel(ONTOLOGY, objects, platform)

    if adapter is None or not view_name:
        return ResolvedModel(
            NOT_FOUND, objects, platform,
            reason=f"'{target_name}' is not in the deployed model",
        )

    try:
        raw = adapter.fetch_definition_text(view_name, connection_config=connection_config)
        definition = adapter.parse_definition(raw, view_name=view_name, source="live")
    except Exception as exc:  # noqa: BLE001 - a warehouse failure is a reason, not a crash
        logger.warning("Could not read %s from the warehouse: %s", view_name, exc)
        return ResolvedModel(NOT_FOUND, objects, platform, reason=str(exc))

    live = list(getattr(definition, "objects", []) or [])
    if _contains(live, name, ttype):
        return ResolvedModel(WAREHOUSE, live, platform)

    return ResolvedModel(
        NOT_FOUND, objects, platform,
        reason=f"'{target_name}' is in neither the deployed model nor the warehouse view",
    )
