import re
sql = "TO_DOUBLE(CAST(REP_SFDC_TASK.DELTA_B_W_KPI_ORIGDATE_AND_LASTMODDATE AS FLOAT))"
out = re.sub(
    r'\bTO_DOUBLE\s*\(\s*CAST\s*\(\s*(.+?)\s+AS\s+(?:FLOAT|DOUBLE)\s*\)\s*\)',
    r'TRY_CAST(\1 AS DOUBLE)',
    sql,
    flags=re.IGNORECASE,
)
print("OUT:", out)

# But wait, does DAX translator generate `TO_DOUBLE` or does sqlglot generate it?
