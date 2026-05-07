from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.exceptions import ConnectorError
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import (
    AggregationType,
    Cardinality,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)
from semabridge.utils.identifiers import IdentifierSanitizer


def build_config() -> SnowflakeConfig:
    return SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="TEST_DB",
        schema_name="TEST_SCHEMA",
        role="test_role",
    )


def test_drop_extra_columns_refuses_full_recreate() -> None:
    config = build_config()
    behavior = ConnectorBehavior()
    connection_manager = MagicMock()
    schema_manager = SnowflakeSchemaManager(config, behavior, IdentifierSanitizer(), connection_manager)
    cursor = MagicMock()
    cursor.fetchall.return_value = [("ID",), ("NAME",)]

    with pytest.raises(ConnectorError, match="full table recreation is disabled"):
        schema_manager._drop_extra_columns(cursor, "SALES", {"ID", "NAME"})

    executed_sql = [call.args[1] for call in connection_manager._execute_sql.call_args_list if len(call.args) > 1]
    assert not any("CREATE OR REPLACE TABLE" in sql.upper() for sql in executed_sql)


def test_filter_ddls_for_existing_tables_uses_exact_table_name_match() -> None:
    config = build_config()
    emitter = SnowflakeEmitter(config, ConnectorBehavior())

    existing_tables = {
        "SALES": {
            "exists": True,
            "columns": ["ID"],
            "table_name": 'TEST_SCHEMA."SALES"',
        }
    }
    sales_history_ddl = 'CREATE TABLE IF NOT EXISTS TEST_DB.TEST_SCHEMA."SALES_HISTORY" ("ID" INTEGER);'
    sales_ddl = 'CREATE TABLE IF NOT EXISTS TEST_DB.TEST_SCHEMA."SALES" ("ID" INTEGER);'

    filtered = emitter._filter_ddls_for_existing_tables([sales_history_ddl, sales_ddl], existing_tables)

    assert sales_history_ddl in filtered
    assert sales_ddl not in filtered


def test_existing_table_validation_checks_columns() -> None:
    config = build_config()
    emitter = SnowflakeEmitter(config, ConnectorBehavior())

    model = SMLModel(
        unique_name="MODEL",
        datasets=[
            SMLDataset(
                unique_name="FACT",
                source_table="FACT",
                columns=[
                    SMLColumn(unique_name="ID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="REVENUE", data_type=DataType.FLOAT),
                ],
            ),
            SMLDataset(
                unique_name="DIM",
                source_table="DIM",
                columns=[
                    SMLColumn(unique_name="DIM_ID", data_type=DataType.INTEGER),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TOTAL_REVENUE",
                dataset="FACT",
                source_column="REVENUE",
                aggregation=AggregationType.SUM,
            )
        ],
        relationships=[
            SMLRelationship(
                unique_name="FACT_TO_DIM",
                from_dataset="FACT",
                from_columns=["FACT_ID"],
                to_dataset="DIM",
                to_columns=["DIM_ID"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ],
    )

    existing_tables = {
        "FACT": {"exists": True, "columns": ["ID", "REVENUE"], "table_name": 'TEST_SCHEMA."FACT"'},
        "DIM": {"exists": True, "columns": ["DIM_ID"], "table_name": 'TEST_SCHEMA."DIM"'},
    }

    errors = emitter._validate_relationships_measures_on_existing_tables(MagicMock(), model, existing_tables, False)

    assert any("missing from_columns" in error for error in errors)
    assert not any("source_column" in error for error in errors)


def test_existing_table_validation_detects_type_mismatches() -> None:
    config = build_config()
    emitter = SnowflakeEmitter(config, ConnectorBehavior())

    model = SMLModel(
        unique_name="MODEL",
        datasets=[
            SMLDataset(
                unique_name="FACT",
                source_table="FACT",
                columns=[
                    SMLColumn(unique_name="FACT_ID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="REVENUE", data_type=DataType.FLOAT),
                ],
            ),
            SMLDataset(
                unique_name="DIM",
                source_table="DIM",
                columns=[
                    SMLColumn(unique_name="DIM_ID", data_type=DataType.INTEGER),
                ],
            ),
        ],
        metrics=[],
        relationships=[
            SMLRelationship(
                unique_name="FACT_TO_DIM",
                from_dataset="FACT",
                from_columns=["FACT_ID"],
                to_dataset="DIM",
                to_columns=["DIM_ID"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ],
    )

    existing_tables = {
        "FACT": {
            "exists": True,
            "columns": ["FACT_ID", "REVENUE"],
            "column_types": {"FACT_ID": "VARCHAR", "REVENUE": "FLOAT"},
            "table_name": 'TEST_SCHEMA."FACT"',
        },
        "DIM": {
            "exists": True,
            "columns": ["DIM_ID"],
            "column_types": {"DIM_ID": "INTEGER"},
            "table_name": 'TEST_SCHEMA."DIM"',
        },
    }

    errors = emitter._validate_relationships_measures_on_existing_tables(MagicMock(), model, existing_tables, False)

    assert any("column type mismatch" in error for error in errors)
