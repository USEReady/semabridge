import sys, os, re
import pytest
sys.path.insert(0, ".")

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.converter.dax_translator import DAXTranslator

_report_path = os.environ.get(
    "SEMABRIDGE_TEST_MEASURES_XLSX",
    "",
)
if not os.path.isfile(_report_path):
    pytest.skip(f"Measures Excel report not found at {_report_path}", allow_module_level=True)
openpyxl = pytest.importorskip("openpyxl")
wb = openpyxl.load_workbook(_report_path, read_only=True, data_only=True)
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

translator = DAXTranslator()

for idx, (table, meas, dax) in enumerate(ai_measures, 1):
    print(f"\n==================================================")
    print(f"[{idx}] {table}[{meas}]")
    print(f"DAX: {repr(dax[:100])}")
    
    # 1. AST Parser
    ast_res = None
    ast_err = None
    try:
        p = DaxAstParser()
        tree = p.parse(dax)
        r = DaxSqlRenderer(table_alias=table.upper() if table else "T")
        ast_res = r.render(tree)
    except Exception as e:
        ast_err = str(e)

    # 2. DAXTranslator
    dt_res = None
    dt_err = None
    try:
        res = translator.translate(dax, table, table)
        dt_res = (res.sql, res.tier, res.is_success)
    except Exception as e:
        dt_err = str(e)
        
    print(f"AST: res={ast_res} | err={ast_err}")
    print(f"DAXTranslator: {dt_res} | err={dt_err}")
