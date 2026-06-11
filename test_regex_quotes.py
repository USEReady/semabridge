import re
_DATE_FUNC_QUALIFIED_RE = re.compile(
    r'\b(DATE_TRUNC|DATEADD|DATEDIFF|DATE_PART)\s*\(\s*[\'"]?\s*(?:"?[A-Za-z_][A-Za-z0-9_]*"?\s*\.\s*)?'
    r'"?(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)"?\s*[\'"]?\s*,',
    re.IGNORECASE,
)

def _repl_qualified(m: re.Match) -> str:
    fn = m.group(1)
    part = m.group(2).lower()
    return f"{fn}('{part}',"

test_sqls = [
    "DATE_TRUNC('COL_DATE.\"YEAR\"', MAX_DATE)",
    "DATE_TRUNC('\"COL_DATE\".\"YEAR\"', MAX_DATE)",
    "DATE_TRUNC(\"COL_DATE.YEAR\", MAX_DATE)",
    "DATE_TRUNC('COL_DATE.YEAR', MAX_DATE)",
]

for sql in test_sqls:
    out = _DATE_FUNC_QUALIFIED_RE.sub(_repl_qualified, sql)
    print(f"IN:  {sql}\nOUT: {out}\n")
