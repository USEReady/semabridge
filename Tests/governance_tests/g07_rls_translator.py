"""G07 - RLS policy translator, both directions (POST /api/security/rls/tmdl-to-snowflake, /snowflake-to-tmdl).

    uv run python governance_tests/g07_rls_translator.py

Security translations must be exact or refused; a "translated" policy that filters differently is worse
than no policy. Every case states the safe expected outcome.

  R1  [Col] = "Value"                         -> translated, COL = 'Value'
  R2  'Table Name'[Col] == "x"                -> translated; table with spaces quoted in DDL
  R3  dynamic RLS (USERPRINCIPALNAME/USERNAME/LOOKUPVALUE/RELATED) -> 'not translated' + reason
  R4  [A] = "x" && [B] = "y"                  -> both conditions kept, or refused (never one silently)
  R5  [A] = "x" || [B] = "y"                  -> kept or refused
  R6  [Col] IN {"a","b"}                      -> kept or refused
  R7  value with a quote: [Col] = "O'Brien"   -> escaped ('O''Brien')
  R8  injection: [Col] = 'x' OR 1=1 / '; DROP ... -> refused
  R9  numeric comparison [Year] >= 2020       -> column type not forced to VARCHAR (or refused)
  R10 two permissions on the same table / two roles on one table -> one policy per table (Snowflake allows one)
  R11 users NOT in the role                   -> policy must not hide all rows from every other role unexpectedly
                                                 (reported: CURRENT_ROLE() equality, no role hierarchy)
  R12 role name with spaces/quotes            -> valid identifier, no injection
  R13 validation: empty role name / no permissions / no target database -> 400
  R14 reverse: simple policy -> TMDL filter; unparseable policy body -> refused, not an invented USERNAME() filter
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("G07 RLS translator")


def t2s(filters, role="Test Role", db="TESTDB", schema="TESTSCH"):
    perms = [{"table_name": t, "dax_filter_expression": f} for t, f in filters]
    body = {"role": {"name": role, "table_permissions": perms}, "target_schema": schema}
    if db is not None:
        body["target_database"] = db
    return api.post("/api/security/rls/tmdl-to-snowflake", body)


def ddl_of(r):
    j = r.json() if r.ok else {}
    return j.get("status"), j.get("ddl") or "", j.get("reason") or ""


def cond(ddl):
    """The row filter inside the policy body: text after 'AND ' up to ');'."""
    m = re.search(r"AND (.+?)\);", ddl, re.S)
    return m.group(1) if m else ""

st, ddl, _ = ddl_of(t2s([("Sales", '[Region] = "East"')]))
rep.check(st == "translated" and "REGION = 'East'" in ddl, "R1 simple equality translated", ddl[:200])
st, ddl, _ = ddl_of(t2s([("Sales Territory", "'Sales Territory'[Region] == \"x\"")]))
rep.check(st == "translated", "R2 '==' and quoted table accepted")
rep.check('"SALES TERRITORY"' in ddl or "SALES_TERRITORY" in ddl, "R2 table name with a space is a valid identifier",
          re.findall(r"ALTER TABLE ([^\n]+)", ddl)[:1])
for fn in ('[Email] = USERPRINCIPALNAME()', '[Owner] = USERNAME()',
           '[Region] = LOOKUPVALUE(Map[Region], Map[Email], USERPRINCIPALNAME())', 'RELATED(Region[Name]) = "East"'):
    st, ddl, reason = ddl_of(t2s([("Sales", fn)]))
    rep.check(st == "not translated" and not ddl and reason, f"R3 dynamic RLS refused: {fn[:40]}", f"{st} {reason[:80]}")
for code, f, parts in (("R4", '[A] = "x" && [B] = "y"', ("'x'", "'y'")), ("R5", '[A] = "x" || [B] = "y"', ("'x'", "'y'")),
                       ("R6", '[A] IN {"a","b"}', ("'a'", "'b'"))):
    st, ddl, reason = ddl_of(t2s([("Sales", f)]))
    exact = st == "translated" and all(p in ddl for p in parts) and '"' not in ddl.split("RETURNS BOOLEAN")[-1].split(";")[0].replace('""', "")
    rep.check(st == "not translated" or exact, f"{code} '{f}' kept exactly or refused",
              f"{st}: {cond(ddl) or reason[:80]}")
st, ddl, _ = ddl_of(t2s([("Sales", '[Name] = "O\'Brien"')]))
rep.check(st == "translated" and "'O''Brien'" in ddl, "R7 quote in value escaped", re.findall(r"NAME = [^)]+", ddl)[:1])
for inj in ("[Region] = 'x' OR 1=1", "[Region] = 'x'); DROP TABLE T; --", '[Region] = "x" OR TRUE'):
    st, ddl, _ = ddl_of(t2s([("Sales", inj)]))
    rep.check(st != "translated" or ("DROP TABLE" not in ddl and "OR 1=1" not in ddl and "OR TRUE" not in ddl),
              f"R8 injection refused: {inj}", f"{st}: {cond(ddl)}")
st, ddl, _ = ddl_of(t2s([("Sales", "[Year] >= 2020")]))
rep.check(st != "translated" or "YEAR VARCHAR" not in ddl, "R9 numeric column not declared VARCHAR",
          "string comparison: '2100' < '300' etc.")
st, ddl, _ = ddl_of(t2s([("Sales", '[Region] = "East"'), ("Sales", '[Country] = "US"')]))
n_alter = len(re.findall(r"ALTER TABLE \S+\s+ADD ROW ACCESS POLICY", ddl))
rep.check(st != "translated" or n_alter <= 1, "R10 one row access policy per table",
          f"{n_alter} ALTER TABLE ... ADD ROW ACCESS POLICY on the same table (Snowflake allows only one)")
st, ddl, _ = ddl_of(t2s([("Sales", '[Region] = "East"')]))
if "CURRENT_ROLE() =" in ddl:
    rep.warn("R11 policy uses CURRENT_ROLE() = '<role>'", "every other non-admin role sees 0 rows; role hierarchy and "
             "secondary roles ignored (IS_ROLE_IN_SESSION / mapping table needed)")
st, ddl, _ = ddl_of(t2s([("Sales", '[Region] = "East"')], role="Ops' Team"))
rep.check(st != "translated" or "'OPS'_TEAM'" not in ddl, "R12 role name with a quote cannot break the DDL",
          re.findall(r"CURRENT_ROLE\(\) = [^ ]+", ddl)[:1])
rep.check(t2s([("Sales", '[A] = "x"')], role=" ").status_code == 400, "R13 empty role name -> 400")
rep.check(t2s([]).status_code == 400, "R13 no permissions -> 400")
nodb = t2s([("Sales", '[A] = "x"')], db=None)
rep.check(nodb.status_code in (200, 400), "R13 no target database -> 400 or configured default",
          f"{nodb.status_code} {body_text(nodb)[:80]}")
# R14 reverse
rv = api.post("/api/security/rls/snowflake-to-tmdl", {"policy": {
    "policy_name": "RAP_X", "target_table": "Sales", "column_name": "REGION",
    "body_expression": "CURRENT_ROLE() IN ('ACCOUNTADMIN') OR (CURRENT_ROLE() = 'R1' AND REGION = 'East')",
    "assigned_roles": ["R1"]}})
tm = rv.json().get("tmdl_content", "") if rv.ok else ""
rep.check('[REGION] = "East"' in tm, "R14 simple policy -> TMDL filter", tm[:160])
rv2 = api.post("/api/security/rls/snowflake-to-tmdl", {"policy": {
    "policy_name": "RAP_Y", "target_table": "Sales", "column_name": "REGION",
    "body_expression": "EXISTS (SELECT 1 FROM ENTITLEMENTS e WHERE e.ROLE = CURRENT_ROLE() AND e.REGION = REGION)",
    "assigned_roles": ["R1"]}})
tm2 = rv2.json().get("tmdl_content", "") if rv2.ok else body_text(rv2)
rep.check("USERNAME()" not in tm2, "R14 unparseable policy refused, not replaced by an invented [REGION] = USERNAME()", tm2[:160])
sys.exit(rep.done())
