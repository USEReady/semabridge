"""G06 - REAL reconciliation: deployed semantic view (Snowflake) vs Power BI values.

    uv run python governance_tests/g06_recon_real.py --view SEMABRIDGE.SCHEMA.VIEW_NAME \
        --expected governance_tests/fixtures/expected_values_example.json [--abs 0.01 --rel 0.0001]

The UI reconciliation screen compares built-in sample rows. This script uses real data:
  * SQL side : runs SEMANTIC_VIEW(<view> METRICS <m> DIMENSIONS <d>) through /api/ai/execute-query
  * DAX side : values exported from Power BI (DAX Studio / Performance Analyzer 'Copy query' + run,
               or a table visual exported to CSV) saved in the expected-values JSON
then sends both to /api/reconciliation/verify AND compares them independently here, so a verifier
bug cannot hide a mismatch.

expected-values JSON (list):
  [{"metric": "Case Count",                 # name used in the Power BI result rows
    "view_metric": "CASES.CASE_COUNT",      # logical name in the semantic view
    "view_dimensions": ["CASES.AGENT"],     # logical names in the semantic view ([] for a total)
    "pbi_dimension_columns": ["Agent"],     # matching column names in the Power BI rows
    "where": "",                            # optional semantic-view WHERE (e.g. CASE_CALENDAR.YEAR = 2024)
    "rows": [{"Agent": "Jane", "Case Count": 12}, ...]}]
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

p = base_args(__doc__)
p.add_argument("--view", required=True, help="fully qualified semantic view db.schema.view")
p.add_argument("--expected", required=True)
p.add_argument("--project")
p.add_argument("--abs", type=float, default=0.01)
p.add_argument("--rel", type=float, default=0.0001)
a = p.parse_args()
api = Api(a.base, a.token)
rep = Report("G06 real reconciliation")
cases = json.loads(Path(a.expected).read_text(encoding="utf-8"))
results = []


def num(v):
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", ""))
        return None if math.isnan(f) else f
    except ValueError:
        return None


def key(row, cols):
    return " | ".join(str(row.get(c)).strip() if row.get(c) is not None else "NULL" for c in cols)


for c in cases:
    metric, vm, vdims = c["metric"], c["view_metric"], c.get("view_dimensions", [])
    pcols = c.get("pbi_dimension_columns", [])
    sql = f"SELECT * FROM SEMANTIC_VIEW({a.view} METRICS {vm}" + (f" DIMENSIONS {', '.join(vdims)}" if vdims else "") \
        + (f" WHERE {c['where']}" if c.get("where") else "") + ")"
    e = api.post("/api/ai/execute-query", {"sql": sql, "target": "SNOWFLAKE", "project_id": a.project, "limit": 100000})
    ej = e.json() if e.ok else {}
    if not rep.check(e.ok and ej.get("success") and not ej.get("is_simulated"), f"{metric}: semantic view query runs",
                     (ej.get("error") or ej.get("message") or body_text(e))[:200]):
        continue
    sf_rows = ej.get("rows") or []
    if ej.get("columns") and sf_rows and isinstance(sf_rows[0], list):
        sf_rows = [dict(zip(ej["columns"], r)) for r in sf_rows]
    # map Snowflake output columns (upper-case logical names) to the Power BI names
    vm_col = vm.split(".")[-1].upper()
    sql_rows = []
    for r in sf_rows:
        up = {str(k).upper(): v for k, v in r.items()}
        row = {metric: up.get(vm_col)}
        for vd, pc in zip(vdims, pcols):
            row[pc] = up.get(vd.split(".")[-1].upper())
        sql_rows.append(row)
    dax_rows = c["rows"]
    rep.check(len(sf_rows) <= 100000, f"{metric}: result not truncated", f"{len(sf_rows)} rows")

    # independent comparison
    d_map, s_map = {}, {}
    dup = False
    for r in dax_rows:
        k = key(r, pcols)
        dup |= k in d_map
        d_map[k] = num(r.get(metric))
    for r in sql_rows:
        k = key(r, pcols)
        dup |= k in s_map
        s_map[k] = num(r.get(metric))
    mism = []
    for k in sorted(set(d_map) | set(s_map)):
        dv, sv = d_map.get(k), s_map.get(k)
        if dv is None and sv is None:
            continue
        if dv is None or sv is None or not (abs(dv - sv) <= a.abs or abs(dv - sv) / max(abs(dv), abs(sv), 1e-9) <= a.rel):
            mism.append((k, dv, sv))
    own = "MATCH" if not mism and d_map and not dup else "MISMATCH"
    rep.check(own == "MATCH", f"{metric}: values match ({len(d_map)} PBI rows vs {len(s_map)} view rows)",
              f"{len(mism)} mismatches, e.g. {mism[:3]}" + ("; duplicate keys" if dup else ""))

    v = api.post("/api/reconciliation/verify", {"metric_name": metric, "dax_results": dax_rows, "sql_results": sql_rows,
                                                "dimension_keys": pcols, "sql_query": sql,
                                                "tolerance": {"absolute_tolerance": a.abs, "relative_tolerance": a.rel}})
    vj = v.json() if v.ok else {}
    verdict = vj.get("status")
    rep.check((verdict == "CERTIFIED") == (own == "MATCH"), f"{metric}: verifier agrees with independent check",
              f"verifier={verdict}, independent={own}")
    results.append({"metric": metric, "sql": sql, "independent": own, "verifier": verdict, "mismatches": mism[:20]})

out = Path("governance_tests/out"); out.mkdir(parents=True, exist_ok=True)
(out / "recon_real_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
print(f"  saved {out / 'recon_real_results.json'}")
sys.exit(rep.done())
