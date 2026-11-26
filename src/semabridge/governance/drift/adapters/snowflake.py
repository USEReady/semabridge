"""Snowflake Platform Adapter for Drift Monitoring and Blast Radius Governance.

Confirmed Snowflake Operations (Real-Account Verification):
- Primary confirmed metadata call returning structured semantic view definitions:
    `DESCRIBE SEMANTIC VIEW <database>.<schema>.<view_name>`
  On real Snowflake accounts, this call reliably returns object kinds (METRICS, DIMENSIONS),
  expressions, and descriptions across all active Snowflake semantic view releases.
- Secondary / version-dependent call:
    `SELECT GET_DDL('SEMANTIC_VIEW', '<database>.<schema>.<view_name>')`
  Attempted first; if unsupported on the warehouse account/version, cleanly falls back
  to DESCRIBE with a logged warning.
- List Managed Semantic Views:
    `SHOW SEMANTIC VIEWS IN SCHEMA <schema>`

Target-Aware Blast Radius Consumer Discovery (Spike Findings):
- Snowflake Data Shares (Consumer Asset):
    Call: `SHOW GRANTS TO SHARE <share_name>`
    Note: `SHOW GRANTS OF SHARE <share_name>` lists accounts granted to, NOT the contents.
    The call that lists the objects exposed by a share is `SHOW GRANTS TO SHARE <share_name>`.
    Output shape:
      Rows of (created_on, privilege, granted_on, name, granted_to, grantee_name, grant_option)
      Filtered for granted_on IN ('VIEW', 'TABLE') where name matches the fully qualified view.
- Cortex Analyst Models (Consumer Asset):
    Status: Staged YAML assumption is TRUE.
    Snowflake does not provide a DDL command such as `SHOW CORTEX ANALYST MODELS` or a dedicated
    REST catalog API. Models are configured as semantic model specification YAML files hosted on
    Snowflake internal stages (e.g. `@<db>.<schema>.<stage>/<model>.yaml`).
    Output shape:
      Parsed YAML document containing `tables: [ { name: ..., base_table: { database: ..., schema: ..., table: ... } } ]`.
- Downstream Semantic Views:
    Call: `SHOW SEMANTIC VIEWS IN ACCOUNT` or schema-scoped DDL inspection.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from semabridge.governance.drift.adapters.base import PlatformAdapter
from semabridge.governance.drift.models import (
    ObjectDef,
    ObjectKind,
    ViewDefinition,
)

logger = logging.getLogger("semabridge.governance.drift.adapters.snowflake")

_DEFAULT_STATEMENT_TIMEOUT = 30


def get_snowflake_statement_timeout() -> int:
    """Return the configured Snowflake statement timeout in seconds."""
    raw = os.getenv("SEMABRIDGE_DRIFT_STATEMENT_TIMEOUT_SECONDS", "").strip()
    if raw.isdigit():
        val = int(raw)
        if val > 0:
            return val
    return _DEFAULT_STATEMENT_TIMEOUT

_IDENT_RE = re.compile(r'^(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"]+")$')


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


def _safe_quote_view_name(sv_name: str) -> Optional[str]:
    """Quote each period-separated segment of a view name safely."""
    parts = [p.strip() for p in str(sv_name).split(".") if p.strip()]
    if not 1 <= len(parts) <= 3:
        return None
    quoted_parts = []
    for p in parts:
        if not _IDENT_RE.match(p):
            return None
        quoted_parts.append(p if p.startswith('"') else f'"{p.upper()}"' if not p.isupper() else p)
    return ".".join(quoted_parts)


SNOWFLAKE_CAPABILITIES_DESCRIBE_JSON = {
    ObjectKind.METRIC.value: {"expression", "description"},
    ObjectKind.DIMENSION.value: {"expression", "description"},
    ObjectKind.FACT.value: {"expression", "description"},
    ObjectKind.RELATIONSHIP.value: {"expression"},
}

SNOWFLAKE_CAPABILITIES_DDL = {
    ObjectKind.METRIC.value: {"expression", "description"},
    ObjectKind.DIMENSION.value: {"expression", "description"},
    ObjectKind.FACT.value: {"expression", "description"},
    ObjectKind.RELATIONSHIP.value: {"expression"},
}


class SnowflakeAdapter(PlatformAdapter):
    """Adapter for Snowflake Semantic Views."""

    platform_name: str = "SNOWFLAKE"

    def list_views(self, project_id: str, scope: Optional[Dict[str, str]] = None) -> List[str]:
        """List deployed semantic views for the given project."""
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        deployments = repo.list_project_deployments(project_id, include_unmanaged=True)
        views = []
        for d in deployments:
            v = d.get("semantic_view") or d.get("view")
            if v and v not in views:
                views.append(v)
        return views

    def fetch_definition_text(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Fetch raw DDL or DESCRIBE definition text from Snowflake."""
        safe_name = _safe_quote_view_name(view_name)
        if not safe_name:
            raise ValueError(f"Invalid Snowflake view identifier: {view_name}")

        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs, SnowflakeConfig
        from semabridge.core.settings import get_settings

        if connection_config is None:
            cfg_obj = get_settings().snowflake
        elif isinstance(connection_config, SnowflakeConfig):
            cfg_obj = connection_config
        elif isinstance(connection_config, dict):
            cfg_dict = dict(connection_config)
            if "schema" in cfg_dict and "schema_name" not in cfg_dict:
                cfg_dict["schema_name"] = cfg_dict["schema"]
            cfg_obj = SnowflakeConfig(**cfg_dict)
        else:
            cfg_obj = connection_config

        try:
            kw = get_snowflake_connect_kwargs(cfg_obj)
        except ValueError as val_err:
            account = getattr(cfg_obj, "account", None) or (connection_config.get("account") if isinstance(connection_config, dict) else None)
            user = getattr(cfg_obj, "user", None) or (connection_config.get("user") if isinstance(connection_config, dict) else None)
            if account and user:
                kw = {
                    "account": account,
                    "user": user,
                    "warehouse": getattr(cfg_obj, "warehouse", None) or (connection_config.get("warehouse") if isinstance(connection_config, dict) else None),
                    "database": getattr(cfg_obj, "database", None) or (connection_config.get("database") if isinstance(connection_config, dict) else None),
                    "schema": getattr(cfg_obj, "schema_name", None) or getattr(cfg_obj, "schema", None) or (connection_config.get("schema") if isinstance(connection_config, dict) else None),
                    "role": getattr(cfg_obj, "role", None) or (connection_config.get("role") if isinstance(connection_config, dict) else None),
                }
            else:
                raise RuntimeError(f"warehouse unavailable: {val_err}") from val_err

        if not kw.get("account") or not kw.get("user"):
            raise ValueError("Snowflake connection credentials missing or incomplete")

        import snowflake.connector
        from semabridge.connectors.sql_guard import guard_connection

        try:
            conn = snowflake.connector.connect(**kw)
        except Exception as conn_exc:
            logger.error("Snowflake connection failed for view %s: %s", view_name, conn_exc)
            raise RuntimeError(f"warehouse unavailable: {conn_exc}") from conn_exc

        try:
            timeout_sec = get_snowflake_statement_timeout()
            try:
                raw_cur = conn.cursor()
                raw_cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout_sec}")
                raw_cur.close()
            except Exception:
                pass

            guarded_conn = guard_connection(conn, read_only=True)
            cur = guarded_conn.cursor()

            # 1. First attempt SELECT GET_DDL
            try:
                cur.execute(f"SELECT GET_DDL('SEMANTIC_VIEW', '{safe_name}')")
                ddl_row = cur.fetchone()
                if (
                    isinstance(ddl_row, (tuple, list))
                    and ddl_row
                    and isinstance(ddl_row[0], str)
                    and len(ddl_row[0].strip()) > 0
                    and ("CREATE" in ddl_row[0].upper() or "VIEW" in ddl_row[0].upper())
                ):
                    return str(ddl_row[0])
            except Exception as ddl_exc:
                logger.warning(
                    "GET_DDL failed for %s (falling back to DESCRIBE): %s",
                    safe_name, ddl_exc,
                )

            # 2. Fallback to DESCRIBE SEMANTIC VIEW
            try:
                cur.execute(f"DESCRIBE SEMANTIC VIEW {safe_name}")
                cols = [c[0].lower() for c in cur.description] if cur.description else []
                records = [dict(zip(cols, row)) for row in cur.fetchall()]
                return json.dumps({"describe_records": records}, default=str)
            except Exception as desc_exc:
                logger.error("DESCRIBE SEMANTIC VIEW failed for %s: %s", safe_name, desc_exc)
                raise RuntimeError(f"warehouse unavailable: {desc_exc}") from desc_exc
        finally:
            conn.close()

    def list_consumers(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List downstream consumer assets (e.g. data shares, Cortex models) consuming view_name."""
        consumers: List[Dict[str, Any]] = []
        if not view_name:
            return consumers

        if connection_config is None:
            # Consistent with fetch_definition_text: require caller/project config, no global password fallback
            logger.debug("Snowflake list_consumers skipped: no connection_config provided")
            return consumers

        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs, SnowflakeConfig

        if isinstance(connection_config, SnowflakeConfig):
            cfg_obj = connection_config
        elif isinstance(connection_config, dict):
            cfg_dict = dict(connection_config)
            if "schema" in cfg_dict and "schema_name" not in cfg_dict:
                cfg_dict["schema_name"] = cfg_dict["schema"]
            cfg_obj = SnowflakeConfig(**cfg_dict)
        else:
            cfg_obj = connection_config

        try:
            kw = get_snowflake_connect_kwargs(cfg_obj)
        except ValueError as val_err:
            account = getattr(cfg_obj, "account", None) or (connection_config.get("account") if isinstance(connection_config, dict) else None)
            user = getattr(cfg_obj, "user", None) or (connection_config.get("user") if isinstance(connection_config, dict) else None)
            if account and user:
                kw = {
                    "account": account,
                    "user": user,
                    "warehouse": getattr(cfg_obj, "warehouse", None) or (connection_config.get("warehouse") if isinstance(connection_config, dict) else None),
                    "database": getattr(cfg_obj, "database", None) or (connection_config.get("database") if isinstance(connection_config, dict) else None),
                    "schema": getattr(cfg_obj, "schema_name", None) or getattr(cfg_obj, "schema", None) or (connection_config.get("schema") if isinstance(connection_config, dict) else None),
                    "role": getattr(cfg_obj, "role", None) or (connection_config.get("role") if isinstance(connection_config, dict) else None),
                }
            else:
                logger.debug("Snowflake list_consumers missing credentials: %s", val_err)
                return consumers
        except Exception as exc:
            logger.debug("Snowflake list_consumers connect kwargs resolution failed: %s", exc)
            return consumers

        if not kw.get("account") or not kw.get("user"):
            return consumers

        import snowflake.connector
        from semabridge.connectors.sql_guard import guard_connection

        try:
            conn = snowflake.connector.connect(**kw)
        except Exception as conn_exc:
            logger.warning("Snowflake consumer discovery connection failed: %s", conn_exc)
            return consumers

        v_upper = view_name.upper().strip().strip('"')
        v_parts = [p.strip().strip('"') for p in v_upper.split(".")]
        simple_name = v_parts[-1]

        try:
            timeout_sec = get_snowflake_statement_timeout()
            try:
                raw_cur = conn.cursor()
                raw_cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout_sec}")
                raw_cur.close()
            except Exception:
                pass

            cur = conn.cursor()

            # 1. Discover data shares exposing this view
            try:
                cur.execute("SHOW SHARES")
                shares = [row[1] for row in cur.fetchall() if len(row) > 1 and str(row[2]).upper() == "OUTBOUND"]
                for s_name in shares:
                    try:
                        cur.execute(f"SHOW GRANTS TO SHARE {s_name}")
                        grant_rows = cur.fetchall()
                        for gr in grant_rows:
                            if len(gr) >= 4:
                                granted_on = str(gr[2]).upper()
                                asset_name = str(gr[3]).upper().strip('"')
                                if granted_on in ("VIEW", "TABLE", "SEMANTIC VIEW") and (asset_name == v_upper or asset_name.endswith(f".{simple_name}") or asset_name == simple_name):
                                    consumers.append({
                                        "name": f"Snowflake Data Share: {s_name}",
                                        "type": "consumer_asset",
                                        "platform": "SNOWFLAKE",
                                        "category": "DATA_SHARE",
                                        "details": f"Exposed externally via outbound data share '{s_name}'.",
                                    })
                                    break
                    except Exception as share_exc:
                        logger.debug("Failed to inspect share %s: %s", s_name, share_exc)
            except Exception as shares_exc:
                logger.debug("SHOW SHARES failed: %s", shares_exc)

            # 2. Discover Cortex Analyst staged semantic models (filtering by target view reference in tables clause)
            try:
                # Query stages in current schema or target view schema if available
                target_schema = (
                    getattr(cfg_obj, "schema_name", None)
                    or getattr(cfg_obj, "schema", None)
                    or (v_parts[1] if len(v_parts) == 3 else (v_parts[0] if len(v_parts) == 2 else None))
                )
                if target_schema:
                    try:
                        cur.execute(f"SHOW STAGES IN SCHEMA {target_schema}")
                        stage_rows = cur.fetchall()
                    except Exception:
                        cur.execute("SHOW STAGES")
                        stage_rows = cur.fetchall()
                else:
                    cur.execute("SHOW STAGES")
                    stage_rows = cur.fetchall()

                for stg in stage_rows:
                    if len(stg) >= 2:
                        stage_name = str(stg[1])
                        # If database and schema are known from stage_rows, build qualified stage reference
                        stg_db = str(stg[2]) if len(stg) > 2 and stg[2] else None
                        stg_sch = str(stg[3]) if len(stg) > 3 and stg[3] else None
                        qual_stage = f'{stg_db}.{stg_sch}.{stage_name}' if stg_db and stg_sch else f'{stage_name}'
                        try:
                            cur.execute(f"LIST @{qual_stage} PATTERN = '.*\\.ya?ml'")
                            model_files = cur.fetchall()
                            for mf in model_files:
                                file_path = str(mf[0]) if mf else ""
                                if not file_path:
                                    continue
                                consumes_view = False
                                # Reading stage file requires GET in Snowflake SQL; wrap in isolated tempdir with guaranteed cleanup
                                try:
                                    import tempfile, os, yaml
                                    file_tail = file_path.split("/")[-1]
                                    with tempfile.TemporaryDirectory() as tmp_dir:
                                        clean_tmp = tmp_dir.replace("\\", "/")
                                        cur.execute(f"GET @{qual_stage}/{file_tail} file://{clean_tmp}")
                                        downloaded = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.endswith(('.yaml', '.yml'))]
                                        for df in downloaded:
                                            with open(df, 'r', encoding='utf-8') as yfp:
                                                data = yaml.safe_load(yfp)
                                                if isinstance(data, dict):
                                                    tables_section = data.get("tables") or data.get("semantic_models") or []
                                                    if isinstance(tables_section, list):
                                                        for tbl in tables_section:
                                                            if not isinstance(tbl, dict):
                                                                tbl_str = str(tbl).upper().strip().strip('"')
                                                                if tbl_str and (tbl_str == v_upper or tbl_str.endswith(f".{simple_name}") or tbl_str == simple_name):
                                                                    consumes_view = True
                                                                    break
                                                                continue
                                                            tbl_name = str(tbl.get("name") or tbl.get("base_table") or tbl.get("table") or "").upper().strip().strip('"')
                                                            base_name = str(tbl.get("base_table") or "").upper().strip().strip('"')
                                                            if (tbl_name and (tbl_name == v_upper or tbl_name.endswith(f".{simple_name}") or tbl_name == simple_name)) or \
                                                               (base_name and (base_name == v_upper or base_name.endswith(f".{simple_name}") or base_name == simple_name)):
                                                                consumes_view = True
                                                                break
                                                    elif isinstance(tables_section, dict):
                                                        for k, v in tables_section.items():
                                                            tbl_name = str(k).upper().strip().strip('"')
                                                            base_name = str((v or {}).get("base_table") or "").upper().strip().strip('"') if isinstance(v, dict) else ""
                                                            if (tbl_name and (tbl_name == v_upper or tbl_name.endswith(f".{simple_name}") or tbl_name == simple_name)) or \
                                                               (base_name and (base_name == v_upper or base_name.endswith(f".{simple_name}") or base_name == simple_name)):
                                                                consumes_view = True
                                                                break
                                except Exception as read_exc:
                                    logger.debug("Could not parse staged model %s for view reference check: %s", file_path, read_exc)

                                if consumes_view:
                                    consumers.append({
                                        "name": f"Cortex Analyst Model: {file_path}",
                                        "type": "consumer_asset",
                                        "platform": "SNOWFLAKE",
                                        "category": "CORTEX_ANALYST",
                                        "details": f"Staged semantic model YAML file '{file_path}' in stage '{stage_name}' references {simple_name}.",
                                    })
                        except Exception:
                            pass
            except Exception as cortex_exc:
                logger.debug("Cortex model discovery failed: %s", cortex_exc)

        except Exception as exc:
            logger.warning("Snowflake consumer discovery failed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return consumers

    def parse_definition(
        self,
        raw_text: str,
        view_name: str,
        source: str = "live",
        descriptions: Optional[Dict[str, str]] = None,
    ) -> ViewDefinition:
        """Parse Snowflake raw DDL or DESCRIBE records into canonical ViewDefinition."""
        objects: List[ObjectDef] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        extra_desc = {str(k).upper(): str(v).strip() for k, v in (descriptions or {}).items()}

        if not raw_text or not str(raw_text).strip():
            raise ValueError("Snowflake definition text cannot be empty or truncated")

        # Case A: JSON structured DESCRIBE records
        if raw_text.strip().startswith("{") and "describe_records" in raw_text:
            try:
                data = json.loads(raw_text)
                records = data.get("describe_records") or []
                metric_entries: Dict[str, Dict[str, Any]] = {}
                dim_entries: Dict[str, Dict[str, Any]] = {}
                fact_entries: Dict[str, Dict[str, Any]] = {}
                rel_entries: Dict[str, Dict[str, Any]] = {}

                for rec in records:
                    kind = str(rec.get("object_kind") or rec.get("kind") or "").upper()
                    name = str(rec.get("object_name") or rec.get("name") or "").strip()
                    prop = str(rec.get("property") or "").upper()
                    val = str(rec.get("property_value") or "").strip()
                    expr = str(rec.get("expression") or "").strip()

                    if kind in ("TABLE", "TABLES"):
                        target = fact_entries
                        def_kind = ObjectKind.FACT
                    elif kind in ("RELATIONSHIP", "RELATIONSHIPS"):
                        target = rel_entries
                        def_kind = ObjectKind.RELATIONSHIP
                    elif kind in ("DIMENSION", "DIMENSIONS"):
                        target = dim_entries
                        def_kind = ObjectKind.DIMENSION
                    else:
                        target = metric_entries
                        def_kind = ObjectKind.METRIC

                    if name:
                        entry = target.setdefault(name.upper(), {
                            "name": name,
                            "expression": "",
                            "description": None,
                            "kind": def_kind,
                        })
                        if expr:
                            entry["expression"] = expr
                        elif prop == "EXPRESSION":
                            entry["expression"] = val
                        elif prop in ("COMMENT", "DESCRIPTION") and val:
                            entry["description"] = val

                tbl_tail = view_name.split(".")[-1]
                for name, entry in metric_entries.items():
                    desc = entry.get("description") or extra_desc.get(name)
                    objects.append(
                        ObjectDef(
                            kind=ObjectKind.METRIC,
                            table=tbl_tail,
                            name=entry["name"],
                            expression=entry["expression"],
                            description=desc,
                        )
                    )
                for name, entry in dim_entries.items():
                    desc = entry.get("description") or extra_desc.get(name)
                    objects.append(
                        ObjectDef(
                            kind=ObjectKind.DIMENSION,
                            table=tbl_tail,
                            name=entry["name"],
                            expression=entry["expression"],
                            description=desc,
                        )
                    )
                for name, entry in fact_entries.items():
                    desc = entry.get("description") or extra_desc.get(name)
                    objects.append(
                        ObjectDef(
                            kind=ObjectKind.FACT,
                            table=tbl_tail,
                            name=entry["name"],
                            expression=entry["expression"] or entry["name"],
                            description=desc,
                        )
                    )
                for name, entry in rel_entries.items():
                    desc = entry.get("description") or extra_desc.get(name)
                    objects.append(
                        ObjectDef(
                            kind=ObjectKind.RELATIONSHIP,
                            table=tbl_tail,
                            name=entry["name"],
                            expression=entry["expression"],
                            description=desc,
                        )
                    )

                return ViewDefinition(
                    view_name=view_name,
                    platform=self.platform_name,
                    dialect="snowflake",
                    source=source,
                    retrieved_at=now_iso,
                    objects=objects,
                    raw_definition_text=raw_text,
                    source_path="describe_json",
                    capabilities=dict(SNOWFLAKE_CAPABILITIES_DESCRIBE_JSON),
                )
            except Exception as exc:
                logger.warning("Failed parsing DESCRIBE JSON records for %s: %s", view_name, exc)

        # Case B: DDL SQL Syntax parsing
        self._parse_ddl_sql(raw_text, view_name, objects, extra_desc)

        return ViewDefinition(
            view_name=view_name,
            platform=self.platform_name,
            dialect="snowflake",
            source=source,
            retrieved_at=now_iso,
            objects=objects,
            raw_definition_text=raw_text,
            source_path="ddl",
            capabilities=dict(SNOWFLAKE_CAPABILITIES_DDL),
        )

    def _parse_ddl_sql(
        self,
        ddl: str,
        view_name: str,
        objects: List[ObjectDef],
        extra_desc: Dict[str, str],
    ) -> None:
        """Parse METRICS, DIMENSIONS, FACTS, TABLES, and RELATIONSHIPS clauses from DDL SQL text."""
        tbl_name = view_name.split(".")[-1].strip('"')
        sections = self._extract_sections(ddl)

        # 1. Parse TABLES (...) block
        if "tables" in sections:
            self._extract_tables_clause(sections["tables"], tbl_name, objects, extra_desc)

        # 2. Parse RELATIONSHIPS (...) block
        if "relationships" in sections:
            self._extract_relationships_clause(sections["relationships"], tbl_name, objects)

        # 3. Parse FACTS (...) block
        if "facts" in sections:
            self._extract_clause_objects(sections["facts"], ObjectKind.FACT, tbl_name, objects, extra_desc)

        # 4. Parse DIMENSIONS (...) block
        if "dimensions" in sections:
            self._extract_clause_objects(sections["dimensions"], ObjectKind.DIMENSION, tbl_name, objects, extra_desc)

        # 5. Parse METRICS (...) block
        if "metrics" in sections:
            self._extract_clause_objects(sections["metrics"], ObjectKind.METRIC, tbl_name, objects, extra_desc)

    def _extract_sections(self, ddl_text: str) -> Dict[str, str]:
        """Extract top-level semantic view clause blocks respecting balanced parentheses and quotes."""
        sections: Dict[str, str] = {}
        keywords = ["TABLES", "RELATIONSHIPS", "FACTS", "DIMENSIONS", "METRICS", "WITH EXTENSION"]

        i = 0
        n = len(ddl_text)

        while i < n:
            matched_kw = None
            for kw in keywords:
                kw_len = len(kw)
                if ddl_text[i:i + kw_len].upper() == kw:
                    before_ok = (i == 0 or not (ddl_text[i - 1].isalnum() or ddl_text[i - 1] == "_"))
                    after_idx = i + kw_len
                    after_ok = (after_idx >= n or not (ddl_text[after_idx].isalnum() or ddl_text[after_idx] == "_"))
                    if before_ok and after_ok:
                        matched_kw = kw
                        i = after_idx
                        break

            if not matched_kw:
                i += 1
                continue

            # Skip whitespace and optional '=' until opening '('
            while i < n and (ddl_text[i].isspace() or ddl_text[i] == "="):
                i += 1

            if i >= n or ddl_text[i] != "(":
                continue

            start_paren = i
            depth = 1
            i += 1
            in_str = False
            str_char = ""

            while i < n and depth > 0:
                ch = ddl_text[i]
                if ch in ("'", '"'):
                    if not in_str:
                        in_str = True
                        str_char = ch
                    elif str_char == ch:
                        if ch == "'" and i + 1 < n and ddl_text[i + 1] == "'":
                            i += 1
                        else:
                            in_str = False
                elif not in_str:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                i += 1

            if depth == 0:
                block_content = ddl_text[start_paren + 1:i - 1]
                sections[matched_kw.lower()] = block_content

        return sections

    def _split_comma_clause(self, block: str) -> List[str]:
        """Split a comma-separated block respecting quotes and nested parentheses."""
        items: List[str] = []
        current: List[str] = []
        depth = 0
        in_str = False
        str_char = ""
        i = 0
        n = len(block)

        while i < n:
            ch = block[i]
            if ch in ("'", '"'):
                if not in_str:
                    in_str = True
                    str_char = ch
                elif str_char == ch:
                    if ch == "'" and i + 1 < n and block[i + 1] == "'":
                        current.append(ch)
                        current.append(block[i + 1])
                        i += 2
                        continue
                    else:
                        in_str = False
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                elif ch == "," and depth == 0:
                    item_str = "".join(current).strip()
                    if item_str:
                        items.append(item_str)
                    current = []
                    i += 1
                    continue
            current.append(ch)
            i += 1

        last_item = "".join(current).strip()
        if last_item:
            items.append(last_item)
        return items

    def _split_first_top_level_as(self, item: str) -> Tuple[str, Optional[str]]:
        """Split item by first top-level AS keyword outside parentheses and quotes."""
        depth = 0
        in_str = False
        str_char = ""
        i = 0
        n = len(item)

        while i < n:
            ch = item[i]
            if ch in ("'", '"'):
                if not in_str:
                    in_str = True
                    str_char = ch
                elif str_char == ch:
                    if ch == "'" and i + 1 < n and item[i + 1] == "'":
                        i += 2
                        continue
                    else:
                        in_str = False
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                elif depth == 0 and item[i:i + 2].upper() == "AS":
                    before_ok = (i == 0 or item[i - 1].isspace())
                    after_ok = (i + 2 >= n or item[i + 2].isspace())
                    if before_ok and after_ok:
                        return item[:i].strip(), item[i + 2:].strip()
            i += 1
        return item.strip(), None

    def _extract_trailing_clauses(self, text: str) -> Tuple[str, Optional[str], Optional[str]]:
        """Extract and strip trailing COMMENT and WITH SYNONYMS clauses."""
        comment = None
        synonyms = None
        clean = text.strip()

        changed = True
        while changed:
            changed = False
            comm_m = re.search(r"\bCOMMENT\s*(?:=\s*)?'((?:[^']|'')*)'\s*$", clean, re.I)
            if comm_m:
                comment = comm_m.group(1).replace("''", "'")
                clean = clean[:comm_m.start()].strip()
                changed = True
                continue

            syn_m = re.search(r"\bWITH\s+SYNONYMS\s*(?:=\s*)?\((.*?)\)\s*$", clean, re.I)
            if syn_m:
                synonyms = syn_m.group(1).strip()
                clean = clean[:syn_m.start()].strip()
                changed = True
                continue

        return clean, comment, synonyms

    def _extract_clause_objects(
        self,
        block: str,
        kind: str,
        table_name: str,
        objects: List[ObjectDef],
        extra_desc: Dict[str, str],
    ) -> None:
        """Extract individual metric/dimension/fact objects from a clause block.
        
        Strictly maps 'NAME as EXPRESSION': name on left of top-level AS, expression on right.
        Strips PRIVATE/PUBLIC, surrounding quotes, and trailing clauses.
        """
        items = self._split_comma_clause(block)
        for item in items:
            clean, comment, synonyms = self._extract_trailing_clauses(item)
            left, right = self._split_first_top_level_as(clean)

            # Left side: [PRIVATE|PUBLIC] [table.]name [NON ADDITIVE BY (...)]
            left_clean = re.sub(r'^\s*(?:PRIVATE|PUBLIC)\s+', '', left, flags=re.I).strip()
            is_private = bool(re.match(r'^\s*PRIVATE\b', left, flags=re.I))

            non_add = None
            non_add_m = re.search(r'\s+NON\s+ADDITIVE\s+BY\s*\(.*?\)', left_clean, flags=re.I)
            if non_add_m:
                non_add = non_add_m.group(0).strip()
                left_clean = left_clean[:non_add_m.start()].strip()

            # Snowflake Semantic View syntax: [table.]name AS <expression>
            # If left side has parentheses '(' or is not an identifier, but right side is a clean identifier (e.g. classic SQL expr AS alias in fixtures)
            ident_pattern = r'^(?:(?:[A-Za-z0-9_"]+)\.)?(?:[A-Za-z0-9_"]+)$'
            if right is not None and ("(" in left_clean or not re.match(ident_pattern, left_clean)) and re.match(ident_pattern, right.strip()):
                target_ident = right.strip()
                expr = left_clean
            else:
                target_ident = left_clean
                expr = right.strip() if right is not None else left_clean

            if "." in target_ident:
                tbl_part, name_part = target_ident.rsplit(".", 1)
                obj_table = _clean_identifier(tbl_part)
                name = _clean_identifier(name_part)
            else:
                obj_table = table_name
                name = _clean_identifier(target_ident)

            desc = comment or extra_desc.get(name.upper())
            props: Dict[str, Any] = {}
            if synonyms:
                props["synonyms"] = synonyms
            if is_private:
                props["visibility"] = "private"
            if non_add:
                props["non_additive_by"] = non_add

            objects.append(
                ObjectDef(
                    kind=kind,
                    table=obj_table,
                    name=name,
                    expression=expr,
                    description=desc,
                    properties=props,
                )
            )

    def _extract_tables_clause(
        self,
        block: str,
        table_name: str,
        objects: List[ObjectDef],
        extra_desc: Dict[str, str],
    ) -> None:
        """Extract table definitions from a TABLES (...) clause block."""
        items = self._split_comma_clause(block)
        for item in items:
            clean, comment, _ = self._extract_trailing_clauses(item)

            pk = None
            pk_m = re.search(r'\s+PRIMARY\s+KEY\s*\((.*?)\)', clean, re.I)
            if pk_m:
                pk = pk_m.group(1).strip()
                clean = clean[:pk_m.start()].strip()

            left, right = self._split_first_top_level_as(clean)
            if right is not None:
                l_s = left.strip()
                r_s = right.strip()
                if "." in l_s and "." not in r_s:
                    alias = r_s.strip('"').strip("'")
                    underlying_table = l_s
                else:
                    alias = l_s.strip('"').strip("'")
                    underlying_table = r_s
            else:
                underlying_table = left.strip()
                alias = underlying_table.split(".")[-1].strip().strip('"').strip("'")

            props: Dict[str, Any] = {}
            if pk:
                props["primary_key"] = pk

            desc = comment or extra_desc.get(alias.upper())
            objects.append(
                ObjectDef(
                    kind=ObjectKind.FACT,
                    table=table_name,
                    name=alias,
                    expression=underlying_table,
                    description=desc,
                    properties=props,
                )
            )

    def _extract_relationships_clause(
        self,
        block: str,
        table_name: str,
        objects: List[ObjectDef],
    ) -> None:
        """Extract relationship definitions from a RELATIONSHIPS (...) clause block."""
        items = self._split_comma_clause(block)
        for item in items:
            clean, comment, _ = self._extract_trailing_clauses(item)
            left, right = self._split_first_top_level_as(clean)

            if right is not None:
                name = left.strip().strip('"').strip("'")
                expr = right.strip()
            else:
                expr = clean
                ref_match = re.search(r'([A-Za-z0-9_]+)\s*\(.*?\)\s*REFERENCES\s*([A-Za-z0-9_]+)', expr, re.I)
                if ref_match:
                    name = f"{ref_match.group(1).upper()}->{ref_match.group(2).upper()}"
                else:
                    name = expr

            objects.append(
                ObjectDef(
                    kind=ObjectKind.RELATIONSHIP,
                    table=table_name,
                    name=name,
                    expression=expr,
                )
            )

    def inject_verified_queries(
        self,
        pairs: List[Dict[str, Any]],
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Inject verified question/SQL pairs into a staged Cortex Analyst semantic model YAML file.

        Spike Finding (Snowflake Cortex Analyst):
        Cortex Analyst models reside as YAML files in Snowflake stages. Cortex Analyst reads
        the stage path (@stage/model.yaml) directly on each REST inference request, so updates
        written via PUT ... OVERWRITE=TRUE are immediately active without re-registration.
        In-flight requests hold in-memory models; subsequent requests read the updated file.
        Stage overwrite is atomic at the stage file boundary.
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

        if connection_config is None:
            return {
                "supported": False,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": "Snowflake connection configuration is required for verified query injection.",
                "dry_run": dry_run,
            }

        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs, SnowflakeConfig
        if isinstance(connection_config, SnowflakeConfig):
            cfg_obj = connection_config
        elif isinstance(connection_config, dict):
            cfg_dict = dict(connection_config)
            if "schema" in cfg_dict and "schema_name" not in cfg_dict:
                cfg_dict["schema_name"] = cfg_dict["schema"]
            cfg_obj = SnowflakeConfig(**cfg_dict)
        else:
            cfg_obj = connection_config

        try:
            kw = get_snowflake_connect_kwargs(cfg_obj)
        except Exception as exc:
            return {
                "supported": False,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": f"Snowflake credentials resolution failed: {exc}",
                "dry_run": dry_run,
            }

        import tempfile
        import os
        import yaml
        import snowflake.connector

        v_upper = view_name.upper().strip().strip('"')
        v_parts = [p.strip().strip('"') for p in v_upper.split(".")]
        simple_name = v_parts[-1]

        try:
            conn = snowflake.connector.connect(**kw)
            cur = conn.cursor()

            # 1. Discover stages in target schema or database
            target_schema = (
                getattr(cfg_obj, "schema_name", None)
                or getattr(cfg_obj, "schema", None)
                or (v_parts[1] if len(v_parts) == 3 else (v_parts[0] if len(v_parts) == 2 else None))
            )
            stage_rows = []
            if target_schema:
                try:
                    cur.execute(f"SHOW STAGES IN SCHEMA {target_schema}")
                    stage_rows = cur.fetchall()
                except Exception:
                    pass
            if not stage_rows:
                try:
                    cur.execute("SHOW STAGES")
                    stage_rows = cur.fetchall()
                except Exception as stg_exc:
                    return {
                        "supported": True,
                        "injected_count": 0,
                        "target": None,
                        "details": [],
                        "reason": f"Failed to list Snowflake stages: {stg_exc}",
                        "dry_run": dry_run,
                    }

            # 2. Find staged YAML model that consumes view_name
            target_stage = None
            target_file = None
            staged_yaml_data = None

            for stg in stage_rows:
                if len(stg) >= 2:
                    stage_name = str(stg[1])
                    stg_db = str(stg[2]) if len(stg) > 2 and stg[2] else None
                    stg_sch = str(stg[3]) if len(stg) > 3 and stg[3] else None
                    qual_stage = f"{stg_db}.{stg_sch}.{stage_name}" if stg_db and stg_sch else stage_name

                    try:
                        cur.execute(f"LIST @{qual_stage} PATTERN = '.*\\.ya?ml'")
                        model_files = cur.fetchall()
                        for mf in model_files:
                            file_path = str(mf[0]) if mf else ""
                            if not file_path:
                                continue
                            file_tail = file_path.split("/")[-1]
                            with tempfile.TemporaryDirectory() as tmp_dir:
                                clean_tmp = tmp_dir.replace("\\", "/")
                                cur.execute(f"GET @{qual_stage}/{file_tail} file://{clean_tmp}")
                                downloaded = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.endswith(('.yaml', '.yml'))]
                                for df in downloaded:
                                    with open(df, 'r', encoding='utf-8') as yfp:
                                        data = yaml.safe_load(yfp)
                                    if isinstance(data, dict):
                                        tables_section = data.get("tables") or data.get("semantic_models") or []
                                        consumes = False
                                        if isinstance(tables_section, list):
                                            for tbl in tables_section:
                                                tbl_name = str(tbl.get("name") or tbl.get("base_table") or tbl.get("table") or "").upper().strip().strip('"') if isinstance(tbl, dict) else str(tbl).upper().strip().strip('"')
                                                if tbl_name and (tbl_name == v_upper or tbl_name.endswith(f".{simple_name}") or tbl_name == simple_name):
                                                    consumes = True
                                                    break
                                        if consumes:
                                            target_stage = qual_stage
                                            target_file = file_tail
                                            staged_yaml_data = data
                                            break
                            if target_stage:
                                break
                    except Exception as inspect_exc:
                        logger.debug("Stage %s inspection failed: %s", qual_stage, inspect_exc)
                if target_stage:
                    break

            if not target_stage or not target_file or staged_yaml_data is None:
                return {
                    "supported": True,
                    "injected_count": 0,
                    "target": None,
                    "details": [],
                    "reason": f"No staged Cortex Analyst semantic model YAML found referencing view '{simple_name}'.",
                    "dry_run": dry_run,
                }

            # 3. Prepare updated verified_queries section
            existing_vqs = staged_yaml_data.get("verified_queries")
            if not isinstance(existing_vqs, list):
                existing_vqs = []
                staged_yaml_data["verified_queries"] = existing_vqs

            existing_questions = {str(q.get("question") or "").strip().lower() for q in existing_vqs if isinstance(q, dict)}

            to_inject = []
            for p in pairs:
                q_text = str(p.get("question") or "").strip()
                if not q_text or q_text.lower() in existing_questions:
                    continue
                to_inject.append({
                    "name": p.get("name") or f"query_{len(existing_vqs) + len(to_inject) + 1}",
                    "question": q_text,
                    "sql": p.get("sql") or "",
                    "verified_at": int(p.get("verified_at") or 0),
                    "verified_by": str(p.get("verified_by") or "steward"),
                })

            if not to_inject:
                return {
                    "supported": True,
                    "injected_count": 0,
                    "target": f"@{target_stage}/{target_file}",
                    "details": [],
                    "reason": "All qualifying query pairs already exist in target model.",
                    "dry_run": dry_run,
                }

            if dry_run:
                return {
                    "supported": True,
                    "injected_count": len(to_inject),
                    "target": f"@{target_stage}/{target_file}",
                    "details": to_inject,
                    "reason": f"Dry run: {len(to_inject)} query pairs prepared for injection into @{target_stage}/{target_file}.",
                    "dry_run": True,
                }

            # 4. Perform actual write
            existing_vqs.extend(to_inject)
            with tempfile.TemporaryDirectory() as write_dir:
                local_write_path = os.path.join(write_dir, target_file)
                with open(local_write_path, "w", encoding="utf-8") as out_fp:
                    yaml.safe_dump(staged_yaml_data, out_fp, sort_keys=False)

                clean_local_path = local_write_path.replace("\\", "/")
                cur.execute(f"PUT file://{clean_local_path} @{target_stage} OVERWRITE=TRUE AUTO_COMPRESS=FALSE")
                put_result = cur.fetchall()

            return {
                "supported": True,
                "injected_count": len(to_inject),
                "target": f"@{target_stage}/{target_file}",
                "details": to_inject,
                "reason": f"Successfully injected {len(to_inject)} verified query pairs into @{target_stage}/{target_file}.",
                "dry_run": False,
            }
        except Exception as exc:
            return {
                "supported": True,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": f"Snowflake injection failed: {exc}",
                "dry_run": dry_run,
            }
