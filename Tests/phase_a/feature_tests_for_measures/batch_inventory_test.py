import os
import re
import csv
import glob
import json
import pandas as pd
from pathlib import Path

# Import Phase A validation and inventory modules without modifying them
import sys
phase_a_dir = os.path.abspath(r"..\phase_a")
if phase_a_dir not in sys.path:
    sys.path.insert(0, phase_a_dir)

from validate_semantic_view import parse_ddl
from relationship_inventory import alias_index, norm_col, has_key, sb_table, sb_column, AUTO_DATE

def norm(name):
    s = os.path.splitext(os.path.basename(name))[0]
    s = re.sub(r"^debug_generated_sql_", "", s, flags=re.I)
    s = re.sub(r"^[0-9a-f]{32}_", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())

def get_source_relationships_safe(pbix):
    from pbixray import PBIXRay
    r = PBIXRay(pbix).relationships
    out = []
    for row in r.itertuples():
        from_t = getattr(row, "FromTableName", None)
        to_t = getattr(row, "ToTableName", None)
        from_c = getattr(row, "FromColumnName", None)
        to_c = getattr(row, "ToColumnName", None)

        # Skip invalid or auto-calendar dates
        if not from_t or not to_t or pd.isna(from_t) or pd.isna(to_t):
            continue
        if str(from_t).startswith(AUTO_DATE) or str(to_t).startswith(AUTO_DATE):
            continue
        if str(from_t) in ("None", "nan") or str(to_t) in ("None", "nan"):
            continue

        out.append({
            "from_t": str(from_t), "from_c": str(from_c or ""),
            "to_t": str(to_t), "to_c": str(to_c or ""),
            "active": bool(getattr(row, "IsActive", True)),
            "card": str(getattr(row, "Cardinality", "") or ""),
            "xfilter": str(getattr(row, "CrossFilteringBehavior", "") or ""),
        })
    return out

def reconcile_safe(pbix, ddl_text):
    tables, ddl_rels = parse_ddl(ddl_text)
    src = get_source_relationships_safe(pbix)
    idx = alias_index(tables)

    used, rows = set(), []
    for s in src:
        f_aliases = idx.get(sb_table(s["from_t"]), set())
        t_aliases = idx.get(sb_table(s["to_t"]), set())
        fc, tc = norm_col(sb_column(s["from_c"])), norm_col(sb_column(s["to_c"]))
        candidates = []
        for i, d in enumerate(ddl_rels):
            if i in used:
                continue
            dfc = [norm_col(c) for c in d["from_cols"]]
            dtc = [norm_col(c) for c in (d["to_cols"] or tables.get(d["to"], {}).get("pk", []))]
            if d["from"] in f_aliases and d["to"] in t_aliases:
                kind = "EMITTED" if (dfc == [fc] and dtc == [tc]) else "EMITTED_DIFFERENT"
            elif d["from"] in t_aliases and d["to"] in f_aliases:
                kind = "EMITTED_REVERSED" if (dfc == [tc] and dtc == [fc]) else "EMITTED_DIFFERENT"
            else:
                continue
            candidates.append((kind, i, d))
        exact = [c for c in candidates if c[0] in ("EMITTED", "EMITTED_REVERSED")]
        pick = exact or candidates
        if not pick:
            outcome, d = "MISSING", None
        elif len(pick) > 1:
            outcome, d = "AMBIGUOUS", pick[0][2]
            used.add(pick[0][1])
        else:
            outcome, d = pick[0][0], pick[0][2]
            used.add(pick[0][1])
        key_ok = (has_key(tables, d["to"], d["to_cols"] or tables[d["to"]]["pk"])
                  if d and d["to"] in tables else None)
        
        if not f_aliases or not t_aliases:
            missing_tbls = [n for n, a in ((s["from_t"], f_aliases), (s["to_t"], t_aliases)) if not a]
            note = "table not in DDL: " + ", ".join(str(m) for m in missing_tbls)
        elif outcome == "MISSING":
            note = "both tables in DDL, relationship not -> dropped; trace logs / drop ledger"
        elif outcome == "AMBIGUOUS":
            note = f"{len(pick)} DDL relationships could match; review by hand"
        elif key_ok is False:
            note = f"referenced side '{d['to']}' has no PRIMARY KEY/UNIQUE on {d['to_cols']}"
        else:
            note = ""

        rows.append({
            "source_relationship": f"{s['from_t']}[{s['from_c']}] -> {s['to_t']}[{s['to_c']}]",
            "active": "yes" if s["active"] else "NO",
            "cardinality": s["card"], "cross_filter": s["xfilter"],
            "outcome": outcome,
            "ddl_relationship": f"{d['name']}: {d['from']}{d['from_cols']} -> {d['to']}{d['to_cols']}" if d else "",
            "key_on_referenced_side": "" if key_ok is None else ("yes" if key_ok else "NO"),
            "note": note,
        })

    extra = [d for i, d in enumerate(ddl_rels) if i not in used]
    counts = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    summary = {
        "source": len(src),
        "ddl_relationships": len(ddl_rels),
        "ddl_tables": len(tables),
        "EMITTED": counts.get("EMITTED", 0),
        "EMITTED_REVERSED": counts.get("EMITTED_REVERSED", 0),
        "EMITTED_DIFFERENT": counts.get("EMITTED_DIFFERENT", 0),
        "AMBIGUOUS": counts.get("AMBIGUOUS", 0),
        "MISSING": counts.get("MISSING", 0),
        "EXTRA": len(extra),
        "key_missing": sum(1 for r in rows if r["key_on_referenced_side"] == "NO"),
    }
    return rows, extra, summary

temp_uploads = os.path.expandvars(r"%LOCALAPPDATA%\Temp\semabridge\uploads")
pbix_dirs = [temp_uploads, "uploads", "."]
pbix_map = {}
for d in pbix_dirs:
    for p in glob.glob(os.path.join(d, "**", "*.pbix"), recursive=True):
        k = norm(p)
        if k not in pbix_map or os.path.getmtime(p) > os.path.getmtime(pbix_map[k]):
            pbix_map[k] = p

ddls = sorted(glob.glob("output/debug/debug_generated_sql_*.sql"))

matched_models = []
unmatched_models = []

all_summaries = []
all_inventory_rows = []

for ddl in ddls:
    k = norm(ddl)
    model_name = os.path.splitext(os.path.basename(ddl))[0].replace("debug_generated_sql_", "")
    if k in pbix_map:
        matched_models.append((model_name, pbix_map[k], ddl))
    else:
        unmatched_models.append(model_name)

print(f"Total Debug SQL Models: {len(ddls)}")
print(f"Matched Models: {len(matched_models)}")
print(f"Unavailable/Unmatched Models: {len(unmatched_models)} -> {unmatched_models}\n")

for model_name, pbix_path, ddl_path in matched_models:
    with open(ddl_path, "r", encoding="utf-8") as f:
        ddl_text = f.read()
    rows, extra, summary = reconcile_safe(pbix_path, ddl_text)
    summary["model"] = model_name
    all_summaries.append(summary)
    for r in rows:
        r["model"] = model_name
        all_inventory_rows.append(r)

out_dir = Path("phase_a_results")
out_dir.mkdir(parents=True, exist_ok=True)

# Write summary
summary_csv = out_dir / "safe_batch_summary.csv"
with open(summary_csv, "w", newline="", encoding="utf-8") as f:
    fields = ["model", "source", "ddl_relationships", "ddl_tables", "EMITTED", "MISSING", "EXTRA", "key_missing"]
    writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for s in all_summaries:
        writer.writerow(s)

print(f"=== BATCH INVENTORY RESULTS ({len(all_summaries)} models) ===")
total_src = sum(s["source"] for s in all_summaries)
total_emitted = sum(s["EMITTED"] for s in all_summaries)
total_missing = sum(s["MISSING"] for s in all_summaries)
total_key_missing = sum(s["key_missing"] for s in all_summaries)

print(f"Total Source Relationships: {total_src}")
print(f"Total EMITTED: {total_emitted} ({total_emitted/total_src*100:.1f}%)")
print(f"Total MISSING: {total_missing} ({total_missing/total_src*100:.1f}%)")
print(f"Total Relationships with Key Missing on Target: {total_key_missing}")

print("\nModel Breakdown:")
for s in all_summaries:
    print(f"  {s['model']:<35} | Src: {s['source']:>2} | Emit: {s['EMITTED']:>2} | Miss: {s['MISSING']:>2} | KeyMiss: {s['key_missing']:>2}")


