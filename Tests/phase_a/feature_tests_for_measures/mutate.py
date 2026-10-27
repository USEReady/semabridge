"""
mutate.py - "no hardcoding" proof. Renames every table and column of the synthetic fixture (and rewrites
every DAX case to match) with names chosen from a seed. The data and expected values stay the same, so a
generic implementation passes exactly the same cases; code that only works for particular names fails.

Names deliberately include spaces, digits and special characters (%, $, (, ), &, -).
"""
import random
import re

TABLE_POOL = {
    "Orders":   ["Fct Sales", "Transactions Log", "Order Lines 2", "Billing & Usage"],
    "Product":  ["Dim Item", "Catalogue", "SKU Master", "Item-List"],
    "Measures": ["Calc Measures", "_Metrics", "KPI Box", "Key Figures"],
    "Param":    ["What If Pct", "Slider Values", "Scenario %", "Adj Param"],
}
COLUMN_POOL = {
    "OrderID": ["Order Key", "Line No", "Txn Id"], "ProductID": ["Item Key", "SKU Id", "Prod No"],
    "Status": ["Order State", "Stage Flag", "Lifecycle"], "Qty": ["Units", "Quantity Sold", "Pcs"],
    "Price": ["List Price", "Unit Rate", "Tariff"], "Amount": ["Net Amt ($)", "Revenue USD", "Line Total"],
    "Unit Price ($)": ["Cost per Unit (%)", "Rate & Fee", "Price-Each ($)"],
    "Order Date": ["Placed On", "Txn Date", "Booked"], "Ship Date": ["Shipped On", "Dispatch Date", "Delivered"],
    "Is Priority": ["Rush Flag", "Is Urgent", "Express?"], "Step": ["Pipeline Stage", "Phase Code", "Funnel Step"],
    "Name": ["Item Name", "Title", "Label Text"], "Category": ["Item Group", "Segment", "Family"],
    "ID": ["Calc Id", "Row Key", "Slot"], "Value": ["Pct Value", "Adj Amount", "Level"],
}


def plan(seed):
    rnd = random.Random(seed)
    suffix = lambda: f" {rnd.choice('ABCDEFGHJKMNPQRSTUVWXYZ')}{rnd.randint(1, 9)}"
    tables = {k: rnd.choice(v) + suffix() for k, v in TABLE_POOL.items()}
    cols = {k: rnd.choice(v) + suffix() for k, v in COLUMN_POOL.items()}
    return tables, cols


def _quote(name):
    return "'" + name.replace("'", "''") + "'"


def rewrite_dax(dax, tables, cols, measure_names):
    out = dax
    for old, new in tables.items():                       # 'Old'[ / 'Old' / Old[ / Old (bare table argument)
        out = re.sub(rf"'{re.escape(old)}'", _quote(new), out)
        out = re.sub(rf"(?<![\w'\[\]]){re.escape(old)}(?![\w'\]])", _quote(new), out)
    def col(m):
        inner = m.group(1)
        return m.group(0) if inner in measure_names else "[" + cols.get(inner, inner).replace("]", "]]") + "]"
    return re.sub(r"\[([^\]]+)\]", col, out)


def apply(seed, TABLES, FACT, RELATIONSHIPS, CASES, BASE):
    tables, cols = plan(seed)
    new_tables = {tables[t]: df.rename(columns=cols) for t, df in TABLES.items()}
    new_rel = [(tables[a], cols.get(b, b), tables[c], cols.get(d, d)) for a, b, c, d in RELATIONSHIPS]
    measure_names = set(BASE) | {n for c in CASES for n in (c.get("context") or {})}
    new_cases = []
    for c in CASES:
        n = dict(c)
        n["dax"] = rewrite_dax(c["dax"], tables, cols, measure_names)
        n["home"] = tables[c["home"]]
        if c.get("context"):
            n["context"] = {k: (tables[h], rewrite_dax(d, tables, cols, measure_names)) for k, (h, d) in c["context"].items()}
        n.pop("expected_sql", None)
        new_cases.append(n)
    new_base = {k: (tables[h], rewrite_dax(d, tables, cols, measure_names)) for k, (h, d) in BASE.items()}
    return new_tables, tables[FACT], new_rel, new_cases, new_base, tables["Measures"], {**tables, **cols}
