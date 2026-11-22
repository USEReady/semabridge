"""Points the tests at the implementation.

Default: the product module semabridge.ai.sql_validator (to be implemented, see README).
Set SB_SQL_GUARD_IMPL=reference to run against reference_sql_guard.py (proves the tests are consistent).
"""
import importlib
import os


def load():
    if os.environ.get("SB_SQL_GUARD_IMPL", "").lower() == "reference":
        return importlib.import_module(os.environ.get("SB_SQL_GUARD_MODULE", "reference_sql_guard"))
    try:
        return importlib.import_module("semabridge.ai.sql_validator")
    except ImportError as exc:  # pragma: no cover
        raise ImportError("semabridge.ai.sql_validator is not implemented yet - see README.md "
                          "(contract: find_synonym_collisions, assign_synonyms, resolve_metric, "
                          "validate_sql, status_dimensions, verified_queries_from_cases)") from exc


impl = load()
