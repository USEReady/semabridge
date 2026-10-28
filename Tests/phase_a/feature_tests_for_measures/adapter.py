"""
adapter.py - the ONLY file that knows how to call SemaBridge. Wire it once to your repo's API;
the cases and the runner never change.

translate(dax, home_table, name, context) must return a dict:
  {"status": "ok" | "fail" | "not_applicable" | "error",
   "sql": <Snowflake metric expression or None>,
   "reason": <text, optional>}

Default implementation: semabridge.converter.dax_translator.DAXTranslator, deterministic tiers only
(set SEMABRIDGE_TIER5=1 to allow the LLM tier). Measures referenced by the case are translated first and
passed as metrics_context, the same way SemaBridge resolves dependencies.
"""
import os
import re
from types import SimpleNamespace

ALLOW_TIER5 = os.environ.get("SEMABRIDGE_TIER5") == "1"
NA_WORDS = re.compile(r"by[_ ]design|not applicable|exclu|string|text|non-?numeric|selectedvalue|what-?if|"
                      r"rank|isinscope|filter context|all\(|window metric|non-?additive|unrelated|time intelligence", re.I)
_translator = None


def alias(table):
    """SQL alias SemaBridge uses for a table's columns (home table alias passed to translate())."""
    from semabridge.utils.naming import to_alias
    return to_alias(table)


def _get():
    global _translator
    if _translator is None:
        from semabridge.converter.dax_translator import DAXTranslator
        _translator = DAXTranslator()
    return _translator


def _call(dax, home, name, ctx):
    t = _get()
    current_tables, current_rels, current_inactive = None, None, None
    try:
        import sys
        mod = sys.modules.get("__main__")
        if mod and hasattr(mod, "TABLES"):
            current_tables = getattr(mod, "TABLES", None)
            current_rels = getattr(mod, "RELATIONSHIPS", None)
            current_inactive = getattr(mod, "INACTIVE", None)
        else:
            import run_feature_tests as rft
            current_tables = getattr(rft, "TABLES", None)
            current_rels = getattr(rft, "RELATIONSHIPS", None)
            current_inactive = getattr(rft, "INACTIVE", None)
    except Exception:
        pass

    if current_tables is None:
        from fixture import TABLES, RELATIONSHIPS
        try:
            from fixture import INACTIVE
        except ImportError:
            INACTIVE = []
        current_tables, current_rels, current_inactive = TABLES, RELATIONSHIPS, INACTIVE

    table_key_map = {}
    for t_name, df in (current_tables or {}).items():
        key_col = next((c for c in df.columns if re.search(r"(?:^|_)id$", c, re.I) or re.search(r"(?:^|_)key$", c, re.I)), None)
        if not key_col:
            key_col = next((c for c in df.columns if re.search(r"(id|key)", c, re.I)), df.columns[0] if len(df.columns) > 0 else "ID")
        table_key_map[t_name.casefold()] = key_col
        table_key_map[alias(t_name).casefold()] = key_col

    rels = [
        SimpleNamespace(from_dataset=ft, from_columns=[fc], to_dataset=tt, to_columns=[tc], is_active=True)
        for ft, fc, tt, tc in (current_rels or ())
    ] + [
        SimpleNamespace(from_dataset=ft, from_columns=[fc], to_dataset=tt, to_columns=[tc], is_active=False)
        for ft, fc, tt, tc in (current_inactive or ())
    ]

    kw = dict(
        metric_name=name,
        skip_tier5=not ALLOW_TIER5,
        relationships=rels,
        table_key_map=table_key_map,
    )
    try:
        return t.translate(dax, alias(home), home, metrics_context=ctx, **kw)
    except TypeError:
        kw.pop("table_key_map", None)
        return t.translate(dax, alias(home), home, metrics_context=ctx, **kw)


def translate(dax, home_table, name, context=None):
    ctx = []
    for cname, (chome, cdax) in (context or {}).items():
        cres = translate(cdax, chome, cname)
        ctx.append(SimpleNamespace(unique_name=cname, name=cname, label=cname, expression=cdax, dataset=chome,
                                   sql_expression=cres["sql"], tier=1, sync_enabled=cres["sql"] is not None))
    try:
        res = _call(dax, home_table, name, ctx)
    except Exception as exc:                     # a crash is a failure, with the message
        return {"status": "error", "sql": None, "reason": f"{type(exc).__name__}: {exc}"}
    sql = getattr(res, "sql", None)
    reason = " ".join(str(getattr(res, a, "") or "") for a in ("failure_reason", "reason", "exclusion_reason", "status"))
    if getattr(res, "is_success", False) and sql and not re.match(r"^\(?\s*CAST\s*\(\s*NULL", sql, re.I):
        return {"status": "ok", "sql": sql, "reason": reason.strip()}
    if getattr(res, "sync_enabled", True) is False or NA_WORDS.search(reason):
        return {"status": "not_applicable", "sql": None, "reason": reason.strip()}
    return {"status": "fail", "sql": None, "reason": reason.strip()}
