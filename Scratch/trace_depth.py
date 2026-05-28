raw_expr = "LAG((SUM(\"TOTAL_VANARSDEL_UNITS\") OVER (PARTITION BY YEAR(COL_DATE.\"DATE_DIM_CK\") ORDER BY COL_DATE.\"DATE_DIM_CK\") WITH SYNONYMS = ('Total Van Arsdel Units Ytd', 'Sum Van Arsdel Units Ytd', 'Aggregate Van Arsdel Units Ytd')), 12) OVER (ORDER BY COL_DATE.\"DATE_DIM_CK\") WITH SYNONYMS = ('Total Van Arsdel Units Ytd Sply', 'Sum Van Arsdel Units Ytd Sply', 'Aggregate Van Arsdel Units Ytd Sply')"

print(f"Length of expr: {len(raw_expr)}")
depth = 0
in_single = False
idx = 0
length = len(raw_expr)
syn_starts = []

while idx < length:
    ch = raw_expr[idx]
    old_depth = depth
    
    if in_single:
        if ch == "'" and idx + 1 < length and raw_expr[idx + 1] == "'":
            idx += 2
            continue
        if ch == "'":
            in_single = False
    elif ch == "'":
        in_single = True
    elif ch == "(":
        depth += 1
    elif ch == ")":
        depth -= 1
        
    if depth == 0 and ch in ("W", "w"):
        import re
        tail = raw_expr[idx:]
        if re.match(r"(?i)WITH\s+SYNONYMS\s*=\s*\(", tail):
            syn_starts.append((idx, depth))
            
    # Print near parentheses or WITH
    if ch in ("(", ")", "W", "w") or idx > 250:
        print(f"Char at {idx:3d}: '{ch}' | Depth: {old_depth:2d} -> {depth:2d} | in_single: {in_single}")
        
    idx += 1

print("Detected synonym starts (idx, depth):", syn_starts)
