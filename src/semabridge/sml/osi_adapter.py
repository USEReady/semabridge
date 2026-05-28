from typing import Dict, Any

from .canonicalizer import canonicalize_model


def canonical_from_osi(osi_model: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a simple OSI-like model into the canonical SML representation."""
    return canonicalize_model(osi_model)


def osi_from_canonical(canonical: Dict[str, Any]) -> Dict[str, Any]:
    """Convert canonical SML back to a simple OSI-like structure.

    This is intentionally minimal — it only reconstructs tables with names and columns.
    """
    tables = []
    for t in canonical.get("tables", []):
        tables.append({"name": t.get("name"), "columns": t.get("columns", [])})
    return {"tables": tables}
