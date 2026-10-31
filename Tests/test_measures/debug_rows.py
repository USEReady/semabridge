import openpyxl

wb = openpyxl.load_workbook(r"C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise.xlsx", read_only=True, data_only=True)
ws = wb["Measures Report"]
for i, r in enumerate(ws.iter_rows(values_only=True)):
    if i in [0, 1, 2, 3, 4, 5, 6, 7]:
        print(f"Row {i}: repr(r[0])={repr(r[0])}, repr(r[1])={repr(r[1])}")
