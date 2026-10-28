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
    kw = dict(metric_name=name, skip_tier5=not ALLOW_TIER5)
    try:
        return t.translate(dax, alias(home), home, metrics_context=ctx, **kw)
    except TypeError:
        return t.translate(dax, alias(home), home, **kw)


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
