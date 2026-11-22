"""Reference implementation of the Ask AI SQL guard contract (metadata-driven, no model names).

Not production code: it exists to prove the 50 tests are consistent and achievable. The product
implementation (semabridge.ai.sql_validator) must satisfy the same tests.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

TODAY_FUNCS = re.compile(r"\b(CURRENT_DATE|CURRENT_TIMESTAMP|GETDATE|SYSDATE|NOW|LOCALTIMESTAMP)\b(\s*\(\s*\))?", re.I)
TODAY_WORDS = re.compile(r"\b(today|now|current date|this moment|as of today)\b", re.I)
WRITE_START = re.compile(r"^\s*(insert|update|delete|merge|drop|create|alter|truncate|grant|revoke|call|copy|put|use)\b", re.I)


# ---------------------------------------------------------------- synonyms / metric choice

def _norm_term(t: str) -> str:
    words = re.findall(r"[a-z0-9]+", (t or "").lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def find_synonym_collisions(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    owners: Dict[str, List[str]] = {}
    for m in model["metrics"]:
        for s in m.get("synonyms") or []:
            key = _norm_term(s)
            if key and m["name"] not in owners.setdefault(key, []):
                owners[key].append(m["name"])
    return [{"term": t, "metrics": sorted(ms)} for t, ms in sorted(owners.items()) if len(ms) > 1]


def _is_base_unfiltered(m):
    return m.get("kind", "base") == "base" and not m.get("filter")


def assign_synonyms(model: Dict[str, Any]) -> Dict[str, List[str]]:
    out = {m["name"]: list(dict.fromkeys(m.get("synonyms") or [])) for m in model["metrics"]}
    by_name = {m["name"]: m for m in model["metrics"]}
    for col in find_synonym_collisions(model):
        unfiltered = [n for n in col["metrics"] if _is_base_unfiltered(by_name[n])]
        keep = unfiltered[0] if len(unfiltered) == 1 else None
        for n in col["metrics"]:
            if n == keep:
                continue
            out[n] = [s for s in out[n] if _norm_term(s) != col["term"]]
            label = by_name[n].get("label")
            if label and _norm_term(label) not in {_norm_term(s) for s in out[n]}:
                out[n].append(label.lower())
    return out


def resolve_metric(question: str, model: Dict[str, Any]) -> Dict[str, Any]:
    q = f" {_norm_term(question)} "
    syn = assign_synonyms(model)
    hits = []
    for m in model["metrics"]:
        for s in syn[m["name"]] + [m.get("label") or ""]:
            t = _norm_term(s)
            if t and f" {t} " in q:
                hits.append((len(t.split()), 1 if _is_base_unfiltered(m) else 0, m["name"]))
    if not hits:
        return {"metric": None, "alternatives": []}
    hits.sort(reverse=True)
    best = hits[0][2]
    raw = f" {_norm_term(question)} "
    alts = sorted({m["name"] for m in model["metrics"] if m["name"] != best
                   and any(f" {_norm_term(s)} " in raw for s in (m.get("synonyms") or []) if _norm_term(s))})
    return {"metric": best, "alternatives": alts}


# ---------------------------------------------------------------- helpers

def _strip_literals(sql: str) -> str:
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    sql = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)
    return sql


def _metric(model, name):
    for m in model["metrics"]:
        if m["name"].lower() == (name or "").lower():
            return m
    return None


def _governed_parts(sql: str):
    body = _strip_literals(sql)
    mm = re.search(r"\bMETRICS\s+(.+?)(?=\bDIMENSIONS\b|\bWHERE\b|\)\s*(?:$|\)|\bAS\b|;|\bORDER\b|\bLIMIT\b|\bWHERE\b)|$)", body, re.I | re.S)
    dm = re.search(r"\bDIMENSIONS\s+(.+?)(?=\bMETRICS\b|\bWHERE\b|\)\s*(?:$|\)|\bAS\b|;|\bORDER\b|\bLIMIT\b)|$)", body, re.I | re.S)
    split = lambda s: [x.strip().split(".")[-1] for x in (s or "").split(",") if x.strip()]
    full = lambda s: [x.strip() for x in (s or "").split(",") if x.strip()]
    return split(mm.group(1) if mm else ""), full(dm.group(1) if dm else "")


def _semantic_view_sql(view, metrics, dims):
    s = f"SELECT * FROM SEMANTIC_VIEW({view} METRICS {', '.join(metrics)}"
    if dims:
        s += f" DIMENSIONS {', '.join(dims)}"
    return s + ")"


def _result(action, path, sql, flags, assumptions=None, unmapped=None, suggestion=None):
    flags = sorted(set(flags))
    return {"action": action, "path": path, "sql": sql, "flags": flags,
            "assumptions": assumptions or [], "unmapped": unmapped or [], "suggestion": suggestion,
            "certifiable": action == "execute" and path == "governed" and not flags}


def _today_flag(sql, model, question):
    if not model.get("data_anchored"):
        return False
    if question and TODAY_WORDS.search(question):
        return False
    return bool(TODAY_FUNCS.search(_strip_literals(sql)))


def _period_metric(model, base_name):
    for m in model["metrics"]:
        if m.get("kind") == "period" and (m.get("base_metric") or "").lower() == (base_name or "").lower():
            return m
    return None


def _helper_flag(model, names):
    flags = []
    for n in names:
        m = _metric(model, n)
        if m and m.get("kind") == "helper":
            if any(o.get("kind") == "base" and o["table"] == m["table"] and o["agg"] == m["agg"]
                   and (o.get("column") or "") == (m.get("column") or "") and not o.get("filter")
                   for o in model["metrics"]):
                flags.append("HELPER_METRIC")
    return flags


def _norm_pred(p: str) -> str:
    p = re.sub(r"\b\w+\.(?=\w)", "", p or "")
    return re.sub(r"\s+", " ", p).strip().lower()


# ---------------------------------------------------------------- validate_sql

def validate_sql(sql: str, model: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    text = (sql or "").strip()
    if not text:
        return _result("refuse", "none", "", ["EMPTY_SQL"])
    stmts = [s for s in sqlglot.parse(text, read="snowflake", error_level=sqlglot.ErrorLevel.IGNORE) if s is not None] \
        if not re.search(r"\bSEMANTIC_VIEW\s*\(", text, re.I) else [None]
    plain = _strip_literals(text)
    if len([s for s in re.split(r";\s*", plain.strip()) if s.strip()]) > 1:
        return _result("refuse", "none", text, ["MULTI_STATEMENT"])
    if WRITE_START.search(plain):
        return _result("refuse", "none", text, ["WRITE_STATEMENT"])

    # ---- governed SQL
    if re.search(r"\bSEMANTIC_VIEW\s*\(", text, re.I):
        metrics, dims = _governed_parts(text)
        flags = []
        unknown = [m for m in metrics if not _metric(model, m)]
        if unknown:
            return _result("refuse", "governed", text, ["UNKNOWN_METRIC"], unmapped=unknown)
        flags += _helper_flag(model, metrics)
        if _today_flag(text, model, question):
            periods = [_period_metric(model, m) for m in metrics]
            if metrics and all(periods):
                new_metrics = metrics + [p["name"] for p in periods]
                return _result("rewrite", "rewritten", _semantic_view_sql(model["view"], new_metrics, dims),
                               flags + ["CURRENT_DATE_ANCHOR"])
            return _result("refuse", "governed", text, flags + ["CURRENT_DATE_ANCHOR"],
                           suggestion="Use the model's period metrics; data ends " + str(model.get("latest_data_date")))
        return _result("execute", "governed", text, flags)

    # ---- free-form SQL
    tree = stmts[0] if stmts else None
    if tree is None:
        return _result("refuse", "free_form", text, ["UNPARSEABLE"])
    flags, assumptions, unmapped = ["FREE_FORM"], [], []
    if _today_flag(text, model, question):
        flags.append("CURRENT_DATE_ANCHOR")
    tables = {t.name.upper(): t for t in tree.find_all(exp.Table)}
    alias_to_table = {}
    for t in tree.find_all(exp.Table):
        alias_to_table[(t.alias or t.name).upper()] = t.name.upper()
    ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    real_tables = {n for n in tables if n not in ctes}
    for c in tree.find_all(exp.CTE):
        for t in c.find_all(exp.Table):
            alias_to_table[c.alias_or_name.upper()] = t.name.upper()
    if any(n.endswith("_ENRICHED") for n in real_tables):
        flags.append("ENRICHED_TABLE")

    meta_tables = model["tables"]
    snapshot_tables = {n for n, t in meta_tables.items() if t.get("grain") == "snapshot"} | \
        {m["table"] for m in model["metrics"] if m.get("non_additive_by")}
    fact_tables = [n for n in real_tables if meta_tables.get(n, {}).get("grain") in ("event", "snapshot")]
    if len(set(fact_tables)) >= 2:
        flags.append("FAN_OUT")

    # joins
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            cols = [c for c in eq.find_all(exp.Column)]
            if len(cols) != 2:
                continue
            right_alias = (j.this.alias_or_name or "").upper()
            right_table = alias_to_table.get(right_alias, right_alias)
            right_col = next((c.name.upper() for c in cols if (c.table or "").upper() == right_alias), cols[1].name.upper())
            u = model.get("key_uniqueness", {}).get(f"{right_table}.{right_col}")
            if not u or u["rows"] != u["distinct"]:
                flags.append("NON_UNIQUE_JOIN")

    # aggregations / grain
    aggs = list(tree.find_all(exp.AggFunc))
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    where_sql = (sel.args.get("where").sql() if sel and sel.args.get("where") else "").upper()
    has_qualify = bool(tree.find(exp.Qualify))
    for n in real_tables:
        t = meta_tables.get(n, {})
        if n in snapshot_tables and aggs:
            sd, ek = (t.get("snapshot_date") or "").upper(), (t.get("entity_key") or "").upper()
            distinct_entity = any(isinstance(a, exp.Count) and a.find(exp.Distinct) and ek and ek in a.sql().upper() for a in aggs)
            if not (has_qualify or (sd and re.search(rf"\b{sd}\b", where_sql)) or distinct_entity):
                flags.append("SNAPSHOT_GRAIN")

    # definitions: conditions in WHERE and CASE WHEN
    def _conjuncts(node):
        if isinstance(node, exp.And):
            return _conjuncts(node.left) + _conjuncts(node.right)
        return [node]

    metric_filters = {_norm_pred(m["filter"]): m["name"] for m in model["metrics"] if m.get("filter")}
    dim_cols = {d["column"].upper() for d in model["dimensions"]}
    snap_cols = {(t.get("snapshot_date") or "").upper() for t in meta_tables.values() if t.get("snapshot_date")}
    conds = []
    if sel and sel.args.get("where"):
        conds += [("where", c) for c in _conjuncts(sel.args["where"].this)]
    for case in tree.find_all(exp.If):
        conds.append(("case", case.this))
    for kind, c in conds:
        key = _norm_pred(c.sql(dialect="snowflake"))
        if not key:
            continue
        if key in metric_filters:
            assumptions.append(f"condition matches the model's definition of {metric_filters[key]}")
            continue
        cols = {col.name.upper() for col in c.find_all(exp.Column)}
        if kind == "where" and cols and cols <= (snap_cols | dim_cols):
            continue  # period filter or a filter on a model dimension
        flags.append("GUESSED_RULE")
        assumptions.append(f"assumed rule: {c.sql(dialect='snowflake')}")

    # governed rewrite attempt
    mapped_metrics, ok = [], True
    for a in aggs:
        col = a.find(exp.Column)
        agg = type(a).__name__.upper()
        agg = {"COUNT": "COUNT", "AVG": "AVG", "SUM": "SUM", "MIN": "MIN", "MAX": "MAX"}.get(agg, agg)
        tname = alias_to_table.get((col.table or "").upper(), next(iter(real_tables), "")) if col else next(iter(real_tables), "")
        cname = col.name.upper() if col else None
        hit = next((m for m in model["metrics"] if m["agg"] == agg and (m.get("column") or None) == cname
                    and m["table"] == tname.replace("_ENRICHED", "") and not m.get("filter") and m.get("kind") == "base"), None)
        if hit:
            mapped_metrics.append(hit["name"])
        else:
            ok = False
            unmapped.append(a.sql())
    group = sel.args.get("group") if sel else None
    dims = []
    for g in (group.expressions if group else []):
        col = g if isinstance(g, exp.Column) else g.find(exp.Column)
        d = next((d for d in model["dimensions"] if col and d["column"] == col.name.upper()), None)
        if d and isinstance(g, exp.Column):
            dims.append(d["name"])
        else:
            ok = False
            unmapped.append(g.sql())
    rewritable = set(flags) <= {"FREE_FORM", "ENRICHED_TABLE", "SNAPSHOT_GRAIN"}
    if ok and mapped_metrics and not conds and rewritable:
        return _result("rewrite", "rewritten", _semantic_view_sql(model["view"], mapped_metrics, dims), [], assumptions)
    action = "refuse" if ("CURRENT_DATE_ANCHOR" in flags or "FAN_OUT" in flags) else "execute"
    return _result(action, "free_form", text, flags, assumptions, unmapped)


# ---------------------------------------------------------------- governed status dimensions

def status_dimensions(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for m in model["metrics"]:
        pred = m.get("filter")
        if not pred:
            continue
        try:
            node = sqlglot.parse_one(pred, read="snowflake")
        except Exception:
            continue
        if node.find(exp.AggFunc):
            continue
        cols = list(node.find_all(exp.Column))
        table_cols = set(model["tables"].get(m["table"], {}).get("columns", []))
        if not cols or any((c.table and c.table.upper() != m["table"]) or c.name.upper() not in table_cols for c in cols):
            continue
        out.append({"name": f"IS_{m['name']}", "table": m["table"], "predicate": pred})
    return out


# ---------------------------------------------------------------- verified queries

def verified_queries_from_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for c in cases:
        if str(c.get("status", "")).lower() != "certified" or not c.get("power_bi_match"):
            continue
        key = re.sub(r"\s+", " ", (c.get("question") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"question": c["question"], "sql": c["sql"]})
    return out
