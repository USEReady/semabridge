import re

_DATE_TRUNC_RE = re.compile(
    r"\bDATE_TRUNC\s*\(\s*['\"]*(YEAR|MONTH|DAY|WEEK|QUARTER|HOUR|MINUTE|SECOND)['\"]* *,",
    re.IGNORECASE,
)

def _repl(fn_name: str):
    def _inner(m: re.Match) -> str:
        part = m.group(1).strip("\"'").lower()
        return f"{fn_name}('{part}',"
    return _inner

sql = "DATE_TRUNC('\"YEAR\"', MAX_DATE)"
out = _DATE_TRUNC_RE.sub(_repl("DATE_TRUNC"), sql)
print(f"IN:  {sql}\nOUT: {out}")
