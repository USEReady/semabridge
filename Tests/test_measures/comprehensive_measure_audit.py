import sys, os, re, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

import openpyxl
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer

EXCEL_PATH = r"C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise (1).xlsx"

wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
ws = wb["Measures Report"]

models = {}
current_model = None
for r in ws.iter_rows(values_only=True):
    c0 = str(r[0]).strip() if r and r[0] is not None else ""
    c1 = str(r[1]).strip() if r and len(r) > 1 and r[1] is not None else ""
    m = re.search(r"^(.*?)\s*[\W_]+\s*(\d+)\s*measures\b", c0, re.I)
    if m:
        current_model = m.group(1).strip()
        models[current_model] = []
        continue
    if current_model and c0:
        table, meas = "", c0
        m_tm = re.match(r"^'?(.+?)'?\[(.+)\]$", c0)
        if m_tm:
            table, meas = m_tm.group(1).replace("''", "'"), m_tm.group(2)
        models[current_model].append((table, meas, c1))

translator = DAXTranslator()

results = {}
failure_buckets = {
    "DAX Comments (-- or //)": [],
    "VAR ... RETURN Unhandled (VarNode)": [],
    "TRUE() / FALSE() Lexer / Literal Error": [],
    "KEEPFILTERS() in FILTER()": [],
    "SUMX / COUNTAX / AVERAGEX Aggregation": [],
    "Virtual Table Alias / Column Discard": [],
    "SELECTEDVALUE / VALUE / MROUND Missing": [],
    "ALL() Windowing / Non-Scalar In Semantic View": [],
    "Cascading Dependent Measure (Parent NULL)": [],
    "Other Syntax / Complex DAX": [],
}

print(f"=== AUDITING ALL {sum(len(v) for v in models.values())} MEASURES ACROSS {len(models)} MODELS ===\n")

for model_name, measures in models.items():
    model_results = []
    resolved_in_model = {}
    
    # 3 topological passes to resolve dependencies
    for pass_num in range(3):
        for table, meas, dax in measures:
            k = meas.upper()
            if k in resolved_in_model:
                continue
            
            # Check translation
            res = translator.translate(
                dax,
                table.upper() if table else "T",
                table,
                metric_name=meas,
                skip_tier5=True,
            )
            if res.is_success and res.sql:
                resolved_in_model[k] = res.sql
                resolved_in_model[meas] = res.sql
                resolved_in_model[f"[{k}]"] = res.sql
                resolved_in_model[f"[{meas}]"] = res.sql

    for table, meas, dax in measures:
        k = meas.upper()
        sql = resolved_in_model.get(k)
        
        # Diagnose root cause if failed
        bucket = None
        detail = ""
        
        if sql:
            bucket = "SUCCESS"
            detail = sql
        else:
            # Check specific failure patterns
            if re.search(r"(--|//|/\*)", dax):
                bucket = "DAX Comments (-- or //)"
                detail = "DAX contains comments that were tokenized as operators or corrupt AST"
            elif re.search(r"\bVAR\b", dax, re.I) and re.search(r"\bRETURN\b", dax, re.I):
                bucket = "VAR ... RETURN Unhandled (VarNode)"
                detail = "VarNode not handled in DaxSqlRenderer._render_node"
            elif re.search(r"\b(TRUE|FALSE)\s*\(\s*\)", dax, re.I):
                bucket = "TRUE() / FALSE() Lexer / Literal Error"
                detail = "TRUE()/FALSE() treated as number instead of boolean/function"
            elif "KEEPFILTERS" in dax.upper():
                bucket = "KEEPFILTERS() in FILTER()"
                detail = "KEEPFILTERS wrapper inside FILTER() not unwrapped"
            elif re.search(r"\b(SUMX|COUNTAX|AVERAGEX|MINX|MAXX)\b", dax, re.I):
                bucket = "SUMX / COUNTAX / AVERAGEX Aggregation"
                detail = "Iterator functions not handled as conditional CASE aggregations"
            elif re.search(r"\b(SELECTEDVALUE|VALUE|MROUND)\b", dax, re.I):
                bucket = "SELECTEDVALUE / VALUE / MROUND Missing"
                detail = "Function missing from AST renderer (SELECTEDVALUE, VALUE, MROUND)"
            elif re.search(r"\bALL\s*\(", dax, re.I):
                bucket = "ALL() Windowing / Non-Scalar In Semantic View"
                detail = "ALL() requires whole-table partition/window not allowed in METRICS clause"
            elif re.search(r"\[([^\]]+)\]", dax):
                # Check if it references other measures
                refs = re.findall(r"\[([^\]]+)\]", dax)
                unresolved_refs = [r for r in refs if r.upper() not in resolved_in_model]
                if unresolved_refs:
                    bucket = "Cascading Dependent Measure (Parent NULL)"
                    detail = f"Depends on unresolvable measure(s): {', '.join(unresolved_refs)}"
                else:
                    bucket = "Virtual Table Alias / Column Discard"
                    detail = "Table alias discarded or column not found on container table"
            else:
                bucket = "Other Syntax / Complex DAX"
                detail = "Complex DAX structure"
                
            failure_buckets[bucket].append({
                "model": model_name,
                "table": table,
                "measure": meas,
                "dax": dax,
                "detail": detail
            })
            
        model_results.append({
            "table": table,
            "measure": meas,
            "dax": dax,
            "status": "SUCCESS" if sql else "FAILED",
            "bucket": bucket,
            "detail": detail
        })
        
    results[model_name] = model_results

# Summary
print("================ MODEL BREAKDOWN ================")
for model_name, m_list in results.items():
    succ = sum(1 for x in m_list if x["status"] == "SUCCESS")
    tot = len(m_list)
    pct = (succ / tot * 100) if tot else 0
    print(f"{model_name:30s}: {succ:2d}/{tot:2d} ({pct:5.1f}%) SUCCESS")

print("\n================ FAILURE ROOT CAUSES ================")
for b_name, b_list in failure_buckets.items():
    print(f"\n### {b_name} ({len(b_list)} measures)")
    for item in b_list[:3]:  # Top 3 examples per bucket
        print(f"  * [{item['model']}] {item['table']}[{item['measure']}]")
        print(f"    DAX: {repr(item['dax'][:95])}")
        print(f"    Issue: {item['detail']}")

# Save full results to JSON for artifact report
with open(r"Tests\test_measures\full_audit_results.json", "w", encoding="utf-8") as f:
    json.dump({"models": results, "buckets": failure_buckets}, f, indent=2)
print("\nSaved full audit to Tests/test_measures/full_audit_results.json")
