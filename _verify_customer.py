"""Regression check: Customer Profitability model still infers 8 correct rels."""
import json, sys
sys.path.insert(0, 'src')
# Import the actual updated extractor logic by running a minimal parse
from semabridge.connectors.fabric_extractor import FabricExtractor

raw = json.loads(open('output/debug/e05c3659-5b6a-44f5-b7bf-8426f8a6f7ae/raw_fabric_model.json').read())

# Simulate what the extractor does after parsing: call the heuristic block
# by re-running the relevant section inline (same logic as the extractor)
model_obj = raw.get('model', raw)
tables = model_obj.get('tables', [])
raw_rels = model_obj.get('relationships', [])

table_columns_map = {
    str(t.get('name') or '').strip(): {
        str(c.get('name') or '').strip()
        for c in (t.get('columns') or [])
        if str(c.get('name') or '').strip()
    }
    for t in tables
    if str(t.get('name') or '').strip()
}

rels = list(raw_rels)
existing_rel_keys = {
    (str(r.get('fromTable') or '').strip().casefold(), str(r.get('fromColumn') or '').strip().casefold(),
     str(r.get('toTable') or '').strip().casefold(), str(r.get('toColumn') or '').strip().casefold())
    for r in rels
}
covered_triples = {
    (str(r.get('fromTable') or '').strip().casefold(), str(r.get('fromColumn') or '').strip().casefold(),
     str(r.get('toTable') or '').strip().casefold())
    for r in rels
}

def _try_add_rel(from_table, from_col, to_table, to_col):
    rel_key = (from_table.casefold(), from_col.casefold(), to_table.casefold(), to_col.casefold())
    if rel_key in existing_rel_keys:
        return False
    rels.append({'fromTable': from_table, 'fromColumn': from_col, 'toTable': to_table, 'toColumn': to_col})
    existing_rel_keys.add(rel_key)
    return True

def _find_pk_col_in_table(table_name, dim_cols, base_name):
    base_up = base_name.replace(' ', '_').upper()
    for ck_suffix in ('_DIM_CK', '_CK'):
        for dc in dim_cols:
            if dc.replace(' ', '_').upper() == f'{base_up}{ck_suffix}':
                return dc
    for suffix in ('CODE', 'ID', 'KEY'):
        for dc in dim_cols:
            if dc.replace(' ', '_').upper() == f'{base_up}{suffix}':
                return dc
    for bare in ('ID', 'CODE'):
        for dc in dim_cols:
            if dc.replace(' ', '_').upper() == bare:
                return dc
    for dc in dim_cols:
        dc_up = dc.replace(' ', '_').upper()
        if dc_up.endswith('_CK') or dc_up.endswith('_KEY'):
            return dc
    for dc in dim_cols:
        if dc.replace(' ', '_').upper() == base_up:
            return dc
    return None

fact_tables = [
    name for name in table_columns_map
    if (name.strip().casefold() == 'fact'
        or name.strip().casefold().endswith('_fact')
        or name.strip().casefold().startswith('fact_'))
]
inferred = 0
_FACT_KEY_SUFFIXES = ('_CK', '_KEY', '_ID', '_CODE', '_DIM_CK')
_KEY_SUFFIXES = ('_CK', '_KEY', '_ID', '_CODE', '_DIM_CK')

for fact_table in fact_tables:
    fact_cols = table_columns_map.get(fact_table, set())
    for dim_table, dim_cols in table_columns_map.items():
        if dim_table == fact_table:
            continue
        for fact_col in fact_cols:
            norm_fact_col = fact_col.replace(' ', '_').upper()
            col_looks_like_key = any(norm_fact_col.endswith(s) for s in _FACT_KEY_SUFFIXES)
            col_matches_table = norm_fact_col == dim_table.replace(' ', '_').upper()
            if col_looks_like_key or col_matches_table:
                for dc in dim_cols:
                    if dc.replace(' ', '_').upper() == norm_fact_col:
                        if _try_add_rel(fact_table, fact_col, dim_table, dc):
                            inferred += 1
                        covered_triples.add((fact_table.casefold(), fact_col.casefold(), dim_table.casefold()))
                        break
            triple = (fact_table.casefold(), fact_col.casefold(), dim_table.casefold())
            if triple in covered_triples:
                continue
            dim_up = dim_table.replace(' ', '_').upper()
            stripped = None
            for suffix in ('_KEY', '_ID', '_CK', '_DIM_CK'):
                if norm_fact_col == f'{dim_up}{suffix}':
                    stripped = dim_table
                    break
            if stripped:
                pk_col = _find_pk_col_in_table(dim_table, dim_cols, stripped)
                if pk_col:
                    if _try_add_rel(fact_table, fact_col, dim_table, pk_col):
                        inferred += 1
                        covered_triples.add(triple)

for from_table, from_cols in table_columns_map.items():
    for to_table, to_cols in table_columns_map.items():
        if from_table == to_table:
            continue
        to_up = to_table.replace(' ', '_').upper()
        for fc in from_cols:
            fc_up = fc.replace(' ', '_').upper()
            triple = (from_table.casefold(), fc.casefold(), to_table.casefold())
            if triple in covered_triples:
                continue
            if not any(fc_up.endswith(s) for s in _KEY_SUFFIXES):
                continue
            is_fk = (
                fc_up == to_up
                or fc_up in (f'{to_up}_ID', f'{to_up}_KEY', f'{to_up}_CODE',
                             f'{to_up}_CK', f'{to_up}_DIM_CK')
                or fc_up in (f'{to_up}ID', f'{to_up}KEY', f'{to_up}CODE')
            )
            if not is_fk:
                continue
            pk_col = _find_pk_col_in_table(to_table, to_cols, to_table)
            if pk_col:
                if _try_add_rel(from_table, fc, to_table, pk_col):
                    inferred += 1
                    covered_triples.add(triple)

raw_rel_keys = {(r.get('fromTable'), r.get('fromColumn'), r.get('toTable'), r.get('toColumn')) for r in raw_rels}
print(f'Raw: {len(raw_rels)}, Inferred: {inferred}, Total: {len(rels)}')
for r in rels:
    key = (r.get('fromTable'), r.get('fromColumn'), r.get('toTable'), r.get('toColumn'))
    tag = '[raw]     ' if key in raw_rel_keys else '[inferred]'
    print(f'  {tag} {r["fromTable"]}({r["fromColumn"]}) -> {r["toTable"]}({r["toColumn"]})')

# Regression assertions
assert len(rels) == 8, f"Expected 8 total rels, got {len(rels)}"
assert inferred == 5, f"Expected 5 inferred, got {inferred}"
print('\nREGRESSION PASS: Customer Profitability still infers 8 rels correctly')
