"""Ontology review for the new-project wizard (runs BEFORE the dry run).

Reads the selected source models into OSI (no target calls, no SML/DDL), runs
the quality analysis against the federated ontology, and turns the reviewer's
decisions into a saved domain.

Source support (source-agnostic before dry run):
* ``pbix``: each selected .pbix is read locally via LocalPBIXConnector + TMSLToOSIConverter;
* ``fabric``: each selected Fabric semantic model is extracted live via FabricExtractor + TMSLToOSIConverter;
* ``tableau``: .tds / .tdsx / .twb / .twbx files are parsed via TableauConnector;
* ``snowflake``: Snowflake Semantic Views (via SemanticViewToOSIConverter) and tables;
* ``databricks``: Unity Catalog Metric Views (YAML measures) and tables;
* any source for an EXISTING project: the project's latest per-model OSI snapshots.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 60 * 60
_CACHE_MAX = 20
_cache: "OrderedDict[str, Tuple[float, List[Any]]]" = OrderedDict()
_cache_lock = threading.Lock()


class UnsupportedSourceError(ValueError):
    """The source type can't be read before the dry run (yet)."""


def _cache_put(models: List[Any]) -> str:
    key = uuid.uuid4().hex
    with _cache_lock:
        _cache[key] = (time.time(), models)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return key


def cache_get(key: Optional[str]) -> Optional[List[Any]]:
    if not key:
        return None
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        ts, models = hit
        if time.time() - ts > _CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        return models


def _read_pbix(path_str: str) -> Any:
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.utils.identifiers import clean_pbix_model_name

    path = Path(str(path_str).strip().strip('"').strip("'")).expanduser()
    if path.suffix.lower() != ".pbix":
        raise ValueError("not a .pbix file")
    if not path.is_file():
        raise ValueError("file not found on the server; upload it again")

    connector = LocalPBIXConnector({"pbix_path": str(path)})
    discovered = connector.discover()
    tmsl = discovered.get("raw_tmsl") or connector.extract()
    if not tmsl:
        raise ValueError("could not read the data model")
    name = clean_pbix_model_name(str(path)) or path.stem
    return TMSLToOSIConverter().to_osi(
        {
            "tmsl": tmsl,
            "workspace_id": "local",
            "dataset_id": name,
            "display_name": name,
            "field_aliases": discovered.get("field_aliases", []),
            "source_file": str(path),
        }
    )


def _resolve_fabric_token_and_workspace(source_config: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Resolve (interactive_token_or_None, workspace_id) for Fabric pre-dry-run extraction."""
    from semabridge.core.settings import get_settings

    requested_ws = str((source_config or {}).get("workspace_id") or "").strip()
    identity_id = str((source_config or {}).get("identity_id") or "").strip()
    stored_ws = ""
    configured_ws = ""
    interactive_token: Optional[str] = None

    try:
        configured_ws = str(getattr(get_settings().fabric, "workspace_id", "") or "").strip()
    except Exception as cfg_exc:
        logger.debug("Fabric settings workspace lookup skipped: %s", cfg_exc)

    try:
        from semabridge.repository.credential_manager import CredentialManager

        cm = CredentialManager()
        stored_fabric = cm.get_credentials("fabric", mask_secrets=False) or {}
        stored_ws = str(stored_fabric.get("workspace_id") or "").strip()

        if identity_id:
            from semabridge.auth.token_resolver import resolve_fabric_access_token

            interactive_token = resolve_fabric_access_token(None, identity_id)
        else:
            token_data = cm.get_msal_token()
            if cm.get_fabric_auth_method() == "interactive" and cm.has_valid_token() and token_data:
                interactive_token = token_data.get("access_token")
    except Exception as auth_exc:
        if identity_id:
            logger.warning("Fabric token lookup failed for identity '%s': %s", identity_id, auth_exc)
            raise UnsupportedSourceError(
                f"Could not authenticate Fabric account '{identity_id}': {auth_exc}"
            ) from auth_exc
        logger.debug("Fabric interactive credential lookup skipped: %s", auth_exc)

    guid_re = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    if requested_ws and not guid_re.match(requested_ws):
        if guid_re.match(stored_ws):
            requested_ws = stored_ws
        elif guid_re.match(configured_ws):
            requested_ws = configured_ws

    ws_id = requested_ws or configured_ws or stored_ws
    return interactive_token, ws_id


def _read_fabric_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Read selected live Fabric semantic models into OSIModels before dry run."""
    from semabridge.connectors.fabric_extractor import FabricExtractor
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.settings import FabricConfig, get_settings

    refs = [str(s).strip() for s in (selected_sources or []) if str(s or "").strip()]
    if not refs:
        refs = [str(m).strip() for m in ((source_config or {}).get("models") or []) if str(m or "").strip()]
    refs = list(OrderedDict((r.casefold(), r) for r in refs).values())

    behavior = ConnectorBehavior()
    if behavior.features.offline_mode:
        offline_path = Path(behavior.features.offline_fabric_model_path)
        if not offline_path.is_file():
            raise UnsupportedSourceError(f"Offline Fabric model file not found: {offline_path}")
        raw_tmsl = json.loads(offline_path.read_text(encoding="utf-8"))
        if isinstance(raw_tmsl, dict) and "model" not in raw_tmsl and "tables" in raw_tmsl:
            raw_tmsl = {"model": raw_tmsl}
        label = refs[0] if refs else offline_path.stem
        model = TMSLToOSIConverter().to_osi(
            {
                "tmsl": raw_tmsl,
                "workspace_id": str((source_config or {}).get("workspace_id") or "offline"),
                "dataset_id": label,
                "display_name": label,
            }
        )
        return [model], []

    if not refs:
        raise UnsupportedSourceError("Select at least one Fabric semantic model to review its measures.")

    interactive_token, ws_id = _resolve_fabric_token_and_workspace(source_config)
    if not ws_id:
        raise UnsupportedSourceError("Select a Fabric workspace to review its semantic models.")

    try:
        base_fabric = get_settings().fabric
        fabric_cfg = base_fabric.model_copy(update={"workspace_id": ws_id})
    except Exception as cfg_exc:
        logger.debug("Using minimal FabricConfig for workspace %s: %s", ws_id, cfg_exc)
        fabric_cfg = FabricConfig(workspace_id=ws_id)

    extractor = FabricExtractor(fabric_cfg)
    if interactive_token:
        extractor._access_token = interactive_token
        extractor._token_expires_at = time.time() + 1800

    models: List[Any] = []
    errors: List[Dict[str, str]] = []
    for ref in refs:
        try:
            resolved_id = extractor.resolve_model_id(ref)
            tmsl = extractor.get_model_definition(resolved_id)
            if not tmsl:
                raise ValueError(f"Fabric model '{ref}' returned an empty definition.")
            try:
                display_name = extractor.get_model_display_name(resolved_id) or ref
            except Exception as name_exc:
                logger.debug("Falling back to model ref '%s' for display_name: %s", ref, name_exc)
                display_name = ref
            models.append(
                TMSLToOSIConverter().to_osi(
                    {
                        "tmsl": tmsl,
                        "workspace_id": ws_id,
                        "dataset_id": resolved_id,
                        "display_name": display_name,
                    }
                )
            )
        except Exception as exc:
            logger.warning("Ontology review: could not read Fabric model '%s': %s", ref, exc)
            errors.append({"source": ref, "error": str(exc)})
    return models, errors


_TABLEAU_SUFFIXES = (".tds", ".tdsx", ".twb", ".twbx")


def _read_tableau_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Read Tableau .tds / .tdsx / .twb / .twbx files into OSIModels before dry run."""
    from semabridge.connectors.tableau_connector import TableauConnector

    raw_paths: List[str] = []
    datasource_names: List[str] = []

    for item in selected_sources or []:
        val = str(item or "").strip()
        if not val:
            continue
        if val.lower().endswith(_TABLEAU_SUFFIXES) or Path(val).expanduser().is_file():
            raw_paths.append(val)
        else:
            datasource_names.append(val)

    for key in ("path", "file_path", "pbix_path"):
        cfg_val = str((source_config or {}).get(key) or "").strip()
        if cfg_val:
            raw_paths.append(cfg_val)

    for m in (source_config or {}).get("models") or []:
        m_str = str(m or "").strip()
        if not m_str:
            continue
        if m_str.lower().endswith(_TABLEAU_SUFFIXES) or Path(m_str).expanduser().is_file():
            raw_paths.append(m_str)
        elif m_str not in datasource_names:
            datasource_names.append(m_str)

    paths = list(OrderedDict((str(Path(p).expanduser()).casefold(), str(Path(p).expanduser())) for p in raw_paths).values())
    if not paths:
        raise UnsupportedSourceError("Upload or select a Tableau (.tds, .tdsx, .twb, .twbx) file to review its measures.")

    models: List[Any] = []
    errors: List[Dict[str, str]] = []
    for p in paths:
        try:
            connector = TableauConnector(p)
            if datasource_names:
                for ds_name in datasource_names:
                    models.append(connector.extract_osi_model(model_name=ds_name))
            else:
                xml_text = connector._read_xml_content()
                root = ET.fromstring(xml_text)
                if root.tag == "workbook":
                    ds_elems = [
                        d for d in root.findall(".//datasources/datasource")
                        if (d.get("name") or "").lower() != "parameters"
                        and (d.get("formatted-name") or "").lower() != "parameters"
                    ]
                    if len(ds_elems) > 1:
                        for d in ds_elems:
                            dname = d.get("name") or d.get("formatted-name")
                            models.append(connector.extract_osi_model(model_name=dname))
                    else:
                        models.append(connector.extract_osi_model())
                else:
                    models.append(connector.extract_osi_model())
        except Exception as exc:
            logger.warning("Ontology review: could not read Tableau file '%s': %s", p, exc)
            errors.append({"source": Path(p).name, "error": str(exc)})
    return models, errors


def _numeric_measure_candidates(dataset_name: str, columns: List[Dict[str, Any]]) -> List[Any]:
    """Infer SUM(<col>) OSI metrics for numeric non-key columns when reading raw tables."""
    from semabridge.intermediate.models import OSIAggregationType, OSIMetric

    numeric_tokens = ("int", "decimal", "numeric", "number", "float", "double", "real", "money")
    id_suffixes = ("_id", "id", "_key", "key", "_code", "_num", "guid", "uuid")
    metrics: List[Any] = []
    for col in columns:
        cname = str(col.get("name") or col.get("col_name") or "").strip()
        dtype = str(col.get("data_type") or "").strip().lower()
        if not cname or not any(tok in dtype for tok in numeric_tokens):
            continue
        low = cname.lower()
        if low in ("id", "key", "row_num") or any(low.endswith(sfx) for sfx in id_suffixes):
            continue
        expr = f"SUM({dataset_name}[{cname}])"
        metrics.append(
            OSIMetric(
                unique_name=f"Total {cname}",
                label=f"Total {cname}",
                dataset=dataset_name,
                source_column=cname,
                aggregation=OSIAggregationType.SUM,
                expression=expr,
                sql_expression=f'SUM("{dataset_name}"."{cname}")',
                description=str(col.get("comment") or col.get("description") or "").strip() or None,
            )
        )
    return metrics


def _read_snowflake_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Read Snowflake Semantic Views and/or tables into OSIModels before dry run."""
    import snowflake.connector
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
    from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
    from semabridge.core.settings import get_settings
    from semabridge.intermediate.models import OSIColumn, OSIDataType, OSIDataset, OSIModel

    database = str((source_config or {}).get("database") or "").strip()
    schema = str((source_config or {}).get("schema") or (source_config or {}).get("schema_name") or "").strip()
    identity_id = str((source_config or {}).get("identity_id") or "").strip()

    sf_cfg = None
    if identity_id:
        try:
            from semabridge.auth.credential_builder import build_snowflake_config
            from semabridge.repository.account_repository import AccountRepository
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                acct = AccountRepository(session).get_account_by_id(identity_id)
                if acct is not None:
                    sf_cfg = build_snowflake_config(acct, session, get_settings().snowflake)
        except Exception as acct_exc:
            logger.warning("Snowflake account lookup failed for '%s': %s", identity_id, acct_exc)

    if sf_cfg is None:
        try:
            sf_cfg = get_settings().snowflake
        except Exception as cfg_exc:
            raise UnsupportedSourceError(f"Configure a Snowflake account to review its models: {cfg_exc}") from cfg_exc

    updates: Dict[str, Any] = {}
    if database:
        updates["database"] = database
    if schema:
        updates["schema_name"] = schema
    if updates and hasattr(sf_cfg, "model_copy"):
        sf_cfg = sf_cfg.model_copy(update=updates)

    db_name = database or str(getattr(sf_cfg, "database", "") or "").strip()
    sch_name = schema or str(getattr(sf_cfg, "schema_name", "") or "").strip()
    if not db_name or not sch_name:
        raise UnsupportedSourceError("Select a Snowflake database and schema to review its measures.")

    kw = get_snowflake_connect_kwargs(sf_cfg)
    if not kw.get("account") or not kw.get("user"):
        raise UnsupportedSourceError("Select a connected Snowflake account to review its measures.")

    refs = [str(s).strip() for s in (selected_sources or (source_config or {}).get("models") or []) if str(s or "").strip()]
    wanted = {r.split(".")[-1].strip('"').casefold() for r in refs}

    models: List[Any] = []
    errors: List[Dict[str, str]] = []
    matched_sv: set = set()

    try:
        conn = snowflake.connector.connect(**kw)
    except Exception as conn_exc:
        raise UnsupportedSourceError(f"Could not connect to Snowflake ({db_name}.{sch_name}): {conn_exc}") from conn_exc

    try:
        cur = conn.cursor()
        try:
            cur.execute(f'SHOW SEMANTIC VIEWS IN SCHEMA "{db_name}"."{sch_name}"')
            sv_cols = [d[0].lower() for d in (cur.description or [])]
            sv_rows = [dict(zip(sv_cols, r)) for r in cur.fetchall()]
        except Exception as sv_exc:
            logger.debug("SHOW SEMANTIC VIEWS skipped in %s.%s: %s", db_name, sch_name, sv_exc)
            sv_rows = []

        for vr in sv_rows:
            vname = str(vr.get("name") or "").strip()
            if not vname:
                continue
            if wanted and vname.casefold() not in wanted:
                continue
            try:
                cur.execute(f"SELECT GET_DDL('SEMANTIC_VIEW', '\"{db_name}\".\"{sch_name}\".\"{vname}\"')")
                ddl_row = cur.fetchone()
                ddl = str(ddl_row[0]) if ddl_row and ddl_row[0] else ""
                if ddl:
                    models.append(
                        SemanticViewToOSIConverter().to_osi(
                            {"ddl": ddl, "view_name": vname, "column_metadata": {}}
                        )
                    )
                    matched_sv.add(vname.casefold())
            except Exception as ddl_exc:
                logger.warning("Could not read Snowflake Semantic View '%s': %s", vname, ddl_exc)
                errors.append({"source": vname, "error": str(ddl_exc)})

        remaining_tables = [r.split(".")[-1].strip('"') for r in refs if r.split(".")[-1].strip('"').casefold() not in matched_sv]
        if remaining_tables or not models:
            cur.execute(
                f'SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COMMENT '
                f'FROM "{db_name}".INFORMATION_SCHEMA.COLUMNS '
                f"WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (sch_name,),
            )
            by_table: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
            want_tables = {t.casefold() for t in remaining_tables}
            for tname, cname, dtype, comment in cur.fetchall():
                if want_tables and str(tname).casefold() not in want_tables:
                    continue
                by_table.setdefault(str(tname), []).append(
                    {"name": str(cname), "data_type": str(dtype or ""), "comment": str(comment or "")}
                )
            if by_table:
                datasets = []
                inferred_metrics = []
                for tname, cols in by_table.items():
                    osi_cols = [
                        OSIColumn(unique_name=c["name"], label=c["name"], data_type=OSIDataType.STRING)
                        for c in cols
                    ]
                    datasets.append(OSIDataset(unique_name=tname, label=tname, source_table=tname, columns=osi_cols))
                    inferred_metrics.extend(_numeric_measure_candidates(tname, cols))
                models.append(
                    OSIModel(
                        unique_name=f"{db_name}_{sch_name}",
                        label=f"{db_name}.{sch_name}",
                        datasets=datasets,
                        metrics=inferred_metrics,
                    )
                )
    finally:
        conn.close()

    return models, errors


def _resolve_databricks_source_creds(source_config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve Databricks {host, warehouse_id, token} for pre-dry-run source inspection."""
    import requests as _requests
    from sqlalchemy import select as _sa_select
    from semabridge.repository.credential_manager import CredentialManager
    from semabridge.repository.orm.account_models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.utils.tls import requests_verify

    creds: Dict[str, Any] = {}
    identity_id = str((source_config or {}).get("identity_id") or "").strip()

    try:
        with db_manager.get_session() as sess:
            acct = None
            if identity_id:
                acct = sess.execute(_sa_select(Account).where(Account.id == identity_id)).scalars().first()
            if acct is None:
                acct = sess.execute(_sa_select(Account).where(Account.connector_type == "DATABRICKS")).scalars().first()
            if acct is not None and getattr(acct, "credentials_json", None):
                raw = acct.credentials_json
                parsed = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
                if isinstance(parsed, dict):
                    creds.update(parsed)
    except Exception as acct_exc:
        logger.debug("Databricks Account lookup skipped: %s", acct_exc)

    if not creds.get("host") or not (creds.get("token") or creds.get("access_token") or creds.get("client_secret")):
        try:
            cm_creds = CredentialManager().get_credentials("databricks", mask_secrets=False) or {}
            for k, v in cm_creds.items():
                if v and not creds.get(k):
                    creds[k] = v
        except Exception as cm_exc:
            logger.debug("CredentialManager Databricks lookup skipped: %s", cm_exc)

    if not creds.get("host"):
        try:
            from semabridge.core.settings import get_settings

            dbx = get_settings().databricks
            if getattr(dbx, "host", None):
                creds.setdefault("host", dbx.host)
            if getattr(dbx, "warehouse_id", None):
                creds.setdefault("warehouse_id", dbx.warehouse_id)
            tok = getattr(dbx, "token", None)
            if tok is not None:
                tok_val = tok.get_secret_value() if hasattr(tok, "get_secret_value") else str(tok)
                if tok_val:
                    creds.setdefault("token", tok_val)
        except Exception as set_exc:
            logger.debug("Settings Databricks lookup skipped: %s", set_exc)

    raw_host = str(creds.get("host") or creds.get("server_hostname") or "").strip()
    host = re.sub(r"^https?://", "", raw_host).rstrip("/")
    wh_id = str((source_config or {}).get("warehouse_id") or creds.get("warehouse_id") or "").strip()
    if not wh_id:
        hp = str(creds.get("http_path") or "").strip()
        m_wh = re.search(r"/warehouses/([A-Za-z0-9_-]+)", hp)
        if m_wh:
            wh_id = m_wh.group(1)

    token = str(creds.get("token") or creds.get("access_token") or "").strip()
    if not token and creds.get("client_id") and creds.get("client_secret") and host:
        token_resp = _requests.post(
            f"https://{host}/oidc/v1/token",
            data={"grant_type": "client_credentials", "scope": "all-apis"},
            auth=(str(creds["client_id"]), str(creds["client_secret"])),
            timeout=10,
            verify=requests_verify(),
        )
        if token_resp.status_code == 200:
            token = str((token_resp.json() or {}).get("access_token") or "").strip()

    if not host or not wh_id or not token:
        raise UnsupportedSourceError(
            "Connect a Databricks account with host, SQL warehouse_id, and token to review its models before dry run."
        )
    return {"host": host, "warehouse_id": wh_id, "token": token, "creds": creds}


def _exec_databricks_rows(creds: Dict[str, Any], statement: str) -> List[Dict[str, Any]]:
    """Execute one read-only metadata SQL statement on Databricks SQL Statements API."""
    import requests as _requests
    from semabridge.utils.tls import requests_verify

    resp = _requests.post(
        f"https://{creds['host']}/api/2.0/sql/statements",
        json={"statement": statement, "warehouse_id": creds["warehouse_id"], "wait_timeout": "20s"},
        headers={"Authorization": f"Bearer {creds['token']}", "Content-Type": "application/json"},
        timeout=25,
        verify=requests_verify(),
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Databricks HTTP {resp.status_code}: {resp.text[:200]}")
    body = resp.json() or {}
    state = ((body.get("status") or {}).get("state") or "").upper()
    if state != "SUCCEEDED":
        err_msg = ((body.get("status") or {}).get("error") or {}).get("message") or state or "SQL failed"
        raise RuntimeError(f"Databricks SQL failed: {err_msg}")
    cols = [c.get("name", f"c{i}") for i, c in enumerate(((body.get("manifest") or {}).get("schema") or {}).get("columns") or [])]
    data = ((body.get("result") or {}).get("data_array")) or []
    return [dict(zip(cols, row)) for row in data]


def _q_uc(ident: str) -> str:
    return "`" + str(ident).replace("`", "``") + "`"


def _read_databricks_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Read Databricks Unity Catalog Metric Views and/or tables into OSIModels before dry run."""
    import yaml
    from semabridge.intermediate.models import OSIColumn, OSIDataType, OSIDataset, OSIMetric, OSIModel

    creds = _resolve_databricks_source_creds(source_config)
    raw_creds = creds.get("creds") if isinstance(creds.get("creds"), dict) else {}
    default_cat = str(
        (source_config or {}).get("catalog")
        or (source_config or {}).get("database")
        or raw_creds.get("catalog")
        or "main"
    ).strip()
    default_sch = str(
        (source_config or {}).get("schema")
        or (source_config or {}).get("schema_name")
        or raw_creds.get("schema")
        or "default"
    ).strip()

    refs = [
        str(s).strip()
        for s in (selected_sources or (source_config or {}).get("tables") or (source_config or {}).get("models") or [])
        if str(s or "").strip()
    ]

    targets: List[Tuple[str, str, str]] = []
    if refs:
        for ref in refs:
            parts = [p.strip("`").strip() for p in ref.split(".") if p.strip("`").strip()]
            if len(parts) >= 3:
                targets.append((parts[-3], parts[-2], parts[-1]))
            elif len(parts) == 2:
                targets.append((default_cat, parts[0], parts[1]))
            elif len(parts) == 1:
                targets.append((default_cat, default_sch, parts[0]))
    else:
        rows = _exec_databricks_rows(creds, f"SHOW TABLES IN {_q_uc(default_cat)}.{_q_uc(default_sch)}")
        for r in rows[:50]:
            tname = str(r.get("tableName") or r.get("table_name") or "").strip()
            if tname and not bool(r.get("isTemporary")):
                targets.append((default_cat, default_sch, tname))

    if not targets:
        raise UnsupportedSourceError("Select at least one Databricks table or Metric View to review its measures.")

    models: List[Any] = []
    errors: List[Dict[str, str]] = []
    regular_datasets: List[Any] = []
    regular_metrics: List[Any] = []

    for cat, sch, obj_name in targets:
        fq = f"{_q_uc(cat)}.{_q_uc(sch)}.{_q_uc(obj_name)}"
        try:
            desc_rows = _exec_databricks_rows(creds, f"DESCRIBE TABLE EXTENDED {fq}")
            is_metric_view = False
            view_yaml_text = ""
            columns: List[Dict[str, str]] = []
            in_detail = False

            for dr in desc_rows:
                k = str(dr.get("col_name") or "").strip()
                v = str(dr.get("data_type") or "")
                cmt = str(dr.get("comment") or "")
                if k.startswith("#"):
                    in_detail = True
                    continue
                if not in_detail and k:
                    columns.append({"name": k, "data_type": v, "comment": cmt})
                elif in_detail:
                    if k.lower() == "type" and "METRIC_VIEW" in v.upper():
                        is_metric_view = True
                    elif k.lower() == "view text" and v.strip():
                        view_yaml_text = v

            if is_metric_view and view_yaml_text:
                parsed = yaml.safe_load(view_yaml_text)
                if isinstance(parsed, dict):
                    src_tbl = str(parsed.get("source") or obj_name).split(".")[-1].strip("`") or obj_name
                    dims = [
                        OSIColumn(
                            unique_name=str(d.get("name")),
                            label=str(d.get("name")),
                            data_type=OSIDataType.STRING,
                            description=str(d.get("comment") or "").strip() or None,
                        )
                        for d in (parsed.get("dimensions") or [])
                        if isinstance(d, dict) and d.get("name")
                    ] or [OSIColumn(unique_name="id", label="id", data_type=OSIDataType.STRING)]
                    mv_metrics = []
                    for m in parsed.get("measures") or []:
                        if not isinstance(m, dict) or not m.get("name"):
                            continue
                        mname = str(m["name"]).strip()
                        mexpr = str(m.get("expr") or "").strip()
                        syns = [str(s) for s in (m.get("synonyms") or []) if str(s or "").strip()]
                        mv_metrics.append(
                            OSIMetric(
                                unique_name=mname,
                                label=mname,
                                dataset=src_tbl,
                                expression=mexpr,
                                sql_expression=mexpr,
                                description=str(m.get("comment") or "").strip() or None,
                                synonyms=syns,
                            )
                        )
                    models.append(
                        OSIModel(
                            unique_name=obj_name,
                            label=obj_name,
                            datasets=[OSIDataset(unique_name=src_tbl, label=src_tbl, source_table=src_tbl, columns=dims)],
                            metrics=mv_metrics,
                        )
                    )
                    continue

            if columns:
                osi_cols = [
                    OSIColumn(
                        unique_name=c["name"],
                        label=c["name"],
                        data_type=OSIDataType.STRING,
                        description=c["comment"] or None,
                    )
                    for c in columns
                ]
                regular_datasets.append(
                    OSIDataset(unique_name=obj_name, label=obj_name, source_table=obj_name, columns=osi_cols)
                )
                regular_metrics.extend(_numeric_measure_candidates(obj_name, columns))
        except Exception as exc:
            logger.warning("Ontology review: could not read Databricks object '%s.%s.%s': %s", cat, sch, obj_name, exc)
            errors.append({"source": f"{cat}.{sch}.{obj_name}", "error": str(exc)})

    if regular_datasets:
        models.append(
            OSIModel(
                unique_name=f"{default_cat}_{default_sch}",
                label=f"{default_cat}.{default_sch}",
                datasets=regular_datasets,
                metrics=regular_metrics,
            )
        )

    return models, errors


async def read_source_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
    project_id: Optional[str] = None,
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Return (OSI models, per-source errors) for ANY supported source connector."""
    import asyncio

    stype = str((source_config or {}).get("type") or "").strip().lower()
    models: List[Any] = []
    errors: List[Dict[str, str]] = []

    if stype == "pbix":
        # Single-file wizards send model names in selected_sources and the file
        # in source_config.pbix_path; multi-file wizards send the file paths.
        paths = [str(p) for p in (selected_sources or []) if str(p).strip().lower().endswith(".pbix")]
        if not paths and source_config.get("pbix_path"):
            paths = [str(source_config["pbix_path"])]
        if not paths:
            raise UnsupportedSourceError("Select at least one .pbix file to review its measures.")
        # The same file selected twice would report every measure as a duplicate.
        paths = list(OrderedDict((str(Path(p).expanduser()).casefold(), p) for p in paths).values())
        for p in paths:
            try:
                models.append(await asyncio.to_thread(_read_pbix, p))
            except Exception as exc:  # one bad file must not hide the others
                logger.warning("Ontology review: could not read %s: %s", p, exc)
                errors.append({"source": Path(str(p)).name, "error": str(exc)})
        return models, errors

    has_explicit_selection = bool(
        selected_sources
        or (source_config or {}).get("models")
        or (source_config or {}).get("tables")
        or (source_config or {}).get("path")
        or (source_config or {}).get("file_path")
    )

    if stype == "fabric" and (has_explicit_selection or not project_id or project_id == "preview"):
        from semabridge.core.behavior import ConnectorBehavior

        if ConnectorBehavior().features.offline_mode:
            return await asyncio.to_thread(_read_fabric_models, source_config or {}, list(selected_sources or []))
        # Live Fabric: read each selected model with the project's own credentials
        # (no server-wide default workspace or identity).
        from semabridge.sources.base import SourceError
        from semabridge.sources.fabric.model_reader import read_fabric_models

        try:
            return await asyncio.to_thread(read_fabric_models, source_config or {}, list(selected_sources or []))
        except SourceError as exc:
            raise UnsupportedSourceError(str(exc)) from exc

    if stype == "tableau" and (has_explicit_selection or not project_id or project_id == "preview"):
        return await asyncio.to_thread(_read_tableau_models, source_config or {}, list(selected_sources or []))

    if stype == "snowflake" and (has_explicit_selection or not project_id or project_id == "preview"):
        return await asyncio.to_thread(_read_snowflake_models, source_config or {}, list(selected_sources or []))

    if stype == "databricks" and (has_explicit_selection or not project_id or project_id == "preview"):
        return await asyncio.to_thread(_read_databricks_models, source_config or {}, list(selected_sources or []))

    if project_id and project_id not in ("preview", ""):
        # Multi-model project: review EVERY model's latest snapshot rather than
        # only whichever model synced last.
        try:
            from semabridge.ontology.project_models import osi_from_blob, project_models

            per_model = [m for m in await asyncio.to_thread(project_models, project_id) if m.blob]
            if len(per_model) >= 2:
                for m in per_model:
                    try:
                        models.append(osi_from_blob(m.blob))
                    except Exception as exc:
                        logger.warning("Ontology review: snapshot of model %s unreadable: %s", m.model_name, exc)
                        errors.append({"source": m.model_name, "error": str(exc)})
                return models, errors
        except Exception as pm_exc:
            logger.debug("Multi-model snapshot lookup skipped for %s: %s", project_id, pm_exc)

        from semabridge.api.routers.export_router import _load_osi_model

        osi = await _load_osi_model(project_id)
        if osi is not None:
            return [osi], errors

    raise UnsupportedSourceError(
        f"Select source models or configure '{stype or 'source'}' credentials to review business definitions before the dry run."
    )


def combine_models(models: List[Any], name: str) -> Any:
    """One OSIModel holding every model's datasets/metrics/relationships (first wins by name).

    Legacy helper: the review now builds one domain per model
    (:func:`build_reviewed_domains`), so same-named items of different models
    are never collapsed.
    """
    if not models:
        raise ValueError("No source models to combine")
    if len(models) == 1:
        return models[0]

    def merged(attr: str) -> List[Any]:
        out: "OrderedDict[str, Any]" = OrderedDict()
        for model in models:
            for item in getattr(model, attr, None) or []:
                key = str(getattr(item, "unique_name", "") or id(item)).casefold()
                out.setdefault(key, item)
        return list(out.values())

    update: Dict[str, Any] = {
        "unique_name": name,
        "label": name,
        "datasets": merged("datasets"),
        "metrics": merged("metrics"),
        "relationships": merged("relationships"),
    }
    if hasattr(models[0], "dimensions"):
        update["dimensions"] = merged("dimensions")
    return models[0].model_copy(update=update)


def analyze(models: List[Any]) -> Dict[str, Any]:
    from semabridge.ontology.quality import analyze_source_metrics, source_metrics_from_osi
    from semabridge.ontology.store import ontology_store

    ontology_store.reload_from_disk()

    metrics = []
    for model in models:
        metrics.extend(source_metrics_from_osi(model))
    return analyze_source_metrics(metrics, ontology_store.get_federated_ontology())


async def run_analysis(
    source_config: Dict[str, Any],
    selected_sources: List[str],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    models, errors = await read_source_models(source_config, selected_sources, project_id)
    if not models:
        return {"analysis_id": None, "errors": errors, "summary": None}
    result = analyze(models)
    result["analysis_id"] = _cache_put(models)
    result["errors"] = errors
    return result


def build_reviewed_domain(
    models: List[Any],
    *,
    domain_name: str,
    owner: Optional[str],
    merges: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    descriptions: Dict[str, str],
    existing: Optional[Any] = None,
) -> Tuple[Any, Dict[str, List[str]]]:
    """Build the reviewed domain; with ``existing`` it MERGES into that domain
    (same semantics as a re-sync: user edits kept, BI items refreshed)."""
    from semabridge.ontology.federated_loader import FederatedOntologyLoader
    from semabridge.ontology.quality import apply_review_decisions

    combined = combine_models(models, domain_name)
    draft = FederatedOntologyLoader.from_osi_model(
        model=combined, domain_name=domain_name, existing_ontology=existing
    )
    if owner:
        draft = draft.model_copy(update={"owner": owner})
    return apply_review_decisions(draft, merges=merges, conflicts=conflicts, descriptions=descriptions)


def review_model_id(model: Any) -> str:
    """Model id (``model_key``) of a reviewed source model."""
    from semabridge.ontology.models import model_key

    for raw in (getattr(model, "source_file", None), getattr(model, "unique_name", None), getattr(model, "label", None)):
        key = model_key(raw)
        if key:
            return key
    return ""


def build_reviewed_domains(
    models: List[Any],
    *,
    domain_name: str,
    owner: Optional[str],
    merges: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    descriptions: Dict[str, str],
    project_id: Optional[str] = None,
    existing_by_model: Optional[Dict[str, Any]] = None,
) -> List[Tuple[Any, Dict[str, List[str]], str, str]]:
    """One reviewed domain PER MODEL: ``[(domain, report, model_id, model_name)]``.

    Nothing is merged across models: same-named measures of two models stay in
    their own model's domain. Review decisions apply inside each domain (a
    cross-model "merge" gives every member the same business name in its own
    model's domain). Domain ids are ``{project_slug}__{model_slug}``; display
    names ``"<domain_name> · <model>"``.
    """
    from semabridge.ontology.federated_loader import FederatedOntologyLoader
    from semabridge.ontology.models import per_model_domain_id
    from semabridge.ontology.quality import apply_review_decisions

    out: List[Tuple[Any, Dict[str, List[str]], str, str]] = []
    seen: set = set()
    base = (project_id if project_id and project_id != "preview" else domain_name) or "review"
    for model in models:
        mid = review_model_id(model)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        mname = str(getattr(model, "label", None) or getattr(model, "unique_name", None) or mid)
        existing = (existing_by_model or {}).get(mid)
        name = existing.domain if existing is not None else f"{domain_name} · {mname}"
        draft = FederatedOntologyLoader.from_osi_model(model=model, domain_name=name, existing_ontology=existing)
        update: Dict[str, Any] = {
            "id": existing.id if existing is not None else per_model_domain_id(base, mid),
            "source_model_id": mid,
            "source_model_name": mname,
        }
        if owner and not draft.owner:
            update["owner"] = owner
        draft = draft.model_copy(update=update)
        draft, report = apply_review_decisions(draft, merges=merges, conflicts=conflicts, descriptions=descriptions)
        out.append((draft, report, mid, mname))
    return out
