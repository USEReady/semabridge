import openpyxl
import re
from collections import Counter

def read_report(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        def is_header(r):
            cells = [str(c or "").strip().lower() for c in (r or []) if c is not None and len(str(c)) < 60]
            has = lambda w, excl=(): [i for i, c in enumerate(cells) if w in c and not any(x in c for x in excl)]
            m, x, ms = has("model"), has("expression", ("no expression",)), has("measure")
            return bool(m and x and ms and len({m[0], x[0], ms[-1]}) == 3)
        hdr_i = next((i for i, r in enumerate(rows[:15]) if is_header(r)), None)
        if hdr_i is not None:
            hdr = [str(c or "").strip().lower() for c in rows[hdr_i]]
            def col(*words):
                return next((i for i, h in enumerate(hdr) if all(w in h for w in words)), None)
            cm = col("model")
            cx = next((i for i, h in enumerate(hdr) if "expression" in h and "no expression" not in h), None)
            cmeas = next((i for i, h in enumerate(hdr) if "measure" in h and "model" not in h and "uses" not in h and "total" not in h), None)
            ct = next((i for i, h in enumerate(hdr) if h == "table" or h.startswith("table")), None)
            if cm is not None and cx is not None and cmeas is not None:
                for r in rows[hdr_i + 1:]:
                    if not r or not r[cm] or not r[cmeas]:
                        continue
                    meas, table = str(r[cmeas]).strip(), (str(r[ct]).strip() if ct is not None and r[ct] else "")
                    m = re.match(r"^'?(.+?)'?\[(.+)\]$", meas)
                    if m:
                        table, meas = m.group(1).replace("''", "'"), m.group(2)
                    out.append({"model": str(r[cm]).strip(), "table": table, "measure": meas,
                                "expr": str(r[cx] or "").strip(), "sheet": ws.title})
        else:
            current_model = None
            for r in rows:
                c0 = str(r[0]).strip() if r and r[0] is not None else ""
                c1 = str(r[1]).strip() if r and len(r) > 1 and r[1] is not None else ""
                if not c0 and not c1:
                    continue
                if "measures report" in c0.lower() or c0.lower().startswith("measure ("):
                    continue
                m_sect = re.search(r"^(.*?)\s*[\W_]+\s*(\d+)\s*measures\b", c0, re.I)
                if m_sect and not c1:
                    current_model = m_sect.group(1).strip()
                    continue
                if current_model and c0:
                    table, meas = "", c0
                    m = re.match(r"^'?(.+?)'?\[(.+)\]$", c0)
                    if m:
                        table, meas = m.group(1).replace("''", "'"), m.group(2)
                    out.append({
                        "model": current_model,
                        "table": table,
                        "measure": meas,
                        "expr": c1,
                        "sheet": ws.title
                    })

    seen, uniq = set(), []
    for x in out:
        k = (re.sub(r"[^a-z0-9]", "", str(x["model"] or "").lower()), x["measure"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq

if __name__ == "__main__":
    path = r"C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise.xlsx"
    measures = read_report(path)
    print(f"Total measures extracted: {len(measures)}")
    by_model = Counter(x["model"] for x in measures)
    for model, count in by_model.items():
        print(f"  {model}: {count} measures")
