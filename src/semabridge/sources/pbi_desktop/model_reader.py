"""Model reader for Power BI Desktop Analysis Services engine.

Queries live engine metadata via DAX INFO.*() functions with $SYSTEM.TMSCHEMA_* DMV
fallbacks, and transforms the result into a TMSL-shaped model dictionary.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from semabridge.reconciliation.dax_executor import _open_adomd_connection

logger = logging.getLogger(__name__)

# AMO / Tabular integer data types -> TMSL string
DATA_TYPE_MAP: Dict[int, str] = {
    1: "string",
    2: "string",
    6: "int64",
    8: "double",
    9: "dateTime",
    10: "decimal",
    11: "boolean",
    17: "binary",
}

# SummarizeBy integer -> TMSL string
SUMMARIZE_BY_MAP: Dict[int, str] = {
    0: "default",
    1: "default",
    2: "none",
    3: "sum",
    4: "min",
    5: "max",
    6: "count",
    7: "average",
    8: "distinctCount",
}

# CrossFilteringBehavior integer -> TMSL string
CROSS_FILTER_MAP: Dict[int, str] = {
    1: "oneDirection",
    2: "bothDirections",
    3: "automatic",
}

# Cardinality integer -> TMSL string
CARDINALITY_MAP: Dict[int, str] = {
    1: "one",
    2: "many",
}


def _execute_query_adomd(conn_str: str, query: str) -> List[Dict[str, Any]]:
    """Execute a query against Analysis Services and return rows as list of dicts."""
    mode, driver = _open_adomd_connection(conn_str)
    if mode == "pyadomd":
        with driver as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                cols = [desc[0] for desc in (cur.description or [])]
                rows = cur.fetchall()
                return [dict(zip(cols, list(r))) for r in rows]

    AdomdConnection, AdomdCommand, cstr = driver
    conn = AdomdConnection(cstr)
    conn.Open()
    try:
        cmd = AdomdCommand(query, conn)
        reader = cmd.ExecuteReader()
        try:
            cols = [reader.GetName(i) for i in range(reader.FieldCount)]
            result = []
            while reader.Read():
                row = {}
                for i, col in enumerate(cols):
                    val = reader.GetValue(i)
                    if hasattr(val, "ToString") and val.__class__.__name__ == "DBNull":
                        row[col] = None
                    else:
                        row[col] = val
                result.append(row)
            return result
        finally:
            reader.Close()
    finally:
        conn.Close()


def _query_entity_with_fallback(
    query_exec: Callable[[str], List[Dict[str, Any]]],
    info_query: str,
    tmschema_query: str,
    entity_name: str,
) -> List[Dict[str, Any]]:
    """Execute an INFO query, falling back to TMSCHEMA DMV if INFO fails."""
    try:
        rows = query_exec(info_query)
        if rows:
            return rows
    except Exception as exc:
        logger.debug(f"INFO query failed for {entity_name}: {exc}, falling back to {tmschema_query}")

    try:
        return query_exec(tmschema_query)
    except Exception as exc:
        logger.warning(f"Failed to query {entity_name} using both INFO and TMSCHEMA: {exc}")
        return []


def read_model_entities(
    port: int,
    database_name: Optional[str] = None,
    query_executor: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Query all model entities from a local Analysis Services port."""
    if query_executor is None:
        conn_str = f"Data Source=localhost:{port};"
        if database_name:
            conn_str += f"Catalog={database_name};"
        query_executor = lambda q: _execute_query_adomd(conn_str, q)

    entities: Dict[str, List[Dict[str, Any]]] = {}

    entities["tables"] = _query_entity_with_fallback(
        query_executor,
        "EVALUATE INFO.TABLES()",
        "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES",
        "tables",
    )
    entities["columns"] = _query_entity_with_fallback(
        query_executor,
        "EVALUATE INFO.COLUMNS()",
        "SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS",
        "columns",
    )
    entities["measures"] = _query_entity_with_fallback(
        query_executor,
        "EVALUATE INFO.MEASURES()",
        "SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES",
        "measures",
    )
    entities["partitions"] = _query_entity_with_fallback(
        query_executor,
        "EVALUATE INFO.PARTITIONS()",
        "SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS",
        "partitions",
    )
    entities["relationships"] = _query_entity_with_fallback(
        query_executor,
        "EVALUATE INFO.RELATIONSHIPS()",
        "SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS",
        "relationships",
    )

    # Hierarchies and levels are DMVs
    try:
        entities["hierarchies"] = query_executor("SELECT * FROM $SYSTEM.TMSCHEMA_HIERARCHIES")
    except Exception:
        entities["hierarchies"] = []

    try:
        entities["levels"] = query_executor("SELECT * FROM $SYSTEM.TMSCHEMA_LEVELS")
    except Exception:
        entities["levels"] = []

    try:
        entities["model"] = query_executor("SELECT * FROM $SYSTEM.TMSCHEMA_MODEL")
    except Exception:
        entities["model"] = []

    return entities


def assemble_tmsl_from_entities(entities: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Assemble a TMSL-shaped dictionary from raw entity lists."""
    raw_tables = entities.get("tables", [])
    raw_columns = entities.get("columns", [])
    raw_measures = entities.get("measures", [])
    raw_partitions = entities.get("partitions", [])
    raw_relationships = entities.get("relationships", [])
    raw_hierarchies = entities.get("hierarchies", [])
    raw_levels = entities.get("levels", [])
    raw_model = (entities.get("model") or [{}])[0]

    tables_by_id: Dict[int, Dict[str, Any]] = {}
    table_id_to_name: Dict[int, str] = {}
    column_id_to_info: Dict[int, Tuple[str, str]] = {}

    # Tables
    for t in raw_tables:
        t_id = t.get("ID") if "ID" in t else t.get("[ID]")
        name = t.get("Name") if "Name" in t else t.get("[Name]")
        is_hidden = bool(t.get("IsHidden") or t.get("[IsHidden]") or False)
        desc = t.get("Description") or t.get("[Description]")

        table_dict: Dict[str, Any] = {
            "name": name,
            "columns": [],
            "partitions": [],
        }
        if desc:
            table_dict["description"] = desc
        if is_hidden:
            table_dict["isHidden"] = True

        tables_by_id[t_id] = table_dict
        table_id_to_name[t_id] = name

    # Columns
    for c in raw_columns:
        c_id = c.get("ID") if "ID" in c else c.get("[ID]")
        t_id = c.get("TableID") if "TableID" in c else c.get("[TableID]")
        col_type = c.get("Type") if "Type" in c else c.get("[Type]", 1)

        # Skip RowNumber columns (type 3)
        if col_type == 3:
            continue

        name = c.get("ExplicitName") or c.get("InferredName") or c.get("[ExplicitName]") or c.get("[InferredName]")
        source_col = c.get("SourceColumn") if "SourceColumn" in c else c.get("[SourceColumn]")
        raw_dt = c.get("ExplicitDataType") or c.get("InferredDataType") or c.get("[ExplicitDataType]") or c.get("[InferredDataType]")
        data_type = DATA_TYPE_MAP.get(raw_dt, "string")

        raw_sum = c.get("SummarizeBy") if "SummarizeBy" in c else c.get("[SummarizeBy]")
        summarize_by = SUMMARIZE_BY_MAP.get(raw_sum)

        is_hidden = bool(c.get("IsHidden") or c.get("[IsHidden]") or False)
        is_key = bool(c.get("IsKey") if "IsKey" in c else c.get("[IsKey]", False))
        desc = c.get("Description") or c.get("[Description]")
        expr = c.get("Expression") or c.get("[Expression]")
        fmt = c.get("FormatString") or c.get("[FormatString]")
        folder = c.get("DisplayFolder") or c.get("[DisplayFolder]")
        data_cat = c.get("DataCategory") or c.get("[DataCategory]")
        sort_by_id = c.get("SortByColumnID") if "SortByColumnID" in c else c.get("[SortByColumnID]")

        col_dict: Dict[str, Any] = {
            "name": name,
            "dataType": data_type,
        }
        if source_col and col_type != 2:
            col_dict["sourceColumn"] = source_col
        elif col_type == 2:
            col_dict["type"] = "calculated"
            if expr:
                col_dict["expression"] = expr
        elif expr:
            col_dict["expression"] = expr

        if summarize_by and summarize_by != "default":
            col_dict["summarizeBy"] = summarize_by
        if is_hidden:
            col_dict["isHidden"] = True
        if is_key:
            col_dict["isKey"] = True
        if data_cat:
            col_dict["dataCategory"] = data_cat
        if sort_by_id:
            col_dict["_sort_by_id"] = sort_by_id
        if desc:
            col_dict["description"] = desc
        if fmt:
            col_dict["formatString"] = fmt
        if folder:
            col_dict["displayFolder"] = folder

        if t_id in tables_by_id:
            tables_by_id[t_id]["columns"].append(col_dict)
            column_id_to_info[c_id] = (table_id_to_name[t_id], name)

    # Resolve sortByColumn names from _sort_by_id
    for t in tables_by_id.values():
        for col in t["columns"]:
            s_id = col.pop("_sort_by_id", None)
            if s_id and s_id in column_id_to_info:
                col["sortByColumn"] = column_id_to_info[s_id][1]

    # Hierarchies and Levels
    levels_by_hierarchy: Dict[int, List[Dict[str, Any]]] = {}
    sorted_levels = sorted(raw_levels, key=lambda l: (l.get("HierarchyID", 0), l.get("Ordinal", 0)))
    for l in sorted_levels:
        h_id = l.get("HierarchyID")
        col_id = l.get("ColumnID")
        l_name = l.get("Name")
        col_name = column_id_to_info.get(col_id, (None, l_name))[1]
        levels_by_hierarchy.setdefault(h_id, []).append({
            "name": l_name,
            "ordinal": l.get("Ordinal", 0),
            "column": col_name,
        })

    for h in raw_hierarchies:
        h_id = h.get("ID")
        t_id = h.get("TableID")
        h_name = h.get("Name")
        h_desc = h.get("Description")
        h_hidden = bool(h.get("IsHidden", False))
        h_dict: Dict[str, Any] = {
            "name": h_name,
            "levels": levels_by_hierarchy.get(h_id, []),
        }
        if h_desc:
            h_dict["description"] = h_desc
        if h_hidden:
            h_dict["isHidden"] = True

        if t_id in tables_by_id:
            tables_by_id[t_id].setdefault("hierarchies", []).append(h_dict)

    # Measures
    for m in raw_measures:
        t_id = m.get("TableID") if "TableID" in m else m.get("[TableID]")
        name = m.get("Name") if "Name" in m else m.get("[Name]")
        expr = m.get("Expression") if "Expression" in m else m.get("[Expression]")
        desc = m.get("Description") or m.get("[Description]")
        fmt = m.get("FormatString") or m.get("[FormatString]")
        is_hidden = bool(m.get("IsHidden") or m.get("[IsHidden]") or False)
        folder = m.get("DisplayFolder") or m.get("[DisplayFolder]")
        raw_dt = m.get("DataType") if "DataType" in m else m.get("[DataType]")
        data_type = DATA_TYPE_MAP.get(raw_dt)

        m_dict: Dict[str, Any] = {
            "name": name,
            "expression": expr or "",
        }
        if data_type:
            m_dict["dataType"] = data_type
        if desc:
            m_dict["description"] = desc
        if fmt:
            m_dict["formatString"] = fmt
        if is_hidden:
            m_dict["isHidden"] = True
        if folder:
            m_dict["displayFolder"] = folder

        if t_id in tables_by_id:
            tables_by_id[t_id].setdefault("measures", []).append(m_dict)

    # Partitions
    for p in raw_partitions:
        t_id = p.get("TableID") if "TableID" in p else p.get("[TableID]")
        p_name = p.get("Name") if "Name" in p else p.get("[Name]")
        p_type = p.get("Type") if "Type" in p else p.get("[Type]", 1)
        p_mode = p.get("Mode") if "Mode" in p else p.get("[Mode]", 1)
        q_def = p.get("QueryDefinition") if "QueryDefinition" in p else p.get("[QueryDefinition]")

        mode_str = "import" if p_mode == 1 else "directQuery"
        source_type = "m" if p_type == 1 else ("calculated" if p_type == 2 else "query")

        p_dict: Dict[str, Any] = {
            "name": p_name,
            "mode": mode_str,
            "source": {
                "type": source_type,
                "expression": q_def or "",
            },
        }
        if t_id in tables_by_id:
            tables_by_id[t_id]["partitions"].append(p_dict)

    # Relationships
    relationships: List[Dict[str, Any]] = []
    for r in raw_relationships:
        r_name = r.get("Name") if "Name" in r else r.get("[Name]")
        from_c_id = r.get("FromColumnID") if "FromColumnID" in r else r.get("[FromColumnID]")
        to_c_id = r.get("ToColumnID") if "ToColumnID" in r else r.get("[ToColumnID]")
        is_active = bool(r.get("IsActive") if "IsActive" in r else r.get("[IsActive]", True))
        raw_cross = r.get("CrossFilteringBehavior") if "CrossFilteringBehavior" in r else r.get("[CrossFilteringBehavior]", 1)
        cross_filter = CROSS_FILTER_MAP.get(raw_cross, "oneDirection")
        raw_from_card = r.get("FromCardinality") if "FromCardinality" in r else r.get("[FromCardinality]", 2)
        raw_to_card = r.get("ToCardinality") if "ToCardinality" in r else r.get("[ToCardinality]", 1)

        from_card = CARDINALITY_MAP.get(raw_from_card, "many")
        to_card = CARDINALITY_MAP.get(raw_to_card, "one")

        from_info = column_id_to_info.get(from_c_id)
        to_info = column_id_to_info.get(to_c_id)

        if from_info and to_info:
            rel_dict: Dict[str, Any] = {
                "name": r_name,
                "fromTable": from_info[0],
                "fromColumn": from_info[1],
                "toTable": to_info[0],
                "toColumn": to_info[1],
                "isActive": is_active,
                "crossFilteringBehavior": cross_filter,
                "fromCardinality": from_card,
                "toCardinality": to_card,
            }
            relationships.append(rel_dict)

    # Filter out auto-generated date tables that Power BI creates internally
    final_tables = sorted(
        [
            t
            for t in tables_by_id.values()
            if not t["name"].startswith("LocalDateTable_") and not t["name"].startswith("DateTableTemplate_")
        ],
        key=lambda t: t["name"],
    )

    final_relationships = sorted(
        [
            r
            for r in relationships
            if not r["fromTable"].startswith("LocalDateTable_") and not r["toTable"].startswith("LocalDateTable_")
        ],
        key=lambda r: (r["fromTable"], r["toTable"], r["fromColumn"], r["toColumn"]),
    )

    model_name = raw_model.get("Name") or "Model"
    culture = raw_model.get("Culture") or "en-US"

    return {
        "model": {
            "name": model_name,
            "culture": culture,
            "tables": final_tables,
            "relationships": final_relationships,
        }
    }


def read_tmsl_from_engine(
    port: int,
    database_name: Optional[str] = None,
    query_executor: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Extract model from running Desktop Analysis Services engine and return TMSL dict."""
    entities = read_model_entities(port, database_name, query_executor=query_executor)
    return assemble_tmsl_from_entities(entities)


def read_pbi_desktop_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
    *,
    adapter: Optional[Any] = None,
    max_models: int = 10,
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """(OSI models, per-model errors) for the selected Power BI Desktop models."""
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.sources.base import SourceConfigError, SourceError, get_source_adapter

    refs = [str(s).strip() for s in (selected_sources or []) if str(s or "").strip()]
    if not refs:
        models_cfg = (source_config or {}).get("models")
        if isinstance(models_cfg, list):
            refs = [m for m in models_cfg if m]
    if not refs:
        raise SourceConfigError("Select at least one Power BI Desktop model to review its measures.")

    adp = adapter or get_source_adapter("pbi_desktop")
    check = adp.validate_and_resolve(refs, max_models=max_models)

    models: List[Any] = []
    errors: List[Dict[str, str]] = []

    for identity in check.identities:
        try:
            tmsl = adp.extract_model(identity)
            if not tmsl or not tmsl.get("model"):
                raise SourceError(f"Model '{identity.display_name}' returned no definition.")

            field_aliases = []
            try:
                rl = adp.extract_report_layer(identity, tmsl=tmsl)
                field_aliases = getattr(rl, "field_aliases", []) or []
            except Exception as rl_exc:
                logger.debug("Report layer extraction skipped for %s: %s", identity.display_name, rl_exc)

            osi = TMSLToOSIConverter().to_osi(
                {
                    "tmsl": tmsl,
                    "dataset_id": identity.model_id,
                    "display_name": identity.display_name,
                    "field_aliases": field_aliases,
                }
            )
            models.append(osi)
        except Exception as exc:
            logger.warning("Ontology review: could not read Desktop model %s: %s", identity.display_name, exc)
            errors.append({"source": identity.display_name, "error": str(exc)})

    return models, errors
