import re
_DATE_PART_KEYWORDS = frozenset({"year", "month", "day", "week", "quarter", "hour", "minute", "second"})
_DATE_FUNC_QUALIFIED_RE = re.compile(
    r'\b(DATE_TRUNC|DATEADD|DATEDIFF|DATE_PART)\s*\(\s*(?:"?[A-Za-z_][A-Za-z0-9_]*"?\s*\.\s*)?'
    r'"?(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)"?\s*,',
    re.IGNORECASE,
)
_DATE_PART_RE = re.compile(
    r"\bDATE_PART\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)
_DATE_TRUNC_RE = re.compile(
    r"\bDATE_TRUNC\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)
_DATEADD_RE = re.compile(
    r"\bDATEADD\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)
_DATEDIFF_RE = re.compile(
    r"\bDATEDIFF\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)
def _sanitize(sql: str) -> str:
    def _repl_qualified(m: re.Match) -> str:
        fn = m.group(1)
        part = m.group(2).lower()
        return f"{fn}('{part}',"
    sql = _DATE_FUNC_QUALIFIED_RE.sub(_repl_qualified, sql)

    def _repl(fn_name: str):
        def _inner(m: re.Match) -> str:
            part = m.group(1).strip("\"'").lower()
            return f"{fn_name}('{part}',"
        return _inner

    sql = _DATE_PART_RE.sub(_repl("DATE_PART"), sql)
    sql = _DATE_TRUNC_RE.sub(_repl("DATE_TRUNC"), sql)
    sql = _DATEADD_RE.sub(_repl("DATEADD"), sql)
    sql = _DATEDIFF_RE.sub(_repl("DATEDIFF"), sql)
    return sql

test_sqls = [
    'DATE_TRUNC("COL_DATE"."YEAR", MAX_DATE)',
    'DATE_TRUNC(COL_DATE."YEAR", MAX_DATE)',
    'DATE_TRUNC("COL_DATE".YEAR, MAX_DATE)',
    'DATE_TRUNC(COL_DATE.YEAR, MAX_DATE)',
    'DATE_TRUNC(\'COL_DATE."YEAR"\', MAX_DATE)',
    "DATE_TRUNC('COL_DATE.YEAR', MAX_DATE)",
    'DATE_TRUNC(COL_DATE."YEAR", "MAX_DATE")',
    'DATE_TRUNC( "COL_DATE" . "YEAR" , MAX_DATE )',
]

for sql in test_sqls:
    out = _sanitize(sql)
    print(f"IN:  {sql}\nOUT: {out}\n")
