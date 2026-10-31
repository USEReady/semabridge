import openpyxl
import re

wb = openpyxl.load_workbook(r"C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise.xlsx", read_only=True, data_only=True)
ws = wb["Measures Report"]
rows = list(ws.iter_rows(values_only=True))

def is_header(r):
    cells = [str(c or "").strip().lower() for c in (r or []) if c is not None and len(str(c)) < 60]
    has = lambda w, excl=(): [i for i, c in enumerate(cells) if w in c and not any(x in c for x in excl)]
    m, x, ms = has("model"), has("expression", ("no expression",)), has("measure")
    return bool(m and x and ms and len({m[0], x[0], ms[-1]}) == 3)

hdr_i = next((i for i, r in enumerate(rows[:15]) if is_header(r)), None)
print("hdr_i:", hdr_i)

for i in range(10):
    r = rows[i]
    c0 = str(r[0]).strip() if r and r[0] is not None else ""
    c1 = str(r[1]).strip() if r and len(r) > 1 and r[1] is not None else ""
    m_sect = re.search(r"^(.*?)\s*[\u2022\ufffd\-\xb7|]\s*(\d+)\s*measures", c0, re.I)
    print(f"Row {i}: c0={c0!r}, c1={c1!r}, m_sect={m_sect}")
