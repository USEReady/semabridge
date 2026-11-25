from __future__ import annotations

import time
import uuid
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import Settings, get_settings
from semabridge.core.config_loader import get_project_file_path
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.core.source_format import (
    SourceFormat,
    from_fabric_tmsl,
    from_pbix_tmsl,
    from_snowflake_metadata,
)
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel, SMLRelationship
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.exceptions import (
    ConfigValidationError,
    AuthenticationError,
    ExtractionError,
    SourceFormatError,
    ConversionError,
    PersistenceError,
    DeploymentError,
)

logger = get_logger(__name__)

def _extract_databricks_target_dict(context: RunContext) -> Dict[str, Any]:
    """Extract target dict (identity_id, catalog, schema, warehouse_id) for Databricks."""
    target_dict: Dict[str, Any] = {}
    target_configs = getattr(context.config, "targets", None) or []
    for tc in target_configs:
        if isinstance(tc, dict):
            if str(tc.get("type") or "").strip().lower() == "databricks":
                target_dict.update(tc)
                break
        else:
            tc_type = str(getattr(getattr(tc, "type", None), "value", getattr(tc, "type", "")) or "").strip().lower()
            if tc_type == "databricks":
                for attr, key in (
                    ("identity_id", "identity_id"),
                    ("catalog", "catalog"),
                    ("database", "database"),
                    ("schema_name", "schema"),
                    ("schema", "schema"),
                    ("warehouse_id", "warehouse_id"),
                    ("warehouse", "warehouse"),
                ):
                    val = getattr(tc, attr, None)
                    if val and not target_dict.get(key):
                        target_dict[key] = str(val).strip()
                break
    target_cfg = getattr(context.config, "target", None)
    if target_cfg is not None:
        for attr, key in (
            ("identity_id", "identity_id"),
            ("catalog", "catalog"),
            ("database", "database"),
            ("schema_name", "schema"),
            ("schema", "schema"),
            ("warehouse_id", "warehouse_id"),
            ("warehouse", "warehouse"),
        ):
            val = getattr(target_cfg, attr, None)
            if val and not target_dict.get(key):
                target_dict[key] = str(val).strip()
    if not target_dict.get("identity_id") and getattr(context, "account_id", None):
        target_dict["identity_id"] = str(context.account_id).strip()
    return target_dict


def resolve_databricks_config_for_context(
    context: RunContext,
    *,
    allow_offline_stub: bool = False,
):
    """Resolve a thread-safe DatabricksConfig from linked Account, CredentialManager, or env.

    Never crashes on missing .env DATABRICKS_* vars when credentials are stored in DB.
    """
    from semabridge.core.settings import DatabricksConfig
    from semabridge.auth.credential_builder import build_databricks_config

    project_target = _extract_databricks_target_dict(context)
    identity_id = str(project_target.get("identity_id") or "").strip()

    base_cfg = None
    try:
        base_cfg = context.config.databricks
    except Exception as exc:
        logger.debug("Global env DatabricksConfig unavailable (will resolve from DB): %s", exc)

    # 1. Linked Account by identity_id (or active DATABRICKS Account in DB)
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            account = None
            if identity_id:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == "DATABRICKS",
                        Account.id == identity_id,
                    )
                ).scalars().first()
                if not account and not allow_offline_stub:
                    raise DeploymentError(
                        f"No Databricks account found for identity_id '{identity_id}'. "
                        "Please link this account in the Connections panel."
                    )
            if account is None:
                account = session.execute(
                    select(Account).where(Account.connector_type == "DATABRICKS")
                ).scalars().first()

            if account is not None:
                cfg = build_databricks_config(
                    account,
                    session,
                    base_cfg,
                    project_target=project_target,
                )
                logger.info(
                    "Databricks config resolved from account %s (%s) — no os.environ mutation",
                    getattr(account, "tag", "default"),
                    getattr(account, "id", ""),
                )
                return cfg
    except DeploymentError:
        raise
    except Exception as exc:
        logger.warning("Databricks Account lookup failed: %s", exc)

    # 2. CredentialManager (Settings > Connections encrypted store)
    try:
        from semabridge.repository.credential_manager import CredentialManager
        from semabridge.api.services.databricks_connection_service import decrypt_databricks_secret

        cm = CredentialManager()
        uid = getattr(context, "user_id", None) or getattr(context, "owner_id", None) or 0
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            uid_int = 0
        creds = cm.get_credentials("databricks", mask_secrets=False, user_id=uid_int) or {}
        if not creds.get("host") and uid_int != 0:
            creds = cm.get_credentials("databricks", mask_secrets=False, user_id=0) or {}
        host = str(creds.get("host") or "").strip()
        if host:
            token = decrypt_databricks_secret(creds.get("token"))
            access_token = decrypt_databricks_secret(creds.get("access_token"))
            refresh_token = decrypt_databricks_secret(creds.get("refresh_token"))
            client_secret = decrypt_databricks_secret(creds.get("client_secret"))
            catalog = str(
                project_target.get("catalog")
                or project_target.get("database")
                or creds.get("catalog")
                or "main"
            ).strip()
            schema = str(
                project_target.get("schema")
                or creds.get("schema_name")
                or creds.get("schema")
                or "semabridge"
            ).strip()
            warehouse_id = str(
                project_target.get("warehouse_id")
                or project_target.get("warehouse")
                or creds.get("warehouse_id")
                or ""
            ).strip()
            return DatabricksConfig(
                host=host,
                auth_type=str(creds.get("auth_type") or "pat"),
                token=token or None,
                access_token=access_token or None,
                refresh_token=refresh_token or None,
                client_id=str(creds.get("client_id") or "").strip() or None,
                client_secret=client_secret or None,
                warehouse_id=warehouse_id,
                catalog=catalog,
                schema_name=schema,
            )
    except Exception as exc:
        logger.warning("Databricks CredentialManager resolution failed: %s", exc)

    # 3. Global env DatabricksConfig (with project target overrides)
    if base_cfg is not None:
        updates: Dict[str, Any] = {}
        cat = str(project_target.get("catalog") or project_target.get("database") or "").strip()
        sch = str(project_target.get("schema") or "").strip()
        wh = str(project_target.get("warehouse_id") or project_target.get("warehouse") or "").strip()
        if cat:
            updates["catalog"] = cat
        if sch:
            updates["schema_name"] = sch
        if wh:
            updates["warehouse_id"] = wh
        return base_cfg.model_copy(update=updates) if updates else base_cfg

    if allow_offline_stub:
        return DatabricksConfig(
            host="offline.databricks.local",
            warehouse_id=str(project_target.get("warehouse_id") or project_target.get("warehouse") or ""),
            catalog=str(project_target.get("catalog") or project_target.get("database") or "main"),
            schema_name=str(project_target.get("schema") or "semabridge"),
        )

    raise DeploymentError(
        "Databricks credentials are not configured. Add your Workspace Host and Auth in Settings > Connections."
    )


def _auto_discover_warehouse_id(cfg) -> str:
    """Discover a SQL Warehouse ID from the Databricks workspace when none is configured.

    Prefers a RUNNING warehouse, then STARTING, then any available warehouse
    (e.g. Serverless warehouses that auto-start on statement execution).
    """
    import requests as _requests
    from semabridge.utils.tls import requests_verify

    host = str(getattr(cfg, "host", "") or "").strip().rstrip("/")
    if not host:
        return ""
    if host.startswith("https://"):
        host = host[len("https://"):]
    elif host.startswith("http://"):
        host = host[len("http://"):]

    token = ""
    for attr in ("token", "access_token"):
        val = getattr(cfg, attr, None)
        if val is not None:
            token = val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)
            token = token.strip()
            if token:
                break

    verify = requests_verify()
    if not token and getattr(cfg, "client_id", None) and getattr(cfg, "client_secret", None):
        secret = cfg.client_secret.get_secret_value() if hasattr(cfg.client_secret, "get_secret_value") else str(cfg.client_secret)
        try:
            oidc = _requests.post(
                f"https://{host}/oidc/v1/token",
                data={"grant_type": "client_credentials", "scope": "all-apis"},
                auth=(str(cfg.client_id), secret),
                timeout=15,
                verify=verify,
            )
            if oidc.status_code == 200:
                token = str((oidc.json() or {}).get("access_token") or "").strip()
        except Exception as exc:
            logger.warning("Databricks OIDC token lookup during warehouse auto-discovery failed: %s", exc)

    if not token:
        return ""

    try:
        resp = _requests.get(
            f"https://{host}/api/2.0/sql/warehouses",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            verify=verify,
        )
        if resp.status_code != 200:
            logger.warning("Databricks warehouse auto-discovery returned HTTP %s", resp.status_code)
            return ""
        warehouses = (resp.json() or {}).get("warehouses") or []
        if not warehouses:
            return ""
        priority = {"RUNNING": 0, "STARTING": 1, "STOPPED": 2}
        ranked = sorted(
            [w for w in warehouses if w.get("id")],
            key=lambda w: (
                priority.get(str(w.get("state") or "").upper(), 3),
                0 if w.get("enable_serverless_compute") else 1,
                str(w.get("name") or ""),
            ),
        )
        if ranked:
            chosen = ranked[0]
            logger.info(
                "Auto-discovered Databricks SQL Warehouse '%s' (id=%s, state=%s)",
                chosen.get("name"),
                chosen.get("id"),
                chosen.get("state"),
            )
            return str(chosen["id"])
    except Exception as exc:
        logger.warning("Databricks SQL Warehouse auto-discovery failed: %s", exc)
    return ""


def _first_column(rows: Any) -> list[str]:
    """First column of every row returned by ``publisher.execute_statements``."""
    values: list[str] = []
    for payload in rows or []:
        result = payload.get("result") if isinstance(payload, dict) else None
        data = result.get("data_array") if isinstance(result, dict) else None
        for row in data or []:
            if isinstance(row, (list, tuple)) and row and row[0] is not None:
                values.append(str(row[0]).strip())
    return [v for v in values if v]


def _project_chose(context: RunContext, *keys: str) -> bool:
    target = _extract_databricks_target_dict(context)
    return any(str(target.get(k) or "").strip() for k in keys)


def ensure_databricks_target_namespace(
    context: RunContext,
    cfg: Any,
    *,
    publisher: Any,
) -> Any:
    """Verify the target catalog.schema exists before anything is deployed.

    A catalog/schema the project never chose (the old hard-coded ``main`` /
    ``semabridge`` defaults) falls back to the workspace's default catalog
    (``current_catalog()``) and its ``default`` schema. A catalog/schema the
    project *did* choose is never silently replaced: a missing one fails fast
    with the names that do exist, instead of every metric view failing later
    with NO_SUCH_CATALOG_EXCEPTION. Nothing is ever created.

    Returns ``cfg`` unchanged, or a copy pointing at the resolved namespace.
    """
    catalog = str(getattr(cfg, "catalog", "") or "").strip()
    schema = str(getattr(cfg, "schema_name", "") or "").strip()

    catalogs = _first_column(publisher.execute_statements(["SHOW CATALOGS"]))
    if not catalogs:
        # The lookup itself returned nothing we can trust; let deploy proceed
        # and report the real error rather than guessing.
        logger.warning("Databricks SHOW CATALOGS returned no rows; skipping namespace check")
        return cfg
    by_lower = {c.lower(): c for c in catalogs}

    if catalog.lower() not in by_lower:
        if _project_chose(context, "catalog", "database"):
            raise DeploymentError(
                f"Databricks catalog '{catalog}' was not found in this workspace (or this "
                f"identity cannot see it). Available catalogs: {', '.join(sorted(catalogs))}. "
                "Set 'Target Unity Catalog' in the project settings to one of these."
            )
        default_catalog = (_first_column(publisher.execute_statements(["SELECT current_catalog()"])) or [""])[0]
        if default_catalog.lower() not in by_lower:
            raise DeploymentError(
                f"No target catalog is set for this project and the default '{catalog}' does not "
                f"exist. Available catalogs: {', '.join(sorted(catalogs))}. "
                "Set 'Target Unity Catalog' in the project settings."
            )
        logger.warning(
            "Databricks catalog '%s' does not exist; using the workspace default catalog '%s'. "
            "Set 'Target Unity Catalog' on the project to make this explicit.",
            catalog,
            default_catalog,
        )
        catalog = by_lower[default_catalog.lower()]
    else:
        catalog = by_lower[catalog.lower()]

    schemas = _first_column(publisher.execute_statements([f"SHOW SCHEMAS IN `{catalog.replace('`', '')}`"]))
    schemas_lower = {s.lower(): s for s in schemas}
    if schemas and schema.lower() not in schemas_lower:
        if _project_chose(context, "schema", "schema_name") or "default" not in schemas_lower:
            raise DeploymentError(
                f"Databricks schema '{catalog}.{schema}' does not exist. SemaBridge never creates "
                f"schemas. Create it in Databricks, or pick one of: {', '.join(sorted(schemas))}."
            )
        logger.warning(
            "Databricks schema '%s.%s' does not exist; using '%s.default'.", catalog, schema, catalog
        )
        schema = schemas_lower["default"]
    elif schemas:
        schema = schemas_lower[schema.lower()]

    updates: Dict[str, Any] = {}
    if catalog != getattr(cfg, "catalog", None):
        updates["catalog"] = catalog
    if schema != getattr(cfg, "schema_name", None):
        updates["schema_name"] = schema
    return cfg.model_copy(update=updates) if updates else cfg


def _deploy_to_databricks(self, context: RunContext) -> None:
    """Deploy SML metadata projection and measure views to Databricks.

    Resolves credentials from linked Account, CredentialManager, or env vars.
    """
    from semabridge.connectors.databricks_publisher import DatabricksPublisher
    from semabridge.connectors.staged_deploy import persist_deploy_events

    databricks_cfg = resolve_databricks_config_for_context(context, allow_offline_stub=False)
    if not str(getattr(databricks_cfg, "warehouse_id", "") or "").strip():
        discovered_wh = _auto_discover_warehouse_id(databricks_cfg)
        if discovered_wh:
            databricks_cfg = databricks_cfg.model_copy(update={"warehouse_id": discovered_wh})
        else:
            raise DeploymentError(
                "No Databricks SQL Warehouse ID is configured and none could be auto-discovered. "
                "Create a SQL Warehouse in Databricks or set SQL Warehouse ID in Settings > Connections."
            )
    publisher = DatabricksPublisher(databricks_cfg, behavior=context.behavior)
    resolved_cfg = ensure_databricks_target_namespace(context, databricks_cfg, publisher=publisher)
    if resolved_cfg is not databricks_cfg:
        databricks_cfg = resolved_cfg
        publisher = DatabricksPublisher(databricks_cfg, behavior=context.behavior)
    context.target_artifact_path = publisher.publish(context.sml_model)
    persist_deploy_events(
        list(getattr(publisher, "deploy_events", None) or []),
        run_id=getattr(context, "run_id", None),
        project_id=getattr(context, "project_id", None),
        model=getattr(context, "project_name", None) or getattr(context.sml_model, "unique_name", None),
    )
    publish_summary = publisher.get_last_publish_summary()
    context.routing_summary = publish_summary.get("routing_summary") if isinstance(publish_summary, dict) else None
    self._raise_if_databricks_fallback_failed(context, publish_summary)

def _raise_if_databricks_fallback_failed(
    self,
    context: RunContext,
    publish_summary: Any,
) -> None:
    """Fail deployment when Databricks SQL fallback fails or zero views deploy."""
    if not isinstance(publish_summary, dict):
        return

    fallback_state = str(publish_summary.get("sql_fallback_state") or "").strip().upper()
    if fallback_state == "FALLBACK_FAILED":
        fallback_reason = str(publish_summary.get("sql_fallback_reason") or "").strip()
        if fallback_reason:
            raise DeploymentError(
                f"Databricks SQL fallback failed for model '{context.project_id}': {fallback_reason}"
            )
        raise DeploymentError(f"Databricks SQL fallback failed for model '{context.project_id}'")

    deploy_summary = publish_summary.get("deploy_summary")
    if not isinstance(deploy_summary, dict):
        return

    planned_views = int(deploy_summary.get("planned_views") or 0)
    views_deployed = int(deploy_summary.get("views_deployed") or 0)
    views_failed = int(deploy_summary.get("views_failed") or 0)
    views_skipped = int(deploy_summary.get("views_skipped") or 0)

    views_stubbed = int(deploy_summary.get("views_stubbed") or 0)
    _report_databricks_structure_only(context, deploy_summary)

    if planned_views > 0 and views_deployed == 0 and views_stubbed == 0 and fallback_state != "FALLBACK_SUCCESS":
        reasons: list[str] = []
        for r in (deploy_summary.get("failed_reasons") or [])[:3]:
            if r:
                reasons.append(str(r))
        for r in (deploy_summary.get("skipped_reasons") or [])[:3]:
            if r:
                reasons.append(str(r))
        detail = "; ".join(reasons)
        missing = [str(t) for t in (deploy_summary.get("missing_source_tables") or []) if t]
        hint = ""
        if missing:
            hint = (
                " Ensure the underlying physical source tables exist in Databricks Unity Catalog "
                "(or configure source_table_mapping) before deploying metric views."
            )
        raise DeploymentError(
            f"No Databricks views were deployed for '{context.project_id}' "
            f"(0/{planned_views} deployed, {views_skipped} skipped, {views_failed} failed)"
            + (f": {detail}" if detail else ".")
            + hint
        )


def _report_databricks_structure_only(context: RunContext, deploy_summary: dict) -> None:
    """Make structure-only (stubbed) views and unbound source tables visible.

    A metric view deployed over an inline NULL stub because its source tables
    are missing exists in Unity Catalog but returns no data. It is reported as
    a run warning and in the Step 9 message, never as a plain success.
    """
    stubbed = [v for v in (deploy_summary.get("stubbed_views") or []) if isinstance(v, dict)]
    binding_issues = [i for i in (deploy_summary.get("source_binding_issues") or []) if isinstance(i, dict)]
    warnings = getattr(context, "target_warnings", None)
    if warnings is None:
        warnings = []
        try:
            context.target_warnings = warnings
        except AttributeError:
            pass

    def _warn(message: str) -> None:
        if message and message not in warnings:
            warnings.append(message)

    if stubbed:
        missing: list[str] = []
        for v in stubbed:
            for t in v.get("missing_source_tables") or []:
                if t and str(t) not in missing:
                    missing.append(str(t))
        views = ", ".join(str(v.get("view") or "") for v in stubbed)
        _warn(
            f"Structure only (no data): {len(stubbed)} metric view(s) were published without data because "
            f"their source tables do not exist in Databricks: {views}"
            + (f" (missing: {', '.join(missing)})" if missing else "")
            + ". Create the tables or set source_table_mapping, then sync again."
        )
    for issue in binding_issues:
        _warn(str(issue.get("message") or ""))
    omitted = [o for o in (deploy_summary.get("omitted_measures") or []) if isinstance(o, dict)]
    if omitted:
        details = "; ".join(f"{o.get('name')}: {o.get('reason')}" for o in omitted[:5])
        _warn(
            f"Databricks rejected {len(omitted)} measure(s); they were left out of the metric view: {details}"
            + (" …" if len(omitted) > 5 else "")
        )

    views_deployed = int(deploy_summary.get("views_deployed") or 0)
    views_failed = int(deploy_summary.get("views_failed") or 0)
    if views_failed and (views_deployed or stubbed):
        failed_reasons = [str(r) for r in (deploy_summary.get("failed_reasons") or []) if r][:3]
        _warn(
            f"Partially deployed: {views_failed} metric view(s) failed while "
            f"{views_deployed + len(stubbed)} deployed"
            + (f" ({'; '.join(failed_reasons)})" if failed_reasons else "")
            + ". The failed views were not changed in Databricks; fix them and sync again."
        )
        context.deploy_step_message = (
            f"Deployment complete with failures: {views_deployed} view(s) with data, "
            f"{len(stubbed)} structure only, {views_failed} failed"
        )
        return
    if stubbed:
        context.deploy_step_message = (
            f"Deployment complete with warnings: {views_deployed} view(s) with data, "
            f"{len(stubbed)} structure only (no data, source tables missing)"
        )
