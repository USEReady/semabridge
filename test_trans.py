import re

def _qualify_bare_column_identifiers(metric_sql: str) -> str:
    metric_names = {"Metric1"}
    keywords = {"AND", "OR", "AS", "IN", "IS", "NOT", "NULL", "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET", "CAST", "TRY_CAST", "TRY_TO_DATE", "TO_DATE", "TO_DOUBLE", "DOUBLE", "INT", "FLOAT", "VARCHAR", "DATE", "TIMESTAMP", "BOOLEAN", "TRUE", "FALSE"}
    dataset_aliases = {"SalesFact": "SALESFACT"}
    
    def _resolve_owner(ident: str):
        # mock it returning SalesFact
        return "SALESFACT", ident

    def _replace(match: re.Match) -> str:
        token = match.group(1)
        upper = token.upper()
        if upper in keywords:
            return match.group(0)
        if metric_names and upper in metric_names:
            return match.group(0)
        if token.endswith("("):
            return match.group(0)
        owner = _resolve_owner(token)
        if not owner:
            return match.group(0)
        alias, col = owner
        if not alias:
            return match.group(0)
        return f'{alias}."{col}"'

    pattern = re.compile(r'(?<![\w\.\'"])\b([A-Za-z_][A-Za-z0-9_$]*)\b(?![\w\.\'"])')
    return pattern.sub(_replace, metric_sql)

def _repair_bare_aggregate_identifiers(metric_sql: str, metric_name: str) -> str:
    agg_pattern = re.compile(r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)', re.IGNORECASE)
    
    def _replace(match: re.Match) -> str:
        agg_fn = match.group(1).upper()
        ident = match.group(2)
        return f'{agg_fn}(SALESFACT."{ident}")'

    return agg_pattern.sub(_replace, metric_sql)


def main():
    expr1 = 'SUM("MANUFACTURER_MFGISVANARSDEL")'
    out1 = _qualify_bare_column_identifiers(expr1)
    print("1:", out1)
    
    expr2 = 'SUM(MANUFACTURER_MFGISVANARSDEL)'
    out2 = _qualify_bare_column_identifiers(expr2)
    print("2:", out2)

    expr3 = 'SUM(MANUFACTURER_MFGISVANARSDEL)'
    out3 = _repair_bare_aggregate_identifiers(expr3, "Metric1")
    print("3:", out3)

if __name__ == "__main__":
    main()
