"""Ontology extraction coverage audit (READ-ONLY).

Compares, field by field, what each layer holds for one model:

    PBIX (optional, ground truth via pbixray)  ->  SML model.yaml  ->  OSI json (optional)  ->  ontology YAML

and classifies every gap so we know WHERE it belongs:

    OK                    preserved
    LOST_BEFORE_SML       in the PBIX, not in SML                    -> extraction pipeline gap
    LOST_IN_ONTOLOGY      in SML/OSI, ontology has a field but it is empty/different -> generator gap
    NO_ONTOLOGY_FIELD     in SML/OSI, ontology schema has nowhere to keep it        -> schema gap
    MISLABELED            kept, but in the wrong field / wrong meaning
    AUTO_ONLY             only auto-generated values (e.g. synonyms = the name itself)
    DERIVABLE             not filled, but could be derived from the existing model
    NOT_IN_SOURCE         not in any technical source                -> business / governance input
    N/A                   not applicable to this object

Nothing is generated, saved or sent anywhere. No model or measure names are hard-coded.

Usage (repo root):
    uv run python ontology_tests/ontology_extraction_coverage.py \
        --sml output/reverse/<project>/sml/model.yaml \
        --ontology Config/ontologies/<domain_id>.ontology.yaml \
        [--osi output/inferred/<project>/osi_inferred.json] \
        [--pbix "<model>.pbix"] [--out ontology_tests/out/coverage]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

TECH, CALC, BUSINESS, GOV, ANALYTIC = "technical", "calculation", "business", "governance", "analytical"
OK, LOST_SML, LOST_ONT, NO_FIELD, MISLABEL, AUTO, DERIV, NOT_SRC, NA = (
    "OK", "LOST_BEFORE_SML", "LOST_IN_ONTOLOGY", "NO_ONTOLOGY_FIELD", "MISLABELED",
    "AUTO_ONLY", "DERIVABLE", "NOT_IN_SOURCE", "N/A")
WHERE = {
    LOST_SML: "extraction pipeline (PBIX -> SML)",
    LOST_ONT: "ontology generator (from_osi_model)",
    NO_FIELD: "ontology schema (models.py)",
    MISLABEL: "ontology generator / schema",
    AUTO: "business input (only auto values today)",
    DERIV: "ontology generator (derive from model)",
    NOT_SRC: "business / governance input",
}

rows: List[Dict[str, Any]] = []


def has(v: Any) -> bool:
    if isinstance(v, float) and v != v:  # NaN (pandas empty cell)
        return False
    return v not in (None, "", [], {}, False)


def short(v: Any, n: int = 90) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    s = " ".join(str(s).split())
    return s[:n] + ("..." if len(s) > n else "")


def norm(s: Any) -> str:
    return " ".join(str(s or "").split()).casefold()


def add(obj_type, obj, field, category, status, pbix=None, sml=None, osi=None, ont=None, note=""):
    rows.append({
        "object_type": obj_type, "object": obj, "field": field, "category": category, "status": status,
        "where_to_fix": WHERE.get(status, ""), "pbix": short(pbix) if pbix is not None else "",
        "sml": short(sml) if sml is not None else "", "osi": short(osi) if osi is not None else "",
        "ontology": short(ont) if ont is not None else "", "note": note,
    })


def by(items: Iterable[dict], key: str) -> Dict[str, dict]:
    return {norm(i.get(key)): i for i in items or [] if i.get(key)}


def real_synonyms(item: dict) -> List[str]:
    """Synonyms that are not just auto-generated copies of the name."""
    src = item.get("synonym_sources") or {}
    name = norm(item.get("unique_name") or item.get("name"))
    out = []
    for s in item.get("synonyms") or []:
        if src.get(s) == "auto_generated" or norm(s) == name:
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------- loaders
def load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_pbix(p: Path) -> Optional[dict]:
    try:
        from pbixray import PBIXRay
    except ImportError:
        print("  pbixray not installed: PBIX layer skipped")
        return None
    px = PBIXRay(str(p))
    out: Dict[str, Any] = {"measures": {}, "relationships": [], "columns": {}}
    m = px.dax_measures
    for _, r in m.iterrows():
        out["measures"][norm(r.get("Name"))] = {
            "name": r.get("Name"), "table": r.get("TableName"), "expression": r.get("Expression"),
            "description": r.get("Description") if "Description" in m.columns else None,
            "folder": r.get("DisplayFolder") if "DisplayFolder" in m.columns else None,
        }
    rel = px.relationships
    for _, r in rel.iterrows():
        if "LocalDate" in f"{r.get('FromTableName')}{r.get('ToTableName')}":
            continue
        out["relationships"].append({
            "from": r.get("FromTableName"), "from_col": r.get("FromColumnName"),
            "to": r.get("ToTableName"), "to_col": r.get("ToColumnName"),
            "active": bool(r.get("IsActive")), "cross": r.get("CrossFilteringBehavior"),
            "card": r.get("Cardinality"),
        })
    try:
        cols = px.schema
        for _, r in cols.iterrows():
            out["columns"].setdefault(norm(r.get("TableName")), set()).add(norm(r.get("ColumnName")))
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- audits
CARD = {"many-to-one": "N:1", "one-to-many": "1:N", "one-to-one": "1:1", "many-to-many": "N:N",
        "many_to_one": "N:1", "one_to_many": "1:N", "one_to_one": "1:1", "many_to_many": "N:N"}
TYPE = {"string": "VARCHAR", "text": "VARCHAR", "integer": "NUMBER", "int64": "NUMBER", "number": "NUMBER",
        "decimal": "NUMBER", "double": "FLOAT", "float": "FLOAT", "boolean": "BOOLEAN", "date": "DATE",
        "datetime": "TIMESTAMP", "timestamp": "TIMESTAMP", "time": "TIME"}


def audit_domain(ont: dict) -> None:
    for f, cat in (("description", BUSINESS), ("owner", GOV)):
        add("domain", ont.get("domain"), f, cat, OK if has(ont.get(f)) else NOT_SRC, ont=ont.get(f))


def audit_datasets(sml: dict, osi: Optional[dict], ont: dict) -> None:
    concepts = by(ont.get("concepts"), "name")
    osi_ds = by((osi or {}).get("datasets"), "unique_name")
    for ds in sml.get("datasets") or []:
        n = ds["unique_name"]
        c = concepts.get(norm(n))
        if not c:
            add("dataset", n, "exists", TECH, LOST_ONT, sml=n, note="no concept for this dataset")
            continue
        add("dataset", n, "exists", TECH, OK, sml=n, ont=c["name"])
        add("dataset", n, "physical_table", TECH,
            OK if has(c.get("physical_table")) else LOST_ONT, sml=ds.get("source_table") or n, ont=c.get("physical_table"))
        is_fact = ds.get("is_fact")
        ct = str(c.get("concept_type") or "")
        if is_fact is None:
            add("dataset", n, "fact/dimension role", ANALYTIC, DERIV, ont=ct,
                note="SML is_fact empty; role derivable from relationships (many side) and measures")
        else:
            want = "FACT" if is_fact else "DIMENSION"
            add("dataset", n, "fact/dimension role", ANALYTIC, OK if want in ct.upper() else MISLABEL,
                sml=want, ont=ct)
        add("dataset", n, "is_hidden", TECH, NO_FIELD if ds.get("is_hidden") else NA, sml=ds.get("is_hidden"))
        add("dataset", n, "description", BUSINESS,
            OK if has(c.get("description")) else (LOST_ONT if has(ds.get("description")) else NOT_SRC),
            sml=ds.get("description"), ont=c.get("description"))
        rs = real_synonyms(ds)
        add("dataset", n, "synonyms", BUSINESS,
            OK if rs and all(norm(s) in map(norm, c.get("synonyms") or []) for s in rs) else (LOST_ONT if rs else
            (AUTO if ds.get("synonyms") else NOT_SRC)), sml=ds.get("synonyms"), ont=c.get("synonyms"))
        for f, cat in (("business_rules", BUSINESS), ("data_steward", GOV)):
            add("dataset", n, f, cat, OK if has(c.get(f)) else NOT_SRC, ont=c.get(f))
        audit_columns(n, ds, osi_ds.get(norm(n)), c)


def audit_columns(ds_name: str, ds: dict, osi_ds: Optional[dict], concept: dict) -> None:
    attrs = by(concept.get("attributes"), "name")
    for col in ds.get("columns") or []:
        n = f'{ds_name}.{col["unique_name"]}'
        a = attrs.get(norm(col["unique_name"]))
        if not a:
            add("column", n, "exists", TECH, LOST_ONT, sml=col["unique_name"])
            continue
        add("column", n, "exists", TECH, OK)
        st = str(col.get("data_type") or "").lower()
        want = TYPE.get(st, st.upper())
        got = str(a.get("data_type") or "").upper()
        same = (not st or got == want or got == st.upper()
                or (want == "TIMESTAMP" and got.startswith("TIMESTAMP")) or (want == "NUMBER" and got in ("NUMBER", "INTEGER", "BIGINT"))
                or (want in ("FLOAT", "NUMBER") and (got.startswith("DECIMAL") or got.startswith("NUMBER") or got in ("FLOAT", "DOUBLE")))
                or (st == "decimal" and got.startswith(("DECIMAL", "NUMBER"))))
        add("column", n, "data_type", TECH, OK if same else MISLABEL,
            sml=st, ont=got, note="" if same else "type changed on the way into the ontology")
        add("column", n, "is_key", TECH,
            (OK if a.get("is_primary_key") else LOST_ONT) if col.get("is_key") else NA, sml=col.get("is_key"),
            ont=a.get("is_primary_key"))
        for f in ("is_hidden", "format_string", "folder", "data_category", "sort_by_column"):
            add("column", n, f, TECH, NO_FIELD if has(col.get(f)) else NA, sml=col.get(f))
        add("column", n, "calculated column expression", CALC,
            NO_FIELD if col.get("is_calculated") and has(col.get("expression")) else NA, sml=col.get("expression"))
        add("column", n, "description", BUSINESS,
            OK if has(a.get("description")) else (LOST_ONT if has(col.get("description")) else NOT_SRC),
            sml=col.get("description"), ont=a.get("description"))
        rs = real_synonyms(col)
        add("column", n, "synonyms", BUSINESS,
            OK if rs and all(norm(s) in map(norm, a.get("synonyms") or []) for s in rs) else (LOST_ONT if rs else
            (AUTO if col.get("synonyms") else NOT_SRC)), sml=col.get("synonyms"), ont=a.get("synonyms"))


def audit_metrics(sml: dict, osi: Optional[dict], ont: dict, pbix: Optional[dict]) -> None:
    om = by(ont.get("metrics"), "name")
    osi_m = by((osi or {}).get("metrics"), "unique_name")
    for m in sml.get("metrics") or []:
        n = m["unique_name"]
        t = om.get(norm(n))
        p = (pbix or {}).get("measures", {}).get(norm(m.get("original_name") or n)) if pbix else None
        if pbix is not None:
            add("metric", n, "in PBIX", TECH, OK if p else NA, pbix=bool(p), note="" if p else "SML metric not found in PBIX by name")
        if not t:
            add("metric", n, "exists", TECH, LOST_ONT, sml=n)
            continue
        add("metric", n, "exists", TECH, OK)
        dax, sql, ont_expr = m.get("expression"), m.get("sql_expression"), t.get("sql_expression")
        if p is not None:
            add("metric", n, "DAX expression (PBIX -> SML)", CALC,
                OK if norm(p.get("expression")) == norm(dax) else LOST_SML, pbix=p.get("expression"), sml=dax)
        add("metric", n, "DAX expression (original)", CALC,
            MISLABEL if has(dax) and norm(ont_expr) == norm(dax) else (OK if not has(dax) else NO_FIELD),
            sml=dax, ont=ont_expr,
            note="DAX is stored in ontology 'sql_expression' (no DAX field); SQL consumers would get DAX"
            if has(dax) and norm(ont_expr) == norm(dax) else "")
        add("metric", n, "translated SQL expression", CALC,
            OK if has(sql) and norm(ont_expr) == norm(sql) else (LOST_ONT if has(sql) else
            (NA if m.get("is_placeholder") or not m.get("sync_enabled", True) else NOT_SRC)),
            sml=sql, ont=ont_expr, note="" if has(sql) else "no SQL in SML (not translated / placeholder)")
        agg = str(m.get("aggregation") or "").lower()
        ont_agg = str(t.get("aggregation_type") or "").upper()
        add("metric", n, "aggregation", ANALYTIC,
            MISLABEL if agg in ("none", "", "custom") and ont_agg in ("SUM",) else OK, sml=agg, ont=ont_agg,
            note="calculated measure labelled SUM in the ontology" if agg in ("none", "", "custom") and ont_agg == "SUM" else "")
        grain_src = m.get("non_additive_dimension") or m.get("group_by_dimensions") or m.get("partition_dimension")
        add("metric", n, "grain", ANALYTIC,
            OK if has(t.get("grain")) else (DERIV if has(grain_src) else NOT_SRC), sml=grain_src, ont=t.get("grain"),
            note="grain available in SML (non-additive / group-by) but not copied" if has(grain_src) and not has(t.get("grain")) else "")
        add("metric", n, "home table (concept)", TECH,
            OK if norm(t.get("concept")) == norm(m.get("dataset")) else LOST_ONT, sml=m.get("dataset"), ont=t.get("concept"))
        for f in ("depends_on_measures", "format_string", "folder", "is_hidden", "non_additive_dimension",
                  "sync_failure_reason", "complexity_tier"):
            v = m.get(f)
            add("metric", n, f, CALC if f in ("depends_on_measures", "non_additive_dimension", "sync_failure_reason") else TECH,
                NO_FIELD if has(v) else NA, sml=v)
        if p is not None:
            for f in ("description", "folder"):
                pv, sv = p.get(f), m.get(f if f != "folder" else "folder")
                if has(pv) and not has(sv):
                    add("metric", n, f"{f} (PBIX -> SML)", BUSINESS if f == "description" else TECH, LOST_SML, pbix=pv, sml=sv)
        add("metric", n, "description", BUSINESS,
            OK if has(t.get("description")) else (LOST_ONT if has(m.get("description")) else NOT_SRC),
            sml=m.get("description"), ont=t.get("description"))
        rs = real_synonyms(m)
        add("metric", n, "synonyms", BUSINESS,
            OK if rs and all(norm(s) in map(norm, t.get("synonyms") or []) for s in rs) else (LOST_ONT if rs else
            (AUTO if m.get("synonyms") else NOT_SRC)), sml=m.get("synonyms"), ont=t.get("synonyms"))
        add("metric", n, "business_rules", BUSINESS, OK if has(t.get("business_rules")) else NOT_SRC, ont=t.get("business_rules"))
        owner = (osi_m.get(norm(n)) or {}).get("business_owner")
        add("metric", n, "data_steward / owner", GOV,
            OK if has(t.get("data_steward")) else (LOST_ONT if has(owner) else NOT_SRC), osi=owner, ont=t.get("data_steward"))
        add("metric", n, "business domain", GOV, AUTO if has(t.get("domain")) else NOT_SRC, ont=t.get("domain"),
            note="filled with the generated domain's name, not a business classification (Sales, Support, ...)")


def audit_relationships(sml: dict, osi: Optional[dict], ont: dict, pbix: Optional[dict]) -> None:
    orels = ont.get("relationships") or []
    by_name = by(orels, "name")
    by_pair = defaultdict(list)
    for r in orels:
        by_pair[(norm(r["from_concept"]), norm(r["to_concept"]))].append(r)
    osi_r = by((osi or {}).get("relationships"), "unique_name")
    pairs = Counter((norm(r["from_dataset"]), norm(r["to_dataset"])) for r in sml.get("relationships") or [])
    for r in sml.get("relationships") or []:
        n = r["unique_name"]
        t = by_name.get(norm(n))
        pair = (norm(r["from_dataset"]), norm(r["to_dataset"]))
        if not t:
            collapsed = pairs[pair] > 1 and by_pair.get(pair)
            add("relationship", n, "exists", TECH, LOST_ONT, sml=f'{r["from_dataset"]} -> {r["to_dataset"]}',
                note="collapsed: another relationship between the same tables was kept" if collapsed else "")
            continue
        add("relationship", n, "exists", TECH, OK)
        want = CARD.get(str(r.get("cardinality") or "").lower(), str(r.get("cardinality") or ""))
        got = str(t.get("cardinality") or "")
        add("relationship", n, "cardinality", TECH, OK if got == want or got.lower() == want.lower() else MISLABEL,
            sml=r.get("cardinality"), ont=got)
        cols_ok = all(norm(c) in norm(t.get("join_expression")) for c in (r.get("from_columns") or []) + (r.get("to_columns") or []))
        add("relationship", n, "join columns", TECH, OK if cols_ok else LOST_ONT,
            sml=f'{r.get("from_columns")} = {r.get("to_columns")}', ont=t.get("join_expression"))
        cf = r.get("cross_filter") or (osi_r.get(norm(n)) or {}).get("cross_filter_direction")
        add("relationship", n, "cross-filter direction", TECH, NO_FIELD if has(cf) else NA, sml=cf,
            note="needed for 'both' filters; single is the default" if str(cf).lower() == "both" else "")
        add("relationship", n, "is_active", TECH, NO_FIELD if r.get("is_active") is False else NA, sml=r.get("is_active"),
            note="inactive (USERELATIONSHIP) relationship looks active in the ontology" if r.get("is_active") is False else "")
        add("relationship", n, "business meaning (predicate)", BUSINESS,
            OK if has(t.get("predicate_label")) else NOT_SRC, ont=t.get("predicate_label"))
    if pbix:
        sml_pairs = {(norm(r["from_dataset"]), norm(r["to_dataset"]),
                      norm((r.get("from_columns") or [""])[0])) for r in sml.get("relationships") or []}
        for pr in pbix["relationships"]:
            key = (norm(pr["from"]), norm(pr["to"]), norm(pr["from_col"]))
            add("relationship", f'{pr["from"]}[{pr["from_col"]}] -> {pr["to"]}', "in SML", TECH,
                OK if key in sml_pairs else LOST_SML, pbix=f'active={pr["active"]} cross={pr["cross"]}')


def audit_hierarchies(sml: dict, ont: dict) -> None:
    for d in sml.get("dimensions") or []:
        for h in d.get("hierarchies") or []:
            add("hierarchy", f'{d.get("dataset") or d.get("unique_name")}.{h.get("unique_name")}', "levels", TECH,
                NO_FIELD, sml=[lv.get("unique_name") if isinstance(lv, dict) else lv for lv in h.get("levels") or []],
                note="ontology concepts have no hierarchy field")


# --------------------------------------------------------------------------- output
def write_outputs(out: Path, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "extraction_coverage_report.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=2), encoding="utf-8")
    with open(out / "extraction_coverage_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    relevant = [r for r in rows if r["status"] != NA]
    by_field = defaultdict(Counter)
    for r in relevant:
        by_field[(r["object_type"], r["field"], r["category"])][r["status"]] += 1
    order = [OK, LOST_SML, LOST_ONT, NO_FIELD, MISLABEL, DERIV, AUTO, NOT_SRC]

    L = [f"# Ontology extraction coverage: {meta['model']}", "",
         f"Sources: SML `{meta['sml']}` · ontology `{meta['ontology']}`"
         + (f" · OSI `{meta['osi']}`" if meta.get("osi") else "") + (f" · PBIX `{meta['pbix']}`" if meta.get("pbix") else ""),
         "", "Counts: " + ", ".join(f"{k} {v}" for k, v in meta["counts"].items()), "",
         "## Where each gap belongs", "", "| Status | Rows | Fix belongs in |", "|---|---|---|"]
    tot = Counter(r["status"] for r in relevant)
    for s in order:
        if tot[s]:
            L.append(f"| {s} | {tot[s]} | {WHERE.get(s, 'nothing to do')} |")
    L += ["", "## Field-by-field", "", "| Object | Field | Category | " + " | ".join(order) + " |",
          "|---|---|---|" + "---|" * len(order)]
    for (ot, fld, cat), c in sorted(by_field.items()):
        L.append(f"| {ot} | {fld} | {cat} | " + " | ".join(str(c[s] or "") for s in order) + " |")

    L += ["", "## Notable findings (first examples)", ""]
    seen = set()
    for r in relevant:
        if r["status"] in (OK, NOT_SRC, AUTO) or not r["note"]:
            continue
        k = (r["object_type"], r["field"], r["note"])
        if k in seen:
            continue
        seen.add(k)
        L.append(f"- **{r['object_type']} `{r['object']}` · {r['field']}** ({r['status']}): {r['note']}"
                 + (f" — SML: `{r['sml']}`" if r["sml"] else "") + (f" — ontology: `{r['ontology']}`" if r["ontology"] else ""))

    L += ["", "## Per-metric cards", ""]
    cards = defaultdict(list)
    for r in rows:
        if r["object_type"] == "metric" and r["status"] != NA:
            cards[r["object"]].append(r)
    for name, rs in cards.items():
        L.append(f"**METRIC: {name}**")
        L.append("```")
        for cat in (CALC, ANALYTIC, TECH, BUSINESS, GOV):
            sub = [r for r in rs if r["category"] == cat]
            if sub:
                L.append(f"  {cat}:")
                L += [f"    {r['field']:<34} {r['status']}" for r in sub]
        L.append("```")
    (out / "extraction_coverage_summary.md").write_text("\n".join(L), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sml", required=True)
    ap.add_argument("--ontology", required=True)
    ap.add_argument("--osi")
    ap.add_argument("--pbix")
    ap.add_argument("--out", default="ontology_tests/out/coverage")
    a = ap.parse_args()

    sml = load_yaml(Path(a.sml))
    ont = load_yaml(Path(a.ontology))
    if "datasets" not in sml:
        raise SystemExit("--sml must be an SML/OSI model file (has 'datasets')")
    if "domain" not in ont:
        raise SystemExit("--ontology must be a domain ontology YAML (has 'domain')")
    osi = json.loads(Path(a.osi).read_text(encoding="utf-8")) if a.osi else None
    pbix = load_pbix(Path(a.pbix)) if a.pbix else None

    audit_domain(ont)
    audit_datasets(sml, osi, ont)
    audit_metrics(sml, osi, ont, pbix)
    audit_relationships(sml, osi, ont, pbix)
    audit_hierarchies(sml, ont)

    counts = {
        "sml_datasets": len(sml.get("datasets") or []),
        "sml_columns": sum(len(d.get("columns") or []) for d in sml.get("datasets") or []),
        "sml_metrics": len(sml.get("metrics") or []),
        "sml_relationships": len(sml.get("relationships") or []),
        "ontology_concepts": len(ont.get("concepts") or []),
        "ontology_attributes": sum(len(c.get("attributes") or []) for c in ont.get("concepts") or []),
        "ontology_metrics": len(ont.get("metrics") or []),
        "ontology_relationships": len(ont.get("relationships") or []),
    }
    if pbix:
        counts["pbix_measures"] = len(pbix["measures"])
        counts["pbix_relationships"] = len(pbix["relationships"])
    meta = {"model": sml.get("label") or sml.get("unique_name"), "sml": a.sml, "ontology": a.ontology,
            "osi": a.osi, "pbix": a.pbix, "counts": counts}
    write_outputs(Path(a.out), meta)
    tot = Counter(r["status"] for r in rows if r["status"] != NA)
    print(f"Counts: {counts}")
    print("Status totals:", dict(tot))
    print(f"Written to {a.out}/extraction_coverage_summary.md (+ .json, .csv)")


if __name__ == "__main__":
    main()
