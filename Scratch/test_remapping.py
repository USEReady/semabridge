import re
from typing import Dict, Set, Optional, List, Any
from dataclasses import dataclass

@dataclass
class MockMetric:
    unique_name: str
    dataset: str
    sql_expression: Optional[str] = None
    expression: Optional[str] = None
    source_column: Optional[str] = None
    aggregation: Optional[Any] = None

class MockTranslator:
    def _resolve_column_name_for_dataset(self, known_columns: Set[str], candidate: str) -> Optional[str]:
        if candidate in known_columns:
            return candidate
        return None

def _is_virtual_measures_table(dataset_name: Optional[str], dataset_col_lookup: Dict[str, Set[str]]) -> bool:
    if not dataset_name:
        return False
    name_upper = dataset_name.upper().replace(" ", "_").replace("-", "_")
    is_measures_name = (
        name_upper == "MEASURES"
        or name_upper == "PROJECT_MEASURES"
        or name_upper.endswith("_MEASURES")
        or name_upper.startswith("MEASURES_")
    )
    if not is_measures_name:
        return False
    cols = dataset_col_lookup.get(dataset_name, set())
    if not cols:
        return True
    non_id_cols = {c for c in cols if c.upper() not in ("ID", "NAME", "DESCRIPTION")}
    return len(non_id_cols) == 0

def _remap_virtual_measures_table_refs(
    sql_expr: str,
    measures_alias: str,
    dataset_col_lookup: Dict[str, Set[str]],
    dataset_aliases: Dict[str, str],
    fact_aliases: Optional[Set[str]] = None,
) -> Optional[str]:
    if not sql_expr:
        return sql_expr

    physical_datasets = [
        ds for ds in dataset_col_lookup.keys()
        if not _is_virtual_measures_table(ds, dataset_col_lookup)
    ]
    
    pattern = re.compile(
        rf'(?P<alias>{re.escape(measures_alias)})\s*\.\s*(?P<col>"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
    )

    unresolved = False
    translator = MockTranslator()

    def _replace(match: re.Match) -> str:
        nonlocal unresolved
        raw_col = match.group("col")
        col_name = raw_col.strip('"')

        owners: list[str] = []
        for ds in physical_datasets:
            cols = dataset_col_lookup.get(ds, set())
            resolved_col = translator._resolve_column_name_for_dataset(cols, col_name)
            if resolved_col:
                owners.append(ds)

        if not owners:
            unresolved = True
            return match.group(0)

        chosen_dataset = owners[0]
        chosen_alias = dataset_aliases.get(chosen_dataset)
        if not chosen_alias:
            unresolved = True
            return match.group(0)

        chosen_col = translator._resolve_column_name_for_dataset(
                dataset_col_lookup.get(chosen_dataset, set()),
                col_name,
            ) or col_name
        return f'{chosen_alias}."{chosen_col}"'

    rewritten = pattern.sub(_replace, sql_expr)

    if unresolved:
        print(f"DEBUG: Could not resolve all virtual measures-table references for alias '{measures_alias}'. SQL: {sql_expr}")
        return None

    return rewritten

# Test Case
dataset_col_lookup = {
    "Project Measures": set(["ID"]),
    "Corporate DSI Last Refreshed": set(["GL_REFRESH_DATETIME", "PBI_REFRESH_DATETIME"]),
    "Inventory Fact": set(["TPLANT_CURR_SKEY", "TOTAL_STOCK_QTY"])
}
dataset_aliases = {
    "Project Measures": "PROJECT_MEASURES",
    "Corporate DSI Last Refreshed": "CORPORATE_DSI_LAST_REFRESHED",
    "Inventory Fact": "INVENTORY_FACT"
}

# 1. Qualified reference - should work
sql1 = 'MAX(PROJECT_MEASURES."GL_REFRESH_DATETIME")'
remap1 = _remap_virtual_measures_table_refs(sql1, "PROJECT_MEASURES", dataset_col_lookup, dataset_aliases)
print(f"Test 1: {sql1} -> {remap1}")

# 2. Bare reference - will NOT be caught by _remap_virtual_measures_table_refs
sql2 = 'MAX("GL_REFRESH_DATETIME")'
remap2 = _remap_virtual_measures_table_refs(sql2, "PROJECT_MEASURES", dataset_col_lookup, dataset_aliases)
print(f"Test 2: {sql2} -> {remap2}")
