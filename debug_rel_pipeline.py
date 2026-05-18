"""
debug_rel_pipeline.py - Trace each stage of the relationship pipeline for Customer_Profitability.
Run from the semabridge root: python debug_rel_pipeline.py
"""
import json, sys
from pathlib import Path

MODEL_JSON = Path("output/debug/e05c3659-5b6a-44f5-b7bf-8426f8a6f7ae/raw_fabric_model.json")

def step(title): print(f"\n{'='*60}\n{title}\n{'='*60}")

# ── Stage 1: Raw Fabric JSON ───────────────────────────────────
step("STAGE 1 - Raw Fabric relationships")
raw = json.loads(MODEL_JSON.read_text())
model_obj = raw.get("model", raw)
raw_rels = model_obj.get("relationships", [])
print(f"  Count: {len(raw_rels)}")
for r in raw_rels:
    print(f"    {r.get('fromTable')}({r.get('fromColumn')}) -> {r.get('toTable')}({r.get('toColumn')})")

tables = model_obj.get("tables", [])
all_tables = {t["name"]: t for t in tables if "name" in t}
print(f"\n  Tables in model: {sorted(all_tables.keys())}")
step("STAGE 1b - Columns per table")
for tname, t in sorted(all_tables.items()):
    cols = [c["name"] for c in t.get("columns", [])]
    print(f"  {tname}: {cols}")

# ── Stage 2: FabricExtractor heuristic inference ─────────────
step("STAGE 2 - FabricExtractor heuristic (from fabric_extractor.py:884)")
import re

table_columns_map = {
    str(t.get("name") or "").strip(): {
        str(c.get("name") or "").strip()
        for c in (t.get("columns") or [])
        if str(c.get("name") or "").strip()
    }
    for t in tables
    if str(t.get("name") or "").strip()
}

rels = list(raw_rels)
existing_rel_keys = {
    (
        str(r.get("fromTable") or "").strip().casefold(),
        str(r.get("fromColumn") or "").strip().casefold(),
        str(r.get("toTable") or "").strip().casefold(),
        str(r.get("toColumn") or "").strip().casefold(),
    )
    for r in rels
}

fact_table = next((n for n in table_columns_map if n.strip().casefold() == "fact"), None)
print(f"  Fact table detected: {fact_table!r}")
inferred = []
if fact_table:
    fact_cols = table_columns_map.get(fact_table, set())
    print(f"  Fact columns: {sorted(fact_cols)}")
    for dim_table, dim_cols in table_columns_map.items():
        if dim_table == fact_table:
            continue
        for fact_col in fact_cols:
            norm_fact_col = fact_col.replace(" ", "_").upper()
            if not norm_fact_col.endswith("_KEY") and norm_fact_col != "ID":
                print(f"    SKIP (not _KEY/ID): {fact_col!r} (normalized={norm_fact_col!r})")
                continue
            matching_dim_col = None
            for dc in dim_cols:
                if dc.replace(" ", "_").upper() == norm_fact_col:
                    matching_dim_col = dc
                    break
            if not matching_dim_col:
                continue
            rel_key = (fact_table.casefold(), fact_col.casefold(), dim_table.casefold(), matching_dim_col.casefold())
            if rel_key in existing_rel_keys:
                print(f"    SKIP (already exists): {fact_col} -> {dim_table}.{matching_dim_col}")
                continue
            print(f"    INFERRED: {fact_table}({fact_col}) -> {dim_table}({matching_dim_col})")
            inferred.append({"fromTable": fact_table, "fromColumn": fact_col, "toTable": dim_table, "toColumn": matching_dim_col})

print(f"\n  Total after heuristic: {len(rels) + len(inferred)} ({len(rels)} raw + {len(inferred)} inferred)")

# ── Stage 3: TMDL→OSI conversion ─────────────────────────────
step("STAGE 3 - TMDLToOSIConverter output")
sys.path.insert(0, "src")
try:
    from semabridge.converter.tmsl_to_osi import TMDLToOSIConverter
    source_data = {
        "tmdl": raw,
        "workspace_id": "test-ws",
        "dataset_id": "e05c3659-5b6a-44f5-b7bf-8426f8a6f7ae",
        "display_name": "Customer_Profitability",
    }
    osi = TMDLToOSIConverter().to_osi(source_data)
    print(f"  Relationships in OSI: {len(osi.relationships)}")
    for r in osi.relationships:
        print(f"    {r.from_dataset}({r.from_columns}) -> {r.to_dataset}({r.to_columns})")
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

# ── Stage 4: OSI→SML conversion ──────────────────────────────
step("STAGE 4 - OSI→SML via OSIToSMLConverter")
try:
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    sml = OSIToSMLConverter().from_osi(osi)
    print(f"  Relationships in SML: {len(sml.relationships)}")
    for r in sml.relationships:
        print(f"    {r.unique_name}: {r.from_dataset}({r.from_column}) -> {r.to_dataset}({r.to_column})")

    # ── Stage 5: Column resolution check ─────────────────────
    step("STAGE 5 - Physical column lookup per dataset (what TablesClauseBuilder sees)")
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.utils.identifiers import IdentifierSanitizer

    class MockConnMgr:
        def _execute_sql(self, *a, **k): return None

    cfg = SnowflakeConfig(account="test", user="test", warehouse="WH", database="DB", schema_name="PUBLIC")
    from semabridge.connectors.schema_manager import SnowflakeSchemaManager
    sm = SnowflakeSchemaManager(cfg, ConnectorBehavior(), IdentifierSanitizer(), MockConnMgr())

    for ds in sml.datasets:
        phys = sm._collect_physical_source_columns(ds)
        rel_ds = [r for r in sml.relationships if r.from_dataset == ds.unique_name or r.to_dataset == ds.unique_name]
        if rel_ds or phys:
            pk_cols = [c.unique_name for c in ds.columns if c.is_key]
            print(f"\n  Dataset: {ds.unique_name}")
            print(f"    Physical cols (sample): {list(phys.keys())[:8]}")
            print(f"    is_key columns: {pk_cols}")
            print(f"    Involved in rels: {[(r.from_dataset+'('+r.from_column+')->'+r.to_dataset+'('+r.to_column+')') for r in rel_ds]}")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()
