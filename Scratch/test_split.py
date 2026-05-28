import re

def original_split(expr):
    marker = " WITH SYNONYMS = ("
    idx = str(expr or "").upper().rfind(marker)
    if idx < 0:
        return expr, ""
    return expr[:idx].rstrip(), expr[idx:]

expr = "SUM(\"TOTAL_VANARSDEL_UNITS\") OVER (PARTITION BY YEAR(COL_DATE.\"DATE_DIM_CK\") ORDER BY COL_DATE.\"DATE_DIM_CK\") WITH SYNONYMS = ('Total Van Arsdel Units Ytd', 'Sum Van Arsdel Units Ytd', 'Aggregate Van Arsdel Units Ytd')"

print("Expr:", expr)
body, syn = original_split(expr)
print("Split body:", body)
print("Split syn:", syn)
