"""Source-table resolution for the Databricks publisher.

Where does each semantic dataset's data live in Unity Catalog, and does that
table exist? Explicit ``source_table_mapping`` first, then the Unity Catalog
matcher, the dataset's own catalog/schema, the configured source location,
and finally the target schema. A same-named table in another schema is used
only when ``allow_alternate_schema_discovery`` is on and the match is unique.

Split out of ``databricks_publisher.py`` unchanged (a mixin of
``DatabricksPublisher``): it reads ``self.config``, ``self._dbx_behavior``,
``self.execute_statements`` and the publisher's caches.
"""
from __future__ import annotations

import re
from typing import Optional

from semabridge.connectors.databricks_table_matcher import MatchedTable
from semabridge.sml.models import SMLDataset, SMLModel
from semabridge.utils.logger import get_logger

logger = get_logger("semabridge.connectors.databricks_publisher")


class DatabricksSourceResolutionMixin:
    """Methods of DatabricksPublisher that resolve and check source tables."""

    def _lookup_matched_source_table(self, dataset: SMLDataset) -> Optional[MatchedTable]:
        if not self._matched_source_tables:
            return None
        for cand in (dataset.unique_name, dataset.source_table):
            if not cand:
                continue
            if cand in self._matched_source_tables:
                return self._matched_source_tables[cand]
            cand_lower = str(cand).strip().lower()
            for key, matched in self._matched_source_tables.items():
                if key.lower() == cand_lower:
                    return matched
        return None

    def _resolve_source_table(
        self,
        dataset: SMLDataset,
    ) -> str:
        """Resolve the fully-qualified Databricks source table for a dataset.

        Resolution priority:
            1. Explicit mapping in behavior.source_table_mapping
            1.5. Unity Catalog matcher result (``_matched_source_tables``)
            2. Dataset source_database.source_schema.source_table (if configured;
               when live lookup detects foreign source_database/source_schema like
               Snowflake ``semabridge.public`` does not exist in Databricks, binds
               to the real table in the Databricks target catalog.schema instead)
            3. source_catalog.source_schema.dataset_name (if configured)
            4. Same catalog/schema as metadata table

        Args:
            dataset: The SML dataset to resolve a source table for.

        Returns:
            Fully-qualified Databricks table name with backtick quoting.
        """
        ds_name = self._sanitize_identifier(
            dataset.source_table or dataset.unique_name
        )

        # Priority 1: Explicit mapping
        mapping = self._dbx_behavior.source_table_mapping
        if ds_name in mapping:
            return mapping[ds_name]
        # Case-insensitive lookup
        for key, val in mapping.items():
            if key.upper() == ds_name.upper():
                return val

        # Priority 1.5: Unity Catalog loose table matcher result
        matched = self._lookup_matched_source_table(dataset)
        if matched is not None and matched.quoted_fqn:
            return matched.quoted_fqn

        # Priority 2: Dataset-level source location from extracted model metadata
        dataset_catalog = str(dataset.source_database or "").strip()
        dataset_schema = str(dataset.source_schema or "").strip()
        if dataset_catalog and dataset_schema:
            sanitized_cat = self._sanitize_identifier(dataset_catalog)
            sanitized_sch = self._sanitize_identifier(dataset_schema)
            foreign_fqn = f"`{sanitized_cat}`.`{sanitized_sch}`.`{ds_name}`"
            is_foreign_location = (
                sanitized_cat.lower() != str(self.config.catalog or "").strip().lower()
                or sanitized_sch.lower() != str(self.config.schema_name or "").strip().lower()
            )
            if is_foreign_location:
                if self._offline_plan_mode:
                    if sanitized_cat.lower() == "semabridge" and sanitized_sch.lower() == "public":
                        return self._fq_name(ds_name)
                else:
                    foreign_exists = self._check_source_table_exists(foreign_fqn)
                    if foreign_exists is False:
                        target_fqn = self._fq_name(ds_name)
                        if self._check_source_table_exists(target_fqn) is True:
                            logger.info(
                                "Rebound foreign source '%s' for dataset '%s' to target Databricks table '%s'",
                                foreign_fqn,
                                dataset.unique_name,
                                target_fqn,
                            )
                            return target_fqn
                        discovered = self._discover_source_table_schema(foreign_fqn)
                        if discovered:
                            return discovered
                        return target_fqn
            return foreign_fqn

        # Priority 3: Configured source catalog/schema
        src_catalog = self._dbx_behavior.source_catalog.strip()
        src_schema = self._dbx_behavior.source_schema.strip()
        if src_catalog and src_schema:
            sanitized_cat = self._sanitize_identifier(src_catalog)
            sanitized_sch = self._sanitize_identifier(src_schema)
            configured_fqn = f"`{sanitized_cat}`.`{sanitized_sch}`.`{ds_name}`"
            is_foreign_location = (
                sanitized_cat.lower() != str(self.config.catalog or "").strip().lower()
                or sanitized_sch.lower() != str(self.config.schema_name or "").strip().lower()
            )
            if is_foreign_location:
                if self._offline_plan_mode:
                    if sanitized_cat.lower() == "semabridge" and sanitized_sch.lower() == "public":
                        return self._fq_name(ds_name)
                else:
                    if self._check_source_table_exists(configured_fqn) is False:
                        target_fqn = self._fq_name(ds_name)
                        if self._check_source_table_exists(target_fqn) is True:
                            logger.info(
                                "Rebound configured source '%s' for dataset '%s' to target Databricks table '%s'",
                                configured_fqn,
                                dataset.unique_name,
                                target_fqn,
                            )
                            return target_fqn
                        discovered = self._discover_source_table_schema(configured_fqn)
                        if discovered:
                            return discovered
                        return target_fqn
            return configured_fqn

        # Priority 4: Same catalog/schema as metadata table
        return self._fq_name(ds_name)

    def _resolve_existing_source_for_dataset(self, dataset: SMLDataset, expected_source: str) -> str | None:
        """Return an existing source table for a dataset if one can be resolved."""
        existing = self._resolve_existing_source_table(dataset, expected_source)
        if existing:
            return existing

        fallback_source = self._fq_name(self._sanitize_identifier(dataset.unique_name))
        if fallback_source != expected_source:
            existing = self._resolve_existing_source_table(dataset, fallback_source)
            if existing:
                logger.info(
                    "Source table for dataset '%s' resolved via dataset-name fallback: %s",
                    dataset.unique_name,
                    existing,
                )
                return existing

        return None

    def _resolve_existing_source_table(self, dataset: SMLDataset, expected_source: str) -> str | None:
        """Return the source table reference only if it exists in Databricks."""
        if not expected_source:
            return None
        exists = self._check_source_table_exists(expected_source)
        if exists is False:
            discovered = self._discover_source_table_schema(expected_source)
            if discovered:
                logger.info(
                    "Source table for dataset '%s' discovered in alternate schema: %s",
                    dataset.unique_name,
                    discovered,
                )
            return discovered
        return expected_source

    def _discover_source_table_schema(self, source_table: str) -> str | None:
        """Find a table with the same name in an alternate schema in the catalog."""
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return None

        catalog, schema_name, table_name = parsed
        stmt = (
            f"SELECT LOWER(table_schema) AS table_schema FROM {catalog}.information_schema.tables "
            f"WHERE LOWER(table_name) = LOWER('{table_name}') ORDER BY table_schema"
        )

        try:
            rows = self.execute_statements([stmt])
        except Exception:
            return None

        if not rows:
            return None

        payload = rows[0] if isinstance(rows[0], dict) else {}
        result_block = payload.get("result") if isinstance(payload, dict) else {}
        data_array = result_block.get("data_array") if isinstance(result_block, dict) else None
        if not data_array:
            return None

        expected_schema = str(schema_name or "").strip().lower()
        discovered_schemas: list[str] = []
        for row in data_array:
            if isinstance(row, list) and row:
                candidate = str(row[0]).strip().lower()
                if re.fullmatch(r"[a-z_][a-z0-9_]*", candidate):
                    discovered_schemas.append(candidate)

        candidates = sorted({s for s in discovered_schemas if s and s != expected_schema})
        if not candidates:
            return None

        expected_fqn = f"{catalog}.{schema_name}.{table_name}"
        found_in = ", ".join(f"{catalog}.{s}.{table_name}" for s in candidates)
        if not bool(getattr(self._dbx_behavior, "allow_alternate_schema_discovery", False)):
            # Exact binding only: a same-named table in another schema may be
            # a dev or backup copy, so it is reported, never used silently.
            self._record_source_binding_issue(
                expected_fqn,
                f"Source table {expected_fqn} does not exist. A table with the same name exists in "
                f"{found_in}, but it was not used automatically. Map it explicitly with "
                "source_table_mapping if it is the right table.",
            )
            return None
        if len(candidates) > 1:
            self._record_source_binding_issue(
                expected_fqn,
                f"Source table {expected_fqn} does not exist and a table with the same name exists in "
                f"several schemas ({found_in}); none was chosen. Map the right one with source_table_mapping.",
            )
            return None

        chosen_schema = candidates[0]
        logger.warning(
            "Source table %s does not exist; bound to %s.%s.%s (allow_alternate_schema_discovery is on)",
            expected_fqn,
            catalog,
            chosen_schema,
            table_name,
        )
        return f"`{catalog}`.`{chosen_schema}`.`{table_name}`"

    def _record_source_binding_issue(self, source_table: str, message: str) -> None:
        """Record (once per table) why a source table was not bound automatically."""
        issues = getattr(self, "_source_binding_issues", None)
        if issues is None:
            issues = self._source_binding_issues = []
        if any(i.get("source_table") == source_table for i in issues):
            return
        issues.append({"source_table": source_table, "message": message})
        logger.warning("%s", message)

    def _parse_table_reference(self, source_table: str) -> tuple[str, str, str] | None:
        """Parse a Databricks table reference into catalog, schema, and table."""
        raw = str(source_table or "").strip().replace("`", "")
        if not raw:
            return None
        parts = [part for part in raw.split(".") if part]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2:
            return self.config.catalog, parts[0], parts[1]
        if len(parts) == 1:
            return self.config.catalog, self.config.schema_name, parts[0]
        return None

    def _get_source_table_columns(self, source_table: str) -> set[str]:
        """Best-effort introspection of physical columns for a source table."""
        if getattr(self, "_offline_plan_mode", False):
            return set()
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return set()

        catalog, schema_name, table_name = parsed
        # Use native Databricks SHOW COLUMNS metadata command to bypass Serverless Information Schema locks
        stmt = f"SHOW COLUMNS IN `{catalog}`.`{schema_name}`.`{table_name}`"

        try:
            rows = self.execute_statements([stmt])
        except Exception:
            return set()

        if not rows:
            return set()

        payload = rows[0] if isinstance(rows[0], dict) else {}
        result_block = payload.get("result") if isinstance(payload, dict) else {}
        data_array = result_block.get("data_array") if isinstance(result_block, dict) else None
        if not data_array:
            return set()

        columns: set[str] = set()
        for row in data_array:
            if isinstance(row, list) and row:
                columns.add(str(row[0]).strip().lower())
        return columns

    def _check_source_table_exists(self, source_table: str) -> bool | None:
        """Best-effort existence check for a source table reference.

        Returns:
            True when the table is definitively present,
            False when definitively missing,
            None when the check could not be completed.
        """
        if getattr(self, "_offline_plan_mode", False):
            return None
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return None

        catalog, schema_name, table_name = parsed
        
        # O(1) Preload Schema Cache (Eliminates 15 min N+1 sync blocks & case-sensitivity flaws!)
        cache_key = f"{catalog}.{schema_name}".lower()
        if getattr(self, "_schema_cache", None) is None:
            self._schema_cache = set()
            self._table_exists_cache = set()
            
        if cache_key not in self._schema_cache:
            stmt = f"SHOW TABLES IN `{catalog}`.`{schema_name}`"
            try:
                rows = self.execute_statements([stmt])
                if not rows or not isinstance(rows[0], dict) or "result" not in rows[0]:
                    return None
                payload = rows[0]
                result_block = payload.get("result")
                if not isinstance(result_block, dict):
                    return None
                data_array = result_block.get("data_array")
                if data_array:
                    for row in data_array:
                        # Databricks SHOW TABLES returns row like [database, tableName, isTemporary].
                        # We safely index all valid string fields into the cache to avoid schema shifts.
                        for field in row:
                            if isinstance(field, str) and field:
                                self._table_exists_cache.add(f"{cache_key}.{field.lower()}")
                self._schema_cache.add(cache_key)
            except Exception as e:
                err_text = str(e)
                if any(
                    code in err_text
                    for code in (
                        "NO_SUCH_CATALOG_EXCEPTION",
                        "CATALOG_NOT_FOUND",
                        "SCHEMA_NOT_FOUND",
                        "DATABASE_NOT_FOUND",
                        "TABLE_OR_VIEW_NOT_FOUND",
                        "42704",
                    )
                ):
                    self._schema_cache.add(cache_key)
                    logger.info(
                        "Databricks catalog/schema `%s`.`%s` does not exist; using target catalog.schema fallback",
                        catalog,
                        schema_name,
                    )
                    return False
                logger.warning("Databricks Schema Cache load failed: %s", e)
                return None
                
        return f"{cache_key}.{table_name.lower()}" in self._table_exists_cache

    def _auto_initialize_missing_tables(
        self,
        sml_model: SMLModel,
        resolved_view_type: str = "",
    ) -> list[str]:
        """Report missing source tables without ever creating physical tables.

        SemaBridge never runs ``CREATE TABLE`` or ``CREATE SCHEMA`` for missing
        source datasets. Any missing source table is recorded so dependent views
        are skipped with a clear reason while healthy views continue.
        """
        _ = resolved_view_type
        if not self._dbx_behavior.create_measure_views:
            self._missing_source_tables = []
            return []

        missing_tables: list[str] = []
        seen_sources: set[str] = set()

        for dataset in sml_model.datasets:
            expected_source = self._resolve_source_table(dataset)
            existing_source = self._resolve_existing_source_for_dataset(dataset, expected_source)
            if existing_source:
                continue

            normalized_source = str(expected_source or "").strip()
            if not normalized_source or normalized_source in seen_sources:
                continue

            seen_sources.add(normalized_source)
            missing_tables.append(normalized_source)

        self._missing_source_tables = list(missing_tables)
        if missing_tables:
            logger.warning(
                "Databricks pre-flight detected %d missing source table(s) (will NOT create tables; affected views will be skipped): %s",
                len(missing_tables),
                ", ".join(missing_tables),
            )
        return missing_tables

    def _quote_table_reference(self, source_table: str) -> str:
        """Return a safely quoted Databricks table reference."""
        parsed = self._parse_table_reference(source_table)
        if not parsed:
            return str(source_table or "").replace("`", "")
        catalog, schema_name, table_name = parsed
        return f"`{catalog}`.`{schema_name}`.`{table_name}`"
