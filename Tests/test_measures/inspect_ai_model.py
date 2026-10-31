import sys, os, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
sys.path.insert(0, "Tests/phase_a")
sys.path.insert(0, "Tests/test_measures")

from validate_semantic_view import _clause, _split_top
from relationship_inventory import sb_column
import openpyxl

wb = openpyxl.load_workbook(r"C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise.xlsx", read_only=True, data_only=True)
ws = wb["Measures Report"]
ai_measures = []
current_model = None
for r in ws.iter_rows(values_only=True):
    c0 = str(r[0]).strip() if r and r[0] is not None else ""
    c1 = str(r[1]).strip() if r and len(r) > 1 and r[1] is not None else ""
    m = re.search(r"^(.*?)\s*[\W_]+\s*(\d+)\s*measures\b", c0, re.I)
    if m:
        current_model = m.group(1).strip()
        continue
    if current_model == "Artificial Intelligence" and c0:
        table, meas = "", c0
        m_tm = re.match(r"^'?(.+?)'?\[(.+)\]$", c0)
        if m_tm:
            table, meas = m_tm.group(1).replace("''", "'"), m_tm.group(2)
        ai_measures.append((table, meas, c1))

# Read DDL metrics
ddl_path = r"output\debug\debug_generated_sql_Artificial_Intelligence_Sample.sql"
ddl_text = open(ddl_path, encoding="utf-8").read() if os.path.exists(ddl_path) else ""
ddl_items = {}
if ddl_text:
    for item in _split_top(_clause(ddl_text, "metrics")):
        m = re.match(r'^\s*("?[^".\s]+"?)\s*\.\s*("(?:[^"]|"")+"|[^\s]+)\s+as\s+(.*)$', item, re.I | re.S)
        if m:
            ddl_items[m.group(2).strip('"').upper()] = (m.group(1).strip('"'), m.group(3).strip())

# Read log
log_path = r"output\runs\proj-artificial-intelligence-sample\run-1792836396638\Artificial_Intelligence_Sample.log"
log_text = open(log_path, encoding="utf-8", errors="ignore").read() if os.path.exists(log_path) else ""

print(f"=== Artificial Intelligence Sample: {len(ai_measures)} Measures ===")
for tbl, meas, expr in ai_measures:
    k = sb_column(meas).upper().strip('"')
    d = ddl_items.get(k)
    
    # Check log for this measure
    log_hits = [line.strip() for line in log_text.splitlines() if f"'{meas}'" in line or f"'{k}'" in line]
    
    outcome = "MISSING (Not in DDL)"
    if d:
        if "CAST(NULL AS" in d[1].upper():
            outcome = "NULL_CAST"
        else:
            outcome = "REAL_SQL"
            
    print(f"\nMeasure: {tbl}[{meas}] -> Key: {k} | Outcome: {outcome}")
    print(f"  DAX: {repr(expr[:120])}")
    if d:
        print(f"  DDL: {d[0]}.{k} AS {d[1][:120]}")
    if log_hits:
        for lh in log_hits[:3]:
            print(f"  LOG: {lh[:140]}")
