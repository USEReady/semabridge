"""Issue: SEX treated as reserved (COL_SEX) while START, really reserved, is not prefixed (breaks Cortex Analyst).
Expect: every logical name that is a Snowflake reserved keyword is prefixed; ordinary words are not.
Keyword list: Snowflake 'Reserved & limited keywords' documentation - re-check it if Snowflake adds keywords."""
from common import Result, cli
NAME = "T12 reserved words in names"
RESERVED = set("""ACCOUNT ALL ALTER AND ANY AS BETWEEN BY CASE CAST CHECK COLUMN CONNECT CONNECTION CONSTRAINT CREATE
CROSS CURRENT CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER DATABASE DELETE DISTINCT DROP ELSE EXISTS FALSE
FOLLOWING FOR FROM FULL GRANT GROUP GSCLUSTER HAVING ILIKE IN INCREMENT INNER INSERT INTERSECT INTO IS ISSUE JOIN
LATERAL LEFT LIKE LOCALTIME LOCALTIMESTAMP MINUS NATURAL NOT NULL OF ON OR ORDER ORGANIZATION QUALIFY REGEXP REVOKE
RIGHT RLIKE ROW ROWS SAMPLE SCHEMA SELECT SET SOME START TABLE TABLESAMPLE THEN TO TRIGGER TRUE TRY_CAST UNION UNIQUE
UPDATE USING VALUES VIEW WHEN WHENEVER WHERE WITH""".split())
# Prefixed by SemaBridge on purpose although not on the list (kept as a decision, reported as INFO)
ACCEPTED_EXTRA = {"DATE", "VALUE", "END", "INDEX", "TIMESTAMP", "TIME", "YEAR", "MONTH", "DAY"}
def check(sv, pbix, args):
    r = Result(NAME)
    names = [(d.table, d.name, "dimension") for d in sv.dims] + [(m.table, m.name, "metric") for m in sv.metrics]
    names += [(a, a, "table alias") for a in sv.tables]
    for t, n, kind in names:
        if n in RESERVED:
            r.fail(f"{kind} {t}.{n}: reserved keyword used as a name (prefix it, e.g. COL_{n})")
        elif n.startswith("COL_"):
            base = n[4:]
            if base in RESERVED: continue
            if base in ACCEPTED_EXTRA: r.info(f"{kind} {t}.{n}: prefixed by choice")
            else: r.warn(f"{kind} {t}.{n}: '{base}' is not reserved - prefix not needed")
    if not any(s in ("FAIL", "WARN") for s, _ in r.items): r.ok("names follow the reserved-word rule")
    return r
if __name__ == "__main__": cli(check, NAME)
