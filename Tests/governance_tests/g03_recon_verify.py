"""G03 - Reconciliation verifier: compare DAX vs SQL result sets correctly (POST /api/reconciliation/verify).

    uv run python governance_tests/g03_recon_verify.py

Pure logic test with crafted rows (no Power BI / Snowflake needed). Each case states the CORRECT
expected status; a FAIL means the verifier would certify wrong numbers or reject right ones.

  V1  identical rows                                   -> CERTIFIED
  V2  delta within absolute tolerance (0.005)          -> CERTIFIED
  V3  delta within relative tolerance on big numbers   -> CERTIFIED
  V4  delta just above both tolerances                 -> FAILED
  V5  row missing on the SQL side                      -> FAILED
  V6  extra row on the SQL side                        -> FAILED
  V7  value NULL on one side, number on the other      -> FAILED
  V8  value column named differently from the metric   -> must NOT certify (values not found)
  V9  both sides empty (0 rows)                         -> must NOT certify
  V10 NaN vs 0                                          -> must NOT certify
  V11 duplicate dimension key on one side              -> must NOT silently keep the last row
  V12 key type 2024 vs '2024' / 2024.0                  -> same key
  V13 key whitespace / case ('North ' vs 'north')       -> reported, not certified blindly
  V14 numbers as text '1234.5' and '1,234.5'            -> parsed or flagged, never silently NULL
  V15 multi-column dimension keys                       -> matched on all keys
  V16 table-qualified dimension key 'T.col'             -> uses 'col' in the rows
  V17 single total row (no dimensions)                  -> compared
  V18 negative values and zero                          -> tolerance applied symmetrically
  V19 custom tolerance + max_mismatch_percentage        -> WARNING when within allowed mismatch %
  V20 negative tolerance in request                     -> rejected (400/422)
  V21 counts: total = matched + mismatched; badge consistent with status
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Api, Report, base_args, body_text  # noqa: E402

a = base_args(__doc__).parse_args()
api = Api(a.base, a.token)
rep = Report("G03 reconciliation verify")
M = "Test Metric"


def rows(dim, pairs, value_key=M):
    return [{dim: k, value_key: v} for k, v in pairs]


def verify(dax, sql, keys, tol=None, metric=M):
    body = {"metric_name": metric, "dax_results": dax, "sql_results": sql, "dimension_keys": keys}
    if tol:
        body["tolerance"] = tol
    return api.post("/api/reconciliation/verify", body)


def case(code, desc, dax, sql, keys, want, tol=None, not_status=None, metric=M):
    r = verify(dax, sql, keys, tol, metric)
    if not r.ok:
        rep.fail(f"{code} {desc}", f"HTTP {r.status_code}: {body_text(r)[:150]}")
        return None
    j = r.json()
    if not_status:
        rep.check(j["status"] != not_status, f"{code} {desc}", f"got {j['status']} ({j['certification_badge']})")
    else:
        rep.check(j["status"] == want, f"{code} {desc}", f"got {j['status']} ({j['certification_badge']})")
    tot_ok = j["total_rows_compared"] == j["matched_rows"] + j["mismatched_rows"]
    if not tot_ok:
        rep.fail(f"{code} counts add up", f"{j['total_rows_compared']} != {j['matched_rows']} + {j['mismatched_rows']}")
    return j


base = [("A", 100.0), ("B", 200.0), ("C", 300.0)]
case("V1", "identical rows -> CERTIFIED", rows("Region", base), rows("Region", base), ["Region"], "CERTIFIED")
case("V2", "abs delta 0.005 -> CERTIFIED", rows("Region", base), rows("Region", [("A", 100.005), ("B", 200), ("C", 300)]),
     ["Region"], "CERTIFIED")
case("V3", "relative delta 0.005% on 1e8 -> CERTIFIED", rows("R", [("A", 1e8)]), rows("R", [("A", 1e8 + 5000)]), ["R"], "CERTIFIED")
case("V4", "delta above both tolerances -> FAILED", rows("R", [("A", 100.0)]), rows("R", [("A", 100.05)]), ["R"], "FAILED")
case("V5", "row missing on SQL side -> FAILED", rows("R", base), rows("R", base[:2]), ["R"], "FAILED")
case("V6", "extra row on SQL side -> FAILED", rows("R", base), rows("R", base + [("D", 1.0)]), ["R"], "FAILED")
case("V7", "NULL vs number -> FAILED", rows("R", [("A", None)]), rows("R", [("A", 5.0)]), ["R"], "FAILED")
case("V8", "values under another column name -> not CERTIFIED",
     rows("R", base, value_key="metric_value"), rows("R", [("A", 1), ("B", 2), ("C", 3)], value_key="metric_value"),
     ["R"], None, not_status="CERTIFIED")
case("V9", "both sides empty -> not CERTIFIED", [], [], ["R"], None, not_status="CERTIFIED")
case("V10", "'NaN' vs 0 -> not CERTIFIED", rows("R", [("A", "NaN")]), rows("R", [("A", 0.0)]), ["R"], None, not_status="CERTIFIED")
case("V11", "duplicate key on DAX side (100 and 999) vs SQL 999 -> not CERTIFIED",
     rows("R", [("A", 100.0), ("A", 999.0)]), rows("R", [("A", 999.0)]), ["R"], None, not_status="CERTIFIED")
case("V12", "key 2024 vs '2024' -> same key", rows("Y", [(2024, 5.0)]), rows("Y", [("2024", 5.0)]), ["Y"], "CERTIFIED")
case("V12", "key 2024 vs 2024.0 -> same key", rows("Y", [(2024, 5.0)]), rows("Y", [(2024.0, 5.0)]), ["Y"], "CERTIFIED")
case("V13", "'North ' vs 'north' -> not CERTIFIED blindly", rows("R", [("North ", 5.0)]), rows("R", [("north", 5.0)]),
         ["R"], None, not_status="CERTIFIED")
case("V14", "'1234.5' text vs 1234.5 -> CERTIFIED", rows("R", [("A", "1234.5")]), rows("R", [("A", 1234.5)]), ["R"], "CERTIFIED")
case("V14", "'1,234.5' text vs 1234.5 -> not silently NULL==NULL", rows("R", [("A", "1,234.5")]), rows("R", [("A", "1,234.5")]),
     ["R"], None, not_status="CERTIFIED")
case("V15", "two-column key, swapped second key -> FAILED",
     [{"R": "A", "Y": 2023, M: 1.0}, {"R": "A", "Y": 2024, M: 2.0}],
     [{"R": "A", "Y": 2023, M: 2.0}, {"R": "A", "Y": 2024, M: 1.0}], ["R", "Y"], "FAILED")
case("V16", "dimension key 'T.Region' uses 'Region' column", rows("Region", base), rows("Region", base), ["T.Region"], "CERTIFIED")
case("V17", "single total row, no dimensions", [{M: 600.0}], [{M: 600.0}], [], "CERTIFIED")
case("V17", "single total row, different", [{M: 600.0}], [{M: 601.0}], [], "FAILED")
case("V18", "negative vs negative within tolerance", rows("R", [("A", -100.0)]), rows("R", [("A", -100.004)]), ["R"], "CERTIFIED")
case("V18", "zero vs tiny (0 vs 0.02) -> FAILED", rows("R", [("A", 0.0)]), rows("R", [("A", 0.02)]), ["R"], "FAILED")
case("V18", "sign flip 5 vs -5 -> FAILED", rows("R", [("A", 5.0)]), rows("R", [("A", -5.0)]), ["R"], "FAILED")
ten = [(f"K{i}", float(i)) for i in range(10)]
case("V19", "1 of 10 rows off, max_mismatch 20% -> WARNING", rows("R", ten),
     rows("R", ten[:9] + [("K9", 999.0)]), ["R"], "WARNING",
     tol={"absolute_tolerance": 0.01, "relative_tolerance": 0.0001, "max_mismatch_percentage": 20})
case("V19", "custom abs tolerance 1.0 -> CERTIFIED", rows("R", [("A", 10.0)]), rows("R", [("A", 10.9)]), ["R"], "CERTIFIED",
     tol={"absolute_tolerance": 1.0, "relative_tolerance": 0.0})
neg = verify(rows("R", [("A", 10.0)]), rows("R", [("A", 99.0)]), ["R"], {"absolute_tolerance": -1, "relative_tolerance": -1})
rep.check(neg.status_code in (400, 422), "V20 negative tolerance rejected", f"HTTP {neg.status_code}")
# V21 badge vs status
jj = verify(rows("R", base), rows("R", base), ["R"]).json()
rep.check(("Certified" in jj["certification_badge"]) == (jj["status"] == "CERTIFIED"), "V21 badge consistent with status")
sys.exit(rep.done())
