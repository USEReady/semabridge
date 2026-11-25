"""Assemble TMSL from recorded INFO/TMSCHEMA fixtures and run through the conversion pipeline.

Compares generated OSI, SML, and DDL against the characterization snapshots:
Tests/characterization/snapshots/real_pbix/competitive_marketing_analysis_more_synonyms_1.*
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

RECORDED_DIR = Path(__file__).resolve().parent / "recorded"
SNAPSHOT_DIR = REPO_ROOT / "Tests" / "characterization" / "snapshots" / "real_pbix"
PBIX_SLUG = "competitive_marketing_analysis_more_synonyms_1"

# Type mappings: AMO/Tabular integer -> TMSL string
DATA_TYPE_MAP = {
    1: "string",
    2: "string",
    6: "int64",
    8: "double",
    9: "dateTime",
    10: "decimal",
    11: "boolean",
    17: "binary",
}

SUMMARIZE_BY_MAP = {
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

CROSS_FILTER_MAP = {
    1: "oneDirection",
    2: "bothDirections",
    3: "automatic",
}

CARDINALITY_MAP = {
    1: "one",
    2: "many",
}


def load_fixtures() -> Dict[str, List[Dict[str, Any]]]:
    fixtures = {}
    for f in RECORDED_DIR.glob("*.json"):
        if f.stem in ("assembled_tmsl", "generated.osi", "generated.sml"):
            continue
        fixtures[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return fixtures


def assemble_tmsl(fixtures: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Assemble a TMSL model definition from TMSCHEMA / INFO fixtures."""
    raw_tables = fixtures.get("tmschema_tables") or fixtures.get("info_tables", [])
    raw_columns = fixtures.get("tmschema_columns") or fixtures.get("info_columns", [])
    raw_measures = fixtures.get("tmschema_measures") or fixtures.get("info_measures", [])
    raw_partitions = fixtures.get("tmschema_partitions") or fixtures.get("info_partitions", [])
    raw_relationships = fixtures.get("tmschema_relationships") or fixtures.get("info_relationships", [])
    raw_model = (fixtures.get("tmschema_model") or [{}])[0]

    tables_by_id: Dict[int, Dict[str, Any]] = {}
    table_id_to_name: Dict[int, str] = {}
    column_id_to_info: Dict[int, Tuple[str, str]] = {}  # col_id -> (table_name, col_name)

    # Tables
    for t in raw_tables:
        t_id = t["ID"] if "ID" in t else t.get("[ID]")
        name = t["Name"] if "Name" in t else t.get("[Name]")
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
    raw_hierarchies = fixtures.get("tmschema_hierarchies", [])
    raw_levels = fixtures.get("tmschema_levels", [])
    levels_by_hierarchy: Dict[int, List[Dict[str, Any]]] = {}
    
    # Sort levels by Ordinal
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
        h_dict = {
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
            }
        }
        if t_id in tables_by_id:
            tables_by_id[t_id]["partitions"].append(p_dict)

    # Relationships
    relationships: List[Dict[str, Any]] = []
    for r in raw_relationships:
        r_name = r.get("Name") if "Name" in r else r.get("[Name]")
        from_t_id = r.get("FromTableID") if "FromTableID" in r else r.get("[FromTableID]")
        from_c_id = r.get("FromColumnID") if "FromColumnID" in r else r.get("[FromColumnID]")
        to_t_id = r.get("ToTableID") if "ToTableID" in r else r.get("[ToTableID]")
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
    final_tables = sorted([
        t for t in tables_by_id.values()
        if not t["name"].startswith("LocalDateTable_") and not t["name"].startswith("DateTableTemplate_")
    ], key=lambda t: t["name"])

    tmsl = {
        "model": {
            "name": raw_model.get("Name") or "Competitive Marketing Analysis (More Synonyms) (1)",
            "culture": raw_model.get("Culture") or "en-US",
            "tables": final_tables,
            "relationships": sorted([
                r for r in relationships
                if not r["fromTable"].startswith("LocalDateTable_") and not r["toTable"].startswith("LocalDateTable_")
            ], key=lambda r: (r["fromTable"], r["toTable"], r["fromColumn"], r["toColumn"])),
        }
    }
    return tmsl


def compare_with_snapshot(kind: str, generated_text: str) -> List[str]:
    snap_file = SNAPSHOT_DIR / f"{PBIX_SLUG}.{kind}"
    if not snap_file.exists():
        return [f"Snapshot file not found: {snap_file}"]
    expected = snap_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    diff = list(difflib.unified_diff(
        expected.splitlines(keepends=True),
        generated_text.splitlines(keepends=True),
        fromfile=f"snapshot/{snap_file.name}",
        tofile=f"live_assembled/{kind}",
        n=2,
    ))
    return diff


def run_pipeline():
    fixtures = load_fixtures()
    tmsl = assemble_tmsl(fixtures)
    
    # Save assembled TMSL
    out_tmsl = RECORDED_DIR / "assembled_tmsl.json"
    out_tmsl.write_text(json.dumps(tmsl, indent=2), encoding="utf-8")
    print(f"Assembled TMSL saved to {out_tmsl.name}")

    # Import characterization build helpers
    import importlib.util
    spec = importlib.util.spec_from_file_location("characterization_build", REPO_ROOT / "Tests" / "characterization" / "_build.py")
    _build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_build)

    from semabridge.core.source_format import from_pbix_tmsl
    from semabridge.core.engine.conversion.pbix import _convert_pbix_to_sml
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.sources import report_parsing

    # Read report layout directly from pbix zip (without invoking pbixray)
    pbix_path = REPO_ROOT / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix"
    connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
    connector._open_archive()
    layout = connector._extract_report_layout()
    tables_for_alias = connector._parse_tables(tmsl["model"])
    measures_for_alias = connector._parse_measures(tmsl["model"])
    
    pres, unresolved = connector._parse_presentation_metadata(layout, tables_for_alias, measures_for_alias)
    field_aliases, ambiguous = report_parsing.summarize_report_aliases(pres)
    connector._close_archive()

    print(f"Report aliases found: {len(field_aliases)}, ambiguous: {len(ambiguous)}, unresolved: {len(unresolved)}")

    sf = from_pbix_tmsl(
        project_id=_build.PROJECT_ID,
        run_id="run-characterization",
        tmsl=tmsl,
        pbix_path="Examples/Competitive Marketing Analysis (More Synonyms) (1).pbix",
        field_aliases=field_aliases,
        unresolved_report_field_references=unresolved,
        ambiguous_report_aliases=ambiguous,
    )

    ctx = _build._context("pbix", sf)
    sml = _convert_pbix_to_sml(_build._ENGINE_SELF, ctx)

    osi_text = _build._dump(ctx.osi_model)
    sml_text = _build._dump(sml)
    ddl_text = _build._ddl(sml)

    # Save outputs
    (RECORDED_DIR / "generated.osi.json").write_text(osi_text, encoding="utf-8")
    (RECORDED_DIR / "generated.sml.json").write_text(sml_text, encoding="utf-8")
    (RECORDED_DIR / "generated.ddl.sql").write_text(ddl_text, encoding="utf-8")

    # Diffs
    print("\n================ COMPARING OSI ================")
    osi_diff = compare_with_snapshot("osi.json", osi_text)
    print(f"OSI diff lines: {len(osi_diff)}")
    if osi_diff:
        print("".join(osi_diff[:50]))

    print("\n================ COMPARING SML ================")
    sml_diff = compare_with_snapshot("sml.json", sml_text)
    print(f"SML diff lines: {len(sml_diff)}")
    if sml_diff:
        print("".join(sml_diff[:50]))

    print("\n================ COMPARING DDL ================")
    ddl_diff = compare_with_snapshot("ddl.sql", ddl_text)
    print(f"DDL diff lines: {len(ddl_diff)}")
    if ddl_diff:
        print("".join(ddl_diff[:50]))

    return osi_diff, sml_diff, ddl_diff


if __name__ == "__main__":
    run_pipeline()
