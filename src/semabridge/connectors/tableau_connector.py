"""Tableau Data Source (.tds / .tdsx / .twb / .twbx) Ingestion Connector.

Extracts Tableau data sources, physical relations, column ownership,
joins/relationships, and calculated fields (including Level-of-Detail LOD expressions)
into a canonical OSI (Open Semantic Interchange) model.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIBaseModel,
    OSICardinality,
    OSIColumn,
    OSICrossFilterDirection,
    OSIDataType,
    OSIDataset,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)

logger = logging.getLogger("semabridge.connectors.tableau_connector")


class SourceNotSupportedError(NotImplementedError):
    """Raised when a BI source format is recognised but not supported yet."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source} ingestion is not supported yet: {reason}")


TABLEAU_UNSUPPORTED_REASON = (
    "Tableau .tds/.twb parsing does not yet resolve column-to-table ownership, "
    "joins/relationships, or Tableau calculation semantics."
)


_TYPE_MAP: Dict[str, OSIDataType] = {
    "string": OSIDataType.STRING,
    "str": OSIDataType.STRING,
    "integer": OSIDataType.INTEGER,
    "int": OSIDataType.INTEGER,
    "real": OSIDataType.FLOAT,
    "float": OSIDataType.FLOAT,
    "double": OSIDataType.FLOAT,
    "date": OSIDataType.DATE,
    "datetime": OSIDataType.DATETIME,
    "timestamp": OSIDataType.DATETIME,
    "boolean": OSIDataType.BOOLEAN,
    "bool": OSIDataType.BOOLEAN,
}


def clean_ident(s: str) -> str:
    """Strip Tableau bracket and quote delimiters from identifiers."""
    if not s:
        return ""
    # Strip [Table].[Column] or [Column]
    return re.sub(r"[\[\]'\"]", "", s).strip()


def sanitize_ident_for_rel(name: str) -> str:
    """Ensure identifier characters are safe for OSI relationship regex."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", clean_ident(name)).strip("_")
    return clean or "ITEM"


def translate_tableau_formula_to_sql(formula: str) -> str:
    """Translate Tableau calculation formulas into standard SQL / OSI expressions."""
    if not formula:
        return ""
    f = formula.strip()

    # 1. FIXED LOD: { FIXED [Dim1], [Dim2] : SUM([Metric]) }
    lod_match = re.match(r"^\{\s*FIXED\s*([^:]*)\s*:\s*(.*?)\s*\}$", f, re.IGNORECASE | re.DOTALL)
    if lod_match:
        dims_raw = lod_match.group(1).strip()
        agg_raw = lod_match.group(2).strip()
        agg_trans = translate_tableau_formula_to_sql(agg_raw)
        if dims_raw:
            dims = [clean_ident(d) for d in dims_raw.split(",") if d.strip()]
            quoted_dims = [f'"{d}"' if not d.startswith('"') else d for d in dims]
            return f"{agg_trans} OVER (PARTITION BY {', '.join(quoted_dims)})"
        else:
            return f"{agg_trans} OVER ()"

    # 2. ZN(x) -> COALESCE(x, 0)
    f = re.sub(r"\bZN\s*\((.*?)\)", r"COALESCE(\1, 0)", f, flags=re.IGNORECASE)

    # 3. IFNULL(a, b) -> COALESCE(a, b)
    f = re.sub(r"\bIFNULL\s*\(", "COALESCE(", f, flags=re.IGNORECASE)

    # 4. ISNULL(a) -> (a IS NULL)
    f = re.sub(r"\bISNULL\s*\((.*?)\)", r"(\1 IS NULL)", f, flags=re.IGNORECASE)

    # 5. COUNTD(x) -> COUNT(DISTINCT x)
    f = re.sub(r"\bCOUNTD\s*\((.*?)\)", r"COUNT(DISTINCT \1)", f, flags=re.IGNORECASE)

    # 6. Clean Table.Column references: [Table].[Column] -> "Table"."Column"
    f = re.sub(r"\[([A-Za-z0-9_]+)\]\.\[([A-Za-z0-9_]+)\]", r'"\1"."\2"', f)
    # [Column] -> "Column" (inside expressions)
    f = re.sub(r"\[([A-Za-z0-9_]+)\]", r'"\1"', f)

    return f


def detect_aggregation_type(formula: str) -> OSIAggregationType:
    """Infer the metric aggregation type from the calculation expression."""
    up = formula.upper().strip()
    if "COUNTD(" in up or "COUNT(DISTINCT" in up:
        return OSIAggregationType.COUNT_DISTINCT
    if up.startswith("SUM(") or "SUM(" in up:
        return OSIAggregationType.SUM
    if up.startswith("AVG(") or "AVG(" in up or "AVERAGE(" in up:
        return OSIAggregationType.AVG
    if up.startswith("COUNT(") or "COUNT(" in up:
        return OSIAggregationType.COUNT
    if up.startswith("MIN(") or "MIN(" in up:
        return OSIAggregationType.MIN
    if up.startswith("MAX(") or "MAX(" in up:
        return OSIAggregationType.MAX
    return OSIAggregationType.NONE


class TableauConnector:
    """Ingests Tableau Data Sources (.tds, .tdsx, .twb, .twbx) into canonical OSIModels."""

    def __init__(self, file_path_or_xml: str):
        self.raw_input = file_path_or_xml

    def _read_xml_content(self) -> str:
        s = self.raw_input.strip()
        if s.startswith("<"):
            return s

        path = Path(s)
        if not path.exists():
            # If not a valid file path, return string as XML
            return s

        suffix = path.suffix.lower()
        if suffix in (".tdsx", ".twbx") or zipfile.is_zipfile(path):
            with zipfile.ZipFile(path, "r") as z:
                # Find .tds or .twb inside archive
                for fname in z.namelist():
                    if fname.lower().endswith((".tds", ".twb")):
                        return z.read(fname).decode("utf-8", errors="replace")
            raise ValueError(f"No .tds or .twb file found inside archive: {path}")

        return path.read_text(encoding="utf-8", errors="replace")

    def extract_osi_model(self, model_name: Optional[str] = None) -> OSIModel:
        """Parse Tableau XML content and return an OSIModel with tables, joins, and measures."""
        xml_text = self._read_xml_content()
        root = ET.fromstring(xml_text)

        # Locate target <datasource>
        ds_elem = root
        if root.tag == "workbook":
            datasources = root.findall(".//datasources/datasource")
            # Filter out Tableau internal Parameters datasource
            ds_candidates = [
                d for d in datasources
                if (d.get("name") or "").lower() != "parameters"
                and (d.get("formatted-name") or "").lower() != "parameters"
            ]
            if ds_candidates:
                if model_name:
                    match = next((d for d in ds_candidates if d.get("name") == model_name or d.get("formatted-name") == model_name), None)
                    ds_elem = match or ds_candidates[0]
                else:
                    ds_elem = ds_candidates[0]

        source_model_name = (
            model_name
            or ds_elem.get("formatted-name")
            or ds_elem.get("name")
            or (Path(self.raw_input).stem if Path(self.raw_input).exists() else "TableauModel")
        )
        # Clean model name for unique identifier
        safe_model_name = re.sub(r"[^A-Za-z0-9_]", "_", source_model_name).strip("_") or "TableauModel"

        # 1. Parse connection and relation trees
        discovered_tables: Dict[str, Dict[str, Any]] = {}
        discovered_joins: List[Dict[str, Any]] = []
        col_to_tables_map: Dict[str, Set[str]] = {}

        # Scan for <cols><map key='[Table].[Col]' value='...'/>
        for col_map in ds_elem.findall(".//connection/cols/map"):
            k = col_map.get("key") or ""
            # e.g. [SalesFact].[Units]
            parts = [clean_ident(p) for p in re.findall(r"\[([^\]]+)\]", k)]
            if len(parts) >= 2:
                tbl_name, col_name = parts[0], parts[1]
                col_to_tables_map.setdefault(col_name.lower(), set()).add(tbl_name)
                col_to_tables_map.setdefault(f"{tbl_name.lower()}.{col_name.lower()}", set()).add(tbl_name)

        # Scan for metadata records
        for meta_elem in ds_elem.findall(".//metadata-records/metadata-record"):
            if (meta_elem.get("class") or "").lower() == "column":
                parent = clean_ident(meta_elem.findtext("parent-name") or "")
                local = clean_ident(meta_elem.findtext("local-name") or "")
                remote = clean_ident(meta_elem.findtext("remote-name") or "")
                if parent:
                    if local:
                        col_to_tables_map.setdefault(local.lower(), set()).add(parent)
                    if remote:
                        col_to_tables_map.setdefault(remote.lower(), set()).add(parent)

        def _traverse_relation(rel_node: ET.Element) -> Optional[str]:
            rel_type = (rel_node.get("type") or "").lower()
            if rel_type in ("table", "text") or "table" in rel_node.attrib:
                name = rel_node.get("name") or rel_node.get("table") or "Table"
                clean_name = clean_ident(name)
                physical_table = clean_ident(rel_node.get("table") or clean_name)
                discovered_tables[clean_name] = {
                    "name": clean_name,
                    "physical_table": physical_table,
                    "schema": clean_ident(rel_node.get("schema") or ""),
                    "columns": {},
                }
                return clean_name

            if rel_type == "join":
                join_op = (rel_node.get("join") or "inner").lower()
                child_rels = rel_node.findall("./relation")
                left_name = None
                right_name = None
                if len(child_rels) >= 2:
                    left_name = _traverse_relation(child_rels[0])
                    right_name = _traverse_relation(child_rels[1])

                # Parse join clauses
                for clause in rel_node.findall("./clause"):
                    exprs = clause.findall(".//expression[@op='=']/expression")
                    if len(exprs) >= 2:
                        left_op = clean_ident(exprs[0].get("op") or "")
                        right_op = clean_ident(exprs[1].get("op") or "")
                        l_parts = left_op.split(".")
                        r_parts = right_op.split(".")

                        l_tbl = l_parts[0] if len(l_parts) > 1 else (left_name or "TableA")
                        l_col = l_parts[-1]
                        r_tbl = r_parts[0] if len(r_parts) > 1 else (right_name or "TableB")
                        r_col = r_parts[-1]

                        discovered_joins.append({
                            "from_table": l_tbl,
                            "from_col": l_col,
                            "to_table": r_tbl,
                            "to_col": r_col,
                            "join_type": join_op,
                        })
                return left_name or right_name

            # Fallback for nested elements
            last = None
            for child in rel_node.findall("./relation"):
                last = _traverse_relation(child)
            return last

        # Traverse relations starting from root relation inside connection
        root_relation = ds_elem.find(".//connection/relation")
        if root_relation is not None:
            _traverse_relation(root_relation)

        # Fallback: if no relation tags found, create a default dataset
        if not discovered_tables:
            discovered_tables[safe_model_name] = {
                "name": safe_model_name,
                "physical_table": safe_model_name,
                "schema": "",
                "columns": {},
            }

        first_table_name = list(discovered_tables.keys())[0]

        # 2. Parse <column> elements
        metrics: List[OSIMetric] = []
        table_column_objs: Dict[str, List[OSIColumn]] = {t: [] for t in discovered_tables}

        for col_elem in ds_elem.findall("./column"):
            raw_name = col_elem.get("name") or ""
            col_name = clean_ident(raw_name)
            if not col_name or col_name.startswith("Calculation_"):
                # Handle unnamed or generated calculation names gracefully
                caption = col_elem.get("caption") or col_name
                col_name = clean_ident(caption)
            if not col_name:
                continue

            caption = col_elem.get("caption") or col_name
            dtype_str = (col_elem.get("datatype") or "string").lower()
            osi_dtype = _TYPE_MAP.get(dtype_str, OSIDataType.STRING)
            role = (col_elem.get("role") or "dimension").lower()

            calc_elem = col_elem.find("./calculation")
            formula = calc_elem.get("formula") if calc_elem is not None else None

            # Resolve which datasets own this column
            target_tbls = col_to_tables_map.get(col_name.lower()) or {first_table_name}
            valid_tbls = [t for t in target_tbls if t in table_column_objs]
            if not valid_tbls:
                valid_tbls = [first_table_name]

            # Check if this column is a Measure / Calculation
            is_measure = (
                role == "measure"
                or (formula and detect_aggregation_type(formula) != OSIAggregationType.NONE)
                or (formula and "{" in formula and "FIXED" in formula.upper())
            )

            if is_measure and formula:
                sql_expr = translate_tableau_formula_to_sql(formula)
                agg_type = detect_aggregation_type(formula)
                metric = OSIMetric(
                    unique_name=col_name,
                    label=caption,
                    dataset=valid_tbls[0],
                    expression=sql_expr,
                    sql_expression=sql_expr,
                    aggregation=agg_type,
                    description=f"Tableau measure '{caption}'",
                )
                metrics.append(metric)
            else:
                col_obj = OSIColumn(
                    unique_name=col_name,
                    label=caption,
                    data_type=osi_dtype,
                    is_calculated=bool(formula),
                    expression=translate_tableau_formula_to_sql(formula) if formula else None,
                    is_key=(col_name.lower().endswith("id") or col_name.lower().endswith("key")),
                )
                for tbl in valid_tbls:
                    table_column_objs[tbl].append(col_obj)

        # 3. Build OSIDataset list
        datasets: List[OSIDataset] = []
        for tbl_name, tbl_info in discovered_tables.items():
            cols = table_column_objs.get(tbl_name, [])
            if not cols:
                # Ensure each dataset has at least one column
                cols = [OSIColumn(unique_name="ID", label="ID", data_type=OSIDataType.INTEGER, is_key=True)]

            ds = OSIDataset(
                unique_name=tbl_name,
                label=tbl_name,
                source_table=tbl_info["physical_table"],
                source_schema=tbl_info["schema"] or None,
                columns=cols,
                is_fact=tbl_name == first_table_name,
            )
            datasets.append(ds)

        # 4. Build OSIRelationship list
        relationships: List[OSIRelationship] = []
        rel_index = 1
        for j in discovered_joins:
            f_tbl = j["from_table"]
            f_col = j["from_col"]
            t_tbl = j["to_table"]
            t_col = j["to_col"]

            # Validate tables exist in datasets
            f_clean = clean_ident(f_tbl)
            t_clean = clean_ident(t_tbl)
            if f_clean not in discovered_tables:
                f_clean = first_table_name
            if t_clean not in discovered_tables:
                t_clean = first_table_name

            rel_name = f"REL_{sanitize_ident_for_rel(f_clean)}_{sanitize_ident_for_rel(f_col)}__{sanitize_ident_for_rel(t_clean)}_{sanitize_ident_for_rel(t_col)}"
            if any(r.unique_name == rel_name for r in relationships):
                rel_name = f"{rel_name}_{rel_index}"
                rel_index += 1

            rel = OSIRelationship(
                unique_name=rel_name,
                from_dataset=f_clean,
                from_columns=[f_col],
                to_dataset=t_clean,
                to_columns=[t_col],
                cardinality=OSICardinality.MANY_TO_ONE,
                cross_filter_direction=OSICrossFilterDirection.SINGLE,
                is_active=True,
            )
            relationships.append(rel)

        return OSIModel(
            unique_name=safe_model_name,
            label=source_model_name,
            source_platform="tableau",
            datasets=datasets,
            metrics=metrics,
            relationships=relationships,
        )

    def extract_to_osi(self, model_name: Optional[str] = None) -> OSIModel:
        """Alias for extract_osi_model, implementing the BaseExtractor contract."""
        return self.extract_osi_model(model_name)

    def extract(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Extract semantic model metadata as a dictionary."""
        return self.extract_osi_model(model_name).model_dump()

