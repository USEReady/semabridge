"""Databricks Platform Adapter for Drift Monitoring and Blast Radius Governance.

Confirmed Databricks Operations (Real-Account Verification):
- Primary confirmed metadata call returning metric view YAML or view definition:
    `DESCRIBE TABLE EXTENDED <catalog>.<schema>.<view_name>`
  On real Databricks Unity Catalog accounts, this command returns column schemas and
  extended table properties including `view_expanded_text` or `view_original_text`, which
  contain the embedded YAML metric definitions for Unity Catalog Metric Views.
- Secondary / version-dependent DDL call:
    `SHOW CREATE TABLE <catalog>.<schema>.<view_name>`
  Returns the complete `CREATE OR REPLACE VIEW ... WITH METRICS LANGUAGE YAML AS $$ ... $$` DDL.
- List Managed Views:
    `SELECT table_name FROM <catalog>.information_schema.views WHERE table_schema = ...`

Target-Aware Blast Radius Consumer Discovery (Spike Findings):
- Table Lineage (Consumer Asset Discovery):
    Call: `SELECT source_table_full_name, target_table_full_name, target_type FROM system.information_schema.table_lineage WHERE source_table_full_name = :view_name`
    Does it report which view/table it uses? YES.
    Output shape:
      Rows of (source_table_full_name, target_table_full_name, target_type, event_time).
- Databricks Genie Spaces (Consumer Asset):
    Call: `GET /api/2.0/genie/spaces` and `GET /api/2.0/genie/spaces/{space_id}`
    Does it report which view/table it uses? YES.
    Output shape:
      JSON dictionary `{ "space_id": str, "title": str, "tables": [ {"table_name": "catalog.schema.view"} ] }`.
- Databricks Lakeview Dashboards (Consumer Asset):
    Call: `GET /api/2.0/lakeview/dashboards` and `GET /api/2.0/lakeview/dashboards/{dashboard_id}`
    Does it report which view/table it uses?
      GAP: List metadata returns only `{ "dashboards": [ {"dashboard_id": str, "display_name": str} ] }`.
      Individual dataset queries are nested inside serialized JSON widget definitions and SQL strings;
      direct metadata listing does not expose structured view sources. Lineage must be resolved via
      `system.information_schema.table_lineage`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import yaml

from semabridge.governance.drift.adapters.base import PlatformAdapter
from semabridge.governance.drift.models import (
    ObjectDef,
    ObjectKind,
    ViewDefinition,
)

logger = logging.getLogger("semabridge.governance.drift.adapters.databricks")

_IDENT_RE = re.compile(r'^(?:[A-Za-z0-9_]+|`[^`]+`)$')
_AGG_PREFIX_RE = re.compile(
    r'^\s*(?:sum|avg|count|min|max|approx_count_distinct|stddev|variance)\s*\(',
    re.IGNORECASE,
)


def _clean_identifier(raw: str) -> str:
    """Strip quotes, leading visibility keywords, and trailing clauses from an identifier."""
    cleaned = str(raw or "").strip()
    while cleaned.startswith(('"', "'", '`')) and cleaned.endswith(('"', "'", '`')) and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    cleaned = re.sub(r'^(?:PRIVATE|PUBLIC)\s+', '', cleaned, flags=re.I).strip()
    cleaned = re.sub(r'\s+WITH\s+SYNONYMS.*$', '', cleaned, flags=re.I).strip()
    cleaned = re.sub(r'\s+COMMENT\s*=.*$', '', cleaned, flags=re.I).strip()
    cleaned = re.sub(r'\s+NON\s+ADDITIVE\s+BY.*$', '', cleaned, flags=re.I).strip()
    while cleaned.startswith(('"', "'", '`')) and cleaned.endswith(('"', "'", '`')) and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _safe_quote_view_name(view_name: str) -> Optional[str]:
    """Quote each period-separated segment of a Databricks view name with backticks safely."""
    parts = [p.strip() for p in str(view_name).split(".") if p.strip()]
    if not 1 <= len(parts) <= 3:
        return None
    quoted_parts = []
    for p in parts:
        clean = p.strip("`").strip('"')
        if not re.match(r'^[A-Za-z0-9_]+$', clean):
            return None
        quoted_parts.append(f"`{clean}`")
    return ".".join(quoted_parts)


DATABRICKS_CAPABILITIES_YAML = {
    ObjectKind.METRIC.value: {"expression", "description"},
    ObjectKind.DIMENSION.value: {"expression", "description"},
    ObjectKind.FACT.value: {"expression", "description"},
    ObjectKind.RELATIONSHIP.value: {"expression"},
}

DATABRICKS_CAPABILITIES_DESCRIBE_JSON = {
    ObjectKind.METRIC.value: {"description"},
    ObjectKind.DIMENSION.value: {"description"},
}

DATABRICKS_CAPABILITIES_SQL_DDL = {
    ObjectKind.METRIC.value: {"expression", "description"},
    ObjectKind.DIMENSION.value: {"expression", "description"},
    ObjectKind.FACT.value: {"expression"},
}


class DatabricksAdapter(PlatformAdapter):
    """Adapter for Databricks Unity Catalog Metric Views and SQL Views."""

    platform_name: str = "DATABRICKS"

    def _resolve_connection_config(
        self,
        connection_config: Optional[Dict[str, Any]] = None,
        view_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve Databricks connection credentials strictly from stored warehouse config."""
        if hasattr(connection_config, "model_dump"):
            kw = connection_config.model_dump()
        elif hasattr(connection_config, "dict"):
            kw = connection_config.dict()
        elif hasattr(connection_config, "__dict__"):
            kw = dict(connection_config.__dict__)
        else:
            kw = dict(connection_config or {})

        host = str(
            getattr(connection_config, "host", None)
            or kw.get("host")
            or getattr(connection_config, "server_hostname", None)
            or kw.get("server_hostname")
            or ""
        ).strip()

        raw_token = (
            getattr(connection_config, "token", None)
            or kw.get("token")
            or getattr(connection_config, "access_token", None)
            or kw.get("access_token")
            or getattr(connection_config, "client_secret", None)
            or kw.get("client_secret")
            or ""
        )
        if hasattr(raw_token, "get_secret_value"):
            token = raw_token.get_secret_value().strip()
        else:
            token = str(raw_token).strip()

        warehouse_id = str(
            getattr(connection_config, "warehouse_id", None)
            or kw.get("warehouse_id")
            or getattr(connection_config, "http_path", None)
            or kw.get("http_path")
            or ""
        ).strip()

        catalog = str(
            getattr(connection_config, "catalog", None)
            or kw.get("catalog")
            or ""
        ).strip()

        schema = str(
            getattr(connection_config, "schema_name", None)
            or getattr(connection_config, "schema", None)
            or kw.get("schema_name")
            or kw.get("schema")
            or ""
        ).strip()

        if view_name:
            parts = [p.strip().strip("`").strip('"') for p in str(view_name).split(".") if p.strip()]
            if len(parts) == 3:
                if not catalog:
                    catalog = parts[0]
                if not schema:
                    schema = parts[1]
            elif len(parts) == 2 and not schema:
                schema = parts[0]

        if not catalog or not schema:
            raise ValueError("MISSING_WAREHOUSE_CONFIG: Databricks catalog and schema are required")

        if not host or not token:
            raise ValueError("MISSING_WAREHOUSE_CONFIG: Databricks host and token credentials are required")

        return {
            "host": host,
            "token": token,
            "warehouse_id": warehouse_id,
            "catalog": catalog,
            "schema": schema,
        }

    def _execute_sql_statement(
        self,
        statement: str,
        connection_config: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> List[List[Any]]:
        """Execute a SQL statement against Databricks using the SQL Execution REST API.
        
        Falls back to databricks-sql-connector if available.
        """
        import time
        import requests

        cfg = self._resolve_connection_config(connection_config)
        host = cfg.get("host")
        token = cfg.get("token")
        warehouse_id = cfg.get("warehouse_id")

        if not host or not token:
            raise ValueError("Databricks connection credentials missing or incomplete (host/token required)")

        clean_host = host.replace("https://", "").replace("http://", "").rstrip("/")
        endpoint = f"https://{clean_host}/api/2.0/sql/statements"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "statement": statement,
            "wait_timeout": f"{min(timeout, 30)}s",
        }
        if warehouse_id:
            payload["warehouse_id"] = warehouse_id
        if cfg.get("catalog"):
            payload["catalog"] = cfg["catalog"]
        if cfg.get("schema"):
            payload["schema"] = cfg["schema"]

        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout + 5)
        except requests.exceptions.Timeout as t_err:
            logger.error("Databricks request timed out: %s", t_err)
            raise RuntimeError("warehouse unavailable: query timed out") from t_err
        except Exception as net_err:
            logger.error("Databricks network error: %s", net_err)
            raise RuntimeError(f"warehouse unavailable: {net_err}") from net_err

        if resp.status_code in (401, 403):
            logger.error("Databricks authentication failed (%d): %s", resp.status_code, resp.text[:300])
            raise RuntimeError("warehouse unavailable: authentication failed")
        if resp.status_code == 404:
            raise RuntimeError(f"warehouse unavailable: endpoint or resource not found: {resp.text[:300]}")
        if resp.status_code >= 400:
            err_msg = resp.text[:400]
            if "TABLE_OR_VIEW_NOT_FOUND" in err_msg or "cannot be found" in err_msg.lower():
                raise RuntimeError(f"view not found: {err_msg}")
            raise RuntimeError(f"warehouse unavailable: HTTP {resp.status_code}: {err_msg}")

        data = resp.json()
        statement_id = data.get("statement_id")
        status_info = data.get("status") or {}
        state = status_info.get("state")  # hardcoding-ok: Databricks statement execution API status field

        start_t = time.time()
        while state in ("PENDING", "RUNNING") and statement_id and (time.time() - start_t < timeout):
            time.sleep(1)
            try:
                poll_resp = requests.get(f"{endpoint}/{statement_id}", headers=headers, timeout=10)
                if poll_resp.status_code in (401, 403):
                    raise RuntimeError("warehouse unavailable: authentication failed")
                if poll_resp.status_code >= 400:
                    break
                data = poll_resp.json()
                state = (data.get("status") or {}).get("state")  # hardcoding-ok: Databricks statement execution API status field
            except Exception:
                break

        if state not in ("SUCCEEDED",):
            err_desc = (data.get("status") or {}).get("error", {}).get("message") or state or "execution failed"
            if "TABLE_OR_VIEW_NOT_FOUND" in str(err_desc) or "cannot be found" in str(err_desc).lower():
                raise RuntimeError(f"view not found: {err_desc}")
            raise RuntimeError(f"warehouse unavailable: {err_desc}")

        result = data.get("result") or {}
        return result.get("data_array") or []

    def list_views(self, project_id: str, scope: Optional[Dict[str, str]] = None) -> List[str]:
        """List deployed views for the given project in Databricks."""
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        deployments = repo.list_project_deployments(project_id, include_unmanaged=True)
        views: List[str] = []
        for d in deployments:
            conn_type = str(d.get("connector_type") or "").upper()
            if conn_type and conn_type != self.platform_name:
                continue
            v = d.get("semantic_view") or d.get("view") or d.get("table")
            if v and v not in views:
                views.append(v)

        if not views and scope:
            cfg = self._resolve_connection_config(scope)
            cat = cfg.get("catalog")
            sch = cfg.get("schema")
            if cat and sch:
                try:
                    rows = self._execute_sql_statement(f"SHOW VIEWS IN `{cat}`.`{sch}`", cfg)
                    for r in rows:
                        if len(r) >= 2:
                            raw_v = str(r[1]).strip().strip('"`')
                            if raw_v.startswith(f"{cat}."):
                                full_v = raw_v
                            else:
                                full_v = f"{cat}.{sch}.{raw_v}"
                            if full_v not in views:
                                views.append(full_v)
                except Exception as e:
                    logger.debug("Failed listing views via Databricks REST API: %s", e)

        return views

    def fetch_definition_text(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Fetch raw DDL or YAML metric definition text from Databricks."""
        safe_name = _safe_quote_view_name(view_name)
        if not safe_name:
            raise ValueError(f"Invalid Databricks view identifier: {view_name}")

        cfg = self._resolve_connection_config(connection_config, view_name=view_name)

        # Explicit client selection: use databricks-sql-connector if available, otherwise use REST API
        try:
            import databricks.sql as db_sql
        except (ImportError, ModuleNotFoundError, AttributeError):
            db_sql = None

        if db_sql is not None:
            try:
                conn = db_sql.connect(
                    server_hostname=cfg.get("host", "").replace("https://", "").replace("http://", "").rstrip("/"),
                    http_path=f"/sql/1.0/warehouses/{cfg.get('warehouse_id')}" if cfg.get('warehouse_id') else None,
                    access_token=cfg.get("token"),
                )
                try:
                    cur = conn.cursor()
                    try:
                        cur.execute(f"SHOW CREATE TABLE {safe_name}")
                        row = cur.fetchone()
                        if row and row[0]:
                            return str(row[0])
                    except Exception as s_exc:
                        logger.warning("SHOW CREATE TABLE via connector failed: %s", s_exc)
                    cur.execute(f"DESCRIBE TABLE EXTENDED {safe_name}")
                    cols = [c[0].lower() for c in cur.description] if cur.description else ["col_name", "data_type", "comment"]
                    records = [dict(zip(cols, r)) for r in cur.fetchall()]
                    for rec in records:
                        p_name = str(rec.get("col_name") or rec.get("name") or "").strip().lower()
                        p_val = str(rec.get("data_type") or rec.get("type") or "").strip()
                        if p_name in ("view expanded text", "view original text", "view text") and p_val:
                            return p_val
                    return json.dumps({"describe_records": records}, default=str)
                finally:
                    conn.close()
            except Exception as exc:
                msg = str(exc).lower()
                if any(code in msg for code in ("401", "403", "unauthorized", "authentication", "invalid access token")):
                    logger.error("Databricks authentication failed for view %s: %s", view_name, exc)
                    raise RuntimeError("warehouse unavailable: authentication failed") from exc
                if "timeout" in msg or "timed out" in msg:
                    logger.error("Databricks query timed out for view %s: %s", view_name, exc)
                    raise RuntimeError("warehouse unavailable: query timed out") from exc
                logger.error("Databricks warehouse error for view %s: %s", view_name, exc)
                raise RuntimeError(f"warehouse unavailable: {exc}") from exc

        # 1. First attempt SHOW CREATE TABLE
        try:
            rows = self._execute_sql_statement(f"SHOW CREATE TABLE {safe_name}", cfg)
            if rows and rows[0] and rows[0][0]:
                return str(rows[0][0])
        except Exception as show_exc:
            exc_str = str(show_exc).lower()
            if "view not found" in exc_str or "table_or_view_not_found" in exc_str:
                raise RuntimeError(f"view not found: {show_exc}") from show_exc
            if "authentication failed" in exc_str:
                raise RuntimeError("warehouse unavailable: authentication failed") from show_exc
            if "query timed out" in exc_str:
                raise RuntimeError("warehouse unavailable: query timed out") from show_exc
            logger.warning(
                "SHOW CREATE TABLE failed for %s (falling back to DESCRIBE): %s",
                safe_name, show_exc,
            )

        # 2. Fallback to DESCRIBE TABLE EXTENDED
        try:
            rows = self._execute_sql_statement(f"DESCRIBE TABLE EXTENDED {safe_name}", cfg)
            for r in rows:
                if len(r) >= 2:
                    prop_name = str(r[0]).strip().lower()
                    prop_val = str(r[1]).strip()
                    if prop_name in ("view expanded text", "view original text", "view text") and prop_val:
                        return prop_val

            records = [{"col_name": r[0] if len(r) > 0 else "", "data_type": r[1] if len(r) > 1 else "", "comment": r[2] if len(r) > 2 else ""} for r in rows]
            return json.dumps({"describe_records": records}, default=str)
        except Exception as desc_exc:
            exc_str = str(desc_exc).lower()
            if "view not found" in exc_str or "table_or_view_not_found" in exc_str:
                raise RuntimeError(f"view not found: {desc_exc}") from desc_exc
            if "authentication failed" in exc_str:
                raise RuntimeError("warehouse unavailable: authentication failed") from desc_exc
            if "query timed out" in exc_str:
                raise RuntimeError("warehouse unavailable: query timed out") from desc_exc
            logger.error("Databricks warehouse error for view %s: %s", view_name, desc_exc)
            raise RuntimeError(f"warehouse unavailable: {desc_exc}") from desc_exc

    def list_consumers(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List downstream consumer assets (e.g. table lineage, Genie spaces) consuming view_name.

        API Limitation - Lakeview Dashboards:
        The Databricks Lakeview / AI/BI Dashboard API (GET /api/2.0/lakeview/dashboards)
        does not expose dataset or view source lineage. As confirmed during spike analysis,
        this is recorded as an upstream Databricks API limitation, not outstanding work.
        Heuristic parsing or inferring links from widget JSON is deliberately not performed.
        """
        consumers: List[Dict[str, Any]] = []
        if not view_name:
            return consumers

        try:
            cfg = self._resolve_connection_config(connection_config, view_name=view_name)
        except Exception:
            return consumers

        host = cfg.get("host")
        token = cfg.get("token")
        if not host or not token:
            return consumers

        v_upper = view_name.upper().strip().strip("`")
        v_parts = [p.strip().strip("`") for p in v_upper.split(".")]
        simple_name = v_parts[-1]

        # 1. Discover table lineage from Unity Catalog system table
        try:
            lineage_sql = (
                f"SELECT target_table_full_name, target_type "
                f"FROM system.information_schema.table_lineage "
                f"WHERE UPPER(source_table_full_name) = '{v_upper}' "
                f"LIMIT 50"
            )
            rows = self._execute_sql_statement(lineage_sql, connection_config=connection_config, timeout=15)
            for r in rows:
                if len(r) >= 1 and r[0]:
                    t_name = str(r[0])
                    t_type = str(r[1]) if len(r) > 1 and r[1] else "TABLE"
                    consumers.append({
                        "name": f"Lineage Target: {t_name}",
                        "type": "consumer_asset",
                        "platform": "DATABRICKS",
                        "category": "TABLE_LINEAGE",
                        "details": f"Downstream Unity Catalog {t_type} '{t_name}'.",
                    })
        except Exception as lin_exc:
            logger.debug("Unity Catalog table lineage query failed: %s", lin_exc)

        # 2. Discover Databricks Genie spaces referencing this view
        try:
            import json, requests
            clean_host = host.replace("https://", "").replace("http://", "").rstrip("/")
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            resp = requests.get(
                f"https://{clean_host}/api/2.0/genie/spaces",
                headers=headers,
                params={"page_size": 100},
                timeout=15,
            )
            if resp.status_code == 200:
                spaces = resp.json().get("spaces") or []
                for sp in spaces:
                    sp_id = sp.get("space_id") or sp.get("id")
                    sp_title = sp.get("title") or sp.get("name") or sp_id
                    tables = sp.get("tables") or []
                    has_view = any(
                        simple_name in str(t.get("identifier") or t.get("table_name") or t).upper()
                        for t in tables
                    )
                    if not has_view and sp_id:
                        try:
                            sp_resp = requests.get(
                                f"https://{clean_host}/api/2.0/genie/spaces/{sp_id}",
                                headers=headers,
                                params={"include_serialized_space": "true"},
                                timeout=10,
                            )
                            if sp_resp.status_code == 200:
                                sp_data = sp_resp.json()
                                sp_tables = sp_data.get("tables") or []
                                ser = sp_data.get("serialized_space")
                                if ser:
                                    try:
                                        ser_parsed = json.loads(ser) if isinstance(ser, str) else ser
                                        ds_tables = (ser_parsed.get("data_sources") or {}).get("tables") or []
                                        sp_tables = list(sp_tables) + list(ds_tables)
                                    except Exception:
                                        pass
                                has_view = any(
                                    simple_name in str(t.get("identifier") or t.get("table_name") or t).upper()
                                    for t in sp_tables
                                )
                        except Exception:
                            pass
                    if has_view:
                        consumers.append({
                            "name": f"Genie Space: {sp_title}",
                            "type": "consumer_asset",
                            "platform": "DATABRICKS",
                            "category": "GENIE_SPACE",
                            "details": f"Databricks Genie AI Space '{sp_title}' references this view.",
                        })
            else:
                logger.debug("Databricks Genie spaces API returned HTTP %d: %s", resp.status_code, resp.text[:200])
        except Exception as genie_exc:
            logger.debug("Databricks Genie spaces discovery failed: %s", genie_exc)

        return consumers

    def parse_definition(
        self,
        raw_text: str,
        view_name: str,
        source: str = "live",
        descriptions: Optional[Dict[str, str]] = None,
    ) -> ViewDefinition:
        """Parse Databricks raw definition text into a canonical ViewDefinition."""
        import textwrap

        objects: List[ObjectDef] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        extra_desc = {str(k).upper(): str(v).strip() for k, v in (descriptions or {}).items()}

        if not raw_text or not str(raw_text).strip():
            raise ValueError("Databricks definition text cannot be empty or truncated")

        text = textwrap.dedent(raw_text).strip()
        tbl_name = view_name.split(".")[-1].strip("`\"")

        # Case A: Metric View YAML (either embedded in WITH METRICS LANGUAGE YAML or raw YAML)
        yaml_content = self._extract_yaml_content(raw_text)
        if yaml_content is not None:
            try:
                parsed_yaml = yaml.safe_load(yaml_content)
                if isinstance(parsed_yaml, dict):
                    self._parse_metric_view_yaml(parsed_yaml, tbl_name, objects, extra_desc)
                    return ViewDefinition(
                        view_name=view_name,
                        platform=self.platform_name,
                        dialect="databricks",
                        source=source,
                        retrieved_at=now_iso,
                        objects=objects,
                        raw_definition_text=raw_text,
                        source_path="yaml",
                        capabilities=dict(DATABRICKS_CAPABILITIES_YAML),
                    )
                raise ValueError("not checked: unparseable definition: YAML root is not a dictionary")
            except yaml.YAMLError as yaml_exc:
                logger.warning("Failed parsing metric view YAML for %s: %s", view_name, yaml_exc)
                raise ValueError(f"not checked: unparseable definition: {yaml_exc}") from yaml_exc

        # Case B: DESCRIBE JSON records
        if text.startswith("{") and "describe_records" in text:
            try:
                data = json.loads(text)
                self._parse_describe_json(data.get("describe_records") or [], tbl_name, objects, extra_desc)
                return ViewDefinition(
                    view_name=view_name,
                    platform=self.platform_name,
                    dialect="databricks",
                    source=source,
                    retrieved_at=now_iso,
                    objects=objects,
                    raw_definition_text=raw_text,
                    source_path="describe_json",
                    capabilities=dict(DATABRICKS_CAPABILITIES_DESCRIBE_JSON),
                )
            except Exception as json_exc:
                logger.warning("Failed parsing DESCRIBE JSON records for %s: %s", view_name, json_exc)

        # Case C: SQL View DDL (CREATE OR REPLACE VIEW ... AS SELECT ...)
        self._parse_sql_view_ddl(text, tbl_name, objects, extra_desc)

        return ViewDefinition(
            view_name=view_name,
            platform=self.platform_name,
            dialect="databricks",
            source=source,
            retrieved_at=now_iso,
            objects=objects,
            raw_definition_text=raw_text,
            source_path="sql_ddl",
            capabilities=dict(DATABRICKS_CAPABILITIES_SQL_DDL),
        )

    def _extract_yaml_content(self, text: str) -> Optional[str]:
        """Extract YAML content from Databricks metric view DDL or return raw YAML if applicable."""
        import textwrap

        raw_yaml = None
        # 1. Match WITH METRICS LANGUAGE YAML AS $$ ... $$
        dollar_match = re.search(
            r'WITH\s+METRICS\s+LANGUAGE\s+YAML\s+AS\s*\$\$(.*?)\$\$',
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if dollar_match:
            raw_yaml = textwrap.dedent(dollar_match.group(1)).strip()

        # 2. Match WITH METRICS LANGUAGE YAML AS ' ... '
        if raw_yaml is None:
            single_quote_match = re.search(
                r"WITH\s+METRICS\s+LANGUAGE\s+YAML\s+AS\s*'(.*?)'",
                text,
                re.DOTALL | re.IGNORECASE,
            )
            if single_quote_match:
                raw_quoted = single_quote_match.group(1)
                raw_yaml = textwrap.dedent(raw_quoted.replace("\\'", "'").replace("''", "'")).strip()

        # 3. Check if text is raw YAML metric definition
        if raw_yaml is None and ("measures:" in text or "dimensions:" in text or "version:" in text):
            raw_yaml = textwrap.dedent(text).strip()

        if raw_yaml is not None:
            # Strip backticks from YAML identifiers so PyYAML parses cleanly
            cleaned = raw_yaml.replace("`", "")
            return cleaned

        return None

    def _parse_metric_view_yaml(
        self,
        yaml_dict: Dict[str, Any],
        table_name: str,
        objects: List[ObjectDef],
        extra_desc: Dict[str, str],
    ) -> None:
        """Parse measures and dimensions from a parsed Databricks metric view YAML dictionary."""
        # Parse measures -> ObjectKind.METRIC
        measures = yaml_dict.get("measures") or []
        if isinstance(measures, list):
            for m in measures:
                if not isinstance(m, dict):
                    continue
                name = _clean_identifier(m.get("name") or "")
                if not name:
                    continue
                expr = str(m.get("expr") or m.get("formula") or "").strip()
                desc = m.get("comment") or m.get("description")
                if desc is not None:
                    desc = str(desc).strip()
                else:
                    desc = extra_desc.get(name.upper())

                objects.append(
                    ObjectDef(
                        kind=ObjectKind.METRIC,
                        table=table_name,
                        name=name,
                        expression=expr,
                        description=desc,
                        properties={k: v for k, v in m.items() if k not in ("name", "expr", "formula", "comment", "description")},
                    )
                )

        # Parse dimensions -> ObjectKind.DIMENSION
        dimensions = yaml_dict.get("dimensions") or []
        if isinstance(dimensions, list):
            for d in dimensions:
                if not isinstance(d, dict):
                    continue
                name = _clean_identifier(d.get("name") or "")
                if not name:
                    continue
                expr = str(d.get("expr") or d.get("formula") or "").strip()
                desc = d.get("comment") or d.get("description")
                if desc is not None:
                    desc = str(desc).strip()
                else:
                    desc = extra_desc.get(name.upper())

                objects.append(
                    ObjectDef(
                        kind=ObjectKind.DIMENSION,
                        table=table_name,
                        name=name,
                        expression=expr,
                        description=desc,
                        properties={k: v for k, v in d.items() if k not in ("name", "expr", "formula", "comment", "description")},
                    )
                )

        # Parse fact / source table -> ObjectKind.FACT
        if yaml_dict.get("source"):
            src_val = str(yaml_dict["source"]).strip().strip("`\"")
            objects.append(
                ObjectDef(
                    kind=ObjectKind.FACT,
                    table=table_name,
                    name=table_name,
                    expression=src_val,
                    description=str(yaml_dict.get("comment") or yaml_dict.get("description") or "").strip() or None,
                )
            )
        tables = yaml_dict.get("tables") or []
        if isinstance(tables, list):
            for t in tables:
                if not isinstance(t, dict):
                    continue
                t_name = str(t.get("name") or "").strip().strip("`\"")
                t_src = str(t.get("source") or t.get("table") or t_name).strip().strip("`\"")
                if t_name or t_src:
                    objects.append(
                        ObjectDef(
                            kind=ObjectKind.FACT,
                            table=table_name,
                            name=t_name or t_src,
                            expression=t_src or t_name,
                            description=str(t.get("comment") or t.get("description") or "").strip() or None,
                        )
                    )

        # Parse joins / relationships -> ObjectKind.RELATIONSHIP
        joins = yaml_dict.get("joins") or yaml_dict.get("relationships") or []
        if isinstance(joins, list):
            for j in joins:
                if not isinstance(j, dict):
                    continue
                j_name = str(j.get("name") or "").strip().strip("`\"")
                j_from = str(j.get("source") or j.get("from") or table_name).strip().strip("`\"")
                j_to = str(j.get("target") or j.get("to") or "").strip().strip("`\"")
                j_on = str(j.get("on") or "").strip()
                if not j_on and "keys" in j:
                    k_pairs = []
                    for k in (j.get("keys") or []):
                        if isinstance(k, dict):
                            k_pairs.append(f"{j_from}.{k.get('from')} = {j_to}.{k.get('to')}")
                    j_on = " AND ".join(k_pairs)
                if not j_name:
                    j_name = f"{j_from}->{j_to}" if j_to else f"{table_name}_join"
                objects.append(
                    ObjectDef(
                        kind=ObjectKind.RELATIONSHIP,
                        table=table_name,
                        name=j_name,
                        expression=j_on,
                        properties={"from_table": j_from, "to_table": j_to},
                    )
                )

    def _parse_describe_json(
        self,
        records: List[Dict[str, Any]],
        table_name: str,
        objects: List[ObjectDef],
        extra_desc: Dict[str, str],
    ) -> None:
        """Parse column records from DESCRIBE JSON output."""
        for rec in records:
            col_name = _clean_identifier(rec.get("col_name") or rec.get("name") or "")
            if not col_name or col_name.startswith("#"):
                continue
            comment = rec.get("comment") or extra_desc.get(col_name.upper())
            data_type = str(rec.get("data_type") or "").strip()

            # Determine kind: if column starts with metric prefix or contains agg keyword, or metric by convention
            is_metric = _AGG_PREFIX_RE.match(data_type) is not None or "metric" in col_name.lower()
            kind = ObjectKind.METRIC if is_metric else ObjectKind.DIMENSION

            objects.append(
                ObjectDef(
                    kind=kind,
                    table=table_name,
                    name=col_name,
                    expression=None,
                    description=str(comment).strip() if comment else None,
                )
            )

    def _parse_sql_view_ddl(
        self,
        ddl: str,
        table_name: str,
        objects: List[ObjectDef],
        extra_desc: Dict[str, str],
    ) -> None:
        """Parse standard SQL view statements (CREATE VIEW ... AS SELECT ...)."""
        import sqlglot
        from sqlglot import expressions as exp

        # Collect any ALTER VIEW ... ALTER COLUMN ... COMMENT statements
        alter_comments: Dict[str, str] = {}
        for alter_match in re.finditer(
            r'ALTER\s+VIEW\s+[^\s]+\s+ALTER\s+COLUMN\s+([^\s]+)\s+COMMENT\s+\'((?:[^\']|\'\')*)\'',
            ddl,
            re.IGNORECASE,
        ):
            c_name = alter_match.group(1).strip("`\"").upper()
            c_comment = alter_match.group(2).replace("''", "'").strip()
            alter_comments[c_name] = c_comment

        # Extract the CREATE VIEW statement if multiple statements are concatenated
        create_view_sql = ddl
        for stmt in ddl.split(";"):
            if "CREATE" in stmt.upper() and "VIEW" in stmt.upper():
                create_view_sql = stmt
                break

        try:
            parsed = sqlglot.parse_one(create_view_sql, read="databricks")
        except Exception as exc:
            logger.warning("Failed parsing SQL view DDL with Databricks dialect: %s", exc)
            raise ValueError(f"not checked: unparseable definition: {exc}") from exc

        if parsed is None:
            raise ValueError("not checked: unparseable definition: SQL view DDL could not be parsed")

        # Find the SELECT query inside the statement (supports CTEs, subqueries, complex views)
        select_node = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
        if select_node is None:
            raise ValueError("not checked: unparseable definition: no SELECT statement found in view DDL")

        for proj in select_node.expressions:
            if isinstance(proj, exp.Star):
                continue

            # Alias / Name
            if isinstance(proj, exp.Alias):
                alias = _clean_identifier(proj.alias)
                expr_ast = proj.this
            else:
                alias = _clean_identifier(proj.alias_or_name)
                expr_ast = proj

            # Classify kind: AST contains an aggregate function -> METRIC, else DIMENSION
            is_metric = bool(expr_ast.find(exp.AggFunc))
            kind = ObjectKind.METRIC if is_metric else ObjectKind.DIMENSION

            # Generate canonical expression string without outer parentheses
            raw_expr = expr_ast.sql(dialect="databricks", comments=False, identify=False, normalize_functions="lower").strip()
            while raw_expr.startswith("(") and raw_expr.endswith(")"):
                raw_expr = raw_expr[1:-1].strip()

            final_desc = alter_comments.get(alias.upper()) or extra_desc.get(alias.upper())

            objects.append(
                ObjectDef(
                    kind=kind,
                    table=table_name,
                    name=alias,
                    expression=raw_expr,
                    description=final_desc,
                )
            )

        # Parse underlying fact table from FROM clause -> ObjectKind.FACT
        from_table = select_node.find(exp.Table)
        if from_table is not None:
            src_tbl = from_table.name
            alias = from_table.alias or src_tbl.split(".")[-1]
            objects.append(
                ObjectDef(
                    kind=ObjectKind.FACT,
                    table=table_name,
                    name=_clean_identifier(alias),
                    expression=from_table.sql(dialect="databricks", identify=False),
                    description=None,
                )
            )

    def _split_select_items(self, select_clause: str) -> List[str]:
        """Split SELECT projection items by comma respecting parentheses and string quotes."""
        items: List[str] = []
        current: List[str] = []
        depth = 0
        in_str = False
        str_char = ""

        for char in select_clause:
            if char in ("'", '"', '`'):
                if not in_str:
                    in_str = True
                    str_char = char
                elif str_char == char:
                    in_str = False
            elif not in_str:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth = max(0, depth - 1)
                elif char == "," and depth == 0:
                    item_str = "".join(current).strip()
                    if item_str:
                        items.append(item_str)
                    current = []
                    continue
            current.append(char)

        last_item = "".join(current).strip()
        if last_item:
            items.append(last_item)

        return items

    def inject_verified_queries(
        self,
        pairs: List[Dict[str, Any]],
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Inject verified question/SQL pairs into a Databricks Genie Space configuration.

        Spike Finding (Databricks AI/BI Genie):
        Databricks Genie stores instructions, example queries, and evaluation benchmarks
        inside the `serialized_space` JSON string payload via the Genie Spaces Management API:
        PATCH /api/2.0/genie/spaces/{space_id}.
        Writing to it requires fetching the existing configuration via
        GET /api/2.0/genie/spaces/{space_id}?include_serialized_space=true, parsing the
        `serialized_space` JSON string, appending to `example_queries` and `benchmarks`,
        and pushing the updated serialized string back via PATCH.
        Requires CAN EDIT permissions on the Genie space.
        """
        if not pairs:
            return {
                "supported": True,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": "No qualifying verified query pairs provided for injection.",
                "dry_run": dry_run,
            }

        try:
            cfg = self._resolve_connection_config(connection_config, view_name=view_name)
        except Exception as exc:
            return {
                "supported": False,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": f"Databricks connection resolution failed: {exc}",
                "dry_run": dry_run,
            }

        host = cfg.get("host")
        token = cfg.get("token")
        if not host or not token:
            return {
                "supported": False,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": "Databricks Genie space update refused: missing host or token credentials.",
                "dry_run": dry_run,
            }

        import json
        import requests

        clean_host = host.replace("https://", "").replace("http://", "").rstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        v_upper = view_name.upper().strip().strip("`")
        v_parts = [p.strip().strip("`") for p in v_upper.split(".")]
        simple_name = v_parts[-1]

        target_space_id = None
        target_space_title = None
        space_data = None

        try:
            list_resp = requests.get(
                f"https://{clean_host}/api/2.0/genie/spaces",
                headers=headers,
                params={"page_size": 100},
                timeout=15,
            )
            if list_resp.status_code == 200:
                spaces = list_resp.json().get("spaces") or []
                for sp in spaces:
                    sp_id = sp.get("space_id") or sp.get("id")
                    sp_title = sp.get("title") or sp.get("name") or sp_id
                    tables = sp.get("tables") or []
                    has_view = any(
                        simple_name in str(t.get("identifier") or t.get("table_name") or t).upper()
                        for t in tables
                    )
                    if not has_view and sp_id:
                        try:
                            sp_resp = requests.get(
                                f"https://{clean_host}/api/2.0/genie/spaces/{sp_id}",
                                headers=headers,
                                params={"include_serialized_space": "true"},
                                timeout=10,
                            )
                            if sp_resp.status_code == 200:
                                cand_data = sp_resp.json()
                                sp_tables = cand_data.get("tables") or []
                                ser = cand_data.get("serialized_space")
                                if ser:
                                    try:
                                        ser_parsed = json.loads(ser) if isinstance(ser, str) else ser
                                        ds_tables = (ser_parsed.get("data_sources") or {}).get("tables") or []
                                        sp_tables = list(sp_tables) + list(ds_tables)
                                    except Exception:
                                        pass
                                if any(simple_name in str(t.get("identifier") or t.get("table_name") or t).upper() for t in sp_tables):
                                    has_view = True
                                    space_data = cand_data
                        except Exception:
                            pass
                    if has_view:
                        target_space_id = sp_id
                        target_space_title = sp_title
                        if space_data is None:
                            full_resp = requests.get(
                                f"https://{clean_host}/api/2.0/genie/spaces/{sp_id}",
                                headers=headers,
                                params={"include_serialized_space": "true"},
                                timeout=10,
                            )
                            if full_resp.status_code == 200:
                                space_data = full_resp.json()
                        break
        except Exception as net_exc:
            return {
                "supported": True,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": f"Databricks Genie spaces discovery network error: {net_exc}",
                "dry_run": dry_run,
            }

        if not target_space_id or not space_data:
            return {
                "supported": True,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": f"Databricks Genie space update refused: no matching Genie space found referencing '{simple_name}'.",
                "dry_run": dry_run,
            }

        # Parse serialized space
        raw_ser = space_data.get("serialized_space") or "{}"
        try:
            ser_dict = json.loads(raw_ser) if isinstance(raw_ser, str) else (raw_ser or {})
        except Exception as json_exc:
            return {
                "supported": True,
                "injected_count": 0,
                "target": target_space_title,
                "details": [],
                "reason": f"Failed to parse serialized_space from Genie space '{target_space_title}': {json_exc}",
                "dry_run": dry_run,
            }

        ex_queries = ser_dict.get("example_queries")
        if not isinstance(ex_queries, list):
            ex_queries = []
            ser_dict["example_queries"] = ex_queries

        benchmarks = ser_dict.get("benchmarks")
        if not isinstance(benchmarks, list):
            benchmarks = []
            ser_dict["benchmarks"] = benchmarks

        existing_questions = {str(q.get("question") or "").strip().lower() for q in ex_queries if isinstance(q, dict)}

        to_inject = []
        for p in pairs:
            q_text = str(p.get("question") or "").strip()
            if not q_text or q_text.lower() in existing_questions:
                continue
            to_inject.append({
                "question": q_text,
                "sql": p.get("sql") or "",
            })

        if not to_inject:
            return {
                "supported": True,
                "injected_count": 0,
                "target": f"Genie Space: {target_space_title} ({target_space_id})",
                "details": [],
                "reason": "All qualifying query pairs already exist in Genie space example queries.",
                "dry_run": dry_run,
            }

        if dry_run:
            return {
                "supported": True,
                "injected_count": len(to_inject),
                "target": f"Genie Space: {target_space_title} ({target_space_id})",
                "details": to_inject,
                "reason": f"Dry run: {len(to_inject)} query pairs prepared for injection into Genie space '{target_space_title}'.",
                "dry_run": True,
            }

        # Update space via PATCH
        ex_queries.extend(to_inject)
        for item in to_inject:
            benchmarks.append({
                "question": item["question"],
                "expected_sql": item["sql"],
            })

        payload = {
            "title": space_data.get("title") or target_space_title,
            "description": space_data.get("description") or f"Governed space updated by SemaBridge with {len(to_inject)} verified queries.",
            "serialized_space": json.dumps(ser_dict),
        }

        try:
            patch_resp = requests.patch(
                f"https://{clean_host}/api/2.0/genie/spaces/{target_space_id}",
                headers=headers,
                json=payload,
                timeout=15,
            )
            if patch_resp.status_code not in (200, 204):
                return {
                    "supported": True,
                    "injected_count": 0,
                    "target": f"Genie Space: {target_space_title} ({target_space_id})",
                    "details": [],
                    "reason": f"Databricks Genie space update failed (HTTP {patch_resp.status_code}): {patch_resp.text[:300]}",
                    "dry_run": False,
                }
            return {
                "supported": True,
                "injected_count": len(to_inject),
                "target": f"Genie Space: {target_space_title} ({target_space_id})",
                "details": to_inject,
                "reason": f"Successfully injected {len(to_inject)} verified query pairs into Genie space '{target_space_title}'.",
                "dry_run": False,
            }
        except Exception as patch_exc:
            return {
                "supported": True,
                "injected_count": 0,
                "target": f"Genie Space: {target_space_title} ({target_space_id})",
                "details": [],
                "reason": f"Databricks Genie space PATCH request failed: {patch_exc}",
                "dry_run": False,
            }
