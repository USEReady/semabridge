from typing import Dict, Any


TYPE_MAP = {
    "integer": "int",
    "int": "int",
    "bigint": "int",
    "string": "string",
    "varchar": "string",
    "text": "string",
    "float": "float",
    "double": "float",
}


def map_type(fabric_type: str) -> str:
    if not fabric_type:
        return "string"
    t = fabric_type.lower()
    return TYPE_MAP.get(t, "string")


def fabric_to_osi(fabric_model: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a minimal Fabric model to an OSI-style payload (`datasets`).

    The function is permissive and accepts the older `tables` shape or the
    newer `datasets/fields` shape. The returned payload uses the official
    `datasets` array with `unique_name` and `columns` entries.
    """
    tables = []
    # Support legacy 'tables' input for compatibility
    if fabric_model.get("tables"):
        tables = fabric_model.get("tables", [])
    else:
        for ds in fabric_model.get("datasets", []):
            cols = []
            for f in ds.get("fields", []):
                cols.append({"name": f.get("name"), "type": map_type(f.get("type"))})
            tables.append({"name": ds.get("name"), "columns": cols})
    # map relationships if present
    rels = []
    for r in fabric_model.get("relationships", []):
        rels.append(
            {
                "from": {"dataset": r.get("from_dataset"), "column": r.get("from_field")},
                "to": {"dataset": r.get("to_dataset"), "column": r.get("to_field")},
                "cardinality": r.get("cardinality", "many-to-one"),
            }
        )
    # Emit OSI-style `datasets` with `unique_name` and `columns`/`data_type`
    datasets = []
    for t in tables:
        cols = []
        for c in t.get("columns", []):
            cols.append({"unique_name": c.get("name"), "data_type": c.get("type")})
        datasets.append({"unique_name": t.get("name"), "columns": cols})
    out = {"datasets": datasets}
    if rels:
        out["relationships"] = rels
    # map simple metrics/measure definitions if present
    measures = []
    for m in fabric_model.get("metrics", []):
        measures.append({
            "name": m.get("name"),
            "expression": m.get("expression"),
            "type": m.get("type", "numeric"),
        })
    if measures:
        out["measures"] = measures
    return out


def fabric_from_osi(osi_model: Dict[str, Any]) -> Dict[str, Any]:
    """Convert from OSI-style payload to a minimal Fabric structure.

    Accepts either `datasets` (preferred) or legacy `tables` shape.
    """
    datasets = []
    source = osi_model.get("datasets") or osi_model.get("tables") or []
    for t in source:
        fields = []
        cols = t.get("columns") or []
        for c in cols:
            # Support both `unique_name`/`data_type` and `name`/`type`
            name = c.get("unique_name") or c.get("name")
            typ = c.get("data_type") or c.get("type")
            fields.append({"name": name, "type": typ})
        datasets.append({"name": t.get("unique_name") or t.get("name"), "fields": fields})
    result = {"datasets": datasets}
    # pass through relationships if present
    rels = osi_model.get("relationships")
    if rels:
        fabric_rels = []
        for r in rels:
            fabric_rels.append(
                {
                    "from_dataset": r.get("from", {}).get("dataset"),
                    "from_field": r.get("from", {}).get("column"),
                    "to_dataset": r.get("to", {}).get("dataset"),
                    "to_field": r.get("to", {}).get("column"),
                    "cardinality": r.get("cardinality"),
                }
            )
        result["relationships"] = fabric_rels
    # pass through measures -> metrics if present
    measures = osi_model.get("measures")
    if measures:
        metrics = []
        for m in measures:
            metrics.append({
                "name": m.get("name"),
                "expression": m.get("expression"),
                "type": m.get("type", "numeric"),
            })
        result["metrics"] = metrics
    return result
