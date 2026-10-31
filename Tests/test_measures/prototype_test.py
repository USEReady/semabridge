import sys
sys.path.insert(0, ".")

# Let's inspect all 22 AI measures
import openpyxl, re

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

for i, (t, m, d) in enumerate(ai_measures, 1):
    print(f"{i}. {t}[{m}]")
