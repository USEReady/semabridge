import re

def _repair_bare_aggregate_identifiers(metric_sql: str) -> str:
    agg_pattern = re.compile(r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)', re.IGNORECASE)
    
    def _replace(match: re.Match) -> str:
        agg_fn = match.group(1).upper()
        ident = match.group(2)
        return f'{agg_fn}(SALESFACT."{ident}")'

    return agg_pattern.sub(_replace, metric_sql)

print(_repair_bare_aggregate_identifiers('SUM("MANUFACTURER_MFGISVANARSDEL")'))
print(_repair_bare_aggregate_identifiers('SUM("SENTIMENT_SCORE")'))
