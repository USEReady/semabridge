#!/usr/bin/env python3
"""
semabridge_trace.py - Phase A runtime trace for SemaBridge's relationship builder.

NO source files are changed. When this module is imported it wraps the existing
`_build_relationships` method at runtime and, for every call:
  * records each relationship the builder RECEIVED (with active flag, cardinality, columns),
  * records dataset_aliases, declared_pk_by_alias and relationship_target_alias,
  * records which relationships ended up in the emitted RELATIONSHIPS lines,
  * runs the report-only resolver (relationship_resolver.build_plan) on the same inputs,
and writes one JSON line per call to  output/trace/relationship_trace.jsonl .

The original method's return value is passed through UNCHANGED, and any error inside the
tracer is swallowed, so generated DDL is identical with or without the trace.

How to use (pick one):
  1) Launch SemaBridge through this file (same command you use today, after "--"):
       uv run python semabridge_trace.py -- -m uvicorn <your.app.module>:app --port 8000
       uv run python semabridge_trace.py -- path\\to\\your_cli.py run --project proj-sales-and-table
  2) Or add one line at the top of SemaBridge's entry point while diagnosing:
       import semabridge_trace  # noqa
Then run a dry run / sync of the models you want to trace.

Env: SEMABRIDGE_TRACE_DIR (default: output/trace)
"""
import functools
import importlib
import inspect
import json
import logging
import os
import runpy
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

log = logging.getLogger("semabridge.reltrace")
TRACE_DIR = os.environ.get("SEMABRIDGE_TRACE_DIR", os.path.join("output", "trace"))
MODULE = "semabridge.connectors.relationships_clause_builder"


def _attr(o, name, default=None):
    return getattr(o, name, default) if not isinstance(o, dict) else o.get(name, default)


def _rel_info(rel):
    return {
        "unique_name": str(_attr(rel, "unique_name", "") or ""),
        "from_dataset": _attr(rel, "from_dataset"),
        "from_columns": list(_attr(rel, "from_columns", []) or []),
        "to_dataset": _attr(rel, "to_dataset"),
        "to_columns": list(_attr(rel, "to_columns", []) or []),
        "is_active": bool(_attr(rel, "is_active", True)),
        "cardinality": str(_attr(rel, "cardinality", "") or ""),
    }


def _emitted_line(lines, from_alias, to_alias, from_cols, to_cols):
    """Find the emitted line for this relationship (by aliases + first columns)."""
    if not from_alias or not to_alias:
        return None
    for ln in lines or []:
        up = ln.upper()
        if (f" {from_alias.upper()} (" in up or up.strip().startswith(f"{from_alias.upper()} (")) \
                and f"REFERENCES {to_alias.upper()}" in up:
            if not from_cols or f'"{str(from_cols[0]).upper()}"' in up:
                return ln.strip()
    return None


def _wrap(original):
    @functools.wraps(original)
    def traced(self, relationships, dataset_aliases, dataset_by_name, dataset_col_lookup,
               declared_pk_by_alias, relationship_target_alias, *args, **kwargs):
        lines = original(self, relationships, dataset_aliases, dataset_by_name, dataset_col_lookup,
                         declared_pk_by_alias, relationship_target_alias, *args, **kwargs)
        try:
            from relationship_resolver import build_plan

            def resolve(ds, c):
                # same resolution order as _build_relationships: dataset object -> schema manager,
                # otherwise the identifier sanitizer
                try:
                    obj = (dataset_by_name or {}).get(ds)
                    if obj is not None and hasattr(self, "schema_manager"):
                        return self.schema_manager._resolve_physical_column_name(obj, c)
                    san = getattr(self, "identifier_sanitizer", None)
                    return san.sanitize_column(c) if san else c
                except Exception:
                    return c

            plan = build_plan(relationships or [], dict(dataset_aliases or {}),
                              {k: list(v or []) for k, v in (declared_pk_by_alias or {}).items()},
                              {k: set(v or []) for k, v in (dataset_col_lookup or {}).items()},
                              resolve_column=resolve, allow_cardinality_hint=True)
            records = []
            for rel, dec in zip(relationships or [], plan.decisions):
                info = _rel_info(rel)
                f_alias = (dataset_aliases or {}).get(info["from_dataset"])
                t_alias = (dataset_aliases or {}).get(info["to_dataset"])
                rf = [resolve(info["from_dataset"], c) for c in info["from_columns"]]
                rt = [resolve(info["to_dataset"], c) for c in info["to_columns"]]
                remap = (relationship_target_alias or {}).get((info["to_dataset"], str(rt[0]).upper())) if rt else None
                emitted = _emitted_line(lines, f_alias, remap or t_alias, rf, rt)
                records.append({**info, "resolved_from_columns": rf, "resolved_to_columns": rt,
                                "from_alias": f_alias, "to_alias": t_alias, "target_alias_remap": remap,
                                "emitted": emitted is not None, "emitted_line": emitted,
                                "plan_decision": dec.decision, "plan_reason": dec.reason,
                                "plan_to_logical": dec.to_logical})
            os.makedirs(TRACE_DIR, exist_ok=True)
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "datasets": sorted((dataset_aliases or {}).keys()),
                "dataset_aliases": dataset_aliases,
                "declared_pk_by_alias": {k: list(v or []) for k, v in (declared_pk_by_alias or {}).items()},
                "relationship_target_alias": {f"{k[0]}|{k[1]}": v for k, v in (relationship_target_alias or {}).items()},
                "received": len(relationships or []), "emitted_lines": len(lines or []),
                "relationships": records,
                "plan": plan.as_dict(),
            }
            with open(os.path.join(TRACE_DIR, "relationship_trace.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
            not_emitted = [r for r in records if not r["emitted"]]
            log.warning("[RelTrace] received=%d emitted=%d not_emitted=%d -> %s",
                        entry["received"], entry["emitted_lines"], len(not_emitted),
                        [f"{r['from_dataset']}->{r['to_dataset']}" for r in not_emitted])
        except Exception as exc:          # never affect SemaBridge
            log.warning("[RelTrace] tracer error (ignored): %s", exc)
        return lines
    traced.__semabridge_traced__ = True
    return traced


def install():
    mod = importlib.import_module(MODULE)
    patched = 0
    for _, cls in inspect.getmembers(mod, inspect.isclass):
        fn = cls.__dict__.get("_build_relationships")
        if fn and not getattr(fn, "__semabridge_traced__", False):
            setattr(cls, "_build_relationships", _wrap(fn))
            patched += 1
    log.warning("[RelTrace] installed on %d class(es) in %s; writing to %s", patched, MODULE, TRACE_DIR)
    print(f"[RelTrace] installed on {patched} class(es); trace -> {os.path.join(TRACE_DIR, 'relationship_trace.jsonl')}")
    return patched


try:
    install()
except Exception as _exc:  # pragma: no cover
    print(f"[RelTrace] not installed: {_exc}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        sys.exit(__doc__)
    if argv[0] == "-m":
        sys.argv = [argv[1]] + argv[2:]
        runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = argv
        runpy.run_path(argv[0], run_name="__main__")
