"""REST API router exposing the dynamic connector registry (sources & targets)."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from semabridge.core.target_rules import (
    get_target_rules,
    list_registered_sources,
    list_registered_targets,
)

router = APIRouter(prefix="/api/connectors/registry", tags=["connector_registry"])


@router.get("", response_model=Dict[str, List[Dict[str, Any]]])
def get_connector_registry() -> Dict[str, List[Dict[str, Any]]]:
    """Return all dynamically registered source and target connector descriptors."""
    return {
        "sources": list_registered_sources(),
        "targets": list_registered_targets(),
    }


@router.get("/targets/{target_id}", response_model=Dict[str, Any])
def get_target_connector_descriptor(target_id: str) -> Dict[str, Any]:
    """Return the registry descriptor for a single target connector."""
    try:
        rules = get_target_rules(target_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return rules.to_registry_descriptor()
