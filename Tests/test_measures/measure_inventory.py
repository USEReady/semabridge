#!/usr/bin/env python3
"""
measure_inventory.py - Phase A for MEASURES (read-only, no SemaBridge changes).

For every Power BI measure it answers: did it reach Snowflake with real SQL, as a
CAST(NULL AS DOUBLE) placeholder, or not at all - and if not, at WHICH stage and WHY.

Sources it combines (all optional except the expected list and the DDL):
  expected   --report  your model-wise measures Excel (Model Name / Measure / Expression columns)
             or --pbix the .pbix itself
  DDL        generated semantic view (output\\debug\\debug_generated_sql_<model>.sql) - what was emitted
  log        the run log (output\\runs\\<project>\\<run>\\<model>.log) - SemaBridge's own reasons
  sml        output\\reverse\\<project>\\sml\\model.yaml - translated SQL / sync flag per metric
  translate  --translate re-runs SemaBridge's DAX translator on each measure in isolation
             (must run inside the SemaBridge repo / venv)

Outcome per measure
  REAL_SQL            in the semantic view with a real expression
  NULL_PLACEHOLDER    in the view as CAST(NULL AS ...)            -> reason + stage below
  MISSING             not in the view at all                         -> reason + stage below
Reason (from the log, then from dependencies / DAX pattern)
  UNREACHABLE         measure sits on a table with no relationships (measure-only table)
  NOT_TRANSLATABLE    translator tiers 1-4 failed and tier 5 (LLM) unavailable
  COLUMN_NOT_RESOLVED translated, then a column name could not be resolved (sanitiser / parser bug)
  VALIDATION_GATE     translated SQL rejected by the DAX validation gate
  CIRCULAR            circular measure dependency reported
  CASCADED            depends on another measure that is NULL / missing
  NO_EXPRESSION       empty measure in Power BI
  BY_DESIGN           text / formatting / slicer measure (cannot be a numeric metric)
  UNKNOWN             no evidence found - look at the log around this measure
SIMPLE_BUT_NULL = yes  -> a simple DAX pattern (SUM, AVERAGE, simple CALCULATE, arithmetic) that still
                          failed: these point at translator bugs, check them first.

Usage
  # one model
  python measure_inventory.py --report PowerBI_Measures_Report_Model_wise.xlsx --model "Life expectancy v202009" \\
      --ddl output\\debug\\debug_generated_sql_Life_expectancy_v202009.sql --log <run>\\Life_expectancy_v202009.log \\
      --sml output\\reverse\\proj-life-expect\\sml\\model.yaml --translate

  # all models found under the repo (DDL + newest log matched by model name)
  python measure_inventory.py --report PowerBI_Measures_Report_Model_wise.xlsx --repo . --out measure_results

  # explicit mapping: batch.csv with columns  model,ddl,log,sml,pbix  (log/sml/pbix optional)
  python measure_inventory.py --report PowerBI_Measures_Report_Model_wise.xlsx --batch measure_batch.csv

  # later phases: compare with a saved baseline
  python measure_inventory.py ... --compare-baseline measure_results\\baseline.json
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from validate_semantic_view import _clause, _split_top  # noqa: E402
from relationship_inventory import sb_column  # noqa: E402  (SemaBridge naming rules, with fallback)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def key(name):
    """Comparable metric key: SemaBridge sanitised name, without quotes/case."""
    return sb_column(str(name)).upper().strip('"')


# ----------------------------------------------------------------------------- expected measures
def read_report(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        def is_header(r):
            cells = [str(c or "").strip().lower() for c in (r or []) if c is not None and len(str(c)) < 60]
            has = lambda w, excl=(): [i for i, c in enumerate(cells) if w in c and not any(x in c for x in excl)]
            x, ms = has("expression", ("no expression",)), has("measure")
            return bool(x and ms and len({x[0], ms[-1]}) >= 2)
        hdr_i = next((i for i, r in enumerate(rows[:15]) if is_header(r)), None)
        if hdr_i is None:
            continue
        hdr = [str(c or "").strip().lower() for c in rows[hdr_i]]
        def col(*words):
            return next((i for i, h in enumerate(hdr) if all(w in h for w in words)), None)
        cm = col("model")
        cx = next((i for i, h in enumerate(hdr) if "expression" in h and "no expression" not in h), None)
        cmeas = next((i for i, h in enumerate(hdr) if "measure" in h and "model" not in h and "uses" not in h and "total" not in h), None)
        ct = next((i for i, h in enumerate(hdr) if h == "table" or h.startswith("table")), None)
        if cx is None or cmeas is None:
            continue
        curr_model = None
        for r in rows[hdr_i + 1:]:
            if not r or not any(r):
                continue
            first_cell = str(r[0] or "").strip()
            sec_match = re.search(r"^(.+?)\s*[\u2013\u2014\-\ufffd]\s*\d+\s+Measures", first_cell, re.IGNORECASE)
            if sec_match:
                curr_model = sec_match.group(1).strip()
                continue
            row_model = str(r[cm]).strip() if (cm is not None and r[cm]) else curr_model
            if not row_model or not r[cmeas]:
                continue
            meas, table = str(r[cmeas]).strip(), (str(r[ct]).strip() if ct is not None and r[ct] else "")
            m = re.match(r"^'?(.+?)'?\[(.+)\]$", meas)          # 'Table'[Measure] form
            if m:
                table, meas = m.group(1).replace("''", "'"), m.group(2)
            out.append({"model": row_model, "table": table, "measure": meas,
                        "expr": str(r[cx] or "").strip(), "sheet": ws.title})
    # the same measure can appear on a combined sheet and a model sheet: keep one
    seen, uniq = set(), []
    for x in out:
        k = (norm(x["model"]), x["measure"])
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq


def read_pbix(path, model_name):
    from pbixray import PBIXRay
    db = PBIXRay(path)._metadata._meta._db
    q = db.query("SELECT t.Name AS TableName, ms.Name AS MeasureName, ms.Expression AS Expr "
                 "FROM Measure ms JOIN [Table] t ON ms.TableID = t.ID ORDER BY t.Name, ms.Name")
    if q is None or q.empty:
        return []
    return [{"model": model_name, "table": r.TableName, "measure": r.MeasureName,
             "expr": (r.Expr or "").strip() if isinstance(r.Expr, str) else ""} for r in q.itertuples()]


# ----------------------------------------------------------------------------- DAX pattern
SIMPLE = {"Simple aggregate", "Arithmetic / IF over measures", "CALCULATE + filter", "Iterator"}


def pattern(e):
    if not e or not e.strip():
        return "Empty"
    u = re.sub(r"\s+", " ", e).upper()
    if re.fullmatch(r"[\d\.\s\-]+", u.strip()):
        return "Constant"
    if re.search(r"SELECTEDVALUE|ISFILTERED|HASONEVALUE|USERELATIONSHIP|ISINSCOPE", u) and not re.search(r"SAMEPERIOD|TOTALYTD|DATEADD", u):
        return "Slicer / context"
    if re.search(r'"#[0-9A-F]{3,8}"|CONCATENATE|FORMAT\(|LEFT\(|& *"|" *&|UNICHAR', u) or re.fullmatch(r'\s*"[^"]*"\s*', e):
        return "Text / formatting"
    if re.search(r"SAMEPERIODLASTYEAR|TOTALYTD|TOTALMTD|TOTALQTD|DATESYTD|DATEADD|PREVIOUSMONTH|PREVIOUSYEAR|PARALLELPERIOD|DATESINPERIOD|DATESBETWEEN|LASTNONBLANK|LASTDATE|FIRSTDATE", u):
        return "Time intelligence"
    if re.search(r"=\s*MAX\(", u) and "FILTER" in u:
        return "Closing balance"
    if re.search(r"\bALL(SELECTED|EXCEPT|NOBLANKROW)?\(|REMOVEFILTERS\(", u):
        return "ALL() / % of total"
    if re.search(r"\b(SUMX|AVERAGEX|MAXX|MINX|COUNTX|RANKX|PRODUCTX)\(", u):
        return "Iterator"
    if "CALCULATE" in u or "FILTER" in u:
        return "CALCULATE + filter"
    if re.fullmatch(r"\s*(SUM|AVERAGE|COUNT|COUNTA|COUNTROWS|DISTINCTCOUNT|MIN|MAX)\s*\([^()]*\)\s*", u):
        return "Simple aggregate"
    return "Arithmetic / IF over measures"


def refs(expr, names):
    return sorted({x for x in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", expr or "") if x in names})


# ----------------------------------------------------------------------------- DDL metrics
def ddl_metrics(text):
    """{metric_key: {'table':..., 'name':..., 'expr':..., 'null': bool}}"""
    out = {}
    for item in _split_top(_clause(text, "metrics")):
        item = re.sub(r"\s+comment\s*=\s*'(?:[^']|'')*'\s*$", "", item, flags=re.I | re.S)
        item = re.sub(r"\s+with\s+synonyms\s*=\s*\(.*\)\s*$", "", item, flags=re.I | re.S)
        m = re.match(r'^\s*("?[^".\s]+"?)\s*\.\s*("(?:[^"]|"")+"|[^\s]+)\s+as\s+(.*)$', item, re.I | re.S)
        if not m:
            continue
        name = m.group(2).strip('"')
        expr = m.group(3).strip()
        out[name.upper()] = {"table": m.group(1).strip('"'), "name": name, "expr": expr,
                             "null": bool(re.match(r"^\(?\s*CAST\s*\(\s*NULL\s+AS", expr, re.I))}
    return out


# ----------------------------------------------------------------------------- log reasons
LOG_RULES = [
    ("UNREACHABLE", re.compile(r"Emitting unreachable metric '(?P<m>[^']+)' on '(?P<t>[^']+)' as NULL placeholder:?\s*(?P<r>[^\n]*)")),
    ("COLUMN_NOT_RESOLVED", re.compile(r"Metric '(?P<m>[^']+)': skipping invalid SQL expression after normalization:\s*(?P<r>.{0,300})")),
    ("PLACEHOLDER", re.compile(r"Emitting metric '(?P<m>[^']+)' on '(?P<t>[^']+)' as NULL placeholder \((?P<k>[^)]*)\):\s*(?P<r>.{0,300})")),
    ("NOT_TRANSLATABLE", re.compile(r"Could not translate metric '(?P<m>[^']+)' with expression '(?P<e>.{0,200}?)'\.? Skipping")),
    ("CIRCULAR", re.compile(r"Circular measure dependency detected for '(?P<m>[^']+)'")),
    ("NO_EXPRESSION", re.compile(r"Measure '(?P<m>[^']+)' in dataset '(?P<t>[^']+)' has no expression")),
    ("REANCHORED", re.compile(r"Re-anchoring metric '(?P<m>[^']+)' from '(?P<a>[^']+)' to '(?P<b>[^']+)'")),
    ("BY_DESIGN", re.compile(r"(?:Excluding|Excluded|excluded|not syncable|sync_enabled\s*=\s*False)[^\n']{0,80}'(?P<m>[^']+)'")),
]
GATE = re.compile(r"Translated SQL failed DAX validation gate:\s*(?P<sql>.*?)\s*\|\s*DAX:\s*(?P<dax>.+?)(?=\s+(?:WARNING|INFO|ERROR|DEBUG)\s|\Z)", re.S)


def read_log(path):
    text = open(path, encoding="utf-8", errors="ignore").read()
    # rich console wraps long records onto deeply indented lines; join those, but keep new records separate
    text = re.sub(r"\n\s{20,}(?!(?:INFO|WARNING|ERROR|DEBUG|CRITICAL)\s)", " ", text)
    ev = defaultdict(list)
    for kind, rx in LOG_RULES:
        for m in rx.finditer(text):
            k = key(m.group("m"))
            reason = m.groupdict().get("r") or m.groupdict().get("e") or ""
            if kind == "PLACEHOLDER":
                kind2 = "COLUMN_NOT_RESOLVED" if "could not be resolved" in reason else "NOT_TRANSLATABLE"
                ev[k].append((kind2, f"{m.group('k')}: {reason.strip()}"[:250]))
            elif kind == "REANCHORED":
                ev[k].append((kind, f"{m.group('a')} -> {m.group('b')}"))
            else:
                ev[k].append((kind, reason.strip()[:250]))
    gates = [(re.sub(r"\s+", "", m.group("dax")).upper(), m.group("sql")[:200]) for m in GATE.finditer(text)]
    return ev, gates


# ----------------------------------------------------------------------------- SML model.yaml
def read_sml(path):
    try:
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as exc:
        print(f"  (could not read SML {path}: {exc})")
        return {}
    out = {}
    def walk(o):
        if isinstance(o, dict):
            if "metrics" in o and isinstance(o["metrics"], list):
                for mt in o["metrics"]:
                    if isinstance(mt, dict) and (mt.get("unique_name") or mt.get("name")):
                        nm = mt.get("unique_name") or mt.get("name")
                        out[key(nm)] = {
                            "dataset": mt.get("dataset"),
                            "sql": mt.get("sql_expression") or mt.get("sql") or "",
                            "sync_enabled": mt.get("sync_enabled", True),
                            "status": mt.get("translation_status") or mt.get("status") or mt.get("tier") or "",
                        }
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out


# ----------------------------------------------------------------------------- isolated translator
def make_translator():
    try:
        from semabridge.converter.dax_translator import DAXTranslator
        t = DAXTranslator()
    except Exception as exc:
        print(f"  --translate: SemaBridge translator not importable here ({exc}); skipping that column")
        return None

    def run(expr, table, name):
        try:
            res = t.translate(expr, str(table).lower(), table, metric_name=name, skip_tier5=True)
            ok = bool(getattr(res, "is_success", False) and getattr(res, "sql", None))
            return ("OK" if ok else "FAIL", getattr(res, "tier", ""), (res.sql or "")[:200] if ok else (getattr(res, "failure_reason", "") or "")[:200])
        except Exception as exc:
            return ("ERROR", "", str(exc)[:200])
    return run


# ----------------------------------------------------------------------------- reconcile one model
PRIORITY = ["NO_EXPRESSION", "COLUMN_NOT_RESOLVED", "CIRCULAR", "UNREACHABLE", "NOT_TRANSLATABLE", "BY_DESIGN"]
STAGE = {
    "NO_EXPRESSION": "Power BI model (measure is empty)",
    "COLUMN_NOT_RESOLVED": "Step 8 DDL emission - column resolution after translation (sanitiser / parser)",
    "VALIDATION_GATE": "Step 6 OSI->SML - translated SQL rejected by DAX validation gate",
    "CIRCULAR": "Step 6 OSI->SML - dependency resolution",
    "UNREACHABLE": "Step 8 DDL emission - metric's table has no relationship path (measure-only table)",
    "NOT_TRANSLATABLE": "Step 6/8 DAX->SQL translation (tiers 1-4 failed, tier 5 unavailable)",
    "CASCADED": "Step 8 DDL emission - depends on a NULL / missing measure",
    "BY_DESIGN": "Step 6 - excluded: text / formatting / slicer measure",
    "UNKNOWN": "No evidence in the inputs - check the log around this measure",
}


def reconcile(expected, ddl_text, log_path=None, sml_path=None, translate=None):
    dm = ddl_metrics(ddl_text) if ddl_text else {}
    ev, gates = read_log(log_path) if log_path else ({}, [])
    sml = read_sml(sml_path) if sml_path else {}
    names = {e["measure"] for e in expected}
    rows, used = [], set()
    for e in expected:
        k = key(e["measure"])
        d = dm.get(k)
        if d:
            used.add(k)
        pat = pattern(e["expr"])
        if d and not d["null"]:
            outcome = "REAL_SQL"
        elif d:
            outcome = "NULL_PLACEHOLDER"
        else:
            outcome = "MISSING"
        evid = ev.get(k, [])
        kinds = [x[0] for x in evid]
        gate = next((s for dax, s in gates if dax == re.sub(r"\s+", "", e["expr"]).upper()), None)
        reason, detail = "", ""
        if outcome != "REAL_SQL":
            reason = next((p for p in PRIORITY if p in kinds), "")
            if gate and reason in ("", "NOT_TRANSLATABLE"):
                reason, detail = "VALIDATION_GATE", f"gate rejected: {gate}"
            if not reason and not e["expr"]:
                reason = "NO_EXPRESSION"
            if not reason and pat in ("Text / formatting", "Slicer / context", "Constant"):
                reason = "BY_DESIGN"
            detail = detail or next((x[1] for x in evid if x[0] == reason), "")
        rows.append({
            "model": e["model"], "table": e["table"], "measure": e["measure"], "expression": e["expr"],
            "dax_pattern": pat, "uses_measures": ", ".join(refs(e["expr"], names - {e["measure"]})),
            "expected_metric": k, "outcome": outcome,
            "ddl_table": d["table"] if d else "", "ddl_expression": (d["expr"][:300] if d else ""),
            "reason": reason, "stage": STAGE.get(reason, "") if outcome != "REAL_SQL" else "",
            "detail": detail, "reanchored": next((x[1] for x in evid if x[0] == "REANCHORED"), ""),
            "in_sml": "yes" if k in sml else ("" if not sml else "NO"),
            "sml_sync_enabled": str(sml[k]["sync_enabled"]) if k in sml else "",
            "sml_sql": (str(sml[k]["sql"])[:200] if k in sml else ""),
        })
    # cascades: a non-real measure whose dependencies are non-real, and no stronger own reason
    state = {r["measure"]: r["outcome"] for r in rows}
    for r in rows:
        bad = [x for x in r["uses_measures"].split(", ") if x and state.get(x) != "REAL_SQL"]
        r["cascaded_from"] = ", ".join(bad) if r["outcome"] != "REAL_SQL" else ""
        if r["outcome"] != "REAL_SQL" and bad and r["reason"] in ("", "NOT_TRANSLATABLE"):
            r["reason"], r["stage"] = "CASCADED", STAGE["CASCADED"]
            r["detail"] = r["detail"] or f"depends on: {', '.join(bad)}"
        if r["outcome"] != "REAL_SQL" and not r["reason"]:
            r["reason"], r["stage"] = "UNKNOWN", STAGE["UNKNOWN"]
        r["simple_but_null"] = "yes" if (r["outcome"] != "REAL_SQL" and r["dax_pattern"] in SIMPLE
                                         and r["reason"] not in ("CASCADED", "NO_EXPRESSION", "UNREACHABLE")) else ""
        if translate and r["outcome"] != "REAL_SQL" and r["expression"]:
            st, tier, info = translate(r["expression"], r["table"], r["measure"])
            r["translator_isolated"], r["translator_tier"], r["translator_output"] = st, tier, info
    extra = [{"model": expected[0]["model"] if expected else "", "measure": v["name"], "outcome": "EXTRA_IN_DDL",
              "ddl_table": v["table"], "ddl_expression": v["expr"][:300]}
             for k2, v in dm.items() if k2 not in used]
    return rows, extra


# ----------------------------------------------------------------------------- discovery / main
def newest(paths):
    return max(paths, key=os.path.getmtime) if paths else None


def discover(repo, models):
    ddls = glob.glob(os.path.join(repo, "output", "debug", "debug_generated_sql_*.sql"))
    logs = glob.glob(os.path.join(repo, "output", "runs", "**", "*.log"), recursive=True)
    smls = glob.glob(os.path.join(repo, "output", "reverse", "**", "model.yaml"), recursive=True)
    jobs = []
    for mname in models:
        n = norm(mname)
        m_words = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", mname.lower())} - {"and", "sample", "v201912", "v202009", "pbix"}
        def match_fn(cand_norm, cand_raw=""):
            if cand_norm == n or n in cand_norm or cand_norm in n:
                return True
            c_words = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", cand_raw.lower())} - {"and", "sample", "v201912", "v202009", "pbix"}
            return bool(m_words and c_words and (m_words.issubset(c_words) or len(m_words & c_words) >= 2))
        ddl = newest([p for p in ddls if match_fn(norm(os.path.basename(p)[len("debug_generated_sql_"):-4]), os.path.basename(p))])
        log = newest([p for p in logs if match_fn(norm(os.path.splitext(os.path.basename(p))[0]), os.path.basename(p))])
        sml = newest([p for p in smls if match_fn(n, open(p, encoding="utf-8", errors="ignore").read(4000))])
        jobs.append({"model": mname, "ddl": ddl, "log": log, "sml": sml, "pbix": None})
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", help="model-wise measures Excel (Model Name, Measure, Expression columns)")
    ap.add_argument("--pbix", help="read measures from this .pbix instead of a report (single model)")
    ap.add_argument("--model", help="single model name (as in the report)")
    ap.add_argument("--ddl"); ap.add_argument("--log"); ap.add_argument("--sml")
    ap.add_argument("--repo", help="SemaBridge repo root: auto-find DDL / log / SML per model")
    ap.add_argument("--batch", help="CSV: model,ddl,log,sml,pbix")
    ap.add_argument("--translate", action="store_true", help="re-run SemaBridge's translator on failing measures")
    ap.add_argument("--out", default="measure_results")
    ap.add_argument("--compare-baseline")
    args = ap.parse_args()

    expected_all = read_report(args.report) if args.report else []
    by_model = defaultdict(list)
    for e in expected_all:
        by_model[norm(e["model"])].append(e)

    if args.batch:
        with open(args.batch, newline="", encoding="utf-8") as fh:
            jobs = [{k: (v or None) for k, v in r.items()} for r in csv.DictReader(fh)]
    elif args.repo:
        jobs = discover(args.repo, [v[0]["model"] for v in by_model.values()])
    else:
        if not args.model and not args.pbix:
            ap.error("give --model (with --report) or --pbix, plus --ddl; or --repo / --batch")
        jobs = [{"model": args.model or os.path.splitext(os.path.basename(args.pbix))[0],
                 "ddl": args.ddl, "log": args.log, "sml": args.sml, "pbix": args.pbix}]

    translate = make_translator() if args.translate else None
    os.makedirs(args.out, exist_ok=True)
    all_rows, extras, summary = [], [], []
    for j in jobs:
        exp = read_pbix(j["pbix"], j["model"]) if j.get("pbix") else by_model.get(norm(j["model"]), [])
        if not exp:
            print(f"\n=== {j['model']}: no expected measures (check the model name in the report)"); continue
        if not j.get("ddl") or not os.path.exists(j["ddl"]):
            print(f"\n=== {j['model']}: no generated DDL found - every measure will show MISSING")
        ddl_text = open(j["ddl"], encoding="utf-8", errors="ignore").read() if j.get("ddl") and os.path.exists(j["ddl"]) else ""
        rows, extra = reconcile(exp, ddl_text, j.get("log"), j.get("sml"), translate)
        all_rows += rows; extras += extra
        c = Counter(r["outcome"] for r in rows); rc = Counter(r["reason"] for r in rows if r["reason"])
        sb = sum(1 for r in rows if r["simple_but_null"])
        summary.append({"model": j["model"], "measures": len(rows), "REAL_SQL": c["REAL_SQL"],
                        "NULL_PLACEHOLDER": c["NULL_PLACEHOLDER"], "MISSING": c["MISSING"],
                        "simple_but_null": sb, **{f"reason_{k}": v for k, v in rc.items()},
                        "ddl": j.get("ddl") or "", "log": j.get("log") or "", "sml": j.get("sml") or ""})
        print(f"\n=== {j['model']}  ({len(rows)} measures)   REAL_SQL {c['REAL_SQL']}   NULL {c['NULL_PLACEHOLDER']}   "
              f"MISSING {c['MISSING']}   simple-but-null {sb}")
        print(f"    ddl={j.get('ddl') or '-'}\n    log={j.get('log') or '-'}\n    sml={j.get('sml') or '-'}")
        for reason, n in rc.most_common():
            print(f"    {reason:20} {n:3}  {STAGE.get(reason, '')}")
        for r in rows:
            if r["simple_but_null"]:
                print(f"    ! SIMPLE BUT {r['outcome']}: {r['measure']}  [{r['reason']}]  {r['expression'][:70]!r}")

    fields = ["model", "table", "measure", "expression", "dax_pattern", "uses_measures", "expected_metric", "outcome",
              "reason", "stage", "detail", "cascaded_from", "simple_but_null", "reanchored", "ddl_table", "ddl_expression",
              "in_sml", "sml_sync_enabled", "sml_sql", "translator_isolated", "translator_tier", "translator_output"]
    with open(os.path.join(args.out, "measure_inventory.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(all_rows)
    with open(os.path.join(args.out, "measure_extra_in_ddl.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["model", "measure", "outcome", "ddl_table", "ddl_expression"]); w.writeheader(); w.writerows(extras)
    keys = sorted({k for s in summary for k in s}, key=lambda k: (k != "model", k))
    with open(os.path.join(args.out, "measure_summary.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(summary)

    snap = {f"{r['model']}|{r['measure']}": {"outcome": r["outcome"], "reason": r["reason"]} for r in all_rows}
    base = args.compare_baseline or os.path.join(args.out, "baseline.json")
    if os.path.exists(base) and (args.compare_baseline or os.path.exists(os.path.join(args.out, "baseline.json"))):
        old = json.load(open(base, encoding="utf-8"))
        changes = [f"{k}: {old[k]['outcome']}/{old[k]['reason'] or '-'} -> {v['outcome']}/{v['reason'] or '-'}"
                   for k, v in snap.items() if k in old and (old[k]["outcome"], old[k]["reason"]) != (v["outcome"], v["reason"])]
        print(f"\n=== Changes vs baseline ({base}): {len(changes)}")
        for ch in changes[:200]:
            print("   ", ch)
        json.dump(snap, open(os.path.join(args.out, "latest.json"), "w", encoding="utf-8"), indent=1)
    else:
        json.dump(snap, open(base, "w", encoding="utf-8"), indent=1)
        print(f"\nBaseline saved: {base}")
    tot = Counter(r["outcome"] for r in all_rows)
    print(f"\nTOTAL {len(all_rows)} measures: REAL_SQL {tot['REAL_SQL']}, NULL {tot['NULL_PLACEHOLDER']}, MISSING {tot['MISSING']}, "
          f"simple-but-null {sum(1 for r in all_rows if r['simple_but_null'])}")
    print(f"Written: {os.path.abspath(args.out)}\\measure_inventory.csv, measure_summary.csv, measure_extra_in_ddl.csv")


if __name__ == "__main__":
    main()
