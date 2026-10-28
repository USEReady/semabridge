#!/usr/bin/env python3
"""
measure_stage_diagnostic.py - Step 1 (extraction & registration): does EVERY Power BI measure reach
SemaBridge's translator, and if not, at which stage is it lost?  Read-only: nothing is changed or deployed.

Stages checked for each .pbix
  S1 raw      model metadata (Measure table)                        - the ground truth
  S2 pbixray  pbixray.dax_measures                                  - what the library exposes
  S3 fallback LocalPBIXConnector._extract_with_pbixray()            - SemaBridge's raw extraction
  S4 tmsl     connector TMSL (discover()['raw_tmsl'] or _build_tmsl_from_fallback)
  S5 osi      TMSLToOSIConverter().to_osi(...).metrics
  S6 sml      OSIToSMLConverter().from_osi(...).metrics             - what the translator receives
  S7 ddl      (optional --ddl) generated semantic view metrics: REAL / NULL / absent

For every measure it reports present/absent per stage, the FIRST stage where it disappears, and the
kind of table it lives on (normal, calculated, measure-only, hidden, private/auto-date), because
measures usually disappear together with their table. Tables are tracked through the stages too.

Stages S3-S6 need SemaBridge importable (run inside the repo venv). If a stage's API differs, that
stage is shown as ERROR with the message and the other stages still run. Field names are discovered
at runtime (no assumption about exact dict keys / attribute names).

Usage (repo root)
  uv run python phase_a\\measure_stage_diagnostic.py --pbix "C:\\...\\Life expectancy v202009.pbix"
  uv run python phase_a\\measure_stage_diagnostic.py --pbix-dir "C:\\Users\\manoj.s\\Downloads\\PBIX Files" --out measure_stage_results
  uv run python phase_a\\measure_stage_diagnostic.py --pbix model.pbix --ddl output\\debug\\debug_generated_sql_<model>.sql
"""
import argparse
import csv
import glob
import os
import re
import sys
import traceback
import warnings
from collections import Counter, defaultdict

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STAGES = ["S1_raw", "S2_pbixray", "S3_fallback", "S4_tmsl", "S5_osi", "S6_sml", "S7_ddl"]
STAGE_TXT = {
    "S2_pbixray": "pbixray does not expose it",
    "S3_fallback": "SemaBridge pbixray extraction (LocalPBIXConnector._extract_with_pbixray)",
    "S4_tmsl": "TMSL assembly (connector discover / _build_tmsl_from_fallback)",
    "S5_osi": "TMSL -> OSI conversion",
    "S6_sml": "OSI -> SML conversion (never reaches the translator)",
    "S7_ddl": "after SML: not emitted in the semantic view",
}


def nk(s):
    """Loose name key: case/space/punctuation-insensitive (handles SemaBridge sanitised names)."""
    s = str(s or "")
    for a, b in {"%": "pct", "$": "dol", "&": "and", "#": "num", "@": "at"}.items():
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def nk_variants(s):
    """Both spellings: with %/$/& written out (SemaBridge sanitiser) and with them simply removed."""
    return {nk(s), re.sub(r"[^a-z0-9]", "", str(s or "").lower())}


# ----------------------------------------------------------------------------- S1 / S2
def stage_raw(pbix):
    from pbixray import PBIXRay
    m = PBIXRay(pbix)
    db = m._metadata._meta._db
    tables = db.query("SELECT ID, Name, COALESCE(SystemFlags,0) SF, COALESCE(IsHidden,0) H, COALESCE(IsPrivate,0) P, "
                      "COALESCE(ShowAsVariationsOnly,0) V FROM [Table]")
    cols = db.query("SELECT TableID, COUNT(*) N FROM [Column] WHERE Type IN (1,2,4) GROUP BY TableID")
    ncols = dict(zip(cols.TableID, cols.N)) if cols is not None and not cols.empty else {}
    meas = db.query("SELECT t.Name AS TableName, ms.Name AS MeasureName, ms.Expression AS Expr, "
                    "COALESCE(ms.IsHidden,0) AS Hidden FROM Measure ms JOIN [Table] t ON ms.TableID = t.ID")
    kind = {}
    for t in tables.itertuples():
        if t.Name.startswith(("H$", "R$", "U$")):
            continue
        k = []
        if t.P or t.V or t.Name.startswith(("LocalDateTable_", "DateTableTemplate_")):
            k.append("auto-date/private")
        if t.SF == 2:
            k.append("calculated (SystemFlags 2)")
        if ncols.get(t.ID, 0) == 0:
            k.append("measure-only (no columns)")
        if t.H:
            k.append("hidden")
        kind[t.Name] = ", ".join(k) or "normal"
    rows = []
    if meas is not None and not meas.empty:
        for r in meas.itertuples():
            rows.append({"table": r.TableName, "measure": r.MeasureName,
                         "expr": r.Expr.strip() if isinstance(r.Expr, str) else "", "hidden": bool(r.Hidden)})
    s2 = set()
    try:
        dm = m.dax_measures
        if dm is not None and len(dm):
            s2 = {(nk(a), nk(b)) for a, b in zip(dm.TableName, dm.Name)}
    except Exception:
        pass
    t2 = {nk(t) for t in (m.tables if m.tables is not None else [])}
    return rows, kind, s2, t2


# ----------------------------------------------------------------------------- generic extractors
NAME_KEYS = ("unique_name", "name", "Name", "measure", "measure_name", "label")
TABLE_KEYS = ("dataset", "dataset_name", "table", "table_name", "TableName", "fromTable", "parent")


def get(o, keys):
    for k in keys:
        v = o.get(k) if isinstance(o, dict) else getattr(o, k, None)
        if v not in (None, "") and not callable(v):
            return v if isinstance(v, str) else getattr(v, "unique_name", None) or getattr(v, "name", None) or str(v)
    return None


def measures_in(obj, depth=0, table=None, out=None):
    """Find measure-like records anywhere in a dict/list structure (TMSL, fallback dict)."""
    out = [] if out is None else out
    if depth > 8:
        return out
    if isinstance(obj, dict):
        tname = obj.get("name") if ("columns" in obj or "measures" in obj) else table
        for k, v in obj.items():
            if k.lower() in ("measures", "metrics") and isinstance(v, list):
                for mm in v:
                    if isinstance(mm, dict) and get(mm, NAME_KEYS):
                        out.append((get(mm, TABLE_KEYS) or tname or "", get(mm, NAME_KEYS)))
            elif isinstance(v, (dict, list)):
                measures_in(v, depth + 1, tname, out)
    elif isinstance(obj, list):
        for v in obj:
            measures_in(v, depth + 1, table, out)
    return out


def tables_in(obj):
    names = set()
    def walk(o, d=0):
        if d > 6:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower() in ("tables", "datasets") and isinstance(v, list):
                    for t in v:
                        n = t.get("name") or t.get("unique_name") if isinstance(t, dict) else (t if isinstance(t, str) else None)
                        if n:
                            names.add(nk(n))
                elif isinstance(v, (dict, list)):
                    walk(v, d + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, d + 1)
    walk(obj)
    return names


def model_metrics(model):
    """(table, name, extra) for OSI / SML model objects."""
    out = []
    for mt in getattr(model, "metrics", None) or []:
        extra = {}
        for f in ("sync_enabled", "sql_expression"):
            if hasattr(mt, f):
                extra[f] = getattr(mt, f)
        out.append((get(mt, TABLE_KEYS) or "", get(mt, NAME_KEYS) or "", extra))
    for ds in getattr(model, "datasets", None) or []:        # some models keep metrics per dataset
        for mt in getattr(ds, "metrics", None) or []:
            out.append((getattr(ds, "unique_name", None) or getattr(ds, "name", ""), get(mt, NAME_KEYS) or "", {}))
    return out


def model_tables(model):
    return {nk(getattr(d, "unique_name", None) or getattr(d, "name", "")) for d in (getattr(model, "datasets", None) or [])}


# ----------------------------------------------------------------------------- S3 - S6 (SemaBridge)
def semabridge_stages(pbix, model_name):
    res = {s: {"status": "not run", "measures": None, "tables": None, "extra": {}} for s in ("S3_fallback", "S4_tmsl", "S5_osi", "S6_sml")}
    try:
        from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    except Exception as exc:
        for s in res:
            res[s]["status"] = f"SemaBridge not importable: {exc}"
        return res
    conn = None
    for ctor in (lambda: LocalPBIXConnector(config={"pbix_path": pbix}), lambda: LocalPBIXConnector({"pbix_path": pbix}),
                 lambda: LocalPBIXConnector(pbix)):
        try:
            conn = ctor(); break
        except Exception:
            continue
    if conn is None:
        for s in res:
            res[s]["status"] = "ERROR: could not construct LocalPBIXConnector"
        return res

    fb = None
    try:
        fb = conn._extract_with_pbixray()
        res["S3_fallback"].update(status="ok", measures=measures_in(fb), tables=tables_in(fb))
    except Exception as exc:
        res["S3_fallback"]["status"] = f"ERROR: {exc}"

    tmsl = None
    try:
        disc = conn.discover()
        tmsl = disc.get("raw_tmsl") if isinstance(disc, dict) else None
    except Exception as exc:
        res["S4_tmsl"]["status"] = f"discover() failed: {exc}"
    if tmsl is None and fb is not None:
        for fn in ("_build_tmsl_from_fallback", "_build_fallback_tmsl", "_build_tmsl_payload"):
            if hasattr(conn, fn):
                try:
                    tmsl = getattr(conn, fn)(fb); break
                except Exception as exc:
                    res["S4_tmsl"]["status"] = f"ERROR {fn}: {exc}"
    if tmsl is not None:
        res["S4_tmsl"].update(status="ok", measures=measures_in(tmsl), tables=tables_in(tmsl))

    osi = None
    if tmsl is not None:
        try:
            from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
            conv = TMSLToOSIConverter()
            for payload in ({"tmsl": tmsl, "dataset_id": model_name}, tmsl):
                try:
                    osi = conv.to_osi(payload); break
                except Exception as exc:
                    err = exc
            if osi is None:
                raise err
            res["S5_osi"].update(status="ok", measures=[(a, b) for a, b, _ in model_metrics(osi)], tables=model_tables(osi))
        except Exception as exc:
            res["S5_osi"]["status"] = f"ERROR: {exc}"
    if osi is not None:
        try:
            from semabridge.converter.osi_to_sml import OSIToSMLConverter
            sml = OSIToSMLConverter().from_osi(osi)
            mm = model_metrics(sml)
            res["S6_sml"].update(status="ok", measures=[(a, b) for a, b, _ in mm], tables=model_tables(sml),
                                 extra={v: x for _, b, x in mm for v in nk_variants(b)})
        except Exception as exc:
            res["S6_sml"]["status"] = f"ERROR: {exc}"
    return res


# ----------------------------------------------------------------------------- S7 DDL
def stage_ddl(path):
    from measure_inventory import ddl_metrics
    return {nk(v["name"]): ("NULL" if v["null"] else "REAL") for v in ddl_metrics(open(path, encoding="utf-8", errors="ignore").read()).values()}


# ----------------------------------------------------------------------------- one model
def diagnose(pbix, ddl=None, skip_semabridge=False):
    model = re.sub(r"^[0-9a-f]{32}_", "", os.path.splitext(os.path.basename(pbix))[0])
    raw, kind, s2, t2 = stage_raw(pbix)
    sb = {} if skip_semabridge else semabridge_stages(pbix, model)
    d7 = stage_ddl(ddl) if ddl and os.path.exists(ddl) else None

    def present(stage, table, name):
        st = sb.get(stage)
        if not st or st["measures"] is None:
            return None                                   # stage not available
        names_any = set().union(*[nk_variants(b) for _, b in st["measures"]]) if st["measures"] else set()
        return bool(nk_variants(name) & names_any)

    rows = []
    for r in raw:
        t, n = r["table"], r["measure"]
        st = {"S1_raw": True, "S2_pbixray": (nk(t), nk(n)) in s2}
        for s in ("S3_fallback", "S4_tmsl", "S5_osi", "S6_sml"):
            st[s] = present(s, t, n)
        st["S7_ddl"] = None if d7 is None else d7.get(nk(n), False)
        first = next((s for s in STAGES[1:] if st[s] is False), "")
        ex = (sb.get("S6_sml") or {}).get("extra", {})
        sml_x = next((ex[v] for v in nk_variants(n) if v in ex), {})
        rows.append({
            "model": model, "table": t, "measure": n, "table_kind": kind.get(t, "?"),
            "hidden_measure": "yes" if r["hidden"] else "", "has_expression": "yes" if r["expr"] else "NO",
            **{s: ("" if st[s] is None else ("yes" if st[s] is True else (st[s] if isinstance(st[s], str) else "NO"))) for s in STAGES},
            "first_lost_stage": first, "lost_where": STAGE_TXT.get(first, ""),
            "sml_sync_enabled": str(sml_x.get("sync_enabled", "")) if sml_x else "",
            "sml_has_sql": ("yes" if sml_x.get("sql_expression") else "NO") if sml_x and "sql_expression" in sml_x else "",
            "expression": r["expr"][:300],
        })

    # tables through the stages
    real_tables = {nk(t): t for t, k in kind.items() if "auto-date" not in k}
    trows = []
    for key_, tname in sorted(real_tables.items(), key=lambda x: x[1].lower()):
        tr = {"model": model, "table": tname, "table_kind": kind[tname], "measures": sum(1 for r in raw if r["table"] == tname),
              "S1_raw": "yes", "S2_pbixray": "yes" if key_ in t2 else "NO"}
        for s in ("S3_fallback", "S4_tmsl", "S5_osi", "S6_sml"):
            tabs = (sb.get(s) or {}).get("tables")
            tr[s] = "" if tabs is None else ("yes" if key_ in tabs else "NO")
        trows.append(tr)
    status = {s: (sb.get(s) or {}).get("status", "skipped") for s in ("S3_fallback", "S4_tmsl", "S5_osi", "S6_sml")}
    return model, rows, trows, status


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbix", action="append", default=[], help=".pbix file (repeatable)")
    ap.add_argument("--pbix-dir", help="folder: run every .pbix in it")
    ap.add_argument("--ddl", help="generated DDL for S7 (single --pbix only)")
    ap.add_argument("--no-semabridge", action="store_true", help="only S1/S2 (no SemaBridge import)")
    ap.add_argument("--out", default="measure_stage_results")
    args = ap.parse_args()
    files = list(args.pbix) + (sorted(glob.glob(os.path.join(args.pbix_dir, "*.pbix"))) if args.pbix_dir else [])
    if not files:
        ap.error("give --pbix or --pbix-dir")
    os.makedirs(args.out, exist_ok=True)

    all_rows, all_tables, summary = [], [], []
    for f in files:
        try:
            model, rows, trows, status = diagnose(f, args.ddl if len(files) == 1 else None, args.no_semabridge)
        except Exception as exc:
            print(f"\n=== {os.path.basename(f)}: FAILED ({exc})"); traceback.print_exc(limit=1); continue
        all_rows += rows; all_tables += trows
        cnt = {s: sum(1 for r in rows if r[s] == "yes") for s in STAGES}
        lost = Counter(r["first_lost_stage"] for r in rows if r["first_lost_stage"])
        summary.append({"model": model, "S1_raw_measures": len(rows),
                        **{f"{s}_present": (cnt[s] if any(r[s] != "" for r in rows) else "") for s in STAGES[1:]},
                        **{f"lost_at_{s}": lost.get(s, 0) for s in STAGES[1:]},
                        **{f"status_{s}": v for s, v in status.items()}})
        print(f"\n=== {model}: {len(rows)} measures in the model")
        for s in STAGES[1:]:
            if any(r[s] != "" for r in rows):
                print(f"    {s:12} present {cnt[s]:4}   lost here {lost.get(s, 0):3}   {STAGE_TXT.get(s, '') if lost.get(s) else ''}")
            else:
                st = status.get(s, "not run (no --ddl)" if s == "S7_ddl" else "")
                print(f"    {s:12} -- {st}")
        for r in rows:
            if r["first_lost_stage"] and r["first_lost_stage"] != "S7_ddl":
                print(f"    LOST at {r['first_lost_stage']:11} {r['table']}[{r['measure']}]   table: {r['table_kind']}"
                      f"{'' if r['has_expression'] == 'yes' else '   (no expression)'}")
        for t in trows:
            gone = [s for s in ("S2_pbixray", "S3_fallback", "S4_tmsl", "S5_osi", "S6_sml") if t[s] == "NO"]
            if gone and t["measures"]:
                print(f"    TABLE '{t['table']}' ({t['table_kind']}, {t['measures']} measures) missing from: {', '.join(gone)}")

    def write(name, rows):
        if not rows:
            return
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with open(os.path.join(args.out, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(rows)
    write("measure_stages.csv", all_rows)
    write("table_stages.csv", all_tables)
    write("stage_summary.csv", summary)
    tot = Counter(r["first_lost_stage"] for r in all_rows if r["first_lost_stage"])
    print(f"\nTOTAL {len(all_rows)} measures. First lost at: " + (", ".join(f"{k} {v}" for k, v in sorted(tot.items())) or "nowhere"))
    print(f"Written to {os.path.abspath(args.out)}: measure_stages.csv, table_stages.csv, stage_summary.csv")


if __name__ == "__main__":
    main()
