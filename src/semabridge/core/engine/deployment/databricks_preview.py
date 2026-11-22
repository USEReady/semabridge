"""Dry-run preview for Databricks Unity Catalog metric views.

Calls ``DatabricksPublisher.plan(sml_model, lookup=...)`` when available on the
publisher, normalizing the returned plan to the shared contract:

    {
        "target": "catalog.schema",
        "source": "publisher" | "derived",
        "views": [
            {
                "name": fq_view_name,
                "kind": "metric_view" | "view",
                "fact": dataset_name,
                "source_table": fq_source_table,
                "joins": [{"dataset": ..., "table": ..., "on": ...}],
                "dimensions": [{"name": ..., "expr": ..., "display_name": ..., "synonyms": [...]}],
                "measures": [{"name": ..., "expr": ..., "status": "ok" | "placeholder", "reason": ...}],
                "skipped": bool,
                "reason": str,
            }
        ],
        "missing_tables": [...],
        "blocked": [...],
        "warnings": [...],
        "error": None | str,
    }

When ``DatabricksPublisher.plan`` is not yet implemented or no live Databricks
connection is configured for the dry run, derives a minimal read-only plan
directly from the SML model (one metric view per fact dataset) and marks
``"source": "derived"``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from semabridge.core.engine.context import RunContext
from semabridge.core.engine.exceptions import DeploymentError
from semabridge.utils.identifiers import (
    databricks_sanitize_identifier,
    databricks_target_name,
    databricks_view_name,
    format_databricks_fqn,
    quote_databricks_identifier,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

_DATABRICKS_TYPES = frozenset({"databricks", "databricks_sql", "dbx", "databricks_metric_view"})


def _is_databricks_type(value: Any) -> bool:
    return str(value or "").strip().lower() in _DATABRICKS_TYPES


def _extract_target_dict(context: RunContext) -> Dict[str, Any]:
    """Return the raw Databricks target mapping from config/payload, if any."""
    payload = getattr(context, "config_payload", None)
    if isinstance(payload, dict):
        t = payload.get("target")
        if isinstance(t, dict) and _is_databricks_type(t.get("type")):
            return t
        targets = payload.get("targets")
        if isinstance(targets, list):
            for tc in targets:
                if isinstance(tc, dict) and _is_databricks_type(tc.get("type")):
                    return tc
        dbx = payload.get("databricks")
        if isinstance(dbx, dict):
            return dbx

    cfg = getattr(context, "config", None)
    if cfg is not None:
        targets = getattr(cfg, "targets", None) or []
        for tc in targets:
            if isinstance(tc, dict) and _is_databricks_type(tc.get("type")):
                return tc
        target_obj = getattr(cfg, "target", None)
        if target_obj is not None and _is_databricks_type(getattr(target_obj, "type", None)):
            return {
                "type": getattr(target_obj, "type", "databricks"),
                "identity_id": getattr(target_obj, "identity_id", ""),
                "catalog": getattr(target_obj, "catalog", None),
                "schema": getattr(target_obj, "schema", None) or getattr(target_obj, "schema_name", None),
            }
    return {}


def target_databricks_config(context: RunContext) -> Any:
    """Resolve the Databricks config this run's target uses.

    Mirrors ``target_snowflake_config``: uses the linked Account row when
    ``identity_id`` is configured, otherwise ``context.config.databricks``,
    and applies any explicit target ``catalog``/``schema`` overrides from the
    project config. When no live credentials are configured (e.g. an offline
    dry run), returns a lightweight namespace carrying ``catalog`` and
    ``schema_name`` so callers can still derive target names cleanly.
    """
    target_dict = _extract_target_dict(context)
    identity_id = str(target_dict.get("identity_id") or "").strip()

    base_cfg = None
    try:
        base_cfg = getattr(context.config, "_databricks", None) or context.config.databricks
    except Exception as cfg_exc:
        logger.debug("Global DatabricksConfig unavailable for run %s: %s", getattr(context, "run_id", None), cfg_exc)

    resolved_cfg = base_cfg
    if identity_id:
        try:
            from sqlalchemy import select
            from semabridge.auth.credential_builder import build_databricks_config
            from semabridge.repository.orm.models import Account
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == "DATABRICKS",
                        Account.id == identity_id,
                    )
                ).scalars().first()
                if account:
                    resolved_cfg = build_databricks_config(account, session, base_cfg)
                else:
                    logger.warning(
                        "No linked Databricks account found for identity_id %s; falling back to global settings",
                        identity_id,
                    )
        except Exception as acct_exc:
            logger.warning(
                "Could not resolve Databricks account %s for dry-run preview: %s",
                identity_id,
                acct_exc,
            )

    override_catalog = str(target_dict.get("catalog") or "").strip()
    override_schema = str(target_dict.get("schema") or target_dict.get("schema_name") or "").strip()

    if resolved_cfg is None:
        return SimpleNamespace(
            host="",
            warehouse_id="",
            catalog=override_catalog or "main",
            schema_name=override_schema or "semabridge",
        )

    if override_catalog or override_schema:
        if hasattr(resolved_cfg, "model_copy"):
            updates: Dict[str, Any] = {}
            if override_catalog:
                updates["catalog"] = override_catalog
            if override_schema:
                updates["schema_name"] = override_schema
            resolved_cfg = resolved_cfg.model_copy(update=updates)
        else:
            resolved_cfg = SimpleNamespace(
                host=getattr(resolved_cfg, "host", ""),
                warehouse_id=getattr(resolved_cfg, "warehouse_id", ""),
                catalog=override_catalog or getattr(resolved_cfg, "catalog", "main"),
                schema_name=override_schema or getattr(resolved_cfg, "schema_name", "semabridge"),
            )
    return resolved_cfg


def _catalog_and_schema(dbx_cfg: Any) -> Tuple[str, str]:
    catalog = str(getattr(dbx_cfg, "catalog", None) or "main").strip() or "main"
    schema = (
        str(getattr(dbx_cfg, "schema_name", None) or getattr(dbx_cfg, "schema", None) or "semabridge").strip()
        or "semabridge"
    )
    return catalog, schema


def _normalize_plan(raw: Dict[str, Any], *, default_target: str, source: str) -> Dict[str, Any]:
    """Ensure every contract key is present and well-typed."""
    views_in = raw.get("views") if isinstance(raw.get("views"), list) else []
    views: List[Dict[str, Any]] = []
    for v in views_in:
        if not isinstance(v, dict):
            continue
        views.append({
            "name": str(v.get("name") or ""),
            "kind": str(v.get("kind") or "metric_view"),
            "fact": str(v.get("fact") or ""),
            "source_table": str(v.get("source_table") or ""),
            "joins": list(v.get("joins") or []),
            "dimensions": list(v.get("dimensions") or []),
            "measures": list(v.get("measures") or []),
            "skipped": bool(v.get("skipped", False)),
            "reason": str(v.get("reason") or ""),
        })
    return {
        "target": str(raw.get("target") or default_target),
        "source": str(raw.get("source") or source),
        "views": views,
        "missing_tables": list(raw.get("missing_tables") or []),
        "blocked": list(raw.get("blocked") or []),
        "warnings": list(raw.get("warnings") or []),
        "error": raw.get("error"),
    }


def _select_fact_datasets(sml_model: Any) -> List[Any]:
    """Identify fact datasets on *sml_model* for the derived fallback plan."""
    datasets = list(getattr(sml_model, "datasets", None) or [])
    visible = [ds for ds in datasets if not getattr(ds, "is_hidden", False)]
    pool = visible or datasets
    if not pool:
        return []

    explicit_facts = [ds for ds in pool if getattr(ds, "is_fact", False)]
    if explicit_facts:
        return explicit_facts

    relationships = list(getattr(sml_model, "relationships", None) or [])
    from_names = {
        str(getattr(r, "from_dataset", "") or "").strip().lower()
        for r in relationships
        if getattr(r, "is_active", True) and getattr(r, "from_dataset", None)
    }
    metrics = list(getattr(sml_model, "metrics", None) or [])
    metric_ds_names = {
        str(getattr(m, "dataset", "") or "").strip().lower()
        for m in metrics
        if getattr(m, "dataset", None)
    }
    inferred = [
        ds for ds in pool
        if str(getattr(ds, "unique_name", "") or "").strip().lower() in (from_names | metric_ds_names)
    ]
    return inferred or pool


def _derive_minimal_plan(
    sml_model: Any,
    *,
    catalog: str,
    schema: str,
    behavior: Any = None,
) -> Dict[str, Any]:
    """Derive a minimal read-only Databricks plan directly from *sml_model*."""
    target_str = f"{catalog}.{schema}"
    if sml_model is None:
        return {
            "target": target_str,
            "source": "derived",
            "views": [],
            "missing_tables": [],
            "blocked": [],
            "warnings": [],
            "error": None,
        }

    dbx_behavior = getattr(behavior, "databricks", None) if behavior is not None else None
    view_prefix = str(getattr(dbx_behavior, "view_prefix", None) or "mv").strip() or "mv"
    src_catalog_default = str(getattr(dbx_behavior, "source_catalog", None) or "").strip() or catalog
    src_schema_default = str(getattr(dbx_behavior, "source_schema", None) or "").strip() or schema

    model_name = str(
        getattr(sml_model, "unique_name", None) or getattr(sml_model, "label", None) or "model"
    )
    datasets = list(getattr(sml_model, "datasets", None) or [])
    ds_by_lower = {
        str(getattr(ds, "unique_name", "") or "").strip().lower(): ds
        for ds in datasets
    }
    relationships = list(getattr(sml_model, "relationships", None) or [])
    metrics = list(getattr(sml_model, "metrics", None) or [])

    fact_datasets = _select_fact_datasets(sml_model)
    views: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for ds in fact_datasets:
        ds_name = str(getattr(ds, "unique_name", None) or getattr(ds, "label", None) or "dataset")
        ds_lower = ds_name.strip().lower()
        src_cat = str(getattr(ds, "source_database", None) or "").strip() or src_catalog_default
        src_sch = str(getattr(ds, "source_schema", None) or "").strip() or src_schema_default
        src_tbl = databricks_sanitize_identifier(getattr(ds, "source_table", None) or ds_name)
        source_table_fq = format_databricks_fqn(src_cat, src_sch, src_tbl)

        view_short = databricks_view_name(model_name, ds_name, view_prefix=view_prefix)
        view_fq = format_databricks_fqn(catalog, schema, view_short)

        joins: List[Dict[str, Any]] = []
        for rel in relationships:
            if not getattr(rel, "is_active", True):
                continue
            if str(getattr(rel, "from_dataset", "") or "").strip().lower() != ds_lower:
                continue
            to_name = str(getattr(rel, "to_dataset", "") or "").strip()
            to_ds = ds_by_lower.get(to_name.lower())
            to_cat = str(getattr(to_ds, "source_database", None) or "").strip() if to_ds else ""
            to_sch = str(getattr(to_ds, "source_schema", None) or "").strip() if to_ds else ""
            to_tbl = databricks_sanitize_identifier(
                (getattr(to_ds, "source_table", None) if to_ds else None) or to_name
            )
            to_fq = format_databricks_fqn(to_cat or src_cat, to_sch or src_sch, to_tbl)
            from_cols = list(getattr(rel, "from_columns", None) or [])
            to_cols = list(getattr(rel, "to_columns", None) or [])
            if from_cols and to_cols:
                on_clause = " AND ".join(
                    f"{quote_databricks_identifier(fc)} = {quote_databricks_identifier(tc)}"
                    for fc, tc in zip(from_cols, to_cols)
                )
            else:
                on_clause = ""
            joins.append({
                "dataset": to_name,
                "table": to_fq,
                "on": on_clause,
            })

        dimensions: List[Dict[str, Any]] = []
        for col in (getattr(ds, "columns", None) or []):
            if getattr(col, "is_hidden", False):
                continue
            col_name = str(getattr(col, "unique_name", "") or "")
            if not col_name:
                continue
            dim_name = databricks_target_name(col_name, entity_kind="column")
            dimensions.append({
                "name": dim_name,
                "expr": quote_databricks_identifier(dim_name),
                "display_name": str(getattr(col, "label", None) or col_name),
                "synonyms": list(getattr(col, "synonyms", None) or []),
            })

        ds_metrics = [
            m for m in metrics
            if str(getattr(m, "dataset", "") or "").strip().lower() == ds_lower
        ]
        # If there is a single fact dataset and some metrics have no dataset assigned, attach them.
        if len(fact_datasets) == 1:
            ds_metrics.extend(
                m for m in metrics
                if not str(getattr(m, "dataset", "") or "").strip()
            )

        measures: List[Dict[str, Any]] = []
        for m in ds_metrics:
            m_raw = str(getattr(m, "unique_name", "") or "")
            if not m_raw:
                continue
            m_name = databricks_target_name(m_raw, entity_kind="metric")
            sql_expr = str(getattr(m, "sql_expression", None) or "").strip()
            dax_expr = str(getattr(m, "expression", None) or "").strip()
            if sql_expr:
                measures.append({
                    "name": m_name,
                    "expr": sql_expr,
                    "status": "ok",
                    "reason": "",
                })
            elif dax_expr:
                measures.append({
                    "name": m_name,
                    "expr": dax_expr,
                    "status": "placeholder",
                    "reason": "Derived preview: SQL translation runs during publish",
                })
            else:
                measures.append({
                    "name": m_name,
                    "expr": "",
                    "status": "placeholder",
                    "reason": "Missing expression",
                })

        skipped = not dimensions and not measures
        views.append({
            "name": view_fq,
            "kind": "metric_view",
            "fact": ds_name,
            "source_table": source_table_fq,
            "joins": joins,
            "dimensions": dimensions,
            "measures": measures,
            "skipped": skipped,
            "reason": "Dataset has no dimensions or measures" if skipped else "",
        })

    if not views:
        warnings.append("No fact datasets found in the semantic model to generate Databricks metric views.")

    return {
        "target": target_str,
        "source": "derived",
        "views": views,
        "missing_tables": [],
        "blocked": [],
        "warnings": warnings,
        "error": None,
    }


def preview_databricks_plan(
    context: RunContext,
    *,
    publisher: Any = None,
    lookup: bool = False,
) -> Dict[str, Any]:
    """Produce the dry-run Databricks metric-view plan for *context*.

    Calls ``publisher.plan(context.sml_model, lookup=lookup)`` when the
    publisher implements ``plan``; otherwise derives a minimal plan directly
    from ``context.sml_model`` with ``source="derived"``.
    """
    dbx_cfg = target_databricks_config(context)
    catalog, schema = _catalog_and_schema(dbx_cfg)
    default_target = f"{catalog}.{schema}"

    active_publisher = publisher
    if active_publisher is None:
        host = str(getattr(dbx_cfg, "host", "") or "").strip()
        warehouse_id = str(getattr(dbx_cfg, "warehouse_id", "") or "").strip()
        if host and warehouse_id:
            try:
                from semabridge.connectors.databricks_publisher import DatabricksPublisher

                active_publisher = DatabricksPublisher(dbx_cfg, behavior=context.behavior)
                if lookup:
                    from semabridge.core.engine.deployment.databricks import (
                        ensure_databricks_target_namespace,
                    )

                    try:
                        resolved = ensure_databricks_target_namespace(
                            context, dbx_cfg, publisher=active_publisher
                        )
                    except DeploymentError as ns_exc:
                        # Same check the sync runs: surface it in the dry run
                        # instead of a plan that can never deploy.
                        return {
                            "target": default_target,
                            "source": "publisher",
                            "views": [],
                            "missing_tables": [],
                            "blocked": [str(ns_exc)],
                            "warnings": [],
                            "error": str(ns_exc),
                        }
                    if resolved is not dbx_cfg:
                        dbx_cfg = resolved
                        catalog, schema = _catalog_and_schema(dbx_cfg)
                        default_target = f"{catalog}.{schema}"
                        active_publisher = DatabricksPublisher(dbx_cfg, behavior=context.behavior)
            except Exception as init_exc:
                logger.warning(
                    "Databricks dry-run preview: could not initialize DatabricksPublisher (%s); "
                    "falling back to derived plan",
                    init_exc,
                )

    plan_fn = getattr(active_publisher, "plan", None) if active_publisher is not None else None
    if callable(plan_fn):
        try:
            raw_plan = plan_fn(context.sml_model, lookup=lookup)
            if isinstance(raw_plan, dict):
                return _normalize_plan(raw_plan, default_target=default_target, source="publisher")
            logger.warning(
                "DatabricksPublisher.plan returned non-dict (%s); falling back to derived plan",
                type(raw_plan).__name__,
            )
        except Exception as plan_exc:
            logger.warning(
                "DatabricksPublisher.plan failed for %s: %s",
                default_target,
                plan_exc,
            )
            return {
                "target": default_target,
                "source": "publisher",
                "views": [],
                "missing_tables": [],
                "blocked": [],
                "warnings": [],
                "error": str(plan_exc),
            }

    return _derive_minimal_plan(
        context.sml_model,
        catalog=catalog,
        schema=schema,
        behavior=context.behavior,
    )
