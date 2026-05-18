import re

def check_var(expr):
    match = re.search(r'\bVAR\b', expr.upper())
    print(f"Expr: {expr} -> Match: {bool(match)}")

check_var("VAR")
check_var('"VAR"')
check_var("VAR_NAME")
check_var("my VAR")
check_var("SUM(VAR)")
