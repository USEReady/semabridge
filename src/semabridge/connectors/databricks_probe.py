"""Workspace Capability Probe for Databricks SQL Warehouses (Step 6).

Discovers runtime Databricks Unity Catalog Metric View and Genie capabilities
using strictly read-only / harmless dry probes without mutating user tables.
Results are cached in-memory by ``(host, warehouse_id)``.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from semabridge.core.target_rules import TargetCapabilities

logger = logging.getLogger(__name__)

_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE: Dict[Tuple[str, str], TargetCapabilities] = {}


def clear_probe_cache() -> None:
    """Clear the in-memory workspace capability probe cache (useful in tests)."""
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE.clear()


def _clone_capabilities(cap: TargetCapabilities) -> TargetCapabilities:
    return TargetCapabilities(
        probed=cap.probed,
        measure_references=cap.measure_references,
        window_measures=cap.window_measures,
        genie_ready=cap.genie_ready,
        cortex_ready=cap.cortex_ready,
        warehouse_state=cap.warehouse_state,
        probe_errors=list(cap.probe_errors),
    )


def _is_genie_ready(warehouse_info: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Determine whether the SQL warehouse supports Databricks AI/BI Genie."""
    info = warehouse_info or {}
    wh_type = str(info.get("warehouse_type") or "").strip().upper()
    enable_serverless = bool(info.get("enable_serverless_compute", False))
    genie_ready = wh_type in ("PRO", "SERVERLESS") or enable_serverless
    warehouse_state = str(info.get("state") or "UNKNOWN").strip().upper() or "UNKNOWN"
    return genie_ready, warehouse_state


def _build_measure_ref_probe_sql(catalog: str = "", schema: str = "") -> Tuple[str, Optional[str]]:
    """Build harmless probe SQL to test ``MEASURE(`...`)`` inside a measure."""
    if catalog and schema:
        probe_fqn = f"`{catalog}`.`{schema}`.`_semabridge_probe_measure_ref`"
        probe_source = f"{catalog}.information_schema.tables"
        sql = (
            f"CREATE OR REPLACE VIEW {probe_fqn} "
            f"WITH METRICS LANGUAGE YAML AS $$\n"
            f"version: 1.1\n"
            f"source: {probe_source}\n"
            f"measures:\n"
            f"  - name: base_cnt\n"
            f"    expr: COUNT(1)\n"
            f"  - name: double_cnt\n"
            f"    expr: MEASURE(`base_cnt`) * 2\n"
            f"$$"
        )
        return sql, f"DROP VIEW IF EXISTS {probe_fqn}"

    probe_source = f"{catalog}.information_schema.tables" if catalog else "system.information_schema.tables"
    sql = (
        f"EXPLAIN CREATE OR REPLACE VIEW `_semabridge_probe_measure_ref` "
        f"WITH METRICS LANGUAGE YAML AS $$\n"
        f"version: 1.1\n"
        f"source: {probe_source}\n"
        f"measures:\n"
        f"  - name: base_cnt\n"
        f"    expr: COUNT(1)\n"
        f"  - name: double_cnt\n"
        f"    expr: MEASURE(`base_cnt`) * 2\n"
        f"$$"
    )
    return sql, None


def _build_window_measure_probe_sql(catalog: str = "", schema: str = "") -> Tuple[str, Optional[str]]:
    """Build harmless probe SQL to test native YAML ``window:`` measures."""
    if catalog and schema:
        probe_fqn = f"`{catalog}`.`{schema}`.`_semabridge_probe_window`"
        probe_source = f"{catalog}.information_schema.tables"
        sql = (
            f"CREATE OR REPLACE VIEW {probe_fqn} "
            f"WITH METRICS LANGUAGE YAML AS $$\n"
            f"version: 1.1\n"
            f"source: {probe_source}\n"
            f"dimensions:\n"
            f"  - name: probe_date\n"
            f"    expr: CURRENT_DATE()\n"
            f"measures:\n"
            f"  - name: cum_cnt\n"
            f"    expr: COUNT(1)\n"
            f"    window:\n"
            f"      - order: probe_date\n"
            f"        range: cumulative\n"
            f"        semiadditive: last\n"
            f"$$"
        )
        return sql, f"DROP VIEW IF EXISTS {probe_fqn}"

    probe_source = f"{catalog}.information_schema.tables" if catalog else "system.information_schema.tables"
    sql = (
        f"EXPLAIN CREATE OR REPLACE VIEW `_semabridge_probe_window` "
        f"WITH METRICS LANGUAGE YAML AS $$\n"
        f"version: 1.1\n"
        f"source: {probe_source}\n"
        f"dimensions:\n"
        f"  - name: probe_date\n"
        f"    expr: CURRENT_DATE()\n"
        f"measures:\n"
        f"  - name: cum_cnt\n"
        f"    expr: COUNT(1)\n"
        f"    window:\n"
        f"      - order: probe_date\n"
        f"        range: cumulative\n"
        f"        semiadditive: last\n"
        f"$$"
    )
    return sql, None


def probe_databricks_capabilities(
    execute_fn: Optional[Callable[[str], Any]],
    warehouse_info: Optional[Dict[str, Any]] = None,
    *,
    allow_probe: bool = True,
    host: str = "",
    warehouse_id: str = "",
    catalog: str = "",
    schema: str = "",
    use_cache: bool = True,
) -> TargetCapabilities:
    """Probe Databricks workspace capabilities safely without mutating user tables.

    Args:
        execute_fn: Callable accepting a single SQL statement string.
        warehouse_info: Optional warehouse metadata dict (from Databricks SQL
            Warehouses API: ``warehouse_type``, ``enable_serverless_compute``,
            ``state``, ``id`` / ``warehouse_id``, ``host``).
        allow_probe: When ``False``, skips SQL probes and returns unprobed
            defaults plus ``genie_ready`` derived from ``warehouse_info``.
        host: Optional Databricks workspace host (used for cache key).
        warehouse_id: Optional SQL warehouse ID (used for cache key).
        catalog: Optional catalog name for probe source/view scoping.
        schema: Optional schema name for probe view scoping.
        use_cache: Whether to consult/populate the in-memory cache.

    Returns:
        ``TargetCapabilities`` populated with discovered flags and any probe errors.
    """
    info = warehouse_info or {}
    resolved_host = str(host or info.get("host") or "").strip().lower()
    resolved_wh_id = str(warehouse_id or info.get("warehouse_id") or info.get("id") or "").strip()
    cache_key: Optional[Tuple[str, str]] = (
        (resolved_host, resolved_wh_id) if (resolved_host or resolved_wh_id) else None
    )

    genie_ready, warehouse_state = _is_genie_ready(info)

    if not allow_probe:
        return TargetCapabilities(
            probed=False,
            measure_references=True,
            window_measures=True,
            genie_ready=genie_ready,
            warehouse_state=warehouse_state,
            probe_errors=[],
        )

    if use_cache and cache_key is not None:
        with _PROBE_CACHE_LOCK:
            cached = _PROBE_CACHE.get(cache_key)
            if cached is not None:
                return _clone_capabilities(cached)

    if execute_fn is None:
        return TargetCapabilities(
            probed=False,
            measure_references=False,
            window_measures=False,
            genie_ready=genie_ready,
            warehouse_state=warehouse_state,
            probe_errors=["No execute_fn provided for workspace capability probe"],
        )

    probe_errors: list[str] = []

    def _run_probe(cap_name: str, probe_sql: str, cleanup_sql: Optional[str]) -> bool:
        supported = False
        try:
            execute_fn(probe_sql)
            supported = True
        except Exception as exc:
            err_msg = f"{cap_name}: {str(exc)[:240]}"
            probe_errors.append(err_msg)
            logger.warning("Databricks capability probe failed for %s: %s", cap_name, err_msg)
            supported = False
        finally:
            if cleanup_sql and supported:
                try:
                    execute_fn(cleanup_sql)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Databricks capability probe cleanup failed for %s: %s",
                        cap_name,
                        str(cleanup_exc)[:200],
                    )
        return supported

    mref_sql, mref_cleanup = _build_measure_ref_probe_sql(catalog=catalog, schema=schema)
    measure_references = _run_probe("measure_references", mref_sql, mref_cleanup)

    win_sql, win_cleanup = _build_window_measure_probe_sql(catalog=catalog, schema=schema)
    window_measures = _run_probe("window_measures", win_sql, win_cleanup)

    result = TargetCapabilities(
        probed=True,
        measure_references=measure_references,
        window_measures=window_measures,
        genie_ready=genie_ready,
        warehouse_state=warehouse_state,
        probe_errors=probe_errors,
    )

    if use_cache and cache_key is not None:
        with _PROBE_CACHE_LOCK:
            _PROBE_CACHE[cache_key] = _clone_capabilities(result)

    return result
