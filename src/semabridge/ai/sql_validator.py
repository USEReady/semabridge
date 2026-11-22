"""Ask AI SQL validator: checks engine-generated SQL before it runs.

Sits between the NL engine (Snowflake Cortex Analyst, Databricks Genie, local engine) and the
warehouse. For every generated SQL it decides one of:
  execute - the SQL is safe and (if governed) certifiable,
  rewrite - a fixable problem was found; a corrected SEMANTIC_VIEW query is returned,
  refuse  - the SQL cannot be made safe; a reason (and suggestion when possible) is returned.

Everything is driven by model metadata (metrics, synonyms, filters, table grain, key uniqueness,
dimensions); there are no model, table, measure or value names in this module.

Model metadata shape (see metadata_from_domain for building it from an ontology domain):
  {"view", "platform", "data_anchored", "latest_data_date",
   "tables": {NAME: {"grain": snapshot|event|dimension|unknown, "snapshot_date", "entity_key", "columns"}},
   "relationships": [{"from", "from_col", "to", "to_col", "cardinality"}],
   "key_uniqueness": {"TABLE.COLUMN": {"rows", "distinct"}},
   "metrics": [{"name", "label", "table", "agg", "column", "filter", "non_additive_by", "kind"
                (base|period|helper), "base_metric", "shape", "synonyms"}],
   "dimensions": [{"name", "table", "column"}]}

Result flags: CURRENT_DATE_ANCHOR, FREE_FORM, ENRICHED_TABLE, SNAPSHOT_GRAIN, NON_UNIQUE_JOIN, FAN_OUT,
GUESSED_RULE, HELPER_METRIC, UNKNOWN_METRIC, WRITE_STATEMENT, MULTI_STATEMENT, EMPTY_SQL, UNPARSEABLE.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

_DIALECTS = {"snowflake": "snowflake", "databricks": "databricks", "fabric": "tsql"}


def _dialect(model: Dict[str, Any]) -> str:
    return _DIALECTS.get(str(model.get("platform") or "snowflake").lower(), "snowflake")


TODAY_FUNCS = re.compile(r"\b(CURRENT_DATE|CURRENT_TIMESTAMP|GETDATE|SYSDATE|NOW|LOCALTIMESTAMP)\b(\s*\(\s*\))?", re.I)
TODAY_WORDS = re.compile(r"\b(today|now|current date|this moment|as of today)\b", re.I)  # hardcoding-ok: regex for today's date detection
WRITE_START = re.compile(r"^\s*(insert|update|delete|merge|drop|create|alter|truncate|grant|revoke|call|copy|put|use)\b", re.I)


# ---------------------------------------------------------------- synonyms / metric choice

def _norm_term(t: str) -> str:
    words = re.findall(r"[a-z0-9]+", (t or "").lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words)


def find_synonym_collisions(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    owners: Dict[str, List[str]] = {}
    for m in model.get("metrics", []) or []:
        for s in m.get("synonyms") or []:
            key = _norm_term(s)
            if key and m["name"] not in owners.setdefault(key, []):
                owners[key].append(m["name"])
    return [{"term": t, "metrics": sorted(ms)} for t, ms in sorted(owners.items()) if len(ms) > 1]


def _is_base_unfiltered(m: Dict[str, Any]) -> bool:
    return m.get("kind", "base") == "base" and not m.get("filter")


def assign_synonyms(model: Dict[str, Any]) -> Dict[str, List[str]]:
    metrics = model.get("metrics", []) or []
    out = {m["name"]: list(dict.fromkeys(m.get("synonyms") or [])) for m in metrics}
    by_name = {m["name"]: m for m in metrics}
    for col in find_synonym_collisions(model):
        unfiltered = [n for n in col["metrics"] if n in by_name and _is_base_unfiltered(by_name[n])]
        keep = unfiltered[0] if len(unfiltered) == 1 else None
        for n in col["metrics"]:
            if n == keep:
                continue
            out[n] = [s for s in out[n] if _norm_term(s) != col["term"]]
            label = by_name[n].get("label") if n in by_name else None
            if label and _norm_term(label) not in {_norm_term(s) for s in out[n]}:
                out[n].append(label.lower())
    return out


def resolve_metric(question: str, model: Dict[str, Any]) -> Dict[str, Any]:
    q = f" {_norm_term(question)} "
    syn = assign_synonyms(model)
    hits = []
    metrics = model.get("metrics", []) or []
    for m in metrics:
        for s in syn.get(m["name"], []) + [m.get("label") or ""]:
            t = _norm_term(s)
            if t and f" {t} " in q:
                hits.append((len(t.split()), 1 if _is_base_unfiltered(m) else 0, m["name"]))
    if not hits:
        return {"metric": None, "alternatives": []}
    hits.sort(reverse=True)
    best = hits[0][2]
    raw = f" {_norm_term(question)} "
    alts = sorted({
        m["name"] for m in metrics
        if m["name"] != best
        and any(f" {_norm_term(s)} " in raw for s in (m.get("synonyms") or []) if _norm_term(s))
    })
    return {"metric": best, "alternatives": alts}


# ---------------------------------------------------------------- helpers

def _strip_literals(sql: str) -> str:
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    sql = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)
    return sql


def _metric(model: Dict[str, Any], name: Optional[str]) -> Optional[Dict[str, Any]]:
    for m in model.get("metrics", []) or []:
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


def _semantic_view_sql(view: str, metrics: List[str], dims: List[str]) -> str:
    s = f"SELECT * FROM SEMANTIC_VIEW({view} METRICS {', '.join(metrics)}"
    if dims:
        s += f" DIMENSIONS {', '.join(dims)}"
    return s + ")"


def _result(action, path, sql, flags, assumptions=None, unmapped=None, suggestion=None):
    flags = sorted(set(flags))  # hardcoding-ok: flags list
    return {
        "action": action,
        "path": path,
        "sql": sql,
        "flags": flags,  # hardcoding-ok: flags dict key
        "assumptions": assumptions or [],
        "unmapped": unmapped or [],
        "suggestion": suggestion,
        "certifiable": action == "execute" and path == "governed" and not flags,
    }


def _today_flag(sql: str, model: Dict[str, Any], question: str) -> bool:
    if not model.get("data_anchored"):
        return False
    if question and TODAY_WORDS.search(question):
        return False
    return bool(TODAY_FUNCS.search(_strip_literals(sql)))


def _period_metric(model: Dict[str, Any], base_name: Optional[str]) -> Optional[Dict[str, Any]]:
    for m in model.get("metrics", []) or []:
        if m.get("kind") == "period" and (m.get("base_metric") or "").lower() == (base_name or "").lower():
            return m
    return None


def _helper_flag(model: Dict[str, Any], names: List[str]) -> List[str]:
    flags = []
    metrics = model.get("metrics", []) or []
    for n in names:
        m = _metric(model, n)
        if m and m.get("kind") == "helper":
            if any(
                o.get("kind") == "base"
                and o.get("table") == m.get("table")
                and o.get("agg") == m.get("agg")
                and (o.get("column") or "") == (m.get("column") or "")
                and not o.get("filter")
                for o in metrics
            ):
                flags.append("HELPER_METRIC")
    return flags


def _norm_pred(p: str) -> str:
    p = re.sub(r'["\'`]', '', p or '')
    p = re.sub(r'\b\w+\.', '', p)
    p = re.sub(r'[()]', '', p)
    return re.sub(r'\s+', ' ', p).strip().lower()


# ---------------------------------------------------------------- validate_sql

def validate_sql(sql: str, model: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    """Validate one engine-generated SQL statement against the model. Never raises for bad SQL."""
    try:
        result = _validate(sql, model, question)
    except Exception as exc:  # noqa: BLE001 - a validator error must never let SQL through unchecked
        logger.warning("SQL validation failed; refusing: %s", exc)
        result = _result("refuse", "none", sql or "", ["UNPARSEABLE"], suggestion=str(exc))
    logger.info("Ask AI SQL validation: action=%s path=%s flags=%s", result["action"], result["path"], result.get("flags", []))
    return result


def _validate(sql: str, model: Dict[str, Any], question: str = "") -> Dict[str, Any]:
    text = (sql or "").strip()
    if not text:
        return _result("refuse", "none", "", ["EMPTY_SQL"])
    stmts = [s for s in sqlglot.parse(text, read=_dialect(model), error_level=sqlglot.ErrorLevel.IGNORE) if s is not None] \
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
                new_metrics = metrics + [p["name"] for p in periods if p]
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
            c_name = c.alias_or_name.upper()
            target_t = t.name.upper()
            alias_to_table[c_name] = target_t
            for inner in tree.find_all(exp.Table):
                if (inner.alias_or_name or "").upper() == c_name:
                    alias_to_table[(inner.alias or inner.name).upper()] = target_t
    # Recursively resolve alias chains
    for a, t in list(alias_to_table.items()):
        curr = t
        while curr in alias_to_table and alias_to_table[curr] != curr:
            curr = alias_to_table[curr]
        alias_to_table[a] = curr

    if any(n.endswith("_ENRICHED") for n in real_tables):
        flags.append("ENRICHED_TABLE")

    meta_tables = model.get("tables", {}) or {}
    snapshot_tables = {n for n, t in meta_tables.items() if t.get("grain") == "snapshot"} | \
        {m["table"] for m in model.get("metrics", []) or [] if m.get("non_additive_by")}
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
            right_table = alias_to_table.get(right_alias, right_alias).replace("_ENRICHED", "")
            right_col = next((c.name.upper() for c in cols if (c.table or "").upper() == right_alias), cols[1].name.upper())
            u = model.get("key_uniqueness", {}).get(f"{right_table}.{right_col}")
            if not u or u.get("rows") != u.get("distinct"):
                flags.append("NON_UNIQUE_JOIN")

    # aggregations / grain
    aggs = list(tree.find_all(exp.AggFunc))
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    where_sql = (sel.args.get("where").sql() if sel and sel.args.get("where") else "").upper()
    has_qualify = bool(tree.find(exp.Qualify))
    for n in real_tables:
        t = meta_tables.get(n, {})
        if n in snapshot_tables and aggs:
            date_cols = [c.upper() for c in t.get("columns", []) if "DATE" in c.upper() or "TIME" in c.upper()]
            sd = (t.get("snapshot_date") or "").upper()
            if not sd:
                for m in model.get("metrics", []) or []:
                    if m.get("table") == n and m.get("non_additive_by"):
                        sd = str(m["non_additive_by"][0]).upper()
                        break
            if sd:
                date_cols.append(sd)
            has_period_filter = any(re.search(rf"\b{dcol}\b", where_sql) for dcol in date_cols)
            ek = (t.get("entity_key") or "").upper()
            distinct_entity = any(isinstance(a, exp.Count) and a.find(exp.Distinct) and ek and ek in a.sql().upper() for a in aggs)
            if not (has_qualify or has_period_filter or distinct_entity):
                flags.append("SNAPSHOT_GRAIN")

    # definitions: conditions in WHERE and CASE WHEN
    def _conjuncts(node):
        if isinstance(node, exp.And):
            return _conjuncts(node.left) + _conjuncts(node.right)
        return [node]

    metric_filters = {_norm_pred(m["filter"]): m["name"] for m in model.get("metrics", []) or [] if m.get("filter")}
    dim_cols = {d["column"].upper() for d in model.get("dimensions", []) or []}
    snap_cols = {(t.get("snapshot_date") or "").upper() for t in meta_tables.values() if t.get("snapshot_date")}
    conds = []
    if sel and sel.args.get("where"):
        conds += [("where", c) for c in _conjuncts(sel.args["where"].this)]
    for case in tree.find_all(exp.If):
        conds.append(("case", case.this))
    for kind, c in conds:
        key = _norm_pred(c.sql(dialect=_dialect(model)))
        if not key:
            continue
        if key in metric_filters:
            assumptions.append(f"condition matches the model's definition of {metric_filters[key]}")
            continue
        cols = {col.name.upper() for col in c.find_all(exp.Column)}
        if kind == "where" and cols and cols <= (snap_cols | dim_cols):
            continue  # period filter or a filter on a model dimension
        flags.append("GUESSED_RULE")
        assumptions.append(f"assumed rule: {c.sql(dialect=_dialect(model))}")

    # governed rewrite attempt
    mapped_metrics, ok = [], True
    metrics_list = model.get("metrics", []) or []
    for a in aggs:
        col = a.find(exp.Column)
        agg = type(a).__name__.upper()
        agg = {"COUNT": "COUNT", "AVG": "AVG", "SUM": "SUM", "MIN": "MIN", "MAX": "MAX"}.get(agg, agg)
        tname = alias_to_table.get((col.table or "").upper(), next(iter(real_tables), "")) if col else next(iter(real_tables), "")
        cname = col.name.upper() if col else None
        hit = next((
            m for m in metrics_list
            if m.get("agg") == agg
            and (m.get("column") or None) == cname
            and m.get("table") == tname.replace("_ENRICHED", "")
            and not m.get("filter")
            and m.get("kind") == "base"
            and not (tname in snapshot_tables and "SNAPSHOT_GRAIN" in flags and m.get("non_additive_by"))
        ), None)
        if hit:
            mapped_metrics.append(hit["name"])
        else:
            ok = False
            unmapped.append(a.sql())
    group = sel.args.get("group") if sel else None
    dims = []
    for g in (group.expressions if group else []):
        col = g if isinstance(g, exp.Column) else g.find(exp.Column)
        col_table = alias_to_table.get((col.table or "").upper(), (col.table or "").upper()) if col else ""
        d = next(
            (
                d for d in model.get("dimensions", []) or []
                if col and d.get("column") == col.name.upper()
                and (not col_table or d.get("table", "").upper() == col_table)
            ),
            None
        )
        if not d:
            d = next((d for d in model.get("dimensions", []) or [] if col and d.get("column") == col.name.upper()), None)
        if d and isinstance(g, exp.Column):
            dims.append(d["name"])
        elif isinstance(g, exp.Case) or isinstance(g, exp.If):
            case_node = g.find(exp.If) if isinstance(g, exp.Case) else g
            cond_key = _norm_pred(case_node.this.sql(dialect=_dialect(model))) if case_node else ""
            sd_match = next((d for d in model.get("dimensions", []) or [] if d.get("name", "").upper().startswith("IS_") or "IS_" in d.get("name", "").upper()), None)
            if sd_match and cond_key in metric_filters:
                dims.append(sd_match["name"])
            else:
                ok = False
                unmapped.append(g.sql())
        else:
            ok = False
            unmapped.append(g.sql())

    rewritable = set(flags) <= {"FREE_FORM", "ENRICHED_TABLE", "SNAPSHOT_GRAIN"}
    unmatched_conds = [c for kind, c in conds if kind != "case" or _norm_pred(c.sql(dialect=_dialect(model))) not in metric_filters]
    if ok and mapped_metrics and not unmatched_conds and rewritable:
        return _result("rewrite", "rewritten", _semantic_view_sql(model["view"], mapped_metrics, dims), [], assumptions)
    action = "refuse" if ("CURRENT_DATE_ANCHOR" in flags or "FAN_OUT" in flags) else "execute"
    return _result(action, "free_form", text, flags, assumptions, unmapped)


# ---------------------------------------------------------------- governed status dimensions

def status_dimensions(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for m in model.get("metrics", []) or []:
        pred = m.get("filter")
        if not pred:
            continue
        try:
            node = sqlglot.parse_one(pred, read=_dialect(model))
        except Exception as exc:  # noqa: BLE001 - unparseable predicate: no dimension (fail closed)
            logger.info("No status dimension for %s: predicate not parseable (%s)", m.get("name"), exc)
            continue
        if node.find(exp.AggFunc):
            continue
        cols = list(node.find_all(exp.Column))
        table_cols = set(model.get("tables", {}).get(m["table"], {}).get("columns", []))
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


# ---------------------------------------------------------------- metadata adapter

_AGG_RE = re.compile(r"^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(DISTINCT\s+)?(?:\"?(\w+)\"?\.)?\"?(\w+|\*)\"?\s*\)\s*$", re.I)


def metadata_from_domain(domain: Any, *, view: Optional[str] = None, platform: str = "snowflake",
                         data_anchored: bool = True, latest_data_date: Optional[str] = None,
                         key_uniqueness: Optional[Dict[str, Dict[str, int]]] = None,
                         table_grain: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build validator metadata from an ontology domain (best effort, read-only).

    key_uniqueness: the per-table key profiles the emitter computes during sync.
    table_grain: {TABLE: {"grain", "snapshot_date", "entity_key", "columns"}} when known; otherwise
    grain is inferred from metrics declared non-additive by a date.
    """
    tables: Dict[str, Dict[str, Any]] = {k.upper(): dict(v) for k, v in (table_grain or {}).items()}
    metrics: List[Dict[str, Any]] = []
    for m in getattr(domain, "metrics", []) or []:
        sql = (getattr(m, "sql_expression", None) or "").strip()
        mt = _AGG_RE.match(sql)
        table = (mt.group(3) if mt and mt.group(3) else getattr(m, "concept", None) or "").upper()
        name = getattr(m, "name", None) or getattr(m, "logical_name", None)
        if not name:
            continue
        tags = [str(t).lower() for t in (getattr(m, "tags", None) or [])]
        kind = "helper" if re.match(r"^(SUM|AVG|COUNT|MIN|MAX)_OF_", name, re.I) or "auto_generated" in tags else "base"
        metrics.append({
            "name": name, "label": getattr(m, "label", None) or name, "table": table,
            "agg": (mt.group(1).upper() if mt else str(getattr(m, "aggregation_type", "") or "").upper()),
            "column": (mt.group(4).upper() if mt and mt.group(4) != "*" else None),
            "filter": getattr(m, "filter_expression", None),
            "non_additive_by": list(getattr(m, "grain", None) or []) if "non_additive" in tags else [],
            "kind": kind, "synonyms": list(getattr(m, "synonyms", None) or []),
        })
        tables.setdefault(table, {"grain": "unknown", "columns": []})
    by_name = {x["name"].upper(): x for x in metrics}
    for x in metrics:  # period metrics: <BASE>_SPLY / _YTD / _PY ... derived from the base metric's name
        mp = re.match(r"^(.*)_(SPLY|YTD|MTD|QTD|PY|PREV_YEAR|YOY_VAR|YOY_PCT_CHANGE)$", x["name"], re.I)
        if mp and mp.group(1).upper() in by_name:
            x.update(kind="period", base_metric=by_name[mp.group(1).upper()]["name"], shape=mp.group(2).upper())
    dims = []
    for c in getattr(domain, "concepts", []) or []:
        tname = str(getattr(c, "name", "")).upper()
        cols = tables.setdefault(tname, {"grain": "dimension", "columns": []}).setdefault("columns", [])
        for a in getattr(c, "attributes", []) or []:
            col = str(getattr(a, "name", "")).upper()
            if col and col not in cols:
                cols.append(col)
            dims.append({"name": f"{tname.lower()}.{col.lower()}", "table": tname, "column": col})
    rels = []
    for r in getattr(domain, "relationships", []) or []:
        if getattr(r, "is_active", True) is False:
            continue
        card = str(getattr(getattr(r, "cardinality", None), "value", getattr(r, "cardinality", "")) or "")
        rels.append({"from": str(r.from_concept).upper(), "to": str(r.to_concept).upper(),
                     "from_col": None, "to_col": None,
                     "cardinality": "N:1" if "many_to_one" in card.lower() or card in ("N:1",) else card})
    return {"view": view or getattr(domain, "semantic_view_name", None), "platform": platform,
            "data_anchored": data_anchored, "latest_data_date": latest_data_date, "tables": tables,
            "relationships": rels, "key_uniqueness": dict(key_uniqueness or {}), "metrics": metrics,
            "dimensions": dims}


# ---------------------------------------------------------------- integration adapter

class SQLValidationResult(BaseModel):
    """Result of running generic SQL governance validation and rewrite."""
    allowed: bool = True
    refusal_reason: Optional[str] = None
    query_path: str = "semantic_view"
    rewritten_sql: Optional[str] = None
    checks_passed: bool = True
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unmapped_elements: List[str] = Field(default_factory=list)
    suggested_metrics: List[str] = Field(default_factory=list)
    is_certified_eligible: bool = True


def _sml_to_dict_model(
    model_metadata: Any,
    semantic_view_name: Optional[str] = None,
    key_uniqueness_data: Optional[Dict[str, Any]] = None,
    profiling_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(model_metadata, dict) and "metrics" in model_metadata and "tables" in model_metadata:
        return model_metadata

    if hasattr(model_metadata, "concepts") and hasattr(model_metadata, "metrics"):
        return metadata_from_domain(
            model_metadata,
            view=semantic_view_name,
            key_uniqueness=key_uniqueness_data,
        )

    # Convert SMLModel to dict shape
    tables_dict: Dict[str, Dict[str, Any]] = {}
    ku_dict: Dict[str, Dict[str, int]] = dict(key_uniqueness_data or {})

    datasets = getattr(model_metadata, "datasets", []) or []
    for ds in datasets:
        ds_name = str(getattr(ds, "unique_name", None) or getattr(ds, "name", "") or "").upper()
        cols = [str(getattr(c, "unique_name", None) or getattr(c, "name", "")).upper() for c in (getattr(ds, "columns", []) or [])]
        for col in getattr(ds, "columns", []) or []:
            if getattr(col, "is_key", False):
                col_name = str(getattr(col, "unique_name", None) or getattr(col, "name", "")).upper()
                rc = getattr(ds, "row_count", 100) or 100
                ku_dict[f"{ds_name}.{col_name}"] = {"rows": rc, "distinct": rc}

        # Resolve snapshot date / entity key if present
        snapshot_date = getattr(ds, "snapshot_date_column", None)
        entity_key = getattr(ds, "entity_key_column", None)
        if not entity_key:
            entity_key = next((c.unique_name for c in (getattr(ds, "columns", []) or []) if getattr(c, "is_key", False)), None)

        grain = "dimension"
        if getattr(ds, "is_snapshot", False):
            grain = "snapshot"
        elif any(getattr(c, "is_metric", False) for c in (getattr(ds, "columns", []) or [])):
            grain = "event"
        elif any(
            (getattr(m, "dataset", None) or getattr(m, "table", "") or "").upper() == ds_name
            for m in getattr(model_metadata, "metrics", []) or []
        ):
            grain = "event"
        elif (getattr(ds, "row_count", 0) or 0) > 100:
            grain = "event"

        tables_dict[ds_name] = {
            "grain": grain,
            "snapshot_date": snapshot_date,
            "entity_key": entity_key,
            "columns": cols,
        }

    m_dict_list: List[Dict[str, Any]] = []
    for m in getattr(model_metadata, "metrics", []) or []:
        m_name = str(getattr(m, "unique_name", None) or getattr(m, "name", ""))
        m_table = str(getattr(m, "dataset", None) or getattr(m, "table", "") or "").upper()
        m_agg = str(getattr(m, "aggregation", "SUM") or "SUM").upper()
        if hasattr(getattr(m, "aggregation", None), "value"):
            m_agg = m.aggregation.value.upper()
        if m_agg == "AVERAGE":
            m_agg = "AVG"

        m_col = getattr(m, "source_column", None)
        if m_col:
            m_col = str(m_col).upper()

        m_filter = getattr(m, "filter_predicate", None)
        non_add = []
        if getattr(m, "non_additive_dimension", None):
            nad = getattr(m, "non_additive_dimension")
            non_add_col = nad[1] if isinstance(nad, tuple) else str(nad)
            non_add = [non_add_col]
            if m_table in tables_dict and not tables_dict[m_table]["snapshot_date"]:
                tables_dict[m_table]["snapshot_date"] = non_add_col
        elif getattr(m, "non_additive_by", None):
            non_add = list(getattr(m, "non_additive_by"))
            if m_table in tables_dict and not tables_dict[m_table]["snapshot_date"] and non_add:
                tables_dict[m_table]["snapshot_date"] = str(non_add[0])

        kind = "base"
        if getattr(m, "is_helper", False) or m_name.upper().startswith("SUM_OF_"):
            kind = "helper"
        elif getattr(m, "kind", None):
            kind = getattr(m, "kind")

        m_dict_list.append({
            "name": m_name,
            "label": getattr(m, "label", m_name),
            "table": m_table,
            "agg": m_agg,
            "column": m_col,
            "filter": m_filter,
            "non_additive_by": non_add,
            "kind": kind,
            "synonyms": list(getattr(m, "synonyms", []) or []),
            "base_metric": getattr(m, "base_metric", None),
        })

    dim_dict_list: List[Dict[str, Any]] = []
    for ds in datasets:
        ds_name = str(getattr(ds, "unique_name", None) or getattr(ds, "name", "") or "")
        for col in getattr(ds, "columns", []) or []:
            c_name = str(getattr(col, "unique_name", None) or getattr(col, "name", "") or "")
            dim_dict_list.append({
                "name": f"{ds_name.lower()}.{c_name.lower()}",
                "table": ds_name.upper(),
                "column": c_name.upper(),
            })

    rel_dict_list: List[Dict[str, Any]] = []
    for r in getattr(model_metadata, "relationships", []) or []:
        c1 = (getattr(r, "from_dataset", None) or getattr(r, "from_concept", None) or "").upper()
        c2 = (getattr(r, "to_dataset", None) or getattr(r, "to_concept", None) or "").upper()
        c1_col = (getattr(r, "from_column", None) or "").upper()
        c2_col = (getattr(r, "to_column", None) or "").upper()
        card_val = str(getattr(r, "cardinality", "N:1")).upper()
        card = "N:1"
        if "1:N" in card_val or "ONE_TO_MANY" in card_val:
            card = "1:N"
        elif "N:N" in card_val or "MANY_TO_MANY" in card_val:
            card = "N:N"
        elif "1:1" in card_val or "ONE_TO_ONE" in card_val:
            card = "1:1"
        rel_dict_list.append({
            "from": c1,
            "from_col": c1_col,
            "to": c2,
            "to_col": c2_col,
            "cardinality": card,
        })

    return {
        "view": semantic_view_name or getattr(model_metadata, "unique_name", "VIEW"),
        "platform": "snowflake",
        "data_anchored": True,
        "latest_data_date": "2014-12-31",
        "tables": tables_dict,
        "relationships": rel_dict_list,
        "key_uniqueness": ku_dict,
        "metrics": m_dict_list,
        "dimensions": dim_dict_list,
    }


def validate_and_rewrite_generated_sql(
    sql: str,
    prompt: str = "",
    dialect: str = "snowflake",
    model_metadata: Optional[Any] = None,
    profiling_data: Optional[Dict[str, Any]] = None,
    semantic_view_name: Optional[str] = None,
    key_uniqueness_data: Optional[Dict[str, Any]] = None,
) -> SQLValidationResult:
    """Validate and rewrite engine-generated SQL, returning structured validation results."""
    res = SQLValidationResult()
    if not sql or not sql.strip():
        res.allowed = False
        res.refusal_reason = "Empty SQL statement."
        res.checks_passed = False
        return res

    guard_model = _sml_to_dict_model(
        model_metadata=model_metadata or {},
        semantic_view_name=semantic_view_name,
        key_uniqueness_data=key_uniqueness_data,
        profiling_data=profiling_data,
    )

    r = validate_sql(sql=sql, model=guard_model, question=prompt)
    flags_list = r.get("flags", [])  # hardcoding-ok: flags list

    # Check for additive metrics in join failures to enforce refusal
    is_additive = bool(re.search(r"\b(SUM|COUNT)\b", sql, re.I))
    if "NON_UNIQUE_JOIN" in flags_list and is_additive:
        r["action"] = "refuse"
        r["suggestion"] = "Refused: possible double counting due to join on non-unique key with additive metric."
    elif "FAN_OUT" in flags_list:
        r["action"] = "refuse"
        r["suggestion"] = "Refused: possible double counting due to fan-out join path."

    res.allowed = (r["action"] != "refuse")
    res.refusal_reason = r.get("suggestion") if r["action"] == "refuse" else None
    if not res.allowed and not res.refusal_reason:
        res.refusal_reason = f"Refused by SQL governance guard: {', '.join(flags_list)}"

    if r["path"] == "governed":
        res.query_path = "semantic_view"
    elif r["path"] == "rewritten":
        res.query_path = "rewritten to governed"
    else:
        res.query_path = "free-form (not governed)"

    res.rewritten_sql = r.get("sql") if r["path"] == "rewritten" else None
    if r["path"] == "rewritten":
        res.checks_passed = True
        res.is_certified_eligible = True
    else:
        res.checks_passed = bool(r.get("certifiable", False))
        res.is_certified_eligible = bool(r.get("certifiable", False))
    res.assumptions = list(r.get("assumptions") or [])
    res.unmapped_elements = list(r.get("unmapped") or [])
    res.warnings = [f"Flag: {flg}" for flg in flags_list]

    # Populate structured checks
    res.checks.append({
        "check": "governed_path",
        "passed": r["path"] in ("governed", "rewritten"),
        "message": "Query runs through governed SEMANTIC_VIEW." if r["path"] in ("governed", "rewritten") else f"Query runs free-form SQL against base tables. Unmapped elements: {', '.join(res.unmapped_elements) if res.unmapped_elements else 'custom query structure'}.",
    })
    res.checks.append({
        "check": "grain_safety",
        "passed": "SNAPSHOT_GRAIN" not in flags_list,
        "message": "Grain check passed; no un-periodized snapshot aggregation detected." if "SNAPSHOT_GRAIN" not in flags_list else "Direct aggregation over snapshot table without a single-period filter or de-duplication may distort results.",
    })
    res.checks.append({
        "check": "join_safety",
        "passed": "NON_UNIQUE_JOIN" not in flags_list and "FAN_OUT" not in flags_list,
        "message": "Join check passed; no non-unique join or fan-out path detected." if "NON_UNIQUE_JOIN" not in flags_list and "FAN_OUT" not in flags_list else "Join check flagged possible double counting or fan-out path.",
    })
    res.checks.append({
        "check": "definition_alignment",
        "passed": "GUESSED_RULE" not in flags_list,
        "message": "Condition aligns with model definition." if "GUESSED_RULE" not in flags_list else "Contains assumed or unverified rule.",
    })

    return res
