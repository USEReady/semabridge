#!/usr/bin/env python3
"""
relationship_inventory.py - Phase A: relationship accounting (read-only, no SemaBridge changes).

For ANY model, compares every relationship in the .pbix with the relationships in the
generated semantic-view DDL and gives each source relationship exactly one outcome:

  EMITTED            found with the same tables, direction and columns
  EMITTED_REVERSED   found, but FROM and TO are swapped
  EMITTED_DIFFERENT  same tables, but different columns
  MISSING            not in the DDL at all            <- relationships lost silently
  EXTRA              in the DDL but not in the .pbix  (reported separately)

For each emitted relationship it also checks whether the referenced side declares a
PRIMARY KEY or UNIQUE key on the referenced columns.

Nothing is hardcoded: tables and columns are matched through SemaBridge's naming rules
(uppercase, special characters -> '_', reserved words -> COL_<name>) and through the
logical alias / physical table / '<name>_ENRICHED' view in the DDL.

Also reports AMBIGUOUS when more than one DDL relationship could match a source relationship.

Usage
  # one model
  python relationship_inventory.py --pbix "Sales & Returns.pbix" --ddl-file actual_sales_returns.sql

  # many models: batch.csv has columns  model,pbix,ddl
  python relationship_inventory.py --batch batch.csv --csv inventory.csv --summary-csv summary.csv \\
         --save-baseline baseline.json

  # later phases: same run, compared with the saved baseline
  python relationship_inventory.py --batch batch.csv --compare-baseline baseline.json

Exit code: 0 = every source relationship emitted with a valid key, 1 = otherwise.
Needs: pbixray (pip install pbixray), and validate_semantic_view.py in the same folder.
"""
import argparse
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_semantic_view import parse_ddl  # noqa: E402  (same folder)

# ----- SemaBridge naming rules (same as semabridge.utils.identifiers / the loader) -------------
try:
    from semabridge.utils.identifiers import IdentifierSanitizer as _S
    _SAN = _S(force_uppercase=True, suppress_reserved=True)
    sb_table, sb_column = _SAN.sanitize_table_name, _SAN.sanitize_column
except Exception:
    _RESERVED = {"table", "column", "date", "group", "order", "join", "view", "select", "from", "where",
                 "and", "or", "not", "null", "true", "false", "as", "by", "on", "in", "is", "having", "limit",
                 "offset", "union", "except", "intersect", "into", "insert", "update", "delete", "create",
                 "drop", "alter", "grant", "revoke", "value", "count", "sum", "avg", "min", "max", "current",
                 "session", "account", "database", "schema", "user", "role", "warehouse", "stage", "sequence",
                 "stream", "task", "pipe", "integer", "varchar", "boolean", "float", "number", "string",
                 "timestamp", "over", "partition", "row", "rows", "range", "between", "all", "any", "some",
                 "exists", "case", "when", "then", "else", "end", "distinct", "unique", "primary", "foreign",
                 "key", "references", "constraint", "index", "default", "check", "like", "ilike"}
    _REPL = {'%': 'PCT', '$': 'DOL', '@': 'AT', '#': 'NUM', '&': 'AND', '*': 'STAR', '+': 'PLUS', '=': 'EQ',
             '?': 'Q', '!': 'EXCL', '<': 'LT', '>': 'GT'}

    def sb_column(name):
        for a, b in _REPL.items():
            name = name.replace(a, b)
        c = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_$]", "_", name)).strip("_") or "COLUMN_UNKNOWN"
        c = ("_" + c if c[0].isdigit() else c).upper()
        return "COL_" + c if c.lower() in _RESERVED else c

    def sb_table(name):
        c = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_$]", "_", name)).strip("_")
        return (("_" + c) if c and c[0].isdigit() else c).upper() or name.upper()

AUTO_DATE = ("LocalDateTable_", "DateTableTemplate_")


def norm_col(c):
    """Compare columns loosely: SemaBridge may add collision suffixes like _1 or _5D45."""
    return re.sub(r"_(\d+|[0-9A-F]{4})$", "", c.upper())


# ----- source relationships ---------------------------------------------------------------------
_RAW_REL_SQL = """
SELECT ft.Name AS FromTableName, fc.ExplicitName AS FromColumnName, fc.InferredName AS FromInferred,
       tt.Name AS ToTableName,   tc.ExplicitName AS ToColumnName,   tc.InferredName AS ToInferred,
       rel.IsActive AS IsActive, rel.{fc_card} AS FromCard, rel.{tc_card} AS ToCard,
       rel.CrossFilteringBehavior AS XF,
       COALESCE(ft.IsPrivate, 0) + COALESCE(ft.ShowAsVariationsOnly, 0) AS FromAutoDate,
       COALESCE(tt.IsPrivate, 0) + COALESCE(tt.ShowAsVariationsOnly, 0) AS ToAutoDate
FROM Relationship rel
LEFT JOIN [Table] ft ON rel.{f_t} = ft.ID   LEFT JOIN [Column] fc ON rel.{f_c} = fc.ID
LEFT JOIN [Table] tt ON rel.{t_t} = tt.ID   LEFT JOIN [Column] tc ON rel.{t_c} = tc.ID
"""


def _raw_relationship_rows(model):
    """All relationships straight from the model metadata, WITHOUT pbixray's SystemFlags filter.
    pbixray drops (or blanks) relationships whose table has SystemFlags != 0, which also hits
    calculated / what-if parameter tables (e.g. GENERATESERIES). Auto date/time tables are
    excluded here by their own flags instead: IsPrivate (DateTableTemplate) or
    ShowAsVariationsOnly (LocalDateTable). Returns None if the metadata DB is not reachable."""
    query = None
    for path in (("_metadata", "_meta", "_db"),):          # pbixray >= 0.4 style
        obj = model
        try:
            for a in path:
                obj = getattr(obj, a)
            query = obj.query
            break
        except AttributeError:
            continue
    if query is None:                                        # older pbixray: build a handler
        try:
            from pbixray.utils import get_data_slice
            from pbixray.meta.sqlite_handler import SQLiteHandler
            dm = getattr(getattr(model, "_metadata_handler", None), "_data_model", None) or getattr(model, "_data_model")
            query = SQLiteHandler(get_data_slice(dm, "metadata.sqlitedb")).execute_query
        except Exception:
            return None
    try:
        cols = set(query("SELECT * FROM Relationship LIMIT 1").columns)
        pick = lambda a, b: a if a in cols else b
        sql = _RAW_REL_SQL.format(
            f_t="FromTableID", t_t="ToTableID",
            f_c=pick("FromColumnID", "FromEndColumnID"), t_c=pick("ToColumnID", "ToEndColumnID"),
            fc_card=pick("FromCardinality", "FromEndCardinality"), tc_card=pick("ToCardinality", "ToEndCardinality"))
        return query(sql).to_dict("records")
    except Exception:
        return None


def source_relationships(pbix):
    from pbixray import PBIXRay
    model = PBIXRay(pbix)

    def _blank(v):
        return v is None or (isinstance(v, float) and v != v) or str(v).strip() in ("", "None", "nan")

    raw = _raw_relationship_rows(model)
    out = []
    if raw is not None:
        for r in raw:
            if r.get("FromAutoDate") or r.get("ToAutoDate"):
                continue                                     # auto date/time hierarchy tables
            fcol = r["FromColumnName"] if not _blank(r.get("FromColumnName")) else r.get("FromInferred")
            tcol = r["ToColumnName"] if not _blank(r.get("ToColumnName")) else r.get("ToInferred")
            if any(_blank(v) for v in (r.get("FromTableName"), fcol, r.get("ToTableName"), tcol)):
                continue
            out.append({
                "from_t": r["FromTableName"], "from_c": fcol, "to_t": r["ToTableName"], "to_c": tcol,
                "active": bool(r.get("IsActive")),
                "card": f"{'M' if r.get('FromCard') == 2 else '1'}:{'M' if r.get('ToCard') == 2 else '1'}",
                "xfilter": {1: "Single", 2: "Both"}.get(r.get("XF"), str(r.get("XF"))),
            })
        return out

    r = model.relationships                                  # fallback: pbixray's filtered view
    for row in r.itertuples():
        if any(_blank(getattr(row, c, None)) for c in ("FromTableName", "FromColumnName", "ToTableName", "ToColumnName")):
            continue
        if str(row.FromTableName).startswith(AUTO_DATE) or str(row.ToTableName).startswith(AUTO_DATE):
            continue
        out.append({
            "from_t": row.FromTableName, "from_c": row.FromColumnName,
            "to_t": row.ToTableName, "to_c": row.ToColumnName,
            "active": bool(row.IsActive),
            "card": str(getattr(row, "Cardinality", "") or ""),
            "xfilter": str(getattr(row, "CrossFilteringBehavior", "") or ""),
        })
    return out


# ----- map a source table name to the DDL's logical aliases -------------------------------------
def alias_index(tables):
    """sanitized base name -> [logical aliases] (alias itself, physical table, *_ENRICHED base)."""
    idx = {}
    for alias, t in tables.items():
        phys = t["fqn"].split(".")[-1].upper()
        keys = {alias.upper(), phys, re.sub(r"_ENRICHED$", "", phys)}
        for k in keys:
            idx.setdefault(k, set()).add(alias)
    return idx


def has_key(tables, alias, cols):
    t = tables.get(alias, {})
    keys = ([t.get("pk")] if t.get("pk") else []) + t.get("unique", [])
    want = [norm_col(c) for c in cols]
    return any([norm_col(c) for c in k] == want for k in keys)


def reconcile(pbix, ddl_text):
    """-> (rows, extra, summary) for one model. Read-only."""
    src = source_relationships(pbix)
    tables, ddl_rels = parse_ddl(ddl_text)
    idx = alias_index(tables)
    used, rows = set(), []

    for s in src:
        f_aliases = idx.get(sb_table(s["from_t"]), set())
        t_aliases = idx.get(sb_table(s["to_t"]), set())
        fc, tc = norm_col(sb_column(s["from_c"])), norm_col(sb_column(s["to_c"]))
        candidates = []
        for i, d in enumerate(ddl_rels):
            if i in used:
                continue
            dfc = [norm_col(c) for c in d["from_cols"]]
            dtc = [norm_col(c) for c in (d["to_cols"] or tables.get(d["to"], {}).get("pk", []))]
            if d["from"] in f_aliases and d["to"] in t_aliases:
                kind = "EMITTED" if (dfc == [fc] and dtc == [tc]) else "EMITTED_DIFFERENT"
            elif d["from"] in t_aliases and d["to"] in f_aliases:
                kind = "EMITTED_REVERSED" if (dfc == [tc] and dtc == [fc]) else "EMITTED_DIFFERENT"
            else:
                continue
            candidates.append((kind, i, d))
        exact = [c for c in candidates if c[0] in ("EMITTED", "EMITTED_REVERSED")]
        pick = exact or candidates
        if not pick:
            outcome, d = "MISSING", None
        elif len(pick) > 1:
            outcome, d = "AMBIGUOUS", pick[0][2]
            used.add(pick[0][1])
        else:
            outcome, d = pick[0][0], pick[0][2]
            used.add(pick[0][1])
        key_ok = (has_key(tables, d["to"], d["to_cols"] or tables[d["to"]]["pk"])
                  if d and d["to"] in tables else None)
        if not f_aliases or not t_aliases:
            note = "table not in DDL: " + ", ".join(
                str(n) for n, a in ((s["from_t"], f_aliases), (s["to_t"], t_aliases)) if not a)
        elif outcome == "MISSING":
            note = "both tables in DDL, relationship not -> dropped; trace logs / drop ledger"
        elif outcome == "AMBIGUOUS":
            note = f"{len(pick)} DDL relationships could match; review by hand"
        elif key_ok is False:
            note = f"referenced side '{d['to']}' has no PRIMARY KEY/UNIQUE on {d['to_cols']}"
        else:
            note = ""
        rows.append({
            "source_relationship": f"{s['from_t']}[{s['from_c']}] -> {s['to_t']}[{s['to_c']}]",
            "active": "yes" if s["active"] else "NO",
            "cardinality": s["card"], "cross_filter": s["xfilter"],
            "outcome": outcome,
            "ddl_relationship": f"{d['name']}: {d['from']}{d['from_cols']} -> {d['to']}{d['to_cols']}" if d else "",
            "key_on_referenced_side": "" if key_ok is None else ("yes" if key_ok else "NO"),
            "note": note,
        })
    extra = [d for i, d in enumerate(ddl_rels) if i not in used]
    counts = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    summary = {"source": len(src), "ddl_relationships": len(ddl_rels), "ddl_tables": len(tables),
               **counts, "EXTRA": len(extra),
               "key_missing": sum(1 for r in rows if r["key_on_referenced_side"] == "NO")}
    return rows, extra, summary


def is_clean(rows):
    return all(r["outcome"] in ("EMITTED", "EMITTED_REVERSED") and r["key_on_referenced_side"] != "NO"
               for r in rows)


def print_model(name, rows, extra, summary):
    print(f"\n=== {name}\n  Source relationships (.pbix): {summary['source']}   "
          f"DDL relationships: {summary['ddl_relationships']}   DDL tables: {summary['ddl_tables']}\n")
    w = (52, 6, 6, 18, 4)
    print(f"  {'SOURCE RELATIONSHIP':{w[0]}} {'ACTIVE':{w[1]}} {'CARD':{w[2]}} {'OUTCOME':{w[3]}} {'KEY':{w[4]}} NOTE")
    print("  " + "-" * 140)
    for r in rows:
        print(f"  {r['source_relationship'][:w[0]]:{w[0]}} {r['active']:{w[1]}} {r['cardinality']:{w[2]}} "
              f"{r['outcome']:{w[3]}} {r['key_on_referenced_side'] or '-':{w[4]}} {r['note']}")
    for d in extra:
        print(f"  {'(not in .pbix)':{w[0]}} {'':{w[1]}} {'':{w[2]}} {'EXTRA':{w[3]}} {'':{w[4]}} "
              f"{d['name']}: {d['from']} -> {d['to']}")
    print("\n  " + "   ".join(f"{k} {v}" for k, v in summary.items()
                             if k not in ("source", "ddl_relationships", "ddl_tables")))
    print(f"  Accounted: {len(rows)} of {summary['source']}   |   clean: {'yes' if is_clean(rows) else 'NO'}")


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=["model"] + [k for k in (rows[0] if rows else {"source_relationship": 0})
                                                       if k != "model"])
        wr.writeheader()
        wr.writerows(rows)


def compare_baseline(baseline, current):
    """Per relationship: outcome / key changes vs. a saved baseline."""
    print("\n=== Changes vs baseline")
    changed = 0
    for model, rels in current.items():
        old = {r["source_relationship"]: r for r in baseline.get(model, [])}
        for r in rels:
            o = old.get(r["source_relationship"])
            if o is None:
                print(f"  {model}: NEW  {r['source_relationship']} -> {r['outcome']}"); changed += 1
            elif (o["outcome"], o["key_on_referenced_side"]) != (r["outcome"], r["key_on_referenced_side"]):
                print(f"  {model}: {r['source_relationship']}: {o['outcome']}/{o['key_on_referenced_side'] or '-'}"
                      f" -> {r['outcome']}/{r['key_on_referenced_side'] or '-'}"); changed += 1
    print(f"  {changed} change(s)" if changed else "  no changes")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    one = ap.add_argument_group("one model")
    one.add_argument("--pbix")
    one.add_argument("--ddl-file", help="generated CREATE SEMANTIC VIEW statement")
    ap.add_argument("--batch", help="CSV with columns model,pbix,ddl (one row per model)")
    ap.add_argument("--csv", help="write the per-relationship inventory (all models) to this CSV")
    ap.add_argument("--summary-csv", help="write one summary row per model to this CSV")
    ap.add_argument("--save-baseline", help="save results as a JSON baseline for later phases")
    ap.add_argument("--compare-baseline", help="compare results with a saved JSON baseline")
    args = ap.parse_args()

    if args.batch:
        with open(args.batch, newline="", encoding="utf-8") as fh:
            jobs = [(r["model"], r["pbix"], r["ddl"]) for r in csv.DictReader(fh)]
    elif args.pbix and args.ddl_file:
        jobs = [(os.path.splitext(os.path.basename(args.pbix))[0], args.pbix, args.ddl_file)]
    else:
        ap.error("give --pbix and --ddl-file, or --batch")

    all_rows, results, summaries, clean = [], {}, [], True
    for model, pbix, ddl in jobs:
        try:
            rows, extra, summary = reconcile(pbix, open(ddl, encoding="utf-8").read())
        except Exception as exc:
            print(f"\n=== {model}\n  ERROR: {exc}")
            summaries.append({"model": model, "error": str(exc)}); clean = False
            continue
        print_model(model, rows, extra, summary)
        results[model] = rows
        all_rows += [{"model": model, **r} for r in rows]
        summaries.append({"model": model, **summary, "clean": is_clean(rows)})
        clean = clean and is_clean(rows)

    if len(jobs) > 1:
        print("\n=== Summary")
        for s in summaries:
            print("  " + "   ".join(f"{k}={v}" for k, v in s.items()))
    if args.csv and all_rows:
        write_csv(args.csv, all_rows); print(f"\n  Written: {args.csv}")
    if args.summary_csv and summaries:
        keys = sorted({k for s in summaries for k in s}, key=lambda k: (k != "model", k))
        with open(args.summary_csv, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(summaries)
        print(f"  Written: {args.summary_csv}")
    if args.save_baseline:
        json.dump(results, open(args.save_baseline, "w", encoding="utf-8"), indent=2)
        print(f"  Baseline saved: {args.save_baseline}")
    if args.compare_baseline:
        compare_baseline(json.load(open(args.compare_baseline, encoding="utf-8")), results)
    sys.exit(0 if clean else 1)


if __name__ == "__main__":
    main()
