from __future__ import annotations

import re
import time
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    import snowflake.connector.cursor
    from semabridge.converter.measure_triage import TriageResult
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior, SnowflakeBehavior
from semabridge.core.exceptions import ConnectorError
from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLMetric, SMLDimension, SMLRelationship, AggregationType, DataType
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.snowflake_emitter_parts.exceptions import MissingSourceTableWarning
from semabridge.connectors.business_field_type_hints import infer_business_field_type
from semabridge.connectors.table_binding_resolver import resolve_dataset_to_physical_table

if TYPE_CHECKING:
    from semabridge.intermediate.models import (
        OSIModel,
        OSIDataset,
        OSIMetric,
        OSIDimension,
        OSIAttribute,
        OSIColumn,
        OSIDataType
    )
else:
    try:
        from semabridge.intermediate.models import (
            OSIModel,
            OSIDataset,
            OSIMetric,
            OSIDimension,
            OSIAttribute,
            OSIColumn,
            OSIDataType,
        )
    except ImportError:
        OSIModel: TypeAlias = Any
        OSIDataset: TypeAlias = Any
        OSIMetric: TypeAlias = Any
        OSIDimension: TypeAlias = Any
        OSIAttribute: TypeAlias = Any
        OSIColumn: TypeAlias = Any
        OSIDataType = None

from semabridge.utils.logger import get_logger

import threading

_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
_SCHEMA_CACHE_LOCK = threading.Lock()


def _schema_cache_key(database: str, schema: str) -> str:
    return f"{(database or '').upper()}::{(schema or '').upper()}"


def clear_schema_cache() -> None:
    with _SCHEMA_CACHE_LOCK:
        _SCHEMA_CACHE.clear()
logger = get_logger(__name__)

class SnowflakeSchemaManager:
    def __init__(
        self,
        config: SnowflakeConfig,
        behavior: ConnectorBehavior,
        identifier_sanitizer: IdentifierSanitizer,
        connection_manager: Any,
        dup_name_repo: Any = None,
        
    ):
        self.config = config
        self.behavior = behavior
        self.sf_behavior = behavior.snowflake
        self._id = identifier_sanitizer
        self.connection_manager = connection_manager
        self._dup_name_repo = dup_name_repo
        self._verified_tables: set[str] = set()  # Temporary compatibility during phase 2 refactor
        # Columns added via ALTER TABLE ADD COLUMN by THIS deploy's own
        # _ensure_source_tables_exist call, keyed by safe_table_name -> set
        # of safe column names. Fresh every deploy (SchemaManager itself is
        # constructed fresh per deploy -- see SnowflakeEmitter.__init__ --
        # so no explicit reset is needed here). Consumed by
        # _maybe_backfill_source_data/_backfill_one_table to distinguish
        # "this column is null because it was just added this run, and its
        # sibling columns' real data proves the table itself is populated"
        # from "the whole table is empty/garbage" -- see that code for why
        # the table-level real_data/empty/ambiguous check alone can't tell
        # the two apart.
        self._newly_added_columns: Dict[str, set[str]] = {}
        from semabridge.connectors.table_binding_resolver import LiveSchemaMetadata
        self._live_schema_metadata: LiveSchemaMetadata = LiveSchemaMetadata()
        self.duplicate_placeholders: Dict[str, List[str]] = {}
        self._schema_cache_ttl_seconds = int(os.getenv("SEMABRIDGE_SCHEMA_CACHE_TTL_SECONDS", "300"))

    def _get_cached_schema_metadata(self) -> Optional[Dict[str, set[str]]]:
        key = _schema_cache_key(self.config.database, self.config.schema_name)
        with _SCHEMA_CACHE_LOCK:
            entry = _SCHEMA_CACHE.get(key)
            if not entry:
                return None
            loaded_at = entry.get("loaded_at") or 0
            if self._schema_cache_ttl_seconds > 0:
                age = time.time() - float(loaded_at)
                if age > self._schema_cache_ttl_seconds:
                    return None
            metadata = dict(entry.get("metadata") or {})
        if metadata:
            self._live_schema_metadata.update(metadata)
        return metadata

    def refresh_schema_cache(self) -> None:
        key = _schema_cache_key(self.config.database, self.config.schema_name)
        with _SCHEMA_CACHE_LOCK:
            if key in _SCHEMA_CACHE:
                del _SCHEMA_CACHE[key]

    def _execute_sql(self, cursor, sql: str, params: Any = None, context: str = "", expected_failure: bool = False) -> Any:
        kwargs: Dict[str, Any] = {"context": context}
        if expected_failure:
            kwargs["expected_failure"] = True
        if params is None:
            return self.connection_manager._execute_sql(cursor, sql, **kwargs)
        return self.connection_manager._execute_sql(cursor, sql, params, **kwargs)

    def _build_fixed_table_name(self, safe_table_name: str) -> str:
        """Create a collision-resistant temp table name for CTAS+SWAP."""
        suffix = f"{int(time.time() * 1000)}_{os.getpid()}"
        candidate = f"{safe_table_name}__FIXED_{suffix}"
        # Snowflake identifier max length is 255 characters.
        return candidate[:255]

    def _build_staging_table_name(self, safe_table_name: str) -> str:
        """Create a collision-resistant TEMPORARY table name for a
        column-level backfill's staging data (see
        _backfill_columns_for_existing_rows)."""
        suffix = f"{int(time.time() * 1000)}_{os.getpid()}"
        candidate = f"{safe_table_name}__BACKFILL_STAGE_{suffix}"
        return candidate[:255]

    def _safe_table_name(self, name: str) -> str:
        return self._id.sanitize_table_name(name)

    @staticmethod
    def _quote_ident(value: str) -> str:
        """Quote a Snowflake identifier safely."""
        raw = str(value or "").replace('"', '""')
        return f'"{raw}"'

    def _data_schema_name(self) -> str:
        return getattr(self.config, "source_data_schema", None) or self.config.schema_name

    def _schema_fqn(self, schema: Optional[str] = None) -> str:
        """Return fully-qualified schema name as "DB"."SCHEMA"."""
        db_name = str(self.config.database or "").strip()
        schema_name = str(schema or self.config.schema_name or "").strip()
        # Accept accidental DB.SCHEMA input in schema_name without producing
        # malformed "DB"."DB.SCHEMA" references.
        if "." in schema_name and db_name:
            left, right = schema_name.split(".", 1)
            if left.strip().upper() == db_name.upper():
                schema_name = right.strip()
        return f"{self._quote_ident(db_name)}.{self._quote_ident(schema_name)}"

    def _sanitize_col_name(self, name: str) -> str:
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _sanitize_alias(self, name: str) -> str:
        sanitized = self._id.sanitize_alias(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _get_safe_object_name(self, name: str) -> str:
        return self._sanitize_col_name(name)

    @staticmethod
    def _is_physical_source_column(source_expression: str) -> bool:
        return IdentifierSanitizer.is_physical_source_column(source_expression)

    def _build_duplicate_signature_seed(
        self,
        source_name: str,
        source_expression: Optional[str],
        data_type: Optional[str],
        aggregation: Optional[str] = None
    ) -> str:
        return "|".join([source_name or "", source_expression or "", data_type or "", aggregation or ""])

    def _duplicate_namespace_key(self, model_name: Optional[str] = None) -> str:
        parts = [
            self._id.sanitize_alias(self.config.database or "DB"),
            self._id.sanitize_alias(self.config.schema_name or "SCHEMA"),
        ]
        if model_name:
            parts.append(self._id.sanitize_alias(model_name))
        return ".".join(parts)

    def _resolve_persistent_duplicate_name(
        self,
        scope_type: str,
        namespace_key: str,
        dataset_key: str,
        normalized_base: str,
        source_name: str,
        source_signature: str,
        preferred_name: str
    ) -> str:
        if not self._dup_name_repo:
            return preferred_name
        try:
            return self._dup_name_repo.get_or_create_assigned_name(
                scope_type=scope_type,
                namespace_key=namespace_key,
                dataset_key=dataset_key,
                normalized_base=normalized_base,
                source_name=source_name,
                source_signature=source_signature,
                preferred_name=preferred_name,
            )
        except Exception as exc:
            logger.warning("Duplicate mapping failed for %s: %s", source_name, exc)
            return preferred_name

    def _ensure_schema_exists(self, cursor) -> None:
        """Stop with a clear error when the project's target schema doesn't exist.

        SemaBridge never creates schemas (the SQL guard only permits its own
        views and semantic views), so a missing schema is a setup problem
        the user fixes in Snowflake; checking up front reports it plainly
        instead of failing later on the first statement that touches it.
        """
        db = str(self.config.database or "").strip()
        schema = str(self.config.schema_name or "").strip().rsplit(".", 1)[-1]
        like = schema.upper().replace("'", "''")
        try:
            self._execute_sql(
                cursor,
                f"SHOW SCHEMAS LIKE '{like}' IN DATABASE \"{db.replace(chr(34), chr(34) * 2)}\"",
                context="CHECK SCHEMA EXISTS",
            )
            found = any(str(row[1]).upper() == schema.upper() for row in cursor.fetchall() or [])
        except Exception as exc:
            raise ConnectorError(
                f"Could not check that target schema {self._schema_fqn()} exists: {exc}"
            ) from exc
        if not found:
            raise ConnectorError(
                f"Target schema {self._schema_fqn()} does not exist or this role can't see it. "
                f"Create it in Snowflake (or pick an existing schema for the project) and re-run."
            )

    def _ensure_source_data_schema_exists(self, cursor) -> None:
        """Stop with a clear error when the project's source data schema doesn't exist.
        SemaBridge never creates schemas.
        """
        source_data_schema = getattr(self.config, "source_data_schema", None)
        if not source_data_schema:
            return
        db = str(self.config.database or "").strip()
        schema = str(source_data_schema).strip().rsplit(".", 1)[-1]
        like = schema.upper().replace("'", "''")
        try:
            self._execute_sql(
                cursor,
                f"SHOW SCHEMAS LIKE '{like}' IN DATABASE \"{db.replace(chr(34), chr(34) * 2)}\"",
                context="CHECK SOURCE DATA SCHEMA EXISTS",
            )
            found = any(str(row[1]).upper() == schema.upper() for row in cursor.fetchall() or [])
        except Exception as exc:
            raise ConnectorError(
                f"Could not check that source-data schema '{source_data_schema}' exists in database '{db}': {exc}. SemaBridge never creates schemas."
            ) from exc
        if not found:
            raise ConnectorError(
                f"Source-data schema '{source_data_schema}' does not exist in database '{db}'. "
                f"SemaBridge never creates schemas. The schema must already exist."
            )

    def _load_row_counts(self, cursor) -> None:
        """Row count of every base table in the schema, for binding tie-breaks."""
        counts = getattr(self._live_schema_metadata, "row_counts", None)
        if counts is None:
            return
        db = str(self.config.database).replace('"', '""')
        schema = str(self.config.schema_name).upper().replace("'", "''")
        try:
            self._execute_sql(
                cursor,
                f'SELECT table_name, row_count FROM "{db}".information_schema.tables '
                f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE'",
                context="FETCH TABLE ROW COUNTS",
            )
            for table, row_count in cursor.fetchall() or []:
                counts[str(table).upper()] = int(row_count or 0)
        except Exception as exc:
            # Ties then fall back to name order; saved bindings still apply.
            logger.warning("Could not fetch table row counts for binding tie-breaks: %s", exc)

    def plan_source_tables(self, cursor, sml: SMLModel) -> List[Dict[str, Any]]:
        """Decide, per dataset, which existing table it binds to or what to create.

        Looks at ALL tables and views in the target schema (fresh metadata,
        never a partial or cached view). A dataset binds to an existing table
        whose normalized name matches or whose columns match (>= 0.8), or to
        the table saved for it by an earlier sync; an empty table is planned
        for creation only when nothing matches. Read-only.

        Each entry: {dataset, action: "bind"|"create"|"blocked", table, matched_by, tie}.
        """
        from semabridge.connectors.table_binding_resolver import (
            clean_ident,
            compute_column_compatibility,
            rank_table_candidates,
            resolve_dataset_to_physical_table,
            check_table_name_collision_blocker,
            TableBindingAmbiguityError,
        )

        self._ensure_source_data_schema_exists(cursor)
        target_schema = self._data_schema_name()

        self._execute_sql(cursor, f"SHOW TABLES IN SCHEMA {self._schema_fqn(target_schema)}", context="SHOW TABLES")
        existing_tables = {row[1].upper() for row in cursor.fetchall()}
        self._execute_sql(cursor, f"SHOW VIEWS IN SCHEMA {self._schema_fqn(target_schema)}", context="SHOW VIEWS")
        all_existing = existing_tables | {row[1].upper() for row in cursor.fetchall()}

        self.refresh_schema_cache()
        self._fetch_schema_metadata(cursor)

        mapping_dict = getattr(getattr(getattr(self, "behavior", None), "snowflake", None), "source_table_mapping", {}) or {}
        binding_log = getattr(self._live_schema_metadata, "binding_log", None)
        model_name = getattr(sml, "unique_name", None) or getattr(sml, "name", None)

        plan: List[Dict[str, Any]] = []
        blockers: List[Dict[str, Any]] = []
        for dataset in sml.datasets:
            ds_name = dataset.unique_name
            modeled_cols = [c.unique_name for c in getattr(dataset, "columns", []) or []]
            try:
                bound_table = resolve_dataset_to_physical_table(
                    ds_name,
                    modeled_cols,
                    self._live_schema_metadata,
                    explicit_mapping=mapping_dict,
                    model_name=model_name,
                )
            except TableBindingAmbiguityError as err:
                ambiguity_blocker = {
                    "type": "ambiguous_table_binding",
                    "dataset_name": ds_name,
                    "message": str(err),
                    "headline": f"Ambiguous table binding for '{ds_name}'.",
                    "candidates": err.candidates,
                    "resolutions": [
                        {
                            "type": "map_table",
                            "description": (
                                "Map to one of the candidate tables; "
                                "the choice is saved as the project's binding."
                            ),
                            "candidates": err.candidates,
                        }
                    ],
                }
                blockers.append(ambiguity_blocker)
                plan.append({
                    "dataset": ds_name,
                    "action": "blocked",
                    "table": err.candidates[0] if err.candidates else ds_name,
                    "matched_by": "ambiguity",
                    "blocker": ambiguity_blocker,
                    "tie": err.candidates,
                })
                continue

            logged = (binding_log or {}).get(clean_ident(ds_name)) or {}
            safe_target_name = self._safe_table_name(ds_name)

            collision = check_table_name_collision_blocker(ds_name, modeled_cols, self._live_schema_metadata)
            explicit_bound = False
            if collision:
                clean_tbl = clean_ident(collision["table_name"])
                if mapping_dict:
                    for k, v in mapping_dict.items():
                        if clean_ident(k) == clean_ident(ds_name) and clean_ident(v) != clean_tbl:
                            explicit_bound = True
                pinned = (getattr(self._live_schema_metadata, "pinned_bindings", None) or {}).get(clean_ident(ds_name))
                if pinned and clean_ident(pinned) != clean_tbl:
                    explicit_bound = True

            if collision and not explicit_bound:
                blockers.append(collision)
                plan.append({
                    "dataset": ds_name,
                    "action": "blocked",
                    "table": collision["table_name"],
                    "matched_by": "collision",
                    "blocker": collision,
                    "tie": [],
                })
            elif bound_table.upper() in all_existing:
                bound_cols = self._live_schema_metadata.get(bound_table.upper(), set())
                compat = compute_column_compatibility(modeled_cols, bound_cols)
                live_compat = compute_column_compatibility(list(bound_cols), set(modeled_cols))
                if compat < 0.8 and not (live_compat >= 0.8 and compat >= 0.5) and bound_table.upper() not in [k.upper() for k in mapping_dict.values()]:
                    collision_data = {
                        "type": "table_column_mismatch",
                        "dataset_name": ds_name,
                        "table_name": bound_table,
                        "col_compat": compat,
                        "message": (
                            f"{bound_table} already exists with different columns. "
                            f"Target table '{bound_table}' already exists in Snowflake but its columns do not match "
                            f"PBIX table '{ds_name}' (compatibility {compat:.1%} < 80%)."
                        ),
                        "headline": f"{bound_table} already exists with different columns.",
                        "candidates": [
                            c["table"]
                            for c in rank_table_candidates(ds_name, modeled_cols, self._live_schema_metadata)
                            if c["col_compat"] >= 0.8
                        ],
                        "resolutions": [
                            {
                                "type": "map_table",
                                "description": (
                                    "Map to another existing table (choose from compatible candidates); "
                                    "the choice is saved as the project's binding."
                                ),
                                "candidates": [
                                    c["table"]
                                    for c in rank_table_candidates(ds_name, modeled_cols, self._live_schema_metadata)
                                    if c["col_compat"] >= 0.8
                                ],
                            },
                            {
                                "type": "use_schema",
                                "description": (
                                    "Use a different schema for this model's data (the project's source-data schema setting). "
                                    "The schema must already exist; SemaBridge never creates schemas."
                                ),
                            },
                        ],
                    }
                    blockers.append(collision_data)
                    plan.append({
                        "dataset": ds_name,
                        "action": "blocked",
                        "table": bound_table,
                        "matched_by": "collision",
                        "blocker": collision_data,
                        "tie": [],
                    })
                else:
                    plan.append({
                        "dataset": ds_name,
                        "action": "bind",
                        "table": bound_table,
                        "matched_by": logged.get("matched_by") or "mapping",
                        "tie": logged.get("tie") or [],
                    })
            elif safe_target_name.upper() in all_existing:
                target_cols = self._live_schema_metadata.get(safe_target_name.upper(), set())
                compat = compute_column_compatibility(modeled_cols, target_cols)
                live_compat = compute_column_compatibility(list(target_cols), set(modeled_cols))
                if compat < 0.8 and not (live_compat >= 0.8 and compat >= 0.5):
                    collision_data = {
                        "type": "table_column_mismatch",
                        "dataset_name": ds_name,
                        "table_name": safe_target_name,
                        "col_compat": compat,
                        "message": (
                            f"{safe_target_name} already exists with different columns. "
                            f"Target table '{safe_target_name}' already exists in Snowflake but its columns do not match "
                            f"PBIX table '{ds_name}' (compatibility {compat:.1%} < 80%)."
                        ),
                        "headline": f"{safe_target_name} already exists with different columns.",
                        "candidates": [
                            c["table"]
                            for c in rank_table_candidates(ds_name, modeled_cols, self._live_schema_metadata)
                            if c["col_compat"] >= 0.8
                        ],
                        "resolutions": [
                            {
                                "type": "map_table",
                                "description": (
                                    "Map to another existing table (choose from compatible candidates); "
                                    "the choice is saved as the project's binding."
                                ),
                                "candidates": [
                                    c["table"]
                                    for c in rank_table_candidates(ds_name, modeled_cols, self._live_schema_metadata)
                                    if c["col_compat"] >= 0.8
                                ],
                            },
                            {
                                "type": "use_schema",
                                "description": (
                                    "Use a different schema for this model's data (the project's source-data schema setting). "
                                    "The schema must already exist; SemaBridge never creates schemas."
                                ),
                            },
                        ],
                    }
                    blockers.append(collision_data)
                    plan.append({
                        "dataset": ds_name,
                        "action": "blocked",
                        "table": safe_target_name,
                        "matched_by": "collision",
                        "blocker": collision_data,
                        "tie": [],
                    })
                else:
                    plan.append({
                        "dataset": ds_name,
                        "action": "bind",
                        "table": safe_target_name,
                        "matched_by": "name",
                        "tie": [],
                    })
            else:
                # Never create a placeholder if an existing table matches by normalized name or compatible columns
                norm_ds = clean_ident(ds_name)
                matching_norm_tbl = next((t for t in all_existing if clean_ident(t) == norm_ds), None)
                compatible_tbl = None
                if not matching_norm_tbl:
                    for t in all_existing:
                        t_cols = self._live_schema_metadata.get(t.upper(), set())
                        if compute_column_compatibility(modeled_cols, t_cols) >= 0.8:
                            compatible_tbl = t
                            break

                candidate_existing = matching_norm_tbl or compatible_tbl
                if candidate_existing:
                    plan.append({
                        "dataset": ds_name,
                        "action": "bind",
                        "table": candidate_existing,
                        "matched_by": "normalized name" if matching_norm_tbl else "columns",
                        "tie": [],
                    })
                else:
                    plan.append({
                        "dataset": ds_name,
                        "action": "create",
                        "table": safe_target_name,
                        "matched_by": None,
                        "tie": [],
                    })
        self.last_blockers = blockers
        return plan

    def _ensure_source_tables_exist(self, cursor, sml: SMLModel) -> None:
        """
        Create missing source tables EMPTY (CREATE TABLE IF NOT EXISTS, no data)
        — only for datasets that match no existing table at all (see
        plan_source_tables). Existing tables are NEVER touched (no ALTER, DROP,
        INSERT, CTAS, REPLACE, SWAP) and never duplicated.
        """
        from semabridge.connectors.sql_guard import (
            set_allowed_absent_tables,
            get_allowed_absent_tables,
        )

        if all(self._safe_table_name(d.unique_name) in self._verified_tables for d in sml.datasets):
            logger.debug("All source tables already verified — skipping SHOW TABLES")
            return

        absent_tables_to_create = []
        datasets_by_name = {d.unique_name: d for d in sml.datasets}
        for entry in self.plan_source_tables(cursor, sml):
            if entry["action"] == "blocked":
                blocker = entry.get("blocker") or {}
                msg = blocker.get("message") or f"Table '{entry.get('table')}' has collision blocker"
                raise ConnectorError(
                    f"Deployment blocked: cannot sync while blockers are open: {msg}"
                )
            elif entry["action"] == "bind":
                if entry["matched_by"] == "name":
                    logger.warning(
                        "Dataset '%s' bound to existing table '%s' by name only (columns differ); not creating a duplicate.",
                        entry["dataset"], entry["table"],
                    )
                else:
                    logger.debug("Dataset '%s' is bound to existing table '%s' — leaving untouched.", entry["dataset"], entry["table"])
                self._verified_tables.add(self._safe_table_name(entry["table"]))
            else:
                absent_tables_to_create.append((datasets_by_name[entry["dataset"]], entry["table"]))

        if not absent_tables_to_create:
            return

        # Register confirmed absent tables in sql_guard
        allowed_names = {tbl.upper() for _, tbl in absent_tables_to_create}
        set_allowed_absent_tables(get_allowed_absent_tables() | allowed_names)

        for dataset, safe_table_name in absent_tables_to_create:
            # Generate empty table DDL with PBIX columns and types (no data, no CTAS)
            create_ddl = self._generate_create_table_ddl(dataset, safe_table_name)
            try:
                self._execute_sql(cursor, create_ddl, context=f"CREATE TABLE {safe_table_name}")
                logger.info(f"Created empty missing source table: {safe_table_name}")
                self._verified_tables.add(safe_table_name)
            except Exception as e:
                logger.error(f"Failed to create missing source table '{safe_table_name}': {e}")
                raise ConnectorError(f"Cannot create missing source table '{safe_table_name}': {e}") from e
    
    def _verify_table_columns(self, cursor, table_name: str, dataset: SMLDataset) -> tuple:
        """
        Verify that a table's columns match the expected SML columns.
        
        Returns:
            tuple: (missing_columns: set, extra_columns: set)
                - missing_columns: Set of column names that are expected in SML but missing in Snowflake
                - extra_columns: Set of column names that exist in Snowflake but not in SML
        """
        try:
            self._execute_sql(cursor, f'DESC TABLE {self.config.schema_name}."{table_name}"', context=f"DESC TABLE {table_name}")
            existing_cols = {row[0] for row in cursor.fetchall()}
            
            # Check if model expects columns that don't exist
            expected_cols = set(self._collect_physical_source_columns(dataset).keys())
            
            missing = expected_cols - existing_cols
            if missing:
                logger.debug(f"Table {table_name} missing columns: {missing}")
            
            internal_cols = {'METADATA$ROW_ID', 'METADATA$IS_DELETED', 'METADATA$FILE_NAME', 
                           'METADATA$START_SCAN_TIME', 'METADATA$ACTION', 'METADATA$ROW_VERSION'}
            extra_cols = existing_cols - expected_cols - internal_cols
            
            return (missing, extra_cols)
        except Exception as e:
            logger.warning(f"Could not verify table {table_name}: {e}")
            # If table doesn't exist or error occurs, return all columns as missing
            all_expected = set(self._collect_physical_source_columns(dataset).keys())
            return (all_expected, set())

    def _drop_extra_columns(self, cursor, table_name: str, extra_columns: set,
                            dataset: Optional[SMLDataset] = None) -> None:
        """
        Drop extra columns from a table that are not in the SML model.
        
        When the DDL strategy is 'idempotent' (Mandate 2) and the guard
        triggers (would drop all columns), automatically recreates the
        table using CREATE OR REPLACE instead of silently returning.
        
        Args:
            cursor: Snowflake cursor
            table_name: Name of the table
            extra_columns: Set of column names to drop (uppercase)
            dataset: Optional SMLDataset for full recreation when needed
        """
        if not extra_columns:
            return
        
        # Guard: Snowflake forbids dropping ALL columns from a table.
        try:
            self._execute_sql(cursor, f'DESC TABLE {self.config.schema_name}."{table_name}"', context=f"DESC TABLE {table_name}")
            total_cols = {row[0] for row in cursor.fetchall()}
            if extra_columns >= total_cols:
                message = (
                    f"Refusing to drop all columns from existing table {table_name}; "
                    "full table recreation is disabled to preserve user data."
                )
                logger.warning(message)
                raise ConnectorError(message)
        except Exception as e:
            if isinstance(e, ConnectorError):
                raise
            logger.warning(f"Could not check column count for {table_name}: {e}")
        
        for col_name in extra_columns:
            try:
                ddl = f'ALTER TABLE {self.config.schema_name}."{table_name}" DROP COLUMN "{col_name}"'
                logger.info(f"Dropping extra column: {table_name}.{col_name}")
                self._execute_sql(cursor, ddl, context=f"DROP COLUMN {table_name}.{col_name}")
                logger.info(f"Successfully dropped column: {col_name}")
            except Exception as e:
                logger.warning(f"Could not drop column {col_name} from {table_name}: {e}")

    def _generate_date_dim_ddl(self, table_name: str) -> str:
        """
        Generate DDL for a Date Dimension using Snowflake Generator.
        Creates 20 years of data (10 past, 10 future).
        """
        safe_table = self._safe_table_name(table_name)
        schema = self.config.schema_name
        
        return f"""
        CREATE TABLE IF NOT EXISTS {schema}."{safe_table}" AS
        SELECT
          DATEADD(DAY, SEQ4(), DATEADD(YEAR, -10, CURRENT_DATE())) AS DATE,
          YEAR(DATE) AS YEAR,
          QUARTER(DATE) AS QUARTER,
          MONTH(DATE) AS MONTH,
          MONTHNAME(DATE) AS MONTHNAME,
          DAYOFWEEK(DATE) AS DAYOFWEEK,
          DAYNAME(DATE) AS DAYNAME
        FROM TABLE(GENERATOR(ROWCOUNT => 7300));
        """

    def _generate_create_table_ddl(self, dataset: SMLDataset, table_name: str) -> str:
        """Generate CREATE TABLE DDL from SML dataset definition."""
        # Data type mapping from SML/TMSL to Snowflake
        type_map = {
            "STRING": "VARCHAR(500)",
            "INTEGER": "INTEGER",
            "FLOAT": "FLOAT",
            "DECIMAL": "DECIMAL(18,2)",
            "BOOLEAN": "BOOLEAN",
            "DATETIME": "TIMESTAMP_NTZ",
            "DATE": "DATE",
            "BINARY": "BINARY"
        }
        
        col_defs = []
        for safe_name, col in self._collect_physical_source_columns(dataset).items():
            sf_type = type_map.get(col.data_type.value, "VARCHAR(500)")
            col_defs.append(f'    "{safe_name}" {sf_type}')
        
        if not col_defs:
            # Fallback: create with a single ID column
            col_defs.append("    ID INTEGER")
        
        safe_table = self._safe_table_name(table_name)
        quoted_table = f'"{safe_table}"'
        schema_name = self._data_schema_name()
        ddl = f"CREATE TABLE IF NOT EXISTS {schema_name}.{quoted_table} (\n"
        ddl += ",\n".join(col_defs)
        ddl += "\n) COMMENT = 'SemaBridge placeholder';"
        
        return ddl

    def _generate_sample_insert(self, dataset: SMLDataset, table_name: str) -> Optional[str]:
        """Generate INSERT statement with sample data for testing."""
        physical_cols = self._collect_physical_source_columns(dataset)
        if not physical_cols:
            return None

        columns = list(physical_cols.values())
        col_names = [f'"{col_name}"' for col_name in physical_cols.keys()]
        
        # Generate sample values based on data types
        sample_values = []
        for col in columns:
            dtype = col.data_type.value
            if dtype in ("INTEGER", "FLOAT", "DECIMAL"):
                sample_values.append("1")
            elif dtype == "BOOLEAN":
                sample_values.append("TRUE")
            elif dtype in ("DATETIME", "DATE"):
                sample_values.append("CURRENT_DATE()")
            else:
                sample_values.append(f"'Sample_{col.unique_name[:20]}'")
        
        safe_table = self._safe_table_name(table_name)
        cols_str = ", ".join(col_names)
        vals_str = ", ".join(sample_values)
        insert = f'INSERT INTO {self.config.schema_name}."{safe_table}" ({cols_str})\n'
        insert += f"SELECT {vals_str}\n"
        insert += f'WHERE NOT EXISTS (SELECT 1 FROM {self.config.schema_name}."{safe_table}" LIMIT 1);'
        
        return insert

    def _collect_physical_source_columns(self, dataset: SMLDataset) -> dict[str, Any]:
        """Return ordered map of physical Snowflake column name -> SML column.

        All valid semantic columns are preserved. When multiple semantic columns
        normalize to the same Snowflake identifier, deterministic suffixes are
        appended (``_1``, ``_2``, ...) so deployment remains lossless.
        """
        valid_columns: list[Any] = []
        base_totals: dict[str, int] = {}
        for col in dataset.columns:
            col_name = col.unique_name

            if col_name.startswith("RowNumber") or col_name.startswith("_"):
                continue

            source_expr = getattr(col, 'source_expression', None)
            if source_expr and not self._is_physical_source_column(source_expr):
                logger.debug(
                    "Skipping calculated column '%s' from physical source columns",
                    col_name,
                )
                continue

            valid_columns.append(col)
            safe_base = self._sanitize_col_name(col_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        selected: dict[str, Any] = {}
        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self._sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self._sanitize_col_name(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) > 1:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                preferred_name = f"{safe_base}_{next_idx}"
                safe_name = self._resolve_persistent_duplicate_name(
                    scope_type="column",
                    namespace_key=namespace_key,
                    dataset_key=dataset_key,
                    normalized_base=safe_base,
                    source_name=col.unique_name,
                    source_signature=source_signature,
                    preferred_name=preferred_name,
                )
            else:
                safe_name = safe_base

            while safe_name in selected:
                next_idx += 1
                base_seen[safe_base] = next_idx
                safe_name = f"{safe_base}_{next_idx}"

            if base_totals.get(safe_base, 0) > 1:
                logger.warning(
                    "Resolved physical column collision on table '%s': '%s' normalized to '%s'; using '%s'",
                    dataset.source_table or dataset.unique_name,
                    col.unique_name,
                    safe_base,
                    safe_name,
                )

            selected[safe_name] = col

        return selected

    def _resolve_column_via_relationship(self, model: Any, dataset: Any, raw_col_name: str) -> Optional[str]:
        """Never borrow a partner's column name across relationships.
        Primary keys and column names are resolved from the table's own schema only.
        """
        return None

    def _resolve_source_column_name(self, dataset: SMLDataset, raw_col_name: str, model: Any = None) -> str:
        """Resolve column name specifically to the physical source column name as it exists on the raw database table.

        Distinct from `_resolve_physical_column_name` (which resolves target/mapped column names
        for DDL and metric building): this method returns the column name needed to execute raw SQL queries
        against physical database tables (e.g. `SELECT MAX(...) FROM <physical_table>`).
        """
        if not dataset:
            return self._sanitize_col_name(raw_col_name)

        raw_upper = str(raw_col_name).upper()
        table_name_upper = (getattr(dataset, "source_table", None) or dataset.unique_name).upper()

        # 1. Check live schema metadata for physical table exact/case-insensitive match
        live_cols = self._live_schema_metadata.get(table_name_upper, set())
        if live_cols:
            if raw_upper in live_cols:
                return raw_upper
            live_cols_upper = {c.upper(): c for c in live_cols}
            if raw_upper in live_cols_upper:
                return live_cols_upper[raw_upper]

        # 2. Check if dataset columns carry an explicit source_column
        cols = getattr(dataset, "columns", []) or []
        for c in cols:
            scol = getattr(c, "source_column", None)
            uname = getattr(c, "unique_name", None) or ""
            if uname.casefold() == str(raw_col_name).casefold() and scol:
                return scol

        # 3. Default to clean sanitized raw_col_name (without date_patterns / mapping alias overrides)
        if raw_col_name:
            return self._sanitize_col_name(raw_col_name)

        return self._resolve_physical_column_name(dataset, raw_col_name, model=model)

    def _resolve_physical_column_name(self, dataset: SMLDataset, raw_col_name: str, model: Any = None) -> str:
        """Resolve semantic/raw column name to canonical physical column name dynamically."""
        raw_upper = str(raw_col_name).upper()
        
        # Resolve table name via generic table binding resolver
        mapping_dict = getattr(getattr(getattr(self, "behavior", None), "snowflake", None), "source_table_mapping", {}) or {}
        modeled_cols_list = [c.unique_name for c in getattr(dataset, "columns", []) or []]
        resolved_src = resolve_dataset_to_physical_table(
            dataset.unique_name,
            modeled_cols_list,
            self._live_schema_metadata,
            explicit_mapping=mapping_dict,
            model_name=getattr(model, "unique_name", None) or getattr(model, "name", None),
        )
        unq_src = resolved_src.rsplit(".", 1)[-1] if "." in resolved_src else resolved_src
        table_name_upper = unq_src.upper()

        # Get live columns if available, otherwise fall back to dataset's SML/OSI columns
        live_cols = (
            self._live_schema_metadata.get(table_name_upper)
            or self._live_schema_metadata.get(dataset.unique_name.upper())
            or set()
        )
        has_confirmed_live_schema = bool(live_cols)
        if not live_cols:
            # Fallback to SML/OSI columns defined in the model
            try:
                live_cols = set(self._collect_physical_source_columns(dataset).keys())
            except Exception:
                live_cols = set()

        # 1. Exact match
        if raw_upper in live_cols:
            return raw_upper

        # 2. Case-insensitive lookup in live columns
        live_cols_upper = {c.upper(): c for c in live_cols}
        if raw_upper in live_cols_upper:
            return live_cols_upper[raw_upper]

        # 3. Dynamic date column resolution from model metadata (no fixed name list)
        if raw_upper in ("DATE", "COL_DATE", "DATE_DATE"):
            date_col_candidates = []
            for c in getattr(dataset, "columns", []) or []:
                c_type = str(getattr(c, "data_type", "") or getattr(c, "source_type", "")).upper()
                if any(dt in c_type for dt in ("DATE", "TIME", "TIMESTAMP")):
                    scol = getattr(c, "source_column", None)
                    if scol:
                        date_col_candidates.append(scol)
                    date_col_candidates.append(c.unique_name)
            if model:
                try:
                    from semabridge.connectors.dataset_classification_keywords import find_model_date_dataset
                    date_ds = find_model_date_dataset(model)
                    if date_ds:
                        if getattr(date_ds, "unique_name", None) == dataset.unique_name:
                            for c in getattr(date_ds, "columns", []) or []:
                                c_type = str(getattr(c, "data_type", "") or getattr(c, "source_type", "")).upper()
                                if any(dt in c_type for dt in ("DATE", "TIME", "TIMESTAMP")):
                                    scol = getattr(c, "source_column", None)
                                    if scol:
                                        date_col_candidates.append(scol)
                                    date_col_candidates.append(c.unique_name)
                        for rel in getattr(model, "relationships", []) or []:
                            if getattr(rel, "from_dataset", None) == dataset.unique_name and getattr(rel, "to_dataset", None) == date_ds.unique_name:
                                date_col_candidates.extend(getattr(rel, "from_columns", []) or [])
                            elif getattr(rel, "to_dataset", None) == dataset.unique_name and getattr(rel, "from_dataset", None) == date_ds.unique_name:
                                date_col_candidates.extend(getattr(rel, "to_columns", []) or [])
                except Exception:
                    pass
            for candidate in date_col_candidates:
                if not candidate:
                    continue
                cand_str = str(candidate)
                if cand_str in live_cols:
                    return cand_str
                if cand_str.upper() in live_cols_upper:
                    return live_cols_upper[cand_str.upper()]

        # 4. Normalized match ignoring case and underscores, with reserved-word & key-suffix handling
        # 4. Normalized match ignoring case and underscores, with table prefix handling
        clean_raw = raw_upper.replace("_", "").replace(" ", "").replace("/", "").replace(".", "")
        ds_name = getattr(dataset, "unique_name", "") or ""
        ds_prefix = ds_name.upper().replace("_", "")
        stripped_raw = clean_raw[len(ds_prefix):] if (clean_raw.startswith(ds_prefix) and len(clean_raw) > len(ds_prefix)) else clean_raw

        # 4a. Exact normalized match with full raw name
        exact_full = [
            c for c in live_cols
            if clean_raw == c.upper().replace("_", "").replace(" ", "").replace("/", "").replace(".", "")
        ]
        if len(exact_full) == 1:
            return exact_full[0]
        elif len(exact_full) > 1:
            for c in exact_full:
                if c.upper() == raw_upper:
                    return c
            return exact_full[0]

        # 4b. Exact normalized match with table-prefix-stripped raw name (e.g. PRODUCT_PRODUCTID -> PRODUCT_ID)
        if stripped_raw != clean_raw:
            exact_stripped = [
                c for c in live_cols
                if stripped_raw == c.upper().replace("_", "").replace(" ", "").replace("/", "").replace(".", "")
            ]
            if len(exact_stripped) == 1:
                return exact_stripped[0]
            elif len(exact_stripped) > 1:
                for c in exact_stripped:
                    if c.upper() == stripped_raw:
                        return c
                return exact_stripped[0]

        # 4c. Prefix/suffix heuristic match (COL*, CAL*, *ID)
        heuristic_candidates = []
        for col_name in live_cols:
            col_upper = col_name.upper()
            clean_col = col_upper.replace("_", "").replace(" ", "").replace("/", "").replace(".", "")
            if clean_col in (f"COL{stripped_raw}", f"CAL{stripped_raw}") or stripped_raw in (f"COL{clean_col}", f"CAL{clean_col}"):
                heuristic_candidates.append(col_name)
            elif clean_col in (f"COL{clean_raw}", f"CAL{clean_raw}") or clean_raw in (f"COL{clean_col}", f"CAL{clean_col}"):
                heuristic_candidates.append(col_name)
            elif clean_col == f"{stripped_raw}ID" or stripped_raw == f"{clean_col}ID":
                heuristic_candidates.append(col_name)

        if len(heuristic_candidates) == 1:
            return heuristic_candidates[0]

        # 5. Fuzzy match fallback using sanitized names
        sanitized_raw = self._sanitize_col_name(raw_col_name).upper()
        for col_name in live_cols:
            if self._sanitize_col_name(col_name).upper() == sanitized_raw:
                return col_name

        # 6. Default to sanitizing raw_col_name if no match is found
        return self._sanitize_col_name(raw_col_name)


    def _collect_physical_source_columns_osi(self, dataset: OSIDataset) -> dict[str, Any]:
        """Return ordered map of physical Snowflake column name -> OSI column.

        Uses the same collision policy as SML physical columns: keep all by
        suffixing repeated sanitized names with ``_1``, ``_2``, ...
        """
        valid_columns: list[Any] = []
        base_totals: dict[str, int] = {}
        for col in dataset.columns:
            col_name = col.unique_name
            if col_name.startswith("RowNumber") or col_name.startswith("_"):
                continue
            source_expr = getattr(col, "source_expression", None)
            if source_expr and not self._is_physical_source_column(source_expr):
                continue

            valid_columns.append(col)
            safe_base = self._sanitize_col_name(col_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        selected: dict[str, Any] = {}
        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self._sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self._sanitize_col_name(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) == 1:
                safe_name = safe_base
            else:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                preferred_name = f"{safe_base}_{next_idx}"
                safe_name = self._resolve_persistent_duplicate_name(
                    scope_type="column",
                    namespace_key=namespace_key,
                    dataset_key=dataset_key,
                    normalized_base=safe_base,
                    source_name=col.unique_name,
                    source_signature=source_signature,
                    preferred_name=preferred_name,
                )
            while safe_name in selected:
                next_idx += 1
                base_seen[safe_base] = next_idx
                safe_name = f"{safe_base}_{next_idx}"

            selected[safe_name] = col

        return selected

    def generate_ctas_sql(
        self,
        table_name: str,
        columns: list[dict[str, str]],
        schema_name: Optional[str] = None,
        source_types: Optional[dict[str, str]] = None,
        fixed_table_name: Optional[str] = None,
    ) -> str:
        """Generate CTAS SQL that applies safe datatype conversions.

        Args:
            table_name: Physical table name (unqualified).
            columns: List of {"name": <col_name>, "type": <normalized_type>}.
            schema_name: Optional schema override.
            source_types: Optional mapping of column name -> Snowflake source DATA_TYPE.
        """
        if not columns:
            raise ValueError("No columns inferred")

        varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}
        if all((c.get("type", "").upper() in varchar_types) for c in columns):
            logger.warning(
                "All columns are VARCHAR for %s. Generating pass-through CTAS.",
                table_name,
            )

        select_parts: list[str] = []
        source_types = source_types or {}
        for col in columns:
            name = col["name"]
            dtype = col["type"].upper()
            quoted_name = f'"{name}"'
            source_type = (source_types.get(name) or source_types.get(name.upper()) or "").upper()

            cast_expr = self._build_cast_expression(
                quoted_name=quoted_name,
                target_type=dtype,
                source_type=source_type,
                col_name=name,
            )
            select_parts.append(f"{cast_expr} AS {quoted_name}")

        select_sql = ",\n    ".join(select_parts)

        schema = schema_name or self.config.schema_name
        quoted_source = f'{schema}."{table_name}"'
        fixed_name = fixed_table_name or f"{table_name}__FIXED"
        quoted_fixed = f'{schema}."{fixed_name}"'

        return (
            f"CREATE TABLE {quoted_fixed} AS\n"
            f"SELECT\n    {select_sql}\n"
            f"FROM {quoted_source}"
        )

    def _dataset_columns_for_ctas_sml(self, dataset: SMLDataset) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for col_name, col in self._collect_physical_source_columns(dataset).items():
            columns.append(
                {
                    "name": col_name,
                    "type": col.data_type.value.upper(),
                }
            )
        return columns

    @staticmethod
    def _infer_type_from_values(values: list[Any]) -> str:
        """Infer normalized type from sampled Python/Snowflake values."""
        if not values:
            return "VARCHAR"

        def _kind(v: Any) -> str:
            if isinstance(v, bool):
                return "BOOLEAN"
            if isinstance(v, int) and not isinstance(v, bool):
                return "INTEGER"
            if isinstance(v, (float, Decimal)):
                return "FLOAT"
            if isinstance(v, datetime):
                return "TIMESTAMP"
            if isinstance(v, date):
                return "DATE"

            s = str(v).strip()
            if not s:
                return "NULL"

            sl = s.lower()
            if sl in {"true", "false", "yes", "no", "y", "n", "t", "f"}:
                return "BOOLEAN"
            if re.fullmatch(r"[+-]?\d+", s):
                return "INTEGER"
            if re.fullmatch(r"[+-]?(?:\d+\.\d+|\d+\.\d*|\.\d+)", s):
                return "FLOAT"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return "DATE"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", s):
                return "TIMESTAMP"
            return "VARCHAR"

        kinds = {_kind(v) for v in values if v is not None}
        kinds.discard("NULL")
        if not kinds:
            return "VARCHAR"
        if kinds == {"BOOLEAN"}:
            return "BOOLEAN"
        if kinds == {"INTEGER"}:
            return "INTEGER"
        if kinds.issubset({"INTEGER", "FLOAT"}):
            return "FLOAT"
        if kinds == {"DATE"}:
            return "DATE"
        if kinds.issubset({"DATE", "TIMESTAMP"}):
            return "TIMESTAMP"
        return "VARCHAR"

    @staticmethod
    def _fallback_type_from_name(col_name: str) -> str:
        """Fallback inference for all-VARCHAR scenarios."""
        n = col_name.lower()
        tokens = [t for t in re.split(r"[^a-z0-9$]+", n.replace("_", " ")) if t]
        token_set = set(tokens)

        if any(k in n for k in ("timestamp", "datetime", "_ts")) or "ts" in token_set:
            return "TIMESTAMP"
        if "date" in n:
            return "DATE"
        # Duration-like columns (lead_time, processing_time, time_spent) should
        # default to numeric in fallback, not timestamp.
        if "time" in token_set:
            return "FLOAT"
        if any(k in n for k in ("amount", "price", "cost", "rate", "pct", "percent", "score", "revenue", "total", "qty", "quantity")):
            return "FLOAT"
        if any(k in n for k in ("is_", "has_", "flag", "active", "enabled", "deleted", "valid", "bool", "boolean")):
            return "BOOLEAN"
        # Use token-based numeric hints so words like ACCOUNT/COUNTRY do not
        # accidentally match "count".
        if any(k in token_set for k in ("count", "num", "year", "month", "day")):
            return "INTEGER"
        if any(k in token_set for k in ("id", "key")) or n.endswith("_id"):
            return "VARCHAR"
        return "VARCHAR"

    def _build_cast_expression(
        self,
        quoted_name: str,
        target_type: str,
        source_type: str,
        col_name: str
    ) -> str:
        """Build a safe cast expression for CTAS based on source and target types."""
        t = (target_type or "").upper()
        s = (source_type or "").upper()

        numeric_targets = {"INTEGER", "FLOAT", "DECIMAL", "NUMBER"}
        timestamp_targets = {"TIMESTAMP", "DATETIME", "TIME", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
        boolean_targets = {"BOOLEAN", "BOOL"}

        is_source_number = any(k in s for k in ("NUMBER", "DECIMAL", "NUMERIC", "INT", "FLOAT", "DOUBLE", "REAL"))
        is_source_timestamp = "TIMESTAMP" in s
        is_source_date = s.startswith("DATE")
        is_source_varchar = any(k in s for k in ("VARCHAR", "TEXT", "STRING", "CHAR"))
        is_source_boolean = "BOOLEAN" in s or s == "BOOL"

        # Same type -> no cast.
        if s == t:
            return quoted_name

        # Guard unsafe conversions that error in Snowflake.
        if is_source_number and (t == "DATE" or t in timestamp_targets):
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name
        if is_source_timestamp and t in numeric_targets:
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name
        if is_source_date and t in numeric_targets:
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name

        if t in boolean_targets:
            if is_source_boolean:
                return quoted_name
            if is_source_number:
                return f"IFF({quoted_name} IS NULL, NULL, IFF({quoted_name} = 0, FALSE, TRUE))"
            if is_source_varchar:
                return (
                    f"IFF({quoted_name} IS NULL, NULL, "
                    f"IFF(LOWER(TRIM({quoted_name})) IN ('true','1','t','yes','y'), TRUE, "
                    f"IFF(LOWER(TRIM({quoted_name})) IN ('false','0','f','no','n'), FALSE, NULL)))"
                )
            logger.warning("Skipping unsafe cast for %s: %s -> %s", col_name, source_type or "UNKNOWN", t)
            return quoted_name

        # Safe conversions.
        if is_source_timestamp and t == "DATE":
            return f"CAST({quoted_name} AS DATE)"
        if is_source_date and t in timestamp_targets:
            return f"TO_TIMESTAMP({quoted_name})"
        if is_source_varchar and t == "DATE":
            return f"TRY_TO_DATE({quoted_name})"
        if is_source_varchar and t in timestamp_targets:
            return f"TRY_TO_TIMESTAMP({quoted_name})"

        if t in numeric_targets:
            if is_source_number:
                return quoted_name
            # BOOLEAN -> numeric: TRY_TO_NUMBER(BOOLEAN) is invalid in Snowflake;
            # use IFF to convert TRUE->1, FALSE->0 instead.
            if is_source_boolean:
                return f"IFF({quoted_name} IS NULL, NULL, IFF({quoted_name}, 1, 0))"
            return f"TRY_TO_NUMBER({quoted_name})"
        if t == "DATE":
            if is_source_timestamp:
                return f"CAST({quoted_name} AS DATE)"
            if is_source_date:
                return quoted_name
            return f"TRY_TO_DATE({quoted_name})"
        if t in timestamp_targets:
            if is_source_timestamp:
                return quoted_name
            if is_source_date:
                return f"TO_TIMESTAMP({quoted_name})"
            return f"TRY_TO_TIMESTAMP({quoted_name})"

        return quoted_name

    def _get_source_column_types(self, cursor, safe_table_name: str) -> dict[str, str]:
        """Return Snowflake DATA_TYPE by column for the given physical table."""
        query = (
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s AND TABLE_NAME = %s"
        )
        self._execute_sql(
            cursor,
            query,
            (
                self.config.database.upper(),
                self.config.schema_name.upper(),
                safe_table_name.upper(),
            ),
            context=f"INFORMATION_SCHEMA.COLUMNS {safe_table_name}",
        )
        rows = cursor.fetchall() or []
        # Preserve original case for column names so quoted references in CTAS
        # match the actual column names (Snowflake is case-sensitive for quoted identifiers).
        # Store both original-case and uppercase keys so lookups work either way.
        col_types: dict[str, str] = {}
        for name, dtype in rows:
            original = str(name)
            col_types[original] = str(dtype).upper()
            col_types[original.upper()] = str(dtype).upper()
        logger.debug("Source column types for %s: %s", safe_table_name, col_types)
        return col_types

    def _infer_columns_from_table_samples(
        self,
        cursor,
        safe_table_name: str,
        columns: list[dict[str, str]],
        sample_limit: int = 200
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Resolve per-column types using model metadata first, sampling second."""
        if not columns:
            raise ValueError("No columns inferred")

        source_types = self._get_source_column_types(cursor, safe_table_name)
        source_names = list(source_types.keys())
        # Build a map from uppercase -> original-case for safe SELECT references
        original_case_map: dict[str, str] = {}
        for sn in source_names:
            upper = sn.upper()
            if upper not in original_case_map:
                original_case_map[upper] = sn

        def _norm(name: str) -> str:
            return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())

        resolved_pairs: list[tuple[str, str]] = []
        fallback_logs: list[str] = []
        for c in columns:
            requested = str(c["name"])
            requested_upper = requested.upper()
            source_name = None

            if requested_upper in source_types:
                # Use original-case name for the SELECT reference
                source_name = original_case_map.get(requested_upper, requested_upper)
            else:
                requested_norm = _norm(requested)
                matches = [src for src in source_names if _norm(src) == requested_norm]
                if len(matches) == 1:
                    source_name = matches[0]

            if source_name:
                resolved_pairs.append((requested, source_name))
            else:
                fallback_logs.append(
                    f"Skipped non-physical column during sampling: {safe_table_name}.{requested}"
                )

        if not resolved_pairs:
            logger.warning(
                "Skipping sample type inference for %s: no model columns matched physical source columns",
                safe_table_name,
            )
            return [], fallback_logs

        def _q(ident: str) -> str:
            escaped = str(ident).replace('"', '""')
            return f'"{escaped}"'

        col_refs = ", ".join([f"{_q(src)} AS {_q(req)}" for req, src in resolved_pairs])
        sample_sql = (
            f'SELECT {col_refs} FROM {self.config.schema_name}."{safe_table_name}" '
            f'LIMIT {sample_limit}'
        )
        # Be resilient to stale/mismatched identifiers in mixed mapping flows:
        # drop the offending projected column and retry sampling remaining columns.
        while True:
            try:
                self._execute_sql(cursor, sample_sql, context=f"SAMPLE QUERY {safe_table_name}")
                break
            except Exception as exc:
                text = str(exc or "")
                match = re.search(r"invalid identifier '([^']+)'", text, flags=re.IGNORECASE)
                invalid = str(match.group(1) if match else "").strip().replace('"', "")
                invalid_upper = invalid.upper()
                if not invalid_upper:
                    raise
                before = len(resolved_pairs)
                resolved_pairs = [
                    (req, src) for (req, src) in resolved_pairs
                    if str(req).upper() != invalid_upper and str(src).upper() != invalid_upper
                ]
                if len(resolved_pairs) == before:
                    raise
                fallback_logs.append(
                    f"Dropped invalid sampled identifier {safe_table_name}.{invalid_upper} and retried."
                )
                if not resolved_pairs:
                    logger.warning(
                        "Skipping sample type inference for %s after dropping invalid sampled identifiers",
                        safe_table_name,
                    )
                    return [], fallback_logs
                col_refs = ", ".join([f"{_q(src)} AS {_q(req)}" for req, src in resolved_pairs])
                sample_sql = (
                    f'SELECT {col_refs} FROM {self.config.schema_name}."{safe_table_name}" '
                    f'LIMIT {sample_limit}'
                )
        rows = cursor.fetchall() or []
        logger.info("Sample rows fetched for %s: %s", safe_table_name, len(rows))

        inferred: list[dict[str, str]] = []
        varchar_types = {"VARCHAR", "STRING", "TEXT", "UNKNOWN", "VARIANT"}

        sampled_columns = [
            c for c in columns if any(req == c["name"] for req, _src in resolved_pairs)
        ]

        for idx, col in enumerate(sampled_columns):
            declared_type = self._normalize_declared_type(col.get("type", ""))
            values = [r[idx] for r in rows if len(r) > idx and r[idx] is not None]
            logger.debug("%s.%s sample values: %s", safe_table_name, col["name"], values[:5])
            sampled_type = self._infer_type_from_values(values)
            business_type = self._business_rule_type(safe_table_name, col["name"])

            if business_type is not None:
                resolved_type = business_type
            elif declared_type not in varchar_types:
                resolved_type = declared_type
                if sampled_type != "VARCHAR" and sampled_type != declared_type:
                    logger.warning(
                        "Type mismatch for %s.%s (model=%s sample=%s); using model type",
                        safe_table_name,
                        col["name"],
                        declared_type,
                        sampled_type,
                    )
            else:
                resolved_type = sampled_type

            inferred.append({"name": col["name"], "type": resolved_type})
            logger.info(
                "Resolved datatype: %s.%s -> %s (model=%s sample=%s sampled_rows=%s non_null=%s)",
                safe_table_name,
                col["name"],
                resolved_type,
                declared_type,
                sampled_type,
                len(rows),
                len(values),
            )

        logger.info("Inferred types for %s:", safe_table_name)
        for col in inferred:
            logger.info("- %s -> %s", col["name"], col["type"])

        declared_types_map = {
            c["name"].upper(): str(c.get("type", "")).upper()
            for c in columns if c.get("type")
        }

        if all(c["type"] == "VARCHAR" for c in inferred):
            logger.warning(
                "All sampled columns resolved to VARCHAR for %s. Applying metadata-first fallback inference.",
                safe_table_name,
            )
            for c in inferred:
                c_upper = c["name"].upper()
                pbix_type = declared_types_map.get(c_upper)
                if pbix_type and pbix_type not in varchar_types:
                    fallback_logs.append(f"{c['name']}: VARCHAR -> {pbix_type} (PBIX metadata type)")
                    c["type"] = pbix_type
                else:
                    fb = self._fallback_type_from_name(c["name"])
                    if fb != "VARCHAR":
                        fallback_logs.append(f"{c['name']}: VARCHAR -> {fb} (name-pattern fallback)")
                        c["type"] = fb

        for entry in fallback_logs:
            logger.info("Fallback decision: %s", entry)

        if all(c["type"] == "VARCHAR" for c in inferred):
            logger.warning(
                "%s: only text-like columns detected after sampling/fallback; keeping VARCHAR types",
                safe_table_name,
            )

        return inferred, fallback_logs

    @staticmethod
    def _business_rule_type(
        table_name: str,
        col_name: str
    ) -> Optional[str]:
        """Business-rule layer for common CRM-style naming conventions."""
        return infer_business_field_type(table_name, col_name)

    @staticmethod
    def _normalize_declared_type(raw_type: str) -> str:
        """Normalize model-declared types to CTAS inference type family."""
        t = (raw_type or "").upper()
        mapping = {
            "INT64": "INTEGER",
            "INT": "INTEGER",
            "INTEGER": "INTEGER",
            "DOUBLE": "FLOAT",
            "FLOAT": "FLOAT",
            "DECIMAL": "DECIMAL",
            "NUMBER": "NUMBER",
            "BOOLEAN": "BOOLEAN",
            "BOOL": "BOOLEAN",
            "DATE": "DATE",
            "DATETIME": "TIMESTAMP",
            "TIMESTAMP": "TIMESTAMP",
            "TIMESTAMP_NTZ": "TIMESTAMP",
            "TIMESTAMP_LTZ": "TIMESTAMP",
            "TIMESTAMP_TZ": "TIMESTAMP",
            "STRING": "VARCHAR",
            "TEXT": "VARCHAR",
            "VARCHAR": "VARCHAR",
            "VARIANT": "VARCHAR",
        }
        return mapping.get(t, "VARCHAR")

    @staticmethod
    def _to_sml_datatype(inferred_type: str) -> DataType:
        mapping = {
            "INTEGER": DataType.INTEGER,
            "FLOAT": DataType.FLOAT,
            "DECIMAL": DataType.DECIMAL,
            "DATE": DataType.DATE,
            "TIMESTAMP": DataType.DATETIME,
            "BOOLEAN": DataType.BOOLEAN,
            "VARCHAR": DataType.STRING,
        }
        return mapping.get(inferred_type, DataType.STRING)

    @staticmethod
    def _to_osi_datatype(inferred_type: str):
        if OSIDataType is None:
            return None
        mapping = {
            "INTEGER": OSIDataType.INTEGER,
            "FLOAT": OSIDataType.FLOAT,
            "DECIMAL": OSIDataType.DECIMAL,
            "DATE": OSIDataType.DATE,
            "TIMESTAMP": OSIDataType.DATETIME,
            "BOOLEAN": OSIDataType.BOOLEAN,
            "VARCHAR": OSIDataType.STRING,
        }
        return mapping.get(inferred_type, OSIDataType.STRING)

    def _dataset_columns_for_ctas_osi(self, dataset: OSIDataset) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for col_name, col in self._collect_physical_source_columns_osi(dataset).items():
            columns.append(
                {
                    "name": col_name,
                    "type": col.data_type.value.upper(),
                }
            )
        return columns

    def _apply_inferred_types_ctas_sml(self, cursor, sml: SMLModel) -> None:
        """Apply inferred datatypes to physical SML source tables via CTAS + SWAP."""
        for dataset in sml.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            full_table = f'{self.config.schema_name}."{safe_table_name}"'
            fixed_table_name = self._build_fixed_table_name(safe_table_name)
            fixed_table = f'{self.config.schema_name}."{fixed_table_name}"'
            base_columns = self._dataset_columns_for_ctas_sml(dataset)
            if not base_columns:
                logger.info(
                    "Skipping CTAS type inference for dataset '%s': no physical columns inferred",
                    dataset.unique_name,
                )
                continue

            columns, _fallbacks = self._infer_columns_from_table_samples(
                cursor,
                safe_table_name,
                base_columns,
            )

            by_name = {c["name"]: c["type"] for c in columns}
            for col in dataset.columns:
                resolved = self._resolve_physical_column_name(dataset, col.unique_name)
                if resolved in by_name:
                    col.data_type = self._to_sml_datatype(by_name[resolved])

            if all(c["type"] == "VARCHAR" for c in columns):
                logger.warning(
                    "Skipping CTAS for %s: all inferred types are VARCHAR (valid text table)",
                    safe_table_name,
                )
                continue

            source_types = self._get_source_column_types(cursor, safe_table_name)
            ctas_sql = self.generate_ctas_sql(
                safe_table_name,
                columns,
                self.config.schema_name,
                source_types=source_types,
                fixed_table_name=fixed_table_name,
            )
            logger.info(f"Applying inferred datatypes via CTAS for table: {safe_table_name}")
            logger.debug(f"Generated CTAS SQL:\n{ctas_sql}")
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {fixed_table_name}")
            self._execute_sql(cursor, ctas_sql, context=f"CTAS {safe_table_name}")
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {full_table}", context=f"COUNT {safe_table_name}")
            original_count = cursor.fetchone()[0]
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {fixed_table}", context=f"COUNT {fixed_table_name}")
            fixed_count = cursor.fetchone()[0]
            if original_count != fixed_count:
                raise ConnectorError(
                    f"CTAS safety check failed for {safe_table_name}: original row count {original_count} does not match fixed row count {fixed_count}. Aborting swap."
                )
            self._execute_sql(cursor, f"ALTER TABLE {full_table} SWAP WITH {fixed_table}", context=f"SWAP TABLE {safe_table_name}")
            logger.info("Table swapped successfully: %s", safe_table_name)
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {fixed_table_name}")

    def _apply_inferred_types_ctas_osi(self, cursor, osi: OSIModel) -> None:
        """Apply inferred datatypes to physical OSI source tables via CTAS + SWAP."""
        for dataset in osi.datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table_name = self._safe_table_name(source_table)
            full_table = f'{self.config.schema_name}."{safe_table_name}"'
            fixed_table_name = self._build_fixed_table_name(safe_table_name)
            fixed_table = f'{self.config.schema_name}."{fixed_table_name}"'
            physical_cols = self._collect_physical_source_columns_osi(dataset)
            base_columns = self._dataset_columns_for_ctas_osi(dataset)
            if not base_columns:
                logger.info(
                    "Skipping CTAS type inference for dataset '%s': no physical columns inferred",
                    dataset.unique_name,
                )
                continue

            columns, _fallbacks = self._infer_columns_from_table_samples(
                cursor,
                safe_table_name,
                base_columns,
            )

            by_name = {c["name"]: c["type"] for c in columns}
            for phys_name, col in physical_cols.items():
                if phys_name in by_name:
                    mapped = self._to_osi_datatype(by_name[phys_name])
                    if mapped is not None:
                        col.data_type = mapped

            if all(c["type"] == "VARCHAR" for c in columns):
                logger.warning(
                    "Skipping CTAS for %s: all inferred types are VARCHAR (valid text table)",
                    safe_table_name,
                )
                continue

            source_types = self._get_source_column_types(cursor, safe_table_name)
            ctas_sql = self.generate_ctas_sql(
                safe_table_name,
                columns,
                self.config.schema_name,
                source_types=source_types,
                fixed_table_name=fixed_table_name,
            )
            logger.info(f"Applying inferred datatypes via CTAS for table: {safe_table_name}")
            logger.debug(f"Generated CTAS SQL:\n{ctas_sql}")
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {fixed_table_name}")
            self._execute_sql(cursor, ctas_sql, context=f"CTAS {safe_table_name}")
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {full_table}", context=f"COUNT {safe_table_name}")
            original_count = cursor.fetchone()[0]
            self._execute_sql(cursor, f"SELECT COUNT(*) FROM {fixed_table}", context=f"COUNT {fixed_table_name}")
            fixed_count = cursor.fetchone()[0]
            if original_count != fixed_count:
                raise ConnectorError(
                    f"CTAS safety check failed for {safe_table_name}: original row count {original_count} does not match fixed row count {fixed_count}. Aborting swap."
                )
            self._execute_sql(cursor, f"ALTER TABLE {full_table} SWAP WITH {fixed_table}", context=f"SWAP TABLE {safe_table_name}")
            logger.info("Table swapped successfully: %s", safe_table_name)
            self._execute_sql(cursor, f"DROP TABLE IF EXISTS {fixed_table}", context=f"DROP TABLE {fixed_table_name}")

    # ─────────────────────────────────────────────────────────────────────
    # Opt-in data backfill (options.load_source_data) — SML only, v1 scope.
    #
    # Loads real row data extracted from the source PBIX into a table this
    # run's own schema-creation step (_ensure_source_tables_exist) may have
    # just built structure-only, or that already exists but never received
    # real data from whatever process was supposed to populate it (see the
    # real incident this exists to prevent: SEMABRIDGE_WORKSPACE.PRODUCT had
    # data in exactly one column, NULL everywhere else in its non-key
    # columns). Off by default; only reached at all when the caller
    # (snowflake_emitter.py's deploy pipeline) has both
    # options.load_source_data=true AND a resolved PBIX path. Never touches
    # a table that already has any real data in any non-key column — see
    # _table_already_has_real_data.
    # ─────────────────────────────────────────────────────────────────────

    def _maybe_backfill_source_data(
        self,
        cursor,
        model: SMLModel,
        source_pbix_path: str,
        live_schema_metadata: Dict[str, set],
    ) -> list[dict]:
        """Attempt a data backfill for every dataset in `model`.

        Returns one {table, dataset, status, row_count, reason} dict per
        dataset considered (status is "loaded", "skipped", or "failed").
        Never raises — a backfill failure (missing pbixray, a corrupt PBIX,
        one bad table) must not abort an otherwise-successful deploy; each
        table's own attempt is independently wrapped in
        _backfill_one_table's try/except, and this method's own PBIXRay-open
        step is wrapped here for the same reason.
        """
        from semabridge.connectors.pbixray_compat import get_pbixray
        PBIXRay = get_pbixray()
        if PBIXRay is None:
            logger.warning("Data backfill requested but pbixray is unavailable")
            return [{
                "table": None, "dataset": None, "status": "failed", "row_count": 0,
                "reason": "pbixray is not installed/importable",
            }]

        try:
            pbix_model = PBIXRay(source_pbix_path)
        except Exception as exc:
            logger.warning("Data backfill: could not open PBIX file '%s': %s", source_pbix_path, exc)
            return [{
                "table": None, "dataset": None, "status": "failed", "row_count": 0,
                "reason": f"Could not open source PBIX file '{source_pbix_path}': {exc}",
            }]

        results = []
        for dataset in getattr(model, "datasets", []) or []:
            results.append(self._backfill_one_table(cursor, pbix_model, dataset, live_schema_metadata))
        return results

    def _backfill_one_table(
        self,
        cursor,
        pbix_model: Any,
        dataset: SMLDataset,
        live_schema_metadata: Dict[str, set],
    ) -> dict:
        """Backfill one dataset's table. Always returns a result dict, never
        raises — any exception here is caught and reported as status="failed"
        so one broken table can't abort the rest of the deploy or the rest
        of this backfill pass."""
        source_table = dataset.source_table or dataset.unique_name
        safe_table_name = self._safe_table_name(source_table)
        dataset_name = dataset.unique_name

        try:
            physical_cols = self._collect_physical_source_columns(dataset)
            if not physical_cols:
                return self._backfill_result(
                    safe_table_name, dataset_name, "skipped", 0,
                    "No physical columns inferred for this dataset — nothing to load.",
                )

            key_names = {col.unique_name for col in dataset.get_key_columns()}
            non_key_safe_names = [
                safe_name for safe_name, col in physical_cols.items()
                if col.unique_name not in key_names
            ]
            if not non_key_safe_names:
                return self._backfill_result(
                    safe_table_name, dataset_name, "skipped", 0,
                    "This table has no non-key columns to check or load.",
                )

            classification = self._classify_table_data_state(
                cursor, safe_table_name, physical_cols, non_key_safe_names,
            )
            state = classification["state"]
            row_count = classification["row_count"]

            if state == "real_data":
                # The table as a whole is genuinely populated -- but a
                # column that _ensure_source_tables_exist ALTER-TABLE-ADDed
                # during THIS SAME deploy (a newer/wider PBIX schema
                # introducing fields an older sync's table didn't have) is
                # unavoidably NULL for every pre-existing row (ALTER TABLE
                # ADD COLUMN cannot populate historical rows, and nothing
                # else in this pipeline does either) -- see the real
                # incident this exists to prevent: PRODUCT.ISVANARSDEL,
                # added this way, permanently null despite SEGMENT/PRODUCT/
                # PRODUCTID on the same rows being fully real.
                #
                # Deliberately narrow: only columns THIS deploy just added
                # are eligible, and only if they are still 100% null right
                # now (a partially-populated newly-added column is a
                # different, more ambiguous case -- see the guard inside
                # _backfill_columns_for_existing_rows). A column that
                # already existed before this run and is all-null is NOT
                # covered here even if it looks identical -- that's a
                # separate, riskier "was this column always meant to be
                # empty" question this fix does not attempt to answer.
                newly_added = self._newly_added_columns.get(safe_table_name, set())
                still_empty_new_cols = [
                    c for c in non_key_safe_names
                    if c in newly_added and c not in classification["populated_columns"]
                ]
                if not still_empty_new_cols:
                    return self._backfill_result(
                        safe_table_name, dataset_name, "skipped", row_count,
                        "Table already has real data in at least one non-key column — left untouched.",
                    )
                return self._backfill_columns_for_existing_rows(
                    cursor, pbix_model, dataset, safe_table_name, dataset_name,
                    physical_cols, key_names, still_empty_new_cols, row_count,
                    live_schema_metadata,
                )
            if state == "ambiguous":
                reason = self._format_ambiguous_reason(
                    row_count, classification["populated_columns"], classification["samples"],
                )
                return self._backfill_result(
                    safe_table_name, dataset_name, "ambiguous_needs_review", row_count, reason,
                )
            # state == "empty" -- confidently not real data, proceed to load.

            df = pbix_model.get_table(source_table)
            if df is None or df.empty:
                return self._backfill_result(
                    safe_table_name, dataset_name, "skipped", 0,
                    f"Source PBIX table '{source_table}' has no rows to load.",
                )

            # Each source column is loaded into ITS OWN final physical
            # column (the name the table was created with, which can differ
            # from the sanitized source name -- e.g. a disambiguated or
            # renamed column), never matched by re-sanitizing the source
            # name and hoping it lines up (which left such columns 100% NULL).
            df, source_by_target = self._map_source_columns_to_physical(df, physical_cols)

            live_cols = {str(c).upper() for c in live_schema_metadata.get(safe_table_name.upper(), set())}
            if live_cols:
                unmatched = [c for c in df.columns if c.upper() not in live_cols]
                if unmatched:
                    logger.warning(
                        "Data backfill for %s: dropping %d extracted column(s) with no "
                        "matching live physical column: %s",
                        safe_table_name, len(unmatched), unmatched,
                    )
                    df = df.drop(columns=unmatched)
                if df.shape[1] == 0:
                    return self._backfill_result(
                        safe_table_name, dataset_name, "failed", 0,
                        "None of the extracted PBIX columns matched this table's live physical columns.",
                    )

            # A pandas datetime64 column staged with use_logical_type=True
            # (below) still carries a full TIMESTAMP value (midnight time
            # component included) -- confirmed against a real load:
            # Snowflake's COPY INTO rejects casting that timestamp-shaped
            # variant into a strict DATE-typed target column even at
            # 00:00:00.000 ("Failed to cast variant value '<ts>' to
            # DATE"). Narrowing to plain Python date objects for any
            # column this dataset actually declares as DataType.DATE (not
            # DATETIME/TIMESTAMP) avoids the mismatch at the source.
            import pandas as pd

            for safe_name, col in physical_cols.items():
                if (
                    safe_name in df.columns
                    and getattr(col, "data_type", None) == DataType.DATE
                    and pd.api.types.is_datetime64_any_dtype(df[safe_name])
                ):
                    df[safe_name] = df[safe_name].dt.date

            from snowflake.connector.pandas_tools import write_pandas

            success, _num_chunks, num_rows, _details = write_pandas(
                cursor.connection,
                df,
                safe_table_name,
                schema=self.config.schema_name,
                # Explicit and load-bearing: if the sanitized table/column
                # names don't match what's really live, this must fail
                # loudly rather than silently create a shadow table.
                auto_create_table=False,
                quote_identifiers=True,
                # Without this, write_pandas stages a pandas datetime64
                # column as a raw INT64 nanosecond-epoch value with no
                # Parquet logical-type annotation, and Snowflake's COPY
                # INTO then can't implicitly cast that plain integer into
                # a DATE/TIMESTAMP-typed target column ("Failed to cast
                # variant value <ns-epoch> to DATE") -- confirmed against
                # a real load of Sales & Returns Sample's Date column.
                # use_logical_type=True makes write_pandas emit proper
                # DATE/TIMESTAMP Parquet logical types instead.
                use_logical_type=True,
            )
            if not success:
                return self._backfill_result(
                    safe_table_name, dataset_name, "failed", 0,
                    "write_pandas reported failure loading this table.",
                )

            empty_targets = self._columns_left_all_null(cursor, safe_table_name, df, list(df.columns))
            if empty_targets:
                details = ", ".join(f"{target} (source '{source_by_target.get(target, target)}')" for target in empty_targets)
                logger.error(
                    "Data backfill for %s: column(s) loaded 100%% NULL although the source has values: %s",
                    safe_table_name, details,
                )
                return self._backfill_result(
                    safe_table_name, dataset_name, "failed", int(num_rows),
                    f"Loaded {int(num_rows)} row(s), but these column(s) are 100% NULL in the table "
                    f"while their source column has values: {details}.",
                    columns=list(empty_targets),
                )

            logger.info(
                "Data backfill: loaded %d row(s) into %s.%s from source PBIX table '%s'",
                num_rows, self.config.schema_name, safe_table_name, source_table,
            )
            return self._backfill_result(
                safe_table_name, dataset_name, "loaded", int(num_rows),
                f"Loaded {int(num_rows)} row(s) from the source PBIX.",
            )

        except Exception as exc:
            logger.warning("Data backfill failed for table %s: %s", safe_table_name, exc, exc_info=True)
            return self._backfill_result(
                safe_table_name, dataset_name, "failed", 0,
                f"Unexpected error during backfill: {exc}",
            )

    def _backfill_columns_for_existing_rows(
        self,
        cursor,
        pbix_model: Any,
        dataset: SMLDataset,
        safe_table_name: str,
        dataset_name: str,
        physical_cols: Dict[str, Any],
        key_names: set,
        target_cols: list[str],
        row_count: int,
        live_schema_metadata: Dict[str, set],
    ) -> dict:
        """Patch specific, currently-100%-null columns on a table's EXISTING
        rows -- for columns _ensure_source_tables_exist ALTER-TABLE-ADDed
        this same deploy onto a table that already has real data elsewhere.

        write_pandas/COPY INTO (the whole-table path just above) is
        deliberately append-only -- it can only add rows, never update an
        existing row's column value in place (see that path's own comment
        on why: even a wrong eligibility verdict must never destroy data).
        That mechanism is wrong here: the table already has `row_count`
        real rows, and the goal is to fill in one or more columns ON those
        same rows, not add more rows. This uses a narrow, explicitly-scoped
        MERGE instead: stage just {key column(s) + target_cols} from the
        source PBIX into a session-scoped TEMPORARY table, then
        MERGE ... WHEN MATCHED THEN UPDATE SET <target_cols only> -- never
        touching the key column(s) or any other column on the matched
        rows.

        Requires a reliable key column common to both sides, unique on
        both sides, with matching row counts; refuses (returns
        "ambiguous_needs_review") rather than guess when any of that
        doesn't hold -- a mismatch means key-based matching can't be
        trusted not to silently patch the wrong row, patch a duplicate, or
        miss rows entirely.
        """
        source_table = dataset.source_table or dataset.unique_name
        key_safe_names = [
            safe_name for safe_name, col in physical_cols.items()
            if col.unique_name in key_names
        ]
        if not key_safe_names:
            return self._backfill_result(
                safe_table_name, dataset_name, "ambiguous_needs_review", row_count,
                f"Column(s) {target_cols} were added by this deploy and are still empty, "
                "but this dataset has no key column to safely match existing rows against "
                "the source PBIX -- left untouched rather than guess.",
                columns=list(target_cols),
            )

        try:
            df = pbix_model.get_table(source_table)
            if df is None or df.empty:
                return self._backfill_result(
                    safe_table_name, dataset_name, "skipped", 0,
                    f"Column(s) {target_cols} were added by this deploy, but source PBIX "
                    f"table '{source_table}' has no rows to backfill them from.",
                    columns=list(target_cols),
                )

            df, _source_by_target = self._map_source_columns_to_physical(df, physical_cols)

            wanted = list(dict.fromkeys(key_safe_names + target_cols))
            missing = [c for c in wanted if c not in df.columns]
            if missing:
                return self._backfill_result(
                    safe_table_name, dataset_name, "ambiguous_needs_review", row_count,
                    f"Column(s) {target_cols} were added by this deploy, but the extracted "
                    f"PBIX data is missing expected column(s) {missing} needed to match "
                    "existing rows -- left untouched rather than guess.",
                    columns=list(target_cols),
                )
            df = df[wanted]

            # A non-unique key makes "WHEN MATCHED" ambiguous about which
            # source row's value wins -- exactly the kind of silent guess
            # this feature exists to avoid.
            if len(key_safe_names) == 1 and df[key_safe_names[0]].duplicated().any():
                return self._backfill_result(
                    safe_table_name, dataset_name, "ambiguous_needs_review", row_count,
                    f"Column(s) {target_cols} were added by this deploy, but the source "
                    f"PBIX table has duplicate values in key column '{key_safe_names[0]}' -- "
                    "left untouched rather than risk an ambiguous match.",
                    columns=list(target_cols),
                )

            import pandas as pd

            for safe_name, col in physical_cols.items():
                if (
                    safe_name in df.columns
                    and getattr(col, "data_type", None) == DataType.DATE
                    and pd.api.types.is_datetime64_any_dtype(df[safe_name])
                ):
                    df[safe_name] = df[safe_name].dt.date

            source_row_count = len(df)
            # Mirrors the CTAS+SWAP row-count safety guard elsewhere in
            # this file: if the source PBIX's row count doesn't match the
            # live table's, key-based matching can't be trusted -- bail
            # out to a human rather than patch a possibly-wrong subset.
            if source_row_count != row_count:
                return self._backfill_result(
                    safe_table_name, dataset_name, "ambiguous_needs_review", row_count,
                    f"Column(s) {target_cols} were added by this deploy, but the source "
                    f"PBIX has {source_row_count} row(s) for '{source_table}' while the "
                    f"live table has {row_count} -- left untouched rather than risk a "
                    "partial or mismatched patch.",
                    columns=list(target_cols),
                )

            staging_name = self._build_staging_table_name(safe_table_name)
            from snowflake.connector.pandas_tools import write_pandas

            success, _num_chunks, _num_rows, _details = write_pandas(
                cursor.connection,
                df,
                staging_name,
                schema=self.config.schema_name,
                auto_create_table=True,
                table_type="temporary",
                quote_identifiers=True,
                use_logical_type=True,
            )
            if not success:
                return self._backfill_result(
                    safe_table_name, dataset_name, "failed", 0,
                    f"write_pandas reported failure staging data to backfill column(s) {target_cols}.",
                    columns=list(target_cols),
                )

            try:
                key_expr = " AND ".join(f'tgt."{k}" = src."{k}"' for k in key_safe_names)
                set_clause = ", ".join(f'tgt."{c}" = src."{c}"' for c in target_cols)
                merge_sql = (
                    f'MERGE INTO {self.config.schema_name}."{safe_table_name}" AS tgt '
                    f'USING {self.config.schema_name}."{staging_name}" AS src '
                    f'ON {key_expr} '
                    f'WHEN MATCHED THEN UPDATE SET {set_clause}'
                )
                self._execute_sql(
                    cursor, merge_sql, context=f"BACKFILL COLUMNS MERGE {safe_table_name}",
                )
                # Snowflake's connector doesn't reliably populate
                # cursor.rowcount for a MERGE across all driver versions --
                # source_row_count is a safe stand-in since the row-count
                # parity check above already guarantees every source row
                # has exactly one live counterpart to match.
                updated = cursor.rowcount if cursor.rowcount is not None else source_row_count
            finally:
                self._execute_sql(
                    cursor, f'DROP TABLE IF EXISTS {self.config.schema_name}."{staging_name}"',
                    context=f"DROP STAGING TABLE {staging_name}",
                )

            empty_targets = self._columns_left_all_null(cursor, safe_table_name, df, list(target_cols))
            if empty_targets:
                logger.error(
                    "Data backfill for %s: patched column(s) still 100%% NULL although the source has values: %s",
                    safe_table_name, empty_targets,
                )
                return self._backfill_result(
                    safe_table_name, dataset_name, "failed", updated,
                    f"Patched {updated} row(s), but column(s) {empty_targets} are still 100% NULL "
                    "while their source column has values.",
                    columns=list(empty_targets),
                )

            logger.info(
                "Data backfill: patched column(s) %s on %d existing row(s) in %s.%s "
                "(added by this deploy, source PBIX table '%s')",
                target_cols, updated, self.config.schema_name, safe_table_name, source_table,
            )
            return self._backfill_result(
                safe_table_name, dataset_name, "columns_backfilled", updated,
                f"Patched newly-added column(s) {target_cols} on {updated} existing "
                "row(s) from the source PBIX -- other columns on those rows were left "
                "untouched.",
                columns=list(target_cols),
            )
        except Exception as exc:
            logger.warning(
                "Column-level data backfill failed for table %s columns %s: %s",
                safe_table_name, target_cols, exc, exc_info=True,
            )
            return self._backfill_result(
                safe_table_name, dataset_name, "failed", 0,
                f"Unexpected error while backfilling column(s) {target_cols}: {exc}",
                columns=list(target_cols),
            )

    def _map_source_columns_to_physical(self, df: Any, physical_cols: Dict[str, Any]) -> tuple[Any, Dict[str, str]]:
        """Rename the extracted source DataFrame's columns to the FINAL
        physical column each one belongs to, and drop source columns that
        belong to no physical column.

        physical_cols (from _collect_physical_source_columns -- the same map
        the table was created from) maps physical name -> model column. Each
        model column is matched to its source column by identity: exact
        name, then case-insensitive, then sanitized form; each source column
        is used at most once. The column's physical name -- not a
        re-sanitized source name -- is what the data is loaded into, so a
        column whose physical name differs from its source name (suffixed or
        renamed) still receives its data.

        Returns (renamed_df, {physical_name: source_column_name}).
        """
        source_names = [str(col) for col in df.columns]
        exact = {name: name for name in source_names}
        folded: Dict[str, list] = {}
        sanitized: Dict[str, list] = {}
        for name in source_names:
            folded.setdefault(name.casefold(), []).append(name)
            sanitized.setdefault(self._sanitize_col_name(name).upper(), []).append(name)

        used: set = set()
        rename: Dict[str, str] = {}
        source_by_target: Dict[str, str] = {}

        def _take(candidates):
            for candidate in candidates or []:
                if candidate not in used:
                    return candidate
            return None

        for physical_name, col in physical_cols.items():
            unique_name = str(getattr(col, "unique_name", "") or "")
            source = (
                (exact.get(unique_name) if exact.get(unique_name) not in used else None)
                or _take(folded.get(unique_name.casefold()))
                or _take(sanitized.get(self._sanitize_col_name(unique_name).upper()))
            )
            if source is None:
                continue
            used.add(source)
            rename[source] = physical_name
            source_by_target[physical_name] = source

        unmatched = [name for name in source_names if name not in used]
        if unmatched:
            logger.debug("Data backfill: source column(s) with no model column, not loaded: %s", unmatched)
        df = df[[name for name in source_names if name in used]].rename(columns=rename)
        return df, source_by_target

    def _columns_left_all_null(self, cursor, safe_table_name: str, df: Any, target_cols: list[str]) -> list[str]:
        """Post-load check: target columns that are 100% NULL in the table
        although the loaded source data has values for them."""
        expected = [
            col for col in target_cols
            if col in getattr(df, "columns", []) and bool(df[col].notna().any())
        ]
        if not expected:
            return []
        select_list = ", ".join(f'COUNT("{col}")' for col in expected)
        self._execute_sql(
            cursor,
            f'SELECT {select_list} FROM {self.config.schema_name}."{safe_table_name}"',
            context=f"POST-LOAD NULL CHECK {safe_table_name}",
        )
        row = cursor.fetchone() or ()
        return [col for col, count in zip(expected, row) if not count]

    @staticmethod
    def _backfill_result(
        table: str,
        dataset: str,
        status: str,
        row_count: int,
        reason: str,
        columns: Optional[list[str]] = None,
    ) -> dict:
        result = {"table": table, "dataset": dataset, "status": status, "row_count": row_count, "reason": reason}
        # Only present for the column-level backfill path (status
        # "columns_backfilled", or an "ambiguous_needs_review"/"failed"
        # outcome reached from that same path) -- absent (not just empty)
        # for every whole-table loaded/skipped/failed outcome, so the run
        # report can render the two shapes distinctly instead of
        # conflating "loaded a fresh table" with "patched existing rows'
        # column(s)".
        if columns:
            result["columns"] = columns
        return result

    # A table needs at least this many existing rows before it's even
    # eligible to be judged "already populated" at all. Below this floor,
    # ANY non-null pattern is treated as noise, not real data -- this
    # directly covers a single leftover garbage row (all-NULL, or the
    # generate_sample_insert() 'Sample_<column>' placeholder pattern
    # table_management.py writes for a brand-new table), which would
    # otherwise permanently block a legitimate backfill the moment any ONE
    # of its columns happened to carry a stray non-null value. Chosen at
    # the low end of the requested 10-100 range deliberately: this tool's
    # own PBIX source is the ground truth for what a table's real row
    # count should be, so a genuinely tiny-but-real table (say 5 rows)
    # backfilling again just re-loads the SAME rows from the SAME source
    # -- unlike an arbitrary external system that might diverge from the
    # PBIX, there's no independent "real" state here to lose. Note
    # write_pandas is never called with overwrite=True (see
    # _backfill_one_table), so even a wrong "eligible" verdict only ever
    # appends/duplicates rows -- it can never truncate or delete a table
    # that turns out to have been genuinely, if sparsely, populated.
    _MIN_ROW_COUNT_FOR_POPULATED = 10

    # Among the rows that DO exist, a non-key column only counts as
    # "meaningfully populated" once at least half of them are non-null --
    # a single stray non-null value out of hundreds of rows (noise, a
    # manual test insert, a partial/aborted prior load) must not
    # permanently block backfill just because SOME value exists somewhere.
    # 50% still easily preserves the original incident's protection: a
    # genuinely populated column (like PRODUCT in the real incident this
    # feature exists to prevent recurring) is typically at or near 100%
    # non-null, nowhere close to this threshold.
    _MIN_POPULATED_FRACTION = 0.5

    def _classify_table_data_state(
        self,
        cursor,
        safe_table_name: str,
        physical_cols: Dict[str, Any],
        non_key_safe_names: list[str],
    ) -> Dict[str, Any]:
        """Classify a table's current data state into exactly one of three
        outcomes, returned as {"state", "row_count", "populated_columns",
        "samples"}:

        - "real_data": row_count >= _MIN_ROW_COUNT_FOR_POPULATED AND at
          least one non-key column is non-null across at least
          _MIN_POPULATED_FRACTION of those rows -- confidently real,
          skip and leave untouched.
        - "empty": row_count == 0, every non-key column is entirely null
          regardless of row count, OR (below the floor) the populated
          value(s) exactly match the known generate_sample_insert()
          placeholder pattern (see _is_known_placeholder_pattern) --
          confidently NOT real data, safe to proceed with backfill.
        - "ambiguous": below _MIN_ROW_COUNT_FOR_POPULATED but at least one
          non-key column has a non-null value that ISN'T the known
          placeholder pattern -- could be a genuinely small real table
          (e.g. a 1-row or 7-row reference table) or leftover garbage from
          something else, and this check cannot tell the two apart from
          row/null counts alone. Deliberately NOT auto-decided either way:
          skipped without loading, surfaced as its own status
          ("ambiguous_needs_review") with enough evidence (which columns,
          sample values) for a human reading the run report to decide
          without re-running diagnostics themselves.

        A blanket floor-only rule (skip below N rows always eligible)
        would auto-backfill-over a genuinely real small table just as
        easily as a genuinely garbage one -- this three-way split exists
        specifically so that ambiguity is never silently resolved in
        either direction.
        """
        count_exprs = ", ".join(f'COUNT("{c}") AS "{c}"' for c in non_key_safe_names)
        query = (
            f'SELECT COUNT(*) AS "ROW_COUNT", {count_exprs} '
            f'FROM {self.config.schema_name}."{safe_table_name}"'
        )
        self._execute_sql(cursor, query, context=f"BACKFILL SAFETY CHECK {safe_table_name}")
        row = cursor.fetchone()
        if not row:
            return {"state": "empty", "row_count": 0, "populated_columns": [], "samples": {}}

        row_count = int(row[0] or 0)
        counts = {name: int(c or 0) for name, c in zip(non_key_safe_names, row[1:])}
        populated_columns = [name for name, c in counts.items() if c > 0]

        if row_count == 0:
            return {"state": "empty", "row_count": 0, "populated_columns": [], "samples": {}}

        if row_count >= self._MIN_ROW_COUNT_FOR_POPULATED:
            has_real_data = any(
                (c / row_count) >= self._MIN_POPULATED_FRACTION for c in counts.values()
            )
            return {
                "state": "real_data" if has_real_data else "empty",
                "row_count": row_count,
                "populated_columns": populated_columns,
                "samples": {},
            }

        # Below the row-count floor.
        if not populated_columns:
            return {"state": "empty", "row_count": row_count, "populated_columns": [], "samples": {}}

        if self._is_known_placeholder_pattern(
            cursor, safe_table_name, physical_cols, populated_columns, row_count,
        ):
            return {
                "state": "empty", "row_count": row_count,
                "populated_columns": populated_columns, "samples": {},
            }

        samples = self._fetch_sample_values(cursor, safe_table_name, populated_columns, row_count)
        return {
            "state": "ambiguous", "row_count": row_count,
            "populated_columns": populated_columns, "samples": samples,
        }

    def _is_known_placeholder_pattern(
        self,
        cursor,
        safe_table_name: str,
        physical_cols: Dict[str, Any],
        populated_columns: list[str],
        row_count: int,
    ) -> bool:
        """True only for the exact generate_sample_insert() placeholder
        shape (table_management.py) -- a single row where every populated
        non-key STRING column's value is literally
        'Sample_<original PBIX column name, truncated to 20 chars>', the
        one-time seed row that pipeline writes for a brand-new table
        (guarded there by its own WHERE NOT EXISTS clause, so it can never
        insert more than one). Confidently garbage, not ambiguous, because
        this exact literal string can't plausibly arise from real business
        data by coincidence. Only STRING columns carry this identifiable
        signature (generate_sample_insert() fills numeric/boolean/date
        columns with "1"/TRUE/CURRENT_DATE(), none of which are
        distinguishable from real values on their own) -- but requiring
        EVERY populated string column to match simultaneously is already
        strong, specific evidence.
        """
        if row_count != 1:
            return False
        string_populated = [
            name for name in populated_columns
            if getattr(physical_cols.get(name), "data_type", None) == DataType.STRING
        ]
        if not string_populated:
            return False
        samples = self._fetch_sample_values(cursor, safe_table_name, string_populated, row_count)
        for name in string_populated:
            col = physical_cols.get(name)
            if col is None:
                return False
            expected = f"Sample_{col.unique_name[:20]}"
            values = samples.get(name) or []
            if not values or values[0] != expected:
                return False
        return True

    def _fetch_sample_values(
        self,
        cursor,
        safe_table_name: str,
        columns: list[str],
        limit: int,
    ) -> Dict[str, list]:
        """Up to 3 non-null sample values per column, for evidence
        (ambiguous-review reason text) or placeholder-pattern matching.
        `limit` is the table's own row_count -- always small here (only
        ever called below _MIN_ROW_COUNT_FOR_POPULATED), so fetching every
        row is cheap and safe."""
        if not columns:
            return {}
        col_list = ", ".join(f'"{c}"' for c in columns)
        query = f'SELECT {col_list} FROM {self.config.schema_name}."{safe_table_name}" LIMIT {max(int(limit), 1)}'
        self._execute_sql(cursor, query, context=f"BACKFILL SAMPLE VALUES {safe_table_name}")
        rows = cursor.fetchall() or []
        samples: Dict[str, list] = {c: [] for c in columns}
        for r in rows:
            for name, val in zip(columns, r):
                if val is not None and len(samples[name]) < 3:
                    samples[name].append(val)
        return samples

    @staticmethod
    def _format_ambiguous_reason(
        row_count: int,
        populated_columns: list[str],
        samples: Dict[str, list],
    ) -> str:
        """Enough detail for a human reading the run report to decide
        without re-running diagnostics themselves -- row count, which
        columns had data, and what that data actually looks like."""
        parts = []
        for name in populated_columns:
            values = samples.get(name) or []
            sample_str = ", ".join(repr(v) for v in values) if values else "(no sample captured)"
            parts.append(f"{name}=[{sample_str}]")
        cols_desc = "; ".join(parts) if parts else "(none)"
        return (
            f"Ambiguous: {row_count} row(s) — below the minimum ({SnowflakeSchemaManager._MIN_ROW_COUNT_FOR_POPULATED}) "
            f"to confidently call this real data, but not confirmed empty either. Populated non-key column(s) "
            f"and sample value(s): {cols_desc}. Needs manual review — left untouched without loading."
        )

    def evolve_schema(
        self,
        cursor: Any,
        table_name: str,
        new_columns: list[tuple[str, str]]
    ) -> dict[str, str]:
        """Incrementally evolve a Snowflake table schema.

        Compares the existing table structure against the required columns
        from the latest Semantic Snapshot and applies non-destructive changes:
          - New columns â†’ ALTER TABLE ADD COLUMN
          - Removed columns â†’ Renamed with ``_DEPRECATED_`` prefix (Soft Delete)

        Args:
            cursor: Active Snowflake cursor.
            table_name: Fully qualified table name.
            new_columns: List of (column_name, snowflake_type) tuples
                representing the desired schema.

        Returns:
            Dict summarising actions taken, e.g.
            {"added": ["Col_A"], "deprecated": ["Col_B"]}.
        """
        actions: dict[str, list[str]] = {"added": [], "deprecated": []}

        # Fetch existing columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            self._execute_sql(cursor, f"DESC TABLE {table_name}", context=f"DESC TABLE {table_name}")
            existing = {row[0].upper(): row[1] for row in cursor.fetchall()}
        except Exception:
            logger.warning(
                f"Table {table_name} does not exist â€” cannot evolve schema"
            )
            return actions

        desired_upper = {col[0].upper(): col[1] for col in new_columns}

        # ADD new columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for col_name, col_type in new_columns:
            if col_name.upper() not in existing:
                try:
                    ddl = (
                        f'ALTER TABLE {table_name} '
                        f'ADD COLUMN "{col_name.upper()}" {col_type}'
                    )
                    self._execute_sql(cursor, ddl, context=f"ALTER TABLE ADD COLUMN {table_name}.{col_name}")
                    actions["added"].append(col_name)
                    logger.info(f"Schema evolution: added column {col_name}")
                except Exception as e:
                    logger.error(f"Failed to add column {col_name}: {e}")

        # SOFT-DELETE removed columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for existing_col in existing:
            if (
                existing_col not in desired_upper
                and not existing_col.startswith("_DEPRECATED_")
            ):
                new_name = f"_DEPRECATED_{existing_col}"
                try:
                    ddl = (
                        f'ALTER TABLE {table_name} '
                        f'RENAME COLUMN "{existing_col}" TO "{new_name}"'
                    )
                    self._execute_sql(cursor, ddl, context=f"RENAME COLUMN {table_name}.{existing_col}")
                    actions["deprecated"].append(existing_col)
                    logger.info(
                        f"Schema evolution: soft-deleted {existing_col} â†’ {new_name}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to deprecate column {existing_col}: {e}"
                    )

        if actions["added"] or actions["deprecated"]:
            logger.info(
                f"Schema evolution complete for {table_name}: "
                f"+{len(actions['added'])} / "
                f"-{len(actions['deprecated'])} columns"
            )
        return actions
    def _fetch_schema_metadata(self, cursor) -> Dict[str, set]:
        """Fetch all table and column names from the current schema for pre-validation."""
        cached = self._get_cached_schema_metadata()
        if cached:
            self._live_schema_metadata.update(cached)
            return cached
        target_schema = self._data_schema_name().upper()
        query = (
            "SELECT table_name, column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = %s"
        )
        self._execute_sql(cursor, query, (target_schema,), context="FETCH SCHEMA METADATA")
        
        metadata: Dict[str, set] = {}
        for row in cursor.fetchall():
            table = row[0].upper()
            column = row[1].upper()
            if table not in metadata:
                metadata[table] = set()
            metadata[table].add(column)
        self._live_schema_metadata.update(metadata)

        # Identify placeholder tables from table comments
        try:
            query_comments = (
                "SELECT table_name, comment "
                "FROM information_schema.tables "
                "WHERE table_schema = %s"
            )
            self._execute_sql(cursor, query_comments, (target_schema,), context="FETCH TABLE COMMENTS")
            for row in cursor.fetchall() or []:
                tbl = str(row[0]).upper()
                cmt = str(row[1] or "")
                if "SemaBridge placeholder" in cmt:
                    self._live_schema_metadata.placeholder_tables.add(tbl)
        except Exception as exc:
            logger.debug("Table comments unavailable in %s: %s", target_schema, exc)

        # Detect and list duplicate placeholder tables without dropping them
        from semabridge.connectors.table_binding_resolver import clean_ident
        dup_map: Dict[str, List[str]] = {}
        for tbl in metadata.keys():
            dup_map.setdefault(clean_ident(tbl), []).append(tbl)
        dup_placeholders = {
            norm: tbls for norm, tbls in dup_map.items()
            if len(tbls) > 1 and any(t in self._live_schema_metadata.placeholder_tables for t in tbls)
        }
        self._live_schema_metadata.duplicate_placeholder_tables = dup_placeholders
        self.duplicate_placeholders = dup_placeholders

        key = _schema_cache_key(self.config.database, target_schema)
        with _SCHEMA_CACHE_LOCK:
            _SCHEMA_CACHE[key] = {"loaded_at": time.time(), "metadata": metadata}
        return metadata

    def _fetch_model_table_metadata(self, cursor, datasets: list[Any]) -> Dict[str, set[str]]:
        """Fetch metadata only for tables referenced in the current model."""
        cached = self._get_cached_schema_metadata()
        if cached:
            self._live_schema_metadata.update(cached)
            return cached
        table_names = [
            (d.source_table or d.unique_name).upper()
            for d in datasets
        ]
        if not table_names:
            return {}

        placeholders = ", ".join(["%s"] * len(table_names))
        query = (
            f"SELECT table_name, column_name "
            f"FROM information_schema.columns "
            f"WHERE table_schema = %s AND table_name IN ({placeholders})"
        )
        params = (self._data_schema_name().upper(), *table_names)
        self._execute_sql(cursor, query, params, context="FETCH MODEL METADATA")

        metadata: Dict[str, set[str]] = {}
        for row in cursor.fetchall():
            table = row[0].upper()
            column = row[1].upper()
            if table not in metadata:
                metadata[table] = set()
            metadata[table].add(column)
        self._live_schema_metadata.update(metadata)
        return metadata

    def _preflight_check_osi(self, cursor, osi_model) -> None:
        """Validate OSI model relationships against Snowflake schema.

        This checks that all tables referenced in the OSI model exist in the target Snowflake schema.
        When `strict_osi_validation` is enabled in the SnowflakeConfig, missing tables cause a ConnectorError.
        Otherwise, missing tables are only logged as warnings.
        """
        # Get all tables from Snowflake
        self._execute_sql(cursor, f"SHOW TABLES IN SCHEMA {self._schema_fqn()}", context="PREFLIGHT SHOW TABLES")
        existing_tables = {row[1].upper() for row in cursor.fetchall()}
        
        missing_tables = []
        for dataset in osi_model.datasets:
            table_name = dataset.source_table or dataset.unique_name
            if table_name.upper() not in existing_tables:
                logger.warning(f"Table {table_name} not found in Snowflake")
                missing_tables.append(table_name)

        # Enforce strict validation if configured
        if missing_tables and getattr(self.config, "strict_osi_validation", False):
            raise ConnectorError(
                f"Strict OSI validation failed: missing tables in Snowflake: {', '.join(missing_tables)}"
            )

    def _drop_deprecated_views(self, cursor, model: Any) -> None:
        """
        Drop legacy semantic views ending in _SV.
        These are replaced by Cortex Analyst compatible views.
        """
        view_name = self._get_safe_object_name(model.unique_name or model.label)
        legacy_view = f"{self.config.database}.{self.config.schema_name}.{view_name}_SV"
        
    def declared_primary_keys(self, cursor: Optional[Any] = None) -> Dict[str, List[str]]:
        """TABLE -> primary key columns declared in Snowflake for this schema.

        Metadata only (SHOW PRIMARY KEYS); empty when none are declared or
        the lookup isn't possible. Cached per schema manager.
        """
        cached = getattr(self, "_declared_pk_cache", None)
        if cached is not None:
            return cached
        cur = cursor or getattr(self, "_active_cursor", None)
        keys: Dict[str, List[tuple]] = {}
        if cur is not None:
            try:
                self._execute_sql(
                    cur, f"SHOW PRIMARY KEYS IN SCHEMA {self._schema_fqn()}",
                    context="SHOW PRIMARY KEYS", expected_failure=True,
                )
                names = [str(d[0]).lower() for d in (cur.description or [])]
                t_i, c_i = names.index("table_name"), names.index("column_name")
                s_i = names.index("key_sequence") if "key_sequence" in names else None
                for row in cur.fetchall() or []:
                    seq = int(row[s_i]) if s_i is not None and row[s_i] is not None else 0
                    keys.setdefault(str(row[t_i]).upper(), []).append((seq, str(row[c_i]).upper()))
            except Exception as exc:
                logger.debug("Declared primary keys unavailable for %s: %s", self._schema_fqn(), exc)
        result = {t: [c for _, c in sorted(cols)] for t, cols in keys.items()}
        if cur is not None:
            self._declared_pk_cache = result
        return result

    def warn_if_key_not_unique(self, table_name: str, columns: List[str], cursor: Optional[Any] = None) -> None:
        """Log a WARNING when a declared primary key isn't unique in the data.

        Advisory only: the key is chosen from the PBIX model and the schema,
        and this check never changes it.
        """
        table_upper = str(table_name or "").strip('"').upper()
        cols = [str(c).strip('"') for c in columns or [] if c]
        checked = getattr(self, "_key_uniqueness_checked", None)
        if checked is None:
            checked = self._key_uniqueness_checked = set()
        cur = cursor or getattr(self, "_active_cursor", None)
        if cur is None or not cols or (table_upper, tuple(cols)) in checked:
            return
        checked.add((table_upper, tuple(cols)))
        col_list = ", ".join(f'"{c}"' for c in cols)
        try:
            self._execute_sql(
                cur,
                f"SELECT COUNT(*), COUNT(DISTINCT {col_list}) FROM {self._schema_fqn()}.\"{table_upper}\"",
                context=f"KEY UNIQUENESS {table_upper}",
                expected_failure=True,
            )
            row = cur.fetchone() or (0, 0)
            total, distinct = int(row[0] or 0), int(row[1] or 0)
        except Exception as exc:
            logger.debug("Key uniqueness check skipped for %s: %s", table_upper, exc)
            return
        if distinct < total:
            logger.warning(
                "Primary key %s on %s is not unique in the data (%d rows, %d distinct); "
                "the semantic view keeps it as modelled.",
                cols, table_upper, total, distinct,
            )

    @staticmethod

    def _resolve_column_name_for_dataset(
        known_columns: set[str],
        candidate: str
    ) -> Optional[str]:
        """Resolve LLM-drifted column names to an actual dataset column."""
        if not known_columns:
            return None

        if candidate in known_columns:
            return candidate

        # underscore-insensitive match: IS_VAN_ARSDEL -> ISVANARSDEL
        compact = candidate.replace("_", "")
        for col in known_columns:
            if col.replace("_", "") == compact:
                return col
        return None

    @staticmethod
    def _is_history_table_name(name: str) -> bool:
        """Return True for Salesforce-like change-history tables."""
        upper = str(name or "").upper()
        return (
            upper.endswith("_HISTORY")
            or upper.endswith("HISTORY")
            or "FIELDHISTORY" in upper
        )

    @staticmethod
    def _resolve_history_snapshot_keys(physical_columns: list[str]) -> tuple[Optional[str], Optional[str]]:
        """Choose parent/timestamp columns for latest-row snapshoting."""
        if not physical_columns:
            return None, None

        by_upper = {str(col).upper(): col for col in physical_columns}

        parent_candidates = (
            "PARENT_ID", "PARENTID", "QUOTE_ID", "RECORD_ID", "ENTITY_ID", "ID",
        )
        timestamp_candidates = (
            "CREATED_DATE", "CREATEDDATE", "LAST_MODIFIED_DATE", "LASTMODIFIEDDATE",
            "SYSTEMMODSTAMP", "MODSTAMP", "TIMESTAMP",
        )

        parent_col = next((by_upper[c] for c in parent_candidates if c in by_upper), None)
        timestamp_col = next((by_upper[c] for c in timestamp_candidates if c in by_upper), None)
        return parent_col, timestamp_col

    def _build_history_snapshot_ddls_for_sml(
        self,
        sml: SMLModel
    ) -> tuple[list[str], dict[str, str]]:
        """Build helper latest-row views for history datasets to avoid join fan-out."""
        history_view_ddls: list[str] = []
        dataset_source_overrides: dict[str, str] = {}
        emitted_views: set[str] = set()

        for dataset in sml.datasets:
            source_name = dataset.source_table or dataset.unique_name
            if not self._is_history_table_name(source_name):
                continue

            physical_columns = list(self._collect_physical_source_columns(dataset).keys())
            parent_col, timestamp_col = self._resolve_history_snapshot_keys(physical_columns)
            if not parent_col or not timestamp_col:
                logger.info(
                    "History snapshot bypassed for dataset '%s': missing parent/timestamp columns",
                    dataset.unique_name,
                )
                continue

            safe_source = self._safe_table_name(source_name)
            safe_latest_view = self._safe_table_name(f"{safe_source}_LATEST")
            if safe_latest_view in emitted_views:
                dataset_source_overrides[dataset.unique_name] = safe_latest_view
                continue

            full_source = f'"{self.config.database}"."{self.config.schema_name}"."{safe_source}"'
            full_latest = f'"{self.config.database}"."{self.config.schema_name}"."{safe_latest_view}"'
            select_cols = ", ".join(f'"{col}"' for col in physical_columns)
            ddl = (
                f"CREATE OR REPLACE VIEW {full_latest} AS\n"
                f"SELECT {select_cols}\n"
                f"FROM {full_source}\n"
                f'QUALIFY ROW_NUMBER() OVER (PARTITION BY "{parent_col}" '
                f'ORDER BY "{timestamp_col}" DESC NULLS LAST) = 1'
            )

            history_view_ddls.append(ddl)
            emitted_views.add(safe_latest_view)
            dataset_source_overrides[dataset.unique_name] = safe_latest_view
            logger.info(
                "History dataset '%s' mapped to latest-snapshot view '%s' using (%s, %s)",
                dataset.unique_name,
                safe_latest_view,
                parent_col,
                timestamp_col,
            )

        return history_view_ddls, dataset_source_overrides

    def _build_history_snapshot_ddls_for_osi(
        self,
        osi: OSIModel
    ) -> tuple[list[str], dict[str, str]]:
        """OSI equivalent of history latest-row helper view generation."""
        history_view_ddls: list[str] = []
        dataset_source_overrides: dict[str, str] = {}
        emitted_views: set[str] = set()

        for dataset in osi.datasets:
            source_name = dataset.source_table or dataset.unique_name
            if not self._is_history_table_name(source_name):
                continue

            physical_columns = list(self._collect_physical_source_columns_osi(dataset).keys())
            parent_col, timestamp_col = self._resolve_history_snapshot_keys(physical_columns)
            if not parent_col or not timestamp_col:
                logger.info(
                    "History snapshot bypassed for OSI dataset '%s': missing parent/timestamp columns",
                    dataset.unique_name,
                )
                continue

            safe_source = self._safe_table_name(source_name)
            safe_latest_view = self._safe_table_name(f"{safe_source}_LATEST")
            if safe_latest_view in emitted_views:
                dataset_source_overrides[dataset.unique_name] = safe_latest_view
                continue

            full_source = f'"{self.config.database}"."{self.config.schema_name}"."{safe_source}"'
            full_latest = f'"{self.config.database}"."{self.config.schema_name}"."{safe_latest_view}"'
            select_cols = ", ".join(f'"{col}"' for col in physical_columns)
            ddl = (
                f"CREATE OR REPLACE VIEW {full_latest} AS\n"
                f"SELECT {select_cols}\n"
                f"FROM {full_source}\n"
                f'QUALIFY ROW_NUMBER() OVER (PARTITION BY "{parent_col}" '
                f'ORDER BY "{timestamp_col}" DESC NULLS LAST) = 1'
            )

            history_view_ddls.append(ddl)
            emitted_views.add(safe_latest_view)
            dataset_source_overrides[dataset.unique_name] = safe_latest_view
            logger.info(
                "OSI history dataset '%s' mapped to latest-snapshot view '%s' using (%s, %s)",
                dataset.unique_name,
                safe_latest_view,
                parent_col,
                timestamp_col,
            )

        return history_view_ddls, dataset_source_overrides
