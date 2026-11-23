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
   "tables": {NAME: {"grain": snapshot|event|dimension|unknown, "snapshot_date", "entity_key", "columns",
                     "generated": bool (object created by SemaBridge), "source_table": NAME it derives from}},
   "relationships": [{"from", "from_col", "to", "to_col", "cardinality"}],
   "key_uniqueness": {"TABLE.COLUMN": {"rows", "distinct"}},
   "metrics": [{"name", "label", "table", "agg", "column", "filter", "non_additive_by", "kind"
                (base|period|helper), "base_metric", "shape", "synonyms"}],
   "dimensions": [{"name", "table", "column"}]}

Result flags: CURRENT_DATE_ANCHOR, FREE_FORM, GENERATED_OBJECT, SNAPSHOT_GRAIN, NON_UNIQUE_JOIN, FAN_OUT,
GUESSED_RULE, HELPER_METRIC, UNKNOWN_METRIC, WRITE_STATEMENT, MULTI_STATEMENT, EMPTY_SQL, UNPARSEABLE,
NOT_VALIDATED (no metadata available; never certifiable), INCOMPLETE_ANSWER (engine reported
unresolved parts of the question).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

_DIALECTS = {"snowflake": "snowflake", "databricks": "databricks", "fabric": "tsql"}


def _dialect(model: Dict[str, Any]) -> str:
    return _DIALECTS.get(str(model.get("platform") or "snowflake").lower(), "snowflake")

TODAY_FUNCS = re.compile(r"\b(CURRENT_DATE|CURRENT_TIMESTAMP|GETDATE|SYSDATE|NOW|LOCALTIMESTAMP)\b(\s*\(\s*\))?", re.I)
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


def _ident(value) -> str:
    """Comparable identity of a metric name: last dotted part, quotes removed, spaces/punctuation as _."""
    v = str(value or "").strip().strip('"').split(".")[-1].strip('"')
    return re.sub(r"[^0-9a-z]+", "_", v.lower()).strip("_")


def _metric(model, name):
    """Find a metric by its deployed identifier, name, logical name or label (qualified or not)."""
    key = _ident(name)
    if not key:
        return None
    for m in model["metrics"]:
        forms = [m.get("name"), m.get("logical_name"), m.get("label"), m.get("deployed_name")]
        forms += list(m.get("identifiers") or [])
        if any(_ident(f) == key for f in forms if f):
            return m
    # Last resort: ignore separators, but only when exactly one metric matches (never guess between two).
    compact = key.replace("_", "")
    hits = [m for m in model["metrics"]
            if any(_ident(f).replace("_", "") == compact
                   for f in [m.get("name"), m.get("logical_name"), m.get("label"), m.get("deployed_name"),
                             *(m.get("identifiers") or [])] if f)]
    return hits[0] if len(hits) == 1 else None


def _governed_parts(sql: str):
    body = _strip_literals(sql)
    mm = re.search(r"\bMETRICS\s+(.+?)(?=\bDIMENSIONS\b|\bWHERE\b|\)\s*(?:$|\)|\bAS\b|;|\bORDER\b|\bLIMIT\b|\bWHERE\b)|$)", body, re.I | re.S)
    dm = re.search(r"\bDIMENSIONS\s+(.+?)(?=\bMETRICS\b|\bWHERE\b|\)\s*(?:$|\)|\bAS\b|;|\bORDER\b|\bLIMIT\b)|$)", body, re.I | re.S)
    split = lambda s: [x.strip().split(".")[-1] for x in (s or "").split(",") if x.strip()]
    full = lambda s: [x.strip() for x in (s or "").split(",") if x.strip()]
    return split(mm.group(1) if mm else ""), full(dm.group(1) if dm else "")


_IDENT = re.compile(r'^(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"]+")$')


def _quote_ident(part: str) -> Optional[str]:
    part = (part or "").strip()
    if not part or not _IDENT.match(part):
        return None
    return part if part.startswith('"') else '"' + part.upper() + '"' if not part.isupper() else part


def _semantic_view_sql(model: Dict[str, Any], metrics: List[str], dims: List[str]) -> Optional[str]:
    """Build a SEMANTIC_VIEW query, or None when that is not possible safely.

    Only for platforms whose SQL has SEMANTIC_VIEW (Snowflake); the view name comes from server-side
    metadata and every part must be a plain or quoted identifier (no injection through the name).
    """
    if str(model.get("platform") or "").lower() != "snowflake":
        return None
    parts = [p for p in str(model.get("view") or "").split(".")]
    if not 1 <= len(parts) <= 3:
        return None
    quoted = [_quote_ident(p) for p in parts]
    items = [x.strip() for x in list(metrics) + list(dims)]
    if any(q is None for q in quoted) or not metrics or \
            any(not re.match(r"^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)?$", x) for x in items):
        return None
    s = f"SELECT * FROM SEMANTIC_VIEW({'.'.join(quoted)} METRICS {', '.join(metrics)}"
    if dims:
        s += f" DIMENSIONS {', '.join(dims)}"
    return s + ")"


_FLAGS_KEY = "".join(["fl", "ags"])


def _rewrite_or_none(model: Dict[str, Any], metrics: List[str], dims: List[str]) -> Optional[str]:
    """Rewritten SQL only if it can be built and itself validates as governed and clean."""
    sql = _semantic_view_sql(model, metrics, dims)
    if not sql:
        return None
    check = _validate(sql, model, "", False)
    return sql if check["action"] == "execute" and check["path"] == "governed" and not check[_FLAGS_KEY] else None


def _result(action, path, sql, flags, assumptions=None, unmapped=None, suggestion=None):
    flags = sorted(set(flags))
    assumptions = list(dict.fromkeys(assumptions or []))
    return {"action": action, "path": path, "sql": sql, _FLAGS_KEY: flags,
            "assumptions": assumptions, "unmapped": unmapped or [], "suggestion": suggestion,
            "certifiable": action == "execute" and path == "governed" and not flags}


def _today_flag(sql, model, allow_current_date):
    if not model.get("data_anchored") or allow_current_date:
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


def _only_today_predicates(sql: str, model: Dict[str, Any]) -> bool:
    """True when every WHERE conjunct inside SEMANTIC_VIEW(...) references today's date (so replacing
    them with period metrics keeps the meaning). Any other filter -> False (no rewrite)."""
    body = _strip_literals(sql)
    m = re.search(r"\bWHERE\b(.*)\)\s*(?:;|\bORDER\b|\bLIMIT\b|$)", body, re.I | re.S)
    if not m:
        return True
    try:
        node = sqlglot.parse_one("SELECT 1 WHERE " + m.group(1), read=_dialect(model))
    except Exception as exc:  # noqa: BLE001 - unparseable filter: do not rewrite
        logger.info("Not rewriting: WHERE clause not parseable (%s)", exc)
        return False
    where = node.args.get("where")
    if where is None:
        return True

    def conj(n):
        return conj(n.left) + conj(n.right) if isinstance(n, exp.And) else [n]

    return all(TODAY_FUNCS.search(c.sql()) for c in conj(where.this))


def _norm_pred(p: str) -> str:
    p = str(p or "").replace('"', '').replace("'", "")
    p = re.sub(r"\b\w+\.(?=\w)", "", p)
    p = re.sub(r"^\s*\(+|\)+\s*$", "", p)
    return re.sub(r"\s+", " ", p).strip().lower()


# ---------------------------------------------------------------- validate_sql

def _coverage_gaps(coverage: Optional[Dict[str, Any]]) -> List[str]:
    """Parts of the question the NL engine could not resolve (reported by the engine itself)."""
    if not coverage:
        return []
    gaps = [str(x) for x in (coverage.get("unresolved") or [])]
    for kind in ("metrics", "dimensions", "filters", "splits"):
        req = {str(x).casefold() for x in (coverage.get(f"requested_{kind}") or [])}
        got = {str(x).casefold() for x in (coverage.get(f"resolved_{kind}") or [])}
        gaps += [f"{kind[:-1]}: {x}" for x in sorted(req - got)]
    return list(dict.fromkeys(gaps))


def validate_sql(sql: str, model: Dict[str, Any], question: str = "", *,
                 allow_current_date: bool = False, coverage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate one engine-generated SQL statement against the model. Never raises for bad SQL.

    allow_current_date: set by the caller when the NL engine resolved an explicit "as of now" intent
    for this question; the validator itself never interprets natural-language words.
    coverage: what the engine was asked vs what it resolved, e.g.
      {"requested_metrics": [...], "resolved_metrics": [...], "requested_dimensions": [...],
       "resolved_dimensions": [...], "requested_splits": [...], "resolved_splits": [...],
       "unresolved": ["<phrase the engine could not map>"]}
      Any gap -> refuse with INCOMPLETE_ANSWER (the SQL would answer a smaller question).
    """
    gaps = _coverage_gaps(coverage)
    if gaps:
        # The SQL answers a different (smaller) question: never show it as the answer.
        return _result("refuse", "none", sql or "", ["INCOMPLETE_ANSWER"], unmapped=gaps,
                       suggestion="Part of the question could not be answered: " + "; ".join(gaps))
    try:
        result = _validate(sql, model, question, allow_current_date)
    except Exception as exc:  # noqa: BLE001 - a validator error must never let SQL through unchecked
        logger.warning("SQL validation failed; refusing: %s", exc)
        result = _result("refuse", "none", sql or "", ["UNPARSEABLE"], suggestion=str(exc))
    logger.info("Ask AI SQL validation: action=%s path=%s flags=%s", result["action"], result["path"], result["flags"])
    return result


def _validate(sql: str, model: Dict[str, Any], question: str = "", allow_current_date: bool = False) -> Dict[str, Any]:
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
        if not model.get("metrics"):
            # No metadata to check against: let the warehouse validate names, but never certify.
            return _result("execute", "governed", text, ["NOT_VALIDATED"],
                           suggestion="Model schema unavailable; answer not validated")
        unknown = [m for m in metrics if not _metric(model, m)]
        if unknown:
            return _result("refuse", "governed", text, ["UNKNOWN_METRIC"], unmapped=unknown)
        flags += _helper_flag(model, metrics)
        if _today_flag(text, model, allow_current_date):
            periods = [_period_metric(model, m) for m in metrics]
            if metrics and all(periods) and _only_today_predicates(text, model):
                new_metrics = metrics + [p["name"] for p in periods]
                rewritten = _rewrite_or_none(model, new_metrics, dims)
                if rewritten:
                    return _result("rewrite", "rewritten", rewritten, flags + ["CURRENT_DATE_ANCHOR"])
            return _result("refuse", "governed", text, flags + ["CURRENT_DATE_ANCHOR"],
                           suggestion="Use the model's period metrics; data ends " + str(model.get("latest_data_date")))
        return _result("execute", "governed", text, flags)

    # ---- free-form SQL
    tree = stmts[0] if stmts else None
    if tree is None:
        return _result("refuse", "free_form", text, ["UNPARSEABLE"])
    flags, assumptions, unmapped = ["FREE_FORM"], [], []
    if _today_flag(text, model, allow_current_date):
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

    def _resolve_table(t_name: str) -> str:
        curr = (t_name or "").upper()
        seen = set()
        while curr in alias_to_table and curr not in seen and alias_to_table[curr] != curr:
            seen.add(curr)
            curr = alias_to_table[curr]
        return curr

    def _source_table(t_name: str) -> str:
        tbl = _resolve_table(t_name)
        if tbl in model["tables"] and model["tables"][tbl].get("source_table"):
            return model["tables"][tbl]["source_table"]
        for base in model["tables"]:
            if tbl.startswith(base + "_"):
                return base
        return tbl

    if any(model["tables"].get(n, {}).get("generated") for n in real_tables):
        flags.append("GENERATED_OBJECT")

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
            right_table = _resolve_table(right_alias)
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
    for a in aggs:
        col = a.find(exp.Column)
        agg = type(a).__name__.upper()
        agg = {"COUNT": "COUNT", "AVG": "AVG", "SUM": "SUM", "MIN": "MIN", "MAX": "MAX"}.get(agg, agg)
        tname = _resolve_table((col.table or "").upper()) if col else next(iter(real_tables), "")
        cname = col.name.upper() if col else None
        hit = next((m for m in model["metrics"] if m["agg"] == agg and (m.get("column") or None) == cname
                    and m["table"] == _source_table(tname) and not m.get("filter") and not m.get("non_additive_by") and m.get("kind") == "base"), None)
        if hit:
            mapped_metrics.append(hit["name"])
        else:
            ok = False
            unmapped.append(a.sql())
    group = sel.args.get("group") if sel else None
    dims = []
    status_dims = {d["name"].split(".")[-1].upper(): d for d in model["dimensions"]}
    for g in (group.expressions if group else []):
        if isinstance(g, (exp.Case, exp.If)):
            # A CASE split maps to a status dimension only when its condition is exactly the filter of a
            # measure and that measure's status dimension exists in the deployed view.
            first = g.find(exp.If) if isinstance(g, exp.Case) else g
            key = _norm_pred(first.this.sql(dialect=_dialect(model))) if first is not None else ""
            owner = metric_filters.get(key)
            sd = None
            if owner:
                owner_alias = _deployed_alias(owner) or owner
                sd_name = _deployed_alias(f"IS_{owner_alias}") or f"IS_{owner_alias}"
                sd = status_dims.get(sd_name.upper()) or status_dims.get(f"IS_{owner}".upper())
            if sd:
                dims.append(sd["name"])
                continue
            ok = False
            unmapped.append(g.sql())
            continue
        col = g if isinstance(g, exp.Column) else g.find(exp.Column)
        col_t = _source_table(_resolve_table((col.table or "").upper())) if col else ""
        d = next((d for d in model["dimensions"] if col and d["column"] == col.name.upper()
                  and (not col_t or d["table"] == col_t)), None)
        if not d and col:
            d = next((d for d in model["dimensions"] if d["column"] == col.name.upper()), None)
        if d and isinstance(g, exp.Column):
            dims.append(d["name"])
        else:
            ok = False
            unmapped.append(g.sql())
    where_conds = [c for kind, c in conds if kind == "where"]
    unmatched_case = [c for kind, c in conds if kind == "case" and _norm_pred(c.sql(dialect=_dialect(model))) not in metric_filters]
    meaning_risk = bool(where_conds or unmatched_case or (sel and (sel.args.get("having") or sel.args.get("limit")))
                        or tree.find(exp.Qualify) or tree.find(exp.Window))
    rewritable = set(flags) <= {"FREE_FORM", "GENERATED_OBJECT", "SNAPSHOT_GRAIN"}
    if ok and mapped_metrics and not meaning_risk and rewritable:
        rewritten = _rewrite_or_none(model, mapped_metrics, dims)
        if rewritten:
            return _result("rewrite", "rewritten", rewritten, [], assumptions)
    action = "refuse" if ("CURRENT_DATE_ANCHOR" in flags or "FAN_OUT" in flags) else "execute"
    return _result(action, "free_form", text, flags, assumptions, unmapped)


# ---------------------------------------------------------------- governed status dimensions

def status_dimensions(model: Dict[str, Any], *, enabled: bool = False,
                      existing_columns: Optional[set] = None) -> List[Dict[str, Any]]:
    if not enabled:
        return []
    taken = {str(c).upper() for c in (existing_columns or set())}
    out = []
    for m in model.get("metrics", []):
        pred = m.get("filter")
        if not pred:
            continue
        if TODAY_FUNCS.search(_strip_literals(pred)):
            continue
        try:
            node = sqlglot.parse_one(pred, read=_dialect(model))
        except Exception as exc:  # noqa: BLE001 - unparseable predicate: no dimension (fail closed)
            logger.info("No status dimension for %s: predicate not parseable (%s)", m.get("name"), exc)
            continue
        if node.find(exp.AggFunc):
            continue
        cols = list(node.find_all(exp.Column))
        table_cols = set(model.get("tables", {}).get(m.get("table", ""), {}).get("columns", []))
        if not cols or any((c.table and c.table.upper() != m.get("table", "").upper()) or c.name.upper() not in table_cols for c in cols):
            continue
        raw_name = m.get("name", "")
        base = _deployed_alias(raw_name) or raw_name
        is_name = _deployed_alias(f"IS_{base}") or f"IS_{base}"
        table_taken = taken | {str(c).upper() for c in table_cols}
        if is_name.upper() in table_taken or any(d["name"].upper() == is_name.upper() for d in out):
            continue
        out.append({"name": is_name, "table": m["table"], "predicate": pred})
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


def _deployed_alias(name: str) -> Optional[str]:
    """The identifier the emitter deploys for a measure name (same sanitizer as metrics_clause_builder)."""
    try:
        from semabridge.utils.identifiers import IdentifierSanitizer

        return IdentifierSanitizer().to_alias(name)
    except Exception as exc:  # noqa: BLE001 - outside the product (tests) the plain name is used
        logger.debug("Deployed alias unavailable for %s: %s", name, exc)
        return None


def metadata_from_domain(domain: Any, *, view: Optional[str] = None, platform: str = "snowflake",
                         data_anchored: bool = True, latest_data_date: Optional[str] = None,
                         key_uniqueness: Optional[Dict[str, Dict[str, int]]] = None,
                         table_grain: Optional[Dict[str, Dict[str, Any]]] = None,
                         generated_objects: Optional[Dict[str, str]] = None,
                         period_metrics: Optional[Dict[str, Dict[str, str]]] = None,
                         helper_metrics: Optional[List[str]] = None,
                         deployed_status_dimensions: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Build validator metadata from an ontology domain (best effort, read-only).

    Everything model-specific comes from the caller's metadata, never from names:
      key_uniqueness: per-table key profiles the emitter computes during sync.
      table_grain: {TABLE: {"grain", "snapshot_date", "entity_key", "columns"}}; otherwise grain is
        inferred from metrics declared non-additive by a date.
      generated_objects: {OBJECT: SOURCE_TABLE} for objects SemaBridge created (enriched views etc.).
      period_metrics: {METRIC: {"base_metric", "shape"}} recorded by the time-intelligence translation.
      helper_metrics: metrics SemaBridge generated automatically (implicit aggregations).
      deployed_status_dimensions: boolean status columns actually deployed by the emitter (IS_<METRIC>).
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
        helper_set = {str(h).upper() for h in (helper_metrics or [])}
        kind = "helper" if name.upper() in helper_set or "auto_generated" in tags else "base"
        metrics.append({
            "name": name, "label": getattr(m, "label", None) or name, "table": table,
            "logical_name": getattr(m, "logical_name", None),
            "deployed_name": _deployed_alias(name),
            "identifiers": [x for x in (getattr(m, "maps_to", None) or [])],
            "agg": (mt.group(1).upper() if mt else str(getattr(m, "aggregation_type", "") or "").upper()),
            "column": (mt.group(4).upper() if mt and mt.group(4) != "*" else None),
            "filter": getattr(m, "filter_expression", None),
            "non_additive_by": list(getattr(m, "grain", None) or []) if "non_additive" in tags else [],
            "kind": kind, "synonyms": list(getattr(m, "synonyms", None) or []),
        })
        tables.setdefault(table, {"grain": "unknown", "columns": []})
    by_name = {x["name"].upper(): x for x in metrics}
    for mname, info in (period_metrics or {}).items():  # from the time-intelligence translation, not names
        x = by_name.get(str(mname).upper())
        base = by_name.get(str(info.get("base_metric") or "").upper())
        if x and base:
            x.update(kind="period", base_metric=base["name"], shape=info.get("shape"))
    for obj, src in (generated_objects or {}).items():
        tables.setdefault(str(obj).upper(), {"grain": "unknown", "columns": []}).update(
            generated=True, source_table=str(src).upper())
    dims = []
    for c in getattr(domain, "concepts", []) or []:
        tname = str(getattr(c, "name", "")).upper()
        cols = tables.setdefault(tname, {"grain": "dimension", "columns": []}).setdefault("columns", [])
        for a in getattr(c, "attributes", []) or []:
            col = str(getattr(a, "name", "")).upper()
            if col and col not in cols:
                cols.append(col)
            dims.append({"name": f"{tname.lower()}.{col.lower()}", "table": tname, "column": col})
    if deployed_status_dimensions:
        for sd in deployed_status_dimensions:
            sd_name = sd.get("name") or ""
            tname = (sd.get("table") or "").upper()
            col = (sd.get("column") or sd_name.split(".")[-1]).upper()
            dim_name = sd_name if "." in sd_name else f"{tname.lower()}.{col.lower()}"
            dims.append({"name": dim_name, "table": tname, "column": col})
            if tname in tables and col not in tables[tname].get("columns", []):
                tables[tname].setdefault("columns", []).append(col)
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


# ---------------------------------------------------------------- integration adapter (ai_router)

from pydantic import BaseModel, Field  # noqa: E402


class SQLValidationResult(BaseModel):
    """Result returned to ai_router / the answer card."""
    allowed: bool = True
    refusal_reason: Optional[str] = None
    query_path: str = "free_form"          # governed | rewritten | free_form | none
    rewritten_sql: Optional[str] = None
    checks_passed: bool = False
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unmapped_elements: List[str] = Field(default_factory=list)
    is_certified_eligible: bool = False


def _view_key(name: Optional[str]) -> str:
    parts = [p.strip().strip('"') for p in str(name or "").split(".") if p.strip()]
    return ".".join(parts).casefold()


def select_domain_for_view(federation: Any, semantic_view_name: Optional[str], project_id: Optional[str] = None) -> Optional[Any]:
    """The domain deployed as exactly this view (full name, or last part when the other side is unqualified).

    Never merges domains and never matches by substring: an unmatched view yields None, so the answer is
    reported as not validated instead of being checked against other projects' metrics.
    """
    target = _view_key(semantic_view_name)
    if not target:
        logger.error("View name is blank or missing for project '%s'", project_id)
        return None
    t_last = target.split(".")[-1]
    exact, by_last = [], []
    for d in (getattr(federation, "domains", {}) or {}).values():
        dv = _view_key(getattr(d, "semantic_view_name", None))
        if not dv:
            continue
        if dv == target:
            exact.append(d)
        elif ("." not in dv or "." not in target) and dv.split(".")[-1] == t_last:
            by_last.append(d)
    if exact:
        return exact[0]
    if len(by_last) == 1:
        return by_last[0]
    logger.error("View '%s' not found in current project '%s'", semantic_view_name, project_id)
    return None


def _adapt_sml_model(sml: Any, **kwargs: Any) -> Dict[str, Any]:
    tg = kwargs.get("table_grain") or kwargs.get("table_grain_data") or {}
    tables: Dict[str, Dict[str, Any]] = {k.upper(): dict(v) for k, v in tg.items()}
    dims: List[Dict[str, Any]] = []
    ku = kwargs.get("key_uniqueness") or kwargs.get("key_uniqueness_data") or {}
    key_uniqueness = {}
    for k, v in ku.items():
        if "." in k:
            key_uniqueness[k.upper()] = dict(v)
        elif isinstance(v, dict) and "columns" in v:
            for c_col in v["columns"]:
                key_uniqueness[f"{k.upper()}.{c_col.upper()}"] = dict(v)
        else:
            key_uniqueness[k.upper()] = dict(v)

    for d in getattr(sml, "datasets", []) or []:
        tname = str(getattr(d, "unique_name", "")).upper()
        cols = tables.setdefault(tname, {"grain": "dimension" if getattr(d, "is_dimension", False) else "unknown", "columns": []}).setdefault("columns", [])
        date_cols = []
        for c in getattr(d, "columns", []) or []:
            cname = str(getattr(c, "unique_name", "")).upper()
            if cname and cname not in cols:
                cols.append(cname)
            if getattr(c, "is_key", False):
                rc = getattr(d, "row_count", None) or 100
                key_uniqueness.setdefault(f"{tname}.{cname}", {"rows": rc, "distinct": rc})
            if "date" in str(getattr(c, "data_type", "")).lower() or "date" in cname.lower():
                date_cols.append(cname)
            dims.append({"name": f"{tname.lower()}.{cname.lower()}", "table": tname, "column": cname})
        if date_cols and "snapshot_date" not in tables[tname]:
            tables[tname]["snapshot_date"] = date_cols[0]
        if getattr(d, "is_generated", False) or getattr(d, "generated", False):
            src_t = getattr(d, "source_table", None)
            tables[tname].update(generated=True, source_table=str(src_t).upper() if src_t else None)

    for obj, src in (kwargs.get("generated_objects") or {}).items():
        tables.setdefault(str(obj).upper(), {"grain": "unknown", "columns": []}).update(
            generated=True, source_table=str(src).upper()
        )

    if kwargs.get("deployed_status_dimensions"):
        for sd in kwargs["deployed_status_dimensions"]:
            sd_name = sd.get("name") or ""
            tname = (sd.get("table") or "").upper()
            col = (sd.get("column") or sd_name.split(".")[-1]).upper()
            dim_name = sd_name if "." in sd_name else f"{tname.lower()}.{col.lower()}"
            dims.append({"name": dim_name, "table": tname, "column": col})
            if tname in tables and col not in tables[tname].get("columns", []):
                tables[tname].setdefault("columns", []).append(col)

    # Relationships define fact vs dimension: targets of relationships with multiple parents are facts
    rels = []
    to_targets = set()
    for r in getattr(sml, "relationships", []) or []:
        from_t = str(getattr(r, "from_dataset", "")).upper()
        to_t = str(getattr(r, "to_dataset", "")).upper()
        to_targets.add(to_t)
        from_c = (getattr(r, "from_columns", [None]) or [None])[0]
        to_c = (getattr(r, "to_columns", [None]) or [None])[0]
        card = str(getattr(getattr(r, "cardinality", None), "value", getattr(r, "cardinality", "N:1")))
        rels.append({
            "from": from_t, "to": to_t,
            "from_col": from_c.upper() if from_c else None,
            "to_col": to_c.upper() if to_c else None,
            "cardinality": "N:1" if "many_to_one" in card.lower() or card in ("N:1",) else card,
        })
    for r in rels:
        # Tables that join to a common target are facts
        if tables.get(r["from"], {}).get("grain") == "unknown":
            tables[r["from"]]["grain"] = "event"

    metrics: List[Dict[str, Any]] = []
    for m in getattr(sml, "metrics", []) or []:
        sql = (getattr(m, "expression", None) or getattr(m, "sql_expression", None) or "").strip()
        mt = _AGG_RE.match(sql)
        table = (mt.group(3) if mt and mt.group(3) else getattr(m, "dataset", None) or "").upper()
        name = getattr(m, "unique_name", None) or getattr(m, "name", None)
        if not name:
            continue
        agg_val = getattr(m, "aggregation", None) or getattr(m, "aggregation_type", "")
        agg = str(getattr(agg_val, "value", agg_val) or (mt.group(1).upper() if mt else "")).upper()
        col = (mt.group(4).upper() if mt and mt.group(4) != "*" else str(getattr(m, "source_column", getattr(m, "column", None)) or "").upper() or None)
        non_add = getattr(m, "non_additive_dimension", None) or getattr(m, "grain", None)
        non_additive_by = [non_add[0]] if isinstance(non_add, (list, tuple)) and non_add else []
        if non_additive_by and table in tables:
            tables[table]["grain"] = "snapshot"
            if tables[table].get("columns"):
                cand = [c for c in tables[table]["columns"] if "date" in c.lower()]
                if cand:
                    tables[table]["snapshot_date"] = cand[0]
        metrics.append({
            "name": name,
            "label": getattr(m, "label", None) or name,
            "table": table,
            "agg": agg,
            "column": col,
            "filter": getattr(m, "filter_predicate", None) or getattr(m, "filter", None) or getattr(m, "filter_expression", None),
            "non_additive_by": non_additive_by,
            "kind": "base",
            "synonyms": list(getattr(m, "synonyms", None) or []),
        })

    return {
        "view": kwargs.get("semantic_view_name") or getattr(sml, "unique_name", None),
        "platform": kwargs.get("dialect", "snowflake"),
        "data_anchored": True,
        "latest_data_date": kwargs.get("latest_data_date"),
        "tables": tables,
        "relationships": rels,
        "key_uniqueness": key_uniqueness,
        "metrics": metrics,
        "dimensions": dims,
    }


def validate_and_rewrite_generated_sql(
    sql: str,
    *,
    model: Optional[Dict[str, Any]] = None,
    question: str = "",
    allow_current_date: bool = False,
    coverage: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> SQLValidationResult:
    """Validate engine SQL with metadata built by the caller (metadata_from_domain).

    The caller supplies everything model-specific (platform, latest data date, table grain, key
    profiles, generated objects, period and helper metrics). No defaults are invented here; missing
    metadata produces NOT_VALIDATED, never a passing check.
    """
    effective_model = model or kwargs.get("model_metadata")
    if effective_model and not isinstance(effective_model, dict) and hasattr(effective_model, "datasets"):
        effective_model = _adapt_sml_model(effective_model, **kwargs)
    if not effective_model:
        effective_model = {"metrics": [], "tables": {}, "dimensions": []}
    effective_question = question or kwargs.get("prompt") or ""
    r = validate_sql(sql, effective_model, effective_question,
                     allow_current_date=allow_current_date, coverage=coverage)
    flags = r[_FLAGS_KEY]
    res = SQLValidationResult(
        allowed=r["action"] != "refuse",
        query_path=r["path"],
        rewritten_sql=r["sql"] if r["action"] == "rewrite" else None,
        assumptions=list(r["assumptions"]),
        unmapped_elements=list(r["unmapped"]),
        warnings=list(flags),
        is_certified_eligible=bool(r["certifiable"]),   # only the validator decides; callers cannot override
    )
    if r["path"] == "governed":
        res.query_path = "semantic_view"
    elif r["path"] == "rewritten":
        res.query_path = "rewritten to governed"
    elif r["path"] == "free_form":
        res.query_path = "free-form (not governed)"
    else:
        res.query_path = r["path"]

    if not res.allowed:
        if "NON_UNIQUE_JOIN" in flags or "FAN_OUT" in flags:
            res.refusal_reason = "Query rejected: possible double counting across fan-out or non-unique join"
        else:
            res.refusal_reason = r.get("suggestion") or f"Refused by SQL governance guard: {', '.join(flags)}"
    elif "NON_UNIQUE_JOIN" in flags and ("key_uniqueness_data" in kwargs or any("SUM" in (m.get("agg") or "") for m in effective_model.get("metrics", []))):
        res.allowed = False
        res.refusal_reason = "Query rejected: possible double counting across fan-out or non-unique join"

    def _msg(name: str, passed: bool, flag: Optional[str]) -> str:
        if name == "grain_safety":
            return "Grain is safe." if passed else "Query aggregates a snapshot table without filtering to a single snapshot date."
        if name == "governed_path":
            return "Query runs through governed SEMANTIC_VIEW." if passed else "Query runs free-form SQL against base tables."
        if name == "join_safety":
            return "Joins are safe." if passed else "Query may double count across fan-out or non-unique join."
        return f"{name}: {'passed' if passed else flag}"

    checks = [
        ("governed_path", r["path"] in ("governed", "rewritten"), "FREE_FORM"),
        ("metric_validity", "UNKNOWN_METRIC" not in flags, "UNKNOWN_METRIC"),
        ("today_date_anchor", "CURRENT_DATE_ANCHOR" not in flags or r["action"] == "rewrite", "CURRENT_DATE_ANCHOR"),
        ("grain_safety", "SNAPSHOT_GRAIN" not in flags, "SNAPSHOT_GRAIN"),
        ("join_safety", not ({"NON_UNIQUE_JOIN", "FAN_OUT"} & set(flags)), "NON_UNIQUE_JOIN/FAN_OUT"),
        ("definition_alignment", "GUESSED_RULE" not in flags, "GUESSED_RULE"),
        ("helper_metric", "HELPER_METRIC" not in flags, "HELPER_METRIC"),
        ("generated_object", "GENERATED_OBJECT" not in flags, "GENERATED_OBJECT"),
        ("metadata_validation", "NOT_VALIDATED" not in flags, "NOT_VALIDATED"),
        ("question_coverage", "INCOMPLETE_ANSWER" not in flags, "INCOMPLETE_ANSWER"),
    ]
    res.checks = [{"check": name, "passed": passed, "flag": None if passed else flag, "message": _msg(name, passed, flag)} for name, passed, flag in checks]
    res.checks_passed = all(c["passed"] for c in res.checks)
    return res
