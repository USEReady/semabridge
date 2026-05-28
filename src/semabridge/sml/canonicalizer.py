import hashlib
import json
from typing import Dict, Any


def canonicalize_model(osi_model: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a minimal canonical SML representation from a simple OSI-like dict.

    - Sorts tables and columns for deterministic ordering
    - Emits a stable `id` per table computed from table name and column names/types
    """

    # Accept either legacy 'tables' or modern OSI 'datasets' and normalize
    if "tables" in osi_model and isinstance(osi_model.get("tables"), list):
        tables = osi_model.get("tables", [])
    else:
        # Convert OSI-style datasets -> legacy tables shape expected by canonicalizer
        datasets = osi_model.get("datasets", []) or []
        tables = []
        for ds in datasets:
            cols = []
            for c in ds.get("columns", []) or []:
                # prefer 'name' if present, otherwise 'unique_name'
                col_name = c.get("name") or c.get("unique_name")
                col_type = c.get("type") or c.get("data_type")
                cols.append({"name": col_name, "type": col_type})
            tables.append({"name": ds.get("unique_name") or ds.get("name"), "columns": cols})

    result = {"tables": []}
    for table in sorted(tables, key=lambda t: t.get("name", "")):
        cols = sorted(table.get("columns", []), key=lambda c: c.get("name", ""))
        id_source = table.get("name", "") + "|" + ",".join(
            f"{c.get('name','')}:{c.get('type','')}" for c in cols
        )
        table_id = hashlib.sha1(id_source.encode("utf-8")).hexdigest()
        result["tables"].append(
            {
                "id": table_id,
                "name": table.get("name"),
                "columns": [{"name": c.get("name"), "type": c.get("type")} for c in cols],
            }
        )

    # measures (metrics)
    measures = osi_model.get("measures", [])
    if measures:
        result["measures"] = []
        for m in sorted(measures, key=lambda x: x.get("name", "")):
            # stable id for measure from name + expression
            expr = m.get("expression", "")
            id_source = f"measure|{m.get('name','')}|{expr}"
            mid = hashlib.sha1(id_source.encode("utf-8")).hexdigest()
            result["measures"].append({
                "id": mid,
                "name": m.get("name"),
                "expression": expr,
                "type": m.get("type"),
            })
    return result


def serialize_deterministic(obj: Dict[str, Any]) -> str:
    """Return a deterministic JSON serialization for byte-for-byte comparisons."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
