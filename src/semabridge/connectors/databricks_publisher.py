"""
Databricks Publisher.

Publishes SML models to Databricks SQL Warehouse using the Statements API.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import SMLModel
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class DatabricksPublishError(Exception):
    """Raised when Databricks publish fails."""


class DatabricksPublisher:
    """Publish semantic artifacts to Databricks SQL Warehouse."""

    def __init__(self, config: DatabricksConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.token.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _fq_name(self, name: str) -> str:
        safe = str(name).replace('"', '""')
        return f'`{self.config.catalog}`.`{self.config.schema_name}`.`{safe}`'

    def _sql_type(self, normalized_type: str, source_type: str = "", column_name: str = "") -> str:
        # Calendar-like dimension columns do not need BIGINT width.
        if str(normalized_type or "").lower() == "integer" and self._is_small_calendar_int(column_name):
            return "INT"

        # Preserve source type when it maps cleanly to a Databricks SQL type.
        source = str(source_type or "").strip()
        preserved = self._from_source_type(source) if source else None
        if preserved:
            return preserved

        mapping = {
            "string": "STRING",
            "integer": "BIGINT",
            "decimal": "DECIMAL(38, 10)",
            "float": "DOUBLE",
            "boolean": "BOOLEAN",
            "date": "DATE",
            "datetime": "TIMESTAMP",
            "time": "TIMESTAMP",
            "binary": "BINARY",
            "variant": "STRING",
            "unknown": "STRING",
        }
        return mapping.get(str(normalized_type or "").lower(), "STRING")

    def _from_source_type(self, source_type: str) -> str | None:
        """Best-effort mapping from source native type to Databricks SQL type."""
        st = str(source_type or "").strip().upper()
        if not st:
            return None

        st = re.sub(r"\s+", " ", st)

        if st in {"INT", "INTEGER", "BIGINT", "INT64", "LONG"}:
            return "BIGINT"
        if st in {"SMALLINT", "TINYINT", "INT32", "SHORT", "BYTE"}:
            return "INT"
        if st in {"FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL"}:
            return "DOUBLE"
        if st in {"BOOLEAN", "BOOL"}:
            return "BOOLEAN"
        if st in {"DATE"}:
            return "DATE"
        if st in {"DATETIME", "DATETIME2", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIME"}:
            return "TIMESTAMP"
        if st in {"BINARY", "VARBINARY"}:
            return "BINARY"
        if st in {"STRING", "TEXT"}:
            return "STRING"

        # Keep precision/scale if provided by the source.
        if re.fullmatch(r"(DECIMAL|NUMERIC)\(\d+\s*,\s*\d+\)", st):
            return st.replace("NUMERIC", "DECIMAL")

        # VARCHAR/CHAR with optional length still maps safely to STRING.
        if re.fullmatch(r"(VAR)?CHAR(\(\d+\))?", st) or re.fullmatch(r"NVARCHAR(\(\d+\))?", st):
            return "STRING"

        return None

    def _escape_literal(self, value: str) -> str:
        return str(value or "").replace("'", "''")

    def _is_small_calendar_int(self, column_name: str) -> bool:
        normalized = str(column_name or "").strip().lower()
        normalized = re.sub(r"[^a-z0-9]", "", normalized)
        return normalized in {
            "year",
            "quarter",
            "month",
            "monthnumber",
            "monthnum",
            "day",
            "dayofmonth",
            "dayofweek",
            "week",
            "weekofyear",
        }

    def _chunk(self, items: list[str], size: int) -> list[list[str]]:
        return [items[i:i + size] for i in range(0, len(items), size)]

    def _sanitize_identifier(self, value: str) -> str:
        # Databricks object names must be alphanumeric/underscore only.
        raw = str(value or "").strip().replace('`', '')
        safe = re.sub(r"[^0-9A-Za-z_]", "_", raw)
        safe = re.sub(r"_+", "_", safe).strip("_")
        return safe or "unnamed"

    def generate_sql_statements(self, sml_model: SMLModel) -> list[str]:
        """Generate Databricks SQL statements from SML model metadata."""
        stmts: list[str] = [
            f"CREATE SCHEMA IF NOT EXISTS `{self.config.catalog}`.`{self.config.schema_name}`",
        ]

        model_name = self._sanitize_identifier(sml_model.unique_name)
        model_name_lit = self._escape_literal(model_name)
        model_table = self._fq_name(model_name)

        stmts.append(
            (
                f"CREATE TABLE IF NOT EXISTS {model_table} ("
                "model_name STRING, dataset_name STRING, object_name STRING, "
                "object_kind STRING, data_type STRING, source_data_type STRING, "
                "expression STRING, updated_at TIMESTAMP"
                ") USING DELTA"
            )
        )
        stmts.append(f"DELETE FROM {model_table} WHERE model_name = '{model_name_lit}'")

        metrics_by_dataset: dict[str, list[Any]] = {}
        for metric in sml_model.metrics:
            ds_name = self._sanitize_identifier(metric.dataset)
            if not ds_name:
                continue
            metrics_by_dataset.setdefault(ds_name, []).append(metric)

        row_values: list[str] = []

        # Store model structure in one model-named table.
        for ds in sml_model.datasets:
            dataset_name = self._sanitize_identifier(ds.unique_name)
            if not dataset_name:
                continue

            for col in ds.columns:
                col_name = self._sanitize_identifier(col.unique_name)
                if not col_name:
                    continue
                c_type = self._sql_type(col.data_type.value, col.source_type, col.unique_name)
                source_type = self._escape_literal(col.source_type or col.data_type.value or "")
                row_values.append(
                    (
                        f"('{model_name_lit}', '{self._escape_literal(dataset_name)}', '{self._escape_literal(col_name)}', "
                        f"'dimension', '{self._escape_literal(c_type)}', '{source_type}', NULL, current_timestamp())"
                    )
                )

            for metric in metrics_by_dataset.get(dataset_name, []):
                m_name = self._sanitize_identifier(metric.unique_name)
                if not m_name:
                    continue
                expr = self._escape_literal(metric.sql_expression or metric.expression or "")
                row_values.append(
                    (
                        f"('{model_name_lit}', '{self._escape_literal(dataset_name)}', '{self._escape_literal(m_name)}', "
                        f"'measure', 'DOUBLE', '', '{expr}', current_timestamp())"
                    )
                )

        # Keep statement size controlled while minimizing Databricks API round trips.
        for value_chunk in self._chunk(row_values, 500):
            stmts.append(
                (
                    f"INSERT INTO {model_table} (model_name, dataset_name, object_name, object_kind, data_type, source_data_type, expression, updated_at) "
                    f"VALUES {', '.join(value_chunk)}"
                )
            )

        return stmts

    def execute_statements(self, statements: list[str]) -> list[dict[str, Any]]:
        """Execute SQL statements via Databricks SQL Statements API."""
        endpoint = f"{self.config.api_base_url}/api/2.0/sql/statements"
        results: list[dict[str, Any]] = []

        for sql in statements:
            payload = {
                "statement": sql,
                "warehouse_id": self.config.warehouse_id,
                "wait_timeout": "30s",
            }
            resp = requests.post(endpoint, headers=self._headers(), json=payload, timeout=60)
            if resp.status_code >= 400:
                raise DatabricksPublishError(
                    f"Databricks statement failed ({resp.status_code}): {resp.text[:500]}"
                )

            data = resp.json()
            state = (data.get("status") or {}).get("state")
            if state and state not in {"SUCCEEDED"}:
                err = data.get("status", {}).get("error") or "unknown error"
                raise DatabricksPublishError(f"Databricks statement state={state}: {err}")

            results.append(data)

        return results

    def publish(self, sml_model: SMLModel) -> str:
        """Generate and execute Databricks statements."""
        statements = self.generate_sql_statements(sml_model)
        logger.info("Publishing semantic model to Databricks: model=%s statements=%s", sml_model.unique_name, len(statements))
        self.execute_statements(statements)
        return f"databricks://{self.config.catalog}/{self.config.schema_name}/{sml_model.unique_name}"
